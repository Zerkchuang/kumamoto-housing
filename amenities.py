"""Listing-backed surroundings; nearby schools never imply assigned schools."""
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

KUMAMOTO = 'https://www.city.kumamoto.jp/kiji00361022/'
KIKUYO = 'https://www.town.kikuyo.lg.jp/kosodate/kiji003466/index.html'
KOSHI = 'https://www.city.koshi.lg.jp/kiji00321894/index.html'
MALL = re.compile(r'ゆめタウン|イオンモール|サクラマチ|SAKURA\s*MACHI|アミュプラザ|COCOSA|鶴屋|サンリーカリーノ', re.I)


def clean(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value))).strip()


@lru_cache(maxsize=1)
def district_catalog():
    try:
        return json.loads(Path(__file__).with_name('school_districts.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def assigned_schools(address, today=None):
    address = clean(address).replace(' ', '')
    today = today or date.today()
    catalog = district_catalog()
    result = []
    if '熊本市' in address:
        ward = re.search(r'熊本市(中央区|東区|北区|西区|南区)', address)
        if not ward or str(today) >= catalog.get('review_after', '0000-00-00'):
            return [dict(kind=label, name='待確認', note='需完整門牌及最新官方學區表', source=KUMAMOTO)
                    for label in ['小學學區', '中學學區']]
        local = address.split('熊本市' + ward[1], 1)[1]
        for kind, label in [('primary', '小學學區'), ('secondary', '中學學區')]:
            matches = []
            for row in catalog.get('rows', []):
                if row['kind'] != kind or row['ward'] != ward[1]:
                    continue
                town = clean(row['town']).replace(' ', '')
                pattern = re.escape(town)
                if town.endswith('丁目'):
                    pattern = re.escape(town[:-2]) + r'(?:丁目|(?=[^0-9]|$))'
                else:
                    pattern += r'(?=[0-9一二三四五六七八九十\-]|$)'
                if re.match(pattern, local):
                    matches.append(row)
            # A split district is not resolvable from a town/chome alone.
            names = sorted({r['school'] for r in matches})
            complete = len(names) == 1 and any('全域' in (r['residence'], r['lot']) for r in matches)
            display_names = [n + ('学校' if n.endswith(('小', '中')) else '') for n in names]
            result.append(dict(kind=label, name='／'.join(display_names) if names else '待確認',
                               note=('官方全域比對；2026/4適用' if complete else '指定學校待完整門牌確認'),
                               source=catalog.get('sources', {}).get(kind, KUMAMOTO)))
    elif '菊陽町' in address:
        # Official rules use administrative districts, not necessarily postal chome.
        # Do not infer those boundaries from an abbreviated listing address.
        note = '行政區待確認；官方依町內劃分'
        result = [dict(kind='小學學區', name='待確認', note=note, source=KIKUYO),
                  dict(kind='中學學區', name='待確認', note=note, source=KIKUYO)]
    else:
        result = [dict(kind=label, name='待確認', note='需完整門牌及官方通學區比對', source=KOSHI if '合志市' in address else '')
                  for label in ['小學學區', '中學學區']]
    return result


def distance(value):
    value = clean(value)
    parts = []
    m = re.search(r'(?<![\d.])([\d,]+(?:\.\d+)?)\s*(km|m)(?![a-zA-Z0-9])', value)
    if m:
        parts.append(m[1] + (' 公里' if m[2] == 'km' else ' 公尺'))
    walk = re.search(r'(?:徒歩|歩)\s*(約\s*)?(\d+)\s*分', value)
    if walk:
        parts.append('步行 ' + ('約 ' if walk[1] else '') + walk[2] + ' 分鐘')
    drive = re.search(r'車(?:で)?\s*(約\s*)?(\d+)\s*分', value)
    if drive:
        parts.append('開車 ' + ('約 ' if drive[1] else '') + drive[2] + ' 分鐘')
    return '／'.join(parts) if parts else '待確認'


def extract_amenities(soup, address, url):
    schools, stations, malls = [], [], []
    seen = set()

    def add(bucket, name, value, note):
        name = clean(name).strip(' :：・')[:100]
        key = (id(bucket), name, distance(value))
        if name and key not in seen:
            seen.add(key)
            bucket.append(dict(name=name, distance=distance(value), note=note))

    # SUUMO desktop facility rows are category + named facility in a single <li>.
    # Restrict to small semantic rows; never scan the page or recommendation cards.
    for node in soup.find_all(['li', 'tr', 'dl']):
        value = clean(node.get_text(' ', strip=True))
        if len(value) > 350:
            continue
        m = re.match(r'^(小学校|中学校|ショッピングセンター|百貨店)\s+(.+)$', value)
        if not m:
            continue
        name = re.split(r'[:：]|まで|(?:徒歩|歩)\s*\d', m[2], 1)[0].strip()
        if m[1] in {'小学校', '中学校'}:
            add(schools, name, m[2], '刊登周邊學校；非指定學區')
        elif MALL.search(name):
            add(malls, name, m[2], '刊登距離；非實測')

    # SUUMO mobile uses facility cassettes, not the desktop category rows.
    for node in soup.select('.shuhenkankyo-info__item'):
        caption = node.select_one('.shuhenkankyo-info__cassette__info__caption')
        if not caption:
            continue
        name = clean(caption.get_text(' ', strip=True))
        value = clean(node.get_text(' ', strip=True))
        if name.endswith(('小学校', '中学校')):
            add(schools, name, value, '刊登周邊學校；非指定學區')
        elif MALL.search(name):
            add(malls, name, value, '刊登距離；非實測')
    for node in soup.select('.shuhenkankyo-images__list__cassette__text-wrap__shisetsu'):
        value = clean(node.get_text(' ', strip=True))
        name = re.split(r'まで|[:：]', value, 1)[0]
        if name.endswith(('小学校', '中学校')):
            add(schools, name, value, '刊登周邊學校；非指定學區')
        elif MALL.search(name):
            add(malls, name, value, '刊登距離；非實測')

    for h in soup.find_all(['th', 'dt']):
        label = clean(h.get_text(' ', strip=True))
        d = h.find_next_sibling('td' if h.name == 'th' else 'dd')
        if not d:
            continue
        value = clean(d.get_text(' ', strip=True))
        if label.startswith(('交通', '最寄駅', '沿線・駅')):
            # A bus stop must not become a rail station; reject bus-only itineraries.
            for m in re.finditer(r'(JR[^\s「」]{0,25}|熊本(?:電気鉄道|電鉄|市電)[^\s「」]{0,15}|[^\s「」]{1,25}線)「([^」]{1,60})」', value):
                route = m[1].strip()
                remainder = value[m.end():]
                if re.match(r'\s*バス', remainder):
                    continue
                access = re.split(r'JR|熊本(?:電気鉄道|電鉄|市電)|バス|乗り換え', remainder, 1)[0][:60]
                if 'バス' in route:
                    continue
                if not re.search(r'JR|ＪＲ|鉄道|電鉄|市電|線|駅', route):
                    continue
                add(stations, m[2] + '（' + route[-40:] + '）', access, '刊登交通；未核對最近站')
        elif label in {'小学校', '中学校', '小学校区', '中学校区', '周辺環境', '周辺施設'}:
            # Broker fields: keep the named school, but no official assignment claim.
            for part in re.split(r'[\n／/]|(?<=m)\s+(?=[^\d])', value):
                m = re.search(r'([^:：、。]{1,70}(?:小学校|中学校))', part)
                if m:
                    add(schools, m[1], part[m.end():], '刊登學校；指定學區待官方確認')
            for m in re.finditer(r'([^:：、。]{0,30}(?:ゆめタウン|イオンモール|サクラマチ|アミュプラザ)[^:：、。\d]{0,30})[:：]?\s*([^、。]{0,35})', value):
                add(malls, m[1], m[2], '刊登距離；非實測')

    return dict(schools=schools, stations=stations, malls=malls,
                districts=assigned_schools(address), source=url,
                checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))


def amenity_rows(row):
    try:
        data = json.loads(row.get('amenities_json') or '{}')
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    result = []
    for district in data.get('districts') or assigned_schools(row.get('address', '')):
        result.append({'項目': district['kind'], '名稱': district['name'], '距離／確認狀態': district['note']})
    for key, label in [('schools', '附近學校'), ('stations', '車站'), ('malls', '大型商場')]:
        entries = data.get(key) or []
        if not entries:
            result.append({'項目': label, '名稱': '待確認', '距離／確認狀態': '來源未提供可核對資訊'})
        for item in entries:
            result.append({'項目': label, '名稱': item['name'],
                           '距離／確認狀態': item['distance'] + '；' + item['note']})
    return result


def amenity_text(row):
    # LINE text messages do not render Markdown; use a compact column table.
    lines = ['周邊資訊｜名稱｜距離／確認']
    for item in amenity_rows(row):
        lines.append('｜'.join(item.values()))
    return '\n'.join(lines)


def amenity_summary(row, category):
    return '；'.join(item['名稱'] + '：' + item['距離／確認狀態']
                    for item in amenity_rows(row) if item['項目'] == category)
