import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
import flood_policy

class FloodPolicyTest(unittest.TestCase):
    def test_missing_unknown_flooded_or_wrong_address_are_held(self):
        with tempfile.TemporaryDirectory() as d, patch.object(flood_policy,'REVIEW_FILE',Path(d)/'reviews.json'):
            row={'property_id':'test','address':'exact listing address'}
            self.assertFalse(flood_policy.flood_cleared(row))
            review=dict(status='cleared',listing_address=row['address'],exact_location_verified=True,
                history_checked=True,past_flooding=False,flood_zone=False,pluvial_zone=False,
                landslide_zone=False,sources=['official address-specific evidence'],checked_at=date.today().isoformat())
            def write(): flood_policy.REVIEW_FILE.write_text(json.dumps({'properties':{'test':review}}))
            write();self.assertTrue(flood_policy.flood_cleared(row))
            for key in ['past_flooding','flood_zone','pluvial_zone','landslide_zone']:
                review[key]=True;write();self.assertFalse(flood_policy.flood_cleared(row));review[key]=False
            review['history_checked']=None;write();self.assertFalse(flood_policy.flood_cleared(row))
            review['history_checked']=True;write();self.assertFalse(flood_policy.flood_cleared(dict(row,address='changed')))
