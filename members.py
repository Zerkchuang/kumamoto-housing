"""Private member state. Set DATABASE_URL to persistent PostgreSQL in production."""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text


def engine():
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("RENDER"):
            raise RuntimeError("DATABASE_URL is required for member accounts on Render")
        url = "sqlite:///members.local.db"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return create_engine(url, pool_pre_ping=True)


def init():
    with engine().begin() as db:
        db.execute(text("""CREATE TABLE IF NOT EXISTS members (
            user_id VARCHAR(100) PRIMARY KEY, max_price INTEGER NOT NULL DEFAULT 7299,
            min_area INTEGER NOT NULL DEFAULT 100, max_age INTEGER NOT NULL DEFAULT 15,
            regions TEXT NOT NULL DEFAULT '', kind VARCHAR(30) NOT NULL DEFAULT 'all',
            notifications INTEGER NOT NULL DEFAULT 0)"""))
        db.execute(text("""CREATE TABLE IF NOT EXISTS saved (
            user_id VARCHAR(100) NOT NULL, property_id VARCHAR(100) NOT NULL,
            PRIMARY KEY(user_id, property_id))"""))
        db.execute(text("""CREATE TABLE IF NOT EXISTS login_links (
            token_hash VARCHAR(64) PRIMARY KEY, user_id VARCHAR(100) NOT NULL,
            expires_at VARCHAR(40) NOT NULL)"""))
        db.execute(text("""CREATE TABLE IF NOT EXISTS notified (
            user_id VARCHAR(100) NOT NULL, property_id VARCHAR(100) NOT NULL,
            price INTEGER NOT NULL, PRIMARY KEY(user_id, property_id))"""))


def register(user_id):
    init()
    with engine().begin() as db:
        db.execute(text("""INSERT INTO members(user_id) VALUES(:uid)
            ON CONFLICT (user_id) DO NOTHING"""), {"uid": user_id})


def member(user_id):
    with engine().connect() as db:
        row = db.execute(text("SELECT * FROM members WHERE user_id=:uid"), {"uid": user_id}).mappings().first()
        return dict(row) if row else None


def update(user_id, **values):
    allowed = {"max_price", "min_area", "max_age", "regions", "kind", "notifications"}
    values = {k: v for k, v in values.items() if k in allowed}
    if not values:
        return
    values["uid"] = user_id
    with engine().begin() as db:
        db.execute(text("UPDATE members SET " + ", ".join(f"{k}=:{k}" for k in values if k != "uid") +
                        " WHERE user_id=:uid"), values)


def new_link(user_id):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    with engine().begin() as db:
        db.execute(text("INSERT INTO login_links VALUES(:hash,:uid,:expires)"),
                   {"hash": hashlib.sha256(token.encode()).hexdigest(), "uid": user_id, "expires": expires})
    return token


def consume_link(token):
    if not token or len(token) > 128:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    with engine().begin() as db:
        row = db.execute(text("SELECT user_id,expires_at FROM login_links WHERE token_hash=:hash"),
                         {"hash": digest}).first()
        db.execute(text("DELETE FROM login_links WHERE token_hash=:hash"), {"hash": digest})
    if row and datetime.fromisoformat(row[1]) > datetime.now(timezone.utc):
        return row[0]
    return None


def favorite(user_id, property_id, save):
    with engine().begin() as db:
        if save:
            db.execute(text("INSERT INTO saved VALUES(:uid,:pid) ON CONFLICT DO NOTHING"),
                       {"uid": user_id, "pid": property_id})
        else:
            db.execute(text("DELETE FROM saved WHERE user_id=:uid AND property_id=:pid"),
                       {"uid": user_id, "pid": property_id})


def favorites(user_id):
    with engine().connect() as db:
        return {r[0] for r in db.execute(text("SELECT property_id FROM saved WHERE user_id=:uid"), {"uid": user_id})}


def subscribed():
    with engine().connect() as db:
        return [dict(r) for r in db.execute(text("SELECT * FROM members WHERE notifications=1")).mappings()]


def last_notified(user_id, property_id):
    with engine().connect() as db:
        return db.execute(text("SELECT price FROM notified WHERE user_id=:uid AND property_id=:pid"),
                          {"uid": user_id, "pid": property_id}).scalar_one_or_none()


def mark_notified(user_id, property_id, price):
    with engine().begin() as db:
        db.execute(text("""INSERT INTO notified VALUES(:uid,:pid,:price)
            ON CONFLICT(user_id,property_id) DO UPDATE SET price=:price"""),
                   {"uid": user_id, "pid": property_id, "price": price})
