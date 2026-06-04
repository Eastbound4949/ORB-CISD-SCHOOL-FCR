"""
app.py — Streamlit dashboard. Shows P&L, equity curve, metrics per bot.
Run: streamlit run app.py
"""
import streamlit as st
import pandas as pd
import numpy as np
import sqlite3
import os
from datetime import datetime

st.set_page_config(page_title="Multi-Strategy Bot", layout="wide", page_icon="📊")

DB_PATH = os.path.join("data", "trades.db")
INITIAL = float(os.getenv("ACCOUNT_SIZE", "1000"))
STRATEGIES = {
    "ORB":    {"label": "V04 ORB 15-min (SPY)",         "color": "#2196F3"},
    "CISD":   {"label": "V07 CISD Sweep (SPY)",          "color": "#4CAF50"},
    "SCHOOL": {"label": "V23 School Run v2.0 (XAUUSD)", "color": "#FF9800"},
    "FCR":    {"label": "V18 FCR 9:30 v2.0 (SPY)",      "color": "#9C27B0"},
}


@st.cache_data(ttl=30)
def load_trades(strategy: str = None) -> pd.DataFrame:
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    q = "SELECT * FROM trades"
    params = ()
    if strategy:
        q += " WHERE strategy=?"; params = (strategy,)
    q += " ORDER BY entry_time"
    try:
        df = pd.read_sql_query(q, conn, params=params)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


def calc_metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"trades": 0, "wr_%": 0, "cagr_%": 0, "max_dd_%": 0,
                "pf": 0, "net_$": 0, "balance": INITIAL}
    n    = len(df)
    wins = (df["pnl_dollar"] > 0).sum()
    eq   = df["pnl_dollar"].cumsum() + INITIAL
    dd   = ((eq.cummax() - eq) / eq.cummax()).max()
    gw   = df[df["pnl_dollar"] > 0]["pnl_dollar"].sum()
    gl   = abs(df[df["pnl_dollar"] < 0]["pnl_dollar"].sum())
    pf   = round(gw / gl, 2) if gl > 0 else 0
    try:
        first = pd.to_datetime(df["entry_time"].iloc[0])
        last  = pd.to_datetime(df["entry_time"].iloc[-1])
        years = max((last - first).days / 365.25, 1/52)
        cagr  = ((1 + df["pnl_dollar"].sum() / INITIAL)**(1/years) - 1) * 100
    except Exception:
        cagr = 0
    return {"trades": n, "wr_%": round(wins/n*100, 1),
            "cagr_%": round(cagr, 1), "max_dd_%": round(dd*100, 1),
            "pf": pf, "net_$": round(df["pnl_dollar"].sum(), 2),
            "balance": round(eq.iloc[-1], 2)}


# ─── Header ──────────────────────────────────────────────────────────────────
st.title("📊 Multi-Strategy Trading Bot")
st.caption(f"Paper trading | Initial ${INITIAL:.0f}/bot | Refreshes every 30s")
st.button("🔄 Refresh")

# ─── Overview cards ───────────────────────────────────────────────────────────
st.subheader("Overview")
cols = st.columns(4)
for i, (strat, info) in enumerate(STRATEGIES.items()):
    df = load_trades(strat)
    m  = calc_metrics(df)
    with cols[i]:
        pnl_color = "green" if m["net_$"] >= 0 else "red"
        st.markdown(f"""
        <div style="border:1px solid {info['color']};border-radius:8px;padding:12px">
            <b style="color:{info['color']}">{info['label']}</b><br>
            <span style="font-size:22px;color:{pnl_color}">
                ${m['balance']:.2f}
            </span>
            <span style="font-size:12px;color:gray"> ({m['net_$']:+.2f})</span><br>
            CAGR <b>{m['cagr_%']}%</b> &nbsp;|&nbsp;
            DD <b>{m['max_dd_%']}%</b> &nbsp;|&nbsp;
            WR <b>{m['wr_%']}%</b> &nbsp;|&nbsp;
            PF <b>{m['pf']}</b><br>
            <span style="font-size:12px;color:gray">{m['trades']} trades</span>
        </div>
        """, unsafe_allow_html=True)

st.divider()

# ─── Combined equity curve ────────────────────────────────────────────────────
all_df = load_trades()
if not all_df.empty:
    st.subheader("Equity Curves")
    chart_data = {}
    for strat, info in STRATEGIES.items():
        df = all_df[all_df["strategy"] == strat].copy()
        if not df.empty:
            df["equity"] = df["pnl_dollar"].cumsum() + INITIAL
            df["time"]   = pd.to_datetime(df["entry_time"])
            chart_data[info["label"]] = df.set_index("time")["equity"]
    if chart_data:
        eq_df = pd.DataFrame(chart_data).ffill()
        st.line_chart(eq_df, height=300)

st.divider()

# ─── Per-strategy tabs ────────────────────────────────────────────────────────
tab_labels = [info["label"] for info in STRATEGIES.values()]
tabs = st.tabs(tab_labels)

for tab, (strat, info) in zip(tabs, STRATEGIES.items()):
    df = load_trades(strat)
    m  = calc_metrics(df)

    with tab:
        if df.empty:
            st.info(f"No trades yet for {info['label']}")
            continue

        # Metrics row
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Balance",    f"${m['balance']:.2f}", f"{m['net_$']:+.2f}")
        c2.metric("CAGR",       f"{m['cagr_%']}%")
        c3.metric("Max DD",     f"{m['max_dd_%']}%")
        c4.metric("Win Rate",   f"{m['wr_%']}%")
        c5.metric("Profit Factor", f"{m['pf']}")
        c6.metric("Trades",     m["trades"])

        # Equity curve
        df["equity"] = df["pnl_dollar"].cumsum() + INITIAL
        df["time"]   = pd.to_datetime(df["entry_time"])
        st.line_chart(df.set_index("time")["equity"], height=250)

        # Year-by-year
        df["year"] = pd.to_datetime(df["trade_date"]).dt.year
        if df["year"].nunique() > 0:
            yr_rows = []
            for yr, grp in df.groupby("year"):
                ym = calc_metrics(grp)
                yr_rows.append({"Year": yr, "Trades": ym["trades"],
                                 "WR%": ym["wr_%"], "Net $": ym["net_$"],
                                 "Max DD%": ym["max_dd_%"]})
            if yr_rows:
                st.dataframe(pd.DataFrame(yr_rows).set_index("Year"),
                             use_container_width=True)

        # Recent trades
        st.subheader("Recent Trades")
        show_cols = ["trade_date", "direction", "entry", "exit_price",
                     "exit_type", "pnl_dollar", "pnl_r", "balance_after"]
        show = df[[c for c in show_cols if c in df.columns]].tail(20).copy()
        show["pnl_dollar"] = show["pnl_dollar"].map("{:+.2f}".format)
        show["pnl_r"]      = show["pnl_r"].map("{:+.2f}".format)
        st.dataframe(show, use_container_width=True)

        # P&L distribution
        pnl = df["pnl_dollar"]
        if len(pnl) > 3:
            import altair as alt
            chart = alt.Chart(pd.DataFrame({"pnl": pnl})).mark_bar().encode(
                x=alt.X("pnl:Q", bin=alt.Bin(maxbins=20), title="P&L ($)"),
                y="count()",
                color=alt.condition(alt.datum.pnl > 0,
                                    alt.value("#4CAF50"), alt.value("#f44336"))
            ).properties(height=200, title="P&L Distribution")
            st.altair_chart(chart, use_container_width=True)

st.divider()
st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
