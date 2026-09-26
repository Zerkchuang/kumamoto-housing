import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from datetime import timedelta

from flask import Flask

import members
from matching import match
from web import install


class MembersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = "sqlite:///" + self.tmp.name + "/members.db"
        os.environ["INVENTORY_DB"] = self.tmp.name + "/inventory.db"
        os.environ["FLASK_SECRET_KEY"] = "local-test-secret-only"
        with sqlite3.connect(os.environ["INVENTORY_DB"]) as db:
            db.execute("CREATE TABLE properties(property_id TEXT, title TEXT, url TEXT, region TEXT, "
                       "current_price INTEGER, building_area REAL, property_type TEXT, build_year TEXT, "
                       "first_seen_date TEXT, last_seen_date TEXT, status TEXT)")
            db.execute("CREATE TABLE price_history(id INTEGER PRIMARY KEY, property_id TEXT, price INTEGER)")
            db.execute("CREATE TABLE run_report(id INTEGER PRIMARY KEY, payload TEXT)")
            db.execute("INSERT INTO properties VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                "suumo_123", "測試住宅", "https://suumo.jp/ikkodate/kumamoto/sc_koshi/nc_123/",
                "合志市", 40000000, 120, "house_new", "2026年9月", "2026-09-25", "2026-09-25", "active"))
            db.execute("INSERT INTO run_report VALUES(1,?)", (json.dumps({"property_ids": ["suumo_123"],
                "checked_at": "2026-09-25"}),))
        members.register("user-a")
        members.register("user-b")
        app = Flask(__name__)
        install(app)
        self.app = app
        self.client = app.test_client()

    def tearDown(self):
        for key in ("DATABASE_URL", "INVENTORY_DB", "FLASK_SECRET_KEY"):
            os.environ.pop(key, None)
        members.cached_engine.cache_clear()
        self.tmp.cleanup()

    def test_one_time_login_and_member_isolation(self):
        token = members.new_link("user-a")
        self.assertEqual(self.client.get("/login/" + token).status_code, 303)
        self.assertEqual(self.client.get("/login/" + token).status_code, 401)
        with self.client.session_transaction() as sess:
            csrf = sess["csrf"]
        self.assertEqual(self.client.post("/favorite", data={"csrf": csrf, "property_id": "suumo_123",
            "save": "1"}).status_code, 303)
        self.assertEqual(members.favorites("user-a"), {"suumo_123"})
        self.assertEqual(members.favorites("user-b"), set())
        self.assertEqual(self.client.post("/favorite", data={"csrf": "invalid", "property_id": "suumo_123",
            "save": "0"}).status_code, 403)
        page = self.client.get("/homes")
        self.assertEqual(page.status_code, 200)
        self.assertIn("測試住宅".encode(), page.data)

    def test_concurrent_login_link_is_consumed_once(self):
        token = members.new_link('user-a')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(members.consume_link, [token]*4))
        self.assertEqual(results.count('user-a'),1)
        self.assertEqual(results.count(None),3)

    def test_postgres_uses_installed_psycopg_driver_and_reuses_pool(self):
        for prefix in ('postgres://','postgresql://'):
            with patch.dict(os.environ, {'DATABASE_URL':prefix+'user:password@localhost/test'}):
                result=members.engine()
                self.assertEqual(result.dialect.driver,'psycopg')
                self.assertIs(result,members.engine())

    def test_private_pages_no_cache_and_expired_session_rejected(self):
        token=members.new_link('user-a')
        response=self.client.get('/login/'+token)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual(response.headers['Referrer-Policy'],'no-referrer')
        self.assertEqual(self.client.get('/homes').status_code,200)
        self.app.config['PERMANENT_SESSION_LIFETIME']=timedelta(seconds=-1)
        self.assertEqual(self.client.get('/homes').status_code,401)

    def test_price_limit_excludes_and_reason(self):
        profile = members.member("user-b")
        members.update("user-b", max_price=3000)
        profile = members.member("user-b")
        score, reasons = match(dict(current_price=40000000, region="合志市",
            property_type="house_new", building_area=120, build_year="2026年9月",
            last_seen_date="2026-09-25"), profile)
        self.assertEqual(score, 0)
        self.assertIn("超出預算", reasons)


if __name__ == "__main__":
    unittest.main()
