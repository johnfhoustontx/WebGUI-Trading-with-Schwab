"""Tests for the streaming order-flow consumer's PURE helpers.

The blocking ``_stream_worker`` is deliberately NOT unit-tested (mirrors the
portfolio-svc note — the pure parse/roll/aggregate helpers are tested here; the
reconnect loop is verified live against a running proxy). We exercise
``parse_sse_line`` / ``add_tick`` / ``prune`` / ``build_order_flow_view`` /
``reconnect_delay``.
"""
from collections import deque

from services.sentiment_svc import order_flow_consumer as ofc


# ── parse_sse_line ───────────────────────────────────────────────────────────
def test_parse_sse_line_data():
    tick = ofc.parse_sse_line('data: {"symbol": "SPY", "last": 500.1}')
    assert tick == {"symbol": "SPY", "last": 500.1}


def test_parse_sse_line_blank_comment_nondata_badjson():
    assert ofc.parse_sse_line("") is None
    assert ofc.parse_sse_line("   ") is None
    assert ofc.parse_sse_line(": keepalive") is None       # SSE comment
    assert ofc.parse_sse_line("event: ping") is None        # non-data field
    assert ofc.parse_sse_line("data: {not json") is None    # bad JSON
    assert ofc.parse_sse_line("data: 42") is None           # not a dict
    assert ofc.parse_sse_line(None) is None


# ── add_tick + prune ─────────────────────────────────────────────────────────
def test_add_tick_appends():
    windows = {}
    ofc.add_tick(windows, "SPY", {"last": 1.0}, now=100.0)
    ofc.add_tick(windows, "SPY", {"last": 2.0}, now=101.0)
    assert list(windows["SPY"]) == [(100.0, {"last": 1.0}), (101.0, {"last": 2.0})]


def test_add_tick_ignores_bad_symbol_or_tick():
    windows = {}
    ofc.add_tick(windows, None, {"last": 1.0}, now=100.0)
    ofc.add_tick(windows, "", {"last": 1.0}, now=100.0)
    ofc.add_tick(windows, "SPY", "not-a-dict", now=100.0)
    assert windows == {}


def test_prune_drops_old_keeps_recent():
    windows = {"SPY": deque()}
    now = 1000.0
    # one tick 400 s ago (outside the 300 s window) + one 100 s ago (inside).
    ofc.add_tick(windows, "SPY", {"last": 1.0}, now - 400)
    ofc.add_tick(windows, "SPY", {"last": 2.0}, now - 100)
    ofc.prune(windows, now, window_sec=300)
    kept = [t for (_, t) in windows["SPY"]]
    assert kept == [{"last": 2.0}]


# ── build_order_flow_view ────────────────────────────────────────────────────
def test_build_view_mostly_buys_positive():
    """A window of mostly buyer-initiated trades → aggressor_ratio>0, cvd>0."""
    windows = {"SPY": deque()}
    now = 500.0
    # last >= ask ⇒ buyer-initiated (quote rule). Use proxy field names incl.
    # last_size so the last_size→size mapping is exercised.
    for i in range(8):
        ofc.add_tick(windows, "SPY",
                     {"last": 100.0, "bid": 99.9, "ask": 100.0, "last_size": 5},
                     now + i)
    # one seller-initiated (last <= bid) to prove it nets out but stays positive.
    ofc.add_tick(windows, "SPY",
                 {"last": 99.9, "bid": 99.9, "ask": 100.0, "last_size": 5}, now + 9)

    view = ofc.build_order_flow_view(windows, now + 10)
    spy = view["SPY"]
    assert spy["aggressor_ratio"] > 0
    assert spy["cvd"] > 0
    assert spy["n"] == 9
    assert spy["buy_vol"] > spy["sell_vol"]
    assert "ts" in spy


def test_build_view_empty_is_empty():
    assert ofc.build_order_flow_view({}, 0.0) == {}
    # a symbol with an empty deque is skipped, not emitted.
    assert ofc.build_order_flow_view({"SPY": deque()}, 0.0) == {}


def test_build_view_maps_size_key_too():
    """A caller/test that already normalized to a ``size`` key works as well."""
    windows = {"QQQ": deque()}
    for i in range(4):
        ofc.add_tick(windows, "QQQ",
                     {"last": 50.0, "bid": 49.9, "ask": 50.0, "size": 10}, i)
    view = ofc.build_order_flow_view(windows, 10)
    assert view["QQQ"]["buy_vol"] == 40.0   # 4 × 10, all buys


# ── reconnect_delay ──────────────────────────────────────────────────────────
def test_reconnect_delay_capped_exponential():
    assert ofc.reconnect_delay(0) == 3.0
    assert ofc.reconnect_delay(1) == 6.0
    assert ofc.reconnect_delay(2) == 12.0
    assert ofc.reconnect_delay(100) == 60.0   # capped
