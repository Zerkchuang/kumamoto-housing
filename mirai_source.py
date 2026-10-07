"""Public Kumamoto Mirai listings, with incomplete leads kept outside verified inventory."""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from amenities import clean, extract_amenities, amenity_text

ROOT = 'https://kumamoto-mirai.tw/region/kumamoto/'
HOME = 'https://kumamoto-mirai.tw/'


def identity(url):
    p = urlparse(url)
    m = re.fullmatch(r'/our-property-info/(ours|partners)/(\d+)/?', p.path)
    return 'mirai_' + m[1] + '_' + m[2] if p.scheme == 'https' and p.hostname == 'kumamoto-mirai.tw' and not p.query and m else ''


def parse_listing(soup, url, config):
    pid = identity(url)
    article = soup.select_one('article')
    heading = article.find('h1') if article else None
    body = article.select_one('.post_content') if article else None
    if not pid or not heading or not body:
        return None, None, 'unreadable'
    title = clean(heading.get_text(' ', strip=True))
    if re.search(r'已完售|已售出|已成交|成約済|售罄|完売', title):
        return None, None, 'filtered'
    if not re.search(r'透天|一戶建|一戸建|獨棟|住宅|公寓|大樓|豪宅|若水山與', title):
        return None, None, 'filtered'
    if re.search(r'分讓地|分譲地|土地|整棟|全棟|投資物件|非公開', title):
        return None, None, 'filtered'
    raw = body.get_text('\n', strip=True)
    def field(labels):
        m = re.search(r'(?:' + labels + r')\s*[:：]\s*([^\n]{1,160})', raw)
        return clean(m[1]) if m else ''
    address = field('所在地|地點|地址|物件位置')
    scope = address or title
    normalized = scope.replace('區', '区').replace('熊本東区', '熊本市東区').replace('熊本中央区', '熊本市中央区').replace('熊本北区', '熊本市北区')
    region = config.parse_region(normalized, '', '')
    condo = bool(re.search(r'公寓|大樓|マンション', title))
    if region and region not in (config.CONDO_REGIONS if condo else config.HOUSE_REGIONS):
        return None, None, 'filtered'
    amount = field('售價|價格|金額|販売価格|価格').replace('萬', '万').replace('億', '億')
    amount = re.sub(r'日幣|日圓|日元|日円', '円', amount)
    price = config.parse_price(amount)
    def area(labels):
        value = field(labels).replace('㎡', 'm2').replace('平方米', 'm2')
        m = re.match(r'([\d,]+(?:\.\d+)?)\s*m(?:2|²)', value)
        return float(m[1].replace(',', '')) if m else 0
    land = area('土地面積|基地面積')
    building = area('專有面積|専有面積' if condo else '建物面積|建築面積|建物延面積')
    built = field('完工年月|完工時期|築年月|建築年月|完成時期')
    built = built.replace('/', '年', 1) if re.fullmatch(r'\d{4}/\d{1,2}', built) else built
    if re.fullmatch(r'\d{4}年\d{1,2}', built):
        built += '月'
    date_match = re.search(r'\d{4}年\d{1,2}月', built)
    built = date_match[0] if date_match else ''
    hikari = config.is_hikari(normalized)
    if built and not config.within_age_limit(built, strict=hikari):
        return None, None, 'filtered'
    if building and condo and building < config.MIN_CONDO_AREA:
        return None, None, 'filtered'
    if not hikari and not condo and ((price and price > config.MAX_PRICE) or (land and land < config.MIN_HOUSE_LAND) or (building and building < config.MIN_HOUSE_BUILDING)):
        return None, None, 'filtered'
    missing = []
    if not address: missing.append('地址')
    if not region: missing.append('區域')
    if not price: missing.append('售價')
    if not building: missing.append('建物／專有面積')
    if not condo and not land: missing.append('土地面積')
    if not built: missing.append('完工年月／屋齡')
    amenities = extract_amenities(body, normalized if address else '', url)
    row = dict(property_id=pid, title=title, url=url, region=region or '區域待確認',
               address=address or '地址待確認', current_price=price, land_area=land,
               building_area=building, layout=field('格局|間取り'), build_year=built,
               property_type=('condo_new' if '預售' in title else 'condo') if condo else 'house',
               price_basis=config.PRICE_BASIS, amenities_json=json.dumps(amenities, ensure_ascii=False))
    if missing:
        row['missing_fields'] = missing
        return None, row, 'pending'
    return row, None, 'matched'


def collect_mirai(config):
    status = dict(source='熊本未來', scope='熊本地區公開分頁及首頁；自有／合作企業住宅',
                  discovered=0, matched=0, errors=0, unreadable=0, pending_listings=[])
    pages, visited, links = {ROOT, HOME}, set(), set()
    while pages and len(visited) < 20:
        url = min(pages); pages.remove(url); visited.add(url)
        try:
            r = requests.get(url, timeout=25); r.raise_for_status()
            s = BeautifulSoup(r.content, 'html.parser')
            for a in s.select('a[href]'):
                target = urljoin(url, a['href'])
                if identity(target): links.add(target)
                if re.fullmatch(re.escape(ROOT) + r'page/\d+/', target) and target not in visited:
                    pages.add(target)
        except requests.RequestException:
            status['errors'] += 1
    status['scope'] += f'；列表 {len(visited)} 頁'
    if pages: status['errors'] += 1
    status['discovered'] = len(links)
    def fetch(url):
        try:
            r = requests.get(url, timeout=25); r.raise_for_status()
            if identity(r.url) != identity(url): return None, None, 'unreadable'
            return parse_listing(BeautifulSoup(r.content, 'html.parser'), url, config)
        except (requests.RequestException, ValueError):
            return None, None, 'error'
    items = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for item, pending, outcome in pool.map(fetch, sorted(links)):
            if item: items.append(item)
            if pending: status['pending_listings'].append(pending)
            if outcome == 'error': status['errors'] += 1
            if outcome == 'unreadable': status['unreadable'] += 1
    status['matched'] = len(items)
    status['pending'] = len(status['pending_listings'])
    status['status'] = '部分完成' if status['errors'] or status['unreadable'] else ('完成' if links else '未辨識到詳細頁；待檢查')
    return items, status


def pending_blocks(report):
    blocks = []
    for source in report.get('sources', []):
        for row in source.get('pending_listings', []):
            if identity(row.get('url', '')) != row.get('property_id'):
                continue
            blocks.append(f"【熊本未來｜待核對，尚未確認符合條件】\n{row['title'][:180]}\n"
                          f"缺少：{'、'.join(row['missing_fields'])}\n{row['url']}\n{amenity_text(row)}")
    return blocks
