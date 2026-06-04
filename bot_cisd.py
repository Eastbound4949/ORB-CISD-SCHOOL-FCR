"""
bot_cisd.py — V07 ICT CISD BSL/SSL Sweep (SPY)
Detects swing high sweeps (BSL) -> first bearish CISD candle = short entry.
Also detects swing low sweeps (SSL) -> first bullish CISD = long.
RR=3.0, SwingLB=10, Cooldown=3 bars. One trade per day.
"""
from datetime import datetime
import pandas as pd
import pytz
from common import (
    get_spy_5min, send_telegram, load_state, save_state,
    get_balance, update_balance, log_trade, RISK_PCT,
    spy_lock_acquire, spy_lock_release,
)
_RISK = RISK_PCT["CISD"]

STRATEGY   = "CISD"
INSTRUMENT = "SPY"
RR         = 3.0
SWING_LB   = 10   # bars to look back for swing high/low
COOLDOWN   = 3    # bars after sweep before CISD entry
MAX_WAIT   = 20   # bars: give up looking for CISD after this
ET         = pytz.timezone("America/New_York")


def _tag(msg: str) -> str:
    return f"[{STRATEGY}] {msg}"


def run():
    now_et = datetime.now(ET)
    today  = now_et.strftime("%Y-%m-%d")

    state = load_state(STRATEGY)
    if state.get("trade_date") != today:
        if state.get("in_trade"):
            _force_eod_close(state)
        state = {
            "trade_date": today, "in_trade": False, "triggered": False,
            "sweep_detected": False, "sweep_bar_count": 0,
        }
        save_state(STRATEGY, state)

    df = get_spy_5min(days=1)
    if df.empty:
        return

    today_bars = df[df.index.date == now_et.date()]
    if len(today_bars) < SWING_LB + 2:
        return

    # Check exit first
    if state.get("in_trade"):
        _check_exit(state, today_bars, now_et)
        return

    if state.get("triggered"):
        return

    if now_et.hour >= 15:
        return

    bars = today_bars.copy()
    bars = bars[(bars.index.hour > 9) | ((bars.index.hour == 9) & (bars.index.minute >= 35))]

    if len(bars) < SWING_LB + 1:
        return

    # Get the last completed bar
    last = bars.iloc[-1]
    last_ts = bars.index[-1]

    # Rolling swing high/low from recent bars (no lookahead)
    lookback = bars.iloc[-(SWING_LB + 1):-1]  # last SWING_LB bars before current
    if lookback.empty:
        return

    swing_high = lookback["High"].max()
    swing_low  = lookback["Low"].min()

    if state.get("sweep_detected"):
        # Count bars since sweep
        state["sweep_bar_count"] = state.get("sweep_bar_count", 0) + 1
        save_state(STRATEGY, state)

        if state["sweep_bar_count"] > MAX_WAIT:
            state["sweep_detected"] = False
            state["sweep_bar_count"] = 0
            save_state(STRATEGY, state)
            return

        if state["sweep_bar_count"] >= COOLDOWN:
            sweep_type = state["sweep_type"]
            # Look for CISD signal on last bar
            if sweep_type == "short" and last["Close"] < last["Open"]:
                entry = last["Close"]
                sl    = state["sweep_level"] + 0.10
                risk  = sl - entry
                if 0.05 <= risk <= 5.0:
                    if spy_lock_acquire(STRATEGY):
                        _enter(state, today, last_ts, -1, entry, sl, risk)
            elif sweep_type == "long" and last["Close"] > last["Open"]:
                entry = last["Close"]
                sl    = state["sweep_level"] - 0.10
                risk  = entry - sl
                if 0.05 <= risk <= 5.0:
                    if spy_lock_acquire(STRATEGY):
                        _enter(state, today, last_ts, 1, entry, sl, risk)
        return

    # Detect new sweep on current bar
    # BSL sweep: wick above swing_high, close back below it
    if last["High"] > swing_high and last["Close"] < swing_high:
        state.update({
            "sweep_detected": True, "sweep_bar_count": 0,
            "sweep_type": "short", "sweep_level": last["High"],
        })
        save_state(STRATEGY, state)
        print(_tag(f"BSL sweep detected @ ${last['High']:.2f} (SH={swing_high:.2f})"))

    # SSL sweep: wick below swing_low, close back above it
    elif last["Low"] < swing_low and last["Close"] > swing_low:
        state.update({
            "sweep_detected": True, "sweep_bar_count": 0,
            "sweep_type": "long", "sweep_level": last["Low"],
        })
        save_state(STRATEGY, state)
        print(_tag(f"SSL sweep detected @ ${last['Low']:.2f} (SL={swing_low:.2f})"))


def _enter(state, today, ts, direction, entry, sl, risk):
    tp     = entry + direction * RR * risk
    bal    = get_balance(STRATEGY)
    shares = (bal * _RISK) / risk
    dir_str = "LONG" if direction == 1 else "SHORT"

    state.update({
        "in_trade": True, "triggered": True, "sweep_detected": False,
        "direction": direction, "entry": entry, "sl": sl, "tp": tp,
        "shares": shares, "entry_time": str(ts), "risk": risk,
    })
    save_state(STRATEGY, state)

    msg = (f"⚡ <b>CISD {dir_str} {INSTRUMENT}</b> @ <b>${entry:.2f}</b>\n"
           f"SL ${sl:.2f}  |  TP ${tp:.2f}\n"
           f"Risk {_RISK*100:.1f}%  |  {shares:.2f} shares\n"
           f"Sweep @ ${state['sweep_level']:.2f}")
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


def _close_trade(state, entry, exit_price, exit_type, shares,
                 entry_time, exit_time):
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
    msg  = (f"{icon} <b>CLOSED {INSTRUMENT}</b> @ ${exit_price:.2f}\n"
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
