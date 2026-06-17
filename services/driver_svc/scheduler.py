"""Server-side driver scheduler (Task #29c).

Runs the morning pipeline once per trading day at/after 09:28 ET (the legacy
``claude-driver/morning_agent`` cadence, ``config.AGENT_RUN_HOUR/MIN``) and keeps
the performance view warm. The scheduler NEVER executes orders — ``run_morning``
only produces a *pending* approval; execution happens only on an explicit
``approve`` command.

Run-time gate ``morning_due`` is pure (tested); the ``loop`` owns its sleep
cadence and runs the BLOCKING handlers in the default executor so the event loop
stays responsive. Passed to the scaffold as ``make_app(scheduler=loop)``.

**Catch-up semantics (single-user):** ``last_run_date`` lives in memory, so if
the service starts after 09:28 on a trading day it fires once immediately
(``last_run_date=None`` → due) so the page has a fresh pending approval; a
restart later the same day re-fires and overwrites the cached approval. The
market-holiday short-circuit lives in ``compute.run_morning`` (it returns a
``no_trade`` payload), so the gate here only checks weekday + time.
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from services.driver_svc import handlers

_ET = ZoneInfo("America/New_York")
RUN_HOUR, RUN_MIN = 9, 28  # mirrors config.AGENT_RUN_HOUR / AGENT_RUN_MIN

POLL_INTERVAL_SEC = 30        # check the run gate every 30 s
PERF_REFRESH_SEC = 300        # recompute the perf view ~every 5 min


def morning_due(now, last_run_date):
    """(should_run, run_date): True at most once per weekday, at/after 09:28 ET.

    ``now`` is an ET-aware datetime; ``last_run_date`` is the ISO date string of
    the last fire (or None). When not yet due, the passed-in ``last_run_date`` is
    returned unchanged.
    """
    if now.weekday() >= 5:  # Sat/Sun
        return (False, last_run_date)
    if (now.hour, now.minute) < (RUN_HOUR, RUN_MIN):
        return (False, last_run_date)
    today = now.date().isoformat()
    return (today != last_run_date, today)


def _now_et():
    return datetime.now(_ET)


async def loop(bus):
    """Fire the morning pipeline once/day at 09:28 ET; keep perf warm.

    One-shot perf refresh at startup so the page has data on first load, then a
    30 s poll: run the morning pipeline when due (once/day) and recompute the
    perf view every ~5 min. Each blocking call runs in the executor and is
    independently guarded so one failure can't kill the loop or skip the other.
    """
    loop_ = asyncio.get_event_loop()
    last_run_date = None
    try:
        await loop_.run_in_executor(None, handlers.refresh_perf, bus)
    except Exception:  # noqa: BLE001
        pass
    secs_since_perf = 0
    while True:
        try:
            due, run_date = morning_due(_now_et(), last_run_date)
            if due:
                last_run_date = run_date
                await loop_.run_in_executor(None, handlers.run_morning, bus)
        except Exception:  # noqa: BLE001 — never let the scheduler die.
            pass
        if secs_since_perf >= PERF_REFRESH_SEC:
            secs_since_perf = 0
            try:
                await loop_.run_in_executor(None, handlers.refresh_perf, bus)
            except Exception:  # noqa: BLE001
                pass
        secs_since_perf += POLL_INTERVAL_SEC
        await asyncio.sleep(POLL_INTERVAL_SEC)
