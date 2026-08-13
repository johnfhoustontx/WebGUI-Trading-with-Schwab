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
    # Cone values are rounded to 2 decimals (price levels).
    assert upper[3][1] == round(100.0 + w3, 2)
    assert lower[3][1] == round(100.0 - w3, 2)
    assert upper[3][1] == round(upper[3][1], 2)  # no more than 2dp
    assert upper[1][0] - upper[0][0] == 86_400_000


def test_em_cone_empty_on_bad_inputs():
    assert compute.em_cone(None, 0.2, 5, 0) == {"upper": [], "lower": []}
    assert compute.em_cone(100.0, None, 5, 0) == {"upper": [], "lower": []}
    assert compute.em_cone(100.0, 0.2, 0, 0) == {"upper": [], "lower": []}


def test_em_cone_skips_weekends_when_trading_only():
    import datetime as dt
    # Friday 2026-06-19 anchor; the next two days are the weekend.
    start = int(dt.datetime(2026, 6, 19).timestamp() * 1000)
    cone = compute.em_cone(100.0, 0.20, 5, start, trading_days_only=True)
    # Days Fri(0) Sat(1) Sun(2) Mon(3) Tue(4) Wed(5) -> keep 0,3,4,5 = 4 points.
    assert len(cone["upper"]) == 4
    assert cone["upper"][0][1] == 100.0          # anchor kept at spot
    # The kept timestamps are Fri, Mon, Tue, Wed (no Sat/Sun).
    kept = [dt.datetime.fromtimestamp(ts / 1000).date() for ts, _ in cone["upper"]]
    assert dt.date(2026, 6, 20) not in kept and dt.date(2026, 6, 21) not in kept


def test_em_cone_skips_holidays_when_trading_only():
    import datetime as dt
    # Thursday 2026-07-02 anchor; 2026-07-03 is a market holiday.
    start = int(dt.datetime(2026, 7, 2).timestamp() * 1000)
    cone = compute.em_cone(100.0, 0.20, 5, start, holidays={dt.date(2026, 7, 3)},
                           trading_days_only=True)
    kept = [dt.datetime.fromtimestamp(ts / 1000).date() for ts, _ in cone["upper"]]
    assert dt.date(2026, 7, 3) not in kept       # holiday dropped
    assert dt.date(2026, 7, 4) not in kept       # Saturday dropped
    assert dt.date(2026, 7, 6) in kept           # Monday kept


def test_em_cone_default_keeps_all_calendar_days():
    # Without trading_days_only, behaviour is unchanged (all calendar days).
    cone = compute.em_cone(100.0, 0.20, 5, 0)
    assert len(cone["upper"]) == 6


def test_em_lookback_spec_auto():
    assert compute.em_lookback_spec(1)["mode"] == "intraday"
    assert compute.em_lookback_spec(2)["mode"] == "intraday"
    d = compute.em_lookback_spec(15)
    assert d["mode"] == "daily" and d["bars"] == 45      # 3×15
    assert compute.em_lookback_spec(3)["bars"] == 20     # clamp floor (3×3=9 → 20)
    assert compute.em_lookback_spec(200)["bars"] == 252  # clamp cap


def test_em_lookback_spec_override():
    assert compute.em_lookback_spec(15, "6mo")["bars"] == 130
    assert compute.em_lookback_spec(15, "1y")["bars"] == 252
    # Unknown override falls back to auto.
    assert compute.em_lookback_spec(15, "bogus") == compute.em_lookback_spec(15)


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
                        lambda s, e, legs, lookback="auto": {"symbol": s, "expiry": e,
                                            "legs": legs, "lookback": {"key": lookback},
                                            "error": None, "candles": [[1, 1, 1, 1, 1]]})
    bus = Bus()
    handlers.handle_command(bus, _Cmd("expected_move",
                            {"symbol": "SPY", "expiry": "2026-07-18", "legs": []}))
    env = bus.cache_get(handlers.CACHE_EXPECTED_MOVE)
    assert env.payload["symbol"] == "SPY"


import datetime as _dt


def _quote(**over):
    q = {"openPrice": 774.71, "highPrice": 774.9, "lowPrice": 771.28,
         "lastPrice": 772.30}
    q.update(over)
    return q


def _ms(y, m, d):
    # Pin the real convention (Schwab's daily-candle epoch is midnight CT) rather
    # than the host's timezone — building this naively only passed by accident on
    # a CT host.
    from zoneinfo import ZoneInfo
    return int(_dt.datetime(y, m, d, tzinfo=ZoneInfo("America/Chicago")).timestamp() * 1000)


def test_today_candle_builds_bar_from_quote():
    # Wed 2026-08-12 10:00 local, history ends Tue the 11th.
    now = _dt.datetime(2026, 8, 12, 10, 0)
    bar = compute.today_candle(_quote(), _ms(2026, 8, 11), now=now)
    assert bar == [_ms(2026, 8, 12), 774.71, 774.9, 771.28, 772.30]


def test_today_candle_drawn_after_the_close():
    # "From the open onward" — still drawn at 19:58 local, not just during RTH.
    now = _dt.datetime(2026, 8, 12, 19, 58)
    assert compute.today_candle(_quote(), _ms(2026, 8, 11), now=now) is not None


def test_today_candle_skipped_premarket():
    # Before 08:30 CT the quote's openPrice is still the PRIOR session's open.
    now = _dt.datetime(2026, 8, 12, 7, 15)
    assert compute.today_candle(_quote(), _ms(2026, 8, 11), now=now) is None


def test_today_candle_skipped_on_weekend_and_holiday():
    sat = _dt.datetime(2026, 8, 15, 10, 0)
    assert compute.today_candle(_quote(), _ms(2026, 8, 14), now=sat) is None
    xmas = _dt.datetime(2026, 12, 25, 10, 0)
    assert compute.today_candle(_quote(), _ms(2026, 12, 24), now=xmas,
                                holidays={_dt.date(2026, 12, 25)}) is None


def test_today_candle_no_op_when_history_already_has_today():
    now = _dt.datetime(2026, 8, 12, 10, 0)
    assert compute.today_candle(_quote(), _ms(2026, 8, 12), now=now) is None


def test_today_candle_degrades_on_missing_or_zero_fields():
    now = _dt.datetime(2026, 8, 12, 10, 0)
    last = _ms(2026, 8, 11)
    assert compute.today_candle(_quote(openPrice=None), last, now=now) is None
    assert compute.today_candle(_quote(lastPrice=0), last, now=now) is None
    assert compute.today_candle({}, last, now=now) is None
    assert compute.today_candle(None, last, now=now) is None
    # isinstance(True, int) is True in Python — a bool must not slip through
    # the numeric check as a truthy "price".
    assert compute.today_candle(_quote(openPrice=True), last, now=now) is None
