import re
import sqlite3
from datetime import datetime
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

HEADERS={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36","Accept-Language":"ja,en-US;q=0.9,en;q=0.8"}
DB_NAME="kumamoto_properties.db"
TARGET_SOURCES=[
 ("SUUMO-菊陽町","https://suumo.jp/chukoikkodate/kumamoto/sc_kikuchigun/"),
 ("SUUMO-合志市","https://suumo.jp/chukoikkodate/kumamoto/sc_koshi/"),
 ("SUUMO-熊本北區","https://suumo.jp/chukoikkodate/kumamoto/sc_43105/"),
 ("SUUMO-熊本東區","https://suumo.jp/chukoikkodate/kumamoto/sc_43102/"),
 ("SUUMO-光之森","https://suumo.jp/b/kodate/kw/%E5%85%89%E3%81%AE%E6%A3%AE%E3%80%80%E4%B8%AD%E5%8F%A4%E7%89%A9%E4%BB%B6/"),
]

def init_db():
 c=sqlite3.connect(DB_NAME); x=c.cursor()
 x.execute("""CREATE TABLE IF NOT EXISTS properties(property_id TEXT PRIMARY KEY,title TEXT,url TEXT,region TEXT,address TEXT,current_price INTEGER,land_area REAL,building_area REAL,layout TEXT,build_year TEXT,first_seen_date TEXT,last_seen_date TEXT,status TEXT DEFAULT 'active')""")
 x.execute("""CREATE TABLE IF NOT EXISTS price_history(id INTEGER PRIMARY KEY AUTOINCREMENT,property_id TEXT,price INTEGER,recorded_at TEXT,FOREIGN KEY(property_id) REFERENCES properties(property_id))""")
 c.commit(); c.close()

def parse_price(t):
 t=(t or "").replace(",","")
 m=re.search(r"(\d+(?:\.\d+)?)\s*万円",t)
 return int(float(m.group(1))*10000) if m else 0

def parse_area(label,t):
 m=re.search(label+r"[^\d]{0,30}(\d+(?:\.\d+)?)\s*(?:平米|㎡|m2|m²)",t)
 return float(m.group(1)) if m else 0.0

def region_for(t,label):
 if "光の森" in t:return "光之森周邊"
 if "菊陽" in t or "菊池郡" in t:return "菊陽町"
 if "合志" in t:return "合志市"
 if "北区" in t:return "熊本市北區"
 if "東区" in t:return "熊本市東區"
 return label.replace("SUUMO-","")

def scrape(label,url):
 out=[]
 try:
  r=requests.get(url,headers=HEADERS,timeout=20); r.raise_for_status()
  s=BeautifulSoup(r.text,"html.parser")
  # SUUMO search pages expose listing links containing nc_<id>.
  seen=set()
  for a in s.select("a[href]"):
   href=a.get("href","")
   m=re.search(r"/nc_(\d+)/",href)
   if not m or m.group(1) in seen: continue
   seen.add(m.group(1)); pid="suumo_"+m.group(1)
   card=a.find_parent(["li","div","section","article"]) or a
   # Walk upward until enough listing text is available.
   for _ in range(4):
    txt=" ".join(card.stripped_strings)
    if "万円" in txt and ("土地面積" in txt or "建物面積" in txt): break
    if card.parent: card=card.parent
   txt=" ".join(card.stripped_strings)
   price=parse_price(txt); land=parse_area("土地面積",txt); bld=parse_area("建物面積",txt)
   ym=re.search(r"(20\d{2}年\d{1,2}月)",txt)
   layout=(re.search(r"(\d+LDK(?:\+S（納戸）)?|\d+DK)",txt) or [None,""])[1]
   title=" ".join(a.stripped_strings).strip() or pid
   full=urljoin(url,href)
   address=(re.search(r"熊本県[^\s]{2,40}",txt) or [None,label])[1]
   # User preference: <= 50m JPY, detached houses; keep broader inventory,
   # while rejecting malformed cards without real price/areas.
   if price and price<=50_000_000 and land and bld:
    out.append(dict(property_id=pid,title=title,url=full,region=region_for(txt,label),address=address,current_price=price,land_area=land,building_area=bld,layout=layout,build_year=ym.group(1) if ym else ""))
 except Exception as e: print(f"WARN {label}: {e}")
 return out

def save(items):
 c=sqlite3.connect(DB_NAME); x=c.cursor(); today=datetime.now().strftime("%Y-%m-%d")
 ids={i["property_id"] for i in items}; new=changed=0
 for i in items:
  old=x.execute("SELECT current_price FROM properties WHERE property_id=?",(i["property_id"],)).fetchone()
  if old is None:
   new+=1
   x.execute("""INSERT INTO properties VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'active')""",(i["property_id"],i["title"],i["url"],i["region"],i["address"],i["current_price"],i["land_area"],i["building_area"],i["layout"],i["build_year"],today,today))
   x.execute("INSERT INTO price_history(property_id,price,recorded_at) VALUES(?,?,?)",(i["property_id"],i["current_price"],today))
  else:
   if old[0]!=i["current_price"]:
    changed+=1; x.execute("INSERT INTO price_history(property_id,price,recorded_at) VALUES(?,?,?)",(i["property_id"],i["current_price"],today))
   x.execute("""UPDATE properties SET title=?,url=?,region=?,address=?,current_price=?,land_area=?,building_area=?,layout=?,build_year=?,last_seen_date=?,status='active' WHERE property_id=?""",(i["title"],i["url"],i["region"],i["address"],i["current_price"],i["land_area"],i["building_area"],i["layout"],i["build_year"],today,i["property_id"]))
 # Only mark SUUMO records inactive after a successful non-empty crawl.
 if items:
  rows=x.execute("SELECT property_id FROM properties WHERE property_id LIKE 'suumo_%'").fetchall()
  for (pid,) in rows:
   if pid not in ids:x.execute("UPDATE properties SET status='inactive' WHERE property_id=?",(pid,))
 c.commit(); c.close(); print(f"OK inventory={len(items)} new={new} price_changes={changed}")

if __name__=="__main__":
 init_db(); items=[]; keys=set()
 for label,url in TARGET_SOURCES:
  for i in scrape(label,url):
   if i["property_id"] not in keys: keys.add(i["property_id"]); items.append(i)
 save(items)
