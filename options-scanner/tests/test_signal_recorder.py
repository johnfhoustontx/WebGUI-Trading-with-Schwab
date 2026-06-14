import signal_recorder
import signal_db


def _make_signal(score=60, symbol="SPY", type_="PCS", short=690, long=688, exp="2026-04-15", dte=0):
    return {
        "symbol": symbol, "type": type_, "short_strike": short, "long_strike": long,
        "width": abs(short - long), "expiration": exp, "dte": dte,
        "credit": 0.6, "max_loss": 1.4, "composite_score": score, "grade": "Marginal",
        "short_delta": -0.15, "net_theta": 5.0, "iv_rank": 30.0, "underlying_price": 700.0,
    }


def test_records_signal_above_threshold(tmp_path):
    db = tmp_path / "s.db"
    assert signal_recorder.record_signals([_make_signal(score=60)], "0DTE", db_path=db) == 1


def test_filters_below_threshold(tmp_path):
    db = tmp_path / "s.db"
    # 55 < MIN_SCORE (58) -> filtered out.
    assert signal_recorder.record_signals([_make_signal(score=55)], "0DTE", db_path=db) == 0


def test_threshold_exact_boundary_passes(tmp_path):
    db = tmp_path / "s.db"
    # Exact boundary == MIN_SCORE (58) passes.
    assert signal_recorder.record_signals([_make_signal(score=58)], "0DTE", db_path=db) == 1


def test_score_floor_is_58():
    import signal_recorder
    assert signal_recorder.MIN_SCORE == 58


def test_dedup_on_second_call(tmp_path):
    db = tmp_path / "s.db"
    sigs = [_make_signal(score=60)]
    assert signal_recorder.record_signals(sigs, "0DTE", db_path=db) == 1
    assert signal_recorder.record_signals(sigs, "0DTE", db_path=db) == 0


def test_iron_condor_captures_call_strikes(tmp_path):
    db = tmp_path / "s.db"
    sig = _make_signal(type_="IC", score=60)
    sig["call_short"] = 710
    sig["call_long"] = 712
    n = signal_recorder.record_signals([sig], "0DTE", db_path=db)
    assert n == 1
    rows = signal_db.get_open_signals(db_path=db)
    assert rows[0]["call_short"] == 710
    assert rows[0]["call_long"] == 712


def test_db_failure_does_not_raise(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(signal_recorder, "_insert", boom)
    assert signal_recorder.record_signals([_make_signal(score=60)], "0DTE", db_path=tmp_path / "s.db") == 0


def test_scanner_type_is_tagged(tmp_path):
    db = tmp_path / "s.db"
    signal_recorder.record_signals([_make_signal(score=60)], "SWING", db_path=db)
    rows = signal_db.get_open_signals(db_path=db)
    assert rows[0]["scanner_type"] == "SWING"


def test_empty_list_returns_zero(tmp_path):
    db = tmp_path / "s.db"
    assert signal_recorder.record_signals([], "0DTE", db_path=db) == 0


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
    signal_recorder.record_signals([_make_signal(score=60)], "0DTE", db_path=db)
    rows = signal_db.get_open_signals(db_path=db)
    assert len(rows) == 1
    assert rows[0]["mode"] == "PREMIUM"


def test_record_signal_directional_mode_preserved(tmp_path):
    """Signals carrying mode='DIRECTIONAL' record that value."""
    db = tmp_path / "rec_directional.db"
    sig = _make_signal(score=60)
    sig["mode"] = "DIRECTIONAL"
    signal_recorder.record_signals([sig], "0DTE", db_path=db)
    rows = signal_db.get_open_signals(db_path=db)
    assert len(rows) == 1
    assert rows[0]["mode"] == "DIRECTIONAL"
