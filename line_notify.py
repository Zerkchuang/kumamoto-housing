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
          AND current_price > 0
          AND current_price <= 50000000
        ORDER BY
          CASE region
            WHEN '菊陽町' THEN 1
            WHEN '光之森周邊' THEN 2
            WHEN '合志市' THEN 3
            WHEN '熊本市北區' THEN 4
            WHEN '熊本市東區' THEN 5
            ELSE 9
          END,
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
        json={"to": user_id, "messages": [{"type": "text", "text": message[:5000]}]},
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
        lines = [f"🏡 熊本 JASM 符合條件房源｜目前 {len(rows)} 筆"]
        for r in rows:
            price = int((r["current_price"] or 0) / 10000)
            land_ping = round((r["land_area"] or 0) * 0.3025, 1)
            building_ping = round((r["building_area"] or 0) * 0.3025, 1)
            lines += [
                "",
                f"【{r['region']}】{r['title']}",
                f"{price:,}萬円｜土地 {land_ping}坪｜建物 {building_ping}坪",
                f"{r['layout'] or ''} {r['build_year'] or ''}".strip(),
                r["url"],
            ]
        push_line("\n".join(lines))
