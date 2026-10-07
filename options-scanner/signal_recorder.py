"""Capture scanner signals into the signal tracking DB.

Capture is gated to the REGULAR cash session (08:30-15:00 CT). A signal booked
outside it records an entry the account could not have taken, and records it at
a price that is not real: Schwab pins a chain's ``underlyingPrice`` to the PRIOR
CLOSE outside regular hours -- the same freeze ``gex_collector._reanchor_spots``
corrects for GEX -- so a pre-open scan picks its strikes, deltas and credit off
yesterday's price, and the open then gaps away from every one of them.

The gate sits HERE, at the recorder, and not on the scan window, because those
are different questions. ``[windows.scan]`` is 08:00-15:15 CT and premarket
coverage there is deliberate: the Market Scanner page should keep showing what
is setting up before the bell. What must not happen is that a pre-bell sighting
gets BOOKED as an open trade. Placing the gate at the single insert chokepoint
also covers the manual Run-scan command, which runs at any hour and is subject
to no window at all.
"""
import json
import logging
import math
import threading
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import signal_db

import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # repo root
from shared import scanner_config as _scfg  # noqa: E402
from shared import market_calendar as _mc  # noqa: E402

log = logging.getLogger("signal_recorder")
TZ = ZoneInfo("America/Chicago")
# Quality-first capture floor: only signals scoring >= MIN_SCORE are recorded.
# 2026-06-11 quality retune: raised 50 -> 58 to stop capturing marginal signals.
# See docs/plans/2026-06-11-quality-first-selection-design.md.
MIN_SCORE = _scfg.scores()["capture_min"]   # config/scanner.toml

# Per-scanner-type overrides on that floor. A type opts in BY NAME: a new scanner
# must not inherit the loosest floor in the file by accident.
#
# INCOME is 0 deliberately. The composite was tuned for the 0-DTE / swing core
# and the 30-45 DTE board scores well below it — measured on prod 2026-09-11, all
# five candidates came in at 50.2-57.0, every one graded "Marginal" — so sharing
# capture_min would record NOTHING every day, and the point of capturing that
# board (gap assessment C1) is to learn whether those trades work, which cannot
# happen while they are refused. 0 means "whatever the board offered": the income
# scan already cuts below ``swing_min`` service-side, so that is the real filter.
_CAPTURE_FLOORS = {"INCOME": _scfg.scores()["capture_min_income"]}


# Serialises count-then-insert. The 0DTE/SWING scan and the Income board record
# from different executor threads; interleaved, both could read "1 open" and
# both insert, landing a symbol one past its cap.
_CAP_LOCK = threading.Lock()


def capture_floor(scanner_type):
    """The capture floor for one scanner type — its override, else MIN_SCORE."""
    return _CAPTURE_FLOORS.get(str(scanner_type or "").strip().upper(), MIN_SCORE)


def _dedup_key(sig, scanner_type):
    return f"{sig['symbol']}|{sig['type']}|{sig['short_strike']}|{sig['long_strike']}|{sig['expiration']}|{scanner_type}"


def _to_row(sig, scanner_type, now):
    return {
        "signal_id": uuid.uuid4().hex[:8],
        "scanner_type": scanner_type,
        "symbol": sig["symbol"],
        "strategy": sig["type"],
        "short_strike": sig.get("short_strike"),
        "long_strike": sig.get("long_strike"),
        "call_short": sig.get("call_short"),
        "call_long": sig.get("call_long"),
        "width": sig.get("width"),
        "expiration": sig.get("expiration"),
        "dte_at_entry": sig.get("dte", 0),
        # The scored credit IS the realistic fill (scanner_engine._entry_credit)
        # — record exactly what was scored. Raw spread_bid/ask are kept below
        # for audit / future FILL_FRAC calibration.
        "entry_credit": sig.get("credit"),
        "entry_max_loss": sig.get("max_loss"),
        "entry_score": sig.get("composite_score", sig.get("score", 0)),
        "entry_grade": sig.get("grade", ""),
        "entry_short_delta": sig.get("short_delta", 0),
        "entry_net_theta": sig.get("net_theta", 0),
        "entry_net_delta_position": sig.get("entry_net_delta_position", 0),
        "entry_net_theta_position": sig.get("entry_net_theta_position", 0),
        "entry_spread_bid": sig.get("spread_bid", 0),
        "entry_spread_ask": sig.get("spread_ask", 0),
        "entry_iv_rank": sig.get("iv_rank", 0),
        "entry_underlying": sig.get("underlying_price", 0),
        "first_seen_ts": now.isoformat(),
        "first_seen_date": now.date().isoformat(),
        "dedup_key": _dedup_key(sig, scanner_type),
        "status": "OPEN",
        "mode": sig.get("mode", "PREMIUM"),
    }


def _insert(row, db_path):
    """Indirection point so tests can monkeypatch a failure."""
    return signal_db.insert_signal(row, db_path=db_path)


def _now():
    """The capture instant. An indirection point so a test can pin the clock --
    the production call site passes no ``now``, so this is the path that has to
    be exercised."""
    return datetime.now(TZ)


def record_signals(signals, scanner_type, db_path=signal_db.DEFAULT_DB_PATH,
                   now=None):
    """Record signals with score >= this type's ``capture_floor``, inside regular
    hours only, holding each symbol to ``[capture] max_open_per_symbol`` OPEN
    captured signals counted across every scanner type (highest score first).
    Returns count inserted. Never raises — DB failures are logged and counted
    as 0.

    ``now`` is BOTH the gate input and the recorded ``first_seen_ts``, one
    instant for both, so the invariant is exact and checkable: every row in the
    table carries an open time inside the regular session. Gating on the scan's
    START time instead would let a scan launched at 15:00 stamp a row 15:02 —
    still an out-of-hours "Opened" time on the Captured Signals page, which is
    the thing being fixed.

    Outside the session this returns 0 and writes nothing. That is not a
    degrade path swallowing an error: it is the answer. Refusing the pre-open
    sighting is also what lets the real one through — ``dedup_key`` is globally
    UNIQUE with no date component, so a recorded 08:02 row would claim the slot
    permanently and ``INSERT OR IGNORE`` would then discard the SAME spread's
    genuine post-open capture in silence.
    """
    now = _now() if now is None else now
    floor = capture_floor(scanner_type)
    eligible = [s for s in signals
                if s.get("composite_score", s.get("score", 0)) >= floor]
    if not _mc.is_regular_hours(now):
        if eligible:
            # Counted at INFO, not warned: outside the session this is the
            # correct outcome, not a fault. But it is never silent — a scan
            # that found tradeable structure and booked none of it has to say
            # so, or the empty Captured table looks like a broken scanner.
            log.info("%s: regular session closed at %s — %d signal(s) scanned, "
                     "none captured", scanner_type, now.isoformat(timespec="seconds"),
                     len(eligible))
        return 0
    cap = _scfg.capture_max_open_per_symbol()
    # Best first, so when a scan offers more than a symbol's free slots the
    # highest-scoring signals take them. ``sorted`` is stable: ties keep the
    # scan's own order.
    eligible.sort(key=lambda s: s.get("composite_score", s.get("score", 0)),
                  reverse=True)
    inserted = 0
    capped = {}
    with _CAP_LOCK:
        open_n = {}
        if cap:
            try:
                open_n = signal_db.count_open_by_symbol(db_path=db_path)
            except Exception as e:
                # Fail CLOSED: capture feeds the paper Account's entries, and an
                # unreadable book must not become an uncapped one.
                log.error(f"signal_recorder open-count read failed, "
                          f"capturing nothing: {e}")
                return 0
        for sig in eligible:
            sym = sig["symbol"]
            if cap and open_n.get(sym, 0) >= cap:
                capped[sym] = capped.get(sym, 0) + 1
                continue
            row = _to_row(sig, scanner_type, now)
            try:
                if _insert(row, db_path):
                    inserted += 1
                    open_n[sym] = open_n.get(sym, 0) + 1
            except Exception as e:
                log.error(f"signal_recorder insert failed: {e}")
    if capped:
        # INFO, like the out-of-hours refusal: the cap working is the answer,
        # not a fault — but a scan that found structure and booked none of it
        # must say why.
        log.info("%s: %s at the %d-open-per-symbol capture cap", scanner_type,
                 ", ".join(f"{s} ({n} skipped)" for s, n in sorted(capped.items())),
                 cap)
    return inserted


# ── tracked structures: recorded to be MEASURED, never traded ───────────────
# The Market Scanner's structures that are not credit spreads. They go in the
# same store under ``signal_db.TRACKED_TYPES`` so they get a mark series and an
# outcome, and every existing reader of that store excludes those types.

_PER_CONTRACT = 100.0


def _real(value):
    """A finite number, or None (a bool is not a number)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _per_share(value):
    v = _real(value)
    return None if v is None else round(v / _PER_CONTRACT, 4)


def _tracked_legs(sig):
    """The legs as stored, or None when any leg cannot be marked later. Only
    what a mark needs, plus the entry mark for audit."""
    out = []
    for leg in sig.get("legs") or []:
        if not isinstance(leg, dict):
            return None
        kind, side = leg.get("kind"), leg.get("side")
        strike, exp = _real(leg.get("strike")), leg.get("expiration")
        if kind not in ("call", "put") or side not in ("long", "short"):
            return None                      # a share leg: the scanner holds none
        if strike is None or not exp:
            return None
        qty = _real(leg.get("qty"))
        out.append({"kind": kind, "side": side, "strike": strike,
                    "expiration": str(exp),
                    "qty": int(qty) if qty and qty >= 1 else 1,
                    "entry_mark": _real(leg.get("mark"))})
    return out or None


def tracked_dedup_key(sig, scanner_type):
    """One key per structure, legs and window - whatever order the legs came in."""
    legs = sorted(f"{l.get('side')}:{l.get('kind')}:{l.get('strike')}:"
                  f"{l.get('expiration')}:{l.get('qty', 1)}"
                  for l in sig.get("legs") or [])
    return f"{sig.get('symbol')}|{sig.get('type')}|{'/'.join(legs)}|{scanner_type}"


def _to_tracked_row(sig, scanner_type, now):
    """A normalized candidate as a ``signals`` row, or None when it cannot be
    recorded faithfully.

    ⚠ UNITS AND SIGN. The candidate carries per-CONTRACT dollars; the store
    carries per-SHARE values, as ``income_capture_row`` found out for the Income
    board. ``entry_credit`` is SIGNED - a credit positive, a debit NEGATIVE -
    which is what makes ``signal_db.close_signal_manually``'s
    ``(entry_credit - exit_value) * 100`` right for both.

    None rather than a guess for a row with no usable legs or no positive,
    finite risk figure: without the risk there is no R-multiple, and the dedup
    key is unique forever, so a bad row would hold its slot against the real one.
    """
    if not isinstance(sig, dict) or not sig.get("symbol") or not sig.get("type"):
        return None
    legs = _tracked_legs(sig)
    max_loss = _per_share(sig.get("max_loss"))
    if legs is None or max_loss is None or max_loss <= 0 or not sig.get("expiration"):
        return None
    debit, credit = sig.get("net_debit"), sig.get("net_credit")
    if debit is not None:
        debit = _per_share(debit)
        if debit is None:
            return None
        entry = -debit
    elif credit is not None:
        entry = _per_share(credit)
        if entry is None:
            return None
    else:
        entry = 0.0                              # even money
    return {
        "signal_id": uuid.uuid4().hex[:8],
        "scanner_type": scanner_type,
        "symbol": sig["symbol"],
        "strategy": sig["type"],
        # No strike columns and no width: the legs are the structure.
        "short_strike": None, "long_strike": None,
        "call_short": None, "call_long": None, "width": None,
        "expiration": sig["expiration"],          # the FRONT expiry
        "dte_at_entry": sig.get("dte", 0),
        "entry_credit": entry,
        "entry_max_loss": max_loss,
        "entry_score": sig.get("composite_score", 0),
        "entry_grade": sig.get("grade", ""),
        # None is "not recorded" and must never be written as 0.0.
        "entry_short_delta": None,
        "entry_net_theta": None,
        "entry_net_delta_position": _real(sig.get("net_delta")) or 0.0,
        "entry_net_theta_position": _real(sig.get("net_theta")) or 0.0,
        "entry_spread_bid": 0.0,
        "entry_spread_ask": 0.0,
        "entry_iv_rank": sig.get("iv_rank"),
        "entry_underlying": sig.get("underlying_price", 0),
        "first_seen_ts": now.isoformat(),
        "first_seen_date": now.date().isoformat(),
        "dedup_key": tracked_dedup_key(sig, scanner_type),
        "status": "OPEN",
        "mode": "TRACKED",
        "legs_json": json.dumps(legs),
        "family": str(sig.get("group") or sig.get("family") or "").upper() or None,
        "entry_max_profit": _per_share(sig.get("max_profit")),
        "entry_capital": _per_share(sig.get("capital")),
        "unbounded": 1 if sig.get("unbounded_loss") else 0,
        "entry_spans_earnings": 1 if sig.get("spans_earnings") is True else 0,
    }


def record_tracked(signals, scanner_type, db_path=None, now=None):
    """Record structures that are tracked for study. Returns the count inserted.
    Never raises on a bad row or a failed write.

    The same three gates ``record_signals`` applies, with settings of their own:
    the regular session (one ``now`` for the gate and the stamp), a score floor
    (``[scores] capture_min_tracked``), and a per-symbol cap counted across both
    tracked types and NOTHING else (``[capture] max_open_per_symbol_tracked``).
    ``[capture] tracked = false`` records nothing.

    ``scanner_type`` must be one of ``signal_db.TRACKED_TYPES``; anything else
    raises. That is deliberate and it is the one thing here that does: a tracked
    row filed under ``SWING`` would be read by every credit-spread reader and
    offered to the paper Account's entry cycle.
    """
    if scanner_type not in signal_db.TRACKED_TYPES:
        raise ValueError(f"record_tracked: {scanner_type!r} is not one of "
                         f"{signal_db.TRACKED_TYPES}")
    if not _scfg.capture_tracked_enabled():
        return 0
    db_path = db_path or signal_db.DEFAULT_DB_PATH
    now = _now() if now is None else now
    floor = _scfg.scores().get("capture_min_tracked", 0)
    eligible = [s for s in signals or []
                if isinstance(s, dict)
                and (s.get("composite_score") or 0) >= floor]
    if not _mc.is_regular_hours(now):
        if eligible:
            log.info("%s: regular session closed at %s — %d structure(s) scanned, "
                     "none recorded", scanner_type,
                     now.isoformat(timespec="seconds"), len(eligible))
        return 0
    cap = _scfg.capture_max_open_per_symbol_tracked()
    eligible.sort(key=lambda s: s.get("composite_score") or 0, reverse=True)
    inserted = 0
    capped = {}
    with _CAP_LOCK:
        open_n = {}
        if cap:
            try:
                open_n = signal_db.count_open_by_symbol(db_path=db_path, tracked=True)
            except Exception as e:
                log.error(f"record_tracked open-count read failed, "
                          f"recording nothing: {e}")
                return 0
        for sig in eligible:
            sym = sig.get("symbol")
            if cap and open_n.get(sym, 0) >= cap:
                capped[sym] = capped.get(sym, 0) + 1
                continue
            row = _to_tracked_row(sig, scanner_type, now)
            if row is None:
                continue
            try:
                if _insert(row, db_path):
                    inserted += 1
                    open_n[sym] = open_n.get(sym, 0) + 1
            except Exception as e:
                log.error(f"record_tracked insert failed: {e}")
    if capped:
        log.info("%s: %s at the %d-open-per-symbol tracked cap", scanner_type,
                 ", ".join(f"{s} ({n} skipped)" for s, n in sorted(capped.items())),
                 cap)
    return inserted
