import os, json, hmac, hashlib, base64, sqlite3, requests
from flask import Flask, request, abort

app=Flask(__name__)
TOKEN=os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
SECRET=os.environ["LINE_CHANNEL_SECRET"]
OPENAI_KEY=os.environ.get("OPENAI_API_KEY","")
DB=os.environ.get("DB_NAME","kumamoto_properties.db")

def valid(body,sig):
    mac=hmac.new(SECRET.encode(),body,hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(),sig or "")

def reply(token,text):
    requests.post("https://api.line.me/v2/bot/message/reply",
      headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"},
      json={"replyToken":token,"messages":[{"type":"text","text":text[:4900]}]},timeout=20).raise_for_status()

def homes():
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    rows=con.execute("""SELECT title,current_price,land_area,building_area,build_year,url
      FROM properties WHERE status='active' AND current_price<=70000000
      AND land_area>=200 AND building_area>=100
      ORDER BY last_seen_date DESC,current_price ASC LIMIT 8""").fetchall(); con.close()
    if not rows:return "目前資料庫沒有符合條件的已驗證物件。"
    return "\n\n".join(f"{r['title']}\n{r['current_price']//10000}萬円｜土地{r['land_area']}㎡｜建物{r['building_area']}㎡｜{r['build_year']}\n{r['url']}" for r in rows)

def ask_gpt(q):
    if not OPENAI_KEY:return "GPT 尚未啟用；需要先設定 OPENAI_API_KEY。"
    context=homes()
    prompt=f"""你是熊本/JASM購屋助理。用繁體中文簡潔回答。使用者主要條件：總價7000萬日圓內、土地200㎡以上、建物100㎡以上、屋齡10年內，新屋可。只能把資料庫內容當成目前已驗證物件，不可捏造房源。\n目前房源：\n{context}\n\n使用者：{q}"""
    r=requests.post("https://api.openai.com/v1/responses",headers={"Authorization":f"Bearer {OPENAI_KEY}","Content-Type":"application/json"},json={"model":"gpt-5.6","input":prompt},timeout=45)
    r.raise_for_status(); d=r.json()
    if d.get("output_text"):return d["output_text"]
    out=[]
    for x in d.get("output",[]):
      for y in x.get("content",[]):
        if y.get("type")=="output_text":out.append(y.get("text",""))
    return "\n".join(out) or "GPT 暫時沒有回覆。"

@app.get("/")
def health(): return {"ok":True}

@app.post("/webhook")
def webhook():
    body=request.get_data()
    if not valid(body,request.headers.get("x-line-signature")):abort(400)
    data=json.loads(body)
    for e in data.get("events",[]):
      typ=e.get("type"); src=e.get("source",{})
      if typ in ("follow","join"):
        reply(e["replyToken"],"熊本找房助理已啟用。可以直接問我：『最新房源』『光之森有什麼？』『幫我比較這幾間』。")
      elif typ=="message" and e.get("message",{}).get("type")=="text":
        q=e["message"]["text"].strip()
        if q in ("最新房源","房源","找房","物件"): ans=homes()
        else: ans=ask_gpt(q)
        reply(e["replyToken"],ans)
    return "OK",200

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","8080")))
