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
import logging
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
    """Record signals with score >= MIN_SCORE, inside regular hours only.
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
    eligible = [s for s in signals
                if s.get("composite_score", s.get("score", 0)) >= MIN_SCORE]
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
    inserted = 0
    for sig in eligible:
        row = _to_row(sig, scanner_type, now)
        try:
            if _insert(row, db_path):
                inserted += 1
        except Exception as e:
            log.error(f"signal_recorder insert failed: {e}")
    return inserted
