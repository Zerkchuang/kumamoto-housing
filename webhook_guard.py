"""Bound replay processing and GPT spending on the single-instance LINE webhook."""
import hashlib
import os
import sqlite3
import time


def connection():
    db = sqlite3.connect(os.getenv('WEBHOOK_STATE_DB', '/tmp/kumamoto-webhook-state.sqlite'), timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, created REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS usage(id TEXT, bucket INTEGER, count INTEGER, PRIMARY KEY(id,bucket))')
    return db


def claim_event(event_id):
    with connection() as db:
        db.execute('DELETE FROM events WHERE created<?', (time.time() - 86400,))
        inserted = db.execute('INSERT OR IGNORE INTO events VALUES(?,?)',
                              (hashlib.sha256(event_id.encode()).hexdigest(), time.time()))
        return inserted.rowcount == 1


def allow_gpt(source):
    bucket = int(time.time()) // 60
    key = hashlib.sha256(source.encode()).hexdigest()
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM usage WHERE bucket<?', (bucket - 1,))
        for identity, limit in ((key, 6), ('global', 60)):
            row = db.execute('SELECT count FROM usage WHERE id=? AND bucket=?', (identity, bucket)).fetchone()
            if row and row[0] >= limit:
                return False
        for identity in (key, 'global'):
            db.execute('INSERT INTO usage VALUES(?,?,1) ON CONFLICT(id,bucket) DO UPDATE SET count=count+1', (identity, bucket))
    return True
