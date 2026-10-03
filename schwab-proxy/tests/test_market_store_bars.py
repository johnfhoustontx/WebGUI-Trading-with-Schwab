"""Daily bars: one fetch per bar period, and today's bar from the live quote."""
import datetime as dt
import json
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
    """The four calendar calls ``bar_epoch`` makes, and ``regular_open_on``,
    which the gateway asks before it builds today's bar from a quote."""
    @staticmethod
    def is_trading_day(d):
        return d.weekday() < 5

    @staticmethod
    def regular_session_has_opened(now):
        return Cal.is_trading_day(now.date()) and now.time() >= dt.time(8, 30)

    @staticmethod
    def regular_open_on(d):
        return dt.datetime.combine(d, dt.time(8, 30), tzinfo=CT)

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
    assert fetched_at == 500.0 and json.loads(body) == series(FRI, MON)
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
    near["candles"][-1].update(open=100.0, high=103.0, low=99.5)
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
        odd["candles"][-1].update(open=100.0, high=103.0, low=99.5, close=junk)
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


def _todays(**fields):
    """Friday plus a bar for today that agrees with QUOTE except ``fields``."""
    out = series(FRI, MON, close=102.0)
    out["candles"][-1].update({"open": 100.0, "high": 103.0, "low": 99.5,
                               "volume": 5000, **fields})
    return out


def test_a_disagreement_on_the_high_or_the_low_alone_is_a_mismatch():
    assert ms.compare_today_bar(_todays(), QUOTE, MON) == "match"
    assert ms.compare_today_bar(_todays(high=110.0), QUOTE, MON) == "mismatch"
    assert ms.compare_today_bar(_todays(low=90.0), QUOTE, MON) == "mismatch"


def test_the_tolerance_is_half_a_percent_of_the_quote():
    # QUOTE's last is 102.0, so the line sits 0.51 away.
    assert ms.compare_today_bar(_todays(close=102.5), QUOTE, MON) == "match"
    assert ms.compare_today_bar(_todays(close=101.5), QUOTE, MON) == "match"
    assert ms.compare_today_bar(_todays(close=102.52), QUOTE, MON) == "mismatch"
    assert ms.compare_today_bar(_todays(close=101.48), QUOTE, MON) == "mismatch"


def test_a_candle_stamped_late_yesterday_central_is_not_todays_bar():
    # 23:00 CT on Sunday is already Monday in UTC. The candle's date is Central.
    src = series(FRI)
    late = dt.datetime.combine(MON - dt.timedelta(days=1), dt.time(23, 0), tzinfo=CT)
    src["candles"].append({**src["candles"][0], "datetime": int(late.timestamp() * 1000)})
    out = ms.compose_today(src, QUOTE, MON)
    assert len(out["candles"]) == 3 and out["candles"][1] == src["candles"][1]
    assert out["candles"][-1]["datetime"] == stamp(MON)
    assert ms.compare_today_bar(src, QUOTE, MON) == "no_today"


def test_a_volume_that_is_not_a_real_number_is_zero():
    for junk in (float("nan"), float("inf"), True, "5000"):
        q = {"quote": {**QUOTE["quote"], "totalVolume": junk}}
        assert ms.compose_today(series(FRI), q, MON)["candles"][-1]["volume"] == 0


# ---- the bound is a setting, given on each put ------------------------------

def _held(s, *symbols):
    return [sym for sym in symbols if s.get(_range_key(sym), epoch=LIVE) is not None]


def test_the_bound_can_be_changed_on_a_put():
    s = ms.BarStore(max_entries=10)
    for i, sym in enumerate(("A", "B", "C")):
        s.put(_range_key(sym), series(FRI, MON), now=500.0 + i, epoch=LIVE, max_entries=2)
    assert _held(s, "A", "B", "C") == ["B", "C"]
    s.put(_range_key("D"), series(FRI, MON), now=510.0, epoch=LIVE, max_entries=3)
    assert _held(s, "A", "B", "C", "D") == ["B", "C", "D"]      # raised: room for one more


def test_an_unusable_bound_keeps_the_one_in_force():
    s = ms.BarStore(max_entries=10)
    s.put(_range_key("A"), series(FRI, MON), now=500.0, epoch=LIVE, max_entries=2)
    for i, (sym, bad) in enumerate((("B", "many"), ("C", float("inf")),
                                    ("D", float("nan")), ("E", None))):
        s.put(_range_key(sym), series(FRI, MON), now=501.0 + i, epoch=LIVE,
              max_entries=bad)
    assert _held(s, "A", "B", "C", "D", "E") == ["D", "E"]       # still two


def test_a_series_that_is_not_kept_does_not_change_the_bound():
    s = ms.BarStore(max_entries=2)
    s.put(_range_key("A"), series(FRI, MON), now=500.0, epoch=LIVE)
    s.put(_range_key("B"), series(FRI, MON), now=501.0, epoch=LIVE)
    s.put(_range_key("C"), {"candles": []}, now=502.0, epoch=LIVE, max_entries=1)
    assert _held(s, "A", "B", "C") == ["A", "B"]


# ---- a stored body is valid JSON --------------------------------------------

def test_a_series_holding_a_number_that_is_not_json_is_not_stored():
    for bad in (float("nan"), float("inf"), float("-inf")):
        s = ms.BarStore()
        s.put(KEY, series(FRI, MON, close=bad), now=500.0, epoch=LIVE)
        assert s.get(KEY, epoch=LIVE) is None
        s.put(KEY, {**series(FRI, MON), "previousClose": bad}, now=500.0, epoch=LIVE)
        assert s.get(KEY, epoch=LIVE) is None


def test_a_series_that_cannot_be_stored_leaves_the_held_one_alone():
    s = ms.BarStore()
    s.put(KEY, series(FRI, MON), now=500.0, epoch=LIVE)
    s.put(KEY, series(FRI, MON, close=float("nan")), now=600.0, epoch=LIVE)
    body, fetched_at = s.get(KEY, epoch=LIVE)
    assert fetched_at == 500.0 and json.loads(body) == series(FRI, MON)


# ---- the verdict checks the open, and volume has a verdict of its own --------

def test_a_disagreement_on_the_open_alone_is_a_mismatch():
    assert ms.compare_today_bar(_todays(), QUOTE, MON) == "match"
    assert ms.compare_today_bar(_todays(open=99.0), QUOTE, MON) == "mismatch"
    assert ms.compare_today_bar(_todays(open=100.4), QUOTE, MON) == "match"
    for junk in (float("nan"), None, True):
        assert ms.compare_today_bar(_todays(open=junk), QUOTE, MON) == "mismatch"


def test_volume_never_decides_the_price_verdict():
    assert ms.compare_today_bar(_todays(volume=1), QUOTE, MON) == "match"
    no_volume = {"quote": {k: v for k, v in QUOTE["quote"].items() if k != "totalVolume"}}
    assert ms.compare_today_bar(_todays(), no_volume, MON) == "match"


def test_the_volume_tolerance_is_five_percent_short_and_one_percent_over():
    # The quote (5000) may be up to two minutes older than Schwab's bar.
    assert (ms.VOLUME_MAX_SHORTFALL, ms.VOLUME_MAX_EXCESS) == (0.05, 0.01)
    assert ms.compare_today_volume(_todays(volume=5000), QUOTE, MON) == "match"
    assert ms.compare_today_volume(_todays(volume=5200), QUOTE, MON) == "match"     # 3.8% short
    assert ms.compare_today_volume(_todays(volume=5300), QUOTE, MON) == "mismatch"  # 5.7% short
    assert ms.compare_today_volume(_todays(volume=4960), QUOTE, MON) == "match"     # 0.8% over
    assert ms.compare_today_volume(_todays(volume=4900), QUOTE, MON) == "mismatch"  # 2.0% over


NO_VOLUME = {"quote": {k: v for k, v in QUOTE["quote"].items() if k != "totalVolume"}}
UNUSABLE_VOLUMES = (0, None, float("nan"), float("inf"), True, "5000", -1)


def _without_volume():
    """Friday plus a bar for today that carries no volume field at all."""
    out = _todays()
    del out["candles"][-1]["volume"]
    return out


def test_no_quoted_volume_means_no_volume_verdict():
    # An index: the quote carries no volume and neither does Schwab's bar. The
    # quote-built bar would write 0, which is what Schwab's says. Nothing to judge.
    for junk in UNUSABLE_VOLUMES:
        q = {"quote": {**QUOTE["quote"], "totalVolume": junk}}
        assert ms.compare_today_volume(_todays(volume=0), q, MON) is None
        assert ms.compare_today_volume(_without_volume(), q, MON) is None
    assert ms.compare_today_volume(_todays(volume=0), NO_VOLUME, MON) is None
    assert ms.compare_today_volume(_without_volume(), NO_VOLUME, MON) is None
    for unusable in (None, {}, {"quote": None}, "x"):
        assert ms.compare_today_volume(_todays(volume=0), unusable, MON) is None


def test_a_quote_with_no_volume_against_a_bar_that_has_some_is_a_mismatch():
    # The verdict is on the volume the quote-built bar would WRITE: 0. Quote
    # mode would zero today's volume for this symbol, and shadow must show it.
    assert ms.compare_today_volume(_todays(volume=1000), NO_VOLUME, MON) == "mismatch"
    for junk in UNUSABLE_VOLUMES:
        q = {"quote": {**QUOTE["quote"], "totalVolume": junk}}
        assert ms.compare_today_volume(_todays(volume=1000), q, MON) == "mismatch"
    assert ms.compare_today_volume(_todays(volume=1), NO_VOLUME, MON) == "mismatch"


def test_the_written_volume_is_the_one_judged():
    # Whatever compose_today writes for today's volume is what the verdict is on.
    for bar_volume, quote, verdict in ((1000, NO_VOLUME, "mismatch"),
                                       (0, NO_VOLUME, None),
                                       (5000, QUOTE, "match"),
                                       (9000, QUOTE, "mismatch")):
        written = ms.compose_today(_todays(volume=bar_volume), quote, MON)
        assert written["candles"][-1]["volume"] == (5000 if quote is QUOTE else 0)
        assert ms.compare_today_volume(_todays(volume=bar_volume), quote, MON) == verdict


def test_a_bar_volume_that_is_not_a_number_and_no_quoted_volume_is_no_verdict():
    for junk in (float("nan"), None, True, "1000"):
        assert ms.compare_today_volume(_todays(volume=junk), NO_VOLUME, MON) is None


def test_no_bar_for_today_means_no_volume_verdict():
    assert ms.compare_today_volume(series(FRI), QUOTE, MON) is None
    assert ms.compare_today_volume({"candles": []}, QUOTE, MON) is None
    assert ms.compare_today_volume(None, QUOTE, MON) is None


def test_a_bar_volume_that_is_not_a_real_number_is_a_volume_mismatch():
    for junk in (float("nan"), None, True, "5000"):
        assert ms.compare_today_volume(_todays(volume=junk), QUOTE, MON) == "mismatch"
    assert ms.compare_today_volume(_todays(volume=0), QUOTE, MON) == "mismatch"


# ---- a stored series against a fresh one -------------------------------------

THU = dt.date(2026, 10, 1)


def _scaled(src, factor):
    out = json.loads(json.dumps(src))
    for candle in out["candles"]:
        for field in ("open", "high", "low", "close"):
            candle[field] *= factor
    return out


def _with(src, index, **fields):
    out = json.loads(json.dumps(src))
    out["candles"][index].update(fields)
    return out


def test_identical_series_agree():
    held = series(THU, FRI, MON)
    assert ms.series_agree(held, series(THU, FRI, MON)) is True
    assert ms.series_agree(held, series(THU, FRI, MON), moving=MON) is True


def test_a_revised_last_bar_disagrees_once_the_day_is_over():
    held = series(THU, FRI, MON)
    for field in ("open", "high", "low", "close", "volume"):
        assert ms.series_agree(held, _with(held, -1, **{field: 77})) is False


def test_todays_bar_may_move_during_the_session_and_only_then():
    held = series(THU, FRI, MON)
    moved = _with(held, -1, high=105.0, close=104.0, volume=9999)
    assert ms.series_agree(held, moved, moving=MON) is True
    assert ms.series_agree(held, moved) is False
    # Only TODAY's bar may move: a last bar from another day is compared.
    assert ms.series_agree(series(THU, FRI), _with(series(THU, FRI), -1, close=77),
                           moving=MON) is False


def test_a_revised_historical_bar_disagrees_even_during_the_session():
    held = series(THU, FRI, MON)
    assert ms.series_agree(held, _with(held, 0, close=77), moving=MON) is False
    assert ms.series_agree(held, _with(held, 1, volume=77), moving=MON) is False
    assert ms.series_agree(held, _with(held, 0, close=77)) is False


def test_a_split_adjusted_history_disagrees():
    held = series(THU, FRI, MON)
    assert ms.series_agree(held, _scaled(held, 0.5)) is False
    assert ms.series_agree(held, _scaled(held, 0.5), moving=MON) is False


def test_a_different_number_of_bars_or_different_days_disagree():
    held = series(THU, FRI, MON)
    assert ms.series_agree(held, series(FRI, MON)) is False
    assert ms.series_agree(series(THU, FRI), held, moving=MON) is False   # today's bar is new
    assert ms.series_agree(held, _with(held, 0, datetime=stamp(SAT))) is False
    assert ms.series_agree(held, _with(held, -1, datetime=stamp(SAT)), moving=MON) is False


def test_something_that_is_not_a_series_never_agrees():
    held = series(THU, FRI, MON)
    for junk in (None, [], "x", {}, {"candles": None}, {"candles": "x"}):
        assert ms.series_agree(held, junk) is False
        assert ms.series_agree(junk, held) is False
    assert ms.series_agree(held, {"candles": [1, 2, 3]}) is False
