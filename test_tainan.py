import copy
import json
import unittest
from tainan import CONFIG, qualify, elevator, sf_record, parse_detail, messages, discover


class TainanTests(unittest.TestCase):
    def row(self):
        return dict(title='已裝電梯透天',district='安南區',type='透天別墅',price_twd=40000000,
                    building_ping=100,land_ping=40,age_years=20,elevator=elevator('已裝電梯透天'))

    def test_thresholds(self):
        self.assertEqual(qualify(self.row())[0],'刊登符合')
        for k,v in [('price_twd',40000001),('building_ping',99.99),('land_ping',39.99),('district','東區'),('age_years',20.1)]:
            r=self.row();r[k]=v;self.assertEqual(qualify(r)[0],'排除')

    def test_unknown_is_not_match(self):
        for k in ['price_twd','building_ping','land_ping','age_years']:
            r=self.row();r[k]=None;self.assertEqual(qualify(r)[0],'待核對')

    def test_reserved_elevator(self):
        for text in ['預留電梯空間','可加裝電梯','電梯未安裝','沒有電梯','無電梯']:
            r=self.row();r['elevator']=elevator(text);self.assertEqual(qualify(r)[0],'排除')
        r=self.row();r['elevator']=elevator('無說明');self.assertEqual(qualify(r)[0],'待核對')

    def test_sf_units_and_identity(self):
        d=dict(object_info_id=123,commission_model='售案',case_name='電梯透天',area={'name':'北區'},
               obj_type={'class_name':'透天別墅'},outer_address='台南市北區文賢路',co_price=3980,t_floor=105,r_floor=42,m_building=95)
        r=sf_record(d,'https://www.singfujia.com/buy/detail/123')
        self.assertEqual(r['price_twd'],39800000);self.assertEqual(r['land_ping'],42)
        with self.assertRaises(ValueError):sf_record(d,'https://www.singfujia.com/buy/detail/999')
        d['p_price_hide']=1;self.assertIsNone(sf_record(d,'https://www.singfujia.com/buy/detail/123')['price_twd'])

    def test_5168_price_is_not_monthly_or_unit(self):
        html='''<h1>安南電梯透天</h1><main>地址 台南市安南區安和路 型態 透天厝 屋齡 0年 房貸參考 40,935 /月 單價 35 萬
          <span class="text-c-orange-700 text-4xl">3,980</span> 萬 建物 105 坪 土地 45 坪</main>'''
        r=parse_detail('5168',html,'https://buy.houseprice.tw/house/123_456/')
        self.assertEqual(r['price_twd'],39800000);self.assertEqual(r['status'],'刊登符合')
        r=parse_detail('5168',html.replace('class="text-c-orange-700 text-4xl"',''), 'https://buy.houseprice.tw/house/123_456/')
        self.assertIsNone(r['price_twd']);self.assertEqual(r['status'],'待核對')

    def test_stale_yc_identity(self):
        with self.assertRaises(ValueError):
            parse_detail('永慶','<h1>台北大樓</h1>','https://buy.yungching.com.tw/house/123','台南電梯透天')

    def test_discovery_rejects_external_urls(self):
        html='<a href="https://evil.example/house/123_456/">bad</a><a href="/house/123_456/">ok</a>'
        self.assertEqual(list(discover('5168',html,'https://buy.houseprice.tw/list/')),['https://buy.houseprice.tw/house/123_456/'])

    def test_message_empty_and_unreadable(self):
        report=dict(checked_at='2026-11-20',listings=[],pending=[],sources=[dict(source='591',status='讀取失敗',parsed=0,errors=['403'])])
        text='\n'.join(messages(report))
        self.assertIn('台南電梯透天',text);self.assertIn('讀取失敗',text);self.assertIn('未讀取不代表沒有房源',text)
        self.assertNotIn('萬円',text);self.assertTrue(all(len(x)<=4500 for x in messages(report)))

    def test_schedule_and_priorities(self):
        self.assertEqual(CONFIG['schedule_cron_utc'],'0 0 * * 6')
        self.assertEqual(CONFIG['priority'],['採買','交通','醫療'])
        self.assertIsNone(CONFIG['max_driving_distance_km'])
        self.assertIn('附近有公園',CONFIG['preferences'])

if __name__=='__main__':unittest.main()
