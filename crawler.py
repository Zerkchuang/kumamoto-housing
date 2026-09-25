import re
import json
import sys
import sqlite3
import unicodedata
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
    "Accept-Language": "ja-JP,ja;q=0.9",
}
DB_NAME = "kumamoto_properties.db"
# Budget: TWD 15,000,000 / 0.2055 TWD per JPY; reference 2026-09-20.
MAX_PRICE = 72992700
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
    ("光之森中古", "mixed", "https://suumo.jp/b/kodate/kw/%E5%85%89%E3%81%AE%E6%A3%AE%E3%80%80%E4%B8%AD%E5%8F%A4%E7%89%A9%E4%BB%B6/"),
    ("光之森新築", "mixed", "https://suumo.jp/b/kodate/kw/%E5%85%89%E3%81%AE%E6%A3%AE%E3%80%80%E6%96%B0%E7%AF%89/"),
    ("熊本市東區", "house", "https://suumo.jp/chukoikkodate/kumamoto/sc_kumamotoshihigashi/"),
    ("熊本市北區", "house", "https://suumo.jp/chukoikkodate/kumamoto/sc_kumamotoshikita/"),
    ("菊陽町", "house_new", "https://suumo.jp/ikkodate/kumamoto/sc_kikuchigun/"),
    ("合志市", "house_new", "https://suumo.jp/ikkodate/kumamoto/sc_koshi/"),
    ("熊本市東區", "house_new", "https://suumo.jp/ikkodate/kumamoto/sc_kumamotoshihigashi/"),
    ("熊本市北區", "house_new", "https://suumo.jp/ikkodate/kumamoto/sc_kumamotoshikita/"),
    ("熊本市中央區", "condo", "https://suumo.jp/ms/chuko/kumamoto/sc_kumamotoshichuo/"),
    ("熊本市東區", "condo", "https://suumo.jp/ms/chuko/kumamoto/sc_kumamotoshihigashi/"),
    ("熊本市北區", "condo", "https://suumo.jp/ms/chuko/kumamoto/sc_kumamotoshikita/"),
    ("熊本市新築大樓", "condo_new", "https://suumo.jp/ms/shinchiku/kumamoto/"),
]
CONDO_REGIONS = {"熊本市中央區", "熊本市東區", "熊本市北區", "光之森"}
HOUSE_REGIONS = {"菊陽町", "光之森", "光之森周邊", "合志市", "熊本市東區", "熊本市北區"}


def is_hikari(address):
    """The 光の森 neighbourhood, not a station name in an unrelated address."""
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", address or ""))
    return bool(re.search(r"菊陽町光の森(?:[1-7](?:丁目)?|$)", compact))


def parse_price(value):
    """Read the first advertised price, including 億; 0 means unknown."""
    value = unicodedata.normalize("NFKC", value).replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)億(?:(\d+(?:\.\d+)?)万)?円", value)
    if m:
        return round(float(m[1]) * 100000000 + float(m[2] or 0) * 10000)
    m = re.search(r"(\d+(?:\.\d+)?)万円", value)
    return round(float(m[1]) * 10000) if m else 0


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
    if is_hikari(address):
        return "光之森"
    joined = address
    if "熊本市東区" in joined:
        return "熊本市東區"
    if "熊本市北区" in joined:
        return "熊本市北區"
    if "熊本市中央区" in joined:
        return "熊本市中央區"
    if "熊本市南区" in joined:
        return "熊本市南區"
    if "熊本市西区" in joined:
        return "熊本市西區"
    if "大津町" in joined:
        return "大津町"
    if "菊陽" in joined:
        return "菊陽町"
    if "合志" in joined:
        return "合志市"
    return fallback


def build_date(text, is_new=False):
    patterns = [
        r"(?:完成時期|完成予定時期?)\s*(?:\(築年月\)\s*)?((?:19|20)\d{2}年\d{1,2}月)",
        r"築年月\s*((?:19|20)\d{2}年\d{1,2}月)",
    ]
    if is_new:
        patterns.append(r"引渡可能時期\s*((?:20)\d{2}年\d{1,2}月)")
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def within_age_limit(date_text, strict=False, now=None):
    match = re.match(r"(\d{4})年(\d{1,2})月", date_text)
    if not match:
        return False
    built_month = int(match.group(1)) * 12 + int(match.group(2))
    now = now or datetime.now()
    if not 1 <= int(match.group(2)) <= 12:
        return False
    cutoff_month = (now.year - MAX_AGE_YEARS) * 12 + now.month
    return built_month > cutoff_month if strict else built_month >= cutoff_month


def detail(pid, href, label, property_type):
    url = urljoin("https://suumo.jp", href.split("?")[0])
    response = requests.get(url, headers=HEADERS, timeout=25)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    text = normalize_text(soup)
    if pid not in response.url and pid not in response.text:
        raise ValueError("detail identity mismatch")

    early_text = text[:2500]
    is_house = property_type in {"house", "house_new"}
    is_new = property_type in {"house_new", "condo_new"}
    if property_type == "condo_new" and re.search(r"価格\s*未定", early_text[:1000]):
        price = 0
    else:
        price = parse_price(early_text)
    land = first_number([r"土地面積\s*(\d+(?:\.\d+)?)\s*m2"], text)
    if property_type == "house_new":
        land_range = re.search(
            r"土地面積\s*(\d+(?:\.\d+)?)\s*(?:m2)?\s*[～~-]\s*(\d+(?:\.\d+)?)\s*m2",
            text,
        )
        if land_range:
            land = max(float(land_range.group(1)), float(land_range.group(2)))
    if is_house:
        area = first_number([r"建物面積\s*(\d+(?:\.\d+)?)\s*m2"], text)
        if property_type == "house_new":
            area_range = re.search(
                r"建物面積\s*(\d+(?:\.\d+)?)\s*(?:m2)?\s*[～~-]\s*(\d+(?:\.\d+)?)\s*m2",
                text,
            )
            if area_range:
                area = max(float(area_range.group(1)), float(area_range.group(2)))
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
    completed = build_date(text, is_new=is_new)
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
    hikari = is_hikari(address)
    if not completed or not within_age_limit(completed, strict=hikari):
        return None

    if hikari:
        # Collect all known-age residential offers here; budget and area are preferences.
        matches = True
    elif is_house:
        matches = (
            region in HOUSE_REGIONS
            and 0 < price <= MAX_PRICE
            and land >= MIN_HOUSE_LAND
            and area >= MIN_HOUSE_BUILDING
        )
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


def hikari_listing_page(html, page_url, root_url):
    """Return same-search pagination and residential detail links with relevant addresses."""
    soup = BeautifulSoup(html, "html.parser")
    root_path = unquote(urlparse(root_url).path).rstrip("/") + "/"
    pages, pairs = set(), {}
    for a in soup.select("a[href]"):
        href = urljoin(page_url, a["href"]).split("?")[0]
        parsed = urlparse(href)
        if parsed.scheme != "https" or parsed.hostname != "suumo.jp":
            continue
        path = unquote(parsed.path)
        if re.fullmatch(re.escape(root_path) + r"\d+/", path):
            pages.add(href)
        m = re.fullmatch(r"/(chukoikkodate|ikkodate|ms/chuko|ms/shinchiku)/kumamoto/[^/]+/nc_(\d+)/?", parsed.path)
        if not m:
            continue
        card = a.find_parent("div", class_="cassette")
        address = None
        listed_date = ""
        if card:
            for header in card.select(".cassette_item-header"):
                value = header.find_next_sibling(class_="cassette_item-body")
                if not value:
                    continue
                name = header.get_text(strip=True)
                if name == "所在地":
                    address = value.get_text("", strip=True)
                elif name in {"築年月", "完成時期", "完成予定時期"}:
                    listed_date = value.get_text("", strip=True)
        # Missing card address still gets a detail-page check; never trust title/station alone.
        if address and not is_hikari(address):
            continue
        kind = {"chukoikkodate": "house", "ikkodate": "house_new",
                "ms/chuko": "condo", "ms/shinchiku": "condo_new"}[m[1]]
        pairs[m[2]] = (href, kind, listed_date)
    return pairs, pages


def scrape_hikari(url, report=None):
    pending, visited, pairs = {url}, set(), {}
    while pending and len(visited) < 100:
        page_url = sorted(pending)[0]
        pending.remove(page_url)
        if page_url in visited:
            continue
        visited.add(page_url)
        try:
            for attempt in range(2):
                try:
                    response = requests.get(page_url, headers=HEADERS, timeout=25)
                    response.raise_for_status()
                    break
                except requests.RequestException as exc:
                    status = exc.response.status_code if exc.response is not None else 0
                    if attempt or (status and status < 500):
                        raise
                    time.sleep(1)
            found, pages = hikari_listing_page(response.text, page_url, url)
            pairs.update(found)
            pending.update(pages - visited)
        except requests.RequestException as exc:
            if report is not None:
                report["errors"] += 1
            print(f"WARN Hikari list page {page_url}: {exc}")
    if report is not None:
        report["scope"] = f"光之森地址核對；列表分頁 {len(visited)} 頁；屋齡未滿15年，不限預算與面積"
        report["discovered"] = len(pairs)
        if pending:
            report["errors"] += 1
            report["scope"] += "；分頁達保護上限，未完整"
    items = []
    for pid, (href, kind, listed_date) in sorted(pairs.items()):
        if re.match(r"\d{4}年\d{1,2}月", listed_date) and not within_age_limit(listed_date, strict=True):
            continue
        try:
            item = detail(pid, href, "光之森", kind)
            if item and is_hikari(item["address"]):
                items.append(item)
        except Exception as exc:
            if report is not None:
                report["errors"] += 1
            print(f"WARN Hikari detail {pid}: {exc}")
    return items


def scrape(label, property_type, url, report=None):
    if property_type == "mixed":
        return scrape_hikari(url, report)
    listing_headers = HEADERS.copy()
    if property_type in {"house_new", "condo_new"}:
        # SUUMO's mobile new-build listings can omit canonical /nc_ detail links.
        listing_headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
        )
    response = requests.get(url, headers=listing_headers, timeout=25)
    response.raise_for_status()
    pairs = []
    seen = set()
    for match in re.finditer(r'href=["\']([^"\']*/nc_(\d+)/?[^"\']*)["\']', response.text):
        href, pid = match.group(1), match.group(2)
        if pid not in seen:
            seen.add(pid)
            pairs.append((pid, href))
    if report is not None:
        report["discovered"] = len(pairs)
    print(f"DISCOVER {label}/{property_type}: {len(pairs)} detail links")
    items = []
    for pid, href in pairs:
        try:
            item = detail(pid, href, label, property_type)
            if item:
                items.append(item)
        except Exception as exc:
            if report is not None:
                report["errors"] += 1
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
        "SELECT property_id FROM properties"
    ).fetchall():
        if pid not in ids:
            cur.execute("UPDATE properties SET status='unverified' WHERE property_id=?", (pid,))
    conn.commit()
    conn.close()
    print(f"OK verified_inventory={len(items)} new={new} price_changes={changed}")


if __name__ == "__main__":
    from extra_sources import collect
    init_db()
    inventory, statuses = [], []
    seen_ids = set()
    for source_label, source_type, source_url in TARGET_SOURCES:
        report = dict(source="SUUMO / " + source_label + "/" + source_type,
                      scope="列表首頁", discovered=0, matched=0, errors=0, unreadable=0)
        try:
            found = scrape(source_label, source_type, source_url, report)
            report["matched"] = len(found)
            report["status"] = ("部分完成" if report["discovered"] else "連線或解析失敗") if report["errors"] else ("完成" if report["discovered"] else "未辨識到詳細頁；待檢查")
            for item in found:
                if item["property_id"] not in seen_ids:
                    seen_ids.add(item["property_id"])
                    inventory.append(item)
        except Exception as exc:
            report["status"] = "連線或解析失敗"
            report["errors"] += 1
            print(f"WARN source {source_label}/{source_type}: {exc}")
        statuses.append(report)
    extra, reports = collect(sys.modules[__name__])
    for item in extra:
        if item["property_id"] not in seen_ids:
            inventory.append(item)
            seen_ids.add(item["property_id"])
    statuses.extend(reports)
    if inventory:
        save(inventory)
    # Persist every source outcome, including failed/empty runs. Notifications use
    # this exact run's IDs, never yesterday's or an earlier same-day inventory.
    payload = dict(checked_at=datetime.now().isoformat(timespec="seconds") + " UTC",
                   sources=statuses, property_ids=sorted(seen_ids), count=len(inventory))
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS run_report(id INTEGER PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT OR REPLACE INTO run_report VALUES(1, ?)", (json.dumps(payload, ensure_ascii=False),))
    print("RUN_REPORT " + json.dumps(payload, ensure_ascii=False), flush=True)
