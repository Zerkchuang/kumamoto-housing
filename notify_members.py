"""Deliver the current crawl's verified candidate rows to the private member service."""
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time

import requests

from extra_sources import valid_detail_url


def payload(db_path="kumamoto_properties.db"):
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT payload FROM run_report WHERE id=1").fetchone()
        if not row:
            raise RuntimeError("No crawl report")
        report = json.loads(row[0])
        ids = set(report["property_ids"])
        rows = [dict(r) for r in db.execute("SELECT * FROM properties WHERE status='active'")
                if r["property_id"] in ids and valid_detail_url(r["property_id"], r["url"])]
    return json.dumps({"checked_at": report["checked_at"], "rows": rows}, ensure_ascii=False).encode()


def run():
    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    endpoint = os.getenv("MEMBER_NOTIFY_URL")
    if not endpoint:
        print("Member notifications disabled until persistent storage is configured")
        return
    body = payload()
    for attempt in range(3):
        stamp = str(int(time.time()))
        signature = hmac.new(token.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        try:
            response = requests.post(endpoint, data=body, headers={"Content-Type": "application/json",
                "X-Notify-Timestamp": stamp, "X-Notify-Signature": signature}, timeout=60)
            response.raise_for_status()
            print("Member notification result:", response.json())
            return
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))


if __name__ == "__main__":
    run()
