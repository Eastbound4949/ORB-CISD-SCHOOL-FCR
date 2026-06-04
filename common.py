"""
common.py — shared utilities: DB, data fetching, Telegram, metrics
"""
import os, json, sqlite3, requests, sys
import pandas as pd
import numpy as np
import pytz
import yfinance as yf
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")

ET  = pytz.timezone("America/New_York")
UTC = pytz.utc

DB_PATH        = os.path.join("data", "trades.db")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")
INITIAL_BALANCE = float(os.getenv("ACCOUNT_SIZE", "1000"))

# Per-bot risk — optimal from backtests (compound sizing)
RISK_PCT = {
    "ORB":    float(os.getenv("ORB_RISK_PCT",    "0.005")),  # 0.5%
    "CISD":   float(os.getenv("CISD_RISK_PCT",   "0.010")),  # 1.0%
    "SCHOOL": float(os.getenv("SCHOOL_RISK_PCT", "0.040")),  # 4.0% — 322% compound CAGR, DD 27.7%
    "FCR":    float(os.getenv("FCR_RISK_PCT",    "0.010")),  # 1.0% — v2.0 Sharpe 1.59
}

# SPY conflict guard: only one SPY bot in trade at a time
SPY_LOCK_FILE = os.path.join("state", "spy_lock.json")


# ─── Telegram ────────────────────────────────────────────────────────────────

def send_telegram(msg: str, chat_id: str = None):
    cid = chat_id or TELEGRAM_CHAT
    if not TELEGRAM_TOKEN or not cid:
        print(f"[TELEGRAM] {msg}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"Telegram error: {e}")


def bot_started(strategy: str):
    send_telegram(f"🚀 <b>[{strategy}]</b> Bot started — {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")


# ─── Market data ─────────────────────────────────────────────────────────────

def get_spy_5min(days: int = 1) -> pd.DataFrame:
    """SPY 5-min bars, ET timezone. Drops last bar if market open (incomplete)."""
    period = f"{days}d"
    raw = yf.download("SPY", period=period, interval="5m", progress=False, auto_adjust=True)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    if raw.empty:
        return pd.DataFrame()
    df = raw[["Open", "High", "Low", "Close", "Volume"]].dropna().copy()
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(ET)
    now_et = datetime.now(ET)
    mkt_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    mkt_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    if mkt_open <= now_et < mkt_close:
        df = df.iloc[:-1]  # drop incomplete current bar
    return df


def get_xauusd_15min(days: int = 2) -> pd.DataFrame:
    """GC=F 15-min bars, UTC timezone."""
    raw = yf.download("GC=F", period=f"{days}d", interval="15m", progress=False, auto_adjust=True)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    if raw.empty:
        return pd.DataFrame()
    df = raw[["Open", "High", "Low", "Close"]].dropna().copy()
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(UTC)
    return df


# ─── SPY conflict guard ──────────────────────────────────────────────────────

def spy_lock_acquire(strategy: str) -> bool:
    """Returns True if this strategy can enter SPY. False if another SPY bot is in trade."""
    os.makedirs("state", exist_ok=True)
    if os.path.exists(SPY_LOCK_FILE):
        try:
            with open(SPY_LOCK_FILE) as f:
                holder = json.load(f).get("holder")
            if holder and holder != strategy:
                print(f"[SPY LOCK] {strategy} blocked — {holder} already in SPY trade")
                return False
        except Exception:
            pass
    with open(SPY_LOCK_FILE, "w") as f:
        json.dump({"holder": strategy, "since": str(datetime.utcnow())}, f)
    return True


def spy_lock_release(strategy: str):
    if os.path.exists(SPY_LOCK_FILE):
        try:
            with open(SPY_LOCK_FILE) as f:
                holder = json.load(f).get("holder")
            if holder == strategy:
                os.remove(SPY_LOCK_FILE)
        except Exception:
            pass


# ─── State persistence ────────────────────────────────────────────────────────

def load_state(name: str) -> dict:
    path = os.path.join("state", f"{name}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(name: str, state: dict):
    os.makedirs("state", exist_ok=True)
    with open(os.path.join("state", f"{name}.json"), "w") as f:
        json.dump(state, f, default=str)


# ─── SQLite trade database ────────────────────────────────────────────────────

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy      TEXT,
            trade_date    TEXT,
            entry_time    TEXT,
            exit_time     TEXT,
            direction     TEXT,
            instrument    TEXT,
            entry         REAL,
            sl            REAL,
            tp            REAL,
            exit_price    REAL,
            exit_type     TEXT,
            pnl_dollar    REAL,
            pnl_r         REAL,
            shares        REAL,
            balance_after REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS balance (
            strategy TEXT PRIMARY KEY,
            balance  REAL,
            updated  TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_balance(strategy: str) -> float:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT balance FROM balance WHERE strategy=?", (strategy,)).fetchone()
    conn.close()
    return row[0] if row else INITIAL_BALANCE


def update_balance(strategy: str, val: float):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO balance VALUES (?,?,?)",
                 (strategy, val, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def log_trade(strategy: str, trade_date: str, entry_time: str, exit_time: str,
              direction: str, instrument: str, entry: float, sl: float, tp: float,
              exit_price: float, exit_type: str, pnl_dollar: float, pnl_r: float,
              shares: float, balance_after: float):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO trades
        (strategy,trade_date,entry_time,exit_time,direction,instrument,
         entry,sl,tp,exit_price,exit_type,pnl_dollar,pnl_r,shares,balance_after)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (strategy, trade_date, entry_time, exit_time, direction, instrument,
          entry, sl, tp, exit_price, exit_type, pnl_dollar, pnl_r, shares, balance_after))
    conn.commit()
    conn.close()


def get_trades_df(strategy: str = None) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    q = "SELECT * FROM trades"
    params = ()
    if strategy:
        q += " WHERE strategy=?"
        params = (strategy,)
    q += " ORDER BY entry_time"
    df = pd.read_sql_query(q, conn, params=params)
    conn.close()
    return df


# ─── Metrics ──────────────────────────────────────────────────────────────────

def calc_metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"trades": 0, "wr_%": 0, "cagr_%": 0, "max_dd_%": 0,
                "profit_factor": 0, "net_$": 0, "balance": INITIAL_BALANCE}
    n = len(df)
    wins = (df["pnl_dollar"] > 0).sum()
    eq = df["pnl_dollar"].cumsum() + INITIAL_BALANCE
    dd = ((eq.cummax() - eq) / eq.cummax()).max()
    gw = df[df["pnl_dollar"] > 0]["pnl_dollar"].sum()
    gl = abs(df[df["pnl_dollar"] < 0]["pnl_dollar"].sum())
    pf = round(gw / gl, 2) if gl > 0 else 0
    try:
        first = pd.to_datetime(df["entry_time"].iloc[0])
        last  = pd.to_datetime(df["entry_time"].iloc[-1])
        years = max((last - first).days / 365.25, 1 / 52)
        cagr  = ((1 + df["pnl_dollar"].sum() / INITIAL_BALANCE) ** (1 / years) - 1) * 100
    except Exception:
        cagr = 0
    return {
        "trades": n,
        "wr_%": round(wins / n * 100, 1),
        "cagr_%": round(cagr, 1),
        "max_dd_%": round(dd * 100, 1),
        "profit_factor": pf,
        "net_$": round(df["pnl_dollar"].sum(), 2),
        "balance": round(eq.iloc[-1], 2),
    }
