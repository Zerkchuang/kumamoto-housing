"""Public broker listings and explicit connectivity diagnostics.
Only public detail pages are read; access-denied responses are never bypassed.
"""
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs
import requests
from bs4 import BeautifulSoup

BROKERS = [
    ('tatara', 'たたら不動產', 'https://www.tatara-fudousan.com/', '首頁公開房源；不含會員物件'),
    ('smtrc', '三井住友トラスト不動產', 'https://smtrc.jp/list/listViewNewDayPrice/index?prefcode=43&newProperty=5', '熊本最近7日新增／改價'),
]
PROBES = [
    ('homes', "HOME’S", 'https://www.homes.co.jp/'),
    ('athome', 'at home', 'https://www.athome.co.jp/'),
    ('meiwa', '明和不動產', 'https://www.meiwa.jp/'),
]

def text(el):
    return unicodedata.normalize('NFKC', el.get_text(' ', strip=True)).strip()


def valid_detail_url(pid, url):
    p = urlparse(url)
    if p.scheme != 'https':
        return False
    if pid.startswith('suumo_'):
        return p.hostname == 'suumo.jp' and bool(re.fullmatch(r'/(?:ms/(?:chuko|shinchiku)|chukoikkodate|ikkodate)/kumamoto/[^/]+/nc_' + re.escape(pid[6:]) + r'/', p.path))
    if pid.startswith('tatara_'):
        return p.hostname == 'www.tatara-fudousan.com' and p.path.rstrip('/') == '/property_detail/' + pid[7:]
    if pid.startswith('smtrc_'):
        return p.hostname == 'smtrc.jp' and p.path == '/detail/CompareDetails' and parse_qs(p.query).get('propertyCode') == [pid[6:]]
    return False


def discover(soup, base, source):
    found = {}
    for a in soup.select('a[href]'):
        url = urljoin(base, a['href'])
        p = urlparse(url)
        if source == 'tatara':
            m = re.fullmatch(r'/property_detail/(\d+)/?', p.path)
            pid = 'tatara_' + m[1] if m else ''
        else:
            code = parse_qs(p.query).get('propertyCode', [''])[0]
            pid = 'smtrc_' + code if re.fullmatch(r'[A-Za-z0-9]+', code) else ''
        if pid and valid_detail_url(pid, url):
            found[pid] = url
    return found


def parse_detail(soup, pid, url, config):
    fields = {}
    for h in soup.find_all('th'):
        d = h.find_next_sibling('td')
        if d:
            fields.setdefault(text(h), text(d))  # property address precedes broker address
    def field(*keys):
        return next((fields[k] for k in keys if k in fields), '')
    address = field('所在地')
    if not address or not field('価格'):
        return None, 'unreadable'  # includes member-only or expired pages
    region = config.parse_region(address, '', '')
    house_regions = {'菊陽町', '合志市', '光之森周邊', '熊本市東區', '熊本市北區'}
    # Do not accept other towns in Kikuchi county via the legacy broad region mapping.
    if '菊池郡' in address and '菊陽町' not in address:
        return None, 'filtered'
    condo = bool(field('専有面積'))
    kind = 'condo' if condo else 'house'
    if region not in (config.CONDO_REGIONS if condo else house_regions):
        return None, 'filtered'
    date = field('築年月', '建築年月')
    if not date or not config.within_age_limit(date):
        return None, 'filtered'
    def number(value):
        m = re.search(r'(\d[\d,]*(?:\.\d+)?)', value)
        return float(m[1].replace(',', '')) if m else 0
    price_text = field('価格')
    if '億' in price_text or not re.fullmatch(r'[\d,]+(?:\.\d+)?万円', price_text.replace(' ', '')):
        return None, 'unreadable'
    price = round(number(price_text) * 10000)
    area = number(field('専有面積') if condo else field('延床面積', '建物延面積', '建物面積'))
    land = 0 if condo else number(field('土地面積'))
    if not (0 < price <= config.MAX_PRICE):
        return None, 'filtered'
    if area < (config.MIN_CONDO_AREA if condo else config.MIN_HOUSE_BUILDING):
        return None, 'filtered'
    if not condo and land < config.MIN_HOUSE_LAND:
        return None, 'filtered'
    title_node = soup.find('title')
    title = text(title_node).split('｜')[0] if title_node else address
    return dict(property_id=pid, title=title, url=url, region=region, address=address,
                current_price=price, land_area=land, building_area=area,
                layout=field('間取り'), build_year=date, property_type=kind), 'matched'


def collect_broker(source, name, url, scope, config):
    status = dict(source=name, scope=scope, discovered=0, matched=0, errors=0, unreadable=0, status='未執行')
    items = []
    try:
        r = requests.get(url, timeout=25)
        r.raise_for_status()
        links = discover(BeautifulSoup(r.content, 'html.parser'), url, source)
        status['discovered'] = len(links)
        if not links:
            status['status'] = '未辨識到詳細頁；待檢查'
            return items, status
        def fetch(pair):
            pid, link = pair
            try:
                page = requests.get(link, timeout=25)
                page.raise_for_status()
                if not valid_detail_url(pid, page.url):
                    return None, 'unreadable'
                return parse_detail(BeautifulSoup(page.content, 'html.parser'), pid, link, config)
            except requests.RequestException:
                return None, 'error'
        with ThreadPoolExecutor(max_workers=3) as pool:
            for item, result in pool.map(fetch, links.items()):
                if item:
                    items.append(item)
                if result == 'error':
                    status['errors'] += 1
                if result == 'unreadable':
                    status['unreadable'] += 1
        status['matched'] = len(items)
        status['status'] = '部分完成' if status['errors'] or status['unreadable'] else '完成'
    except requests.RequestException as exc:
        code = exc.response.status_code if getattr(exc, 'response', None) is not None else 'timeout/network'
        status['status'] = f'連線受阻 {code}'
        status['errors'] = 1
    return items, status


def collect(config):
    items, statuses = [], []
    for args in BROKERS:
        found, status = collect_broker(*args, config)
        items.extend(found)
        statuses.append(status)
        print('SOURCE_STATUS', status, flush=True)
    for _, name, url in PROBES:
        status = dict(source=name, scope='連線測試；尚未接通物件解析', discovered=0, matched=0, errors=0, unreadable=0)
        try:
            r = requests.get(url, timeout=15)
            status['status'] = f'連線受阻 HTTP {r.status_code}' if r.status_code >= 400 else '首頁可連；物件解析待接通'
        except requests.RequestException:
            status['status'] = '連線逾時／網路錯誤'
        statuses.append(status)
        print('SOURCE_STATUS', status, flush=True)
    return items, statuses
