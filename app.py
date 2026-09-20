import json
import sqlite3
import pandas as pd
import streamlit as st

st.set_page_config(page_title="熊本住宅與高級大樓情報看板", layout="wide", page_icon="🏡")

def load_data():
    conn = sqlite3.connect("kumamoto_properties.db")
    try:
        # 預設僅載入 status 為 active 的有效物件
        df = pd.read_sql_query("SELECT * FROM properties", conn)
    except Exception:
        df = pd.DataFrame()
    history_df = pd.read_sql_query("SELECT * FROM price_history ORDER BY recorded_at DESC", conn)
    conn.close()

    if df.empty:
        return df, history_df

    if "property_type" not in df.columns:
        df["property_type"] = "house"

    df["price_man"] = (df["current_price"] / 10_000).astype(int)
    df["price_display"] = df["price_man"].apply(lambda value: "價格未定" if value == 0 else f"{value:,} 萬円")
    df["land_ping"] = (df["land_area"] * 0.3025).round(1)
    df["bldg_ping"] = (df["building_area"] * 0.3025).round(1)

    df["type_label"] = df["property_type"].map({
        "house": "一戶建", "condo": "大樓候選", "condo_new": "新築建案候選"
    }).fillna("住宅")
    df["land_spec"] = df.apply(
        lambda r: f"{r['land_area']} ㎡ ({r['land_ping']} 坪)" if r["property_type"] == "house" else "—",
        axis=1,
    )
    df["bldg_spec"] = df.apply(lambda r: f"{r['building_area']} ㎡ ({r['bldg_ping']} 坪)", axis=1)

    def get_tags(row):
        tags = []
        if row["property_type"] == "condo_new":
            tags.append("✨ 新築")
        elif row["property_type"] == "condo":
            tags.append("🏙️ 市區大樓")
        if row["property_type"] != "house" and row["bldg_ping"] >= 40:
            tags.append("⭐ 專有面積約40坪以上優先")
        if row["price_man"] >= 6000:
            tags.append("👑 豪邸級")
        if row["property_type"] == "house" and row["land_ping"] >= 70:
            tags.append("🌳 大地坪(>70坪)")
        if any(k in str(row["title"]) for k in ["中庭", "コートハウス", "積水", "ダイワ", "邸宅", "平屋"]):
            tags.append("🛡️ 豪邸/平屋/名門")
        return " ".join(tags) if tags else "優質大宅"

    df["tags"] = df.apply(get_tags, axis=1)
    return df, history_df

df, history_df = load_data()

with sqlite3.connect("kumamoto_properties.db") as conn:
    try:
        report_row = conn.execute("SELECT payload FROM run_report WHERE id=1").fetchone()
    except sqlite3.OperationalError:
        report_row = None
if report_row:
    report = json.loads(report_row[0])
    st.caption("最近抓取：" + report["checked_at"] + "；數量為刊登筆數，跨站可能重複")
    with st.expander("各網站搜尋狀態", expanded=True):
        st.dataframe(pd.DataFrame(report["sources"]).rename(columns={
            "source":"來源", "scope":"搜尋範圍", "status":"狀態", "discovered":"詳細頁",
            "matched":"候選", "errors":"讀取錯誤", "unreadable":"無法解析／非公開"}), hide_index=True)


st.title("🏡 熊本 JASM 生活圈住宅與高級大樓情報看板")
st.caption("一戶建：≤約7,299萬円（台幣1,500萬；換算匯率0.2055）、土地≥200㎡、建物≥100㎡｜高級大樓：不限制土地、專有面積約40坪優先｜全部屋齡15年內（含新築）")

if df.empty:
    st.warning("⚠️ 目前資料庫尚無資料，請先在終端機執行 python3 crawler.py 抓取！")
    st.stop()

# 側邊欄篩選
st.sidebar.header("📍 區域與狀態篩選")
include_inactive = st.sidebar.checkbox("顯示已下架／失效物件", value=False)

if not include_inactive and "status" in df.columns:
    df = df[df["status"] == "active"]

all_regions = ["全部區域"] + sorted(list(df["region"].dropna().unique()))
selected_region = st.sidebar.selectbox("選擇主要區域", all_regions)
type_options = ["全部類型", "一戶建", "大樓候選", "新築建案候選"]
selected_type = st.sidebar.selectbox("選擇住宅類型", type_options)

st.sidebar.header("💰 預算與坪數篩選")
min_p = int(df["price_man"].min()) if not df.empty else 0
max_p = int(df["price_man"].max()) if not df.empty else 10000
if min_p >= max_p:
    max_p = min_p + 1000
selected_price = st.sidebar.slider("售價範圍 (萬日圓)", min_p, max_p, (min_p, max_p), step=100)

min_ping = float(df["bldg_ping"].min()) if not df.empty else 0.0
max_ping = float(df["bldg_ping"].max()) if not df.empty else 100.0
if min_ping >= max_ping:
    max_ping = min_ping + 10.0
selected_ping = st.sidebar.slider("建物／專有面積 (坪)", min_ping, max_ping, (min_ping, max_ping), step=1.0)

filtered = df[
    (df["price_man"] >= selected_price[0]) &
    (df["price_man"] <= selected_price[1]) &
    (df["bldg_ping"] >= selected_ping[0]) &
    (df["bldg_ping"] <= selected_ping[1])
]

if selected_region != "全部區域":
    filtered = filtered[filtered["region"] == selected_region]
if selected_type != "全部類型":
    filtered = filtered[filtered["type_label"] == selected_type]

# 關鍵指標
c1, c2, c3, c4 = st.columns(4)
c1.metric("在架有效物件數", f"{len(filtered)} 件")
priced = filtered[filtered["price_man"] > 0]
c2.metric("已定價物件平均總價", f"{priced['price_man'].mean():.0f} 萬円" if len(priced) else "價格未定")
c3.metric("平均建物／專有面積", f"{filtered['bldg_ping'].mean():.1f} 坪" if len(filtered) else "0")
c4.metric("目前分區", selected_region)

st.markdown("---")

tab1, tab2 = st.tabs(["📋 物件一覽表", "📉 降價歷史追蹤"])

with tab1:
    st.subheader(f"🏠 【{selected_region}】有效在架住宅一覽")
    show_cols = [
        "type_label", "region", "title", "price_display", "land_spec", "bldg_spec", "layout", "build_year", "address", "tags", "url"
    ]
    st.dataframe(
        filtered[show_cols].rename(columns={
            "type_label": "住宅類型",
            "region": "區域分類",
            "title": "物件名稱",
            "price_display": "售價",
            "land_spec": "土地面積 (㎡/坪)",
            "bldg_spec": "建物面積 (㎡/坪)",
            "layout": "格局",
            "build_year": "完工時期",
            "address": "詳細地址",
            "tags": "特色標籤",
            "url": "售屋網連結"
        }),
        column_config={"售屋網連結": st.column_config.LinkColumn("前往查看")},
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
