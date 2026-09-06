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
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://suumo.jp/",
}

DB_NAME = "kumamoto_properties.db"

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

def scrape_suumo(search_url, max_pages=2):
    scraped_items = []
    base_domain = "https://suumo.jp"

    for page in range(1, max_pages + 1):
        target_url = f"{search_url}&pn={page}" if page > 1 else search_url
        print(f"[*] 正在抓取第 {page} 頁: {target_url}")

        try:
            resp = requests.get(target_url, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                print(f"[-] HTTP 狀態碼異常: {resp.status_code}")
                break
        except Exception as e:
            print(f"[-] 連線發生錯誤: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        
        # 擴充相容 SUUMO 的不同版面結構
        units = soup.select(".property_unit")
        if not units:
            units = soup.select(".unit")
        if not units:
            units = soup.select(".cassetteitem")
        if not units:
            units = soup.select("div[class*='property_unit']")

        if not units:
            print(f"[!] 找不到卡片標籤，頁面長度: {len(resp.text)} 字元")
            # 儲存偵錯網頁以便檢查
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(resp.text)
            print("[i] 已輸出 debug_page.html 供排查")
            break

        print(f"[+] 成功解析出 {len(units)} 個物件卡片")

        for unit in units:
            try:
                # 抓取標題與連結
                title_elem = (
                    unit.select_one("h2 a") 
                    or unit.select_one(".property_inner-title a")
                    or unit.select_one(".cassetteitem_content-title a")
                    or unit.select_one("a[href*='/ikkodate/']")
                )
                if not title_elem:
                    continue

                title = title_elem.text.strip()
                raw_link = title_elem.get("href", "")
                link = urljoin(base_domain, raw_link)

                # 提取獨立 ID
                id_match = re.search(r"nc_(\d+)|([0-9]{8,})", link)
                prop_id = id_match.group(0) if id_match else link.split("?")[0].rstrip("/").split("/")[-1]

                # 抓取價格
                price_elem = (
                    unit.select_one(".dottable-value") 
                    or unit.select_one(".price")
                    or unit.select_one(".cassetteitem_price")
                    or unit.select_one("span[class*='price']")
                )
                price = parse_price(price_elem.text if price_elem else "")

                # 抓取表格資料
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

                scraped_items.append({
                    "property_id": prop_id,
                    "title": title,
                    "url": link,
                    "address": address,
                    "current_price": price,
                    "land_area": land_area,
                    "building_area": bldg_area,
                    "layout": layout,
                    "build_year": build_year,
                })
            except Exception:
                continue

        time.sleep(3)
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

    print("\n" + "=" * 50)
    print(f"📊 執行摘要 ({today_str})")
    print(f"✨ 成功入庫新物件: {new_count} 件")
    print(f"📉 本次降價物件: {len(price_drops)} 件")
    print("=" * 50)
    for drop in price_drops:
        print(f"🚨 【降價】{drop['title']}: {drop['old']//10000}萬 ➔ {drop['new']//10000}萬 (-{drop['diff']//10000}萬円)")
        print(f"   連結: {drop['url']}")

if __name__ == "__main__":
    init_db()
    # 鎖定菊陽町一戶建（新築＋中古一戶建綜合一覽頁）
    TARGET_URL = "https://suumo.jp/ikkodate/kumamoto/sc_kikuchigun/"
    print("[*] 開始執行熊本菊陽/周邊房產資料爬取...")
    data = scrape_suumo(TARGET_URL, max_pages=2)
    process_and_save(data)
