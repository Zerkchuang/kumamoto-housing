import unittest
from datetime import datetime
from bs4 import BeautifulSoup
import crawler
from extra_sources import parse_detail, valid_detail_url, discover
from line_notify import build_messages

class SourcesTest(unittest.TestCase):
    def test_hikari_age_is_strict_and_address_not_station(self):
        now = datetime(2026,9,25)
        self.assertTrue(crawler.within_age_limit('2011年10月', strict=True, now=now))
        self.assertFalse(crawler.within_age_limit('2011年9月', strict=True, now=now))
        self.assertFalse(crawler.within_age_limit('2010年4月', strict=True, now=now))
        self.assertFalse(crawler.within_age_limit('', strict=True, now=now))
        self.assertTrue(crawler.is_hikari('熊本県菊池郡菊陽町光の森７'))
        self.assertFalse(crawler.is_hikari('熊本県合志市幾久富'))
        self.assertEqual(crawler.parse_region('熊本県菊池郡菊陽町大字津久礼', '光の森駅徒歩10分', ''),'菊陽町')

    def test_hikari_budget_area_exemption_and_oku_price(self):
        s=self.sample(所在地='熊本県菊池郡菊陽町光の森1',価格='2億6037万円',専有面積='55m2')
        item,_=parse_detail(s,'tatara_1','https://www.tatara-fudousan.com/property_detail/1',crawler)
        self.assertEqual(item['current_price'],260370000)
        self.assertEqual(item['building_area'],55)
        self.assertEqual(item['region'],'光之森')
        old=self.sample(所在地='熊本県菊池郡菊陽町光の森1',築年月='2000年1月')
        self.assertIsNone(parse_detail(old,'tatara_1','https://www.tatara-fudousan.com/property_detail/1',crawler)[0])

    def test_hikari_pagination_and_mixed_property_types(self):
        root='https://suumo.jp/b/kodate/kw/test/'
        html='''<a href="/b/kodate/kw/test/2/">次へ</a>
        <a href="/b/kodate/kw/other/2/">別の検索</a>
        <div class="cassette"><div class="cassette_item-header">所在地</div><div class="cassette_item-body">熊本県菊陽町光の森２</div>
        <a href="/ikkodate/kumamoto/sc_kikuchigun/nc_1/">住宅</a></div>
        <div class="cassette"><div class="cassette_item-header">所在地</div><div class="cassette_item-body">熊本県合志市幾久富</div>
        <a href="/ikkodate/kumamoto/sc_koshi/nc_2/">光の森駅</a></div>
        <a href="/ms/chuko/kumamoto/sc_kikuchigun/nc_3/">住所未取得</a>
        <a href="/tochi/kumamoto/sc_kikuchigun/nc_4/">土地</a>'''
        pairs,pages=crawler.hikari_listing_page(html,root,root)
        self.assertEqual(pages,{root+'2/'})
        self.assertEqual(set(pairs),{'1','3'})
        self.assertEqual(pairs['1'][1],'house_new')
        self.assertEqual(pairs['3'][1],'condo')

    def sample(self, **changes):
        data={'所在地':'熊本県熊本市東区長嶺南', '価格':'6,500万円', '専有面積':'135.50m 2 (壁芯)', '築年月':'2020年09月', '間取り':'4LDK'}
        data.update(changes)
        return BeautifulSoup('<title>住宅</title><table>'+''.join(f'<tr><th>{k}</th><td>{v}</td></tr>' for k,v in data.items())+'</table><table><tr><th>所在地</th><td>東京都 仲介住所</td></tr></table>', 'html.parser')
    def test_condo_without_land_and_property_address(self):
        item, _=parse_detail(self.sample(), 'tatara_1','https://www.tatara-fudousan.com/property_detail/1',crawler)
        self.assertEqual(item['land_area'],0)
        self.assertEqual(item['building_area'],135.5)
        self.assertEqual(item['region'],'熊本市東區')
    def test_budget_and_age(self):
        for kw in [{'価格':'8,000万円'},{'築年月':'1990年01月'},{'価格':'価格未定'}]:
            item,_=parse_detail(self.sample(**kw),'tatara_1','https://www.tatara-fudousan.com/property_detail/1',crawler)
            self.assertIsNone(item)
    def test_house_land_still_required(self):
        s=self.sample();s.find('th',string='専有面積').string='延床面積'
        item,_=parse_detail(s,'tatara_1','https://www.tatara-fudousan.com/property_detail/1',crawler)
        self.assertIsNone(item)
    def test_exact_url_identity(self):
        self.assertTrue(valid_detail_url('smtrc_Bkm1','https://smtrc.jp/detail/CompareDetails?propertyCode=Bkm1'))
        self.assertFalse(valid_detail_url('smtrc_Bkm1','https://smtrc.jp/detail/CompareDetails?propertyCode=Bkm2'))
        self.assertFalse(valid_detail_url('tatara_1','https://evil.test/property_detail/1'))
        self.assertTrue(valid_detail_url('suumo_123','https://suumo.jp/ms/chuko/kumamoto/sc_kumamotoshikita/nc_123/'))
    def test_messages_not_truncated(self):
        rows=[]
        for i in range(150):
            rows.append(dict(property_id=f'tatara_{i}', property_type='condo', building_area=135.5, current_price=65000000, region='熊本市東區', title='住宅'*30,land_area=0,layout='4LDK',build_year='2020年9月',url=f'https://www.tatara-fudousan.com/property_detail/{i}'))
        messages=build_messages({'checked_at':'test','sources':[]},rows)
        self.assertGreater(len(messages),5)
        self.assertTrue(all(len(m)<=4500 for m in messages))
        self.assertIn('/property_detail/149','\n'.join(messages))
    def test_empty_report_still_has_status(self):
        messages=build_messages({'checked_at':'test','sources':[dict(source='HOME’S',scope='連線測試',status='HTTP 403',discovered=0,matched=0,errors=0)]},[])
        self.assertIn('403',messages[0])
    def test_new_house_is_labeled_and_uses_land_area(self):
        row=dict(property_id='suumo_123', property_type='house_new', building_area=111.0,
                 current_price=40000000, region='菊陽町', title='新築住宅',
                 land_area=210.0, layout='4LDK', build_year='2026年9月',
                 url='https://suumo.jp/ikkodate/kumamoto/sc_kikuchigun/nc_123/')
        text='\n'.join(build_messages({'checked_at':'test','sources':[]},[row]))
        self.assertIn('新築一戶建', text)
        self.assertIn('土地 210.00㎡', text)
        self.assertIn('新築 1 筆', text)
    def test_new_house_sources_and_completion_date(self):
        self.assertEqual(sum(kind == 'house_new' for _, kind, _ in crawler.TARGET_SOURCES), 4)
        self.assertEqual(crawler.build_date('完成予定時期 2027年3月', is_new=True), '2027年3月')

if __name__=='__main__':unittest.main()
