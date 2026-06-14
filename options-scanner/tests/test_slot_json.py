"""Tests for slot JSON sidecar read/write."""
import json
import pytest
from gamma_tool import (write_slot_data_json, read_today_slot_data,
                       slot_data_filename)


def _stub_blocks_with_walls(symbol, spot):
    return {
        "gex": {
            "symbol": symbol, "spot": spot, "dte": 0,
            "expected_move": 28.5,
            "em_upper": spot + 28.5, "em_lower": spot - 28.5,
            "flip_point": spot,
            "top_positive": [{"strike": spot + 25, "value": 1.0e9}],
            "top_negative": [{"strike": spot - 25, "value": -1.0e9}],
            "eod_probabilities": {
                "touch_em_upper": 0.42, "touch_em_lower": 0.38,
                "reach_pos_wall": 0.55, "reach_neg_wall": 0.31},
        },
        "charm": None,
        "dex": None,
    }


def test_slot_data_filename():
    assert slot_data_filename("0820") == "gex_analysis_data_0820.json"
    assert slot_data_filename("manual") == "gex_analysis_data_manual.json"


def test_slot_data_filename_invalid():
    with pytest.raises(ValueError):
        slot_data_filename("0830")


def test_write_and_read_roundtrip(tmp_path):
    spx = _stub_blocks_with_walls("SPX", 5800.0)
    spy = _stub_blocks_with_walls("SPY", 580.0)
    qqq = _stub_blocks_with_walls("QQQ", 500.0)
    internals = {"cpce": 0.78, "advn": 1450, "decn": 1602, "skew": 138.4}

    path = write_slot_data_json(tmp_path, "1000", spx, spy, qqq, internals)
    assert path.exists()

    data = read_today_slot_data(tmp_path, "1000")
    assert data is not None
    assert data["slot"] == "1000"
    assert "captured_at" in data
    assert data["symbols"]["SPX"]["spot"] == 5800.0
    assert data["symbols"]["SPX"]["top_positive_walls"][0]["strike"] == 5825.0
    assert data["internals"]["cpce"] == 0.78


def test_write_handles_none_symbols(tmp_path):
    """If a symbol fetch failed, its blocks dict is None."""
    spx = _stub_blocks_with_walls("SPX", 5800.0)
    path = write_slot_data_json(tmp_path, "1000", spx, None, None, {})
    data = json.loads(path.read_text())
    assert data["symbols"]["SPX"] is not None
    assert data["symbols"]["SPY"] is None
    assert data["symbols"]["QQQ"] is None


def test_read_returns_none_for_missing(tmp_path):
    assert read_today_slot_data(tmp_path, "0820") is None


def test_read_returns_none_for_stale(tmp_path):
    """If captured_at is not today, treat as stale."""
    path = tmp_path / "gex_analysis_data_0820.json"
    path.write_text(json.dumps({
        "slot": "0820",
        "captured_at": "2020-01-01T00:00:00-05:00",
        "symbols": {}, "internals": {},
    }))
    assert read_today_slot_data(tmp_path, "0820") is None


def test_read_returns_none_for_corrupt(tmp_path):
    """Malformed JSON returns None, not a crash."""
    path = tmp_path / "gex_analysis_data_0820.json"
    path.write_text("this is not json")
    assert read_today_slot_data(tmp_path, "0820") is None
