import json
import os
import sqlite3
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
        0 if r['property_type'] != 'house' and r['building_area'] >= 132.23 else 1,
        r['region'], r['property_type'], r['current_price']))


def build_messages(report, rows):
    matched = sum(r['current_price'] > 0 and r['property_type'] != 'condo_new' for r in rows)
    blocks = [f"🏡 熊本購屋搜尋狀態\n{report['checked_at']}\n本次候選刊登 {len(rows)} 筆（跨站可能重複）\n單戶已知價格 {matched} 筆；其餘為新築建案線索，戶型與售價需配對確認。\n預算台幣1,500萬（換算上限72,992,700円）｜屋齡15年內\n大樓專有面積約40坪優先；不限制土地"]
    for s in report['sources']:
        blocks.append(f"【{s['source']}】{s['status']}\n範圍：{s['scope']}\n詳細頁 {s['discovered']}／候選 {s['matched']}／讀取錯誤 {s['errors']}／無法解析或非公開 {s.get('unreadable', 0)}")
    blocks.append('以下是本次讀取的候選物件；網站刊登不等於仲介已確認仍可售。大樓為面積候選，管理品質與實際室內淨面積待確認。')
    for r in rows:
        kind = r['property_type']
        label = {'house': '一戶建', 'condo': '大樓候選', 'condo_new': '新築建案線索'}.get(kind, kind)
        source = {'suumo': 'SUUMO', 'tatara': 'たたら', 'smtrc': '三井住友トラスト'}.get(r['property_id'].split('_')[0], '')
        price = f"{r['current_price']/10000:,.0f}萬円" if r['current_price'] else '價格未定；預算未確認'
        area = (f"土地 {r['land_area']:.2f}㎡｜建物 {r['building_area']:.2f}㎡" if kind == 'house'
                else f"專有面積 {r['building_area']:.2f}㎡（{r['building_area']*0.3025:.1f}坪）")
        if kind == 'condo_new':
            area += '；建案最大面積與起價不一定屬同一戶'
        star = '⭐ 約40坪優先 ' if kind != 'house' and r['building_area'] >= 132.23 else ''
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


def push_line(messages):
    token, target = os.getenv('LINE_CHANNEL_ACCESS_TOKEN'), os.getenv('LINE_USER_ID')
    if not token or not target:
        raise RuntimeError('LINE secrets not configured')
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
