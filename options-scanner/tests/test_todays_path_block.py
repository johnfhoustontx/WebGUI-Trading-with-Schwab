"""Tests for the 1500 retrospective TODAY'S PATH block."""
from gamma_tool import build_todays_path_block


def _stub_json(slot, captured_at, spx_spot, spx_top_pos, spx_top_neg):
    return {
        "slot": slot,
        "captured_at": captured_at,
        "symbols": {
            "SPX": {
                "spot": spx_spot, "dte": 0,
                "expected_move": 28.0,
                "em_upper": spx_spot + 28.0, "em_lower": spx_spot - 28.0,
                "flip_point": spx_spot,
                "top_positive_walls": [{"strike": spx_top_pos, "value": 1e9}],
                "top_negative_walls": [{"strike": spx_top_neg, "value": -1e9}],
                "eod_probabilities": None,
            },
            "SPY": None, "QQQ": None,
        },
        "internals": {"cpce": 0.78, "advn": 1450, "decn": 1602, "skew": 138.4},
    }


def test_path_block_with_four_earlier_slots():
    jsons = {
        "0820": _stub_json("0820", "2026-05-20T08:20:00-05:00", 5790.0, 5810, 5775),
        "0845": _stub_json("0845", "2026-05-20T08:45:00-05:00", 5798.0, 5815, 5780),
        "1000": _stub_json("1000", "2026-05-20T10:00:00-05:00", 5810.0, 5825, 5790),
        "1300": _stub_json("1300", "2026-05-20T13:00:00-05:00", 5820.0, 5830, 5795),
    }
    current = {"SPX": 5814.0, "SPY": 581.0, "QQQ": 502.0}
    out = build_todays_path_block(jsons, current)
    assert "TODAY'S PATH" in out
    assert "SPX:" in out
    # Should show spot trail with at least the morning values
    for spot in ("5790", "5798", "5810", "5820", "5814"):
        assert spot in out


def test_path_block_skips_missing_earlier_slots():
    jsons = {"1000": _stub_json("1000", "2026-05-20T10:00:00-05:00",
                                5810.0, 5825, 5790)}
    current = {"SPX": 5814.0, "SPY": 581.0, "QQQ": 502.0}
    out = build_todays_path_block(jsons, current)
    assert "TODAY'S PATH" in out
    # Doesn't crash with missing slots
    assert "1000" in out or "5810" in out


def test_path_block_includes_internals_trail():
    jsons = {
        "0820": _stub_json("0820", "2026-05-20T08:20:00-05:00", 5790.0, 5810, 5775),
        "1300": _stub_json("1300", "2026-05-20T13:00:00-05:00", 5820.0, 5830, 5795),
    }
    # Change SKEW between the two
    jsons["1300"]["internals"]["skew"] = 130.0
    current = {"SPX": 5814.0, "SPY": 581.0, "QQQ": 502.0}
    out = build_todays_path_block(jsons, current)
    # Internals trail mentions SKEW
    assert "SKEW" in out


def test_path_block_em_breakout_detection():
    """When realized range exceeded morning's EM bounds, output says 'broke out'."""
    jsons = {
        "0820": _stub_json("0820", "2026-05-20T08:20:00-05:00", 5800.0, 5810, 5775),
    }
    # Current spot well above the morning EM upper (5800 + 28 = 5828)
    current = {"SPX": 5860.0, "SPY": 581.0, "QQQ": 502.0}
    out = build_todays_path_block(jsons, current)
    assert "broke out" in out.lower()


def test_path_block_em_inside_detection():
    jsons = {
        "0820": _stub_json("0820", "2026-05-20T08:20:00-05:00", 5800.0, 5810, 5775),
    }
    # Current spot inside the morning EM range
    current = {"SPX": 5810.0, "SPY": 581.0, "QQQ": 502.0}
    out = build_todays_path_block(jsons, current)
    assert "stayed inside" in out.lower()
