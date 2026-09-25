"""Private mobile dashboard authenticated by one-use links sent in LINE direct messages."""
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps

from flask import abort, redirect, render_template_string, request, session, url_for
from jinja2 import ChoiceLoader, DictLoader
from werkzeug.middleware.proxy_fix import ProxyFix

import members
from matching import match
from extra_sources import valid_detail_url

PAGE = """<!doctype html><html lang="zh-Hant"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>熊本找房</title>
<style>body{font:16px system-ui;max-width:850px;margin:auto;padding:18px;color:#172b37;background:#f5f8f9}
h1{font-size:25px}nav a{margin-right:16px;color:#086b72}.card{background:white;border:1px solid #dce5e7;
border-radius:12px;padding:16px;margin:13px 0}.muted{color:#54666d;font-size:14px}input,select,button{font-size:16px;padding:9px;margin:5px}
button{background:#086b72;color:white;border:0;border-radius:6px}a{color:#086b72}label{display:block} .note{background:#e7f3f2;padding:12px}</style>
<h1>🏡 我的熊本找房</h1><nav><a href="/homes">我的推薦</a><a href="/homes?view=new">新上架</a>
<a href="/homes?view=price">降價</a><a href="/homes?view=saved">收藏</a><a href="/homes?view=excluded">淘汰原因</a>
<a href="/settings">條件</a><a href="/logout">登出</a></nav><p class="muted">{{ status }}</p>
{% block body %}{% endblock %}</html>"""


def install(app):
    if not os.getenv("FLASK_SECRET_KEY") or not os.getenv("DATABASE_URL"):
        # Explicitly disable the login routes until a stable production secret is set.
        return
    app.secret_key = os.environ["FLASK_SECRET_KEY"]
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=True)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    app.jinja_loader = ChoiceLoader([DictLoader({"base.html": PAGE}), app.jinja_loader])

    def require_user(fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            uid = session.get("uid")
            if not uid or not members.member(uid):
                abort(401, "請私訊 LINE Bot『我的看板』取得一次性登入連結。")
            return fn(uid, *args, **kwargs)
        return inner

    @app.get("/login/<token>")
    def login(token):
        uid = members.consume_link(token)
        if not uid:
            abort(401, "連結已使用或過期，請重新向 Bot 索取。")
        session.clear()
        session["uid"] = uid
        session["csrf"] = secrets.token_urlsafe(24)
        return redirect(url_for("homes"), code=303)

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect("/", code=303)

    def inventory():
        db_path = os.getenv("INVENTORY_DB", "kumamoto_properties.db")
        with sqlite3.connect(db_path) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute("SELECT * FROM properties WHERE status='active'")]
            try:
                raw = db.execute("SELECT payload FROM run_report WHERE id=1").fetchone()
                import json
                report = json.loads(raw[0]) if raw else {}
            except sqlite3.OperationalError:
                report = {}
        ids = set(report.get("property_ids", []))
        return [r for r in rows if r["property_id"] in ids and
                valid_detail_url(r["property_id"], r["url"])], report

    def status_text(report):
        return (f"資料檢查：{report.get('checked_at', '未知')}；僅顯示最近一次爬取有核對的候選。"
                "通勤時間尚未驗證；網站刊登仍須向仲介確認。搜尋池目前受原專案條件限制。")

    @app.get("/homes")
    @require_user
    def homes(uid):
        profile = members.member(uid)
        rows, report = inventory()
        saved = members.favorites(uid)
        view = request.args.get("view", "recommended")
        if view not in ("recommended", "new", "price", "saved", "excluded"):
            abort(400)
        records = []
        for row in rows:
            score, reasons = match(row, profile)
            if view == "recommended" and score == 0:
                continue
            if view == "excluded" and score:
                continue
            if view == "saved" and row["property_id"] not in saved:
                continue
            if view == "new" and row["first_seen_date"] != row["last_seen_date"]:
                continue
            if view == "price":
                with sqlite3.connect(os.getenv("INVENTORY_DB", "kumamoto_properties.db")) as db:
                    history = db.execute("SELECT price FROM price_history WHERE property_id=? ORDER BY id DESC LIMIT 2",
                                         (row["property_id"],)).fetchall()
                if len(history) < 2 or history[0][0] >= history[1][0]:
                    continue
            records.append(dict(row=row, score=score, reasons=reasons,
                                saved=row["property_id"] in saved))
        records.sort(key=lambda x: (-x["score"], x["row"]["current_price"]))
        body = """{% extends 'base.html' %}{% block body %}<p class="note">本版是可解釋條件評分，不含 AI 推估或已驗證通勤時間。</p>
        <h2>{{ title }}（{{ records|length }}）</h2>{% for x in records %}
        <section class="card"><strong>{{ x.row.title }}</strong><p>{{ x.row.region }} · {{ x.row.property_type }} ·
        {{ (x.row.current_price / 10000)|int if x.row.current_price else '價格未定' }} 萬円 · {{ x.row.building_area }}㎡</p>
        <p>分數 {{ x.score }}｜{{ x.reasons|join('、') if x.reasons else '符合目前設定' }}</p>
        <a href="{{ x.row.url }}" rel="noopener noreferrer" target="_blank">原始刊登</a>
        <form method="post" action="/favorite"><input type="hidden" name="csrf" value="{{ csrf }}">
        <input type="hidden" name="property_id" value="{{ x.row.property_id }}">
        <input type="hidden" name="save" value="{{ 0 if x.saved else 1 }}">
        <button>{{ '取消收藏' if x.saved else '收藏' }}</button></form></section>
        {% else %}<p>目前沒有符合此分頁的已核對房源。</p>{% endfor %}{% endblock %}"""
        return render_template_string(body, base=PAGE, records=records, csrf=session["csrf"],
                                      title={"recommended":"我的推薦", "new":"新上架", "price":"降價",
                                             "saved":"收藏", "excluded":"淘汰原因"}[view], status=status_text(report))

    @app.route("/settings", methods=["GET", "POST"])
    @require_user
    def settings(uid):
        if request.method == "POST":
            if not secrets.compare_digest(request.form.get("csrf", ""), session["csrf"]):
                abort(403)
            try:
                price = int(request.form["max_price"])
                area = int(request.form["min_area"])
                age = int(request.form["max_age"])
                if not (500 <= price <= 30000 and 20 <= area <= 500 and 0 <= age <= 100):
                    raise ValueError()
            except (ValueError, KeyError):
                abort(400, "請檢查條件範圍。")
            kind = request.form.get("kind")
            if kind not in ("all", "house", "condo"):
                abort(400)
            regions = request.form.get("regions", "")
            if len(regions) > 200 or any(c in regions for c in "<>\n\r"):
                abort(400)
            members.update(uid, max_price=price, min_area=area, max_age=age, kind=kind,
                           regions=regions.strip(), notifications=int("notifications" in request.form))
            return redirect("/settings", code=303)
        p = members.member(uid)
        body = """{% extends 'base.html' %}{% block body %}<h2>我的條件</h2><p class="note">只有私訊 LINE Bot 的本人可開啟看板。推播預設關閉，可自行開啟。</p>
        <form method="post"><input type="hidden" name="csrf" value="{{ csrf }}">
        <label>預算上限（萬円）<input type="number" name="max_price" min="500" max="30000" value="{{ p.max_price }}" required></label>
        <label>最小建物／專有面積（㎡）<input type="number" name="min_area" min="20" max="500" value="{{ p.min_area }}" required></label>
        <label>最大屋齡（年）<input type="number" name="max_age" min="0" max="100" value="{{ p.max_age }}" required></label>
        <label>區域（完整名稱，逗號分隔；留白為全部）<input name="regions" value="{{ p.regions }}"></label>
        <label>屋型 <select name="kind"><option value="all">全部</option><option value="house" {{ 'selected' if p.kind=='house' }}>一戶建</option>
        <option value="condo" {{ 'selected' if p.kind=='condo' }}>大樓</option></select></label>
        <label><input type="checkbox" name="notifications" {{ 'checked' if p.notifications }}>開啟個人 LINE 新房源／降價通知</label>
        <button>儲存條件</button></form>{% endblock %}"""
        return render_template_string(body, base=PAGE, p=p, csrf=session["csrf"], status="條件儲存在私有資料庫")

    @app.post("/favorite")
    @require_user
    def set_favorite(uid):
        if not secrets.compare_digest(request.form.get("csrf", ""), session["csrf"]):
            abort(403)
        pid = request.form.get("property_id", "")
        rows, _ = inventory()
        if pid not in {r["property_id"] for r in rows}:
            abort(400)
        members.favorite(uid, pid, request.form.get("save") == "1")
        return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url) else "/homes", code=303)
