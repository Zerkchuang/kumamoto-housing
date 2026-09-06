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

# 鎖定目標行政區一覽：熊本市北區(43105)、熊本市東區(43102)、菊池郡菊陽町(43404)、合志市(43216)
TARGET_URLS = [
    "https://suumo.jp/ikkodate/kumamoto/sc_kikuchigun/", # 菊陽町/光之森
    "https://suumo.jp/ikkodate/kumamoto/sc_koshi/",      # 合志市
    "https://suumo.jp/ikkodate/kumamoto/sc_43105/",      # 熊本市北區
    "https://suumo.jp/ikkodate/kumamoto/sc_43102/",      # 熊本市東區
]

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            property_id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
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

def parse_price(price_str):
    if not price_str or "未定" in price_str:
        return 0
    clean_str = price_str.replace(",", "").replace(" ", "")
    match_oku = re.search(r"(\d+)億", clean_str)
    match_man = re.search(r"(\d+(?:\.\d+)?)万", clean_str)
    total = 0
    if match_oku:
        total += int(match_oku.group(1)) * 100_000_000
    if match_man:
        total += int(float(match_man.group(1)) * 10_000)
    return total

def parse_area(area_str):
    if not area_str:
        return 0.0
    match = re.search(r"(\d+(?:\.\d+)?)m", area_str)
    return float(match.group(1)) if match else 0.0

def is_valid_age(build_year_str):
    """判斷是否在 10 年內 (2016年以後或新築)"""
    if not build_year_str or "新築" in build_year_str or "予定" in build_year_str:
        return True
    match = re.search(r"(20\d{2})年", build_year_str)
    if match:
        return int(match.group(1)) >= 2016
    match_age = re.search(r"築(\d+)年", build_year_str)
    if match_age:
        return int(match_age.group(1)) <= 10
    return True

def is_valid_layout(layout_str):
    """判斷是否為 3LDK / 3DK 以上"""
    if not layout_str:
        return False
    match = re.search(r"(\d+)[L|D|K|S]", layout_str)
    if match:
        rooms = int(match.group(1))
        return rooms >= 3
    return False

def scrape_region(search_url, max_pages=3):
    scraped_items = []
    base_domain = "https://suumo.jp"

    for page in range(1, max_pages + 1):
        target_url = f"{search_url}&pn={page}" if page > 1 else search_url
        try:
            resp = requests.get(target_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                break
        except Exception:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        units = soup.select(".property_unit") or soup.select(".unit") or soup.select(".cassetteitem")

        if not units:
            break

        for unit in units:
            try:
                title_elem = unit.select_one("h2 a") or unit.select_one(".property_inner-title a") or unit.select_one(".cassetteitem_content-title a")
                if not title_elem:
                    continue

                title = title_elem.text.strip()
                link = urljoin(base_domain, title_elem.get("href", ""))
                prop_id_match = re.search(r"nc_(\d+)|([0-9]{8,})", link)
                prop_id = prop_id_match.group(0) if prop_id_match else link.split("?")[0].rstrip("/").split("/")[-1]

                price_elem = unit.select_one(".dottable-value") or unit.select_one(".price") or unit.select_one(".cassetteitem_price")
                price = parse_price(price_elem.text if price_elem else "")

                details = {}
                for tr in unit.select("table tr"):
                    th = tr.select_one("th")
                    td = tr.select_one("td")
                    if th and td:
                        details[th.text.strip()] = td.text.strip()

                address = details.get("所在地", "") or details.get("住所", "")
                layout = details.get("間取り", "")
                land_area = parse_area(details.get("土地面積", ""))
                bldg_area = parse_area(details.get("建物面積", ""))
                build_year = details.get("完成時期(築年月)", details.get("築年月", ""))

                # 嚴格條件過濾：地坪>=200㎡, 建坪>=95㎡, 3LDK以上, 10年內
                if land_area < 200.0:
                    continue
                if bldg_area < 95.0:
                    continue
                if not is_valid_layout(layout):
                    continue
                if not is_valid_age(build_year):
                    continue

                scraped_items.append({
                    "property_id": prop_id,
                    "title": title,
                    "url": link,
                    "address": address.strip(),
                    "current_price": price,
                    "land_area": land_area,
                    "building_area": bldg_area,
                    "layout": layout,
                    "build_year": build_year,
                })
            except Exception:
                continue

        time.sleep(2)
    return scraped_items

def process_and_save(items):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today_str = datetime.now().strftime("%Y-%m-%d")

    new_count = 0
    price_drops = []

    for item in items:
        p_id = item["property_id"]
        new_price = item["current_price"]

        cursor.execute("SELECT current_price, title FROM properties WHERE property_id = ?", (p_id,))
        existing = cursor.fetchone()

        if existing is None:
            new_count += 1
            cursor.execute("""
                INSERT INTO properties (
                    property_id, title, url, address, current_price,
                    land_area, building_area, layout, build_year,
                    first_seen_date, last_seen_date, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            """, (
                p_id, item["title"], item["url"], item["address"], new_price,
                item["land_area"], item["building_area"], item["layout"],
                item["build_year"], today_str, today_str
            ))
            cursor.execute("""
                INSERT INTO price_history (property_id, price, recorded_at)
                VALUES (?, ?, ?)
            """, (p_id, new_price, today_str))
        else:
            old_price = existing[0]
            cursor.execute("UPDATE properties SET last_seen_date = ?, status = 'active' WHERE property_id = ?", (today_str, p_id))
            if new_price > 0 and new_price < old_price:
                diff = old_price - new_price
                price_drops.append({
                    "title": item["title"],
                    "old": old_price,
                    "new": new_price,
                    "diff": diff,
                    "url": item["url"]
                })
                cursor.execute("UPDATE properties SET current_price = ? WHERE property_id = ?", (new_price, p_id))
                cursor.execute("INSERT INTO price_history (property_id, price, recorded_at) VALUES (?, ?, ?)", (p_id, new_price, today_str))

    conn.commit()
    conn.close()

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 完成過濾與入庫：新增 {new_count} 件，降價 {len(price_drops)} 件。")

if __name__ == "__main__":
    init_db()
    all_results = []
    print("[*] 開始爬取熊本指定區域（菊陽町/合志市/北區/東區）符合條件之住宅...")
    for url in TARGET_URLS:
        items = scrape_region(url, max_pages=3)
        all_results.extend(items)
    process_and_save(all_results)
