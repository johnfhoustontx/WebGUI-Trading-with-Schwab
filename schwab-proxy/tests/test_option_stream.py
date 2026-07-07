"""Option SSE fan-out + widened option normalizer + ADDITIVE flow subscription.

These cover the additive option streaming path. The CRITICAL tests prove the
flow subscription can never drop a tracked trade leg and a trade untrack can
never strand a wanted flow OSI, and that the trade-detector path in
_on_option_message still runs unchanged.
"""
import sys, pathlib, asyncio, collections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import schwab_proxy
from schwab_proxy import (
    _normalize_level1_option, _update_option_refcount, _option_enqueue,
    _on_option_message,
)
from trade_registry import TradeRegistry


#############################################
# NORMALIZER
#############################################

def _expected(symbol, **over):
    base = {"symbol": symbol, "last": None, "last_size": None,
            "bid": None, "ask": None}
    base.update(over)
    return base


def test_normalize_option_all_fields_numeric_keys():
    # 2=BID_PRICE, 3=ASK_PRICE, 4=LAST_PRICE, 18=LAST_SIZE (LevelOneOptionFields).
    item = {"key": "SPXW_X", "2": 1.20, "3": 1.30, "4": 1.25, "18": 10.0}
    assert _normalize_level1_option(item) == _expected(
        "SPXW_X", last=1.25, last_size=10.0, bid=1.20, ask=1.30)


def test_normalize_option_all_fields_by_enum_name():
    item = {"key": "SPXW_X", "BID_PRICE": 1.20, "ASK_PRICE": 1.30,
            "LAST_PRICE": 1.25, "LAST_SIZE": 10.0}
    assert _normalize_level1_option(item) == _expected(
        "SPXW_X", last=1.25, last_size=10.0, bid=1.20, ask=1.30)


def test_normalize_option_missing_fields_are_none():
    assert _normalize_level1_option({"key": "SPXW_Y"}) == _expected("SPXW_Y")


def test_normalize_option_numeric_key_fallback():
    # enum-name absent, raw numeric key present -> numeric fallback used.
    item = {"key": "SPXW_Z", "4": "2.50", "18": "7"}
    out = _normalize_level1_option(item)
    assert out["last"] == 2.50 and isinstance(out["last"], float)
    assert out["last_size"] == 7.0 and isinstance(out["last_size"], float)
    assert out["bid"] is None and out["ask"] is None


def test_normalize_option_unparseable_is_none():
    item = {"key": "SPXW_B", "4": "n/a", "18": None}
    out = _normalize_level1_option(item)
    assert out["last"] is None and out["last_size"] is None


def test_normalize_option_missing_key_yields_empty_symbol():
    out = _normalize_level1_option({"4": 3.0})
    assert out["symbol"] == "" and out["last"] == 3.0


#############################################
# OPTION REFCOUNT (pure) — copy of the equity refcount lifecycle
#############################################

def test_update_option_refcount_add_remove_lifecycle():
    counter = collections.Counter()
    union = _update_option_refcount(counter, ["OPT_A", "OPT_B"], add=True)
    assert union == {"OPT_A", "OPT_B"}

    union = _update_option_refcount(counter, ["OPT_A"], add=True)
    assert union == {"OPT_A", "OPT_B"} and counter["OPT_A"] == 2

    union = _update_option_refcount(counter, ["OPT_A"], add=False)
    assert union == {"OPT_A", "OPT_B"} and counter["OPT_A"] == 1

    union = _update_option_refcount(counter, ["OPT_A"], add=False)
    assert union == {"OPT_B"} and "OPT_A" not in counter

    union = _update_option_refcount(counter, ["OPT_B"], add=False)
    assert union == set() and list(counter) == []


#############################################
# BOUNDED ENQUEUE (drop-oldest)
#############################################

def test_option_enqueue_drops_oldest_when_full():
    async def run():
        queue = asyncio.Queue(maxsize=1)
        old = {"symbol": "OPT", "last": 1.0}
        new = {"symbol": "OPT", "last": 2.0}
        _option_enqueue(queue, old)
        _option_enqueue(queue, new)
        assert queue.qsize() == 1
        assert queue.get_nowait() is new
    asyncio.run(run())


#############################################
# FAN-OUT (subscriber matching, no real loop/socket)
#############################################

class _StubLoop:
    def __init__(self):
        self.calls = []

    def call_soon_threadsafe(self, fn, *args):
        self.calls.append((fn, args))


def test_on_option_message_fans_out_with_osi_filtering(monkeypatch):
    # No tracked trades -> the detector path is a no-op; only the fan-out runs.
    monkeypatch.setattr(schwab_proxy, "_registry", TradeRegistry())
    monkeypatch.setattr(schwab_proxy, "_leg_quotes", {})
    x_loop, y_loop = _StubLoop(), _StubLoop()
    fake = {
        "x-sub": {"loop": x_loop, "queue": object(), "osis": {"OPT_X"}},
        "y-sub": {"loop": y_loop, "queue": object(), "osis": {"OPT_Y"}},
    }
    with schwab_proxy._option_lock:
        schwab_proxy._option_subscribers.update(fake)
    try:
        _on_option_message({"content": [
            {"key": "OPT_X", "LAST_PRICE": 1.25, "LAST_SIZE": 10.0,
             "BID_PRICE": 1.20, "ASK_PRICE": 1.30},
            {"key": "OPT_Q", "LAST_PRICE": 9.0},
        ]})
    finally:
        with schwab_proxy._option_lock:
            for k in fake:
                schwab_proxy._option_subscribers.pop(k, None)

    # The OPT_X subscriber got exactly one tick: OPT_X (not OPT_Q).
    assert len(x_loop.calls) == 1
    fn, args = x_loop.calls[0]
    assert fn is _option_enqueue
    q, tick = args
    assert q is fake["x-sub"]["queue"]
    assert tick == {"symbol": "OPT_X", "last": 1.25, "last_size": 10.0,
                    "bid": 1.20, "ask": 1.30}
    # The OPT_Y subscriber matched nothing -> no calls.
    assert y_loop.calls == []


#############################################
# TRADE-DETECTOR PATH STILL RUNS (byte-identical coexistence)
#############################################

def test_on_option_message_still_runs_trade_detector(monkeypatch):
    reg = TradeRegistry()
    reg.add({
        "trade_id": "t1", "strategy": "PCS", "entry_credit": 1.0, "quantity": 1,
        "short_strike": 100, "long_strike": 95,
        "target_mid": 0.5, "stop_mid": 2.0,
        "legs": {"put_short": "OPT_PS", "put_long": "OPT_PL"},
        "fired": set(),
    })
    monkeypatch.setattr(schwab_proxy, "_registry", reg)
    monkeypatch.setattr(schwab_proxy, "_leg_quotes", {})
    monkeypatch.setattr(schwab_proxy, "_entry_snapped", set())

    evals = []
    monkeypatch.setattr(schwab_proxy.trade_detector, "evaluate",
                        lambda state, legs, u, ts: evals.append((state["trade_id"], legs)) or None)
    snaps = []
    monkeypatch.setattr(schwab_proxy.perf_writer, "record_iv_snapshot",
                        lambda row: snaps.append(row))

    # First leg only -> not fully quoted -> detector not called yet.
    _on_option_message({"content": [
        {"key": "OPT_PS", "BID_PRICE": 1.0, "ASK_PRICE": 1.1,
         "VOLATILITY": 0.30, "UNDERLYING_PRICE": 99.0},
    ]})
    assert evals == []
    assert schwab_proxy._leg_quotes["OPT_PS"] == {"bid": 1.0, "ask": 1.1, "iv": 0.30}

    # Second leg -> both quoted -> entry snapshot + detector runs.
    _on_option_message({"content": [
        {"key": "OPT_PL", "BID_PRICE": 0.4, "ASK_PRICE": 0.5,
         "VOLATILITY": 0.25, "UNDERLYING_PRICE": 99.0},
    ]})
    assert any(tid == "t1" for tid, _ in evals)
    assert schwab_proxy._leg_quotes["OPT_PL"] == {"bid": 0.4, "ask": 0.5, "iv": 0.25}
    assert any(s["moment"] == "entry" for s in snaps)


#############################################
# CRITICAL: FLOW SUBSCRIPTION IS ADDITIVE TO TRADE TRACKING
#############################################

class _StubStreamClient:
    def __init__(self):
        self.subs_calls = []
        self.unsubs_calls = []

    async def level_one_option_subs(self, osis):
        self.subs_calls.append(list(osis))

    async def level_one_option_unsubs(self, osis):
        self.unsubs_calls.append(list(osis))


def test_reconcile_option_union_always_includes_trade_legs(monkeypatch):
    """Adding/removing a FLOW OSI never removes a tracked trade leg from the
    subscribed union (the reconcile always subs legs_union() | refcount)."""
    reg = TradeRegistry()
    reg.add({"trade_id": "t1", "legs": {"put_short": "OPT_X", "put_long": "OPT_W"}})
    stub = _StubStreamClient()
    monkeypatch.setattr(schwab_proxy, "_registry", reg)
    monkeypatch.setattr(schwab_proxy, "_stream_client", stub)
    monkeypatch.setattr(schwab_proxy, "_option_subscribed", set())
    monkeypatch.setattr(schwab_proxy, "_option_refcount", collections.Counter())

    # Flow wants OPT_Y -> union includes both trade legs + OPT_Y.
    _update_option_refcount(schwab_proxy._option_refcount, ["OPT_Y"], add=True)
    asyncio.run(schwab_proxy._reconcile_option_subscription())
    assert set(stub.subs_calls[-1]) == {"OPT_X", "OPT_W", "OPT_Y"}

    # Flow drops OPT_Y -> trade legs STILL present, OPT_Y removed (replace subs).
    _update_option_refcount(schwab_proxy._option_refcount, ["OPT_Y"], add=False)
    asyncio.run(schwab_proxy._reconcile_option_subscription())
    assert set(stub.subs_calls[-1]) == {"OPT_X", "OPT_W"}
    # never unsubscribed everything while a trade leg exists.
    assert stub.unsubs_calls == []


def test_untrack_spares_flow_wanted_leg(monkeypatch):
    """Untracking a trade does not strand a flow OSI that is still wanted: a leg
    the flow refcount still wants is spared from the orphan unsubscribe."""
    reg = TradeRegistry()
    reg.add({"trade_id": "t1",
             "legs": {"put_short": "OPT_SHARED", "put_long": "OPT_LONG"}})
    monkeypatch.setattr(schwab_proxy, "_registry", reg)
    monkeypatch.setattr(schwab_proxy, "_option_refcount",
                        collections.Counter({"OPT_SHARED": 1}))
    monkeypatch.setattr(schwab_proxy, "_entry_snapped", set())
    recorded = []
    monkeypatch.setattr(schwab_proxy, "_unsubscribe",
                        lambda osis: recorded.append(set(osis)))

    res = schwab_proxy._untrack("t1")
    assert res["status"] == "ok"
    # OPT_LONG (nothing else wants it) is orphaned; OPT_SHARED (flow wants) spared.
    assert recorded == [{"OPT_LONG"}]


def test_untrack_no_orphans_when_all_legs_wanted_by_flow(monkeypatch):
    """If every leg is still wanted by flow, untrack unsubscribes nothing."""
    reg = TradeRegistry()
    reg.add({"trade_id": "t1",
             "legs": {"put_short": "OPT_A", "put_long": "OPT_B"}})
    monkeypatch.setattr(schwab_proxy, "_registry", reg)
    monkeypatch.setattr(schwab_proxy, "_option_refcount",
                        collections.Counter({"OPT_A": 1, "OPT_B": 2}))
    monkeypatch.setattr(schwab_proxy, "_entry_snapped", set())
    recorded = []
    monkeypatch.setattr(schwab_proxy, "_unsubscribe",
                        lambda osis: recorded.append(set(osis)))

    schwab_proxy._untrack("t1")
    assert recorded == []  # _unsubscribe never called (nothing orphaned)


def test_reconcile_option_unsubs_all_when_no_trades_and_no_flow(monkeypatch):
    """When neither a trade nor flow wants anything, the union empties and the
    previously-subscribed set is unsubscribed."""
    reg = TradeRegistry()  # empty
    stub = _StubStreamClient()
    monkeypatch.setattr(schwab_proxy, "_registry", reg)
    monkeypatch.setattr(schwab_proxy, "_stream_client", stub)
    monkeypatch.setattr(schwab_proxy, "_option_subscribed", {"OPT_OLD"})
    monkeypatch.setattr(schwab_proxy, "_option_refcount", collections.Counter())

    asyncio.run(schwab_proxy._reconcile_option_subscription())
    assert stub.unsubs_calls == [["OPT_OLD"]]
    assert stub.subs_calls == []
