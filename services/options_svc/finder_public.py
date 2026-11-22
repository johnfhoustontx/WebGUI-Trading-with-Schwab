"""The public Strategy Finder's worker: one visitor request -> one scan, or a refusal.

A visitor on ``live.neuralstrike.co`` types a symbol; the public process puts
``{"symbol": <SYMBOL>}`` on ``cmd:finder_public`` (``webgui/bus_client.
request_public_scan``, the only write that origin may make). This module is the
handler for that stream. It runs on its OWN consumer loop (``make_app``'s
``extra_consumers``), so a public scan never queues ahead of the owner's paper
creates or Calculator loads on ``cmd:options``, and never waits behind them.

Every request ends in exactly one outcome from ``shared.public_scan.OUTCOMES``,
recorded per symbol in ``cache:options:finder_public_status`` so the page can
word it. The refusals run in this order, and every one of them is decided
BEFORE any Schwab call:

1. ``invalid``    the symbol fails the app's ticker allow-list;
2. ``expired``    the request waited longer than ``max_wait_sec``, or carries a
                  stamp from the future - this is also the replay guard, since a
                  consumer group created at id 0 re-delivers the stream's whole
                  backlog on a fresh start;
3. ``cached``     a result younger than ``result_ttl_min`` exists: served as is;
4. ``no_options`` this symbol was found to have no listed options within
                  ``negative_ttl_min`` - answered from memory, so junk tickers
                  cannot spend the day's budget one minute at a time;
   ``duplicate``  this symbol was scanned under ``dedup_sec`` ago and produced
                  no result (a cache hit never arms this - see ``_last_scan``);
5. ``closed``     outside ``[windows.finder_public]``;
6. ``budget``     the day's ``daily_budget`` of scans is spent.

Then the scan: ``handlers.finder_payload`` with the pinned public filters and
``ask_if_large=False``. It ends ``scanned``, ``no_options`` (no chain, no quote,
or no expirations in range) or ``error``. Only ``scanned`` writes a result, so a
failed rescan never replaces a good earlier one.

Design: docs/plans/2026-09-21-public-strategy-finder-roadmap.md, Phase 1.
"""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from services import _degrade
from services.options_svc import handlers
from shared import market_calendar
from shared import public_scan
from shared.symbols import clean_symbol

log = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")
WINDOW = "finder_public"

# How many symbols' last outcomes the status view keeps. Visitors choose the
# symbols, so the map must be bounded; the oldest entries go first.
LAST_KEEP = 200

# A request stamped this far in the FUTURE is refused as expired. A little
# skew between hosts is normal; a stamp minutes ahead would never age out of
# the replay guard.
FUTURE_SKEW_SEC = 30

# A ``busy`` marker older than this is a crash's leftover, not a running scan:
# the longest measured whole-chain scan is ~40 s, and a public one is capped at
# 90 days of expirations.
BUSY_STALE_SEC = 600


def _now() -> dt.datetime:
    """The current time in CT. A function so tests can pin the clock."""
    return dt.datetime.now(CT)


def _age_s(iso, now) -> float | None:
    """Seconds between an ISO stamp and ``now``, or None when it cannot be read."""
    if not iso:
        return None
    try:
        when = dt.datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return (now - when).total_seconds()


def _read_status(bus, now) -> dict:
    """The status view, rolled over to a fresh count on a new CT day."""
    today = now.date().isoformat()
    env = bus.cache_get(public_scan.STATUS_KEY)
    status = dict(env.payload) if env and isinstance(env.payload, dict) else {}
    last = status.get("last")
    status["last"] = dict(last) if isinstance(last, dict) else {}
    if status.get("date") != today:
        status.update(date=today, scans_today=0, invalid_today=0, busy=None)
    status.setdefault("scans_today", 0)
    status.setdefault("invalid_today", 0)
    busy = status.get("busy")
    since = _age_s(busy.get("since"), now) if isinstance(busy, dict) else None
    if since is None or since > BUSY_STALE_SEC:
        status["busy"] = None
    return status


def _write_status(bus, status) -> None:
    lim = public_scan.limits()
    status["daily_budget"] = lim["daily_budget"]
    status["scans_left"] = max(0, lim["daily_budget"] - status["scans_today"])
    start, end = market_calendar.window_bounds(WINDOW)
    status["window"] = {"start": start.strftime("%H:%M"), "end": end.strftime("%H:%M"),
                        "tz": "CT"}
    # Oldest first out: dicts keep insertion order and _record re-inserts.
    last = status["last"]
    while len(last) > LAST_KEEP:
        del last[next(iter(last))]
    version = bus.cache_set(public_scan.STATUS_KEY, status)
    bus.publish(public_scan.STATUS_EVENT, {"version": version})


def _record(status, symbol, outcome, now, *, scanned=False) -> None:
    prev = status["last"].pop(symbol, None) or {}
    rec = {"outcome": outcome, "at": now.isoformat()}
    if scanned:
        rec["scan_at"], rec["scan_outcome"] = now.isoformat(), outcome
    elif prev.get("scan_at"):
        rec["scan_at"], rec["scan_outcome"] = prev["scan_at"], prev.get("scan_outcome")
    status["last"][symbol] = rec


def _last_scan(status, symbol):
    """``(when, outcome)`` of the last scan this symbol cost, or ``(None, None)``.
    Refusals and cache hits leave it alone, so asking again for a symbol with a
    good result reads ``cached``, never 'just checked, try again in a minute'."""
    rec = status["last"].get(symbol) or {}
    return rec.get("scan_at"), rec.get("scan_outcome")


def _fresh_result(bus, symbol, now, ttl_min) -> bool:
    env = bus.cache_get(public_scan.result_key(symbol))
    if env is None or not isinstance(env.payload, dict):
        return False
    age = _age_s(env.payload.get("scanned_at"), now)
    return age is not None and 0 <= age < ttl_min * 60


def _outcome_of(payload) -> str:
    if payload.get("error"):
        return "error"
    if not payload.get("signals") and (
            payload.get("chain_missing") or payload.get("spot") is None
            or payload.get("no_expiries_in_range")):
        return "no_options"
    return "scanned"


def trim_for_public(payload, rows_per_type):
    """The payload with only the best ``rows_per_type`` ideas of each strategy
    type, the rest added to ``not_shown`` (the page's "N lower-scoring ideas
    not shown"). The same ranking as the service's own per-type limit
    (``compute._keep_best_per_type``), so the public list is the top of the
    private one, never a different cut."""
    from services.options_svc import compute
    signals = [s for s in (payload.get("signals") or []) if s]
    kept, dropped = compute._keep_best_per_type(signals, rows_per_type)
    return {**payload, "signals": kept,
            "not_shown": (payload.get("not_shown") or 0) + dropped}


def warm(bus) -> int:
    """Queue the morning warm-up symbols on the public stream; return how many.

    Through the stream, not a direct scan, so each one meets the same window,
    budget, cache and dedup rules as a visitor's request - a warm-up can never
    spend what a visitor could not."""
    n = 0
    for symbol in public_scan.warm_symbols():
        command = public_scan.request_command(symbol)
        if command is not None:
            bus.enqueue_command(public_scan.STREAM, command)
            n += 1
    return n


def handle(bus, command) -> None:
    """Answer one ``cmd:finder_public`` request. Never raises."""
    now = _now()
    lim = public_scan.limits()
    args = getattr(command, "args", None) or {}
    symbol = clean_symbol(args.get("symbol") if isinstance(args, dict) else None)
    status = _read_status(bus, now)

    if symbol is None:
        # No symbol to key an outcome on; count it, log it, spend nothing.
        status["invalid_today"] += 1
        log.info("public scan refused: invalid symbol %r", args.get("symbol")
                 if isinstance(args, dict) else args)
        _write_status(bus, status)
        return

    age = _age_s(getattr(command, "ts", None), now)
    scan_at, scan_outcome = _last_scan(status, symbol)
    since_scan = _age_s(scan_at, now)
    if age is not None and (age > lim["max_wait_sec"] or age < -FUTURE_SKEW_SEC):
        outcome = "expired"
    elif _fresh_result(bus, symbol, now, lim["result_ttl_min"]):
        outcome = "cached"
    elif (scan_outcome == "no_options" and since_scan is not None
          and since_scan < lim["negative_ttl_min"] * 60):
        outcome = "no_options"
    elif since_scan is not None and since_scan < lim["dedup_sec"]:
        outcome = "duplicate"
    elif not market_calendar.in_window(WINDOW, now):
        outcome = "closed"
    elif status["scans_today"] >= lim["daily_budget"]:
        outcome = "budget"
    else:
        outcome = None

    if outcome is not None:
        _record(status, symbol, outcome, now)
        _write_status(bus, status)
        return

    # The scan. Budget and busy are written BEFORE it, so the page sees the
    # queue move and the budget cannot be overspent by a crash mid-scan.
    status["scans_today"] += 1
    status["busy"] = {"symbol": symbol, "since": now.isoformat()}
    _write_status(bus, status)
    outcome = _scan_and_publish(bus, symbol, now, lim)
    status = _read_status(bus, now)
    status["busy"] = None
    _record(status, symbol, outcome, now, scanned=True)
    _write_status(bus, status)


def _scan_and_publish(bus, symbol, now, lim) -> str:
    """Run the pinned scan and write its result; return the outcome. Never raises:
    a failure anywhere in it - the scan, or the write after a good scan - is an
    ``error``, and the caller still clears ``busy`` and records it."""
    pin = public_scan.scan_pin()
    params = {**handlers._SWING_DEFAULTS, **pin, "symbol": symbol}
    try:
        payload = handlers.finder_payload(bus, params, echo_args=pin,
                                          ask_if_large=False,
                                          degrade_area="options.finder_public")
        outcome = _outcome_of(payload)
        if outcome == "scanned":
            payload = trim_for_public(payload, public_scan.rows_per_type())
            payload = {**payload, "public": True, "scanned_at": now.isoformat()}
            version = bus.cache_set(public_scan.result_key(symbol), payload,
                                    ttl=lim["result_keep_hours"] * 3600)
            bus.publish(public_scan.result_event(symbol), {"version": version})
        return outcome
    except Exception:  # noqa: BLE001 - one visitor's scan must not kill the loop
        _degrade.degraded("options.finder_public", detail=symbol)
        return "error"
