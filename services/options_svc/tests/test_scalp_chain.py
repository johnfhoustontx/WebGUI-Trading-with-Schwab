"""The Opening Scalp's 08:36 chain capture (scalp_chain) and its scheduler gate.

No network and no live store: a fake client answers like the proxy's
``get_option_chain`` and every save goes to a ``tmp_path`` database."""
import datetime as dt
from zoneinfo import ZoneInfo

from services.options_svc import scalp_chain as sc
from services.options_svc import scalp_store as st
from services.options_svc import scheduler

_CT = ZoneInfo("America/Chicago")
DAY = "2026-10-19"
KEEP = ("bid", "ask", "mark", "delta", "gamma", "theta", "vega", "volatility",
        "totalVolume", "openInterest")


def _contract(strike, side, iv=20.0):
    return {"strikePrice": strike, "putCall": side, "bid": 1.0, "ask": 1.2,
            "mark": 1.1, "delta": 0.5 if side == "CALL" else -0.5, "gamma": 0.1,
            "theta": -0.2, "vega": 0.05, "volatility": iv, "totalVolume": 10,
            "openInterest": 100, "symbol": f"X{strike}{side}"}


def _chain(spot=571.3, strikes=range(550, 600), iv=20.0, symbol="SPY"):
    """A 0DTE chain, one strike per dollar, strike keys like Schwab's ``"571.0"``."""
    def side(name):
        return {f"{DAY}:0": {f"{float(k)}": [_contract(float(k), name, iv)]
                             for k in strikes}}
    return {"symbol": symbol, "status": "SUCCESS", "underlyingPrice": spot,
            "callExpDateMap": side("CALL"), "putExpDateMap": side("PUT")}


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


class FakeClient:
    """Answers ``get_option_chain`` per symbol from ``answers`` (a dict or a
    callable); an exception instance in it is raised."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def get_option_chain(self, symbol, **kw):
        self.calls.append((symbol, kw))
        a = self.answers(symbol) if callable(self.answers) else self.answers[symbol]
        if isinstance(a, Exception):
            raise a
        return a


def _keys(m):
    (exp,) = m.keys()
    return sorted(float(k) for k in m[exp])


def test_slice_keeps_n_each_side():
    out = sc.near_money_slice(_chain(spot=571.3), 571.3, 3)
    # nearest strike is 571; three either side
    assert _keys(out["callExpDateMap"]) == [568.0, 569.0, 570.0, 571.0, 572.0, 573.0, 574.0]
    assert _keys(out["putExpDateMap"]) == _keys(out["callExpDateMap"])
    # same nested shape, string strike keys, list of contracts, fields kept
    exp = f"{DAY}:0"
    row = out["callExpDateMap"][exp]["571.0"][0]
    assert all(k in row for k in KEEP)
    # the header survives
    assert out["underlyingPrice"] == 571.3 and out["symbol"] == "SPY"


def test_slice_near_the_edge_keeps_what_exists():
    out = sc.near_money_slice(_chain(spot=550.2, strikes=range(550, 560)), 550.2, 3)
    assert _keys(out["callExpDateMap"]) == [550.0, 551.0, 552.0, 553.0]


def test_capture_saves_and_returns_slice(tmp_path):
    db = str(tmp_path / "scalp.db")
    client = FakeClient({"SPY": _Resp(200, _chain())})
    out = sc.capture("SPY", DAY, client=client, db_path=db)
    assert out is not None and out["underlyingPrice"] == 571.3
    assert len(_keys(out["callExpDateMap"])) == 21           # chain_strikes() = 10
    assert st.chain_slice(DAY, "SPY", db_path=db) == out
    (symbol, kw), = client.calls
    assert symbol == "SPY"
    assert kw["from_date"] == DAY and kw["to_date"] == DAY
    assert not kw.get("priority")                             # never the poll's lane


def test_capture_failure_returns_none_and_saves_nothing(tmp_path):
    db = str(tmp_path / "scalp.db")
    for bad in (_Resp(500, {}), _Resp(200, {"status": "FAILED"}),
                _Resp(200, {"callExpDateMap": {}, "putExpDateMap": {},
                            "underlyingPrice": 571.3}),
                _Resp(200, _chain(spot=None)), RuntimeError("boom")):
        client = FakeClient({"SPY": bad})
        assert sc.capture("SPY", DAY, client=client, db_path=db) is None
    assert st.chain_slice(DAY, "SPY", db_path=db) is None


def test_capture_all_one_failure_does_not_stop_the_others(tmp_path):
    db = str(tmp_path / "scalp.db")
    client = FakeClient({"$SPX": _Resp(200, _chain(5710.0, range(5600, 5800, 5),
                                                    symbol="$SPX")),
                         "SPY": RuntimeError("boom"),
                         "QQQ": _Resp(200, _chain(480.2, range(450, 510),
                                                  symbol="QQQ"))})
    out = sc.capture_all(day=DAY, client=client, db_path=db)
    assert list(out) == ["$SPX", "SPY", "QQQ"]
    assert out["SPY"] is None and out["$SPX"] and out["QQQ"]
    assert st.chain_slice(DAY, "$SPX", db_path=db) is not None
    assert st.chain_slice(DAY, "SPY", db_path=db) is None
    assert st.chain_slice(DAY, "QQQ", db_path=db) is not None


def test_atm_iv_is_the_mean_of_the_atm_call_and_put():
    chain = _chain(spot=571.3, iv=20.0)
    exp = f"{DAY}:0"
    chain["putExpDateMap"][exp]["571.0"][0]["volatility"] = 24.0
    sl = sc.near_money_slice(chain, 571.3, 3)
    assert abs(sc.atm_iv(sl, 571.3) - 0.22) < 1e-9


def test_atm_iv_refuses_minus_999():
    chain = _chain(spot=571.3, iv=20.0)
    exp = f"{DAY}:0"
    chain["callExpDateMap"][exp]["571.0"][0]["volatility"] = -999.0
    sl = sc.near_money_slice(chain, 571.3, 3)
    assert sc.atm_iv(sl, 571.3) is None
    chain = _chain(spot=571.3, iv=20.0)
    chain["putExpDateMap"][exp]["571.0"][0]["volatility"] = float("nan")
    assert sc.atm_iv(sc.near_money_slice(chain, 571.3, 3), 571.3) is None
    assert sc.atm_iv({}, 571.3) is None


def test_atm_iv_reads_the_spxw_contract_when_a_strike_lists_two_roots():
    # A $SPX chain can list the monthly SPX and the weekly SPXW under one strike;
    # the scalp trades SPXW, so its volatility is the one read, whichever is first.
    chain = _chain(spot=5000.0, strikes=range(4990, 5011, 5), iv=20.0, symbol="$SPX")
    exp = f"{DAY}:0"
    for name, side in (("callExpDateMap", "C"), ("putExpDateMap", "P")):
        weekly = dict(chain[name][exp]["5000.0"][0], volatility=30.0,
                      symbol=f"SPXW  261019{side}05000000")
        monthly = dict(chain[name][exp]["5000.0"][0], volatility=12.0,
                       symbol=f"SPX   261019{side}05000000")
        chain[name][exp]["5000.0"] = [monthly, weekly]
    assert abs(sc.atm_iv(chain, 5000.0) - 0.30) < 1e-9
    # A weekly whose volatility is not computed falls back to a usable contract.
    for name in ("callExpDateMap", "putExpDateMap"):
        chain[name][exp]["5000.0"][1]["volatility"] = -999.0
    assert abs(sc.atm_iv(chain, 5000.0) - 0.12) < 1e-9


def test_preferred_contract_takes_spxw_then_the_first_usable():
    a = {"symbol": "SPX   261019C05000000", "bid": None}
    b = {"symbol": "SPXW  261019C05000000", "bid": 1.0}
    assert sc.preferred_contract([a, b]) is b
    assert sc.preferred_contract([a, b], ok=lambda c: c["bid"] is None) is a
    assert sc.preferred_contract([{"symbol": "SPY"}, "junk"])["symbol"] == "SPY"
    assert sc.preferred_contract([]) is None and sc.preferred_contract(None) is None


def test_scalp_call_due_fires_at_0836_not_0835():
    ran = set()
    mon = lambda h, m: dt.datetime(2026, 6, 15, h, m, tzinfo=_CT)
    assert scheduler.scalp_call_due(mon(8, 35), ran) is None
    slot = scheduler.scalp_call_due(mon(8, 36), ran)
    assert slot is not None
    ran.add(("2026-06-15", slot))
    assert scheduler.scalp_call_due(mon(8, 37), ran) is None            # latched
    assert scheduler.scalp_call_due(mon(8, 55), set()) == slot          # inside grace
    assert scheduler.scalp_call_due(mon(8, 56), set()) is None          # past grace
    sat = dt.datetime(2026, 6, 13, 8, 36, tzinfo=_CT)
    assert scheduler.scalp_call_due(sat, set()) is None
