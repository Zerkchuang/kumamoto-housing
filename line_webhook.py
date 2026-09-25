import os, json, hmac, hashlib, base64, sqlite3, requests, time
from flask import Flask, request, abort
import members
from matching import match
from extra_sources import valid_detail_url
from web import install

app=Flask(__name__)
TOKEN=os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
SECRET=os.environ["LINE_CHANNEL_SECRET"]
OPENAI_KEY=os.environ.get("OPENAI_API_KEY","")
DB=os.environ.get("DB_NAME","kumamoto_properties.db")
MODEL=os.environ.get("OPENAI_MODEL","gpt-5.6-luna")
install(app)

PROFILE="""你是熊本找房 LINE 助理。用繁體中文、直接、精簡、有結論。
常用情境：
1. 熊本購屋：只引用已驗證房源，不可捏造；預算及區域由使用者各自設定。
2. 其他問題：如沒有即時資料，明確說明限制。
若問題需要即時網路、ChatGPT記憶、外部工具或資料庫沒有的資料，要清楚說目前 LINE bot 沒有該即時資料，不可假裝已查到。"""

def valid(body,sig):
    mac=hmac.new(SECRET.encode(),body,hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(),sig or "")

def reply(token,text):
    r=requests.post("https://api.line.me/v2/bot/message/reply",
      headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},
      json={"replyToken":token,"messages":[{"type":"text","text":str(text)[:4900]}]},timeout=20)
    r.raise_for_status()

def homes(user_id=None):
    try:
        con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
        report=json.loads(con.execute("SELECT payload FROM run_report WHERE id=1").fetchone()[0])
        ids=set(report["property_ids"])
        rows=[dict(r) for r in con.execute("SELECT * FROM properties WHERE status='active'")
              if r["property_id"] in ids and valid_detail_url(r["property_id"], r["url"])]
        con.close()
        if user_id:
            p=members.member(user_id)
            if p:
                rows=[r for r in rows if match(r,p)[0]]
        rows=sorted(rows,key=lambda r:r["current_price"])[:8]
    except Exception:
        return "房源資料庫目前暫時無法讀取。"
    if not rows:return "目前資料庫沒有符合條件的已驗證物件。"
    return "\n\n".join(f"{r['title']}\n{str(r['current_price']//10000)+'萬円' if r['current_price'] else '價格未定'}｜{r['building_area']}㎡｜{r['build_year']}\n{r['url']}" for r in rows)

def help_text():
    return """Maple 助理可直接使用：
• 我的看板：私訊取得個人找房設定與收藏的登入連結（請勿在群組索取）
• 最新房源／房源：列出符合條件的已驗證熊本物件
• 購屋比較：直接問「幫我比較目前房源」
• 半導體：問 HBM、DRAM、NAND、設備、材料、AI 供應鏈
• 投資：貼股票名稱或資料，我會依短線籌碼／長線基本面整理
• 日文：輸入「日文練習」或直接貼日文讓我修正
• 旅遊：直接說目的地、日期、人數
• 一般問題：直接像 ChatGPT 一樣問我

註：LINE 版目前沒有 ChatGPT 主程式的完整記憶與所有即時工具；需要即時資料時我會明確標示。"""

def ask_gpt(q):
    if not OPENAI_KEY:return "GPT API 尚未啟用；房源指令仍可使用。"
    prompt=f"{PROFILE}\n\n目前已驗證房源：\n{homes()}\n\n使用者：{q}"
    try:
        r=requests.post("https://api.openai.com/v1/responses",
          headers={"Authorization":f"Bearer {OPENAI_KEY}","Content-Type":"application/json"},
          json={"model":MODEL,"input":prompt,"max_output_tokens":900},timeout=45)
        if r.status_code==429:
            return "GPT API 目前額度或速率受限（429）。LINE 與房源功能正常；請稍後再試。若持續發生，需要在 OpenAI API 帳戶啟用/補充 API 計費額度。"
        if r.status_code in (401,403):
            return "GPT API 金鑰目前無法授權；LINE 與房源功能正常。"
        r.raise_for_status(); d=r.json()
        if d.get("output_text"):return d["output_text"]
        out=[]
        for x in d.get("output",[]):
          for y in x.get("content",[]):
            if y.get("type")=="output_text":out.append(y.get("text",""))
        return "\n".join(out) or "GPT 暫時沒有文字回覆。"
    except requests.RequestException:
        return "GPT 服務暫時連線失敗；LINE 與房源功能仍正常，稍後再問即可。"

@app.get("/")
def health(): return {"ok":True,"model":MODEL}

@app.get("/push-target")
def push_target():
    signature=request.headers.get("x-push-signature","")
    expected=hmac.new(TOKEN.encode(),b"get-push-target",hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature,expected):
        abort(403)
    target=os.environ.get("LINE_PUSH_TARGET_ID","")
    if not target:
        abort(404)
    return {"target":target}

@app.post("/members/notify")
def notify_members():
    if not os.getenv("DATABASE_URL"):
        abort(503)
    body = request.get_data()
    stamp = request.headers.get("x-notify-timestamp", "")
    try:
        if abs(time.time() - int(stamp)) > 300:
            abort(403)
    except ValueError:
        abort(403)
    digest = hmac.new(TOKEN.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, request.headers.get("x-notify-signature", "")):
        abort(403)
    data = request.get_json()
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list) or len(data["rows"]) > 200:
        abort(400)
    members.init()
    sent, failed = 0, 0
    for profile in members.subscribed():
        candidates = []
        for row in data["rows"]:
            if not valid_detail_url(row.get("property_id", ""), row.get("url", "")):
                continue
            score, _ = match(row, profile)
            if not score:
                continue
            old = members.last_notified(profile["user_id"], row["property_id"])
            if old is None or (row.get("current_price") and row["current_price"] < old):
                candidates.append((row, score, old))
        candidates.sort(key=lambda x: -x[1])
        if not candidates:
            continue
        # Keep LINE messages concise. Entries outside the first five remain unmarked for next run.
        texts = []
        for row, score, old in candidates[:5]:
            price = f"{row['current_price']//10000}萬円" if row.get("current_price") else "價格未定"
            label = "降價" if old is not None else "新候選"
            texts.append(f"🏡 {label}｜{score}分｜{row['region']}\n{row['title'][:70]}\n{price}｜{row['building_area']}㎡\n{row['url']}")
        try:
            res = requests.post("https://api.line.me/v2/bot/message/push",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={"to": profile["user_id"], "messages": [{"type":"text","text":s} for s in texts]}, timeout=20)
            res.raise_for_status()
            for row, _, _ in candidates[:5]:
                members.mark_notified(profile["user_id"], row["property_id"], row["current_price"])
            sent += 1
        except requests.RequestException:
            failed += 1
    return {"sent_users": sent, "failed_users": failed}, (502 if failed else 200)

@app.post("/webhook")
def webhook():
    body=request.get_data()
    if not valid(body,request.headers.get("x-line-signature")):abort(400)
    data=json.loads(body)
    for e in data.get("events",[]):
      typ=e.get("type")
      if typ in ("follow","join"):
        reply(e["replyToken"],"Maple LINE 助理已啟用。輸入「功能」查看常用功能，或直接跟我說話。")
      elif typ=="message" and e.get("message",{}).get("type")=="text":
        q=e["message"]["text"].strip()
        ql=q.lower()
        source=e.get("source",{})
        if q in ("我的看板", "找房設定"):
          if source.get("type") != "user" or not source.get("userId"):
            ans = "請私訊 Bot『我的看板』；個人登入連結不會發到群組。"
          elif not os.getenv("FLASK_SECRET_KEY") or not os.getenv("DATABASE_URL"):
            ans = "個人看板尚未啟用，請稍後再試。"
          else:
            try:
              members.register(source["userId"])
              token = members.new_link(source["userId"])
              ans = f"你的熊本找房看板（10分鐘內單次使用）：\n{request.host_url.rstrip('/')}/login/{token}\n請勿轉傳此連結。"
            except Exception:
              ans = "個人看板資料庫尚未備妥，稍後再試。"
        elif q in ("推播目標","群組ID","群組id"):
          target=source.get("groupId") or source.get("roomId") or source.get("userId")
          kind={"group":"群組","room":"多人聊天室","user":"個人聊天室"}.get(source.get("type"),source.get("type","未知"))
          ans=(f"目前是{kind}。\n推播目標 ID：\n{target}\n\n請把此 ID 設為 GitHub Actions Secret：LINE_USER_ID"
               if target else "目前無法取得這個聊天室的推播目標 ID。")
        elif q in ("最新房源","房源","找房","物件"):
          ans=homes(source.get("userId") if source.get("type")=="user" else None)
        elif q in ("功能","選單","help","幫助","使用說明"): ans=help_text()
        elif q in ("日文練習","練日文"): ans=ask_gpt("請給我一個適合日本晶圓廠管理工作的短篇日文練習，包含日文、羅馬拼音、中文意思與一題讓我回答。")
        elif q in ("半導體","半導體戰報"): ans=ask_gpt("請依我的半導體關注方向整理一份精簡觀察框架；若沒有即時資料要明確說明。")
        elif q in ("投資","投資戰報"): ans=ask_gpt("請依我的投資規則告訴我今天可以怎麼提供資料給你分析，不要捏造即時行情。")
        else: ans=ask_gpt(q)
        try: reply(e["replyToken"],ans)
        except requests.RequestException: pass
    return "OK",200

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","8080")))
