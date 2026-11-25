import json
import os
import sqlite3
import hmac
import hashlib
import time
import uuid
from urllib.parse import urlparse
import requests
from extra_sources import valid_detail_url
from inventory import load_inventory
from amenities import amenity_text
from mirai_source import pending_blocks
from sekisui_source import builder_blocks

DB_NAME = 'kumamoto_properties.db'


def load_report():
    return load_inventory(DB_NAME)[1]


def get_matching_properties(report):
    rows, current = load_inventory(DB_NAME)
    if current != report:
        raise RuntimeError('Inventory changed during notification; retry with current report')
    for row in rows:
        if not valid_detail_url(row['property_id'], row['url']):
            raise RuntimeError('Unverified detail URL: ' + row['property_id'])
    return sorted(rows, key=lambda r: (
        0 if r['property_type'] not in {'house', 'house_new'} and r['building_area'] >= 132.23 else 1,
        r['region'], r['property_type'], r['current_price']))


def tax_budget(price):
    if not price:
        return '稅金粗估：價格未定，待取得售價與課稅評價額'
    return (f'稅金預留：購入後一次性約{round(price*.005/10000)}–{round(price*.03/10000)}萬円；'
            f'每年約{round(price*.003/10000)}–{round(price*.012/10000)}萬円')


def build_messages(report, rows):
    # A single concise update; missing fields have no labels or placeholders.
    rows = sorted(rows, key=lambda r: (r['region'] != '光之森', r['region'], r['current_price']))
    header = f"🏡 熊本房源｜{report['checked_at']}\n刊登 {len(rows)} 筆（跨站可能重複）；可售狀態以仲介回覆為準。"
    blocks = []
    compact = []
    for r in rows:
        marker = '🔴 新增｜' if r['property_id'] in report.get('new_property_ids', []) else ''
        title = f"{marker}【{r['region']}】{r['title'][:65]}"
        facts = []
        if r.get('current_price'):
            facts.append(f"{r['current_price']/10000:,.0f}萬円")
        if r['property_type'] in {'house', 'house_new'} and r.get('land_area'):
            facts.append(f"土地{r['land_area']:g}㎡")
        if r.get('building_area'):
            facts.append(f"建物{r['building_area']:g}㎡")
        for key in ('layout', 'build_year'):
            if r.get(key) and '待確認' not in str(r[key]):
                facts.append(str(r[key]))
        base = '\n'.join([title, '｜'.join(facts), r['url']])
        nearby = amenity_text(r)
        blocks.append(base + ('\n' + nearby if nearby else ''))
        compact.append(base)
    message = '\n\n'.join([header] + blocks)
    if len(message) > 4500:
        message = '\n\n'.join([header + '\n本則為精簡總覽，周邊資訊請開啟房源連結。'] + compact)
    if len(message) > 4500:
        # Keep every listing's URL and price if full descriptions exceed one message.
        message = header + '\n本則為精簡連結總覽。\n\n' + '\n\n'.join(
            f"【{r['region']}】" + (f"{r['current_price']/10000:,.0f}萬円" if r.get('current_price') else '') + '\n' + r['url']
            for r in rows)
    if len(message) > 4500:
        raise RuntimeError('Inventory exceeds a single LINE message; use a linked report')
    return [message]


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
        payload = {'to': target, 'messages': [{'type': 'text', 'text': part} for part in messages[start:start+5]]}
        retry_key = str(uuid.uuid4())
        for attempt in range(3):
            try:
                r = requests.post('https://api.line.me/v2/bot/message/push',
                    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
                             'X-Line-Retry-Key': retry_key}, json=payload, timeout=20)
                if r.status_code == 409 and r.headers.get('x-line-accepted-request-id'):
                    break
                r.raise_for_status()
                break
            except requests.RequestException as exc:
                code = exc.response.status_code if exc.response is not None else 0
                if attempt == 2 or (code and code < 500):
                    raise
                time.sleep(2 ** attempt)
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



