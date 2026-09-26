"""One verified inventory policy for the dashboard, chatbot and scheduled push."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from crawler import PRICE_BASIS, is_hikari, within_age_limit
from extra_sources import valid_detail_url


def load_inventory(path='kumamoto_properties.db', now=None):
    now = now or datetime.now(timezone.utc)
    with sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        row = db.execute('SELECT payload FROM run_report WHERE id=1').fetchone()
        report = json.loads(row[0]) if row else {}
        if report.get('price_parser_version') != PRICE_BASIS:
            raise ValueError('價格資料需重新驗證，請等待下一次更新。')
        checked = datetime.fromisoformat(report['checked_at'])
        if checked.tzinfo is None or not -300 <= (now - checked).total_seconds() <= 48 * 3600:
            raise ValueError('房源資料已超過48小時未更新，暫不列為最新房源。')
        ids = set(report.get('property_ids', []))
        rows = [dict(r) for r in db.execute("SELECT * FROM properties WHERE status='active'")]
    return [r for r in rows if r['property_id'] in ids and r.get('price_basis') == PRICE_BASIS
            and valid_detail_url(r['property_id'], r['url'])
            and within_age_limit(r['build_year'], strict=is_hikari(r['address']))], report


def verified_history(path='kumamoto_properties.db'):
    with sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute('''SELECT * FROM price_history
            WHERE evidence_version=? ORDER BY id DESC''', (PRICE_BASIS,))]
