import base64
import hashlib
import hmac
import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import requests
from bs4 import BeautifulSoup

import crawler
import line_notify
from inventory import load_inventory, verified_history
from webhook_guard import allow_gpt


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + '/inventory.db'
        self.env = patch.dict(os.environ, {'LINE_CHANNEL_ACCESS_TOKEN':'test-token',
            'LINE_CHANNEL_SECRET':'test-secret', 'WEBHOOK_STATE_DB':self.tmp.name+'/guard.db'})
        self.env.start()
        self.webhook = importlib.import_module('line_webhook')
        self.dbpatch = patch.object(crawler, 'DB_NAME', self.path)
        self.dbpatch.start()
        crawler.init_db()

    def tearDown(self):
        self.dbpatch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def item(self, **changes):
        row=dict(property_id='suumo_123',title='住宅',url='https://suumo.jp/ms/chuko/kumamoto/sc_kikuchigun/nc_123/',
            region='光之森',address='熊本県菊池郡菊陽町光の森1',current_price=80000000,land_area=0,
            building_area=90,layout='3LDK',build_year='2020年9月',property_type='condo',price_basis=crawler.PRICE_BASIS)
        row.update(changes)
        return row

    def report(self, ids=('suumo_123',), **changes):
        value=dict(checked_at=datetime.now(timezone.utc).isoformat(),property_ids=list(ids),sources=[],
                   count=len(ids),price_parser_version=crawler.PRICE_BASIS)
        value.update(changes)
        return value

    def test_sale_field_not_gift_or_monthly_payment(self):
        soup=BeautifulSoup('<h1>来場特典1万円 月々5万円 合志市野々島</h1><table><tr><th>価格 ヒント</th>'
                           '<td>1998万円 支払シミュレーション</td></tr></table>','html.parser')
        self.assertEqual(crawler.advertised_price(soup),19980000)
        self.assertEqual(crawler.parse_price('5000万円 参考：1億円'),50000000)
        self.assertEqual(crawler.parse_price('2億1800万円'),218000000)
        with self.assertRaises(ValueError):
            crawler.advertised_price(BeautifulSoup('<h1>月々6万円のみ</h1>','html.parser'))

    def test_full_detail_split_units_and_date_hints(self):
        html = '<h1>特典1万円</h1><table>' + ''.join(
            '<tr><th>'+label+'</th><td>'+value+'</td></tr>' for label,value in [
                ('価格 ヒント','1998万円'),('所在地','熊本県合志市野々島'),
                ('土地面積 ヒント','212.18m <sup>2</sup>（64.18坪）'),
                ('建物面積 ヒント','112.61m <sup>2</sup>（34.06坪）'),
                ('完成時期（築年月） ヒント','2026年3月')]) + '</table>'
        url='https://suumo.jp/ikkodate/kumamoto/sc_koshi/nc_21213040/'
        with patch('crawler.requests.get',return_value=Mock(url=url,text=html)):
            row=crawler.detail('21213040',url,'合志市','house_new')
        self.assertEqual(row['current_price'],19980000)
        self.assertEqual(row['land_area'],212.18)
        self.assertEqual(row['building_area'],112.61)
        self.assertEqual(row['build_year'],'2026年3月')
        soup=BeautifulSoup('<dl><dt>専有面積</dt><dd>56.05m <sup>2</sup>～70.34m <sup>2</sup>、倉庫0.85m2を含む</dd></dl>','html.parser')
        self.assertEqual(crawler.field_area(soup,'専有面積',largest=True),70.34)

    def test_redirect_to_another_property_rejected(self):
        response=Mock(url='https://suumo.jp/ms/chuko/kumamoto/sc_kikuchigun/nc_456/', text='123')
        with patch('crawler.requests.get',return_value=response),self.assertRaises(ValueError):
            crawler.detail('123',self.item()['url'],'光之森','condo')

    def test_rebaseline_is_not_price_change_and_empty_run_clears_current(self):
        old=self.item(current_price=90000,price_basis='legacy_unverified')
        crawler.save([old],self.report())
        crawler.save([self.item()],self.report())
        history=verified_history(self.path)
        self.assertEqual(len(history),1)
        self.assertEqual(history[0]['event_type'],'rebaseline')
        self.assertEqual(len(load_inventory(self.path)[0]),1)
        crawler.save([],self.report(ids=()))
        self.assertEqual(load_inventory(self.path)[0],[])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM properties').fetchone()[0],'unverified')

    def test_stale_and_legacy_inventory_fail_closed(self):
        crawler.save([self.item()],self.report(checked_at=(datetime.now(timezone.utc)-timedelta(hours=49)).isoformat()))
        with self.assertRaises(ValueError):load_inventory(self.path)
        crawler.save([self.item()],self.report(price_parser_version='legacy'))
        with self.assertRaises(ValueError):load_inventory(self.path)

    def test_chatbot_includes_verified_condo_without_old_land_budget_filter(self):
        crawler.save([self.item()],self.report())
        with patch.object(self.webhook,'DB',self.path):
            result=self.webhook.homes(region='光之森')
        self.assertIn('8,000萬円',result)
        self.assertIn('共1筆',result)

    def signed(self, raw):
        sig=base64.b64encode(hmac.new(self.webhook.SECRET.encode(),raw,hashlib.sha256).digest()).decode()
        return {'Content-Type':'application/json','X-Line-Signature':sig}

    def test_webhook_signature_json_and_replay(self):
        client=self.webhook.app.test_client()
        self.assertEqual(client.post('/webhook',data=b'{}').status_code,400)
        bad=b'[]'
        self.assertEqual(client.post('/webhook',data=bad,headers=self.signed(bad)).status_code,400)
        event={'webhookEventId':'test-event','type':'message','replyToken':'test-reply',
               'source':{'type':'user','userId':'test-user'},'message':{'type':'text','text':'hello'}}
        raw=json.dumps({'events':[event]}).encode()
        with patch.object(self.webhook,'ask_gpt',return_value='test') as ask,patch.object(self.webhook,'reply') as reply:
            self.assertEqual(client.post('/webhook',data=raw,headers=self.signed(raw)).status_code,200)
            self.assertEqual(client.post('/webhook',data=raw,headers=self.signed(raw)).status_code,200)
            ask.assert_called_once()
            reply.assert_called_once()

    def test_gpt_rate_limit_is_per_sender(self):
        self.assertTrue(all(allow_gpt('a') for _ in range(6)))
        self.assertFalse(allow_gpt('a'))
        self.assertTrue(allow_gpt('b'))

    def test_line_retry_uses_same_key_and_accepts_already_sent(self):
        accepted=Mock(status_code=409,headers={'x-line-accepted-request-id':'test-request'})
        with patch.object(line_notify,'resolve_target',return_value='test-target'),patch('line_notify.time.sleep'),\
             patch('line_notify.requests.post',side_effect=[requests.Timeout(),accepted]) as post:
            line_notify.push_line(['test'])
        self.assertEqual(post.call_count,2)
        keys=[c.kwargs['headers']['X-Line-Retry-Key'] for c in post.call_args_list]
        self.assertEqual(keys[0],keys[1])


if __name__=='__main__':unittest.main()
