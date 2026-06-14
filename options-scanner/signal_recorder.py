"""Capture scanner signals into the signal tracking DB."""
import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import signal_db

log = logging.getLogger("signal_recorder")
TZ = ZoneInfo("America/Chicago")
# Quality-first capture floor: only signals scoring >= MIN_SCORE are recorded.
# 2026-06-11 quality retune: raised 50 -> 58 to stop capturing marginal signals.
# See docs/plans/2026-06-11-quality-first-selection-design.md.
MIN_SCORE = 58


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


def record_signals(signals, scanner_type, db_path=signal_db.DEFAULT_DB_PATH):
    """Record signals with score >= MIN_SCORE. Returns count inserted.
    Never raises — DB failures are logged and counted as 0."""
    now = datetime.now(TZ)
    inserted = 0
    for sig in signals:
        score = sig.get("composite_score", sig.get("score", 0))
        if score < MIN_SCORE:
            continue
        row = _to_row(sig, scanner_type, now)
        try:
            if _insert(row, db_path):
                inserted += 1
        except Exception as e:
            log.error(f"signal_recorder insert failed: {e}")
    return inserted
