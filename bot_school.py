"""
bot_school.py — V23 School Run v2.0 (XAUUSD / GC=F)
Mark overnight Asia range (00:00-05:00 UTC). Scan any bar 05:00-06:00 UTC
for first breakout (close beyond range). RR=4.0, SL=midpoint.
v2.0: extended entry window 05:00-06:00 UTC (v1.0 was 05:00 bar only).
Risk: 4% (optimal from 11.4yr backtest sweep, compound CAGR 322%).
"""
from datetime import datetime
import pytz
from common import (
    get_xauusd_15min, send_telegram, load_state, save_state,
    get_balance, update_balance, log_trade, RISK_PCT,
)
_RISK = RISK_PCT["SCHOOL"]

STRATEGY      = "SCHOOL"
INSTRUMENT    = "XAUUSD"
RR            = 4.0
MIN_RANGE     = 3.0   # min Asia range in $ (gold)
ASIA_START_H  = 0     # 00:00 UTC
ASIA_END_H    = 5     # 05:00 UTC = London open proxy
ENTRY_END_H   = 6     # v2.0: scan up to 06:00 UTC for first breakout bar
UTC           = pytz.UTC


def _tag(msg: str) -> str:
    return f"[SCHOOL] {msg}"


def run():
    """Called at 05:00 UTC to check entry signal."""
    now_utc = datetime.now(UTC)
    today   = now_utc.strftime("%Y-%m-%d")

    state = load_state(STRATEGY)
    if state.get("trade_date") == today and state.get("triggered"):
        print(_tag("Already triggered today"))
        return

    df = get_xauusd_15min(days=2)
    if df.empty:
        print(_tag("No XAUUSD data"))
        return

    # Asia range bars: 00:00-05:00 UTC today
    today_dt = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    asia = df[(df.index >= today_dt) & (df.index.hour < ASIA_END_H)]
    if len(asia) < 4:
        print(_tag(f"Not enough Asia bars: {len(asia)}"))
        return

    asia_high = asia["High"].max()
    asia_low  = asia["Low"].min()
    asia_mid  = (asia_high + asia_low) / 2
    asia_range = asia_high - asia_low
    if asia_range < MIN_RANGE:
        print(_tag(f"Asia range too tight: ${asia_range:.2f}"))
        return

    # v2.0: scan any bar in entry window 05:00-06:00 UTC for first breakout
    entry_window = df[
        (df.index.date == now_utc.date()) &
        (df.index.hour >= ASIA_END_H) &
        (df.index.hour < ENTRY_END_H)
    ]
    if entry_window.empty:
        print(_tag("No bars in entry window yet"))
        return

    direction = None
    entry_bar_row = None
    for ts, bar in entry_window.iterrows():
        if bar["Close"] > asia_high:
            direction = 1;  entry_bar_row = bar; break
        elif bar["Close"] < asia_low:
            direction = -1; entry_bar_row = bar; break

    if direction is None:
        # Entry window fully passed with no breakout → mark as checked
        if now_utc.hour >= ENTRY_END_H:
            print(_tag(f"Entry window closed, no breakout. Range=[{asia_low:.2f}-{asia_high:.2f}]"))
            state.update({"trade_date": today, "triggered": True, "in_trade": False})
            save_state(STRATEGY, state)
        else:
            print(_tag(f"No breakout yet. Range=[{asia_low:.2f}-{asia_high:.2f}], watching..."))
        return

    lb    = entry_bar_row
    entry = lb["Close"]
    sl    = asia_mid

    risk   = abs(entry - sl)
    if risk < 0.50:
        return
    tp     = entry + direction * RR * risk
    bal    = get_balance(STRATEGY)
    shares = (bal * _RISK) / risk
    dir_str = "LONG" if direction == 1 else "SHORT"

    state.update({
        "trade_date": today, "triggered": True, "in_trade": True,
        "direction": direction, "entry": entry, "sl": sl, "tp": tp,
        "shares": shares, "entry_time": str(entry_bar.index[0]), "risk": risk,
        "asia_high": asia_high, "asia_low": asia_low,
    })
    save_state(STRATEGY, state)

    msg = (f"🌏 <b>{dir_str} {INSTRUMENT}</b> @ <b>${entry:.2f}</b>\n"
           f"Asia range: ${asia_low:.2f} - ${asia_high:.2f} (${asia_range:.2f})\n"
           f"SL ${sl:.2f}  |  TP ${tp:.2f}  |  RR {RR}\n"
           f"Risk {_RISK*100:.1f}%  |  {shares:.4f} lots")
    print(_tag(msg.replace("\n", " | ")))
    send_telegram(_tag(msg))


def check_exit():
    """Called every 15 min during the day to monitor open trade."""
    state = load_state(STRATEGY)
    if not state.get("in_trade"):
        return

    now_utc = datetime.now(UTC)
    df = get_xauusd_15min(days=1)
    if df.empty:
        return

    entry      = state["entry"]
    sl         = state["sl"]
    tp         = state["tp"]
    direction  = state["direction"]
    shares     = state["shares"]
    entry_time = state["entry_time"]

    after_entry = df[df.index > entry_time]
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

    # Close at 21:00 UTC (gold session close before overnight gap)
    if exit_price is None and now_utc.hour >= 21:
        if not after_entry.empty:
            exit_price = after_entry.iloc[-1]["Close"]
            exit_type  = "EOD"

    if exit_price is not None:
        pnl_pts    = direction * (exit_price - entry)
        pnl_dollar = pnl_pts * shares
        pnl_r      = pnl_pts / state["risk"]

        bal_before = get_balance(STRATEGY)
        bal_after  = bal_before + pnl_dollar
        update_balance(STRATEGY, bal_after)

        log_trade(STRATEGY, state["trade_date"], entry_time, str(now_utc),
                  "LONG" if direction == 1 else "SHORT", INSTRUMENT,
                  entry, sl, tp, exit_price, exit_type,
                  pnl_dollar, pnl_r, shares, bal_after)

        icon = "✅" if pnl_dollar > 0 else "❌"
        msg  = (f"{icon} <b>CLOSED {INSTRUMENT}</b> @ ${exit_price:.2f}\n"
                f"P&L: <b>${pnl_dollar:+.2f}</b> ({pnl_r:+.2f}R)  |  {exit_type}\n"
                f"Balance: ${bal_after:.2f}")
        print(_tag(msg.replace("\n", " | ")))
        send_telegram(_tag(msg))

        state["in_trade"] = False
        save_state(STRATEGY, state)
