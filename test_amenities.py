import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date
from unittest.mock import patch
from bs4 import BeautifulSoup

import crawler
from amenities import assigned_schools, extract_amenities, amenity_rows
from line_notify import build_messages


class AmenitiesTest(unittest.TestCase):
    def soup(self, html):
        return BeautifulSoup(html, 'html.parser')

    def test_facility_rows_keep_distances_and_exclude_supermarket(self):
        html = '''<ul>
        <li><div>小学校</div><div>菊陽西小学校：徒歩8分（595ｍ）</div></li>
        <li><div>中学校</div><div>武蔵ケ丘中学校：徒歩20分（1600ｍ）</div></li>
        <li><div>ショッピングセンター</div><div>ゆめタウン光の森：徒歩12分（960ｍ）</div></li>
        <li><div>スーパー</div><div>近所のスーパー：徒歩2分（160ｍ）</div></li></ul>'''
        a = extract_amenities(self.soup(html), '菊陽町光の森6', 'test')
        self.assertEqual(len(a['schools']), 2)
        self.assertEqual(a['schools'][0]['distance'], '595 公尺／步行 8 分鐘')
        self.assertEqual([m['name'] for m in a['malls']], ['ゆめタウン光の森'])
        self.assertTrue(all(d['name'] == '待確認' for d in a['districts']))

    def test_station_bus_stop_and_transfer_are_not_confused(self):
        html = '''<table><tr><th>交通</th><td>
        JR豊肥本線「光の森」歩12分<br>
        熊本電気鉄道「黒石」車3km<br>
        熊本電気鉄道「黒石」歩38分<br>
        電鉄バス「泉ヶ丘団地」歩4分<br>
        JR豊肥本線「三里木」バス10分停歩2分</td></tr></table>'''
        a = extract_amenities(self.soup(html), '菊陽町光の森6', 'test')
        self.assertEqual(len(a['stations']), 3)
        self.assertIn('光の森', a['stations'][0]['name'])
        self.assertEqual(a['stations'][0]['distance'], '步行 12 分鐘')
        self.assertEqual(a['stations'][1]['distance'], '3 公里')
        self.assertNotIn('泉ヶ丘団地', json.dumps(a['stations'], ensure_ascii=False))

    def test_mobile_facility_components(self):
        html = '''<div class="shuhenkankyo-info__item">
        <div class="shuhenkankyo-info__cassette__info__caption">武蔵ケ丘中学校</div>
        <div>1.4km</div><div>徒歩18分</div></div>
        <span class="shuhenkankyo-images__list__cassette__text-wrap__shisetsu">ゆめタウン光の森まで960m 徒歩約12分</span>'''
        a = extract_amenities(self.soup(html), '菊陽町光の森6', 'test')
        self.assertEqual(a['schools'][0]['distance'], '1.4 公里／步行 18 分鐘')
        self.assertEqual(a['malls'][0]['distance'], '960 公尺／步行 約 12 分鐘')

    def test_official_whole_town_and_split_street(self):
        a = assigned_schools('熊本県熊本市中央区新屋敷1丁目8-3', date(2026, 10, 5))
        self.assertEqual([x['name'] for x in a], ['白川小学校', '白川中学校'])
        self.assertTrue(all('官方全域' in x['note'] for x in a))
        b = assigned_schools('熊本県熊本市中央区坪井1', date(2026, 10, 5))
        self.assertIn('待完整門牌', b[0]['note'])
        self.assertNotIn('官方全域', b[0]['note'])
        self.assertNotIn('官方全域', str(assigned_schools('熊本市中央区新屋敷10')))

    def test_catalog_expiry_and_missing_json_are_explicit(self):
        a = assigned_schools('熊本市中央区新屋敷1丁目', date(2027, 4, 1))
        self.assertTrue(all(x['name'] == '待確認' for x in a))
        rows = amenity_rows({'address': '合志市豊岡', 'amenities_json': 'broken'})
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(x['名稱'] == '待確認' for x in rows))

    def test_schema_persistence_and_update_clears_old_details(self):
        row = dict(property_id='suumo_123', title='test', url='https://suumo.jp/chukoikkodate/kumamoto/sc_koshi/nc_123/',
                   region='合志市', address='合志市豊岡', current_price=30000000, land_area=250,
                   building_area=110, layout='4LDK', build_year='2020年9月', property_type='house',
                   price_basis=crawler.PRICE_BASIS, amenities_json='{"malls":[{"name":"ゆめタウン光の森","distance":"960 公尺","note":"刊登"}]}')
        with tempfile.TemporaryDirectory() as tmp, patch.object(crawler, 'DB_NAME', tmp + '/db'):
            crawler.init_db()
            crawler.save([row])
            with sqlite3.connect(crawler.DB_NAME) as db:
                self.assertIn('ゆめタウン', db.execute('SELECT amenities_json FROM properties').fetchone()[0])
            del row['amenities_json']
            row['address'] = '合志市須屋'
            crawler.save([row])
            with sqlite3.connect(crawler.DB_NAME) as db:
                self.assertEqual(db.execute('SELECT amenities_json FROM properties').fetchone()[0], '{}')

    def test_table_follows_house_info_in_all_line_views(self):
        from importlib import import_module
        row = dict(property_id='suumo_123', title='test', url='https://suumo.jp/chukoikkodate/kumamoto/sc_koshi/nc_123/',
                   region='合志市', address='合志市豊岡', current_price=30000000, land_area=250,
                   building_area=110, layout='4LDK', build_year='2020年9月', property_type='house')
        report = {'checked_at': 'test', 'sources': []}
        message = '\n'.join(build_messages(report, [row]))
        self.assertGreater(message.index('周邊資訊｜'), message.index(row['url']))
        with patch.dict(os.environ, {'LINE_CHANNEL_ACCESS_TOKEN': 'test', 'LINE_CHANNEL_SECRET': 'test'}):
            webhook = import_module('line_webhook')
            pages = webhook.inventory_pages([row], report)
            self.assertIn('大型商場｜待確認', '\n'.join(pages))
            with patch.object(webhook, 'load_inventory', return_value=([row], report)):
                self.assertIn('周邊資訊｜', webhook.homes())


if __name__ == '__main__':
    unittest.main()
