"""Separate Sekisui collections: requested areas, <=15 years, no price/area floor."""
import hashlib
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
from amenities import clean, extract_amenities, amenity_text

HOUSE = '積水ハウス'
HEIM = '積水ハイム'
HOUSE_NEW = 'https://www.sekisuihouse.co.jp/bunjou/43kumamoto'
HOUSE_USED = 'https://sumusite.sekisuihouse.co.jp/kyushu/special/sekisui/kumamoto/'
HEIM_NEW = 'https://www.heim-k.com/land/'
AREA_NOTE = '中央區、東區、合志市、菊陽町（含光之森）、大津町'


def region(address):
    value = clean(address).replace(' ', '')
    for jp, zh in [('熊本市中央区', '熊本市中央區'), ('熊本市東区', '熊本市東區'),
                   ('合志市', '合志市'), ('菊陽町', '菊陽町'), ('大津町', '大津町')]:
        if jp in value:
            return '光之森' if jp == '菊陽町' and '光の森' in value else zh
    return ''


def valid_url(url):
    p = urlparse(url)
    return p.scheme == 'https' and not p.query and (
        (p.hostname == 'www.sekisuihouse.co.jp' and bool(re.fullmatch(r'/bunjou/0/43\d+/[^/]+/[^/]+/\d+/06top.html', p.path)))
        or (p.hostname == 'sumusite.sekisuihouse.co.jp' and bool(re.fullmatch(r'/kyushu/kodate/detail/[A-Z0-9]+/', p.path)))
        or (p.hostname == 'www.heim-k.com' and bool(re.fullmatch(r'/land/[a-z0-9-]+/', p.path)))
        or (p.hostname == 'suumo.jp' and bool(re.fullmatch(r'/(?:chukoikkodate|ikkodate)/kumamoto/[^/]+/nc_\d+/', p.path))))


def get_soup(url):
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    if urlparse(r.url).hostname != urlparse(url).hostname:
        raise ValueError('Unexpected source redirect')
    return BeautifulSoup(r.content, 'html.parser')


def label(soup, names):
    for h in soup.find_all(['dt', 'th']):
        if any(clean(h.get_text()).startswith(n) for n in names):
            d = h.find_next_sibling('dd' if h.name == 'dt' else 'td')
            if d: return clean(d.get_text(' ', strip=True))
    return ''


def amount(soup, names):
    value = label(soup, names)
    m = re.match(r'([\d,]+(?:\.\d+)?)\s*(?:㎡|m2|m²)', value)
    return float(m[1].replace(',', '')) if m else None


def build_date(value):
    value = clean(value)
    m = re.search(r'(\d{4})年\s*(\d{1,2})月', value)
    if m: return f'{m[1]}年{int(m[2])}月'
    m = re.search(r'(令和|平成|昭和)(元|\d+)年\s*(\d{1,2})月', value)
    if m:
        year = {'令和':2018, '平成':1988, '昭和':1925}[m[1]] + (1 if m[2] == '元' else int(m[2]))
        return f'{year}年{int(m[3])}月'
    return ''


def make_row(brand, kind, title, url, address, price='', land=None, building=None, built='', amenities=None):
    price = clean(price)
    yen = re.fullmatch(r'([\d,]+)\s*円', price)
    if yen:
        price = f"{int(yen[1].replace(',', ''))/10000:,.2f}".rstrip('0').rstrip('.') + '萬円'
    return dict(property_id='sek_' + hashlib.sha1(url.encode()).hexdigest()[:16], brand=brand,
                kind=kind, title=clean(title), url=url, address=clean(address), region=region(address),
                price_display=price or '價格待確認', land_area=land, building_area=building,
                build_year=build_date(built), amenities_json=json.dumps(amenities or {}, ensure_ascii=False))


def classify(row, config):
    if not row['region'] or not valid_url(row['url']): return 'outside'
    if row['kind'] == '土地（需另建住宅）': return 'land'
    if not row['build_year']: return 'pending'
    try:
        return 'house' if config.within_age_limit(row['build_year'], strict=False) else 'old'
    except ValueError:
        return 'pending'


def parse_house_new(soup):
    rows = []
    for node in soup.select('li[data-b-propertyid]'):
        a = node.select_one('a.pref-card[href]')
        address = node.select_one('.pref-card-address')
        title = node.select_one('.pref-card-name')
        price = node.select_one('.pref-card-price')
        if not (a and address and title): continue
        is_land = node.get('data-b-tatemono') == '0'
        row = make_row(HOUSE, '土地（需另建住宅）' if is_land else '新築建案（戶別待確認）',
                             title.get_text(), urljoin(HOUSE_NEW, a['href']), address.get_text(),
                             price.get_text() if price else '',
                             land=float(node['data-menseki']) if node.get('data-menseki') else None)
        spec = node.select_one('.pref-card-spec')
        if spec: row['land_area_note'] = clean(spec.get_text(' ',strip=True))
        rows.append(row)
    return rows


def parse_house_used(soup):
    rows = []
    for a in soup.select('a[href*="/kyushu/kodate/detail/"]'):
        title, address = a.select_one('.textTitle'), a.select_one('.add')
        if not title or not address: continue
        # An official broker can sell other builders' homes: require construction evidence.
        text = clean(a.get_text(' ', strip=True))
        if not re.search(r'積水ハウス(?:株式会社|㈱)?施工|積水ハウス施工', text): continue
        price = label(a, ['価格'])
        def number(pattern):
            m = re.search(pattern + r'\s*([\d,.]+)(?:㎡|m2|m²)', text)
            return float(m[1].replace(',', '')) if m else None
        estate = a.select_one('.estate')
        dates = [clean(n.get_text()) for n in estate.select('li')] if estate else []
        built = next((value for value in dates if re.match(r'^(?:\d{4}|令和|平成|昭和)',value) and '年' in value and '月' in value), '')
        rows.append(make_row(HOUSE, '中古一戶建', title.get_text(), a['href'], address.get_text(),
                             price, number('土地'), number('建物'), built,
                             extract_amenities(a, address.get_text(), a['href'])))
    return rows


def parse_heim_unit(soup, url, title=''):
    address = label(soup, ['所在地'])
    heading = soup.find('h1')
    return make_row(HEIM, '建售一戶建', heading.get_text() if heading else title, url, address,
                    label(soup, ['販売価格']), amount(soup, ['土地面積']), amount(soup, ['建物面積']),
                    label(soup, ['完成年月', '建築年月']), extract_amenities(soup, address, url))


def collect_primary(config):
    rows, statuses = [], []
    for name, root, parser in [('積水ハウス／分售', HOUSE_NEW, parse_house_new),
                                ('積水ハウス／中古施工住宅', HOUSE_USED, parse_house_used)]:
        status = dict(source=name, scope=AREA_NOTE+'；無價格／面積限制；住宅15年內', discovered=0,matched=0,errors=0,unreadable=0)
        pending, visited = {root}, set()
        try:
            while pending and len(visited) < 20:
                page = min(pending); pending.remove(page); visited.add(page)
                soup = get_soup(page)
                found = parser(soup); rows.extend(found); status['discovered'] += len(found)
                for a in soup.select('a[href]'):
                    target = urljoin(page, a['href'])
                    if target.startswith(root+'?page=') and re.fullmatch(r'\d+', target.split('?page=')[-1]) and target not in visited:
                        pending.add(target)
            if pending: status['errors'] += 1
            status['status'] = '完成' if status['discovered'] and not status['errors'] else '未完整／未辨識物件'
        except (requests.RequestException, ValueError):
            status['errors'] += 1; status['status'] = '來源讀取失敗'
        statuses.append(status)
    status = dict(source='積水ハイム／分售',scope=AREA_NOTE+'；官方列表及全部公開戶別',discovered=0,matched=0,errors=0,unreadable=0)
    try:
        soup = get_soup(HEIM_NEW)
        projects = {}
        for node in soup.select('.listData'):
            address = label(node, ['所在地'])
            a, title = node.select_one('a[href*="/land/"]'), node.select_one('.title')
            if region(address) and a and title:
                projects[a['href']] = (clean(title.get_text()), address, label(node, ['販売区画']))
        status['discovered'] = len(projects)
        def project(pair):
            url, (title, address, slots) = pair
            out = []
            try:
                detail = get_soup(url)
                # Keep land projects separate; parent listing is not a completed house.
                if re.search(r'土地\s*[:：]\s*[1-9]', slots):
                    out.append(make_row(HEIM, '土地（需另建住宅）', title, url, address))
                unit_links = {urljoin(url, a['href']) for a in detail.select('#house_datatable a[href]') if '/land/' in a['href']}
                for unit in sorted(unit_links):
                    try: out.append(parse_heim_unit(get_soup(unit), unit, title))
                    except (requests.RequestException, ValueError): status['errors'] += 1
                if not unit_links and re.search(r'戸建\s*[:：]\s*[1-9]', slots): status['unreadable'] += 1
            except (requests.RequestException, ValueError): status['errors'] += 1
            return out
        with ThreadPoolExecutor(max_workers=3) as pool:
            for found in pool.map(project, projects.items()): rows.extend(found)
        status['status'] = '部分完成' if status['errors'] or status['unreadable'] else ('完成' if projects else '未辨識到物件')
    except (requests.RequestException, ValueError):
        status['errors'] += 1; status['status'] = '來源讀取失敗'
    statuses.append(status)
    return rows, statuses


def collect_suumo(config):
    rows, statuses = [], []
    for brand, keyword in [(HOUSE, '熊本　積水ハウス'), (HEIM, 'セキスイハイム　熊本')]:
        from urllib.parse import quote
        root = 'https://suumo.jp/b/kodate/kw/' + quote(keyword, safe='') + '/'
        status = dict(source=brand+'／SUUMO補查', scope='同一關鍵字全部分頁；核對施工品牌與所在地；上限20頁', discovered=0,matched=0,errors=0,unreadable=0)
        pages, visited, links = {root}, set(), {}
        while pages and len(visited) < 20:
            page = min(pages); pages.remove(page); visited.add(page)
            try:
                soup = get_soup(page)
                for card in soup.select('.cassette'):
                    address = ''
                    for h in card.select('.cassette_item-header'):
                        if clean(h.get_text()) == '所在地':
                            d = h.find_next_sibling(class_='cassette_item-body')
                            if d: address = clean(d.get_text(' ',strip=True))
                    if not region(address): continue
                    for a in card.select('a[href]'):
                        url = urljoin(page, a['href']).split('?')[0]
                        if valid_url(url) and urlparse(url).hostname == 'suumo.jp': links[url] = address
                for a in soup.select('a[href]'):
                    target = urljoin(page, a['href']).split('?')[0]
                    if re.fullmatch(re.escape(unquote(urlparse(root).path)) + r'\d+/', unquote(urlparse(target).path)) and target not in visited:
                        pages.add(target)
            except (requests.RequestException, ValueError): status['errors'] += 1
        if pages: status['errors'] += 1
        status['scope'] += f'；已讀{len(visited)}頁'
        status['discovered'] = len(links)
        def fetch(pair):
            url, _ = pair
            try:
                r = requests.get(url,headers=config.HEADERS,timeout=25); r.raise_for_status()
                if r.url.split('?')[0] != url: raise ValueError('Detail redirect')
                soup = BeautifulSoup(r.content, 'html.parser')
                heading = soup.find('h1')
                desc = soup.find('meta',attrs={'name':'description'})
                lead = soup.select_one('.lead-bun__text-area')
                evidence = clean(' '.join([heading.get_text() if heading else '', desc.get('content','') if desc else '',lead.get_text(' ',strip=True) if lead else '',label(soup,['備考','建物の特徴'])])).replace(' ', '')
                maker = '積水ハウス' if brand == HOUSE else 'セキスイハイム'
                if not re.search(re.escape(maker)+r'(?:株式会社|㈱)?(?:施工|の(?:家|平屋|中古住宅|住宅)|で建築|による)', evidence):
                    status['unreadable'] += 1; return None
                address = config.sale_field(soup,['所在地'])
                if not region(address): return None
                price = config.advertised_price(soup)
                kind = '新築一戶建' if '/ikkodate/' in url else '中古一戶建'
                built = label(soup,['完成時期','築年月','完成予定時期'])
                return make_row(brand,kind,heading.get_text() if heading else address,url,address,
                                f'{price/10000:,.0f}萬円' if price else '價格待確認',
                                config.field_area(soup,'土地面積'),config.field_area(soup,'建物面積'),built,
                                extract_amenities(soup,address,url))
            except (requests.RequestException, ValueError): status['errors'] += 1; return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            for row in pool.map(fetch, links.items()):
                if row: rows.append(row)
        status['status'] = '部分完成' if status['errors'] or status['unreadable'] else ('完成' if links else '未辨識到物件')
        statuses.append(status)
    return rows, statuses


def collect(config, supplement=True):
    rows, statuses = collect_primary(config)
    if supplement:
        found, extra = collect_suumo(config); rows.extend(found); statuses.extend(extra)
    unique = {r['url']:r for r in rows}
    groups = {'house':[], 'land':[], 'pending':[]}
    for row in unique.values():
        outcome = classify(row, config)
        if outcome in groups: groups[outcome].append(row)
    return dict(builder_checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                builder_policy=dict(areas=AREA_NOTE,max_age_years=15,price_limit=None,area_limit=None),
                builder_listings=groups['house'],builder_land=groups['land'],builder_pending=groups['pending'],builder_sources=statuses)


def builder_blocks(report, brand=None):
    blocks = []
    if not report.get('builder_checked_at'): return ['積水專區尚未更新，請輸入「更新積水」。']
    checked = datetime.fromisoformat(report['builder_checked_at'])
    if checked.tzinfo is None or (datetime.now(timezone.utc)-checked).total_seconds()>48*3600:
        return ['積水專區已超過48小時未核對，請輸入「更新積水」。']
    blocks.append(f"積水獨立專區｜{report['builder_checked_at']}\n{AREA_NOTE}；住宅15年內、無價格／面積限制。以下為刊登，不等於仲介確認可售；跨站可能重複。")
    for maker in [HOUSE, HEIM]:
        if brand and brand != maker: continue
        for key, title in [('builder_listings','住宅｜15年內'),('builder_pending','住宅線索｜屋齡待確認'),('builder_land','土地｜未含另建住宅費用')]:
            rows = [r for r in report.get(key,[]) if r['brand']==maker and region(r['address']) and valid_url(r['url'])]
            blocks.append(f'【{maker}｜{title}｜{len(rows)}筆刊登】')
            for r in rows:
                land = r.get('land_area_note') or (f"{r['land_area']}㎡" if r.get('land_area') is not None else '待確認')
                area = f"{r['building_area']}㎡" if r.get('building_area') is not None else '待確認'
                blocks.append(f"{r['title'][:150]}\n{r['price_display']}｜土地{land}／建物{area}\n{r.get('build_year') or '完工年月待確認'}\n{r['address']}\n{r['url']}\n{amenity_text(r)}")
    for source in report.get('builder_sources',[]):
        blocks.append(f"{source['source']}：{source['status']}；詳細／建案{source['discovered']}，錯誤{source['errors']}，未能核對{source['unreadable']}。{source['scope']}")
    return blocks


def builder_pages(report, brand=None):
    pages, current = [], ''
    for block in builder_blocks(report, brand):
        if len(block)>4200: raise ValueError('Builder detail exceeds message limit')
        if len(current)+len(block)+2>4200:
            pages.append(current); current=''
        current += ('\n\n' if current else '')+block
    if current: pages.append(current)
    return pages


def load_report(path):
    with sqlite3.connect(path) as db:
        row = db.execute('SELECT payload FROM run_report WHERE id=1').fetchone()
    return json.loads(row[0]) if row else {}


def save_report(path, builder):
    with sqlite3.connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT payload FROM run_report WHERE id=1').fetchone()
        if not row: raise ValueError('Main inventory report missing')
        report = json.loads(row[0]); report.update(builder)
        db.execute('UPDATE run_report SET payload=? WHERE id=1',(json.dumps(report,ensure_ascii=False),))
