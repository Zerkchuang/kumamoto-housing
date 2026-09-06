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

# 目標區域（掛載 rn=1 最新刊登優先）
# 43404: 菊池郡(菊陽町/大津町), 43216: 合志市, 43105: 熊本市北區, 43102: 熊本市東區
TARGET_URLS = [
    ("菊陽町・光之森", "https://suumo.jp/ikkodate/kumamoto/sc_kikuchigun/?rn=1"),
    ("合志市", "https://suumo.jp/ikkodate/kumamoto/sc_koshi/?rn=1"),
    ("熊本市北區", "https://suumo.jp/ikkodate/kumamoto/sc_43105/?rn=1"),
    ("熊本市東區", "https://suumo.jp/ikkodate/kumamoto/sc_43102/?rn=1"),
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
    # 支援 200.5m2, 200.5㎡, 200m2 等格式
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:m2|㎡|m²)", text)
    return float(match.group(1)) if match else 0.0

def classify_region(address, default_region):
    """精確劃分區域，特別將光之森生活圈獨立標示"""
    if "光の森" in address or "光之森" in address:
        return "光之森周邊"
    elif "菊陽町" in address:
        return "菊陽町"
    elif "合志市" in address:
        return "合志市"
    elif "北区" in address:
        return "熊本市北區"
    elif "東区" in address:
        return "熊本市東區"
    return default_region

def is_within_10_years(year_str):
    """只抓最新：必須是新築或 2016 年以後完工"""
    if not year_str or "新築" in year_str or "予定" in year_str or "相談" in year_str:
        return True
    match_year = re.search(r"(20\d{2})年", year_str)
    if match_year:
        return int(match_year.group(1)) >= 2016
    match_age = re.search(r"築(\d+)年", year_str)
    if match_age:
        return int(match_age.group(1)) <= 10
    return False

def is_valid_layout(layout):
    """3LDK / 3DK 以上"""
    if not layout:
        return True
    m = re.search(r"(\d+)[L|D|K|S]", layout)
    return int(m.group(1)) >= 3 if m else False

def scrape_suumo(region_label, base_url, max_pages=4):
    scraped = []
    domain = "https://suumo.jp"

    for page in range(1, max_pages + 1):
        url = f"{base_url}&pn={page}" if page > 1 else base_url
        print(f"[*] 爬取最新【{region_label}】第 {page} 頁: {url}")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
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
                title_elem = unit.select_one("h2 a") or unit.select_one(".property_inner-title a") or unit.select_one("a[href*='/ikkodate/']")
                if not title_elem:
                    continue

                title = title_elem.text.strip()
                link = urljoin(domain, title_elem.get("href", ""))
                id_m = re.search(r"nc_(\d+)|([0-9]{8,})", link)
                p_id = id_m.group(0) if id_m else link.split("?")[0].rstrip("/").split("/")[-1]

                # 價格解析
                p_elem = unit.select_one(".dottable-value") or unit.select_one(".price") or unit.select_one(".cassetteitem_price")
                price = parse_price(p_elem.text if p_elem else "")

                # 萃取整張卡片文字進行強力正則比對（徹底解決 table 漏抓面積問題）
                unit_text = unit.text

                # 所在地
                addr_match = re.search(r"(?:所在地|住所)[\s:：]+([^\n\r\t]+(?:熊本[^\n\r\t]+))", unit_text)
                address = addr_match.group(1).strip() if addr_match else ""
                if not address:
                    addr_elem = unit.select_one(".detail-item") or unit.select_one("td")
                    address = addr_elem.text.strip() if addr_elem else region_label

                # 土地面積 (m2)
                land_m = re.search(r"土地面積[\s:：]+([\d\.]+\s*(?:m2|㎡|m²))", unit_text)
                land_area = parse_area(land_m.group(1)) if land_m else 0.0

                # 建物面積 (m2)
                bldg_m = re.search(r"建物面積[\s:：]+([\d\.]+\s*(?:m2|㎡|m²))", unit_text)
                bldg_area = parse_area(bldg_m.group(1)) if bldg_m else 0.0

                # 格局
                layout_m = re.search(r"間取り[\s:：]+(\d+[A-Z]+(?:\+[A-Z]+)?)", unit_text)
                layout = layout_m.group(1).strip() if layout_m else "3LDK以上"

                # 築年月
                year_m = re.search(r"(?:完成時期|築年月)[\s:：]+([^\n\r\t]+)", unit_text)
                build_year = year_m.group(1).strip() if year_m else "新築"

                # 嚴格篩選過濾：地坪 >= 200m2，建坪 >= 95m2，10年內，3LDK以上
                if land_area < 200.0:
                    continue
                if bldg_area < 95.0:
                    continue
                if not is_valid_layout(layout):
                    continue
                if not is_within_10_years(build_year):
                    continue

                region = classify_region(address, region_label)

                scraped.append({
                    "property_id": p_id,
                    "title": title,
                    "url": link,
                    "region": region,
                    "address": address,
                    "current_price": price,
                    "land_area": land_area,
                    "building_area": bldg_area,
                    "layout": layout,
                    "build_year": build_year,
                })
            except Exception:
                continue

        time.sleep(2)
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
            cursor.execute("UPDATE properties SET last_seen_date = ?, region = ?, land_area = ?, building_area = ? WHERE property_id = ?",
                           (today_str, it["region"], it["land_area"], it["building_area"], p_id))

    conn.commit()
    conn.close()
    print(f"\n[+] 處理完成！共新增入庫 {new_cnt} 筆嚴選大坪數最新物件。")

if __name__ == "__main__":
    init_db()
    all_houses = []
    for label, url in TARGET_URLS:
        res = scrape_suumo(label, url, max_pages=4)
        all_houses.extend(res)
    save_to_db(all_houses)
