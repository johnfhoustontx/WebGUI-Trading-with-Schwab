import sys, pathlib, json, asyncio, collections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import schwab_proxy
from schwab_proxy import (
    _normalize_level1_equity, _sse_format, app,
    _update_equity_refcount, _equity_enqueue, _on_equity_message,
)


#############################################
# NORMALIZER
#############################################

def test_normalize_basic_numeric_keys():
    # 3 = LAST_PRICE, 18 = NET_CHANGE per schwab-py LevelOneEquityFields.
    item = {"key": "AAPL", "3": 150.25, "18": 1.10}
    assert _normalize_level1_equity(item) == {
        "symbol": "AAPL", "last": 150.25, "net_change": 1.10,
    }


def test_normalize_missing_fields_are_none():
    item = {"key": "XLK"}
    assert _normalize_level1_equity(item) == {
        "symbol": "XLK", "last": None, "net_change": None,
    }


def test_normalize_missing_key_yields_empty_symbol():
    item = {"3": 99.0}
    out = _normalize_level1_equity(item)
    assert out["symbol"] == ""
    assert out["last"] == 99.0
    assert out["net_change"] is None


def test_normalize_ignores_unknown_fields():
    item = {"key": "SPY", "3": 500.0, "18": -2.5, "7": "garbage", "foo": "bar"}
    assert _normalize_level1_equity(item) == {
        "symbol": "SPY", "last": 500.0, "net_change": -2.5,
    }


def test_normalize_coerces_string_numerics_to_float():
    item = {"key": "MSFT", "3": "412.75", "18": "0.5"}
    out = _normalize_level1_equity(item)
    assert out["last"] == 412.75
    assert isinstance(out["last"], float)
    assert out["net_change"] == 0.5
    assert isinstance(out["net_change"], float)


def test_normalize_tolerates_already_float():
    item = {"key": "QQQ", "3": 480.0, "18": 3.0}
    out = _normalize_level1_equity(item)
    assert isinstance(out["last"], float)
    assert isinstance(out["net_change"], float)


def test_normalize_unparseable_value_is_none():
    item = {"key": "BAD", "3": "n/a", "18": None}
    out = _normalize_level1_equity(item)
    assert out["last"] is None
    assert out["net_change"] is None


def test_normalize_reads_relabeled_enum_names():
    # schwab-py handle_message() relabels numeric keys to UPPERCASE enum names.
    item = {"key": "AAPL", "LAST_PRICE": 150.25, "NET_CHANGE": 1.10}
    assert _normalize_level1_equity(item) == {
        "symbol": "AAPL", "last": 150.25, "net_change": 1.10,
    }


#############################################
# SSE FORMATTER
#############################################

def test_sse_format_frames_data_line():
    payload = {"symbol": "AAPL", "last": 150.25, "net_change": 1.10}
    out = _sse_format(payload)
    assert out.startswith("data: ")
    assert out.endswith("\n\n")
    body = out[len("data: "):-2]
    assert json.loads(body) == payload


#############################################
# EMPTY-SYMBOLS GUARD
#############################################
# These hit the synchronous guard at the top of /stream/quotes, which runs
# BEFORE event_generator() (and thus before any StreamClient build / login),
# so no real Schwab session is touched.

def test_stream_quotes_empty_symbols_returns_400():
    client = TestClient(app)
    resp = client.get("/stream/quotes?symbols=")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "symbols query param required"


def test_stream_quotes_blank_symbols_returns_400():
    client = TestClient(app)
    resp = client.get("/stream/quotes?symbols=,%20,")
    assert resp.status_code == 400


#############################################
# EQUITY REFCOUNT (pure)
#############################################

def test_update_equity_refcount_add_remove_lifecycle():
    counter = collections.Counter()

    # add two symbols -> union {AAPL, SPY}
    union = _update_equity_refcount(counter, ["AAPL", "SPY"], add=True)
    assert union == {"AAPL", "SPY"}

    # add AAPL again -> still {AAPL, SPY}, AAPL count 2
    union = _update_equity_refcount(counter, ["AAPL"], add=True)
    assert union == {"AAPL", "SPY"}
    assert counter["AAPL"] == 2

    # remove AAPL once -> still {AAPL, SPY} (count back to 1)
    union = _update_equity_refcount(counter, ["AAPL"], add=False)
    assert union == {"AAPL", "SPY"}
    assert counter["AAPL"] == 1

    # remove AAPL again -> AAPL dropped, {SPY}
    union = _update_equity_refcount(counter, ["AAPL"], add=False)
    assert union == {"SPY"}
    assert "AAPL" not in counter

    # remove SPY -> empty union, empty counter
    union = _update_equity_refcount(counter, ["SPY"], add=False)
    assert union == set()
    assert list(counter) == []


#############################################
# BOUNDED ENQUEUE (drop-oldest)
#############################################

def test_equity_enqueue_drops_oldest_when_full():
    async def run():
        queue = asyncio.Queue(maxsize=1)
        old = {"symbol": "AAPL", "last": 1.0}
        new = {"symbol": "AAPL", "last": 2.0}
        _equity_enqueue(queue, old)   # fills the queue
        _equity_enqueue(queue, new)   # full -> drop oldest, keep newest
        assert queue.qsize() == 1
        assert queue.get_nowait() is new

    asyncio.run(run())


#############################################
# EQUITY MESSAGE FAN-OUT (subscriber matching, no real loop/socket)
#############################################

class _StubLoop:
    """Records call_soon_threadsafe(fn, *args) calls without executing them."""
    def __init__(self):
        self.calls = []  # list of (fn, args)

    def call_soon_threadsafe(self, fn, *args):
        self.calls.append((fn, args))


class _RaisingLoop:
    """A loop whose call_soon_threadsafe raises, to exercise the per-target guard."""
    def __init__(self):
        self.calls = []

    def call_soon_threadsafe(self, fn, *args):
        self.calls.append((fn, args))
        raise RuntimeError("boom")


def test_on_equity_message_fans_out_with_symbol_filtering():
    aapl_loop = _StubLoop()
    spy_loop = _StubLoop()
    fake_subscribers = {
        "aapl-sub": {"loop": aapl_loop, "queue": object(), "symbols": {"AAPL"}},
        "spy-sub": {"loop": spy_loop, "queue": object(), "symbols": {"SPY"}},
    }
    with schwab_proxy._equity_lock:
        schwab_proxy._equity_subscribers.update(fake_subscribers)
    try:
        _on_equity_message({"content": [
            {"key": "AAPL", "LAST_PRICE": 150.0, "NET_CHANGE": 1.0},
            {"key": "XOM", "LAST_PRICE": 100.0, "NET_CHANGE": -0.5},
        ]})
    finally:
        with schwab_proxy._equity_lock:
            for k in fake_subscribers:
                schwab_proxy._equity_subscribers.pop(k, None)

    # The AAPL subscriber got exactly one tick: AAPL (not XOM).
    assert len(aapl_loop.calls) == 1
    fn, args = aapl_loop.calls[0]
    assert fn is _equity_enqueue
    enqueue_queue, tick = args
    assert enqueue_queue is fake_subscribers["aapl-sub"]["queue"]
    assert tick == {"symbol": "AAPL", "last": 150.0, "net_change": 1.0}

    # The SPY subscriber matched neither AAPL nor XOM -> no calls.
    assert spy_loop.calls == []


def test_on_equity_message_one_bad_target_does_not_block_others():
    good_loop = _StubLoop()
    bad_loop = _RaisingLoop()
    fake_subscribers = {
        # bad-sub registered first so its raising call fires before the good one.
        "bad-sub": {"loop": bad_loop, "queue": object(), "symbols": {"AAPL"}},
        "good-sub": {"loop": good_loop, "queue": object(), "symbols": {"AAPL"}},
    }
    with schwab_proxy._equity_lock:
        schwab_proxy._equity_subscribers.update(fake_subscribers)
    try:
        _on_equity_message({"content": [
            {"key": "AAPL", "LAST_PRICE": 150.0, "NET_CHANGE": 1.0},
        ]})
    finally:
        with schwab_proxy._equity_lock:
            for k in fake_subscribers:
                schwab_proxy._equity_subscribers.pop(k, None)

    # The raising target was attempted...
    assert len(bad_loop.calls) == 1
    # ...and the good target still received its tick despite the other raising.
    assert len(good_loop.calls) == 1
    fn, args = good_loop.calls[0]
    assert fn is _equity_enqueue
    _, tick = args
    assert tick == {"symbol": "AAPL", "last": 150.0, "net_change": 1.0}
