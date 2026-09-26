import os, json, hmac, hashlib, base64, sqlite3, requests
from flask import Flask, request, abort
from inventory import load_inventory
from webhook_guard import claim_event, allow_gpt

app=Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
TOKEN=os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
SECRET=os.environ["LINE_CHANNEL_SECRET"]
OPENAI_KEY=os.environ.get("OPENAI_API_KEY","")
DB=os.environ.get("DB_NAME","kumamoto_properties.db")
MODEL=os.environ.get("OPENAI_MODEL","gpt-5.6-luna")

PROFILE="""你是 Maple 的 LINE 專屬助理。用繁體中文、直接、精簡、有結論。
常用情境：
1. 熊本/JASM購屋：光之森本區屋齡未滿15年，不限價格及面積；其他區域沿用資料庫已設定條件。不可捏造房源或使用其他人的私人設定。
2. 半導體/AI：重點為先進封裝、HBM/DRAM/NAND、設備、化材、AI伺服器；區分已知事實與推論。
3. 投資：短線看3個月內籌碼，長線看3個月以上基本面；提醒資料日期與風險，不捏造即時行情。
4. 日本工作/日文：會議、安全、品質、設備與職場日語；日文可附羅馬拼音。
5. 旅遊：偏好有效率、舒適、少繞路的規劃。
若問題需要即時網路、ChatGPT記憶、外部工具或資料庫沒有的資料，要清楚說目前 LINE bot 沒有該即時資料，不可假裝已查到。"""

def valid(body,sig):
    mac=hmac.new(SECRET.encode(),body,hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac),(sig or '').encode('utf-8'))

def reply(token,text):
    text=str(text)
    chunks=[text[i:i+4500] for i in range(0,len(text),4500)] or ['目前沒有可顯示的內容。']
    if len(chunks)>5:
        chunks=chunks[:5]
        chunks[-1]=chunks[-1][:4400]+'\n（內容較長，剩餘部分請縮小條件後查詢。）'
    r=requests.post("https://api.line.me/v2/bot/message/reply",
      headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},
      json={"replyToken":token,"messages":[{"type":"text","text":part} for part in chunks]},timeout=20)
    r.raise_for_status()

def homes(region=None, limit=8):
    try:
        rows,report=load_inventory(DB)
        if region:
            rows=[r for r in rows if r['region']==region]
        rows.sort(key=lambda r:(r['current_price']==0,r['current_price']))
    except ValueError as exc:
        return str(exc)
    except (sqlite3.Error,KeyError):
        return "房源資料庫目前暫時無法讀取。"
    if not rows:return "目前資料庫沒有符合條件的已驗證物件。"
    header=f"核對時間：{report['checked_at']}\n{region or '全部區域'}共{len(rows)}筆，本次列出前{min(limit,len(rows))}筆；刊登仍須向仲介確認。"
    blocks=[header]
    for r in rows[:limit]:
        price=f"{r['current_price']/10000:,.0f}萬円" if r['current_price'] else '價格未定'
        blocks.append(f"{r['title'][:100]}\n{price}｜建物／專有面積{r['building_area']}㎡｜{r['build_year']}\n{r['url']}")
    return '\n\n'.join(blocks)

def help_text():
    return """Maple 助理可直接使用：
• 最新房源／房源：列出符合條件的已驗證熊本物件
• 光之森：列出光之森本區、屋齡未滿15年的物件
• 購屋比較：直接問「幫我比較目前房源」
• 半導體：問 HBM、DRAM、NAND、設備、材料、AI 供應鏈
• 投資：貼股票名稱或資料，我會依短線籌碼／長線基本面整理
• 日文：輸入「日文練習」或直接貼日文讓我修正
• 旅遊：直接說目的地、日期、人數
• 一般問題：直接像 ChatGPT 一樣問我

註：LINE 版目前沒有 ChatGPT 主程式的完整記憶與所有即時工具；需要即時資料時我會明確標示。"""

def ask_gpt(q, source_key='anonymous'):
    if not OPENAI_KEY:return "GPT API 尚未啟用；房源指令仍可使用。"
    if not allow_gpt(source_key):return "提問較頻繁，請一分鐘後再試；房源指令仍可使用。"
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
    if not hmac.compare_digest(signature.encode('utf-8'),expected.encode('ascii')):
        abort(403)
    target=os.environ.get("LINE_PUSH_TARGET_ID","")
    if not target:
        abort(404)
    return {"target":target}

@app.post("/webhook")
def webhook():
    body=request.get_data()
    if not valid(body,request.headers.get("x-line-signature")):abort(400)
    data=request.get_json(silent=True)
    if not isinstance(data,dict) or not isinstance(data.get('events'),list):abort(400)
    for e in data.get("events",[]):
      if not isinstance(e,dict):continue
      if not isinstance(e.get('replyToken'),str) or not e['replyToken']:continue
      event_id=e.get('webhookEventId') or hashlib.sha256(json.dumps(e,sort_keys=True).encode()).hexdigest()
      if not isinstance(event_id,str) or not claim_event(event_id):continue
      typ=e.get("type")
      if typ in ("follow","join"):
        try: reply(e["replyToken"],"Maple LINE 助理已啟用。輸入「功能」查看常用功能，或直接跟我說話。")
        except requests.RequestException: app.logger.warning('LINE welcome reply failed')
      elif typ=="message" and isinstance(e.get('message'),dict) and e['message'].get("type")=="text":
        if not isinstance(e['message'].get('text'),str):continue
        q=e["message"]["text"].strip()
        if len(q)>2000:
          try: reply(e['replyToken'],'請將問題縮短至2000字以內。')
          except requests.RequestException: pass
          continue
        ql=q.lower()
        source=e.get("source",{})
        if not isinstance(source,dict):continue
        source_key=str(source.get('userId') or source.get('groupId') or source.get('roomId') or 'anonymous')
        if q in ("推播目標","群組ID","群組id"):
          target=source.get("groupId") or source.get("roomId") or source.get("userId")
          kind={"group":"群組","room":"多人聊天室","user":"個人聊天室"}.get(source.get("type"),source.get("type","未知"))
          ans=(f"目前是{kind}。\n推播目標 ID：\n{target}\n\n請把此 ID 設為 GitHub Actions Secret：LINE_USER_ID"
               if target else "目前無法取得這個聊天室的推播目標 ID。")
        elif q in ("最新房源","房源","找房","物件"): ans=homes()
        elif q in ("光之森","光の森","光之森房源"): ans=homes(region='光之森',limit=20)
        elif q in ("功能","選單","help","幫助","使用說明"): ans=help_text()
        elif q in ("日文練習","練日文"): ans=ask_gpt("請給我一個適合日本晶圓廠管理工作的短篇日文練習，包含日文、羅馬拼音、中文意思與一題讓我回答。",source_key)
        elif q in ("半導體","半導體戰報"): ans=ask_gpt("請整理一份半導體觀察框架；若沒有即時資料要明確說明。",source_key)
        elif q in ("投資","投資戰報"): ans=ask_gpt("請告訴我可以提供哪些資料進行投資分析，不要捏造即時行情。",source_key)
        else: ans=ask_gpt(q,source_key)
        try: reply(e["replyToken"],ans)
        except requests.RequestException: pass
    return "OK",200

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","8080")))
