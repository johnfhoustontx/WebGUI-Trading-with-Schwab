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


def test_build_view_pins_last_size_to_size_mapping():
    """VALUE-pinned: with only the proxy ``last_size`` field (NO ``size`` key),
    ``build_order_flow_view`` must weight each trade's volume by ``last_size`` —
    dropping the ``_of_tick`` last_size fallback (volumes collapsing to the 1.0
    count default) would fail these exact totals. Guards the load-bearing mapping.
    """
    windows = {"SPY": deque()}
    now = 500.0
    # 8 buyer-initiated (last >= ask) at last_size 5 → buy_vol 40.0.
    for i in range(8):
        ofc.add_tick(windows, "SPY",
                     {"last": 100.0, "bid": 99.9, "ask": 100.0, "last_size": 5},
                     now + i)
    # 3 seller-initiated (last <= bid) at last_size 4 → sell_vol 12.0.
    for i in range(3):
        ofc.add_tick(windows, "SPY",
                     {"last": 99.9, "bid": 99.9, "ask": 100.0, "last_size": 4},
                     now + 8 + i)
    spy = ofc.build_order_flow_view(windows, now + 20)["SPY"]
    assert spy["buy_vol"] == 40.0      # 8 × last_size 5, NOT 8 × 1.0
    assert spy["sell_vol"] == 12.0     # 3 × last_size 4, NOT 3 × 1.0
    assert spy["cvd"] == 28.0          # 40 − 12
    assert spy["n"] == 11


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


# ── near_atm_osis ────────────────────────────────────────────────────────────
def _osi(root, ymd, cp, strike):
    return f"{root:<6}{ymd}{cp}{int(round(strike * 1000)):08d}"


def _fake_chain(strikes):
    """A tiny Schwab chain with a front + a later expiration on both sides.
    Each contract dict carries its ``symbol`` (OSI)."""
    def side(cp):
        front = {f"{k:.1f}": [{"symbol": _osi("SPY", "260717", cp, k)}]
                 for k in strikes}
        later = {f"{k:.1f}": [{"symbol": _osi("SPY", "260821", cp, k)}]
                 for k in strikes}
        return {"2026-07-17:3": front, "2026-08-21:38": later}
    return {"callExpDateMap": side("C"), "putExpDateMap": side("P"),
            "underlyingPrice": 500.0}


def test_near_atm_osis_picks_n_side_nearest_front():
    chain = _fake_chain([490, 495, 500, 505, 510])
    osis = ofc.near_atm_osis(chain, spot=500.0, n_side=2)
    # 2 calls + 2 puts nearest 500 at the FRONT (2026-07-17) expiration only.
    assert len(osis) == 4
    assert all("260717" in o for o in osis)          # front expiration only
    assert all("260821" not in o for o in osis)
    # nearest two strikes to 500 are 500 & 505 (|0| and |5| beat 495's |5| tie-break
    # by dict order) — assert 500 is present on both sides.
    assert _osi("SPY", "260717", "C", 500) in osis
    assert _osi("SPY", "260717", "P", 500) in osis
    types = [ofc.order_flow_mod.osi_option_type(o) for o in osis]
    assert types.count("C") == 2 and types.count("P") == 2


def test_near_atm_osis_split_calls_and_puts():
    chain = _fake_chain([495, 500, 505])
    osis = ofc.near_atm_osis(chain, spot=500.0, n_side=1)
    assert len(osis) == 2
    types = {ofc.order_flow_mod.osi_option_type(o) for o in osis}
    assert types == {"C", "P"}


def test_near_atm_osis_bad_chain_is_empty():
    assert ofc.near_atm_osis(None, 500.0) == []
    assert ofc.near_atm_osis({}, 500.0) == []
    assert ofc.near_atm_osis({"callExpDateMap": {}}, 500.0) == []
    assert ofc.near_atm_osis(_fake_chain([500]), spot=None) == []


# ── build_option_flow_view ───────────────────────────────────────────────────
def test_build_option_view_put_buying_negative():
    """A window of near-ATM PUT ticks lifted at the ask → signal < 0 (bearish)."""
    window = deque()
    put = _osi("SPY", "260717", "P", 500)
    now = 500.0
    for i in range(6):
        # proxy option tick shape: symbol=OSI, last_size (mapped to size).
        ofc.add_option_tick(
            window, {"symbol": put, "last": 5.0, "bid": 4.9, "ask": 5.0,
                     "last_size": 10}, now + i)
    view = ofc.build_option_flow_view(window, now + 10)
    assert view["put_buy"] == 60.0        # 6 × last_size 10 (last_size→size mapping)
    assert view["signal"] < 0
    assert view["n"] == 6
    assert "ts" in view


def test_build_option_view_call_buying_positive():
    window = deque()
    call = _osi("SPY", "260717", "C", 500)
    for i in range(5):
        ofc.add_option_tick(
            window, {"symbol": call, "last": 5.0, "bid": 4.9, "ask": 5.0,
                     "last_size": 4}, i)
    view = ofc.build_option_flow_view(window, 10)
    assert view["call_buy"] == 20.0
    assert view["signal"] > 0


def test_build_option_view_empty_is_empty():
    assert ofc.build_option_flow_view(deque(), 0.0) == {}


def test_add_option_tick_ignores_no_symbol():
    window = deque()
    ofc.add_option_tick(window, {"last": 5.0}, now=1.0)   # no symbol
    ofc.add_option_tick(window, "not-a-dict", now=1.0)
    assert list(window) == []


def test_prune_option_drops_old():
    window = deque()
    now = 1000.0
    ofc.add_option_tick(window, {"symbol": "SPY   260717C00500000"}, now - 400)
    ofc.add_option_tick(window, {"symbol": "SPY   260717C00500000"}, now - 100)
    ofc.prune_option(window, now, window_sec=300)
    assert len(window) == 1
