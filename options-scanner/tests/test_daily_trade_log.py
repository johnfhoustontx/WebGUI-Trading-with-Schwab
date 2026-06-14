"""
test_daily_trade_log.py - Tests for the daily potential-trade journal
Version: 1.0.0
Last Updated: 2026-06-13

Covers chain parsing (0-DTE filtering), delta-based short selection, candidate
construction, capture (mocked fetchers + in-memory DB), settlement outcomes,
the entry-window gate, and the EOD review rendering.
"""

import sqlite3
import datetime
from zoneinfo import ZoneInfo

import pytest
import daily_trade_log as dtl

DATE = "2026-06-15"  # a Monday


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(dtl.SCHEMA_SQL)
    return conn


def _chain(date_str=DATE):
    """Synthetic 0-DTE chain: short put ~-0.16 at 7320 (long 7295), short call
    ~+0.16 at 7480 (long 7505). Includes a next-day expiry that must be ignored."""
    return {
        "underlyingPrice": 7400.0,
        "putExpDateMap": {
            f"{date_str}:0": {
                "7295.0": [{"delta": -0.09, "mark": 4.0}],
                "7300.0": [{"delta": -0.10, "mark": 5.0}],
                "7320.0": [{"delta": -0.16, "mark": 8.0}],
            },
            "2026-06-16:1": {"7320.0": [{"delta": -0.30, "mark": 20.0}]},  # ignored
        },
        "callExpDateMap": {
            f"{date_str}:0": {
                "7480.0": [{"delta": 0.16, "mark": 7.0}],
                "7505.0": [{"delta": 0.10, "mark": 3.0}],
            },
        },
    }


def test_parse_0dte_strikes_filters_expiration():
    s = dtl.parse_0dte_strikes(_chain(), DATE)
    assert set(s["put"]) == {7295.0, 7300.0, 7320.0}   # next-day expiry excluded
    assert set(s["call"]) == {7480.0, 7505.0}


def test_pick_short_by_delta_closest_to_target():
    s = dtl.parse_0dte_strikes(_chain(), DATE)
    assert dtl.pick_short_by_delta(s["put"], 0.16)["strike"] == 7320.0
    assert dtl.pick_short_by_delta(s["call"], 0.16)["strike"] == 7480.0


def test_build_candidate_credit_and_wing():
    s = dtl.parse_0dte_strikes(_chain(), DATE)
    pcs = dtl.build_candidate(s["put"], "put", wing=25)
    assert pcs["short_strike"] == 7320.0 and pcs["long_strike"] == 7295.0
    assert pcs["credit_pts"] == pytest.approx(4.0)   # 8.0 - 4.0
    ccs = dtl.build_candidate(s["call"], "call", wing=25)
    assert ccs["short_strike"] == 7480.0 and ccs["long_strike"] == 7505.0
    assert ccs["credit_pts"] == pytest.approx(4.0)   # 7.0 - 3.0


def test_capture_writes_both_sides_and_marks_chosen():
    conn = _mem_conn()
    now = datetime.datetime(2026, 6, 15, 13, 0, tzinfo=ZoneInfo("America/Chicago"))
    written = dtl.capture(
        now, conn=conn,
        chain_fetcher=lambda sym, d: _chain(d),
        quote_fetcher=lambda sym: 7400.0,
        sma_fetcher=lambda sym: 7350.0,   # < 7400 -> bullish -> PCS chosen
    )
    assert len(written) == 4  # 2 instruments x 2 sides
    rows = conn.execute("SELECT fut, side, chosen, trend_dir FROM "
                        "daily_potential_trades WHERE fut='/ES' ORDER BY side").fetchall()
    # PCS chosen (bullish), CCS not
    assert ("/ES", "CCS", 0, "put") in rows
    assert ("/ES", "PCS", 1, "put") in rows


def test_settle_value_pts_branches():
    # PCS short 7320 / long 7295
    assert dtl._settle_value_pts("PCS", 7320, 7295, 7400) == 0.0      # OTM
    assert dtl._settle_value_pts("PCS", 7320, 7295, 7300) == 20.0     # partial
    assert dtl._settle_value_pts("PCS", 7320, 7295, 7290) == 25.0     # full width
    # CCS short 7480 / long 7505
    assert dtl._settle_value_pts("CCS", 7480, 7505, 7400) == 0.0
    assert dtl._settle_value_pts("CCS", 7480, 7505, 7600) == 25.0


def test_settle_open_assigns_outcome_and_pnl():
    conn = _mem_conn()
    now = datetime.datetime(2026, 6, 15, 13, 0, tzinfo=ZoneInfo("America/Chicago"))
    dtl.capture(now, conn=conn, chain_fetcher=lambda sym, d: _chain(d),
                quote_fetcher=lambda sym: 7400.0, sma_fetcher=lambda sym: 7350.0)
    # settle with underlying = 7400 -> both put & call spreads expire worthless
    n = dtl.settle_open(DATE, quote_fetcher=lambda sym: 7400.0, conn=conn)
    assert n == 4
    row = conn.execute("SELECT outcome, realized_pnl_pts FROM daily_potential_trades "
                       "WHERE fut='/ES' AND side='PCS'").fetchone()
    assert row[0] == "worthless" and row[1] == pytest.approx(4.0)


def test_settle_open_breach_loss():
    conn = _mem_conn()
    now = datetime.datetime(2026, 6, 15, 13, 0, tzinfo=ZoneInfo("America/Chicago"))
    dtl.capture(now, conn=conn, chain_fetcher=lambda sym, d: _chain(d),
                quote_fetcher=lambda sym: 7400.0, sma_fetcher=lambda sym: 7350.0)
    # crash to 7290 -> PCS fully breached: val 25, pnl = 4 - 25 = -21
    dtl.settle_open(DATE, quote_fetcher=lambda sym: 7290.0, conn=conn)
    row = conn.execute("SELECT outcome, realized_pnl_pts FROM daily_potential_trades "
                       "WHERE fut='/ES' AND side='PCS'").fetchone()
    assert row[0] == "breach" and row[1] == pytest.approx(-21.0)


def test_capture_if_due_gates_outside_window(monkeypatch):
    # 10:00 CT is before the entry window -> no capture
    early = datetime.datetime(2026, 6, 15, 10, 0, tzinfo=ZoneInfo("America/Chicago"))
    assert dtl.capture_if_due(early) == []
    # Saturday -> no capture
    sat = datetime.datetime(2026, 6, 13, 13, 0, tzinfo=ZoneInfo("America/Chicago"))
    assert dtl.capture_if_due(sat) == []


def test_render_eod_section_includes_tally_after_settle():
    conn = _mem_conn()
    now = datetime.datetime(2026, 6, 15, 13, 0, tzinfo=ZoneInfo("America/Chicago"))
    dtl.capture(now, conn=conn, chain_fetcher=lambda sym, d: _chain(d),
                quote_fetcher=lambda sym: 7400.0, sma_fetcher=lambda sym: 7350.0)
    dtl.settle_open(DATE, quote_fetcher=lambda sym: 7400.0, conn=conn)
    md = "\n".join(dtl.render_eod_section(DATE, conn=conn))
    assert "Potential Trades" in md
    assert "Captured today" in md
    assert "Running tally" in md
    assert "/ES" in md
