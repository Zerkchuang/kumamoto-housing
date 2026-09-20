import unittest
from bs4 import BeautifulSoup
import crawler
from extra_sources import parse_detail, valid_detail_url, discover
from line_notify import build_messages

class SourcesTest(unittest.TestCase):
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

if __name__=='__main__':unittest.main()
