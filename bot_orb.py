"""
bot_orb.py — V04 ORB 15-min (SPY)
First 3 5-min candles form the range. Trade close above/below. SL=midpoint. RR=2.5.
Runs every 5 min from 9:35-12:00 ET. One trade per day.
"""
from datetime import datetime
import pytz
from common import (
    get_spy_5min, send_telegram, load_state, save_state,
    get_balance, update_balance, log_trade, RISK_PCT,
    spy_lock_acquire, spy_lock_release,
)
_RISK = RISK_PCT["ORB"]

STRATEGY   = "ORB"
INSTRUMENT = "SPY"
RR         = 2.5
ORB_MINS   = 15   # first 3 bars (9:30, 9:35, 9:40)
CUTOFF_H   = 12   # no new entries after noon
ET         = pytz.timezone("America/New_York")


def _tag(msg: str) -> str:
    return f"[{STRATEGY}] {msg}"


def run():
    now_et = datetime.now(ET)
    today  = now_et.strftime("%Y-%m-%d")

    state = load_state(STRATEGY)
    if state.get("trade_date") != today:
        # New day: reset
        if state.get("in_trade") and state.get("trade_date"):
            # Missed EOD close from yesterday — force close
            _force_eod_close(state)
        state = {"trade_date": today, "in_trade": False, "triggered": False}
        save_state(STRATEGY, state)

    df = get_spy_5min(days=1)
    if df.empty:
        print(_tag("No data available"))
        return

    today_bars = df[df.index.date == now_et.date()]
    if today_bars.empty:
        return

    # If already in a trade — check SL/TP
    if state.get("in_trade"):
        _check_exit(state, today_bars, now_et)
        return

    # Already triggered a trade today
    if state.get("triggered"):
        return

    # No entry after cutoff
    if now_et.hour >= CUTOFF_H:
        return

    # Build ORB from first 15 mins (9:30-9:44)
    orb_bars = today_bars[
        (today_bars.index.hour == 9) &
        (today_bars.index.minute < 30 + ORB_MINS)
    ]
    if len(orb_bars) < 3:
        return  # ORB not complete yet

    orb_high  = orb_bars["High"].max()
    orb_low   = orb_bars["Low"].min()
    orb_mid   = (orb_high + orb_low) / 2
    orb_range = orb_high - orb_low
    if orb_range < 0.20:
        return  # too tight, skip

    # Check for breakout on bars after ORB
    after_orb = today_bars[
        (today_bars.index.hour > 9) |
        ((today_bars.index.hour == 9) & (today_bars.index.minute >= 30 + ORB_MINS))
    ]

    for ts, row in after_orb.iterrows():
        if ts.hour >= CUTOFF_H:
            break
        if row["Close"] > orb_high:
            if not spy_lock_acquire(STRATEGY):
                return
            _enter(state, today, ts, 1, row["Close"], orb_mid, orb_high, orb_range)
            return
        elif row["Close"] < orb_low:
            if not spy_lock_acquire(STRATEGY):
                return
            _enter(state, today, ts, -1, row["Close"], orb_mid, orb_low, orb_range)
            return


def _enter(state, today, ts, direction, entry, sl, orb_extreme, orb_range):
    risk   = abs(entry - sl)
    if risk < 0.05:
        return
    tp     = entry + direction * RR * risk
    bal    = get_balance(STRATEGY)
    shares = (bal * _RISK) / risk
    dir_str = "LONG" if direction == 1 else "SHORT"

    state.update({
        "in_trade": True, "triggered": True,
        "direction": direction, "entry": entry,
        "sl": sl, "tp": tp, "shares": shares,
        "entry_time": str(ts), "risk": risk,
    })
    save_state(STRATEGY, state)

    msg = (f"📈 <b>{dir_str} {INSTRUMENT}</b> @ <b>${entry:.2f}</b>\n"
           f"SL ${sl:.2f}  |  TP ${tp:.2f}\n"
           f"Risk {_RISK*100:.1f}%  |  {shares:.2f} shares\n"
           f"ORB range: ${orb_range:.2f}")
    print(_tag(msg.replace("\n", " | ")))
    send_telegram(_tag(msg))


def _check_exit(state, today_bars, now_et):
    direction  = state["direction"]
    entry      = state["entry"]
    sl         = state["sl"]
    tp         = state["tp"]
    shares     = state["shares"]
    entry_time = state["entry_time"]

    # Only check bars after entry
    after_entry = today_bars[today_bars.index > entry_time]
    exit_price, exit_type = None, None

    for ts, row in after_entry.iterrows():
        if direction == 1:
            if row["Low"] <= sl:
                exit_price, exit_type = sl, "SL"
                break
            if row["High"] >= tp:
                exit_price, exit_type = tp, "TP"
                break
        else:
            if row["High"] >= sl:
                exit_price, exit_type = sl, "SL"
                break
            if row["Low"] <= tp:
                exit_price, exit_type = tp, "TP"
                break

    # EOD close at 15:55
    is_eod = now_et.hour >= 15 and now_et.minute >= 55
    if exit_price is None and is_eod and not today_bars.empty:
        exit_price = today_bars.iloc[-1]["Close"]
        exit_type  = "EOD"

    if exit_price is not None:
        _close_trade(state, entry, exit_price, exit_type, shares,
                     entry_time, str(now_et))


def _close_trade(state, entry, exit_price, exit_type, shares,
                 entry_time, exit_time):
    direction  = state["direction"]
    sl         = state["sl"]
    risk       = state["risk"]
    trade_date = state["trade_date"]

    pnl_pts    = direction * (exit_price - entry)
    pnl_dollar = pnl_pts * shares
    pnl_r      = pnl_pts / risk

    bal_before = get_balance(STRATEGY)
    bal_after  = bal_before + pnl_dollar
    update_balance(STRATEGY, bal_after)

    log_trade(
        STRATEGY, trade_date, entry_time, exit_time,
        "LONG" if direction == 1 else "SHORT", INSTRUMENT,
        entry, sl, state["tp"], exit_price, exit_type,
        pnl_dollar, pnl_r, shares, bal_after
    )

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
    """Close any leftover position from a previous day."""
    if not state.get("in_trade"):
        return
    df = get_spy_5min(days=2)
    if df.empty:
        return
    prev_day_bars = df[df.index.date < datetime.now(ET).date()]
    if prev_day_bars.empty:
        return
    last_close = prev_day_bars.iloc[-1]["Close"]
    _close_trade(state, state["entry"], last_close, "EOD",
                 state["shares"], state["entry_time"], str(prev_day_bars.index[-1]))
