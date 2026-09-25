"""Explainable matching on verified listings; no invented commute estimates."""
import re


def match(row, profile):
    reasons = []
    price = int(row.get("current_price") or 0)
    kind = row.get("property_type") or "house"
    area = float(row.get("building_area") or 0)
    max_price = int(profile["max_price"]) * 10000
    regions = [s for s in profile["regions"].split(",") if s]
    if regions and row.get("region") not in regions:
        reasons.append("區域不符")
    if profile["kind"] == "house" and kind not in ("house", "house_new"):
        reasons.append("屋型不符")
    if profile["kind"] == "condo" and kind not in ("condo", "condo_new"):
        reasons.append("屋型不符")
    if price and price > max_price:
        reasons.append("超出預算")
    if area < int(profile["min_area"]):
        reasons.append("面積不足")
    built = re.search(r"(\d{4})年(\d{1,2})月", row.get("build_year") or "")
    seen = re.match(r"(\d{4})-(\d{2})", row.get("last_seen_date") or "")
    if built and seen:
        age = (int(seen[1]) * 12 + int(seen[2]) - int(built[1]) * 12 - int(built[2])) / 12
        if age > int(profile["max_age"]):
            reasons.append("屋齡超出")
    if reasons:
        return 0, reasons
    score = 60
    if price:
        score += 10 if price <= max_price * .85 else 5
    else:
        reasons.append("價格未定，須查證")
    if area >= int(profile["min_area"]) * 1.25:
        score += 10
    if kind in ("house_new", "condo_new"):
        score += 10
    if regions and row.get("region") == regions[0]:
        score += 10
    return min(score, 100), reasons
