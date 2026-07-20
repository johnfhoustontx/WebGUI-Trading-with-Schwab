from pages.options import matrix


def test_matrix_columns_are_sortable():
    cols = matrix.matrix_columns()
    fields = {c["field"] for c in cols}
    for f in ("symbol", "spot", "day_pct", "n_signals", "n_alerts", "signal_label", "hotness"):
        assert f in fields
    assert all(c["sortable"] for c in cols)


def test_matrix_rows_formats_and_stamps_classes():
    payload = {"rows": [{
        "symbol": "SPY", "spot": 101.0, "day_pct": 1.2, "trend_state": "up",
        "trend_dir": 0.6, "call_accel": "hot", "put_accel": "cool", "pc_ratio": 0.5,
        "net_prem_m": 2.0, "flip": 100.0, "gex_regime": "above", "n_signals": 3,
        "n_alerts": 2, "signal": "buy", "signal_strength": 2, "hotness": 12}]}
    rows = matrix.matrix_rows(payload)
    r = rows[0]
    assert r["symbol"] == "SPY"
    assert r["_signal_class"]
    assert r["_daypct_class"]
    assert r["_regime_class"]
    assert r["signal_label"] == "Buy"
    assert r["trend"]           # an arrow glyph
    assert r["_trend_class"]


def test_matrix_rows_empty_and_none_payload():
    assert matrix.matrix_rows({}) == []
    assert matrix.matrix_rows(None) == []
    assert matrix.matrix_rows({"rows": []}) == []


def test_signal_class_maps_all_states():
    for s in ("buy", "neutral", "sell", "bogus"):
        assert isinstance(matrix.signal_class(s), str) and matrix.signal_class(s)
    assert matrix.signal_class("bogus") == matrix.signal_class("neutral")


def test_daypct_class_sign():
    assert matrix.daypct_class(1.0) != matrix.daypct_class(-1.0)
    assert matrix.daypct_class(None) == matrix.daypct_class(0.0)


def test_matrix_rows_degraded_row_has_defaults():
    # a row with mostly-missing fields must not crash + get sane defaults
    rows = matrix.matrix_rows({"rows": [{"symbol": "AAPL"}]})
    r = rows[0]
    assert r["symbol"] == "AAPL"
    assert r["signal_label"] == "Neutral"
    assert r["gex_regime"] == "na"
    assert r["n_signals"] == 0


def test_short_ts_converts_utc_to_central():
    # 22:03 UTC on 2026-07-20 is CDT (UTC-5) → 5:03 PM Central, not 10:03 PM.
    assert matrix._short_ts("2026-07-20T22:03:00+00:00") == "5:03 PM"
    # a naive timestamp (no offset) is treated as UTC and still converted.
    assert matrix._short_ts("2026-07-20T22:03:00") == "5:03 PM"
    # winter date is CST (UTC-6): 22:03 UTC → 4:03 PM.
    assert matrix._short_ts("2026-01-15T22:03:00+00:00") == "4:03 PM"
    assert matrix._short_ts(None) == ""
    assert matrix._short_ts("garbage") == ""


def test_status_text_updated_clock_is_central():
    text = matrix.status_text({"rows": [{}], "session_date": "2026-07-20",
                               "ts": "2026-07-20T22:03:00+00:00"})
    assert "updated 5:03 PM" in text          # Central, not UTC 10:03 PM
    assert "session 2026-07-20" in text
