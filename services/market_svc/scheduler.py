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
from dataclasses import dataclass
from datetime import time as _time
from zoneinfo import ZoneInfo

from services.market_svc import compute, handlers
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


# The summary is written ON CHANGE, not on a clock (2026-09-10): the Desk's
# MARKET SUMMARY frame and the ticker both show it, and a clock refresh paid for
# ~25 calls a day, most of them overnight rewrites of a market that had not
# moved. Now a sentence is written only when the readings' fingerprint moves
# (compute.summary_fingerprint), never twice within the gap, and never past the
# daily ceiling — so a reading flapping at a band boundary cannot run up cost.
SUMMARY_MIN_GAP_SEC = 10 * 60
SUMMARY_DAILY_CAP = 30


@dataclass(frozen=True)
class SummaryGate:
    fingerprint: tuple | None = None   # what the last sentence was written from
    last_call: float | None = None     # monotonic seconds of the last call
    day: _dt.date | None = None        # CT date the counter belongs to
    calls_today: int = 0


def summary_due(gate, fingerprint, *, now_mono, today):
    """Write a new sentence this poll? (pure)

    No — when there is nothing to summarize, when the readings have not changed
    since the last sentence, within ``SUMMARY_MIN_GAP_SEC`` of the last call, or
    once ``SUMMARY_DAILY_CAP`` calls have been made on ``today``. The first poll
    after a restart (empty gate) with readings present is always due."""
    if fingerprint is None:
        return False
    calls = gate.calls_today if gate.day == today else 0
    if calls >= SUMMARY_DAILY_CAP:
        return False
    if gate.fingerprint == fingerprint:
        return False
    if gate.last_call is not None and now_mono - gate.last_call < SUMMARY_MIN_GAP_SEC:
        return False
    return True


def record_summary(gate, fingerprint, *, now_mono, today):
    """The gate after a call is launched (pure) — a NEW gate, never mutated."""
    calls = gate.calls_today if gate.day == today else 0
    return SummaryGate(fingerprint=fingerprint, last_call=now_mono, day=today,
                       calls_today=calls + 1)


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
