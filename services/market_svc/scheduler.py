"""Market dashboard scheduler — poll the proxy, publish, repeat.

~2 s cadence during regular trading hours; a slower ~5 s off-hours/weekends/
holidays. It is NOT throttled hard off-hours because the equity-index FUTURES
(/ES, /NQ) trade almost 24 h — they're the main thing moving after the cash
close, so a 5 s cadence keeps them visibly ticking (the cash indices/internals
are stale then anyway, and skip_unchanged means an unchanged payload costs
nothing). The market-hours gate mirrors the other services.
"""
import asyncio
import datetime as _dt
import logging
from datetime import time as _time
from zoneinfo import ZoneInfo

from services.market_svc import compute, handlers
from shared.market_calendar import HOLIDAYS as _HOLIDAYS  # noqa: F401  (uniform alias; no consumer here -- prefer is_holiday(): HOLIDAYS covers 2026-27 only)
from shared.market_calendar import is_trading_day as _cal_is_trading_day

_log = logging.getLogger("market_svc.scheduler")

RTH_INTERVAL_SEC = 2
OFFHOURS_INTERVAL_SEC = 5     # futures trade ~24h → keep off-hours snappy
WEEKEND_INTERVAL_SEC = 60    # futures CLOSED (Sat all day, Sun before 17:00 CT)

_CT = ZoneInfo("America/Chicago")
_RTH_START = (8, 30)
_RTH_END = (15, 0)
# NYSE full-closure holidays come from shared/market_calendar.py (derived, not a
# literal — no yearly edit). ``_HOLIDAYS`` stays bound as the local alias.


def _is_rth(now):
    if not _cal_is_trading_day(now.date()):
        return False
    return _time(*_RTH_START) <= now.time() <= _time(*_RTH_END)


def _futures_closed(now):
    """True while the equity-index futures are CLOSED: Saturday all day, and
    Sunday before the 17:00 CT reopen (they otherwise trade ~24h Sun-Fri). During
    this window nothing moves, so the off-hours poll can throttle hard."""
    wd = now.weekday()
    if wd == 5:                                  # Saturday
        return True
    if wd == 6 and now.time() < _time(17, 0):    # Sunday before the 17:00 CT reopen
        return True
    return False


def poll_interval(now=None):
    """Seconds until the next poll — fast during RTH, slow off-hours, slowest when
    the futures are closed (deep weekend) since nothing ticks then (pure)."""
    now = now or _dt.datetime.now(_CT)
    if _is_rth(now):
        return RTH_INTERVAL_SEC
    if _futures_closed(now):
        return WEEKEND_INTERVAL_SEC
    return OFFHOURS_INTERVAL_SEC


# The verdict is the ticker's slow "why" — the live data items beside it refresh on
# the ~2 s poll, so a narrative that lags the tape by up to 40 min costs the reader
# little and halves the Claude spend (it was the stack's single biggest caller at
# 20 min: ~21 of ~39 calls/day).
SUMMARY_RTH_SEC = 40 * 60       # refresh the Claude verdict every ~40 min during RTH
SUMMARY_OFFHOURS_SEC = 60 * 60  # ~hourly off-hours


def summary_due(has_run, secs_since, *, now=None, enabled=True):
    """Whether to regenerate the Claude verdict this cycle (pure).

    ``has_run`` is a True/None sentinel — None until the first run (→ always due).
    Once it has run, fire when ``secs_since`` the last run exceeds the RTH/off-hours
    interval.

    ``enabled`` is the webgui ticker toggle (see ``handlers.summary_enabled``);
    False short-circuits everything — no marquee, no Claude call. ``secs_since``
    keeps accumulating while off, so re-enabling produces a fresh verdict at once.
    """
    if not enabled:
        return False
    if has_run is None:
        return True
    now = now or _dt.datetime.now(_CT)
    threshold = SUMMARY_RTH_SEC if _is_rth(now) else SUMMARY_OFFHOURS_SEC
    return secs_since >= threshold


async def _run_summary(loop_, bus, payload, sent_payload) -> None:
    """Generate the Claude verdict + publish it, OFF the poll loop. Never raises."""
    try:
        summary = await loop_.run_in_executor(
            None, compute.generate_summary, payload, sent_payload)
        await loop_.run_in_executor(None, handlers.publish_summary, bus, summary)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a summary failure can't affect the poll loop.
        _log.exception("market summary generation failed")


async def loop(bus) -> None:
    """Poll → publish → (periodic Claude summary) → sleep, forever. Never raises out."""
    loop_ = asyncio.get_running_loop()
    summary_started = None
    secs_since_summary = 0.0
    summary_task = None
    while True:
        interval = poll_interval()
        try:
            payload = await loop_.run_in_executor(None, compute.collect, bus)
            await loop_.run_in_executor(None, handlers.publish, bus, payload)
            # Read the toggle every cycle (a cheap local cache hit) so flipping it
            # in Settings takes effect on the next poll, with no service restart.
            enabled = handlers.summary_enabled(bus)
            if (summary_due(summary_started, secs_since_summary, enabled=enabled)
                    and (summary_task is None or summary_task.done())):
                sent = bus.cache_get("cache:sentiment:composite")
                sent_payload = sent.payload if sent else {}
                # Launch the Claude summary as a BACKGROUND task — it can take up to
                # ~60s (30s timeout + retry) and must NOT stall the ~2s poll cadence.
                summary_task = asyncio.create_task(
                    _run_summary(loop_, bus, payload, sent_payload))
                summary_started = True
                secs_since_summary = 0.0
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the scheduler die.
            _log.exception("market poll cycle failed")
        await asyncio.sleep(interval)
        secs_since_summary += interval
