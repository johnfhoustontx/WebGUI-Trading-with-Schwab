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
from datetime import date as _date, time as _time
from zoneinfo import ZoneInfo

from services.market_svc import compute, handlers

_log = logging.getLogger("market_svc.scheduler")

RTH_INTERVAL_SEC = 2
OFFHOURS_INTERVAL_SEC = 5   # futures trade ~24h → keep off-hours snappy

_CT = ZoneInfo("America/Chicago")
_RTH_START = (8, 30)
_RTH_END = (15, 0)
# Keep in sync with the other service schedulers + webgui/alerts.py. Update yearly.
_HOLIDAYS = {
    _date(2026, 1, 1), _date(2026, 1, 19), _date(2026, 2, 16), _date(2026, 4, 3),
    _date(2026, 5, 25), _date(2026, 6, 19), _date(2026, 7, 3), _date(2026, 9, 7),
    _date(2026, 11, 26), _date(2026, 12, 25),
    _date(2027, 1, 1), _date(2027, 1, 18), _date(2027, 2, 15), _date(2027, 3, 26),
    _date(2027, 5, 31), _date(2027, 6, 18), _date(2027, 7, 5), _date(2027, 9, 6),
    _date(2027, 11, 25), _date(2027, 12, 24),
}


def _is_rth(now):
    if now.weekday() >= 5 or now.date() in _HOLIDAYS:
        return False
    return _time(*_RTH_START) <= now.time() <= _time(*_RTH_END)


def poll_interval(now=None):
    """Seconds until the next poll — fast during RTH, slow off-hours (pure)."""
    now = now or _dt.datetime.now(_CT)
    return RTH_INTERVAL_SEC if _is_rth(now) else OFFHOURS_INTERVAL_SEC


SUMMARY_RTH_SEC = 20 * 60       # refresh the Claude verdict every ~20 min during RTH
SUMMARY_OFFHOURS_SEC = 60 * 60  # ~hourly off-hours


def summary_due(has_run, secs_since, *, now=None):
    """Whether to regenerate the Claude verdict this cycle (pure).

    ``has_run`` is a True/None sentinel — None until the first run (→ always due).
    Once it has run, fire when ``secs_since`` the last run exceeds the RTH/off-hours
    interval.
    """
    if has_run is None:
        return True
    now = now or _dt.datetime.now(_CT)
    threshold = SUMMARY_RTH_SEC if _is_rth(now) else SUMMARY_OFFHOURS_SEC
    return secs_since >= threshold


async def loop(bus) -> None:
    """Poll → publish → (periodic Claude summary) → sleep, forever. Never raises out."""
    loop_ = asyncio.get_running_loop()
    summary_started = None
    secs_since_summary = 0.0
    while True:
        interval = poll_interval()
        try:
            payload = await loop_.run_in_executor(None, compute.collect, bus)
            await loop_.run_in_executor(None, handlers.publish, bus, payload)
            if summary_due(summary_started, secs_since_summary):
                sent = bus.cache_get("cache:sentiment:composite")
                sent_payload = sent.payload if sent else {}
                summary = await loop_.run_in_executor(
                    None, compute.generate_summary, payload, sent_payload)
                await loop_.run_in_executor(None, handlers.publish_summary, bus, summary)
                summary_started = True
                secs_since_summary = 0.0
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the scheduler die.
            _log.exception("market poll cycle failed")
        await asyncio.sleep(interval)
        secs_since_summary += interval
