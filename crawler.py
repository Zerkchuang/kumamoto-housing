import re
import sqlite3
from datetime import datetime
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

HEADERS={"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1","Accept-Language":"ja-JP,ja;q=0.9"}
DB_NAME="kumamoto_properties.db"
MAX_PRICE=70_000_000
TARGET_SOURCES=[
 ("菊陽町","https://suumo.jp/chukoikkodate/kumamoto/sc_kikuchigun/"),
 ("合志市","https://suumo.jp/chukoikkodate/kumamoto/sc_koshi/"),
 ("光之森","https://suumo.jp/b/kodate/kw/%E5%85%89%E3%81%AE%E6%A3%AE%E3%80%80%E4%B8%AD%E5%8F%A4%E7%89%A9%E4%BB%B6/"),
]

def init_db():
 c=sqlite3.connect(DB_NAME); x=c.cursor()
 x.execute("""CREATE TABLE IF NOT EXISTS properties(property_id TEXT PRIMARY KEY,title TEXT,url TEXT,region TEXT,address TEXT,current_price INTEGER,land_area REAL,building_area REAL,layout TEXT,build_year TEXT,first_seen_date TEXT,last_seen_date TEXT,status TEXT DEFAULT 'active')""")
 x.execute("""CREATE TABLE IF NOT EXISTS price_history(id INTEGER PRIMARY KEY AUTOINCREMENT,property_id TEXT,price INTEGER,recorded_at TEXT,FOREIGN KEY(property_id) REFERENCES properties(property_id))""")
 c.commit(); c.close()

def num(pattern,t):
 m=re.search(pattern,t,re.S); return float(m.group(1).replace(",","")) if m else 0

def detail(pid, href, label):
 url=urljoin("https://suumo.jp",href.split("?")[0])
 r=requests.get(url,headers=HEADERS,timeout=25); r.raise_for_status()
 s=BeautifulSoup(r.text,"html.parser"); t=" ".join(s.stripped_strings)
 # Hard identity check: the fetched page must contain the requested nc id.
 if pid not in r.url and pid not in r.text: raise ValueError("detail identity mismatch")
 price=int(num(r"(\d[\d,]*(?:\.\d+)?)\s*万円",t)*10000)
 land=num(r"土地面積[^\d]{0,80}(\d+(?:\.\d+)?)\s*(?:m2|㎡|平米)",t)
 bld=num(r"建物面積[^\d]{0,80}(\d+(?:\.\d+)?)\s*(?:m2|㎡|平米)",t)
 lm=re.search(r"(\d+LDK(?:\+S（納戸）)?|\d+DK)",t)
 ym=re.search(r"((?:19|20)\d{2}年\d{1,2}月)",t)
 am=re.search(r"(熊本県(?:菊池郡菊陽町|合志市|熊本市(?:北区|東区))[^\s]{0,45})",t)
 h=s.find("h1")
 title=" ".join(h.stripped_strings) if h else f"SUUMO {pid}"
 address=am.group(1) if am else label
 region="光之森周邊" if "光の森" in t else ("菊陽町" if "菊陽" in t else ("合志市" if "合志" in t else label))
 if not (0<price<=MAX_PRICE and land>=200 and bld>=100): return None
 return dict(property_id="suumo_"+pid,title=title,url=url,region=region,address=address,current_price=price,land_area=land,building_area=bld,layout=lm.group(1) if lm else "",build_year=ym.group(1) if ym else "")

def scrape(label,url):
 r=requests.get(url,headers=HEADERS,timeout=25); r.raise_for_status()
 # Regex discovery is intentionally independent of SUUMO card DOM/classes.
 pairs=[]; seen=set()
 for m in re.finditer(r'href=["\']([^"\']*/nc_(\d+)/?[^"\']*)["\']',r.text):
  href,pid=m.group(1),m.group(2)
  if pid not in seen: seen.add(pid); pairs.append((pid,href))
 print(f"DISCOVER {label}: {len(pairs)} detail links")
 out=[]
 for pid,href in pairs:
  try:
   p=detail(pid,href,label)
   if p: out.append(p)
  except Exception as e: print(f"WARN detail {pid}: {e}")
 return out

def save(items):
 if not items: raise RuntimeError("Verified inventory is 0; refusing to send stale data")
 c=sqlite3.connect(DB_NAME); x=c.cursor(); today=datetime.now().strftime("%Y-%m-%d")
 ids={i["property_id"] for i in items}; new=changed=0
 for i in items:
  old=x.execute("SELECT current_price FROM properties WHERE property_id=?",(i["property_id"],)).fetchone()
  if old is None:
   new+=1
   x.execute("INSERT INTO properties VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'active')",(i["property_id"],i["title"],i["url"],i["region"],i["address"],i["current_price"],i["land_area"],i["building_area"],i["layout"],i["build_year"],today,today))
   x.execute("INSERT INTO price_history(property_id,price,recorded_at) VALUES(?,?,?)",(i["property_id"],i["current_price"],today))
  else:
   if old[0]!=i["current_price"]:
    changed+=1; x.execute("INSERT INTO price_history(property_id,price,recorded_at) VALUES(?,?,?)",(i["property_id"],i["current_price"],today))
   x.execute("UPDATE properties SET title=?,url=?,region=?,address=?,current_price=?,land_area=?,building_area=?,layout=?,build_year=?,last_seen_date=?,status='active' WHERE property_id=?",(i["title"],i["url"],i["region"],i["address"],i["current_price"],i["land_area"],i["building_area"],i["layout"],i["build_year"],today,i["property_id"]))
 for (pid,) in x.execute("SELECT property_id FROM properties WHERE property_id LIKE 'suumo_%'").fetchall():
  if pid not in ids: x.execute("UPDATE properties SET status='inactive' WHERE property_id=?",(pid,))
 c.commit(); c.close(); print(f"OK verified_inventory={len(items)} new={new} price_changes={changed}")

if __name__=="__main__":
 init_db(); items=[]; seen=set()
 for label,url in TARGET_SOURCES:
  try:
   for p in scrape(label,url):
    if p["property_id"] not in seen: seen.add(p["property_id"]); items.append(p)
  except Exception as e: print(f"WARN source {label}: {e}")
 save(items)
