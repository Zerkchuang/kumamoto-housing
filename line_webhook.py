import os, json, hmac, hashlib, base64, sqlite3, requests
from flask import Flask, request, abort

app=Flask(__name__)
TOKEN=os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
SECRET=os.environ["LINE_CHANNEL_SECRET"]
OPENAI_KEY=os.environ.get("OPENAI_API_KEY","")
DB=os.environ.get("DB_NAME","kumamoto_properties.db")
MODEL=os.environ.get("OPENAI_MODEL","gpt-5.6-luna")

PROFILE="""你是 Maple 的 LINE 專屬助理。用繁體中文、直接、精簡、有結論。
常用情境：
1. 熊本/JASM購屋：總價7000萬日圓內、土地200㎡以上、建物100㎡以上、屋齡10年內，新屋可；優先菊陽町、光之森、合志市、熊本市東區/北區。不可捏造房源。
2. 半導體/AI：重點為先進封裝、HBM/DRAM/NAND、設備、化材、AI伺服器；區分已知事實與推論。
3. 投資：短線看3個月內籌碼，長線看3個月以上基本面；提醒資料日期與風險，不捏造即時行情。
4. 日本工作/日文：會議、安全、品質、設備與職場日語；日文可附羅馬拼音。
5. 旅遊：偏好有效率、舒適、少繞路的規劃。
若問題需要即時網路、ChatGPT記憶、外部工具或資料庫沒有的資料，要清楚說目前 LINE bot 沒有該即時資料，不可假裝已查到。"""

def valid(body,sig):
    mac=hmac.new(SECRET.encode(),body,hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(),sig or "")

def reply(token,text):
    r=requests.post("https://api.line.me/v2/bot/message/reply",
      headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},
      json={"replyToken":token,"messages":[{"type":"text","text":str(text)[:4900]}]},timeout=20)
    r.raise_for_status()

def homes():
    try:
        con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
        rows=con.execute("""SELECT title,current_price,land_area,building_area,build_year,url
          FROM properties WHERE status='active' AND current_price<=70000000
          AND land_area>=200 AND building_area>=100
          ORDER BY last_seen_date DESC,current_price ASC LIMIT 8""").fetchall(); con.close()
    except Exception:
        return "房源資料庫目前暫時無法讀取。"
    if not rows:return "目前資料庫沒有符合條件的已驗證物件。"
    return "\n\n".join(f"{r['title']}\n{r['current_price']//10000}萬円｜土地{r['land_area']}㎡｜建物{r['building_area']}㎡｜{r['build_year']}\n{r['url']}" for r in rows)

def help_text():
    return """Maple 助理可直接使用：
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
        if q in ("最新房源","房源","找房","物件"): ans=homes()
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
