"""
worker.py — Railway scheduler. Runs all 4 bots via APScheduler.
ORB + CISD + FCR: every 5 min, 9:35 ET (Mon-Fri)
FCR: entry window 9:35-10:30 ET only (bot_fcr.run() self-guards cutoff)
School Run v2.0: every 15 min 05:00-06:00 UTC entry + exit check all day
"""
import sys
import pytz
import logging
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.WARNING)

from common import init_db, bot_started
import bot_orb
import bot_cisd
import bot_school
import bot_fcr

ET  = pytz.timezone("America/New_York")
UTC = pytz.UTC


def run_orb():
    try:
        now_et = datetime.now(ET)
        if now_et.weekday() >= 5:   # skip weekends
            return
        if not (9 <= now_et.hour < 12):
            return
        if now_et.hour == 9 and now_et.minute < 35:
            return
        print(f"[{now_et.strftime('%H:%M ET')}] Running ORB check...")
        bot_orb.run()
    except Exception as e:
        print(f"[ORB] Error: {e}")


def run_cisd():
    try:
        now_et = datetime.now(ET)
        if now_et.weekday() >= 5:
            return
        if not (9 <= now_et.hour < 15):
            return
        if now_et.hour == 9 and now_et.minute < 35:
            return
        print(f"[{now_et.strftime('%H:%M ET')}] Running CISD check...")
        bot_cisd.run()
    except Exception as e:
        print(f"[CISD] Error: {e}")


def run_school_entry():
    try:
        now_utc = datetime.now(UTC)
        if now_utc.weekday() >= 5:
            return
        # v2.0: entry window 05:00-06:00 UTC
        if not (5 <= now_utc.hour < 6):
            return
        print(f"[{now_utc.strftime('%H:%M UTC')}] Running School Run entry check...")
        bot_school.run()
    except Exception as e:
        print(f"[SCHOOL] Error: {e}")


def run_fcr():
    try:
        now_et = datetime.now(ET)
        if now_et.weekday() >= 5:
            return
        if not (9 <= now_et.hour <= 10):
            return
        print(f"[{now_et.strftime('%H:%M ET')}] Running FCR check...")
        bot_fcr.run()
    except Exception as e:
        print(f"[FCR] Error: {e}")


def run_school_exit():
    try:
        now_utc = datetime.now(UTC)
        if now_utc.weekday() >= 5:
            return
        bot_school.check_exit()
    except Exception as e:
        print(f"[SCHOOL EXIT] Error: {e}")


def main():
    print("=" * 50)
    print("Multi-Strategy Bot v2.0 — Starting up (ORB + CISD + School Run v2.0 + FCR v2.0)")
    print(f"Time: {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")
    print("=" * 50)

    init_db()
    bot_started("ORB")
    bot_started("CISD")
    bot_started("SCHOOL")
    bot_started("FCR")

    scheduler = BlockingScheduler(timezone=UTC)

    # ORB + CISD + FCR: every 5 min (each bot self-guards its time window)
    scheduler.add_job(run_orb,          "cron", minute="*/5", id="orb")
    scheduler.add_job(run_cisd,         "cron", minute="*/5", id="cisd")
    scheduler.add_job(run_fcr,          "cron", minute="*/5", id="fcr")

    # School Run v2.0: entry every 15 min 05:00-06:00 UTC, exit check every 15 min all day
    scheduler.add_job(run_school_entry, "cron", hour="5", minute="*/15", id="school_entry")
    scheduler.add_job(run_school_exit,  "cron", hour="5-21", minute="*/15", id="school_exit")

    print("Scheduler started. Press Ctrl+C to stop.\n")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler stopped.")


if __name__ == "__main__":
    main()
