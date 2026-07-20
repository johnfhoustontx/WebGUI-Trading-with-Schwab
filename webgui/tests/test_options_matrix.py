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
