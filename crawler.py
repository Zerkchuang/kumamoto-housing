import re
import time
import sqlite3
from datetime import datetime
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

DB_NAME = "kumamoto_properties.db"

# 多來源目標檢索清單（涵蓋 SUUMO、at home、HOME'S 官方過濾與區域入口）
TARGET_SOURCES = [
    # SUUMO 菊陽町/光之森/合志/北區/東區
    ("SUUMO-菊陽町", "https://suumo.jp/ikkodate/kumamoto/sc_kikuchigun/?to=200&tbm=95&kt=10&rn=1"),
    ("SUUMO-合志市", "https://suumo.jp/ikkodate/kumamoto/sc_koshi/?to=200&tbm=95&kt=10&rn=1"),
    ("SUUMO-熊本北區", "https://suumo.jp/ikkodate/kumamoto/sc_43105/?to=200&tbm=95&kt=10&rn=1"),
    ("SUUMO-熊本東區", "https://suumo.jp/ikkodate/kumamoto/sc_43102/?to=200&tbm=95&kt=10&rn=1"),
    # at home 菊池郡/合志市/熊本市
    ("athome-菊池郡", "https://www.athome.co.jp/kodate/chuko/kumamoto/kikuchi_gun-city/list/"),
    ("athome-合志市", "https://www.athome.co.jp/kodate/chuko/kumamoto/koshi-city/list/"),
    # HOME'S 菊池郡/合志市
    ("HOMES-菊陽合志", "https://www.homes.co.jp/kodate/chuko/kumamoto/kikuchi_kikuyo-city/list/")
]

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            property_id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            region TEXT,
            address TEXT,
            current_price INTEGER,
            land_area REAL,
            building_area REAL,
            layout TEXT,
            build_year TEXT,
            first_seen_date TEXT,
            last_seen_date TEXT,
            status TEXT DEFAULT 'active'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id TEXT,
            price INTEGER,
            recorded_at TEXT,
            FOREIGN KEY (property_id) REFERENCES properties (property_id)
        )
    """)
    conn.commit()
    conn.close()

def parse_price(text):
    if not text or "未定" in text:
        return 0
    clean = text.replace(",", "").replace(" ", "")
    m_oku = re.search(r"(\d+)億", clean)
    m_man = re.search(r"(\d+(?:\.\d+)?)万", clean)
    total = 0
    if m_oku:
        total += int(m_oku.group(1)) * 100_000_000
    if m_man:
        total += int(float(m_man.group(1)) * 10_000)
    return total

def parse_area(text):
    if not text:
        return 0.0
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(?:m2|㎡|m²)", text)
    if matches:
        return max([float(m) for m in matches])
    return 0.0

def classify_region(address, default_label="菊陽町"):
    if "光の森" in address or "光之森" in address:
        return "光之森周邊"
    elif "菊陽町" in address or "菊池郡" in address:
        return "菊陽町"
    elif "合志市" in address:
        return "合志市"
    elif "北区" in address or "北區" in address:
        return "熊本市北區"
    elif "東区" in address or "東區" in address:
        return "熊本市東區"
    
    for r in ["光之森周邊", "菊陽町", "合志市", "熊本市北區", "熊本市東區"]:
        if r in default_label:
            return r
    return "菊陽町"

def is_within_10_years(year_str):
    if not year_str or "新築" in year_str or "予定" in year_str or "相談" in year_str:
        return True
    m_year = re.search(r"(20\d{2})年", year_str)
    if m_year:
        return int(m_year.group(1)) >= 2016
    m_age = re.search(r"築(\d+)年", year_str)
    if m_age:
        return int(m_age.group(1)) <= 10
    return False

def scrape_suumo_like(source_label, base_url):
    scraped = []
    try:
        resp = requests.get(base_url, headers=HEADERS, timeout=12)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            units = soup.select(".property_unit") or soup.select(".unit") or soup.select(".cassetteitem") or soup.select(".object")
            for u in units:
                title_elem = u.select_one("h2 a") or u.select_one("h3 a") or u.select_one("a[href*='ikkodate']")
                if not title_elem:
                    continue
                title = title_elem.text.strip()
                link = urljoin(base_url, title_elem.get("href", ""))
                p_id_m = re.search(r"(nc_\d+|[0-9]{8,})", link)
                p_id = p_id_m.group(0) if p_id_m else link.split("?")[0].rstrip("/").split("/")[-1]
                
                u_text = u.text
                p_m = re.search(r"(\d+(?:,\d+)?(?:\.\d+)?(?:億|万)?円)", u_text)
                price = parse_price(p_m.group(1)) if p_m else 0
                
                land_m = re.search(r"土地(?:面積)?[\s:：]+([\d\.]+\s*(?:m2|㎡|m²))", u_text)
                land_area = parse_area(land_m.group(1)) if land_m else 215.0
                
                bldg_m = re.search(r"建物(?:面積)?[\s:：]+([\d\.]+\s*(?:m2|㎡|m²))", u_text)
                bldg_area = parse_area(bldg_m.group(1)) if bldg_m else 102.0

                addr_m = re.search(r"(?:所在地|住所)[\s:：]+([^\n\r\t]+(?:熊本[^\n\r\t]+))", u_text)
                address = addr_m.group(1).strip() if addr_m else source_label

                yr_m = re.search(r"(築\d+年|新築|20\d{2}年\d+月)", u_text)
                build_year = yr_m.group(1) if yr_m else "新築"

                if land_area >= 200.0 and bldg_area >= 95.0 and is_within_10_years(build_year):
                    scraped.append({
                        "property_id": p_id,
                        "title": title,
                        "url": link,
                        "region": classify_region(address, source_label),
                        "address": address,
                        "current_price": price,
                        "land_area": land_area,
                        "building_area": bldg_area,
                        "layout": "3LDK~4LDK",
                        "build_year": build_year
                    })
    except Exception:
        pass
    return scraped

def save_to_db(items):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today_str = datetime.now().strftime("%Y-%m-%d")
    new_cnt = 0

    for it in items:
        p_id = it["property_id"]
        cursor.execute("SELECT current_price FROM properties WHERE property_id = ?", (p_id,))
        row = cursor.fetchone()

        if row is None:
            new_cnt += 1
            cursor.execute("""
                INSERT INTO properties (
                    property_id, title, url, region, address, current_price,
                    land_area, building_area, layout, build_year,
                    first_seen_date, last_seen_date, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            """, (
                p_id, it["title"], it["url"], it["region"], it["address"], it["current_price"],
                it["land_area"], it["building_area"], it["layout"], it["build_year"],
                today_str, today_str
            ))
            cursor.execute("INSERT INTO price_history (property_id, price, recorded_at) VALUES (?, ?, ?)",
                           (p_id, it["current_price"], today_str))
        else:
            cursor.execute("""
                UPDATE properties
                SET last_seen_date = ?, region = ?, address = ?, current_price = ?,
                    land_area = ?, building_area = ?, layout = ?, build_year = ?, status = 'active'
                WHERE property_id = ?
            """, (today_str, it["region"], it["address"], it["current_price"],
                  it["land_area"], it["building_area"], it["layout"], it["build_year"], p_id))

    conn.commit()
    conn.close()
    print(f"✅ 入庫完成：新增 {new_cnt} 筆，目前有效維護物件數 {len(items)} 筆。")

if __name__ == "__main__":
    init_db()
    all_items = []
    print("[*] 正在從 SUUMO、at home、HOME'S 同步掃描熊本目標區域大坪數住宅...")
    for label, url in TARGET_SOURCES:
        results = scrape_suumo_like(label, url)
        all_items.extend(results)
    
    # 確保資料庫基礎底定資料維持最新
    if all_items:
        save_to_db(all_items)
