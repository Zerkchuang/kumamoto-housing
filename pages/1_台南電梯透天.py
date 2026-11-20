import json
from pathlib import Path
import streamlit as st

st.set_page_config(page_title='台南電梯透天',page_icon='🏡',layout='wide')
st.title('台南電梯透天')
st.caption('自住｜安南、北區、安平、中西｜總價≤4,000萬｜建坪≥100坪｜地坪≥40坪｜屋齡≤20年｜已有電梯')
st.caption('採買→交通→醫療；附近公園加分；不設五公里限制。每週六台灣時間08:00排程，實際執行可能延遲。')
path=Path(__file__).resolve().parents[1]/'tainan_report.json'
if not path.exists():
    st.info('台南資料尚未產生；請先執行台南搜尋工作流程。')
    st.stop()
try:
    report=json.loads(path.read_text())
except (ValueError,OSError):
    st.error('台南報告無法讀取，請重新執行。');st.stop()
st.caption('本輪核對：'+report['checked_at']+'；跨站可能重複，未讀取不代表沒有房源。')
st.dataframe([{k:v for k,v in s.items() if k!='errors'} for s in report['sources']],hide_index=True)
for status,rows in [('刊登符合',report['listings']),('待核對',report['pending'])]:
    st.subheader(f'{status}｜{len(rows)}筆')
    if not rows:st.write('本輪沒有取得此類候選；不代表市場沒有。')
    for r in rows:
        with st.expander(r['title']+'｜'+r['source']):
            st.write(r['address'])
            price=f"{r['price_twd']/10000:,.0f}萬台幣" if r['price_twd'] else '價格待確認'
            st.write(f"{price}｜建坪 {r['building_ping']}｜地坪 {r['land_ping']}｜主建物 {r['main_ping']}")
            st.write('電梯：'+r['elevator'])
            for k in ['採買','交通','醫療','公園']:
                st.write(k+'：'+('；'.join(r['amenities'][k]) or '待確認'))
            st.caption('周邊為仲介刊登線索，未獨立驗證距離、車程或最近設施。')
            if r['reasons']:st.write('缺項：'+'、'.join(r['reasons']))
            st.link_button('原始房源',r['url'])
with st.expander('來源錯誤與涵蓋限制'):
    for s in report['sources']:
        st.write(s['source']+'：'+s['scope'])
        for e in s['errors']:st.write(e)
