# -*- coding: utf-8 -*-
"""PChome × momo 商品比價系統 (Streamlit)

執行方式：streamlit run price_compare_app.py
需求：Python 3.9+，套件見 requirements.txt
"""
from __future__ import annotations

import math
import re
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from curl_cffi import requests
from plotly.subplots import make_subplots

# ==================================================================
# 常數設定
# ==================================================================
PCHOME_API = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
PCHOME_ITEM_URL = "https://24h.pchome.com.tw/prod/{}"

MOMO_API = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
MOMO_HOME = "https://www.momoshop.com.tw/"
MOMO_ITEM_URL = "https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code={}"

MOMO_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}
PCHOME_HEADERS = {"Accept": "application/json, text/plain, */*"}

PLATFORMS = ["PChome", "momo"]
COLORS = {"PChome": "#2E75B6", "momo": "#E5007D"}
COLUMNS = ["平台", "頁數", "品名", "價格", "連結"]

# 品牌擷取時，略過這類促銷用的【】標籤
PROMO_WORDS = re.compile(r"送|免運|限時|現折|加碼|預購|週年|折|優惠|特價|現貨|登記|滿")


# ==================================================================
# 共用工具
# ==================================================================
def parse_price(value) -> int | None:
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


# ==================================================================
# 爬蟲：PChome
# ==================================================================
@st.cache_data(show_spinner=False, ttl=600)
def fetch_pchome(keyword: str, pages: int, _delay: float = 1.0) -> pd.DataFrame:
    session = requests.Session(impersonate="chrome")
    rows = []
    for page in range(1, pages + 1):
        resp = session.get(
            PCHOME_API,
            params={"q": keyword, "page": page, "sort": "sale/dc"},
            headers=PCHOME_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"PChome 第 {page} 頁失敗：HTTP {resp.status_code}")
        prods = resp.json().get("prods", [])
        if not prods:
            break
        for p in prods:
            pid = p.get("Id", "")
            rows.append(
                {
                    "平台": "PChome",
                    "頁數": page,
                    "品名": p.get("name", ""),
                    "價格": parse_price(p.get("price")),
                    "連結": PCHOME_ITEM_URL.format(pid) if pid else "",
                }
            )
        if page < pages:
            time.sleep(_delay)
    return pd.DataFrame(rows, columns=COLUMNS) if rows else _empty()


# ==================================================================
# 爬蟲：momo
# ==================================================================
def _momo_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword,
            "searchType": "1",
            "cateLevel": "-1",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "showType": "chessboardType",
            "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "",
                "cateLv": "-1",
                "keyword": keyword,
                "curPage": str(page),
                "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018,
            "serviceCode": "MT01",
            "addressSearchData": {},
        },
    }


@st.cache_data(show_spinner=False, ttl=600)
def fetch_momo(keyword: str, pages: int, _delay: float = 1.0) -> pd.DataFrame:
    session = requests.Session(impersonate="chrome")  # 模擬 Chrome TLS 指紋
    session.get(MOMO_HOME, headers=MOMO_HEADERS, timeout=15)  # 先取得 cookie
    rows = []
    for page in range(1, pages + 1):
        resp = session.post(
            MOMO_API, headers=MOMO_HEADERS, json=_momo_payload(keyword, page), timeout=15
        )
        if resp.status_code != 200:
            raise RuntimeError(f"momo 第 {page} 頁失敗：HTTP {resp.status_code}")
        goods = resp.json().get("rtnSearchData", {}).get("goodsInfoList", [])
        if not goods:
            break
        for p in goods:
            code = p.get("goodsCode", "")
            rows.append(
                {
                    "平台": "momo",
                    "頁數": page,
                    "品名": p.get("goodsName", ""),
                    "價格": parse_price(p.get("goodsPrice")),
                    "連結": MOMO_ITEM_URL.format(code) if code else "",
                }
            )
        if page < pages:
            time.sleep(_delay)
    return pd.DataFrame(rows, columns=COLUMNS) if rows else _empty()


FETCHERS = {"PChome": fetch_pchome, "momo": fetch_momo}


# ==================================================================
# 資料處理
# ==================================================================
def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["價格"]).copy()
    df["價格"] = df["價格"].astype(int)
    df = df[df["價格"] > 0]
    df = df.drop_duplicates(subset=["平台", "品名", "價格"]).reset_index(drop=True)
    df["品牌"] = df["品名"].apply(extract_brand)
    return df


def extract_brand(name: str) -> str:
    """優先取【品牌】標籤的第一個詞，否則取品名第一個詞；統一轉大寫以便跨平台比對。"""
    for tag in re.findall(r"[【\[](.+?)[】\]]", name):
        tag = tag.strip()
        if tag and not PROMO_WORDS.search(tag):
            return tag.split()[0].upper()[:12]
    parts = name.split()
    return parts[0].upper()[:12] if parts else "其他"


def add_price_bins(df: pd.DataFrame, n_bins: int) -> pd.DataFrame:
    """以所有平台合併後的資料切分位數區間，讓平台間可以直接比較。"""
    df = df.copy()
    if df.empty:
        df["價格區間"] = pd.Categorical([])
        return df
    if df["價格"].nunique() <= 1:
        df["價格區間"] = f"{df['價格'].iloc[0]:,}"
        return df
    try:
        binned = pd.qcut(df["價格"], q=n_bins, duplicates="drop")
    except ValueError:
        binned = pd.cut(df["價格"], bins=n_bins)
    labels = {
        iv: f"{math.floor(iv.left):,}~{math.ceil(iv.right):,}" for iv in binned.cat.categories
    }
    df["價格區間"] = binned.map(labels).astype(str)
    order = [labels[iv] for iv in binned.cat.categories]
    df["價格區間"] = pd.Categorical(df["價格區間"], categories=order, ordered=True)
    return df


def platform_stats(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("平台")["價格"]
    out = pd.DataFrame(
        {
            "商品數": g.count(),
            "平均價": g.mean().round(0),
            "中位數": g.median().round(0),
            "最低價": g.min(),
            "最高價": g.max(),
        }
    )
    return out.reindex([p for p in PLATFORMS if p in out.index]).astype(int)


def brand_compare(df: pd.DataFrame, min_count: int) -> pd.DataFrame:
    """找出兩平台都有販售的品牌，比較中位數價格。"""
    plats = [p for p in PLATFORMS if p in df["平台"].unique()]
    if len(plats) < 2:
        return pd.DataFrame()
    pivot = df.groupby(["品牌", "平台"])["價格"].agg(["median", "count"]).unstack("平台")
    keep = pd.Series(True, index=pivot.index)
    for p in plats:
        keep &= pivot[("count", p)].fillna(0) >= min_count
    pivot = pivot[keep]
    if pivot.empty:
        return pd.DataFrame()
    res = pd.DataFrame(index=pivot.index)
    for p in plats:
        res[f"{p} 中位價"] = pivot[("median", p)].round(0).astype(int)
        res[f"{p} 商品數"] = pivot[("count", p)].astype(int)
    a, b = plats
    diff = res[f"{a} 中位價"] - res[f"{b} 中位價"]
    res[f"價差({a}-{b})"] = diff
    res["較便宜平台"] = diff.apply(lambda d: b if d > 0 else (a if d < 0 else "相同"))
    res["總商品數"] = res[f"{a} 商品數"] + res[f"{b} 商品數"]
    return res.sort_values("總商品數", ascending=False).drop(columns="總商品數").reset_index()


# ==================================================================
# 圖表 (Plotly，字型由瀏覽器渲染，無需安裝中文字型)
# ==================================================================
def box_chart(df, kw, log_y):
    fig = px.box(df, x="平台", y="價格", color="平台", points="outliers",
                 color_discrete_map=COLORS, title=f"「{kw}」各平台價格分布（箱型圖）",
                 hover_data=["品名"])
    fig.update_yaxes(type="log" if log_y else "linear")
    fig.update_layout(showlegend=False)
    return fig


def hist_chart(df, kw):
    fig = px.histogram(df, x="價格", color="平台", barmode="overlay", opacity=0.6,
                       nbins=40, color_discrete_map=COLORS,
                       title=f"「{kw}」價格直方圖（疊圖）")
    fig.update_layout(yaxis_title="商品數")
    return fig


def brand_bar(cmp_df, plats):
    long = pd.concat(
        [cmp_df[["品牌", f"{p} 中位價"]].rename(columns={f"{p} 中位價": "中位價"}).assign(平台=p)
         for p in plats]
    )
    fig = px.bar(long.head(len(cmp_df) * 2), x="品牌", y="中位價", color="平台", barmode="group",
                 color_discrete_map=COLORS, title="同品牌中位價比較")
    return fig


def pie_chart(df, kw):
    plats = [p for p in PLATFORMS if p in df["平台"].unique()]
    fig = make_subplots(rows=1, cols=len(plats), specs=[[{"type": "domain"}] * len(plats)],
                        subplot_titles=plats)
    for i, p in enumerate(plats, start=1):
        cnt = df[df["平台"] == p].groupby("價格區間", observed=True).size()
        fig.add_trace(go.Pie(labels=cnt.index.astype(str), values=cnt.values, hole=0.35,
                             sort=False, name=p, textinfo="percent+label"), row=1, col=i)
    fig.update_layout(title=f"「{kw}」價格區間分布", showlegend=False)
    return fig


def line_chart(df, kw, n):
    fig = go.Figure()
    for p in PLATFORMS:
        sub = df[df["平台"] == p].head(n).reset_index(drop=True)
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub.index + 1, y=sub["價格"], mode="lines+markers", name=p,
            text=sub["品名"], line=dict(color=COLORS[p]),
            hovertemplate="%{text}<br>$%{y:,}<extra>" + p + "</extra>"))
        mean = sub["價格"].mean()
        fig.add_hline(y=mean, line_dash="dash", line_color=COLORS[p],
                      annotation_text=f"{p} 平均 {mean:,.0f}")
    fig.update_layout(title=f"「{kw}」售價（依搜尋排序）",
                      xaxis_title="商品序號", yaxis_title="價格 (元)")
    return fig


def sunburst_chart(df, kw, top_n):
    d = df.copy()
    top = d["品牌"].value_counts().head(top_n).index
    d["品牌"] = d["品牌"].where(d["品牌"].isin(top), "其他")
    d["價格區間"] = d["價格區間"].astype(str)
    grp = d.groupby(["平台", "價格區間", "品牌"]).size().reset_index(name="商品數")
    fig = px.sunburst(grp, path=["平台", "價格區間", "品牌"], values="商品數",
                      color="平台", color_discrete_map=COLORS,
                      title=f"「{kw}」平台 → 價格區間 → 品牌")
    return fig


# ==================================================================
# Streamlit 介面
# ==================================================================
st.set_page_config(page_title="PChome × momo 比價", page_icon="🛒", layout="wide")
st.title("🛒 PChome × momo 商品比價系統")

with st.sidebar:
    st.header("查詢條件")
    keyword = st.text_input("商品關鍵字", value="行動電源")
    pages = st.number_input("每個平台爬取頁數", min_value=1, max_value=20, value=2, step=1)
    chosen = st.multiselect("比價平台", PLATFORMS, default=PLATFORMS)
    delay = st.slider("每頁間隔秒數", 0.0, 5.0, 1.0, 0.5)
    run = st.button("開始比價", type="primary", use_container_width=True)

if run:
    kw_in = keyword.strip()
    if not kw_in:
        st.warning("請輸入商品關鍵字")
        st.stop()
    if not chosen:
        st.warning("請至少選擇一個平台")
        st.stop()

    frames, errors = [], {}
    with st.status(f"正在查詢「{kw_in}」…", expanded=True) as status:
        for p in chosen:
            st.write(f"⏳ 抓取 {p}（{int(pages)} 頁）")
            try:
                frames.append(FETCHERS[p](kw_in, int(pages), float(delay)))
                st.write(f"✅ {p} 完成")
            except Exception as e:  # 單一平台失敗不影響另一個平台
                errors[p] = str(e)
                st.write(f"❌ {p} 失敗：{e}")
        status.update(label="查詢完成", state="complete", expanded=False)

    frames = [f for f in frames if not f.empty]
    if not frames:
        st.session_state.pop("df", None)
        st.error("所有平台都沒有取得資料。" + ("".join(f"\n- {k}：{v}" for k, v in errors.items())))
        st.stop()
    st.session_state["df"] = clean(pd.concat(frames, ignore_index=True))
    st.session_state["kw"] = kw_in
    st.session_state["errors"] = errors

raw = st.session_state.get("df")
if raw is None:
    st.info("請在左側輸入關鍵字、頁數並選擇平台，然後按「開始比價」。")
    st.stop()

kw = st.session_state["kw"]
for p, msg in st.session_state.get("errors", {}).items():
    st.warning(f"{p} 抓取失敗，以下僅顯示其他平台資料：{msg}")

# ---------- 篩選（查詢後即時生效，不需重新爬取） ----------
with st.sidebar:
    st.divider()
    st.header("結果篩選")
    lo, hi = int(raw["價格"].min()), int(raw["價格"].max())
    price_rng = st.slider("價格範圍", lo, hi, (lo, hi)) if lo < hi else (lo, hi)
    must = st.text_input("品名必須包含（空白分隔）", "")
    excl = st.text_input("品名排除關鍵字（空白分隔）", "")
    n_bins = st.slider("價格區間數", 3, 8, 5)

df = raw[raw["價格"].between(*price_rng)]
for w in must.split():
    df = df[df["品名"].str.contains(w, case=False, regex=False)]
for w in excl.split():
    df = df[~df["品名"].str.contains(w, case=False, regex=False)]
df = df.reset_index(drop=True)

if df.empty:
    st.warning("篩選後沒有資料，請放寬條件。")
    st.stop()

df = add_price_bins(df, n_bins)
plats_present = [p for p in PLATFORMS if p in df["平台"].unique()]

# ---------- 頂部摘要 ----------
stats = platform_stats(df)
cols = st.columns(len(plats_present))
for col, p in zip(cols, plats_present):
    s = stats.loc[p]
    col.markdown(f"#### {p}")
    col.metric("商品數", f"{s['商品數']:,}")
    col.metric("平均 / 中位數", f"${s['平均價']:,} / ${s['中位數']:,}")
    col.metric("最低 ～ 最高", f"${s['最低價']:,} ～ ${s['最高價']:,}")

if len(plats_present) == 2:
    a, b = plats_present
    m_a, m_b = stats.loc[a, "中位數"], stats.loc[b, "中位數"]
    if m_a == m_b:
        st.info("兩平台中位數價格相同。")
    else:
        cheap, exp = (a, b) if m_a < m_b else (b, a)
        pct = abs(m_a - m_b) / max(m_a, m_b) * 100
        st.success(f"以中位數來看，**{cheap}** 較便宜，約低 {pct:.1f}%（{cheap} ${min(m_a, m_b):,} vs {exp} ${max(m_a, m_b):,}）")

tab1, tab2, tab3 = st.tabs(["📊 比價總覽", "📈 圖表分析", "📋 商品明細"])

# ---------- Tab1：比價總覽 ----------
with tab1:
    st.subheader("各平台價格統計")
    st.dataframe(stats, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        log_y = st.checkbox("箱型圖使用對數座標（價差大時較易閱讀）", value=False)
        st.plotly_chart(box_chart(df, kw, log_y), use_container_width=True)
    with c2:
        st.plotly_chart(hist_chart(df, kw), use_container_width=True)

    st.subheader("同品牌比價")
    if len(plats_present) < 2:
        st.info("需要同時有兩個平台的資料才能做品牌比價。")
    else:
        min_cnt = st.slider("每個平台該品牌至少要有幾件商品", 1, 10, 2)
        cmp_df = brand_compare(df, min_cnt)
        if cmp_df.empty:
            st.info("沒有符合條件的共同品牌，可調低門檻或增加爬取頁數。")
        else:
            top_cmp = cmp_df.head(15)
            st.plotly_chart(brand_bar(top_cmp, plats_present), use_container_width=True)
            st.dataframe(cmp_df, use_container_width=True, hide_index=True)
            st.caption("品牌由品名【】標籤或第一個詞擷取，屬簡易判斷，僅供參考。")

    st.subheader("各平台最低價 Top 10")
    tcols = st.columns(len(plats_present))
    for col, p in zip(tcols, plats_present):
        col.markdown(f"**{p}**")
        col.dataframe(
            df[df["平台"] == p].nsmallest(10, "價格")[["品名", "價格", "連結"]],
            use_container_width=True, hide_index=True,
            column_config={"連結": st.column_config.LinkColumn("連結", display_text="開啟")},
        )

# ---------- Tab2：圖表分析 ----------
with tab2:
    chart_type = st.radio("圖表類型", ["圓餅圖", "折線圖", "旭日圖"], horizontal=True)
    if chart_type == "圓餅圖":
        fig = pie_chart(df, kw)
    elif chart_type == "折線圖":
        max_n = int(df.groupby("平台").size().max())
        n = st.slider("每平台顯示前幾筆", 5, max_n, min(60, max_n)) if max_n > 5 else max_n
        fig = line_chart(df, kw, n)
    else:
        top_n = st.slider("保留商品數前幾名品牌", 5, 20, 10)
        fig = sunburst_chart(df, kw, top_n)
    fig.update_layout(font=dict(size=14), height=600)
    st.plotly_chart(fig, use_container_width=True)

# ---------- Tab3：商品明細 ----------
with tab3:
    view = st.radio("平台", ["全部"] + plats_present, horizontal=True)
    shown = df if view == "全部" else df[df["平台"] == view]
    shown = shown.sort_values("價格").reset_index(drop=True)
    st.dataframe(
        shown[["平台", "品名", "價格", "品牌", "價格區間", "連結"]].astype({"價格區間": str}),
        use_container_width=True, hide_index=True,
        column_config={
            "價格": st.column_config.NumberColumn("價格", format="$%d"),
            "連結": st.column_config.LinkColumn("連結", display_text="開啟"),
        },
    )
    csv = shown.astype({"價格區間": str}).to_csv(index=False).encode("utf-8-sig")
    st.download_button("下載 CSV", csv, mime="text/csv",
                       file_name=f"比價_{kw}_{time.strftime('%Y%m%d')}.csv")
