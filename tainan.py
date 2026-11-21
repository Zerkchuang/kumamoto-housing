"""Public-source Tainan inventory. TWD/ping are never mixed with Japan's DB."""
import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / 'tainan_config.json').read_text())
SOURCES = {
    '幸福家': ('https://www.singfujia.com/buy?city=Tainan-city&types=house-villa&price-max=4000&search='+quote('電梯'), r'/buy/detail/\d+'),
    '永慶': ('https://buy.yungching.com.tw/list/'+quote('台南市-')+'_c/'+quote('電梯')+'_kw', r'/house/\d+'),
    '信義': ('https://www.sinyi.com.tw/buy/list/'+quote('電梯')+'-keyword/Tainan-city', r'/buy/house/[A-Za-z0-9]+'),
    '5168': ('https://buy.houseprice.tw/list/'+quote('台南市')+'_city/', r'/house/\d+_\d+/'),
    '591': ('https://sale.591.com.tw/?shType=list&regionid=15&kind=2', r'/home/house/detail/2/\d+\.html'),
}


def plain(html):
    return BeautifulSoup(html or '', 'html.parser').get_text(' ', strip=True)


def number(value):
    try:
        n = float(str(value).replace(',', ''))
        return n if 0 < n < 1e9 else None
    except (ValueError, TypeError):
        return None


def extract(pattern, text):
    m = re.search(pattern, text)
    return number(m[1]) if m else None


def elevator(text):
    if re.search(r'(?:預留|可增設|可加裝|可裝設|未裝|無|沒有).{0,6}電梯|電梯.{0,8}(?:預留|未裝|未安裝)', text):
        return '未確認已裝'
    if re.search(r'電梯', text):
        return '刊登稱有電梯，設備現況待看屋'
    return '待確認'


def amenities(text):
    # Only explicit named facilities/transport are retained, never invented POI distances.
    patterns = {
        '採買': r'[^，。；\n]{0,25}(?:全聯|家樂福|好市多|市場|超市)[^，。；\n]{0,25}',
        '交通': r'[^，。；\n]{0,25}(?:公車|車站|交流道|幹道)[^，。；\n]{0,25}',
        '醫療': r'[^，。；\n]{0,25}(?:醫院|診所|醫療中心)[^，。；\n]{0,25}',
        '公園': r'[^，。；\n]{0,25}公園[^，。；\n]{0,25}',
    }
    return {k: list(dict.fromkeys(re.findall(p, text)))[:2] for k, p in patterns.items()}


def qualify(row):
    reasons = []
    if row.get('district') not in CONFIG['districts']:
        return '排除', ['不在指定四區']
    if not row.get('type'):
        reasons.append('住宅類型待確認')
    elif row.get('type') not in ('透天別墅', '透天厝', '別墅', '別墅/透天', '透天/店面', '透天'):
        return '排除', ['非已確認透天類型']
    for key, limit, minimum in [('price_twd', CONFIG['max_price_twd'], False),
                                 ('building_ping', CONFIG['min_building_ping'], True),
                                 ('land_ping', CONFIG['min_land_ping'], True),
                                 ('age_years', CONFIG['max_age_years'], False)]:
        v = row.get(key) if key == 'age_years' and isinstance(row.get(key), (int,float)) and row[key] >= 0 else number(row.get(key))
        if v is None:
            reasons.append(key+'待確認')
        elif (minimum and v < limit) or (not minimum and v > limit):
            return '排除', [key+'不符條件']
    if row['elevator'] == '未確認已裝':
        return '排除', ['僅預留、未裝或電梯狀態矛盾']
    if row['elevator'] == '待確認':
        reasons.append('電梯是否已裝待確認')
    return ('待核對' if reasons else '刊登符合'), reasons


def sf_record(d, url):
    if str(d.get('object_info_id')) != urlparse(url).path.rsplit('/', 1)[-1]:
        raise ValueError('物件ID與網址不符')
    if d.get('commission_model') != '售案':
        raise ValueError('非售案')
    text = plain(d.get('a_feature', ''))
    title = d.get('case_name', '')
    price = number(d.get('co_price')) if not d.get('p_price_hide') else None
    return dict(title=title, address=d.get('outer_address', ''),
                district=d.get('area', {}).get('name'), type=d.get('obj_type', {}).get('class_name'),
                price_twd=round(price*10000) if price else None,
                building_ping=number(d.get('t_floor')), land_ping=number(d.get('r_floor')),
                main_ping=number(d.get('m_building')), age_years=d.get('age'), elevator=elevator(title+' '+text),
                amenities=amenities(text))


def parse_detail(source, html, url, expected_title=''):
    soup = BeautifulSoup(html, 'html.parser')
    if source == '幸福家':
        node = soup.find('script', id='__NEXT_DATA__')
        d = json.loads(node.string)['props']['pageProps']['detailAPI']['detail']
        row = sf_record(d, url)
    else:
        heading = soup.find('h1')
        if not heading:
            raise ValueError('無法辨識物件標題')
        title = heading.get_text(' ', strip=True)
        if expected_title and expected_title not in title:
            raise ValueError('列表與詳細頁標題不一致，不採用')
        for node in soup.select('script,style,header,footer,nav'):
            node.decompose()
        text = soup.get_text(' ', strip=True)
        # Stop before recommendations to avoid borrowing another home's fields.
        text = re.split(r'相似物件|推薦物件|猜你喜歡|類似物件實價登錄', text)[0]
        address = re.search(r'[台臺]南市(?:安南區|北區|安平區|中西區)[^\s<>]{0,45}', text)
        district = re.search(r'[台臺]南市(安南區|北區|安平區|中西區)', address[0] if address else '')
        price_node = soup.select_one('.text-c-orange-700.text-4xl') if source == '5168' else None
        price = number(price_node.get_text(strip=True)) if price_node else extract(r'(?:總價|售價|開價|含車位價)\s*([\d,]+(?:\.\d+)?)\s*萬', text)
        if source == '信義' and not price:
            headline = re.split(r'地址[台臺]', text, maxsplit=1)
            if len(headline) == 2:
                price = extract(r'(?<![\d.])([\d,]{3,}(?:\.\d+)?)\s*萬(?!\s*/)', headline[1].split('基本資料')[0])
        kind = re.search(r'(?:型態|類型|類別)\s*(透天厝|透天別墅|別墅/透天|透天/店面|別墅|透天)', text)
        row = dict(title=title, address=re.split(r'建坪|地坪|單價', address[0])[0] if address else '', district=district[1] if district else '',
                   type=kind[1] if kind else '', price_twd=round(price*10000) if price else None,
                   building_ping=extract(r'(?:登記建坪|建物坪數|建物|建坪)\s*([\d,.]+)\s*坪', text),
                   land_ping=extract(r'(?:登記地坪|土地坪數|土地|地坪)\s*([\d,.]+)\s*坪', text),
                   main_ping=extract(r'主建物\s*([\d,.]+)\s*坪', text),
                   age_years=(float(re.search(r'屋齡\s*(\d+(?:\.\d+)?)\s*年',text)[1]) if re.search(r'屋齡\s*(\d+(?:\.\d+)?)\s*年',text) else None), elevator=elevator(title+' '+text),
                   amenities=amenities(text))
    row.update(source=source, url=url, checked_at=datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds'))
    row['status'], row['reasons'] = qualify(row)
    return row


def get(url):
    response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'zh-TW'}, timeout=(8, 20))
    response.raise_for_status()
    if urlparse(response.url).hostname != urlparse(url).hostname:
        raise ValueError('來源跳轉到其他網域')
    return response.text


def discover(source, html, base):
    soup = BeautifulSoup(html, 'html.parser')
    pattern = SOURCES[source][1]
    links = {}
    for a in soup.select('a[href]'):
        u = urljoin(base, a['href']).split('?')[0]
        if urlparse(u).hostname == urlparse(base).hostname and re.fullmatch(pattern, urlparse(u).path):
            links[u] = a.get_text(' ', strip=True)
    if source == '永慶':
        # Some SSR lists publish stale JSON-LD IDs. Validate each title on detail pages.
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                d = json.loads(script.string)
                if d.get('@type') == 'ItemList':
                    for item in d.get('itemListElement', []):
                        u = item.get('url', '')
                        if urlparse(u).hostname == urlparse(base).hostname and re.fullmatch(pattern, urlparse(u).path):
                            links[u] = item.get('name', '')
            except (ValueError, TypeError, AttributeError):
                continue
    return links


def collect(max_pages=3, max_details=20):
    report = dict(title=CONFIG['title'], config=CONFIG, checked_at=datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds'),
                  sources=[], listings=[], pending=[], excluded=[])
    for name, (root, _) in SOURCES.items():
        state = dict(source=name, pages=0, discovered=0, parsed=0, matched=0, errors=[],
                     scope=f'最多{max_pages}頁、{max_details}詳細頁；不代表全市場', status='未完成')
        links, visited, queue = ({'https://www.sinyi.com.tw/buy/house/8443RL': '綠綠光彩森林系電梯車墅'} if name == '信義' else {}), set(), [root]
        try:
            while queue and len(visited) < max_pages:
                url = queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)
                html = get(url)
                state['pages'] += 1
                links.update(discover(name, html, root))
                soup = BeautifulSoup(html, 'html.parser')
                for a in soup.select('a[href]'):
                    u = urljoin(url, a['href'])
                    if (urlparse(u).hostname == urlparse(root).hostname and urlparse(u).path == urlparse(root).path
                            and re.search(r'(?:page|pg)=\d+', u) and u not in visited and u not in queue):
                        queue.append(u)
                if name == '幸福家':
                    node = soup.find('script', id='__NEXT_DATA__')
                    if node:
                        data = json.loads(node.string)['props']['pageProps']['listAPI']['list']
                        if data.get('current_page', 1) < data.get('last_page', 1):
                            u = root+'&page='+str(data['current_page']+1)
                            if u not in visited and u not in queue: queue.append(u)
                time.sleep(.2)
            state['discovered'] = len(links)
            for u, title in list(links.items())[:max_details]:
                try:
                    row = parse_detail(name, get(u), u, title if name == '永慶' else '')
                    state['parsed'] += 1
                    if row['status'] == '刊登符合':
                        report['listings'].append(row);state['matched'] += 1
                    elif row['status'] == '待核對':report['pending'].append(row)
                    else:report['excluded'].append(row)
                except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as exc:
                    state['errors'].append(str(exc)[:180])
                time.sleep(.2)
            state['status'] = '部分範圍已讀取' if state['parsed'] else '未取得可核對房源'
            if not links:state['errors'].append('未辨識公開詳細頁；不能解讀為沒有符合房源')
            if queue or len(links)>max_details:state['status'] += '（達本輪上限）'
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            state['status'] = '讀取失敗';state['errors'].append(str(exc)[:180])
        report['sources'].append(state)
        print(name, state['status'], 'parsed',state['parsed'],'matched',state['matched'],flush=True)
    # Do not merge street-only addresses: distinct houses can have the same areas and price.
    report['listings'].sort(key=lambda r: tuple(not r['amenities'][k] for k in CONFIG['priority'])+(r['price_twd'],))
    reviewed_path = ROOT/'tainan_reviewed.json'
    report['reviewed'] = []
    if reviewed_path.exists():
        for row in json.loads(reviewed_path.read_text()):
            if datetime.fromisoformat(row['expires_at']) > datetime.now(ZoneInfo('Asia/Taipei')):
                report['reviewed'].append(row)
    return report



def mark_new_listings(report, previous):
    # Retain historical URLs even when a source fails or a listing disappears.
    def key(row):
        parsed = urlparse(row['url'])
        return parsed.netloc.lower() + parsed.path.rstrip('/')
    known = set(previous.get('known_listing_keys', []))
    for group in ('listings', 'pending', 'reviewed', 'excluded'):
        known.update(key(r) for r in previous.get(group, []) if r.get('url'))
    current = set()
    for group in ('listings', 'pending', 'reviewed', 'excluded'):
        for row in report.get(group, []):
            if row.get('url'):
                identity = key(row)
                row['is_new_listing'] = identity not in known
                current.add(identity)
    report['known_listing_keys'] = sorted(known | current)
    return report


def messages(report):
    blocks = [f"🏡 {CONFIG['title']}\n核對：{report['checked_at']}\n安南／北區／安平／中西\n總價≤4,000萬｜建坪≥100坪｜地坪≥40坪｜屋齡≤20年｜已有電梯\n採買→交通→醫療；公園加分。無五公里限制。\n刊登符合 {len(report['listings'])} 筆；待核對 {len(report['pending'])} 筆。跨站未合併，不代表獨立房屋數。"]
    for status, rows in [('刊登符合', report['listings']), ('待核對', report['pending'])]:
        for r in rows:
            price = f"{r['price_twd']/10000:,.0f}萬" if r['price_twd'] else '待確認'
            lines = [f"{'🔴 新增｜' if r.get('is_new_listing') else ''}【{status}｜{r['source']}】{r['title'][:120]}",r['address'],
                     f"{price}｜建坪 {r['building_ping']}｜地坪 {r['land_ping']}", '屋齡：'+str(r.get('age_years'))+'年', '電梯：'+r['elevator']]
            for k in ['採買','交通','醫療','公園']:
                lines.append(k+'：'+('；'.join(r['amenities'][k]) if r['amenities'][k] else '待確認'))
            if r['reasons']:lines.append('缺項：'+'、'.join(r['reasons']))
            lines.extend(['周邊為刊登線索，名稱／距離／行車時間未獨立驗證。',r['url']])
            blocks.append('\n'.join(lines))
    for r in report.get('reviewed', []):
        blocks.append(f"{'🔴 新增｜' if r.get('is_new_listing') else ''}【人工查核補充｜非本輪自動取得】{r['title']}\n查核日期：{r['reviewed_at']}\n{r['address']}\n{r['price_twd']/10000:,.0f}萬｜建坪{r['building_ping']}坪｜地坪{r['land_ping']}坪｜屋齡約{r['age_years']}年\n電梯：刊登標示有電梯，現況待看屋\n採買：{r['shopping']}\n交通：{r['transport']}\n醫療：{r['medical']}\n公園：{r['park']}\n提醒：{r['notes']}\n{r['url']}")
    reasons = {}
    labels = {'price_twd':'總價', 'building_ping':'建坪', 'land_ping':'地坪', 'age_years':'屋齡'}
    for r in report.get('excluded', []):
        for reason in r['reasons']:
            for key, label in labels.items():reason = reason.replace(key, label)
            reasons[reason] = reasons.get(reason, 0) + 1
    blocks.append('排除原因（每筆依第一個不符條件分類，非市場總量）\n'+('\n'.join(f'{k}：{v}筆' for k,v in reasons.items()) or '本輪無可分類的排除資料'))
    blocks.append('來源狀態\n'+'\n'.join(f"{s['source']}：{s['status']}；讀取{s['parsed']}筆，錯誤{len(s['errors'])}項"+('；'+s['errors'][0] if s['errors'] else '') for s in report['sources']))
    blocks.append('🔴 新增＝本輪首次收錄的刊登，不代表屋主剛上市；跨站可能重複。僅本輪公開刊登；是否仍可售、電梯現況、合法登記面積與道路持分需看屋核對。未讀取不代表沒有房源。')
    chunks, current = [], ''
    for block in blocks:
        if len(current)+len(block)+2 > 4500:
            chunks.append(current);current=''
        current += ('\n\n' if current else '')+block
    if current:chunks.append(current)
    return chunks


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--push',action='store_true');parser.add_argument('--pages',type=int,default=3);parser.add_argument('--details',type=int,default=20)
    args=parser.parse_args()
    previous_path = ROOT/'tainan_report.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    report=mark_new_listings(collect(args.pages,args.details), previous)
    (ROOT/'tainan_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    parts=messages(report);(ROOT/'tainan_preview.txt').write_text('\n\n---\n\n'.join(parts)+'\n')
    if args.push:
        from line_notify import push_line
        push_line(parts)
    print('report/preview written; push requested:',args.push)

if __name__=='__main__':main()
