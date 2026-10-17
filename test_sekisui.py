import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from bs4 import BeautifulSoup

import crawler
from sekisui_source import HOUSE, HEIM, region, make_row, classify, parse_house_new, parse_house_used, parse_heim_unit, builder_pages, save_report, load_report


class SekisuiTest(unittest.TestCase):
    url = 'https://www.heim-k.com/land/test-1gouchi/'

    def row(self, **changes):
        r = make_row(HEIM, '中古一戶建', 'test', self.url, '熊本県菊池郡大津町大津',
                     '150000000 円', 80, 60, '2020年6月')
        r.update(changes)
        return r

    def test_scope_is_address_based_and_includes_ozu(self):
        self.assertEqual(region('熊本県菊池郡大津町大津'), '大津町')
        self.assertEqual(region('熊本県菊池郡菊陽町光の森2丁目'), '光之森')
        self.assertEqual(region('熊本県熊本市　東区佐土原'), '熊本市東區')
        self.assertFalse(region('熊本県熊本市北区武蔵ケ丘'))
        self.assertFalse(region('熊本県宇土市'))

    def test_no_budget_or_area_filter_but_age_limit_remains(self):
        self.assertEqual(classify(self.row(), crawler), 'house')
        self.assertEqual(classify(self.row(build_year='2000年6月'), crawler), 'old')
        self.assertEqual(classify(self.row(build_year=''), crawler), 'pending')
        self.assertEqual(classify(self.row(address='福岡県福岡市',region=''), crawler), 'outside')
        self.assertEqual(self.row()['price_display'], '15,000萬円')

    def test_land_not_house_even_without_construction_date(self):
        r = self.row(kind='土地（需另建住宅）',build_year='')
        self.assertEqual(classify(r, crawler), 'land')

    def test_primary_house_card_does_not_use_renovation_as_build_date(self):
        s = BeautifulSoup('''<a href="https://sumusite.sekisuihouse.co.jp/kyushu/kodate/detail/C123/">
        <h2 class="textTitle">住宅</h2><p class="add">熊本県熊本市東区石原</p>
        <span>積水ハウス施工</span><div class="estate"><dl><dt>価格</dt><dd>1500万円</dd></dl>
        <ul><li>4LDK</li><li>建物 90㎡ 土地 250㎡</li><li>平成元年6月</li></ul></div>
        <div class="sales">2026年4月リフォーム</div></a>''','html.parser')
        row = parse_house_used(s)[0]
        self.assertEqual(row['build_year'], '1989年6月')
        self.assertEqual(row['building_area'], 90)
        self.assertEqual(classify(row,crawler),'old')

    def test_heim_unit_reads_explicit_completion_and_yen(self):
        s = BeautifulSoup('<h1>楓の森</h1><dl><dt>所在地</dt><dd>熊本県合志市豊岡</dd>'
             '<dt>販売価格（税込）</dt><dd>74900000 円</dd><dt>建物面積</dt><dd>96.10㎡</dd>'
             '<dt>完成年月</dt><dd>2026年6月</dd></dl>','html.parser')
        row = parse_heim_unit(s,self.url)
        self.assertEqual(row['price_display'],'7,490萬円')
        self.assertEqual(row['building_area'],96.1)
        self.assertEqual(row['build_year'],'2026年6月')

    def test_message_pages_preserve_all_rows_and_separate_brands(self):
        rows=[self.row(url=f'https://www.heim-k.com/land/test-{i}/') for i in range(90)]
        rows.append(self.row(brand=HOUSE,url='https://sumusite.sekisuihouse.co.jp/kyushu/kodate/detail/C123/'))
        report=dict(builder_checked_at=datetime.now(timezone.utc).isoformat(),builder_listings=rows)
        pages=builder_pages(report)
        self.assertTrue(all(len(x)<=4200 for x in pages))
        self.assertTrue(all(row['url'] in '\n'.join(pages) for row in rows))
        self.assertNotIn('/C123/', '\n'.join(builder_pages(report,HEIM)))

    def test_builder_update_does_not_refresh_normal_inventory_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/test.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE run_report(id INTEGER PRIMARY KEY,payload TEXT)')
                db.execute('INSERT INTO run_report VALUES(1,?)',(json.dumps({'checked_at':'old','property_ids':['existing']}),))
            save_report(path,{'builder_checked_at':'new','builder_listings':[]})
            report=load_report(path)
            self.assertEqual(report['checked_at'],'old')
            self.assertEqual(report['property_ids'],['existing'])
