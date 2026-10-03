"""Daily bars: one fetch per bar period, and today's bar from the live quote."""
import datetime as dt
import pathlib
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import market_store as ms  # noqa: E402

CT = ZoneInfo("America/Chicago")
MON = dt.date(2026, 10, 5)      # a trading day
SAT = dt.date(2026, 10, 3)
FRI = dt.date(2026, 10, 2)


class Cal:
    """The four calendar calls ``bar_epoch`` makes."""
    @staticmethod
    def is_trading_day(d):
        return d.weekday() < 5

    @staticmethod
    def regular_session_has_opened(now):
        return Cal.is_trading_day(now.date()) and now.time() >= dt.time(8, 30)

    @staticmethod
    def regular_close_on(d):
        return dt.datetime.combine(d, dt.time(15, 0), tzinfo=CT)

    @staticmethod
    def prev_trading_day(d):
        d -= dt.timedelta(days=1)
        while d.weekday() >= 5:
            d -= dt.timedelta(days=1)
        return d


def at(d, h, m):
    return dt.datetime.combine(d, dt.time(h, m), tzinfo=CT)


def test_the_three_periods_of_a_trading_day():
    assert ms.bar_epoch(at(MON, 7, 0), 10, Cal) == ("2026-10-05", "pre")
    assert ms.bar_epoch(at(MON, 8, 30), 10, Cal) == ("2026-10-05", "live")
    assert ms.bar_epoch(at(MON, 15, 9), 10, Cal) == ("2026-10-05", "live")
    assert ms.bar_epoch(at(MON, 15, 10), 10, Cal) == ("2026-10-05", "settled")
    assert ms.bar_epoch(at(MON, 23, 0), 10, Cal) == ("2026-10-05", "settled")


def test_a_weekend_stays_in_fridays_settled_period():
    assert ms.bar_epoch(at(SAT, 12, 0), 10, Cal) == ("2026-10-02", "settled")
    assert ms.bar_epoch(at(FRI, 20, 0), 10, Cal) == ("2026-10-02", "settled")


def stamp(d):
    return int(dt.datetime.combine(d, dt.time(0), tzinfo=CT).timestamp() * 1000)


def series(*days, close=100.0):
    return {"symbol": "SPY", "empty": False,
            "candles": [{"open": 99.0, "high": 101.0, "low": 98.0, "close": close,
                         "volume": 1000, "datetime": stamp(d)} for d in days]}


KEY = ms.bar_key({"symbol": "SPY", "periodType": "year", "period": 1,
                  "frequencyType": "daily", "frequency": 1})
LIVE = ("2026-10-05", "live")


def test_bar_key_separates_ranges():
    other = ms.bar_key({"symbol": "SPY", "periodType": "month", "period": "3",
                        "frequencyType": "daily", "frequency": "1"})
    assert KEY == ("SPY", "year", 1, "daily", 1) and other != KEY


def test_an_entry_is_served_inside_its_period_only():
    s = ms.BarStore()
    s.put(KEY, series(FRI, MON), now=500.0, epoch=LIVE)
    body, fetched_at = s.get(KEY, epoch=LIVE)
    assert fetched_at == 500.0 and b'"candles"' in body
    assert s.get(KEY, epoch=("2026-10-05", "settled")) is None


def test_an_empty_series_is_not_kept():
    s = ms.BarStore()
    s.put(KEY, {"candles": [], "empty": True}, now=500.0, epoch=LIVE)
    s.put(KEY, None, now=500.0, epoch=LIVE)
    assert s.get(KEY, epoch=LIVE) is None


QUOTE = {"quote": {"openPrice": 100.0, "highPrice": 103.0, "lowPrice": 99.5,
                   "lastPrice": 102.0, "totalVolume": 5000}}


def test_todays_bar_replaces_the_one_schwab_sent():
    out = ms.compose_today(series(FRI, MON), QUOTE, MON)
    assert len(out["candles"]) == 2
    assert out["candles"][-1] == {"open": 100.0, "high": 103.0, "low": 99.5,
                                  "close": 102.0, "volume": 5000,
                                  "datetime": stamp(MON)}
    assert out["candles"][0]["close"] == 100.0          # history untouched


def test_todays_bar_is_appended_when_schwab_sent_none():
    out = ms.compose_today(series(FRI), QUOTE, MON)
    assert [c["datetime"] for c in out["candles"]] == [stamp(FRI), stamp(MON)]


def test_the_input_series_is_not_mutated():
    src = series(FRI, MON)
    ms.compose_today(src, QUOTE, MON)
    assert src["candles"][-1]["close"] == 100.0


def test_an_unusable_quote_composes_nothing():
    for bad in (None, {}, {"quote": {}},
                {"quote": {**QUOTE["quote"], "lastPrice": 0}},
                {"quote": {**QUOTE["quote"], "highPrice": float("nan")}},
                {"quote": {**QUOTE["quote"], "openPrice": True}}):
        assert ms.compose_today(series(FRI, MON), bad, MON) is None


def test_a_missing_volume_is_zero_not_a_refusal():
    q = {"quote": {k: v for k, v in QUOTE["quote"].items() if k != "totalVolume"}}
    assert ms.compose_today(series(FRI), q, MON)["candles"][-1]["volume"] == 0


def test_shadow_verdicts():
    near = series(FRI, MON, close=102.1)
    near["candles"][-1].update(high=103.0, low=99.5)
    assert ms.compare_today_bar(near, QUOTE, MON) == "match"
    assert ms.compare_today_bar(series(FRI, MON, close=90.0), QUOTE, MON) == "mismatch"
    assert ms.compare_today_bar(series(FRI), QUOTE, MON) == "no_today"
    assert ms.compare_today_bar(series(FRI, MON), {}, MON) == "no_quote"


def test_infinity_is_not_a_price():
    bad = {"quote": {**QUOTE["quote"], "highPrice": float("inf")}}
    assert ms.compose_today(series(FRI, MON), bad, MON) is None


def test_a_bar_schwab_sent_without_a_real_number_is_a_mismatch():
    # NaN fails every comparison, so "not past the tolerance" would read as a match.
    for junk in (float("nan"), None, True):
        odd = series(FRI, MON, close=102.0)
        odd["candles"][-1].update(high=103.0, low=99.5, close=junk)
        assert ms.compare_today_bar(odd, QUOTE, MON) == "mismatch"


def _range_key(symbol):
    return ms.bar_key({"symbol": symbol, "periodType": "year", "period": 1,
                       "frequencyType": "daily", "frequency": 1})


def test_the_oldest_series_are_dropped_past_the_bound():
    s = ms.BarStore(max_entries=2)
    a, b, c = (_range_key(sym) for sym in ("A", "B", "C"))
    for i, k in enumerate((a, b, c)):
        s.put(k, series(FRI, MON), now=500.0 + i, epoch=LIVE)
    assert s.get(a, epoch=LIVE) is None
    assert s.get(b, epoch=LIVE)[1] == 501.0 and s.get(c, epoch=LIVE)[1] == 502.0


def test_putting_a_held_series_again_evicts_nothing_and_makes_it_the_newest():
    s = ms.BarStore(max_entries=2)
    a, b, c = (_range_key(sym) for sym in ("A", "B", "C"))
    s.put(a, series(FRI, MON), now=500.0, epoch=LIVE)
    s.put(b, series(FRI, MON), now=501.0, epoch=LIVE)
    s.put(a, series(FRI, MON), now=502.0, epoch=LIVE)
    assert s.get(a, epoch=LIVE)[1] == 502.0 and s.get(b, epoch=LIVE)[1] == 501.0
    s.put(c, series(FRI, MON), now=503.0, epoch=LIVE)   # B is now the oldest
    assert s.get(b, epoch=LIVE) is None
    assert s.get(a, epoch=LIVE)[1] == 502.0 and s.get(c, epoch=LIVE)[1] == 503.0
