"""
bot_fcr.py — V18 FCR 9:30 v2.0 (SPY)
First 5-min candle (9:30 ET) defines the range. Three filters (v2.0):
  1. EMA50 trend — only long if close > EMA50, short if close < EMA50
  2. FCR body direction — only trade breakout in same direction as 9:30 candle body
  3. Entry window — 9:35-10:30 ET only (not until noon)
SL = full FCR range (opposite side). RR = 3.0. One trade per day.
"""
from datetime import datetime
import pytz
import pandas as pd
from common import (
    get_spy_5min, send_telegram, load_state, save_state,
    get_balance, update_balance, log_trade, RISK_PCT,
    spy_lock_acquire, spy_lock_release,
)
_RISK = RISK_PCT["FCR"]

STRATEGY   = "FCR"
INSTRUMENT = "SPY"
RR         = 3.0
EMA_PERIOD = 50
ENTRY_CUTOFF_H = 10
ENTRY_CUTOFF_M = 30
ET         = pytz.timezone("America/New_York")


def _tag(msg: str) -> str:
    return f"[{STRATEGY}] {msg}"


def run():
    now_et = datetime.now(ET)
    today  = now_et.strftime("%Y-%m-%d")

    # Only trade Mon-Fri, 9:35-10:30 ET
    if now_et.weekday() >= 5:
        return
    past_cutoff = (now_et.hour > ENTRY_CUTOFF_H or
                   (now_et.hour == ENTRY_CUTOFF_H and now_et.minute >= ENTRY_CUTOFF_M))
    if past_cutoff:
        return
    if now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 35):
        return

    state = load_state(STRATEGY)
    if state.get("trade_date") != today:
        if state.get("in_trade"):
            _force_eod_close(state)
        state = {"trade_date": today, "in_trade": False, "triggered": False}
        save_state(STRATEGY, state)

    if state.get("triggered") or state.get("in_trade"):
        if state.get("in_trade"):
            df = get_spy_5min(days=1)
            if not df.empty:
                _check_exit(state, df[df.index.date == now_et.date()], now_et)
        return

    df = get_spy_5min(days=5)  # need 5 days for EMA50 warmup
    if df.empty:
        return

    # EMA50 on full history
    df["ema50"] = df["Close"].ewm(span=EMA_PERIOD, adjust=False).mean()

    today_bars = df[df.index.date == now_et.date()]
    if today_bars.empty:
        return

    # FCR = the 9:30 ET bar
    fcr_bars = today_bars[(today_bars.index.hour == 9) & (today_bars.index.minute == 30)]
    if fcr_bars.empty:
        return
    fcr = fcr_bars.iloc[0]
    fcr_high = fcr["High"]
    fcr_low  = fcr["Low"]
    if (fcr_high - fcr_low) < 0.05:
        return

    # v2.0 filter 2: FCR body direction
    fcr_bull = fcr["Close"] >= fcr["Open"]

    # Bars in entry window: 9:35-10:30
    after = today_bars[
        (today_bars.index.hour > 9) |
        ((today_bars.index.hour == 9) & (today_bars.index.minute >= 35))
    ]
    after = after[
        (after.index.hour < ENTRY_CUTOFF_H) |
        ((after.index.hour == ENTRY_CUTOFF_H) & (after.index.minute < ENTRY_CUTOFF_M))
    ]

    for ts, row in after.iterrows():
        ema = row["ema50"]
        close = row["Close"]

        if close > fcr_high:
            # v2.0 filter 1: EMA trend
            if close < ema:
                continue
            # v2.0 filter 2: FCR body direction
            if not fcr_bull:
                continue
            if not spy_lock_acquire(STRATEGY):
                return
            _enter(state, today, ts, 1, close, fcr_low, fcr_high)
            return

        elif close < fcr_low:
            if close > ema:
                continue
            if fcr_bull:
                continue
            if not spy_lock_acquire(STRATEGY):
                return
            _enter(state, today, ts, -1, close, fcr_high, fcr_low)
            return


def _enter(state, today, ts, direction, entry, sl_level, fcr_extreme):
    risk = abs(entry - sl_level)
    if risk < 0.05:
        spy_lock_release(STRATEGY)
        return
    tp      = entry + direction * RR * risk
    bal     = get_balance(STRATEGY)
    shares  = (bal * _RISK) / risk
    dir_str = "LONG" if direction == 1 else "SHORT"

    state.update({
        "in_trade": True, "triggered": True,
        "direction": direction, "entry": entry,
        "sl": sl_level, "tp": tp, "shares": shares,
        "entry_time": str(ts), "risk": risk,
    })
    save_state(STRATEGY, state)

    msg = (f"🕙 <b>FCR {dir_str} {INSTRUMENT}</b> @ <b>${entry:.2f}</b>\n"
           f"FCR range: ${min(sl_level, fcr_extreme):.2f} - ${max(sl_level, fcr_extreme):.2f}\n"
           f"SL ${sl_level:.2f}  |  TP ${tp:.2f}  |  RR {RR}\n"
           f"Risk {_RISK*100:.1f}%  |  {shares:.2f} shares")
    print(_tag(msg.replace("\n", " | ")))
    send_telegram(_tag(msg))


def _check_exit(state, today_bars, now_et):
    direction  = state["direction"]
    entry      = state["entry"]
    sl         = state["sl"]
    tp         = state["tp"]
    shares     = state["shares"]
    entry_time = state["entry_time"]

    after_entry = today_bars[today_bars.index > entry_time]
    exit_price, exit_type = None, None

    for ts, row in after_entry.iterrows():
        if direction == 1:
            if row["Low"] <= sl:
                exit_price, exit_type = sl, "SL"; break
            if row["High"] >= tp:
                exit_price, exit_type = tp, "TP"; break
        else:
            if row["High"] >= sl:
                exit_price, exit_type = sl, "SL"; break
            if row["Low"] <= tp:
                exit_price, exit_type = tp, "TP"; break

    if exit_price is None and now_et.hour >= 15 and now_et.minute >= 55:
        if not today_bars.empty:
            exit_price, exit_type = today_bars.iloc[-1]["Close"], "EOD"

    if exit_price is not None:
        _close_trade(state, entry, exit_price, exit_type, shares,
                     entry_time, str(now_et))


def _close_trade(state, entry, exit_price, exit_type, shares, entry_time, exit_time):
    direction  = state["direction"]
    risk       = state["risk"]
    trade_date = state["trade_date"]

    pnl_pts    = direction * (exit_price - entry)
    pnl_dollar = pnl_pts * shares
    pnl_r      = pnl_pts / risk

    bal_before = get_balance(STRATEGY)
    bal_after  = bal_before + pnl_dollar
    update_balance(STRATEGY, bal_after)

    log_trade(STRATEGY, trade_date, entry_time, exit_time,
              "LONG" if direction == 1 else "SHORT", INSTRUMENT,
              entry, state["sl"], state["tp"], exit_price, exit_type,
              pnl_dollar, pnl_r, shares, bal_after)

    icon = "✅" if pnl_dollar > 0 else "❌"
    msg  = (f"{icon} <b>FCR CLOSED {INSTRUMENT}</b> @ ${exit_price:.2f}\n"
            f"P&L: <b>${pnl_dollar:+.2f}</b> ({pnl_r:+.2f}R)  |  {exit_type}\n"
            f"Balance: ${bal_after:.2f}")
    print(_tag(msg.replace("\n", " | ")))
    send_telegram(_tag(msg))

    state.update({"in_trade": False})
    save_state(STRATEGY, state)
    spy_lock_release(STRATEGY)


def _force_eod_close(state):
    if not state.get("in_trade"):
        return
    df = get_spy_5min(days=2)
    if df.empty:
        return
    prev = df[df.index.date < datetime.now(ET).date()]
    if prev.empty:
        return
    _close_trade(state, state["entry"], prev.iloc[-1]["Close"], "EOD",
                 state["shares"], state["entry_time"], str(prev.index[-1]))
