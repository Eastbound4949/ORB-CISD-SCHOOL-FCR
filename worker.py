"""
worker.py — Railway scheduler. Runs School Run v2.0 via APScheduler.
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
import bot_school

UTC = pytz.UTC


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
    print("School Run Bot v2.0 — Starting up (XAUUSD)")
    print(f"Time: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 50)

    init_db()
    bot_started("SCHOOL")

    scheduler = BlockingScheduler(timezone=UTC)

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
