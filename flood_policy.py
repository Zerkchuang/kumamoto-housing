"""Fail closed: public listings are not flood-cleared without address-specific evidence."""
import json
from pathlib import Path
from datetime import date

REVIEW_FILE = Path(__file__).with_name('flood_reviews.json')

def flood_cleared(row):
    try:
        review = json.loads(REVIEW_FILE.read_text()).get('properties', {}).get(row['property_id'], {})
        return (review.get('status') == 'cleared'
                and review.get('listing_address') == row['address']
                and review.get('exact_location_verified') is True
                and review.get('history_checked') is True
                and review.get('past_flooding') is False
                and review.get('flood_zone') is False
                and review.get('pluvial_zone') is False
                and review.get('landslide_zone') is False
                and bool(review.get('sources'))
                and 0 <= (date.today() - date.fromisoformat(review['checked_at'])).days <= 30)
    except (OSError, ValueError, KeyError, TypeError):
        return False
