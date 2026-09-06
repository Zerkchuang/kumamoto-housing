import sqlite3
import pandas as pd
import streamlit as st

st.set_page_config(page_title="熊本 JASM 頂級住宅與豪邸情報看板", layout="wide", page_icon="🏡")

def load_data():
    conn = sqlite3.connect("kumamoto_properties.db")
    try:
        df = pd.read_sql_query("SELECT * FROM properties", conn)
    except Exception:
        df = pd.DataFrame()
    history_df = pd.read_sql_query("SELECT * FROM price_history ORDER BY recorded_at DESC", conn)
    conn.close()

    if df.empty:
        return df, history_df

    df["price_man"] = (df["current_price"] / 10_000).astype(int)
    df["land_ping"] = (df["land_area"] * 0.3025).round(1)
    df["bldg_ping"] = (df["building_area"] * 0.3025).round(1)

    df["land_spec"] = df.apply(lambda r: f"{r['land_area']} ㎡ ({r['land_ping']} 坪)", axis=1)
    df["bldg_spec"] = df.apply(lambda r: f"{r['building_area']} ㎡ ({r['bldg_ping']} 坪)", axis=1)

    def get_tags(row):
        tags = []
        if row["price_man"] >= 6000:
            tags.append("👑 豪邸級")
        if row["land_ping"] >= 70:
            tags.append("🌳 大地坪(>70坪)")
        if any(k in str(row["title"]) for k in ["中庭", "コートハウス", "積水", "ダイワ", "邸宅", "平屋"]):
            tags.append("🛡️ 豪邸/平屋/名門")
        return " ".join(tags) if tags else "優質大宅"

    df["tags"] = df.apply(get_tags, axis=1)
    return df, history_df

df, history_df = load_data()

st.title("🏡 熊本 JASM 生活圈住宅與豪邸情報看板")
st.caption("篩選條件：地坪 ≥ 200㎡（約60.5坪以上）｜ 建坪 ≥ 95㎡ ｜ 3LDK以上 ｜ 屋齡10年內最新釋出")

if df.empty:
    st.warning("⚠️ 目前資料庫尚無資料，請先在終端機執行 python3 crawler.py 抓取！")
    st.stop()

# 側邊欄篩選
st.sidebar.header("📍 區域分類篩選")
all_regions = ["全部區域"] + sorted(list(df["region"].dropna().unique()))
selected_region = st.sidebar.selectbox("選擇主要區域", all_regions)

st.sidebar.header("💰 預算與坪數篩選")
min_p = int(df["price_man"].min())
max_p = int(df["price_man"].max())
if min_p >= max_p:
    max_p = min_p + 1000
selected_price = st.sidebar.slider("售價範圍 (萬日圓)", min_p, max_p, (min_p, max_p), step=100)

min_ping = float(df["land_ping"].min())
max_ping = float(df["land_ping"].max())
if min_ping >= max_ping:
    max_ping = min_ping + 10.0
selected_ping = st.sidebar.slider("土地坪數 (坪)", min_ping, max_ping, (min_ping, max_ping), step=2.0)

filtered = df[
    (df["price_man"] >= selected_price[0]) &
    (df["price_man"] <= selected_price[1]) &
    (df["land_ping"] >= selected_ping[0]) &
    (df["land_ping"] <= selected_ping[1])
]

if selected_region != "全部區域":
    filtered = filtered[filtered["region"] == selected_region]

# 關鍵指標
c1, c2, c3, c4 = st.columns(4)
c1.metric("符合規格物件數", f"{len(filtered)} 件")
c2.metric("平均總價", f"{filtered['price_man'].mean():.0f} 萬円" if len(filtered) else "0")
c3.metric("平均土地面積", f"{filtered['land_ping'].mean():.1f} 坪" if len(filtered) else "0")
c4.metric("目前分區", selected_region)

st.markdown("---")

tab1, tab2, tab3 = st.tabs(["📋 物件一覽表", "📉 降價歷史追蹤", "📍 JASM 通勤動線分析"])

with tab1:
    st.subheader(f"🏠 【{selected_region}】大坪數優質住宅一覽")
    show_cols = [
        "region", "title", "price_man", "land_spec", "bldg_spec", "layout", "build_year", "address", "tags", "url"
    ]
    st.dataframe(
        filtered[show_cols].rename(columns={
            "region": "區域分類",
            "title": "物件名稱",
            "price_man": "售價 (萬円)",
            "land_spec": "土地面積 (㎡/坪)",
            "bldg_spec": "建物面積 (㎡/坪)",
            "layout": "格局",
            "build_year": "完工時期",
            "address": "詳細地址",
            "tags": "特色標籤",
            "url": "SUUMO 連結"
        }),
        column_config={"SUUMO 連結": st.column_config.LinkColumn("點此開啟原網頁")},
        use_container_width=True,
        hide_index=True
    )

with tab2:
    st.subheader("📉 最新降價履歷")
    if not history_df.empty:
        m_hist = pd.merge(history_df, df[["property_id", "title", "region", "url"]], on="property_id", how="inner")
        m_hist["price_man"] = (m_hist["price"] / 10_000).astype(int)
        st.dataframe(
            m_hist[["recorded_at", "region", "title", "price_man", "url"]].rename(columns={
                "recorded_at": "記錄日期",
                "region": "分區",
                "title": "物件名稱",
                "price_man": "異動後價格 (萬円)",
                "url": "網址"
            }),
            column_config={"網址": st.column_config.LinkColumn("前往查看")},
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("目前尚未有降價異動。")

with tab3:
    st.subheader("📍 各分區通勤 JASM 特性")
    st.markdown("""
    * **光之森周邊 (車程約 12～15 分)**：生活機能最強（youme Town 核心商圈），轉手流動性第一。
    * **菊陽町 (車程約 8～12 分)**：通勤 TSMC 廠區最近，大土地好規劃高圍牆。
    * **合志市 (車程約 12～15 分)**：新興高質感住宅區，多高級注文住宅與中庭極簡宅。
    * **熊本市北區 (車程約 15～20 分)**：高台擁壁別墅多，天然隱私好。
    * **熊本市東區 (車程約 20～25 分)**：近市中心生活圈與熊本 IC 交流道。
    """)
