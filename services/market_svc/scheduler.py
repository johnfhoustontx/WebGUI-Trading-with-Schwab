"""Market dashboard scheduler — poll the proxy, publish, repeat.

3 s cadence during regular trading hours; a slower 15 s off-hours/weekends and
60 s once the equity-index futures are also closed (deep weekend). It is NOT
throttled hard off-hours because the equity-index FUTURES (/ES, /NQ) trade
almost 24 h — they're the main thing moving after the cash close, so the
off-hours cadence keeps them visibly ticking (the cash indices/internals are
stale then anyway, and skip_unchanged means an unchanged payload costs
nothing). The market-hours gate mirrors the other services.

The market summary is refreshed ON THIS SAME LOOP: each poll stats the
published market report (``report_summary.report_stamp``) and republishes
``cache:market:summary`` only when the report was replaced. No Claude call —
the change-driven Claude sentence this loop used to write was retired
2026-09-16 in favour of the report's own highlights.
"""
import asyncio
import datetime as _dt
import logging
from datetime import time as _time
from zoneinfo import ZoneInfo

from services.market_svc import compute, handlers, report_summary
from shared import market_calendar as mc

_log = logging.getLogger("market_svc.scheduler")

# Cadence tuned for Schwab /quotes volume: the dashboard updates tiles in place,
# so 3 s vs 2 s is imperceptible but roughly halves the daily call count
# (~24k → ~12k/day). Off-hours widened 5 s → 15 s (futures still tick visibly).
RTH_INTERVAL_SEC = 3         # regular trading hours
OFFHOURS_INTERVAL_SEC = 15   # futures trade ~24h Sun-Fri → keep visibly ticking
WEEKEND_INTERVAL_SEC = 60    # futures CLOSED (Sat all day, Sun before 17:00 CT)

_CT = ZoneInfo("America/Chicago")
# The 08:30-15:00 CT window and the NYSE holiday set both come from
# shared/market_calendar.py now (config-driven + derived — no yearly edit).
# The 17:00 CT futures reopen in
# ``_futures_closed`` below is deliberately NOT one of these: it is a futures
# cadence boundary, not a named market window.


def _is_rth(now):
    """Mon-Fri 08:30-15:00 CT, holidays excluded — inclusive at both ends."""
    return mc.is_regular_hours(now)


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


def refresh_summary(bus, last_stamp, reports_dir=None):
    """Republish the summary when the published report changed. Returns the
    stamp to remember — a stat per poll is the whole steady-state cost.

    A report that does not parse publishes nothing, so the last good summary
    stays, and its stamp IS remembered: the same bytes would fail the same way,
    and re-reading them every 3 s would log a warning every 3 s."""
    kw = {} if reports_dir is None else {"reports_dir": reports_dir}
    stamp = report_summary.report_stamp(**kw)
    if stamp is None or stamp == last_stamp:
        return last_stamp
    payload = report_summary.read_report(**kw)
    if payload is None:
        return stamp
    handlers.publish_summary(bus, payload)
    return stamp


async def loop(bus) -> None:
    """Poll → publish → (the report summary, when the report changed) → sleep."""
    loop_ = asyncio.get_running_loop()
    report_stamp = None
    while True:
        interval = poll_interval()
        try:
            payload = await loop_.run_in_executor(None, compute.collect, bus)
            await loop_.run_in_executor(None, handlers.publish, bus, payload)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the scheduler die.
            _log.exception("market poll cycle failed")
        try:
            report_stamp = await loop_.run_in_executor(
                None, refresh_summary, bus, report_stamp)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a summary failure can't stop the poll.
            _log.exception("market report summary refresh failed")
        await asyncio.sleep(interval)
