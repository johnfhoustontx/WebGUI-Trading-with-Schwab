"""Traded volume: the figures, the words, when a fetch is due, and how the view
reaches the Market read's Volume row.

Design: docs/plans/2026-11-01-volume-in-read-and-reports-design.md.
"""
import copy
import datetime as dt
import inspect
import time
from zoneinfo import ZoneInfo

import pytest

from services import _degrade
from services.market_svc import compute, handlers, market_read as mr, scheduler, volume_read as vr
from shared import market_read_config
from shared.bus import Bus
from shared.contracts.market import MarketVolume

CT = ZoneInfo("America/Chicago")
FRI = dt.date(2026, 10, 30)
# Nine sessions before FRI, newest last. Monday 2026-10-19 is the first.
BEFORE = [dt.date(2026, 10, d) for d in (19, 20, 21, 22, 23, 26, 27, 28, 29)]
T, H, N, X = mr.TAILWIND, mr.HEADWIND, mr.NEUTRAL, mr.NONE


@pytest.fixture
def cfg():
    return copy.deepcopy(market_read_config.DEFAULTS)["volume"]


def _at(day, hh, mm, ss=0):
    return dt.datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=CT)


def _candle(day, hh, mm, volume, *, rising=True):
    o, c = (100.0, 100.5) if rising else (100.5, 100.0)
    return {"datetime": int(_at(day, hh, mm).timestamp() * 1000), "open": o, "close": c,
            "high": 101.0, "low": 99.0, "volume": volume}


def _session(day, volume=1000, *, upto=(15, 0), rising=True):
    """Whole five-minute bars from the open to ``upto``, each of ``volume``."""
    out, t = [], _at(day, 8, 30)
    while (t + dt.timedelta(minutes=5)) <= _at(day, *upto):
        out.append(_candle(day, t.hour, t.minute, volume, rising=rising))
        t += dt.timedelta(minutes=5)
    return out


def _history(today_volume=1000, *, today_upto=(12, 10), rising=True):
    bars = [c for d in BEFORE for c in _session(d)]
    return bars + _session(FRI, today_volume, upto=today_upto, rising=rising)


def _raw(rising=7, falling=4):
    out = {}
    for i in range(rising + falling):
        pct = 1.0 if i < rising else -1.0
        out[f"S{i}"] = {"assetMainType": "EQUITY", "quote": {
            "lastPrice": 50.0, "netChange": pct, "netPercentChange": pct, "totalVolume": 1000}}
    return out


def _with_sectors(cfg, raw):
    cfg["sector_symbols"] = list(raw)
    return cfg


# --- pace ---------------------------------------------------------------------

def test_pace_compares_today_with_the_same_time_of_day_not_with_a_whole_day(cfg):
    # The same bar size as every earlier session means usual, at noon as at the
    # close, though the part of today is a fraction of any whole day.
    got = vr.symbol_reading("SPY", _history(1000), FRI, dt.time(12, 10), cfg)
    assert got["pace"] == pytest.approx(1.0)
    assert got["word"] == vr.USUAL
    assert got["baseline_sessions"] == 9
    assert got["bars"] == 44


def test_pace_is_the_median_so_one_outsized_session_does_not_set_usual(cfg):
    bars = [c for d in BEFORE[:-1] for c in _session(d)] + _session(BEFORE[-1], 50_000)
    bars += _session(FRI, 1000, upto=(12, 10))
    assert vr.symbol_reading("SPY", bars, FRI, dt.time(12, 10), cfg)["pace"] == pytest.approx(1.0)


@pytest.mark.parametrize("volume,word", [(1200, vr.HEAVY), (1199, vr.USUAL), (801, vr.USUAL),
                                         (800, vr.LIGHT)])
def test_the_words_are_inclusive_at_their_bounds(cfg, volume, word):
    assert vr.symbol_reading("SPY", _history(volume), FRI, dt.time(12, 10), cfg)["word"] == word


def test_a_bar_still_running_is_not_counted(cfg):
    # 12:10 counts the bar that ENDED at 12:10, not the one starting then.
    bars = _session(FRI, 1000, upto=(12, 10)) + [_candle(FRI, 12, 10, 999_999)]
    bars += [c for d in BEFORE for c in _session(d)]
    got = vr.symbol_reading("SPY", bars, FRI, dt.time(12, 10), cfg)
    assert got["bars"] == 44 and got["pace"] == pytest.approx(1.0)


def test_bars_outside_the_regular_session_are_not_counted(cfg):
    extra = [_candle(FRI, 7, 0, 999_999), _candle(FRI, 15, 30, 999_999)]
    got = vr.symbol_reading("SPY", _history(1000) + extra, FRI, dt.time(12, 10), cfg)
    assert got["pace"] == pytest.approx(1.0)


def test_an_early_close_says_nothing_about_what_is_usual_later(cfg, monkeypatch):
    """A half day has no bars at 14:00, and counting its morning as that day's
    volume by 14:00 would drag usual down."""
    real = vr._mc.regular_close_on
    half = BEFORE[-1]
    monkeypatch.setattr(vr._mc, "regular_close_on", lambda d: (
        _at(d, 12, 0) if d == half else real(d)))
    got = vr.symbol_reading("SPY", _history(1000, today_upto=(14, 0)), FRI, dt.time(14, 0), cfg)
    assert got["baseline_sessions"] == 8 and got["pace"] == pytest.approx(1.0)


# --- absence is not a reading ---------------------------------------------------

@pytest.mark.parametrize("candles", [None, [], "nope", [{"datetime": "x", "volume": 1}]])
def test_no_bars_is_absent_never_zero_or_light(cfg, candles):
    got = vr.symbol_reading("SPY", candles, FRI, dt.time(12, 10), cfg)
    assert got["pace"] is None and got["word"] is None and got["up_share"] is None


def test_too_few_finished_bars_is_absent(cfg):
    got = vr.symbol_reading("SPY", _history(1000, today_upto=(8, 35)), FRI, dt.time(8, 35), cfg)
    assert got["bars"] == 1 and got["pace"] is None


def test_the_first_reading_and_the_0845_report_have_a_figure(cfg):
    """Their view is built at 08:41:30 on the two bars ended by 08:40. Requiring a
    third would leave the session's first reading blank every day."""
    assert vr.due(_at(FRI, 8, 41, 30), None, cfg)[1] == _at(FRI, 8, 40)
    got = vr.symbol_reading("SPY", _history(1000, today_upto=(8, 40)), FRI, dt.time(8, 40), cfg)
    assert got["bars"] == 2 and got["pace"] == pytest.approx(1.0)


def test_heavy_volume_with_no_clear_side_says_that_and_no_way(cfg):
    bars = [c for d in BEFORE for c in _session(d)]
    today = _session(FRI, 1400, upto=(12, 10))
    for i, c in enumerate(today):
        if i % 2:
            c["open"], c["close"] = c["close"], c["open"]
    held = {"SPY": bars + today, "QQQ": bars + today}
    view = vr.build(held, {}, _at(FRI, 12, 11, 30), cfg, ts=1, through=_at(FRI, 12, 10))
    joined = " ".join(view["statements"])
    assert "Heavy volume came without leaning clearly" in joined
    assert "way up" not in joined and "way down" not in joined


def test_too_few_earlier_sessions_leaves_pace_absent_but_keeps_the_up_share(cfg):
    bars = [c for d in BEFORE[-4:] for c in _session(d)] + _session(FRI, 1000, upto=(12, 10))
    got = vr.symbol_reading("SPY", bars, FRI, dt.time(12, 10), cfg)
    assert got["baseline_sessions"] == 4 and got["pace"] is None and got["word"] is None
    assert got["up_share"] == pytest.approx(1.0)


def test_a_session_of_zero_volume_is_absent_not_light(cfg):
    got = vr.symbol_reading("SPY", _history(0), FRI, dt.time(12, 10), cfg)
    assert got["pace"] is None and got["word"] is None and got["up_share"] is None


@pytest.mark.parametrize("bad", [None, "many", float("nan"), float("inf"), -5, True])
def test_a_bar_with_no_usable_volume_is_left_out_not_counted_as_zero(cfg, bad):
    candle = _candle(FRI, 12, 5, 1000)
    candle["volume"] = bad
    bars = [c for c in _history(1000) if c["datetime"] != candle["datetime"]] + [candle]
    got = vr.symbol_reading("SPY", bars, FRI, dt.time(12, 10), cfg)
    assert got["bars"] == 43


# --- the up share ---------------------------------------------------------------

def test_the_up_share_is_volume_in_rising_bars_over_rising_and_falling(cfg):
    bars = [c for d in BEFORE for c in _session(d)]
    bars += [_candle(FRI, 8, 30, 600), _candle(FRI, 8, 35, 200, rising=False),
             _candle(FRI, 8, 40, 200, rising=False)]
    flat = _candle(FRI, 8, 45, 5000)
    flat["close"] = flat["open"]
    bars.append(flat)
    got = vr.symbol_reading("SPY", bars, FRI, dt.time(8, 50), cfg)
    assert got["up_share"] == pytest.approx(0.6)


@pytest.mark.parametrize("share,side", [(0.55, vr.RISING), (0.5499, vr.MIXED), (0.4501, vr.MIXED),
                                        (0.45, vr.FALLING), (None, None)])
def test_the_side_is_inclusive_at_its_bounds(cfg, share, side):
    assert vr.symbol_side({"up_share": share}, cfg) == side


# --- the sector share -------------------------------------------------------------

def test_the_sector_share_weighs_dollars_not_funds(cfg):
    raw = _raw(7, 4)
    raw["S10"]["quote"]["totalVolume"] = 7000        # one falling fund as large as the seven
    got = vr.sector_split(raw, _with_sectors(cfg, raw))
    assert got == {"up_share": pytest.approx(7 / (7 + 3 + 7)), "funds": 11}


def test_a_flat_sector_counts_on_neither_side(cfg):
    raw = _raw(4, 4)
    raw["flat"] = {"quote": {"lastPrice": 50.0, "netChange": 0.0, "netPercentChange": 0.0,
                             "totalVolume": 100000}}
    got = vr.sector_split(raw, _with_sectors(cfg, raw))
    assert got["up_share"] == pytest.approx(0.5) and got["funds"] == 9


@pytest.mark.parametrize("field,bad", [("totalVolume", 0), ("totalVolume", None),
                                       ("lastPrice", None)])
def test_a_fund_with_no_volume_or_price_is_left_out(cfg, field, bad):
    raw = _raw(8, 1)
    raw["S0"]["quote"][field] = bad
    assert vr.sector_split(raw, _with_sectors(cfg, raw))["funds"] == 8


def test_too_few_sector_funds_is_absent_not_a_share(cfg):
    raw = _raw(4, 3)
    assert vr.sector_split(raw, _with_sectors(cfg, raw)) is None
    assert vr.sector_split({}, cfg) is None


# --- when a fetch is due ------------------------------------------------------------

def test_in_the_session_a_fetch_waits_for_the_bar_to_close(cfg):
    assert vr.due(_at(FRI, 12, 11, 29), None, cfg) is None
    key, through = vr.due(_at(FRI, 12, 11, 30), None, cfg)
    assert key == "2026-10-30 12:10" and through == _at(FRI, 12, 10)


def test_each_mark_is_fetched_once(cfg):
    key, _ = vr.due(_at(FRI, 12, 11, 30), None, cfg)
    assert vr.due(_at(FRI, 12, 13), key, cfg) is None
    assert vr.due(_at(FRI, 12, 16, 30), key, cfg)[0] == "2026-10-30 12:15"


def test_the_refresh_interval_is_read_from_config(cfg):
    cfg["refresh_min"] = 15
    assert vr.due(_at(FRI, 12, 11, 30), None, cfg)[0] == "2026-10-30 12:00"


@pytest.mark.parametrize("hh,mm", [(8, 30), (9, 0), (9, 30), (12, 0), (12, 30), (14, 30)])
def test_no_fetch_lands_in_the_minute_schwab_refuses(cfg, hh, mm):
    assert vr.due(_at(FRI, hh, mm, 30), None, cfg) is None


def test_the_open_itself_has_no_finished_bar(cfg):
    assert vr.due(_at(FRI, 8, 31, 30), None, cfg) is None
    assert vr.due(_at(FRI, 8, 36, 30), None, cfg)[1] == _at(FRI, 8, 35)


def test_after_the_close_one_fetch_covers_the_whole_session(cfg):
    assert vr.due(_at(FRI, 15, 0, 30), None, cfg) is None
    key, through = vr.due(_at(FRI, 15, 1, 30), None, cfg)
    assert through == _at(FRI, 15, 0)
    assert vr.due(_at(FRI, 15, 6, 30), key, cfg) is None
    assert vr.due(_at(FRI, 23, 1), key, cfg) is None


def test_before_the_open_and_on_a_closed_day_one_fetch_a_day(cfg):
    key, through = vr.due(_at(FRI, 6, 1), None, cfg)
    assert key == "2026-10-30 before_open" and through is None
    assert vr.due(_at(FRI, 7, 44), key, cfg) is None
    sat = dt.date(2026, 10, 31)
    assert vr.due(_at(sat, 9, 1), None, cfg)[0] == "2026-10-31 closed"
    assert vr.due(_at(sat, 9, 30), None, cfg) is None       # that minute again


def test_yesterdays_close_does_not_stop_todays_first_fetch(cfg):
    assert vr.due(_at(FRI, 8, 36, 30), "2026-10-29 15:00", cfg)[0] == "2026-10-30 08:35"


# --- the words ------------------------------------------------------------------------

def _view(cfg, spy=1400, qqq=1400, *, at=None, through=None, rising=True, raw=None, upto=(12, 10)):
    at = at or _at(FRI, 12, 11, 30)
    if through is None and vr.phase_at(at) == vr.SESSION:
        through = _at(FRI, 12, 10)
    bars = {"SPY": _history(spy, today_upto=upto, rising=rising),
            "QQQ": _history(qqq, today_upto=upto, rising=rising)}
    return vr.build(bars, raw or {}, at, cfg, ts=1, through=through)


def test_a_heavy_rising_session_says_so_and_states_only_what_was_measured(cfg):
    raw = _raw(7, 4)
    view = _view(_with_sectors(cfg, raw), raw=raw)
    assert view["statements"] == [
        "Through 12:10 p.m., the S&P 500 fund (SPY) traded 1.40 times its median volume "
        "by this time of day over the last 9 sessions, which is heavy.",
        "Through 12:10 p.m., 100% of the volume in the S&P 500 fund (SPY) came in "
        "five-minute periods when its price rose.",
        "Through 12:10 p.m., the Nasdaq-100 fund (QQQ) traded 1.40 times its median volume "
        "by this time of day over the last 9 sessions, which is heavy.",
        "Through 12:10 p.m., 100% of the volume in the Nasdaq-100 fund (QQQ) came in "
        "five-minute periods when its price rose.",
        "Across 11 sector funds, 64% of the dollars traded were in sectors that were rising.",
        "Heavy volume came mostly in rising five-minute periods, so the heavier trading "
        "has been on the way up.",
        "Heavy volume has gone with wider moves over the rest of the session, and it has "
        "not said which way.",
    ]
    assert view["range_hint"] == "wider"
    MarketVolume(**view)


def test_no_statement_claims_volume_predicts_the_direction(cfg):
    """Measured over 149 sessions: it did not. A statement saying a move "will
    continue" would be a claim the numbers refuse."""
    for rising in (True, False):
        for spy, qqq in ((1400, 1400), (700, 700), (1000, 1000)):
            text = " ".join(_view(cfg, spy, qqq, rising=rising)["statements"]).lower()
            for claim in ("will ", "continue", "likely", "expect", "should"):
                assert claim not in text


def test_a_heavy_falling_session_reads_the_other_way(cfg):
    view = _view(cfg, rising=False)
    assert "Heavy volume came mostly in falling five-minute periods, so the heavier " \
           "trading has been on the way down." in view["statements"]


def test_light_volume_says_narrower_and_never_a_side(cfg):
    view = _view(cfg, 700, 700)
    assert view["range_hint"] == "narrower"
    joined = " ".join(view["statements"])
    assert "Volume is light, so any move so far has light trading behind it." in joined
    assert "Light volume has gone with narrower moves" in joined
    assert "way up" not in joined and "way down" not in joined


def test_symbols_that_disagree_state_no_conclusion(cfg):
    view = _view(cfg, 1400, 700)
    assert view["range_hint"] is None
    joined = " ".join(view["statements"])
    assert "trading behind" not in joined and "moves over the rest" not in joined


def test_a_missing_symbol_is_said_to_be_unavailable_and_no_conclusion_is_drawn(cfg):
    bars = {"SPY": _history(1400), "QQQ": None}
    view = vr.build(bars, {}, _at(FRI, 12, 11, 30), cfg, ts=1, through=_at(FRI, 12, 10))
    joined = " ".join(view["statements"])
    assert ("the volume of the Nasdaq-100 fund (QQQ) is not available to compare, "
            "so it is not read.") in joined
    assert "0.00" not in joined and "light" not in joined and "trading behind" not in joined


def test_before_the_open_it_reports_the_last_session_and_says_the_morning_is_thin(cfg):
    bars = {s: [c for d in BEFORE for c in _session(d)] for s in ("SPY", "QQQ")}
    view = vr.build(bars, _raw(7, 4), _at(FRI, 7, 1), cfg, ts=1)
    assert (view["phase"], view["session_date"], view["through"]) == (
        vr.BEFORE_OPEN, "2026-10-29", "15:00")
    assert view["sectors"] is None and view["range_hint"] is None
    assert view["statements"][0] == (
        "The regular session has not opened, so there is no volume reading for today. "
        "Pre-market trading is thin and is not read as a signal.")
    assert view["statements"][1].startswith(
        "In the session of Thursday, October 29, the S&P 500 fund (SPY) traded 1.00 times "
        "its median full-session volume")
    assert not any("moves over the rest" in s for s in view["statements"])


def test_on_a_closed_day_it_reports_the_last_session(cfg):
    bars = {s: [c for d in BEFORE + [FRI] for c in _session(d)] for s in ("SPY", "QQQ")}
    view = vr.build(bars, {}, _at(dt.date(2026, 10, 31), 9, 1), cfg, ts=1)
    assert (view["phase"], view["session_date"]) == (vr.CLOSED, "2026-10-30")
    assert view["statements"][0].startswith("The market is closed today")


def test_after_the_close_it_describes_the_whole_session_in_the_past(cfg):
    view = _view(cfg, at=_at(FRI, 15, 1, 30), through=_at(FRI, 15, 0), upto=(15, 0))
    assert view["phase"] == vr.AFTER_CLOSE and view["through"] == "15:00"
    joined = " ".join(view["statements"])
    assert "Over the full regular session, the S&P 500 fund (SPY) traded 1.40 times its " \
           "median full-session volume" in joined
    assert "the heavier trading was on the way up" in joined
    assert "moves over the rest" not in joined and view["range_hint"] is None


def test_with_no_bars_at_all_before_the_open_it_says_the_figures_are_unavailable(cfg):
    view = vr.build({"SPY": None, "QQQ": None}, {}, _at(FRI, 7, 1), cfg, ts=1)
    assert view["session_date"] is None and view["symbols"] == []
    assert view["statements"][-1] == "Volume figures are not available."
    MarketVolume(**view)


@pytest.mark.parametrize("hint", [(1400, 1400, True), (1400, 1400, False), (700, 700, True),
                                  (1000, 1000, True), (1400, 700, False)])
def test_no_statement_carries_what_a_model_would_reword_or_the_guide_refuses(cfg, hint):
    """A report prints these word for word, and the plain-English rule binds them."""
    spy, qqq, rising = hint
    for s in _view(cfg, spy, qqq, rising=rising)["statements"]:
        for banned in (";", "'", '"', "×", " vs ", " avg ", "~"):
            assert banned not in s, s


# --- the Volume row -------------------------------------------------------------------

@pytest.fixture
def read_cfg():
    return copy.deepcopy(market_read_config.DEFAULTS)


@pytest.mark.parametrize("spy,qqq,rising,want", [
    (1400, 1400, True, T), (1400, 1400, False, H),
    (700, 700, True, N),                 # a rally on light volume leans neither way
    (700, 700, False, N),
    (1000, 1000, True, N), (1400, 700, True, N)])
def test_the_row_leans_only_when_heavy_volume_stands_behind_a_side(read_cfg, spy, qqq, rising, want):
    view = _view(read_cfg["volume"], spy, qqq, rising=rising)
    assert mr.volume(view, read_cfg)["verdict"] == want


def test_the_row_needs_both_symbols_to_agree_on_the_side(read_cfg):
    limits = read_cfg["volume"]
    bars = {"SPY": _history(1400, rising=True), "QQQ": _history(1400, rising=False)}
    view = vr.build(bars, {}, _at(FRI, 12, 11, 30), limits, ts=1, through=_at(FRI, 12, 10))
    assert mr.volume(view, read_cfg)["verdict"] == N


@pytest.mark.parametrize("view", [None, {}, "x", {"phase": "session", "symbols": []}])
def test_a_missing_view_is_no_reading_never_neutral(read_cfg, view):
    assert mr.volume(view, read_cfg)["verdict"] == X


def test_a_symbol_with_no_figure_costs_the_row_its_reading(read_cfg):
    limits = read_cfg["volume"]
    bars = {"SPY": _history(1400), "QQQ": None}
    view = vr.build(bars, {}, _at(FRI, 12, 11, 30), limits, ts=1, through=_at(FRI, 12, 10))
    assert mr.volume(view, read_cfg)["verdict"] == X


@pytest.mark.parametrize("phase", [vr.BEFORE_OPEN, vr.CLOSED, vr.AFTER_CLOSE])
def test_only_a_view_of_this_session_is_read(read_cfg, phase):
    view = {**_view(read_cfg["volume"]), "phase": phase}
    got = mr.volume(view, read_cfg)
    assert got["verdict"] == X and got["facts"]["symbols"] == []


def test_the_row_carries_what_the_desk_prints(read_cfg):
    raw = _raw(7, 4)
    view = _view(_with_sectors(read_cfg["volume"], raw), raw=raw)
    facts = mr.volume(view, read_cfg)["facts"]
    assert facts["pace"] == pytest.approx(1.4) and facts["through"] == "12:10"
    assert facts["sector_up_share"] == pytest.approx(7 / 11)
    assert facts["range_hint"] == "wider" and len(facts["symbols"]) == 2


def test_a_raising_row_costs_only_itself(read_cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(_degrade, "degraded", lambda area, **k: seen.append(area))
    monkeypatch.setattr(mr, "volume", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    out = mr.build({}, read_cfg, date="2026-10-30", slot="12:15", ts=1)
    assert seen == ["market.read.volume"]
    assert out["rows"][-1]["key"] == "volume" and out["rows"][-1]["verdict"] == X


# --- publishing -----------------------------------------------------------------------

@pytest.fixture
def fetches(monkeypatch):
    calls = {"bars": [], "quotes": []}

    def bars(symbol, *, period_days, frequency):
        calls["bars"].append((symbol, period_days, frequency))
        return _history(1400)

    def quotes(symbols):
        calls["quotes"].append(list(symbols))
        return {}
    monkeypatch.setattr(compute, "fetch_bars", bars)
    monkeypatch.setattr(compute, "fetch_raw_quotes", quotes)
    monkeypatch.setattr(market_read_config, "load",
                        lambda: copy.deepcopy(market_read_config.DEFAULTS))
    return calls


def _published(bus):
    env = bus.cache_get(handlers.CACHE_VOLUME)
    return env.payload if env else None


def test_a_due_refresh_publishes_the_view_and_remembers_it(fetches):
    bus, state = Bus(), {}
    assert scheduler.refresh_volume(bus, state, now=_at(FRI, 12, 11, 30)) == "2026-10-30 12:10"
    view = _published(bus)
    assert view["phase"] == vr.SESSION and view["through"] == "12:10"
    assert view["date"] == "2026-10-30" and len(view["statements"]) >= 4
    assert fetches["bars"] == [("SPY", 10, 5), ("QQQ", 10, 5)]
    assert len(fetches["quotes"]) == 1
    ver = bus.cache_version(handlers.CACHE_VOLUME)
    assert scheduler.refresh_volume(bus, state, now=_at(FRI, 12, 12)) is None
    assert bus.cache_version(handlers.CACHE_VOLUME) == ver
    assert len(fetches["bars"]) == 2


def test_before_the_open_no_quote_is_asked_for(fetches):
    assert scheduler.refresh_volume(Bus(), {}, now=_at(FRI, 7, 1)) is not None
    assert fetches["quotes"] == []


def test_when_every_fetch_fails_the_last_view_stays_and_it_retries_after_a_pause(fetches, monkeypatch):
    bus, state = Bus(), {}
    scheduler.refresh_volume(bus, state, now=_at(FRI, 12, 11, 30))
    before = _published(bus)
    tried = []
    monkeypatch.setattr(compute, "fetch_bars", lambda s, **k: tried.append(s))
    at = _at(FRI, 12, 16, 30)
    assert scheduler.refresh_volume(bus, state, now=at) is None
    assert _published(bus) == before and state["key"] == "2026-10-30 12:10"
    n = len(tried)
    assert scheduler.refresh_volume(bus, state, now=at + dt.timedelta(seconds=3)) is None
    assert len(tried) == n                      # inside the pause
    scheduler.refresh_volume(bus, state, now=at + dt.timedelta(seconds=31))
    assert len(tried) > n


def test_a_failure_counts_a_degrade_and_never_raises(fetches, monkeypatch):
    seen = []
    monkeypatch.setattr(_degrade, "degraded", lambda area, **k: seen.append(area))
    monkeypatch.setattr(vr, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert scheduler.refresh_volume(Bus(), {}, now=_at(FRI, 12, 11, 30)) is None
    assert seen == ["market.volume"]


def test_switched_off_nothing_is_fetched(fetches, monkeypatch):
    cfg = copy.deepcopy(market_read_config.DEFAULTS)
    cfg["volume"]["enabled"] = False
    monkeypatch.setattr(market_read_config, "load", lambda: cfg)
    assert scheduler.refresh_volume(Bus(), {}, now=_at(FRI, 12, 11, 30)) is None
    assert fetches["bars"] == []


def _read_row(bus, now, wall=None):
    scheduler.refresh_read(bus, {}, now=now, wall=wall)
    rows = bus.cache_get(handlers.CACHE_READ).payload["rows"]
    return next(r for r in rows if r["key"] == "volume")


def test_the_published_view_reaches_the_row_end_to_end(fetches):
    """Driven from the producer: a guard on the row alone proves nothing."""
    bus = Bus()
    scheduler.refresh_volume(bus, {}, now=_at(FRI, 12, 11, 30))
    row = _read_row(bus, _at(FRI, 12, 15, 2))
    assert row["verdict"] == T and row["facts"]["through"] == "12:10"


def test_a_view_past_its_limit_is_no_reading(fetches):
    bus = Bus()
    scheduler.refresh_volume(bus, {}, now=_at(FRI, 12, 11, 30))
    assert _read_row(bus, _at(FRI, 12, 15, 2), wall=time.time() + 601)["verdict"] == X


def test_a_view_from_another_day_is_no_reading(fetches):
    bus = Bus()
    scheduler.refresh_volume(bus, {}, now=_at(BEFORE[-1], 14, 56, 30))
    assert _read_row(bus, _at(FRI, 8, 45, 2))["verdict"] == X


def test_the_loop_fetches_volume_beside_the_poll_not_inside_it():
    """Handing it to the executor without an await is what keeps a slow bar
    series from holding up the three-second poll."""
    src = inspect.getsource(scheduler.loop)
    assert "run_in_executor(None, refresh_volume" in src
    assert "await loop_.run_in_executor(None, refresh_volume" not in src


# --- the fetch --------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


@pytest.mark.parametrize("status,body,want", [
    (200, {"candles": [{"volume": 1}]}, [{"volume": 1}]),
    (200, {"candles": []}, []),         # an answer with no bars is not a failure...
    (200, {}, None),                    # ...and one with no series at all is
    (401, {"candles": [{"volume": 1}]}, None)])
def test_fetch_bars_tells_a_failure_from_an_empty_answer(monkeypatch, status, body, want):
    monkeypatch.setattr(compute._SESSION, "get", lambda *a, **k: _Resp(status, body))
    assert compute.fetch_bars("SPY", period_days=10, frequency=5) == want


def test_fetch_bars_never_raises(monkeypatch):
    monkeypatch.setattr(compute._SESSION, "get",
                        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")))
    assert compute.fetch_bars("SPY", period_days=10, frequency=5) is None


def test_fetch_bars_asks_for_the_regular_session_only(monkeypatch):
    seen = {}

    def get(url, params, timeout):
        seen.update(params)
        return _Resp(200, {"candles": []})
    monkeypatch.setattr(compute._SESSION, "get", get)
    compute.fetch_bars("SPY", period_days=10, frequency=5)
    assert "needExtendedHoursData" not in seen      # the proxy's default is false
