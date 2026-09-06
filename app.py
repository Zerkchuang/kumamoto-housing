import sqlite3
import pandas as pd
import streamlit as st

st.set_page_config(page_title="熊本 JASM 購屋與豪邸情報中心", layout="wide", page_icon="🏡")

# 資料庫連線與資料讀取
def load_data():
    conn = sqlite3.connect("kumamoto_properties.db")
    df = pd.read_sql_query("SELECT * FROM properties", conn)
    history_df = pd.read_sql_query("SELECT * FROM price_history ORDER BY recorded_at DESC", conn)
    conn.close()

    if df.empty:
        return df, history_df

    # 單位轉換與計算
    df["price_man"] = df["current_price"] / 10_000
    df["land_ping"] = (df["land_area"] * 0.3025).round(1)
    df["bldg_ping"] = (df["building_area"] * 0.3025).round(1)
    df["ping_price"] = (df["price_man"] / df["land_ping"]).round(1)

    # 自動打上特色標籤（高圍牆 / 豪宅 / 大土地）
    def get_tags(row):
        tags = []
        if row["price_man"] >= 6000:
            tags.append("👑 豪邸級")
        if row["land_ping"] >= 70:
            tags.append("🌳 大地坪(>70坪)")
        if any(k in str(row["title"]) for k in ["中庭", "コートハウス", "積水", "ダイワ", "邸宅"]):
            tags.append("🛡️ 高隱私/名門")
        return " ".join(tags) if tags else "一般住宅"

    df["tags"] = df.apply(get_tags, axis=1)
    return df, history_df

df, history_df = load_data()

# 標題與簡介
st.title("🏡 熊本・JASM 通勤圈住宅與豪邸情報看板")
st.caption("即時監測 SUUMO 物件、價格異動與降價紀錄｜通勤核心：菊陽町、合志市、光之森、大津町")

if df.empty:
    st.warning("目前資料庫尚無物件，請先執行 crawler.py 抓取資料！")
    st.stop()

# 側邊欄篩選器
st.sidebar.header("🔍 物件篩選條件")
min_p, max_p = int(df["price_man"].min()), int(df["price_man"].max())
selected_price = st.sidebar.slider("售價範圍 (萬日圓)", min_p, max_p, (min_p, max_p), step=100)

min_ping, max_ping = float(df["land_ping"].min()), float(df["land_ping"].max())
selected_ping = st.sidebar.slider("土地坪數 (坪)", min_ping, max_ping, (min_ping, max_ping), step=5.0)

only_luxury = st.sidebar.checkbox("只看豪邸／大土地／高隱私物件")

# 資料過濾
filtered_df = df[
    (df["price_man"] >= selected_price[0]) &
    (df["price_man"] <= selected_price[1]) &
    (df["land_ping"] >= selected_ping[0]) &
    (df["land_ping"] <= selected_ping[1])
]

if only_luxury:
    filtered_df = filtered_df[filtered_df["tags"] != "一般住宅"]

# 關鍵數據指標 (KPI)
kpi1, kpi2, kpi3, kpi4 = st.columns(4)
kpi1.metric("符合條件物件", f"{len(filtered_df)} 件")
kpi2.metric("平均總價", f"{filtered_df['price_man'].mean():.0f} 萬円" if len(filtered_df) > 0 else "0")
kpi3.metric("平均土地坪數", f"{filtered_df['land_ping'].mean():.1f} 坪" if len(filtered_df) > 0 else "0")
kpi4.metric("監控總物件數", f"{len(df)} 件")

st.markdown("---")

# 分頁展示
tab1, tab2, tab3 = st.tabs(["📋 物件列表", "📉 降價歷史追蹤", "ℹ️ JASM 通勤與生活地圖"])

with tab1:
    st.subheader("符合篩選條件的物件")
    display_cols = [
        "title", "price_man", "land_ping", "bldg_ping", "layout", "address", "tags", "url"
    ]
    st.dataframe(
        filtered_df[display_cols].rename(columns={
            "title": "物件名稱",
            "price_man": "售價 (萬円)",
            "land_ping": "土地 (坪)",
            "bldg_ping": "建物 (坪)",
            "layout": "格局",
            "address": "所在地",
            "tags": "特色標籤",
            "url": "原始連結"
        }),
        column_config={"原始連結": st.column_config.LinkColumn("房屋連結")},
        use_container_width=True,
        hide_index=True
    )

with tab2:
    st.subheader("降價異動記錄")
    if not history_df.empty:
        merged_history = pd.merge(history_df, df[["property_id", "title", "url"]], on="property_id", how="left")
        merged_history["price_man"] = (merged_history["price"] / 10_000).astype(int)
        st.dataframe(
            merged_history[["recorded_at", "title", "price_man", "url"]].rename(columns={
                "recorded_at": "記錄日期",
                "title": "物件名稱",
                "price_man": "異動後價格 (萬円)",
                "url": "連結"
            }),
            column_config={"連結": st.column_config.LinkColumn("查看物件")},
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("目前尚未有降價變更紀錄。")

with tab3:
    st.subheader("JASM 廠區與生活圈概覽")
    st.write("JASM 廠址：熊本縣菊池郡菊陽町原水 4106-1")
    # 建立以 JASM 為中心的地圖 (經緯度: 32.8753, 130.8350)
    map_data = pd.DataFrame({
        'lat': [32.8753, 32.8601, 32.8805, 32.8770],
        'lon': [130.8350, 130.7925, 130.8650, 130.7650],
        'name': ['JASM 廠區 (TSMC)', '光之森商圈 (youme Town)', '肥後大津站生活圈', '合志市幾久富']
    })
    st.map(map_data)
