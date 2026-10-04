"""A class share is ``BRK.B`` in this app and ``BRK/B`` at Schwab.

Measured 2026-10-04 through the proxy: Schwab answers ``BRK/B``, ``BF/B``,
``BRK/A``, ``HEI/A``, ``LEN/B`` and ``MOG/A``, and lists ``BRK.B``, ``BF.B``,
``BRK.A``, ``HEI.A`` and ``BRKB`` under ``errors.invalidSymbols``. The app's
ticker allow-list (``shared.symbols.SYMBOL_RE``) accepts a dot and refuses a
slash, so the proxy translates on the way out and back on the way in.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import schwab_proxy  # noqa: E402
import schwab_symbols as ss  # noqa: E402


@pytest.mark.parametrize("app, schwab", [
    ("BRK.B", "BRK/B"), ("BF.B", "BF/B"), ("BRK.A", "BRK/A"), ("HEI.A", "HEI/A"),
    ("LEN.B", "LEN/B"), ("MOG.A", "MOG/A")])
def test_a_class_share_is_spelled_with_a_slash_at_schwab(app, schwab):
    assert ss.to_schwab(app) == schwab


@pytest.mark.parametrize("symbol", [
    "AAPL", "SPY", "$SPX", "$NYHGH.X", "$VIX1D", "/ESU26", "BRK/B", "BRKB",
    ".B", "BRK.", "BRK..B", "BRK.B.C", "brk.b", "A.BCD", "TOOLONGX.B", "", None, 5])
def test_anything_else_is_left_exactly_as_it_is(symbol):
    # An index keeps its dot ($NYHGH.X is a real one), a futures root keeps its
    # slash, and something that is not a symbol is not touched.
    assert ss.to_schwab(symbol) == symbol


def test_outbound_rewrites_the_one_symbol_and_remembers_how_to_undo_it():
    params = {"symbol": "BRK.B", "contractType": "ALL"}
    out, back = ss.outbound(params)
    assert out == {"symbol": "BRK/B", "contractType": "ALL"}
    assert back == {"BRK/B": "BRK.B"}
    assert params == {"symbol": "BRK.B", "contractType": "ALL"}     # caller's dict untouched


def test_outbound_rewrites_inside_a_comma_separated_list():
    out, back = ss.outbound({"symbols": "AAPL,BRK.B,$SPX,BF.B", "fields": "quote"})
    assert out == {"symbols": "AAPL,BRK/B,$SPX,BF/B", "fields": "quote"}
    assert back == {"BRK/B": "BRK.B", "BF/B": "BF.B"}


@pytest.mark.parametrize("params", [None, {}, {"symbol": "AAPL"}, {"symbols": "SPY,QQQ"},
                                    {"symbol": None}, {"symbols": 5}])
def test_outbound_hands_back_the_same_object_when_nothing_changes(params):
    out, back = ss.outbound(params)
    assert out is params and back == {}


def test_inbound_renames_quote_keys_and_their_symbol_fields():
    data = {"AAPL": {"symbol": "AAPL", "quote": {"lastPrice": 1}},
            "BRK/B": {"symbol": "BRK/B", "quote": {"lastPrice": 502.65}}}
    got = ss.inbound(data, {"BRK/B": "BRK.B"})
    assert list(got) == ["AAPL", "BRK.B"]                           # order kept
    assert got["BRK.B"] == {"symbol": "BRK.B", "quote": {"lastPrice": 502.65}}


def test_inbound_restores_a_chain_header_and_leaves_the_contracts_alone():
    data = {"symbol": "BRK/B", "status": "SUCCESS",
            "underlying": {"symbol": "BRK/B", "last": 502.65},
            "callExpDateMap": {"2026-10-09:5": {"505.0": [
                {"symbol": "BRKB  261009C00505000", "putCall": "CALL"}]}}}
    got = ss.inbound(data, {"BRK/B": "BRK.B"})
    assert got["symbol"] == "BRK.B" and got["underlying"]["symbol"] == "BRK.B"
    assert got["callExpDateMap"]["2026-10-09:5"]["505.0"][0]["symbol"] == "BRKB  261009C00505000"


def test_inbound_restores_a_price_history_and_an_instrument_list():
    assert ss.inbound({"symbol": "BRK/B", "candles": []}, {"BRK/B": "BRK.B"}) == {
        "symbol": "BRK.B", "candles": []}
    got = ss.inbound({"instruments": [{"symbol": "BRK/B", "cusip": "x"}]}, {"BRK/B": "BRK.B"})
    assert got == {"instruments": [{"symbol": "BRK.B", "cusip": "x"}]}


def test_inbound_names_a_refused_symbol_the_way_the_caller_wrote_it():
    got = ss.inbound({"errors": {"invalidSymbols": ["BRK/B", "ZZZZ"]}}, {"BRK/B": "BRK.B"})
    assert got == {"errors": {"invalidSymbols": ["BRK.B", "ZZZZ"]}}


@pytest.mark.parametrize("data", [None, [], "text", 5, {"AAPL": 1}])
def test_inbound_with_nothing_to_undo_returns_the_same_object(data):
    assert ss.inbound(data, {}) is data


def test_inbound_never_raises_on_a_shape_it_does_not_know():
    for data in (None, [1, 2], "x", {"symbol": 5, "underlying": [1], "errors": "no",
                                     "instruments": "no", "BRK/B": None}):
        ss.inbound(data, {"BRK/B": "BRK.B"})


def test_a_slash_symbol_that_was_asked_for_as_such_is_not_renamed():
    # Only what THIS request translated is undone.
    data = {"BRK/B": {"symbol": "BRK/B"}}
    assert ss.inbound(data, {}) == {"BRK/B": {"symbol": "BRK/B"}}


# ---- through the proxy's one Schwab call ------------------------------------

class _Resp:
    status_code = 200
    text = ""

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _Session:
    def __init__(self, data):
        self.data, self.sent = data, []

    def get(self, url, headers=None, params=None, timeout=None):
        self.sent.append((url, dict(params or {})))
        return _Resp(self.data)


class _TokenMgr:
    def __init__(self, session):
        self.session = session
        self.tokens = {"AccessToken": "tok"}
        self._lock = __import__("threading").Lock()

    def ensure_valid_token(self):
        pass

    def _rate_limit(self):
        pass

    def _refresh(self):
        pass


def _call(data, endpoint, params):
    session = _Session(data)
    result = schwab_proxy.TokenManager.api_request(_TokenMgr(session), endpoint, params=params)
    return session.sent, result


def test_a_chain_request_for_a_class_share_reaches_schwab_with_a_slash():
    sent, result = _call({"symbol": "BRK/B", "status": "SUCCESS", "callExpDateMap": {}},
                         "/chains", {"symbol": "BRK.B", "contractType": "ALL"})
    assert sent[0][1]["symbol"] == "BRK/B"
    assert result["status_code"] == 200 and result["data"]["symbol"] == "BRK.B"


def test_a_quote_request_comes_back_keyed_the_way_it_was_asked():
    sent, result = _call({"AAPL": {"symbol": "AAPL"}, "BRK/B": {"symbol": "BRK/B"}},
                         "/quotes", {"symbols": "AAPL,BRK.B"})
    assert sent[0][1]["symbols"] == "AAPL,BRK/B"
    assert set(result["data"]) == {"AAPL", "BRK.B"}


def test_an_ordinary_request_is_sent_and_returned_untouched():
    data = {"AAPL": {"symbol": "AAPL"}}
    params = {"symbols": "AAPL"}
    sent, result = _call(data, "/quotes", params)
    assert sent[0][1] == {"symbols": "AAPL"} and result["data"] is data
