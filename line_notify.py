import json
import os
import sqlite3
import hmac
import hashlib
import time
from urllib.parse import urlparse
import requests
from extra_sources import valid_detail_url

DB_NAME = 'kumamoto_properties.db'


def load_report():
    with sqlite3.connect(DB_NAME) as conn:
        row = conn.execute('SELECT payload FROM run_report WHERE id=1').fetchone()
    if not row:
        raise RuntimeError('No report from current crawl')
    return json.loads(row[0])


def get_matching_properties(report):
    ids = set(report['property_ids'])
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM properties WHERE status='active'").fetchall()
    rows = [r for r in rows if r['property_id'] in ids]
    for row in rows:
        if not valid_detail_url(row['property_id'], row['url']):
            raise RuntimeError('Unverified detail URL: ' + row['property_id'])
    return sorted(rows, key=lambda r: (
        0 if r['property_type'] not in {'house', 'house_new'} and r['building_area'] >= 132.23 else 1,
        r['region'], r['property_type'], r['current_price']))


def build_messages(report, rows):
    house_count = sum(r['property_type'] in {'house', 'house_new'} for r in rows)
    new_house_count = sum(r['property_type'] == 'house_new' for r in rows)
    condo_count = len(rows) - house_count
    blocks = [f"🏡 熊本購屋搜尋狀態\n{report['checked_at']}\n本次候選刊登 {len(rows)} 筆（跨站可能重複）\n一戶建 {house_count} 筆（新築 {new_house_count} 筆）；大樓／新築大樓 {condo_count} 筆。\n預算台幣1,500萬（換算上限72,992,700円）｜屋齡15年內\n一戶建土地≥200㎡、建物≥100㎡；大樓專有面積約40坪優先"]
    for s in report['sources']:
        blocks.append(f"【{s['source']}】{s['status']}\n範圍：{s['scope']}\n詳細頁 {s['discovered']}／候選 {s['matched']}／讀取錯誤 {s['errors']}／無法解析或非公開 {s.get('unreadable', 0)}")
    blocks.append('以下是本次讀取的候選物件；網站刊登不等於仲介已確認仍可售。大樓為面積候選，管理品質與實際室內淨面積待確認。')
    for r in rows:
        kind = r['property_type']
        label = {'house': '一戶建', 'house_new': '新築一戶建', 'condo': '大樓候選', 'condo_new': '新築大樓建案線索'}.get(kind, kind)
        source = {'suumo': 'SUUMO', 'tatara': 'たたら', 'smtrc': '三井住友トラスト'}.get(r['property_id'].split('_')[0], '')
        price = f"{r['current_price']/10000:,.0f}萬円" if r['current_price'] else '價格未定；預算未確認'
        is_house = kind in {'house', 'house_new'}
        area = (f"土地 {r['land_area']:.2f}㎡｜建物 {r['building_area']:.2f}㎡" if is_house
                else f"專有面積 {r['building_area']:.2f}㎡（{r['building_area']*0.3025:.1f}坪）")
        if kind in {'house_new', 'condo_new'}:
            area += '；建案面積與價格區間需確認是否屬同一戶'
        star = '⭐ 約40坪優先 ' if not is_house and r['building_area'] >= 132.23 else ''
        blocks.append(f"{star}【{r['region']}｜{label}｜{source}】\n{r['title'][:180]}\n{price}｜{area}\n{r['layout']}｜{r['build_year']}\n{r['url']}")
    chunks, current = [], ''
    for block in blocks:
        if len(block) > 4500:
            raise RuntimeError('A property block exceeds LINE limit')
        joined = current + '\n\n' + block if current else block
        if len(joined) > 4500:
            chunks.append(current)
            current = block
        else:
            current = joined
    if current:
        chunks.append(current)
    return chunks


def resolve_target(token):
    endpoint = os.getenv('LINE_PUSH_TARGET_URL')
    if endpoint:
        signature = hmac.new(token.encode(), b'get-push-target', hashlib.sha256).hexdigest()
        for attempt in range(3):
            try:
                response = requests.get(endpoint, headers={'X-Push-Signature': signature}, timeout=60)
                response.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(10 * (attempt + 1))
        target = response.json().get('target')
        if not target:
            raise RuntimeError('Render returned no LINE push target')
        return target
    return os.getenv('LINE_USER_ID')


def push_line(messages):
    token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
    if not token:
        raise RuntimeError('LINE channel access token not configured')
    target = resolve_target(token)
    if not target:
        raise RuntimeError('LINE push target not configured')
    # Five messages per API call; never silently truncate the remaining results.
    for start in range(0, len(messages), 5):
        r = requests.post('https://api.line.me/v2/bot/message/push',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={'to': target, 'messages': [{'type': 'text', 'text': part} for part in messages[start:start+5]]}, timeout=20)
        r.raise_for_status()
        print(f'LINE batch {start//5+1} accepted: HTTP {r.status_code}')
    print(f'LINE notification sent successfully: {len(messages)} message chunks')


if __name__ == '__main__':
    report = load_report()
    rows = get_matching_properties(report)
    messages = build_messages(report, rows)
    if os.getenv('LINE_DRY_RUN') == '1':
        print('\n\n--- MESSAGE ---\n\n'.join(messages))
    else:
        push_line(messages)
