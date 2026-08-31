from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import signal_recorder
import signal_db

TZ = ZoneInfo("America/Chicago")
# Every capture time in this module is explicit. `record_signals` refuses to
# record outside the regular cash session, so a test that let the recorder read
# the wall clock would pass or fail depending on the hour the suite is run.
RTH = datetime(2026, 8, 26, 10, 0, tzinfo=TZ)          # Wednesday, mid-session


def _make_signal(score=60, symbol="SPY", type_="PCS", short=690, long=688, exp="2026-04-15", dte=0):
    return {
        "symbol": symbol, "type": type_, "short_strike": short, "long_strike": long,
        "width": abs(short - long), "expiration": exp, "dte": dte,
        "credit": 0.6, "max_loss": 1.4, "composite_score": score, "grade": "Marginal",
        "short_delta": -0.15, "net_theta": 5.0, "iv_rank": 30.0, "underlying_price": 700.0,
    }


def test_records_signal_above_threshold(tmp_path):
    db = tmp_path / "s.db"
    assert signal_recorder.record_signals([_make_signal(score=60)], "0DTE", db_path=db, now=RTH) == 1


def test_filters_below_threshold(tmp_path):
    db = tmp_path / "s.db"
    # 55 < MIN_SCORE (58) -> filtered out.
    assert signal_recorder.record_signals([_make_signal(score=55)], "0DTE", db_path=db, now=RTH) == 0


def test_threshold_exact_boundary_passes(tmp_path):
    db = tmp_path / "s.db"
    # Exact boundary == MIN_SCORE (58) passes.
    assert signal_recorder.record_signals([_make_signal(score=58)], "0DTE", db_path=db, now=RTH) == 1


def test_score_floor_is_58():
    import signal_recorder
    assert signal_recorder.MIN_SCORE == 58


def test_dedup_on_second_call(tmp_path):
    db = tmp_path / "s.db"
    sigs = [_make_signal(score=60)]
    assert signal_recorder.record_signals(sigs, "0DTE", db_path=db, now=RTH) == 1
    assert signal_recorder.record_signals(sigs, "0DTE", db_path=db, now=RTH) == 0


def test_iron_condor_captures_call_strikes(tmp_path):
    db = tmp_path / "s.db"
    sig = _make_signal(type_="IC", score=60)
    sig["call_short"] = 710
    sig["call_long"] = 712
    n = signal_recorder.record_signals([sig], "0DTE", db_path=db, now=RTH)
    assert n == 1
    rows = signal_db.get_open_signals(db_path=db)
    assert rows[0]["call_short"] == 710
    assert rows[0]["call_long"] == 712


def test_db_failure_does_not_raise(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(signal_recorder, "_insert", boom)
    assert signal_recorder.record_signals([_make_signal(score=60)], "0DTE", db_path=tmp_path / "s.db", now=RTH) == 0


def test_scanner_type_is_tagged(tmp_path):
    db = tmp_path / "s.db"
    signal_recorder.record_signals([_make_signal(score=60)], "SWING", db_path=db, now=RTH)
    rows = signal_db.get_open_signals(db_path=db)
    assert rows[0]["scanner_type"] == "SWING"


def test_empty_list_returns_zero(tmp_path):
    db = tmp_path / "s.db"
    assert signal_recorder.record_signals([], "0DTE", db_path=db, now=RTH) == 0


def test_to_row_includes_position_perspective_fields():
    """_to_row passes through the 4 new scanner fields."""
    from signal_recorder import _to_row
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Chicago")

    sig = {
        "symbol": "$SPX", "type": "CCS",
        "short_strike": 7620, "long_strike": 7630,
        "expiration": "2026-06-08", "dte": 12,
        "credit": 2.90, "max_loss": 7.10,
        "short_delta": 0.27, "net_theta": -0.10,
        "entry_net_delta_position": -0.05,
        "entry_net_theta_position": 0.10,
        "spread_bid": 2.80, "spread_ask": 3.00,
        "underlying_price": 7515.0,
        "iv_rank": 52,
    }
    row = _to_row(sig, "swing", datetime.now(TZ))
    assert row["entry_net_delta_position"] == -0.05
    assert row["entry_net_theta_position"] == 0.10
    assert row["entry_spread_bid"] == 2.80
    assert row["entry_spread_ask"] == 3.00


def test_to_row_handles_missing_position_fields_gracefully():
    """Old-style signals without the new fields produce row with default 0/None."""
    from signal_recorder import _to_row
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Chicago")

    sig = {
        "symbol": "$SPX", "type": "CCS",
        "short_strike": 7620, "long_strike": 7630,
        "expiration": "2026-06-08", "dte": 12,
        "credit": 2.90, "max_loss": 7.10,
        "short_delta": 0.27, "net_theta": -0.10,
        "underlying_price": 7515.0, "iv_rank": 52,
    }
    row = _to_row(sig, "swing", datetime.now(TZ))
    assert "entry_net_delta_position" in row
    assert "entry_net_theta_position" in row
    assert "entry_spread_bid" in row
    assert "entry_spread_ask" in row


def test_entry_credit_is_scored_credit_not_natural_bid():
    """entry_credit records the scored realistic credit (sig['credit']), not
    the natural spread_bid; raw quotes are kept separately for audit."""
    from signal_recorder import _to_row
    from datetime import datetime
    from zoneinfo import ZoneInfo

    sig = {
        "symbol": "$SPX", "type": "CCS",
        "short_strike": 7620, "long_strike": 7630,
        "expiration": "2026-06-08", "dte": 12,
        "credit": 2.90, "max_loss": 7.10,
        "short_delta": 0.27, "net_theta": -0.10,
        "spread_bid": 2.80, "spread_ask": 3.00,
        "underlying_price": 7515.0, "iv_rank": 52,
    }
    row = _to_row(sig, "swing", datetime.now(ZoneInfo("America/Chicago")))
    assert row["entry_credit"] == 2.90
    assert row["entry_spread_bid"] == 2.80


def test_record_signal_default_mode_is_premium(tmp_path):
    """Signals without explicit mode are recorded as mode='PREMIUM'."""
    db = tmp_path / "rec_default.db"
    signal_recorder.record_signals([_make_signal(score=60)], "0DTE", db_path=db, now=RTH)
    rows = signal_db.get_open_signals(db_path=db)
    assert len(rows) == 1
    assert rows[0]["mode"] == "PREMIUM"


def test_record_signal_directional_mode_preserved(tmp_path):
    """Signals carrying mode='DIRECTIONAL' record that value."""
    db = tmp_path / "rec_directional.db"
    sig = _make_signal(score=60)
    sig["mode"] = "DIRECTIONAL"
    signal_recorder.record_signals([sig], "0DTE", db_path=db, now=RTH)
    rows = signal_db.get_open_signals(db_path=db)
    assert len(rows) == 1
    assert rows[0]["mode"] == "DIRECTIONAL"


# ── Capture is gated to the regular cash session ───────────────────────────
#
# A signal recorded outside 08:30-15:00 CT records an entry the account could
# not have taken: Schwab pins a chain's `underlyingPrice` to the PRIOR CLOSE
# outside the regular session (the same freeze `gex_collector._reanchor_spots`
# corrects for GEX), so a pre-open scan picks its strikes, deltas and credit off
# yesterday's price and the open then gaps away from all of them.
#
# The gate lives here, at the recorder, rather than on the scan window: the scan
# still runs pre-open and still populates the Market Scanner page. Only the
# BOOKING is refused.

PREMARKET = datetime(2026, 8, 26, 8, 2, tzinfo=TZ)     # the reported 08:02 capture
AFTER_CLOSE = datetime(2026, 8, 26, 15, 2, tzinfo=TZ)  # a 15:00-slot scan landing late
SATURDAY = datetime(2026, 8, 29, 10, 0, tzinfo=TZ)


@pytest.mark.parametrize("when", [PREMARKET, AFTER_CLOSE, SATURDAY],
                         ids=["premarket", "after_close", "weekend"])
def test_capture_outside_regular_hours_records_nothing(tmp_path, when):
    db = tmp_path / "s.db"
    assert signal_recorder.record_signals([_make_signal(score=60)], "0DTE",
                                          db_path=db, now=when) == 0
    assert signal_db.get_open_signals(db_path=db) == []


@pytest.mark.parametrize("when,expected", [
    (datetime(2026, 8, 26, 8, 29, 59, tzinfo=TZ), 0),   # one second before the bell
    (datetime(2026, 8, 26, 8, 30, 0, tzinfo=TZ), 1),    # the open
    (datetime(2026, 8, 26, 15, 0, 0, tzinfo=TZ), 1),    # the cash close instant
    (datetime(2026, 8, 26, 15, 0, 1, tzinfo=TZ), 0),    # one second past it
], ids=["before_open", "at_open", "at_close", "after_close"])
def test_capture_window_edges(tmp_path, when, expected):
    """The boundary is `market_calendar.is_regular_hours`, compared at SECOND
    granularity — so 15:00:01 is already out, not just 15:01. Pinned because a
    scan launched in the 15:00 slot lands mid-minute and must not book."""
    db = tmp_path / "s.db"
    assert signal_recorder.record_signals([_make_signal(score=60)], "0DTE",
                                          db_path=db, now=when) == expected


def test_premarket_sighting_does_not_burn_the_dedup_slot(tmp_path):
    """`dedup_key` is globally UNIQUE with no date component, so a recorded
    pre-open row would claim the slot forever and INSERT OR IGNORE would then
    silently discard the SAME spread's genuine post-open capture. Refusing the
    pre-open sighting is what lets the real one through."""
    db = tmp_path / "s.db"
    sigs = [_make_signal(score=60)]
    assert signal_recorder.record_signals(sigs, "0DTE", db_path=db, now=PREMARKET) == 0

    after_open = datetime(2026, 8, 26, 8, 35, tzinfo=TZ)
    assert signal_recorder.record_signals(sigs, "0DTE", db_path=db, now=after_open) == 1

    rows = signal_db.get_open_signals(db_path=db)
    assert len(rows) == 1
    assert rows[0]["first_seen_ts"] == after_open.isoformat()


def test_wall_clock_is_gated_when_now_is_omitted(tmp_path, monkeypatch):
    """The production call site passes no `now`, so gating only the injected
    argument would leave the real path wide open. This drives the default."""
    db = tmp_path / "s.db"
    monkeypatch.setattr(signal_recorder, "_now", lambda: PREMARKET)
    assert signal_recorder.record_signals([_make_signal(score=60)], "0DTE", db_path=db) == 0

    monkeypatch.setattr(signal_recorder, "_now", lambda: RTH)
    assert signal_recorder.record_signals([_make_signal(score=60)], "0DTE", db_path=db) == 1
