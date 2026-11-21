import copy
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import crawler
from tainan import mark_new_listings, messages
from line_notify import build_messages

class NewListingTests(unittest.TestCase):
    def test_tainan_retains_history_through_empty_run_and_ignores_query(self):
        previous = {'listings': [{'url': 'https://example.com/house/1/?tracking=old'}]}
        report = dict(listings=[{'url': 'https://example.com/house/1', 'title': 'renamed'},
                                {'url': 'https://example.com/house/2'}])
        mark_new_listings(report, previous)
        self.assertFalse(report['listings'][0]['is_new_listing'])
        self.assertTrue(report['listings'][1]['is_new_listing'])
        empty = mark_new_listings({'listings': []}, report)
        returning = mark_new_listings({'listings': [{'url': 'https://example.com/house/2'}]}, empty)
        self.assertFalse(returning['listings'][0]['is_new_listing'])

    def test_kumamoto_only_new_ids_and_message_prefix(self):
        row = dict(property_id='suumo_123', title='住宅', url='https://suumo.jp/chukoikkodate/kumamoto/sc_koshi/nc_123/',
                   region='合志市', address='合志市', current_price=30000000, land_area=210,
                   building_area=110, layout='3LDK', build_year='2020年', property_type='house', price_basis=crawler.PRICE_BASIS)
        with tempfile.TemporaryDirectory() as directory, patch.object(crawler, 'DB_NAME', directory+'/test.db'):
            crawler.init_db()
            first = dict(checked_at='2026-11-21', sources=[])
            crawler.save([row], first)
            self.assertEqual(first['new_property_ids'], ['suumo_123'])
            with patch('line_notify.amenity_text', return_value=''):
                self.assertIn('🔴 新增｜【合志市', '\n'.join(build_messages(first, [row])))
            row['title'] = '改標題'; row['current_price'] = 29000000
            second = dict(checked_at='2026-11-22', sources=[])
            crawler.save([row], second)
            self.assertEqual(second['new_property_ids'], [])
            crawler.save([], dict(checked_at='2026-11-23', sources=[]))
            returning = dict(checked_at='2026-11-24', sources=[])
            crawler.save([row], returning)
            self.assertEqual(returning['new_property_ids'], [])

if __name__ == '__main__': unittest.main()
