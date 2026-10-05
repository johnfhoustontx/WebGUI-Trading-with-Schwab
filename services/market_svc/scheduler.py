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

Each newly published report is also handed to options_svc for X
(``x_post_report`` on ``cmd:options``). A service restart re-enqueues the
current report; options_svc's report-identity dedup and 45-minute age gate
make that a no-op.
"""
import asyncio
import datetime as _dt
import logging
import time
from datetime import time as _time
from zoneinfo import ZoneInfo

from services import _degrade, _heartbeat
from services.market_svc import compute, handlers, market_read, report_summary
from shared import market_calendar as mc
from shared import market_read_config
from shared.numeric import finite

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
    and re-reading them every 3 s would log a warning every 3 s.

    Each newly published report is also handed to options_svc
    (``x_post_report`` on ``cmd:options``, with latest.html's mtime) to post
    to X. That hand-off is optional: a failed enqueue logs a warning and the
    summary still stands. ``last_stamp`` starts at None on every service
    start, so a restart re-enqueues the current report; options_svc's
    report-identity dedup and 45-minute age gate make that a no-op."""
    kw = {} if reports_dir is None else {"reports_dir": reports_dir}
    stamp = report_summary.report_stamp(**kw)
    if stamp is None or stamp == last_stamp:
        return last_stamp
    payload = report_summary.read_report(**kw)
    if payload is None:
        return stamp
    handlers.publish_summary(bus, payload)
    try:
        mtime = (reports_dir or report_summary.REPORTS_DIR).joinpath("latest.html").stat().st_mtime
        bus.enqueue_command("cmd:options", {"type": "x_post_report",
                                            "args": {"report": payload, "mtime": mtime}})
    except Exception:  # noqa: BLE001 -- the X post is optional; the summary is not
        _log.warning("x report enqueue failed", exc_info=True)
    return stamp


# ── the Desk's Market read ───────────────────────────────────────────────────
# The four views a reading is built from. Every one is rewritten on a clock
# while its publisher is alive (the dashboard every poll; the other three every
# minute or faster, and an unchanged write still refreshes the view's stamp), so
# an OLD one means its publisher has stopped and it is judged by AGE.
READ_INPUTS = {
    "dashboard": handlers.CACHE,
    "matrix": "cache:options:matrix",
    "gex_status": "cache:options:gex_status",
    "sides": "cache:options:flow_sides",
}
# ...and this one by the session DATE it carries as well: yesterday's tally,
# republished by nothing, is still not today's.
_READ_BY_DATE = ("sides",)


def _age_sec(stamp, wall):
    """Seconds since an ISO ``stamp``, or None when it cannot be read."""
    try:
        return wall - _dt.datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return None


def _read_inputs(bus, today, wall, cfg) -> dict:
    """The four source views, each None when it is missing, too old, or from
    another day: absent to the row that reads it, which then has no reading."""
    metas = bus.cache_metas(list(READ_INPUTS.values()))
    out, ages = {}, {}
    for name, key in READ_INPUTS.items():
        env = bus.cache_get(key)
        payload = env.payload if env else None
        stamp = (metas.get(key) or (None, None))[1] or getattr(env, "ts", None)
        age = ages[name] = _age_sec(stamp, wall)
        limit = cfg["dashboard_stale_after_sec" if name == "dashboard"
                    else "stale_after_sec"]
        if (not isinstance(payload, dict) or age is None or age > limit
                or (name in _READ_BY_DATE and payload.get("date") != today)):
            out[name] = None
        else:
            out[name] = payload
    status = out["gex_status"]
    since = finite(status.get("age_seconds")) if status else None
    if since is not None:
        # ``age_seconds`` was true when the status was written. Add how long
        # ago that was, so a status publisher that stops cannot leave the
        # collector looking as fresh as the day it died.
        out["gex_status"] = {**status,
                             "age_seconds": since + max(0.0, ages["gex_status"])}
    return out


def _retraction(today, ts) -> dict:
    """What replaces the reading when the operator switches the Market read
    off: nothing to show, and marked as such, so no screen keeps the last one."""
    return {"enabled": False, "date": today, "ts": ts, "public": False}


def refresh_read(bus, state, now=None, wall=None):
    """Publish the Market read when a clock slot is due; return the slot, or None.

    ``state`` is the caller's dict and survives between calls. On its first use
    the published view is read back, so a restart neither publishes a slot twice
    nor loses the day's history. ``now`` is the market clock (which slot);
    ``wall`` is unix seconds (how old a source is). Never raises: a failure
    leaves the last reading up and the slot still owed, to be retried after
    ``retry_sec`` rather than on every three-second poll.

    The two switches apply BETWEEN slots too, because a switch the operator has
    turned off must not wait up to half an hour: ``enabled`` off replaces a
    reading that is up with a retraction, once; a changed ``public`` republishes
    the reading that is up under the new flag."""
    at = market_read.ct(now or _dt.datetime.now(_CT))
    retry = market_read_config.DEFAULTS["retry_sec"]
    try:
        if at.timestamp() < state.get("retry_at", 0):
            return None
        cfg = market_read_config.load()
        retry = cfg["retry_sec"]
        if not state.get("restored"):
            env = bus.cache_get(handlers.CACHE_READ)
            state["last"] = env.payload if env and isinstance(env.payload, dict) else None
            state["restored"] = True
        today, stamp = at.date().isoformat(), int(at.timestamp())
        last = state.get("last")
        # A reading is UP when the last thing published is one, not a retraction.
        up = isinstance(last, dict) and last.get("enabled") is not False
        if cfg.get("enabled") is not True:
            if up:
                # Published first, remembered second: a write that fails is
                # owed again, not forgotten.
                handlers.publish_read(bus, _retraction(today, stamp))
                state["last"] = _retraction(today, stamp)
            return None
        last_slot = last.get("slot") if up and last.get("date") == today else None
        slot = market_read.slot_due(at, last_slot, cfg["interval_min"])
        if slot is None:
            if up and (last.get("public") is True) != (cfg["public"] is True):
                flagged = {**last, "public": cfg["public"] is True}
                handlers.publish_read(bus, flagged)
                state["last"] = flagged
            return None
        wall = time.time() if wall is None else wall
        inputs = _read_inputs(bus, today, wall, cfg)
        reading = market_read.build(inputs, cfg, date=today, slot=slot,
                                    ts=stamp, previous=last if up else None)
        handlers.publish_read(bus, reading)
        state["last"] = reading
        return slot
    except Exception:
        _degrade.degraded("market.read")
        state["retry_at"] = at.timestamp() + retry
        return None


async def loop(bus) -> None:
    """Poll → publish → the Market read, when a slot is due → (the report
    summary, when the report changed) → sleep."""
    loop_ = asyncio.get_running_loop()
    report_stamp = None
    read_state: dict = {}
    while True:
        _heartbeat.tick()
        interval = poll_interval()
        try:
            payload = await loop_.run_in_executor(None, compute.collect, bus)
            await loop_.run_in_executor(None, handlers.publish, bus, payload)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the scheduler die.
            _log.exception("market poll cycle failed")
        # After the dashboard, so a reading taken on a slot sees this poll's
        # tiles. refresh_read itself never raises; the guard is for the hand-off
        # to the executor, which must not take the poll down with it.
        try:
            await loop_.run_in_executor(None, refresh_read, bus, read_state)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the Market read can't stop the poll.
            _log.exception("market read refresh failed")
        try:
            report_stamp = await loop_.run_in_executor(
                None, refresh_summary, bus, report_stamp)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a summary failure can't stop the poll.
            _log.exception("market report summary refresh failed")
        await asyncio.sleep(interval)
