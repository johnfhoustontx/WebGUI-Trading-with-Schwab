"""The trade service's one scheduled job: the nightly earnings-calendar pull.

Trade analysis stays **on demand** — the GUI enqueues an ``analyze`` command and
the consumer answers it. This module exists for the one thing the service owes
the rest of the stack on a clock, and it is deliberately the smallest scheduler
in the repo: a tick, one gate, one call.

**Why it exists.** ``earnings_calendar.refresh`` was reachable only LAZILY, from
``compute._enrich_earnings_date`` inside an analyze — a user action. So the
forward calendar went stale whenever nobody opened Trade Analyzer, and it stopped
being only that page's store when the 30-45 DTE income window started gating on
it through ``shared/earnings.py`` at 08:45 CT each trading morning. Vendor
coverage decays with distance (measured on a live key: ~2,125 rows for the
current month, ~1,032 two months out, 11 nine months out), so a calendar that is
weeks old fails open on exactly the names the gate is for.

**Why a slot and not a systemd timer.** The stack does run timers
(``trading-prod-backup.timer``, ``trading-prod-stream.timer``, both emitted by
``deploy/systemd/generate_units.py``), so a timer was a real alternative. Two
reasons it lost. ``[slots]`` in ``config/sessions.toml`` is this repo's stated
convention for "a named clock mark a service fires at, once per day", and this
is exactly that — an operator changing the hour should find it beside
``income``, which consumes it, not in a generated unit file. And a timer would
run in its own process, **outside `_schedulers_enabled()`** — the single
mechanism that stops a suppressed environment issuing vendor calls at rest. Dev
would have quietly acquired a nightly Alpha Vantage request that no flag could
turn off.

The blocking pull runs in the default executor, as every other service's branch
does: it is HTTP plus a bulk upsert of thousands of rows, and the loop it would
otherwise stall is shared with the command consumer answering analyze requests.
"""
import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

from services.trade_svc import compute
from shared import market_calendar as mc

log = logging.getLogger(__name__)

_CT = ZoneInfo("America/Chicago")

# One minute. The slot has an hour of grace, so the tick rate only decides how
# promptly it fires inside that hour — and this loop does nothing else, so there
# is no reason to wake more often.
TICK_SEC = 60

# config/sessions.toml [slots.earnings] — 20:00 CT. Resolved once at import, the
# same way options_svc/sentiment_svc resolve theirs, so the gate's target IS the
# config's rather than a second copy free to drift.
_EARNINGS_AT = (lambda t: (t.hour, t.minute))(mc.slot_times("earnings")["at"])
_EARNINGS_GRACE_MIN = mc.slot_grace_min("earnings")


def _market_now():
    """Wall clock in CT. Isolated so the gate is testable without waiting."""
    return dt.datetime.now(_CT)


def earnings_slot_due(now, ran_slots):
    """``"at"`` when the nightly calendar pull is due, else None.

    The house once-per-day-with-grace shape (mirrors
    ``options_svc.scheduler.income_slot_due``): fires when
    ``target <= now < target + grace`` and that ``(date, slot)`` is not already
    in ``ran_slots``, which the caller records. The grace tolerates a missed tick
    or an evening restart; past it the slot is simply skipped, because a pull at
    03:00 would spend the day's vendor request on the previous evening's slot and
    then silence the real one.

    ⚠ **Unlike every other slot gate in this repo, this one does NOT check
    ``is_trading_day``.** The subject is not market data: Alpha Vantage's file is
    a research product that changes whenever an issuer announces or moves a date,
    and those changes accumulate over a weekend exactly as they do overnight.
    Gated on the trading calendar, Monday's 08:45 CT income scan would read a
    calendar pulled Friday at 20:00 — ~61 h old — where a Sunday-evening pull
    makes it ~13 h old; the holiday case is worse and less obvious, since
    skipping Thanksgiving Thursday leaves Friday's half-day scan on Wednesday's
    data. The cost of the divergence is two extra bulk requests a week against a
    25-a-day allowance this job uses one of. Pure; the caller owns the clock.
    """
    day = now.date().isoformat()
    if (day, "at") in ran_slots:
        return None
    target = now.replace(hour=_EARNINGS_AT[0], minute=_EARNINGS_AT[1],
                         second=0, microsecond=0)
    if target <= now < target + dt.timedelta(minutes=_EARNINGS_GRACE_MIN):
        return "at"
    return None


async def loop(bus):
    """Tick the earnings slot forever. ``bus`` is unused — this job writes a
    SQLite store, not a cache view, and publishes nothing for the GUI to repaint;
    the parameter is the scaffold's scheduler signature.

    Guarded so one bad evening cannot end the schedule: an exception escaping
    here would kill the task, and the scaffold's supervisor would restart it only
    ``scheduler_max_restarts`` times before reporting the scheduler dead — one
    vendor outage turning into an indefinitely stale calendar."""
    ran = set()   # (date, slot) of fired pulls — see earnings_slot_due
    while True:
        try:
            now = _market_now()
            slot = earnings_slot_due(now, ran)
            if slot:
                # Recorded BEFORE the call, not after: a pull that raises has
                # still spent the vendor request (Alpha Vantage counts a failed
                # call), so retrying it inside the same grace window would burn
                # the budget an hour at a time for nothing.
                ran.add((now.date().isoformat(), slot))
                loop_ = asyncio.get_running_loop()
                ok = await loop_.run_in_executor(
                    None, compute.refresh_earnings_calendar)
                log.info("nightly earnings-calendar refresh %s",
                         "ok" if ok else "degraded")
        except Exception:
            log.exception("earnings calendar refresh tick degraded")
        await asyncio.sleep(TICK_SEC)
