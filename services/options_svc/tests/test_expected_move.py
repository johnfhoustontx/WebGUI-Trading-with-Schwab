# services/options_svc/tests/test_expected_move.py
from services.options_svc import compute


def _chain(vol_by_strike, exp_key="2026-07-18:28"):
    return {"callExpDateMap": {exp_key: {
        f"{k:.1f}": [{"volatility": v}] for k, v in vol_by_strike.items()}}}


def test_atm_iv_picks_nearest_strike_and_normalizes_percent():
    chain = _chain({100.0: 18.0, 105.0: 22.0})  # Schwab gives vol as a percent
    iv = compute.atm_iv_from_chain(chain, spot=101.0, expiry="2026-07-18")
    assert abs(iv - 0.18) < 1e-9  # nearest strike 100 -> 18% -> 0.18 decimal


def test_atm_iv_none_when_no_contracts():
    assert compute.atm_iv_from_chain({}, spot=100.0, expiry="2026-07-18") is None


import math


def test_em_cone_widens_as_sqrt_time():
    cone = compute.em_cone(spot=100.0, atm_iv=0.20, dte=5, start_ts_ms=0)
    upper, lower = cone["upper"], cone["lower"]
    assert len(upper) == 6 and len(lower) == 6
    assert upper[0][1] == 100.0 and lower[0][1] == 100.0
    w3 = 100.0 * 0.20 * math.sqrt(3 / 365)
    assert abs(upper[3][1] - (100.0 + w3)) < 1e-9
    assert abs(lower[3][1] - (100.0 - w3)) < 1e-9
    assert upper[1][0] - upper[0][0] == 86_400_000


def test_em_cone_empty_on_bad_inputs():
    assert compute.em_cone(None, 0.2, 5, 0) == {"upper": [], "lower": []}
    assert compute.em_cone(100.0, None, 5, 0) == {"upper": [], "lower": []}
    assert compute.em_cone(100.0, 0.2, 0, 0) == {"upper": [], "lower": []}


class _Resp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def json(self):
        return self._data


def test_compute_expected_move_builds_payload(monkeypatch):
    candles = [{"datetime": 1_700_000_000_000 + i * compute._DAY_MS,
                "open": 100, "high": 101, "low": 99, "close": 100 + i}
               for i in range(200)]
    chain = {"callExpDateMap": {"2026-07-18:28": {"100.0": [{"volatility": 20.0}]}}}

    class _PY:
        def get_price_history_every_day(self, sym):
            return _Resp({"candles": candles})

        def get_option_chain(self, sym, **kw):
            return _Resp(chain)

    class _SC:
        def get_quote(self, sym):
            return {"last": 100.0}

    monkeypatch.setattr(compute._proxy, "schwab_py_client", _PY())
    monkeypatch.setattr(compute._proxy, "schwab_client", _SC())

    out = compute.compute_expected_move(
        "SPY", "2026-07-18",
        [{"strike": 100.0, "option_type": "put", "side": "short"}])

    assert out["error"] is None
    assert out["symbol"] == "SPY" and out["spot"] == 100.0
    assert abs(out["atm_iv"] - 0.20) < 1e-9
    assert len(out["candles"]) <= 130
    assert out["candles"][0][0] < out["candles"][-1][0]
    assert len(out["candles"][0]) == 5
    assert out["em_upper"] and out["em_lower"]
    assert out["legs"] == [{"strike": 100.0, "option_type": "put", "side": "short"}]


def test_compute_expected_move_error_on_no_history(monkeypatch):
    class _PY:
        def get_price_history_every_day(self, sym):
            return _Resp({"candles": []})

        def get_option_chain(self, sym, **kw):
            return _Resp({})

    class _SC:
        def get_quote(self, sym):
            return None

    monkeypatch.setattr(compute._proxy, "schwab_py_client", _PY())
    monkeypatch.setattr(compute._proxy, "schwab_client", _SC())
    out = compute.compute_expected_move("SPY", "2026-07-18", [])
    assert out["error"]


def test_compute_expected_move_skips_partial_candle(monkeypatch):
    good = [{"datetime": 1_700_000_000_000 + i * compute._DAY_MS,
             "open": 100, "high": 101, "low": 99, "close": 100 + i}
            for i in range(5)]
    partial = {"datetime": 1_700_000_000_000 + 99 * compute._DAY_MS,
               "close": 150}  # missing open/high/low
    raw = good + [partial]
    chain = {"callExpDateMap": {"2026-07-18:28": {"100.0": [{"volatility": 20.0}]}}}

    class _PY:
        def get_price_history_every_day(self, sym):
            return _Resp({"candles": raw})

        def get_option_chain(self, sym, **kw):
            return _Resp(chain)

    class _SC:
        def get_quote(self, sym):
            return {"last": 100.0}

    monkeypatch.setattr(compute._proxy, "schwab_py_client", _PY())
    monkeypatch.setattr(compute._proxy, "schwab_client", _SC())

    out = compute.compute_expected_move("SPY", "2026-07-18", [])
    assert out["error"] is None
    assert len(out["candles"]) == 5            # the partial bar was skipped
    assert all(len(row) == 5 for row in out["candles"])


from shared.bus import Bus
from services.options_svc import handlers


class _Cmd:
    def __init__(self, type, args):
        self.type = type
        self.args = args


def test_expected_move_command_caches_view(monkeypatch):
    monkeypatch.setattr(compute, "compute_expected_move",
                        lambda s, e, legs: {"symbol": s, "expiry": e, "legs": legs,
                                            "error": None, "candles": [[1, 1, 1, 1, 1]]})
    bus = Bus()
    handlers.handle_command(bus, _Cmd("expected_move",
                            {"symbol": "SPY", "expiry": "2026-07-18", "legs": []}))
    env = bus.cache_get(handlers.CACHE_EXPECTED_MOVE)
    assert env.payload["symbol"] == "SPY"
