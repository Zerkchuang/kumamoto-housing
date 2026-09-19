import os
import sqlite3
import requests
from datetime import datetime

DB_NAME = "kumamoto_properties.db"

def get_new_properties():
    """Return only properties first seen today, avoiding repeated daily pushes."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT * FROM properties
        WHERE status='active' AND first_seen_date = ?
        ORDER BY first_seen_date DESC
        LIMIT 10
    """, (today,)).fetchall()
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
    print("LINE notification sent successfully.")
    return True

if __name__ == "__main__":
    rows = get_new_properties()
    if not rows:
        print("No newly discovered properties today; skip notification.")
    else:
        lines = ["🏡 熊本 JASM 新房源"]
        for r in rows[:5]:
            price = int((r["current_price"] or 0) / 10000)
            land_ping = round((r["land_area"] or 0) * 0.3025, 1)
            lines += ["", f"【{r['region']}】{r['title']}", f"{price:,}萬円｜土地 {land_ping}坪", r["url"]]
        push_line("\n".join(lines))
