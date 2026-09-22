"""The public Gamma page's worker: the HOT SET of visitor-picked symbols.

A visitor on ``live.neuralstrike.co/gamma`` picks a symbol; the public process
puts ``{"symbol": <SYMBOL>}`` on ``cmd:gamma_public`` (``webgui/bus_client.
request_public_gamma``). This module answers that stream on its own consumer
loop (``make_app``'s ``extra_consumers``) and holds the answer: a lease per
symbol, renewed by every request, that keeps the symbol in the hot set the
1-minute GEX branch publishes (``handlers.refresh_gamma_current``).

It spends NOTHING itself: no Schwab call, no snapshot build. A granted symbol
is first published on the NEXT tick, from the chain the collector fetches for
it anyway. That is why the tick takes its hot set ONCE, before the collect
(``begin_tick``): a symbol granted mid-tick was not in that collect's capture
set, so building it in the same tick would fetch its chain from Schwab.

Every request ends in one outcome from ``shared.public_gamma.OUTCOMES``, in
this order:

1. ``invalid``  not a ticker, or not on the dropdown list;
2. ``expired``  waited longer than ``max_wait_sec``, or stamped from the future
                -- also the replay guard for a consumer group created at id 0;
3. ``closed``   outside ``[windows.gamma_public]``;
4. ``live``     already in the hot set: the lease is renewed;
5. ``full``     the hot set holds ``cap`` symbols;
6. ``added``    granted.

$SPX, SPY and QQQ (``handlers.PUBLISHED_GAMMA_SYMBOLS``) are always published,
so a lease on one of them does not take a slot. It still matters: it adds the
history views the permanent publish leaves out (SPY and QQQ publish GEX only).

The hot set lives in THIS process's memory, behind a lock, because the consumer
thread and the scheduler's executor both touch it. A restart empties it; open
pages renew within ``renew_min``, so nobody watching loses a symbol for long.

Design: docs/plans/2026-09-21-public-gamma-any-symbol-roadmap.md, Phase 1.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from zoneinfo import ZoneInfo

from shared import market_calendar
from shared import public_gamma
from shared.symbols import clean_symbol

log = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")
WINDOW = "gamma_public"

# A request stamped this far in the FUTURE is refused as expired (host skew is
# normal; a stamp minutes ahead would never age out of the replay guard).
FUTURE_SKEW_SEC = 30

_LOCK = threading.Lock()
_LEASES: dict = {}          # SYMBOL -> lease end (aware datetime), grant order
_TICK: tuple = ()           # the hot set the current tick captured
# Each dropdown symbol's latest outcome, for the page: {SYMBOL: {outcome, at}}.
# Bounded by the dropdown list (an off-list symbol is ``invalid`` and never
# recorded), and it names only symbols from that public list.
_LAST: dict = {}


def _now() -> dt.datetime:
    """The current time in CT. A function so tests can pin the clock."""
    return dt.datetime.now(CT)


def reset() -> None:
    """Empty the hot set and the tick snapshot (test helper)."""
    global _TICK
    with _LOCK:
        _LEASES.clear()
        _LAST.clear()
        _TICK = ()


def _permanent() -> frozenset:
    from services.options_svc import handlers   # lazy: handlers imports this module
    return frozenset(s.upper() for s in handlers.PUBLISHED_GAMMA_SYMBOLS)


def _prune(now) -> bool:
    """Drop expired leases; return whether any went. Caller holds the lock."""
    gone = [s for s, until in _LEASES.items() if until <= now]
    for s in gone:
        del _LEASES[s]
    return bool(gone)


def hot_symbols(now=None) -> list:
    """The leased symbols, oldest grant first (permanent ones included)."""
    now = now or _now()
    with _LOCK:
        _prune(now)
        return list(_LEASES)


def begin_tick(bus=None, now=None) -> tuple:
    """Take the hot set for THIS tick, before its collect, and return it.

    The collect captures these symbols' chains and the same tick's refresh
    publishes exactly these, so a symbol granted after this call waits for the
    next tick instead of costing a Schwab fetch. Rewrites the status view when a
    lease ran out, so the page stops calling the symbol live."""
    global _TICK
    now = now or _now()
    with _LOCK:
        changed = _prune(now)
        _TICK = tuple(_LEASES)
        snapshot = _TICK
    if changed and bus is not None:
        _write_status(bus, now)
    return snapshot


def tick_symbols(now=None) -> tuple:
    """The hot set ``begin_tick`` captured for the running tick, less any
    symbol whose lease has since run out.

    The filter is what keeps a hot symbol's keys expiring. ``_TICK`` is only
    re-taken by a tick, and ticks stop at the window's end, so unfiltered it
    would name the last tick's symbols all night. The private page's own
    ``gamma_refresh`` reaches ``_gamma_pub_targets`` at any hour, so a symbol
    parked there and once picked by a visitor would have its public keys
    rewritten, TTL renewed, until morning (review of fb882da, 2026-09-21)."""
    now = now or _now()
    with _LOCK:
        _prune(now)
        return tuple(s for s in _TICK if s in _LEASES)


def allowed_symbols(bus) -> frozenset:
    """The dropdown's list, from the key published for the public page. When
    the key is cold (a fresh start), only the permanent symbols: a request is
    never validated against a list nobody published."""
    try:
        env = bus.cache_get(public_gamma.SYMBOLS_KEY)
        raw = (env.payload or {}).get("symbols") if env else None
    except Exception:  # noqa: BLE001 - a read failure refuses, never raises
        raw = None
    if not isinstance(raw, list) or not raw:
        return _permanent()
    return frozenset(s for s in (clean_symbol(x) for x in raw) if s)


def _age_s(iso, now) -> float | None:
    if not iso:
        return None
    try:
        when = dt.datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return (now - when).total_seconds()


def _write_status(bus, now) -> None:
    """The page's view: which symbols are live, the cap, the window."""
    cfg = public_gamma.hot()
    permanent = sorted(_permanent())
    with _LOCK:
        leased = list(_LEASES)
        last = {s: dict(r) for s, r in _LAST.items()}
    start, end = market_calendar.window_bounds(WINDOW)
    status = {
        "permanent": permanent,
        "hot": leased,
        "cap": cfg["cap"],
        "slots_used": sum(1 for s in leased if s not in permanent),
        "renew_min": public_gamma.renew_min(),
        "last": last,
        "window": {"start": start.strftime("%H:%M"), "end": end.strftime("%H:%M"),
                   "tz": "CT"},
        "updated": now.isoformat(),
    }
    version = bus.cache_set(public_gamma.STATUS_KEY, status)
    bus.publish(public_gamma.STATUS_EVENT, {"version": version})


def decide(bus, command, now) -> tuple:
    """``(outcome, symbol)`` for one request, granting or renewing its lease.
    Only ``added`` changes which symbols are hot."""
    args = getattr(command, "args", None) or {}
    symbol = clean_symbol(args.get("symbol") if isinstance(args, dict) else None)
    if symbol is None or symbol not in allowed_symbols(bus):
        return "invalid", symbol
    age = _age_s(getattr(command, "ts", None), now)
    if age is not None and (age > public_gamma.max_wait_sec()
                            or age < -FUTURE_SKEW_SEC):
        return "expired", symbol
    if not market_calendar.in_window(WINDOW, now):
        return "closed", symbol
    cfg = public_gamma.hot()
    until = now + dt.timedelta(minutes=cfg["lease_min"])
    permanent = _permanent()
    with _LOCK:
        _prune(now)
        if symbol in _LEASES:
            _LEASES[symbol] = until
            return "live", symbol
        used = sum(1 for s in _LEASES if s not in permanent)
        if symbol not in permanent and used >= cfg["cap"]:
            return "full", symbol
        _LEASES[symbol] = until
        return "added", symbol


def handle(bus, command) -> None:
    """Answer one ``cmd:gamma_public`` request. Never raises."""
    now = _now()
    try:
        outcome, symbol = decide(bus, command, now)
    except Exception:  # noqa: BLE001 - one visitor's request must not kill the loop
        log.exception("gamma_public request degraded")
        return
    log.info("public gamma %s: %s", symbol, outcome)
    changed = False
    if outcome != "invalid":
        # Recorded per dropdown symbol, so a page can word ITS request: "full"
        # and "closed" leave the hot set untouched and would otherwise be
        # invisible. Written with a FRESH stamp every time, not only when the
        # outcome changes: the page trusts only a record at least as new as its
        # own request, so a second "closed" left unwritten kept the first's
        # stamp and the page fell back to "live" after hours (seen on prod,
        # 2026-09-21 22:12). Only a renewal (live after live) is skipped - it
        # is the frequent one, and it changes nothing a page words.
        with _LOCK:
            prev = (_LAST.get(symbol) or {}).get("outcome")
            _LAST[symbol] = {"outcome": outcome, "at": now.isoformat()}
        changed = not (outcome == "live" and prev == "live")
    # Written when anything a page reads changed, and once when the view is
    # missing (a fresh start), so a page never waits on a status nobody wrote.
    if changed or outcome == "added" or not _status_exists(bus):
        try:
            _write_status(bus, now)
        except Exception:  # noqa: BLE001
            log.exception("gamma_public status write degraded")


def _status_exists(bus) -> bool:
    try:
        return bus.cache_get(public_gamma.STATUS_KEY) is not None
    except Exception:  # noqa: BLE001
        return False
