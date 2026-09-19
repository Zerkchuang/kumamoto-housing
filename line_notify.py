import os
import sqlite3
import requests

DB_NAME = "kumamoto_properties.db"

def get_new_properties():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM properties
        WHERE status='active'
        ORDER BY first_seen_date DESC
        LIMIT 10
    """).fetchall()
    conn.close()
    return rows

def push_line(message):
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.getenv("LINE_USER_ID")
    if not token or not user_id:
        print("LINE secrets not configured; skip notification.")
        return False
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": user_id, "messages": [{"type": "text", "text": message[:5000]}]},
        timeout=15,
    )
    r.raise_for_status()
    return True

if __name__ == "__main__":
    rows = get_new_properties()
    if not rows:
        print("No properties to notify.")
    else:
        lines = ["🏡 熊本 JASM 房源更新"]
        for r in rows[:5]:
            price = int((r["current_price"] or 0) / 10000)
            land_ping = round((r["land_area"] or 0) * 0.3025, 1)
            lines += ["", f"【{r['region']}】{r['title']}", f"{price:,}萬円｜土地 {land_ping}坪", r["url"]]
        push_line("\n".join(lines))
