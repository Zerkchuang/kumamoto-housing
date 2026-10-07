import unittest
from bs4 import BeautifulSoup
import crawler
from mirai_source import identity, parse_listing, pending_blocks
from extra_sources import valid_detail_url
from line_notify import build_messages


class MiraiTest(unittest.TestCase):
    url = 'https://kumamoto-mirai.tw/our-property-info/ours/1472/'

    def sample(self, extra='', title='【現售透天厝】合志市住宅'):
        return BeautifulSoup('<nav class="post_content">價格：1億元</nav><article><h1>' + title + '</h1><time>2026年8月25日</time><div class="post_content">'
          '<p>地點：合志市合生<br>土地面積：202.13㎡<br>建物面積：140.35㎡<br>' + extra + '</p></div></article>', 'html.parser')

    def test_exact_identity(self):
        self.assertEqual(identity(self.url), 'mirai_ours_1472')
        self.assertTrue(valid_detail_url('mirai_ours_1472', self.url))
        self.assertFalse(valid_detail_url('mirai_ours_1', self.url))
        self.assertFalse(identity('https://evil.test/our-property-info/ours/1472/'))

    def test_missing_price_age_pending_not_publication_date(self):
        item, pending, result = parse_listing(self.sample('金額：請洽詢'), self.url, crawler)
        self.assertIsNone(item)
        self.assertEqual(result, 'pending')
        self.assertEqual(pending['missing_fields'], ['售價', '完工年月／屋齡'])
        self.assertEqual(pending['current_price'], 0)
        self.assertEqual(pending['build_year'], '')

    def test_verified_price_area_and_age(self):
        item, pending, result = parse_listing(self.sample('金額：4500萬日幣<br>完工年月：2025年4月'), self.url, crawler)
        self.assertEqual(result, 'matched')
        self.assertIsNone(pending)
        self.assertEqual(item['current_price'], 45000000)
        self.assertEqual(item['land_area'], 202.13)

    def test_sold_land_old_and_budget_are_excluded(self):
        for title in ['【已完售】合志市住宅', '【現售土地】合志市住宅用地', '【非公開】合志市住宅']:
            self.assertEqual(parse_listing(self.sample(title=title),self.url,crawler)[2], 'filtered')
        for extra in ['金額：8000萬日幣', '完工年月：2000年4月']:
            self.assertEqual(parse_listing(self.sample(extra),self.url,crawler)[2], 'filtered')

    def test_pending_is_labeled_and_all_line_links_survive(self):
        _, row, _ = parse_listing(self.sample('金額：請洽詢'), self.url, crawler)
        report = {'checked_at':'test', 'sources':[dict(source='熊本未來',scope='test',status='完成',discovered=1,matched=0,errors=0,pending_listings=[row])]}
        message = '\n'.join(build_messages(report, []))
        self.assertIn(self.url, message)
        self.assertIn('尚未確認符合條件', message)
        row['url'] = 'https://evil.test/'
        self.assertFalse(pending_blocks(report))
