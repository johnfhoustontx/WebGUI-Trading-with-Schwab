"""Market dashboard scheduler — poll the proxy, publish, repeat.

3 s cadence during regular trading hours; a slower 15 s off-hours/weekends and
60 s once the equity-index futures are also closed (deep weekend). It is NOT
throttled hard off-hours because the equity-index FUTURES (/ES, /NQ) trade
almost 24 h — they're the main thing moving after the cash close, so the
off-hours cadence keeps them visibly ticking (the cash indices/internals are
stale then anyway, and skip_unchanged means an unchanged payload costs
nothing). The market-hours gate mirrors the other services.

The Claude-written market summary is written ON THIS SAME LOOP, but only when
the readings' fingerprint has changed (see ``SummaryGate``/``summary_due``
below) — not on a clock, and no longer gated by the webgui ticker toggle
(retired 2026-09-10, when the summary began feeding the Desk as well as the
marquee).
"""
import asyncio
import dataclasses
import datetime as _dt
import logging
import time
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


async def _run_summary(loop_, bus, packet) -> bool:
    """Write the sentence + publish it, OFF the poll loop. Never raises.

    ``generate_summary`` returns None when the attempt FAILED (API error,
    timeout): publish nothing then, so the last good sentence stays on the Desk
    rather than being blanked — the Desk's "readings have changed" line already
    says when a sentence has been overtaken.

    Returns True once the summary has actually been published, False when the
    attempt failed (``generate_summary`` returned None, or anything raised) —
    ``loop()`` uses this to decide whether the fingerprint it recorded at
    launch may stand, or must be forgotten so the same readings are retried."""
    try:
        summary = await loop_.run_in_executor(None, compute.generate_summary, packet)
        if summary is not None:
            await loop_.run_in_executor(None, handlers.publish_summary, bus, summary)
            return True
        return False
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a summary failure can't affect the poll loop.
        _log.exception("market summary generation failed")
        return False


def _published(task) -> bool:
    """Did a finished summary task publish? False when it was cancelled, raised,
    or returned False (the Claude attempt failed)."""
    if task.cancelled() or task.exception() is not None:
        return False
    return task.result() is True


async def loop(bus) -> None:
    """Poll → publish → (a new summary when the readings changed) → sleep."""
    loop_ = asyncio.get_running_loop()
    gate = SummaryGate()
    summary_task = None
    while True:
        interval = poll_interval()
        try:
            payload = await loop_.run_in_executor(None, compute.collect, bus)
            await loop_.run_in_executor(None, handlers.publish, bus, payload)
            if summary_task is not None and summary_task.done():
                if not _published(summary_task):
                    # The attempt still counts toward the gap and the daily cap
                    # (record_summary ran at launch), but the reading was never
                    # written — forget its fingerprint so the SAME readings are
                    # retried once the gap has passed, instead of a transient
                    # failure freezing a stale sentence until the market moves.
                    gate = dataclasses.replace(gate, fingerprint=None)
                summary_task = None
            if summary_task is None:
                packet = await loop_.run_in_executor(
                    None, compute.read_summary_packet, bus)
                fp = compute.summary_fingerprint(packet)
                mono, today = time.monotonic(), _dt.datetime.now(_CT).date()
                if summary_due(gate, fp, now_mono=mono, today=today):
                    gate = record_summary(gate, fp, now_mono=mono, today=today)
                    # A BACKGROUND task: the call can take ~60 s (30 s timeout +
                    # a retry) and must not stall the 3 s poll.
                    summary_task = asyncio.create_task(
                        _run_summary(loop_, bus, packet))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the scheduler die.
            _log.exception("market poll cycle failed")
        await asyncio.sleep(interval)
