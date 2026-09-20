import os
import sqlite3
import requests

DB_NAME = "kumamoto_properties.db"

def get_matching_properties():
    """Send the current active inventory that already matches the user's filters."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM properties
        WHERE status='active'
          AND (current_price BETWEEN 1 AND 70000000
               OR (COALESCE(property_type, 'house')='condo_new' AND current_price=0))
          AND property_id LIKE 'suumo_%'
          AND url LIKE '%/nc_%/%'
          AND last_seen_date = date('now')
        ORDER BY
          CASE region
            WHEN '菊陽町' THEN 1
            WHEN '光之森周邊' THEN 2
            WHEN '合志市' THEN 3
            WHEN '熊本市北區' THEN 4
            WHEN '熊本市東區' THEN 5
            ELSE 9
          END,
          CASE WHEN COALESCE(property_type, 'house')!='house'
                    AND building_area>=132.23 THEN 0 ELSE 1 END,
          current_price ASC
    """).fetchall()
    conn.close()
    return rows

def push_line(message):
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.getenv("LINE_USER_ID")
    if not token or not user_id:
        raise RuntimeError("LINE secrets not configured")
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": user_id, "messages": [{"type": "text", "text": part} for part in message]},
        timeout=15,
    )
    if not r.ok:
        print("LINE API error:", r.status_code, r.text)
    r.raise_for_status()
    print("LINE notification sent successfully.")

if __name__ == "__main__":
    rows = get_matching_properties()
    if not rows:
        print("No matching active properties; skip notification.")
    else:
        lines = [f"🏡 熊本符合條件房源｜目前 {len(rows)} 筆｜屋齡15年內"]
        for r in rows:
            price = int((r["current_price"] or 0) / 10000)
            land_ping = round((r["land_area"] or 0) * 0.3025, 1)
            building_ping = round((r["building_area"] or 0) * 0.3025, 1)
            property_type = r["property_type"] if "property_type" in r.keys() else "house"
            type_label = {"house": "一戶建", "condo": "高級大樓", "condo_new": "新築高級大樓"}.get(property_type, "住宅")
            price_text = f"{price:,}萬円" if price else "價格未定（待確認是否≤7,000萬円）"
            area_text = (
                f"土地 {land_ping}坪｜建物 {building_ping}坪"
                if property_type == "house"
                else f"專有面積 {building_ping}坪"
            )
            priority = "⭐ 約40坪大戶型｜" if property_type != "house" and building_ping >= 40 else ""
            lines += [
                "",
                f"【{r['region']}｜{type_label}】{r['title']}",
                f"{priority}{price_text}｜{area_text}",
                f"{r['layout'] or ''} {r['build_year'] or ''}".strip(),
                r["url"],
            ]
        # Safety gate: only same-run, canonical SUUMO detail URLs are allowed.
        bad = [r for r in rows if "/nc_" not in (r["url"] or "")]
        if bad:
            raise RuntimeError("Refusing to send unverified/non-detail property URLs")
        chunks = []
        current = ""
        for line in lines:
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > 4500:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)
        push_line(chunks[:5])
