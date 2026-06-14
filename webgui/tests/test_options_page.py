"""Tests for webgui/pages/options.py.

Cover the pure transforms (signal dicts -> table rows/columns) and that the
page exposes a render() entrypoint. The NiceGUI rendering itself is exercised
by the shell smoke run; here we keep the data marshalling honest.
"""
import pages.options as options

SAMPLE = {
    "symbol": "SPY",
    "type": "PCS",
    "expiration": "2026-06-14",
    "dte": 0,
    "short_strike": 450.0,
    "long_strike": 445.0,
    "width": 5.0,
    "credit": 0.34,
    "max_loss": 4.66,
    "rr_pct": 7.3,
    "pop_pct": 73.2,
    "composite_score": 72,
    "grade": "A",
}


def test_render_is_callable():
    assert callable(options.render)


def test_signal_columns_expose_key_fields():
    fields = {c["field"] for c in options.signal_columns()}
    assert {"symbol", "type", "grade", "composite_score", "credit", "pop_pct"} <= fields


def test_signal_rows_maps_fields():
    rows = options.signal_rows([SAMPLE])
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "SPY"
    assert r["type"] == "PCS"
    assert r["grade"] == "A"
    assert r["composite_score"] == 72
    assert "450" in str(r["short_strike"])


def test_signal_rows_handles_missing_fields():
    # A sparse signal must not raise (engine fields vary by trade type).
    rows = options.signal_rows([{"symbol": "X"}])
    assert rows[0]["symbol"] == "X"


def test_signal_rows_sorted_by_score_desc():
    rows = options.signal_rows([
        {"symbol": "LO", "composite_score": 10},
        {"symbol": "HI", "composite_score": 90},
    ])
    assert [r["symbol"] for r in rows] == ["HI", "LO"]
