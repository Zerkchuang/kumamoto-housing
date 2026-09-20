import re
import sqlite3
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
    "Accept-Language": "ja-JP,ja;q=0.9",
}
DB_NAME = "kumamoto_properties.db"
MAX_PRICE = 70_000_000
MAX_AGE_YEARS = 15
MIN_HOUSE_LAND = 200
MIN_HOUSE_BUILDING = 100
MIN_CONDO_AREA = 70
PREFERRED_CONDO_AREA = 132.23  # roughly 40 tsubo; preference, not a hard cutoff

# Houses keep the large-lot requirements. Condos use exclusive floor area instead,
# so they are never incorrectly rejected for not owning 200 m2 of land.
TARGET_SOURCES = [
    ("菊陽町", "house", "https://suumo.jp/chukoikkodate/kumamoto/sc_kikuchigun/"),
    ("合志市", "house", "https://suumo.jp/chukoikkodate/kumamoto/sc_koshi/"),
    ("光之森", "house", "https://suumo.jp/b/kodate/kw/%E5%85%89%E3%81%AE%E6%A3%AE%E3%80%80%E4%B8%AD%E5%8F%A4%E7%89%A9%E4%BB%B6/"),
    ("熊本市東區", "house", "https://suumo.jp/chukoikkodate/kumamoto/sc_kumamotoshihigashi/"),
    ("熊本市北區", "house", "https://suumo.jp/chukoikkodate/kumamoto/sc_kumamotoshikita/"),
    ("熊本市中央區", "condo", "https://suumo.jp/ms/chuko/kumamoto/sc_kumamotoshichuo/"),
    ("熊本市東區", "condo", "https://suumo.jp/ms/chuko/kumamoto/sc_kumamotoshihigashi/"),
    ("熊本市北區", "condo", "https://suumo.jp/ms/chuko/kumamoto/sc_kumamotoshikita/"),
    ("熊本市新築大樓", "condo_new", "https://suumo.jp/ms/shinchiku/kumamoto/"),
]
CONDO_REGIONS = {"熊本市中央區", "熊本市東區", "熊本市北區"}


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS properties(
        property_id TEXT PRIMARY KEY,title TEXT,url TEXT,region TEXT,address TEXT,
        current_price INTEGER,land_area REAL,building_area REAL,layout TEXT,
        build_year TEXT,first_seen_date TEXT,last_seen_date TEXT,
        status TEXT DEFAULT 'active',property_type TEXT DEFAULT 'house')"""
    )
    columns = {row[1] for row in cur.execute("PRAGMA table_info(properties)")}
    if "property_type" not in columns:
        cur.execute("ALTER TABLE properties ADD COLUMN property_type TEXT DEFAULT 'house'")
    cur.execute(
        """CREATE TABLE IF NOT EXISTS price_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,property_id TEXT,price INTEGER,
        recorded_at TEXT,FOREIGN KEY(property_id) REFERENCES properties(property_id))"""
    )
    conn.commit()
    conn.close()


def normalize_text(soup):
    return (
        " ".join(soup.stripped_strings)
        .replace("ｍ²", "m2")
        .replace("m²", "m2")
        .replace("㎡", "m2")
        .replace("平米", "m2")
    )


def first_number(patterns, text):
    for pattern in patterns:
        match = re.search(pattern, text, re.S)
        if match:
            return float(match.group(1).replace(",", ""))
    return 0.0


def parse_region(address, title, fallback):
    joined = f"{address} {title}"
    if "熊本市東区" in joined:
        return "熊本市東區"
    if "熊本市北区" in joined:
        return "熊本市北區"
    if "熊本市中央区" in joined:
        return "熊本市中央區"
    if "光の森" in joined:
        return "光之森周邊"
    if "菊陽" in joined or "菊池郡" in joined:
        return "菊陽町"
    if "合志" in joined:
        return "合志市"
    return fallback


def build_date(text, is_new=False):
    patterns = [
        r"完成時期\s*(?:\(築年月\)\s*)?((?:19|20)\d{2}年\d{1,2}月)",
        r"築年月\s*((?:19|20)\d{2}年\d{1,2}月)",
    ]
    if is_new:
        patterns.append(r"引渡可能時期\s*((?:20)\d{2}年\d{1,2}月)")
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def within_age_limit(date_text):
    match = re.match(r"(\d{4})年(\d{1,2})月", date_text)
    if not match:
        return False
    built_month = int(match.group(1)) * 12 + int(match.group(2))
    now = datetime.now()
    cutoff_month = (now.year - MAX_AGE_YEARS) * 12 + now.month
    return built_month >= cutoff_month


def detail(pid, href, label, property_type):
    url = urljoin("https://suumo.jp", href.split("?")[0])
    response = requests.get(url, headers=HEADERS, timeout=25)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    text = normalize_text(soup)
    if pid not in response.url and pid not in response.text:
        raise ValueError("detail identity mismatch")

    early_text = text[:2500]
    if property_type == "condo_new" and re.search(r"価格\s*未定", early_text[:1000]):
        price_man = 0
    else:
        price_man = first_number(
            [r"(?:物件価格|価格)\s*(\d[\d,]*)\s*万円", r"(\d[\d,]*)\s*万円"],
            early_text,
        )
    price = int(price_man) * 10_000
    land = first_number([r"土地面積\s*(\d+(?:\.\d+)?)\s*m2"], text)
    if property_type == "house":
        area = first_number([r"建物面積\s*(\d+(?:\.\d+)?)\s*m2"], text)
    else:
        # For a new development with a size range, use the largest offered unit so
        # developments containing a 70 m2+ candidate remain on the watchlist.
        range_match = re.search(
            r"専有面積\s*(\d+(?:\.\d+)?)\s*(?:m2)?\s*[～~-]\s*(\d+(?:\.\d+)?)\s*m2",
            text,
        )
        area = float(range_match.group(2)) if range_match else first_number(
            [r"専有面積\s*(\d+(?:\.\d+)?)\s*m2"], text
        )

    layout_match = re.search(r"(\d+LDK(?:\+S（納戸）)?(?:\s*[～~-]\s*\d+LDK)?|\d+DK)", text)
    completed = build_date(text, is_new=property_type == "condo_new")
    address_match = re.search(
        r"(熊本県.*?)(?:\s*地図を見る|\s*\[\s*地図\s*\]|\s+TOP\s)", early_text
    )
    if not address_match:
        address_match = re.search(
            r"所在地\s*(熊本県.*?)(?:\s*交通\s|\s*月々支払い|\s*情報提供日)", text
        )
    heading = soup.find("h1")
    title = " ".join(heading.stripped_strings) if heading else (
        soup.title.get_text(" ", strip=True) if soup.title else f"SUUMO {pid}"
    )
    address = address_match.group(1).strip() if address_match else label
    region = parse_region(address, title, label)

    print(
        f"PARSE {pid}: type={property_type} price={price} land={land} "
        f"area={area} built={completed} region={region} url={url}"
    )
    if not completed or not within_age_limit(completed):
        return None

    if property_type == "house":
        matches = 0 < price <= MAX_PRICE and land >= MIN_HOUSE_LAND and area >= MIN_HOUSE_BUILDING
    else:
        known_price_matches = 0 < price <= MAX_PRICE
        price_pending_new_build = property_type == "condo_new" and price == 0
        matches = (
            region in CONDO_REGIONS
            and area >= MIN_CONDO_AREA
            and (known_price_matches or price_pending_new_build)
        )
    if not matches:
        return None

    return {
        "property_id": "suumo_" + pid,
        "title": title,
        "url": url,
        "region": region,
        "address": address,
        "current_price": price,
        "land_area": land,
        "building_area": area,
        "layout": layout_match.group(1) if layout_match else "",
        "build_year": completed,
        "property_type": property_type,
    }


def scrape(label, property_type, url):
    response = requests.get(url, headers=HEADERS, timeout=25)
    response.raise_for_status()
    pairs = []
    seen = set()
    for match in re.finditer(r'href=["\']([^"\']*/nc_(\d+)/?[^"\']*)["\']', response.text):
        href, pid = match.group(1), match.group(2)
        if pid not in seen:
            seen.add(pid)
            pairs.append((pid, href))
    print(f"DISCOVER {label}/{property_type}: {len(pairs)} detail links")
    items = []
    for pid, href in pairs:
        try:
            item = detail(pid, href, label, property_type)
            if item:
                items.append(item)
        except Exception as exc:
            print(f"WARN detail {pid}: {exc}")
    return items


def save(items):
    if not items:
        raise RuntimeError("Verified inventory is 0; refusing to send stale data")
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    ids = {item["property_id"] for item in items}
    new = changed = 0
    for item in items:
        old = cur.execute(
            "SELECT current_price FROM properties WHERE property_id=?", (item["property_id"],)
        ).fetchone()
        if old is None:
            new += 1
            cur.execute(
                """INSERT INTO properties(
                property_id,title,url,region,address,current_price,land_area,
                building_area,layout,build_year,first_seen_date,last_seen_date,
                status,property_type) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'active',?)""",
                (
                    item["property_id"], item["title"], item["url"], item["region"],
                    item["address"], item["current_price"], item["land_area"],
                    item["building_area"], item["layout"], item["build_year"],
                    today, today, item["property_type"],
                ),
            )
            cur.execute(
                "INSERT INTO price_history(property_id,price,recorded_at) VALUES(?,?,?)",
                (item["property_id"], item["current_price"], today),
            )
        else:
            if old[0] != item["current_price"]:
                changed += 1
                cur.execute(
                    "INSERT INTO price_history(property_id,price,recorded_at) VALUES(?,?,?)",
                    (item["property_id"], item["current_price"], today),
                )
            cur.execute(
                """UPDATE properties SET title=?,url=?,region=?,address=?,current_price=?,
                land_area=?,building_area=?,layout=?,build_year=?,last_seen_date=?,
                property_type=?,status='active' WHERE property_id=?""",
                (
                    item["title"], item["url"], item["region"], item["address"],
                    item["current_price"], item["land_area"], item["building_area"],
                    item["layout"], item["build_year"], today, item["property_type"],
                    item["property_id"],
                ),
            )
    for (pid,) in cur.execute(
        "SELECT property_id FROM properties WHERE property_id LIKE 'suumo_%'"
    ).fetchall():
        if pid not in ids:
            cur.execute("UPDATE properties SET status='inactive' WHERE property_id=?", (pid,))
    conn.commit()
    conn.close()
    print(f"OK verified_inventory={len(items)} new={new} price_changes={changed}")


if __name__ == "__main__":
    init_db()
    inventory = []
    seen_ids = set()
    for source_label, source_type, source_url in TARGET_SOURCES:
        try:
            for property_item in scrape(source_label, source_type, source_url):
                if property_item["property_id"] not in seen_ids:
                    seen_ids.add(property_item["property_id"])
                    inventory.append(property_item)
        except Exception as exc:
            print(f"WARN source {source_label}/{source_type}: {exc}")
    save(inventory)
