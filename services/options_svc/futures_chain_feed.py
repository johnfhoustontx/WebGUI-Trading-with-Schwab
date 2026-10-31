"""The /ES and /NQ chain feed's consumer: one captured snapshot -> store + cache.

The capture on the PC sends one snapshot per instrument per interval through
``tools/futures_chain_cli.py ingest``, which puts each on ``cmd:futures_chain``.
This module is that stream's handler (``make_app``'s ``extra_consumers``), on a
loop of its own. Nothing else runs on the stream: the commands are ``snapshot``
and ``pc_status`` and nothing reaches a paper book. It never calls Schwab or the
proxy, and it never touches the collector's symbol list: the chain it is handed
is the only chain these symbols have.

A snapshot ends in exactly one of three ways, and the status view
(``futures_chain.STATUS_KEY``) says which, per instrument, in whole words:

* dropped without comment -- an older or repeated capture than the newest stored
  (the stream can deliver late; a late one never replaces a newer one);
* refused, with a reason -- stale, from the future, not valid, failing the
  adapter's checks, or half filled against the capture before it;
* written -- the five views into ``gex_history.db`` at the capture's own minute,
  the chain into the cache for the page, and the frozen copy while the capture
  is far enough from expiry.

The checks that compare one capture with the one before it (order, contract
count, repeated values) read the previous capture's figures from the STATUS VIEW
in Redis, not from module state: a service restart must not make the next
half-filled capture look like the first.

Design: docs/plans/2026-10-31-futures-0dte-chain-feed-design.md, sections 5.6
and 5.9. Imports nothing from ``compute`` (which has a line ceiling).
"""
from __future__ import annotations

import logging
import math
import sys
import time

from pydantic import ValidationError

from services import _degrade
from services.options_svc.collect_derive import minute_of
from services.options_svc.futures_chain_adapter import Refused, build_chain
from shared import futures_chain, futures_chain_config
from shared.contracts.futures_chain import FuturesChainSnapshot
from shared.raw_minute import RawMinute

log = logging.getLogger(__name__)

# The swallowed-exception counter this feed reports under (``/health``).
AREA = "options.futures_chain"

# The longest reason text kept from the PC: it is shown on the Status page.
_MAX_REASON = 300

# What a status entry holds before the instrument has had a capture accepted.
_BLANK = {"captured_at": None, "expiry": None, "listed": None, "with_gamma": None,
          "parity_gap": None, "reason": "", "repeats": 0, "refused_at": None,
          "price": None, "activity": None}


def _now() -> float:
    """The current time, epoch seconds. A function so tests can pin the clock."""
    return time.time()


def _engines():
    """The options-scanner modules, imported lazily (they live in a hyphenated
    folder the service puts on ``sys.path``)."""
    import repo_paths
    path = str(repo_paths.OPTIONS_SCANNER)
    if path not in sys.path:
        sys.path.insert(0, path)
    import gex_collector
    import gex_history_db
    from gamma_tool import GammaEngine
    return gex_collector, gex_history_db, GammaEngine


# ── the status view ──────────────────────────────────────────────────────────

def _read_status(bus) -> dict:
    envelope = bus.cache_get(futures_chain.STATUS_KEY)
    payload = envelope.payload if envelope is not None else None
    return dict(payload) if isinstance(payload, dict) else {}


def _entry(status: dict, symbol: str) -> dict:
    """``symbol``'s status entry, always with every field."""
    stored = status.get(symbol)
    return {**_BLANK, **(stored if isinstance(stored, dict) else {})}


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _span(seconds: float) -> str:
    """A length of time in whole words: ``45 seconds``, ``20 minutes``."""
    seconds = max(0, int(round(seconds)))
    if seconds < 90:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = int(round(seconds / 60))
    if minutes < 120:
        return f"{minutes} minutes"
    return f"{round(seconds / 3600, 1):g} hours"


def _refuse(bus, status: dict, symbol: str, reason: str, now: float) -> None:
    """Record why this instrument's snapshot was not used, keeping what the last
    good one said about the rest."""
    entry = _entry(status, symbol)
    entry["reason"] = reason
    entry["refused_at"] = now
    status[symbol] = entry
    bus.cache_set(futures_chain.STATUS_KEY, status)
    log.warning("%s snapshot not used: %s", symbol, reason)


# ── the cache ────────────────────────────────────────────────────────────────

def latest(bus, symbol):
    """``{"chain", "captured_at", "expiry", "hours_left"}`` of ``symbol``'s newest
    capture, or ``None`` when it has none. ``expiry`` is an ISO date."""
    envelope = bus.cache_get(futures_chain.chain_key(symbol))
    return dict(envelope.payload) if envelope is not None else None


def frozen(bus, symbol):
    """The same, for the last capture taken at least ``frozen_before_expiry_min``
    before expiry (what the by-strike charts keep on screen between sessions)."""
    envelope = bus.cache_get(futures_chain.frozen_key(symbol))
    return dict(envelope.payload) if envelope is not None else None


# ── one snapshot ─────────────────────────────────────────────────────────────

def _activity(snapshot: FuturesChainSnapshot) -> float:
    """Volume plus open interest over every row: a number that moves whenever a
    contract trades, and sits still only when the feed does."""
    return sum((row.volume or 0.0) + (row.open_int or 0.0) for row in snapshot.contracts)


def _store(adapted) -> bool:
    """Write the capture's five views at its own minute, on a fresh engine (the
    engine keeps the last expiry it saw, so it is never shared). ``True`` when the
    gamma view landed: ``derive_minute`` logs and skips a chain it cannot price
    rather than raising."""
    gex_collector, gex_history, engine_class = _engines()
    minute = minute_of(adapted.captured_at)
    raw = RawMinute(ts=minute, chains=[(adapted.symbol, adapted.chain)],
                    hours_left={adapted.symbol: adapted.hours_left})
    conn = gex_history.connect()
    try:
        gex_history.init_schema(conn)
        gex_collector.derive_minute(engine_class(), conn, raw)
        conn.commit()
        return conn.execute("SELECT 1 FROM snapshots WHERE symbol = ? AND view = 'gex' "
                            "AND ts = ?", (adapted.symbol, minute)).fetchone() is not None
    finally:
        conn.close()


def _publish(bus, adapted, capture: dict) -> None:
    payload = {"chain": adapted.chain, "captured_at": adapted.captured_at,
               "expiry": adapted.expiry.isoformat(), "hours_left": adapted.hours_left}
    bus.cache_set(futures_chain.chain_key(adapted.symbol), payload)
    close = futures_chain.session_bounds(adapted.expiry)[1]
    if adapted.captured_at <= close - capture["frozen_before_expiry_min"] * 60:
        bus.cache_set(futures_chain.frozen_key(adapted.symbol), payload)


def _symbol_of(args):
    """The internal symbol an unreadable payload claims, when it names a
    configured instrument; else ``None``."""
    root = args.get("instrument") if isinstance(args, dict) else None
    spec = futures_chain_config.instruments().get(root) if isinstance(root, str) else None
    return spec["symbol"] if spec else None


def _snapshot(bus, args) -> None:
    now = _now()
    status = _read_status(bus)
    try:
        snapshot = FuturesChainSnapshot.model_validate(args)
    except ValidationError:
        _degrade.degraded(AREA, detail="a snapshot that does not match the contract")
        symbol = _symbol_of(args)
        if symbol:
            _refuse(bus, status, symbol, "The snapshot could not be read: it does "
                    "not match the agreed layout.", now)
        return
    spec = futures_chain_config.instruments().get(snapshot.instrument)
    if spec is None:
        _degrade.degraded(AREA, detail=f"unknown instrument {snapshot.instrument!r}",
                          exc_info=False)
        return
    symbol = spec["symbol"]
    capture = futures_chain_config.capture()
    taken = snapshot.captured_at

    # The age gate is also the replay guard: a consumer group created at id 0 is
    # handed the stream's whole history, and none of it may read as current.
    if taken < now - capture["max_age_sec"]:
        _refuse(bus, status, symbol, f"Dropped: the snapshot was {_span(now - taken)} "
                f"old when it arrived (the limit is {_span(capture['max_age_sec'])}).", now)
        return
    if taken > now + capture["max_future_skew_sec"]:
        _refuse(bus, status, symbol, f"Dropped: the snapshot is stamped {_span(taken - now)} "
                "ahead of the server's clock. Check the clock on the capturing PC.", now)
        return

    previous = _entry(status, symbol)
    if _number(previous["captured_at"]) and taken <= previous["captured_at"]:
        log.info("%s capture at %s is not newer than the stored one; dropped", symbol, taken)
        return

    try:
        adapted = build_chain(snapshot)
    except Refused as refusal:
        _refuse(bus, status, symbol, f"Refused: {refusal.reason}", now)
        return
    except Exception:
        _degrade.degraded(AREA, detail=symbol)
        _refuse(bus, status, symbol, "Refused: the snapshot could not be checked "
                "(see the options service log).", now)
        return

    same_session = previous["expiry"] == adapted.expiry.isoformat()

    # A capture that reads far fewer contracts than the one before it is a sheet
    # that had not filled; the page keeps the last good chain instead.
    share = capture["min_fill_fraction"]
    if (same_session and _number(previous["listed"])
            and adapted.listed < share * previous["listed"]):
        _refuse(bus, status, symbol, f"Refused: the capture read {adapted.listed} "
                f"contracts, fewer than {round(share * 100):g} percent of the "
                f"{previous['listed']} before it. ThinkorSwim may not have finished "
                "filling the sheet.", now)
        return

    # The run of captures with the same price and the same volume and open
    # interest: a live future does not hold one price for five minutes.
    price, activity = snapshot.underlying_price, _activity(snapshot)
    unchanged = (same_session and previous["price"] == price
                 and previous["activity"] == activity)
    repeats = max(int(previous["repeats"] or 0), 1) + 1 if unchanged else 0

    try:
        stored = _store(adapted)
    except Exception:
        _degrade.degraded(AREA, detail=f"{symbol} store write")
        _refuse(bus, status, symbol, "Refused: the chain could not be written to "
                "the store (see the options service log).", now)
        return
    if not stored:
        _refuse(bus, status, symbol, "Refused: the chain could not be turned into "
                "gamma figures (see the options service log).", now)
        return

    _publish(bus, adapted, capture)
    entry = _entry(status, symbol)
    entry.update(
        captured_at=taken, expiry=adapted.expiry.isoformat(), listed=adapted.listed,
        with_gamma=adapted.with_gamma, parity_gap=adapted.parity_gap, repeats=repeats,
        price=price, activity=activity, reason="")
    if repeats >= capture["frozen_repeats"]:
        entry["reason"] = (f"The prices and counts are not changing: the last {repeats} "
                           "captures read exactly the same values. ThinkorSwim may have "
                           "stopped updating.")
    status[symbol] = entry
    bus.cache_set(futures_chain.STATUS_KEY, status)


# ── the capture machine's own report ─────────────────────────────────────────

def _pc_status(bus, args) -> None:
    """The PC says why it is not capturing (ThinkorSwim logged out, Excel would
    not start) or that it is. Kept apart from the instruments' entries."""
    reason = args.get("reason") if isinstance(args, dict) else None
    reason = reason.strip()[:_MAX_REASON] if isinstance(reason, str) else ""
    at = args.get("at") if isinstance(args, dict) else None
    at = float(at) if _number(at) and math.isfinite(at) else _now()
    status = _read_status(bus)
    status["pc"] = {"reason": reason, "at": at}
    bus.cache_set(futures_chain.STATUS_KEY, status)


def handle(bus, command) -> None:
    """Answer one ``cmd:futures_chain`` command. A command that raises is
    dead-lettered by the consumer loop, not retried."""
    args = command.args if isinstance(command.args, dict) else {}
    if command.type == "snapshot":
        _snapshot(bus, args)
    elif command.type == "pc_status":
        _pc_status(bus, args)
    else:
        log.warning("unknown futures_chain command %r ignored", command.type)
