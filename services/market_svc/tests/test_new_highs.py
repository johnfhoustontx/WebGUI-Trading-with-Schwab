"""New highs / new lows on the Macro Board: the screener requests and the view.

A new high is made TODAY: the stock traded today and today's high is above the
window's highest high as of the day before. Measured against the live screener
on 2026-11-27 (see docs/plans/2026-11-27-new-highs-made-today-design.md)."""
import datetime as dt

import pytest

from services.market_svc import new_highs as N
from shared.bus.client import reset_fake_bus


# A cut-down industry -> GICS sector map (config/new_highs.toml [gics]).
_GICS = {"Engineering & Construction": "Industrials",
         "Packaged Software": "Information Technology",
         "Life/Health Insurance": "Financials",
         "Property/Casualty Insurance": "Financials",
         "Investment Managers": "Financials",
         "Wholesale Distributors": "Industrials",
         "Electric Utilities": "Utilities",
         "Oil & Gas Production": "Energy"}


def _cfg(**over):
    cfg = {"enabled": True, "url": "https://scanner.tradingview.com/america/scan",
           "exchanges": ["NYSE", "NASDAQ", "AMEX"], "min_price": 5.0,
           "rth_refresh_sec": 60, "after_close_min": 10, "timeout_sec": 10,
           "retry_sec": 120, "public_show": True, "list_max": 5000,
           "gics": dict(_GICS)}
    cfg.update(over)
    return cfg


_BASE = [
    {"left": "type", "operation": "equal", "right": "stock"},
    {"left": "is_primary", "operation": "equal", "right": True},
    {"left": "exchange", "operation": "in_range", "right": ["NYSE", "NASDAQ", "AMEX"]},
]
_PRICE = {"left": "close", "operation": "egreater", "right": 5.0}

_DAY = dt.date(2026, 11, 27)
# Midnight Central on 2026-11-27 in unix seconds. The screener's ``time`` is the
# daily bar's open (08:30 CT, 1795789800 that day), so a bar from today is at or
# after it and Wednesday's (1795617000) is not.
_MIDNIGHT = 1795759200
_TRADED_TODAY = {"left": "time", "operation": "egreater", "right": _MIDNIGHT}

# The 2026-11-27 counts under the made-today rule, straight from the screener.
_COUNTS = {"universe": 3426,
           "52w.highs": 90, "52w.lows": 10,
           "13w.highs": 168, "13w.lows": 24,
           "4w.highs": 266, "4w.lows": 49}


@pytest.fixture(autouse=True)
def _empty_bus():
    reset_fake_bus()
    yield


def test_windows_compare_with_the_day_before():
    # The plain fields (High.1M ...) include today, so a stock that only ties its
    # old high, or did not trade today, would still pass "at or above" them.
    assert N.WINDOWS == (
        ("52w", "52 wk", "price_52_week_high[1]", "price_52_week_low[1]"),
        ("13w", "13 wk", "High.3M[1]", "Low.3M[1]"),
        ("4w", "4 wk", "High.1M[1]", "Low.1M[1]"),
    )


def test_requests_for_order_and_count():
    keys = [k for k, _ in N.requests_for(_cfg(), _DAY)]
    assert keys == ["universe", "52w.highs", "52w.lows", "13w.highs", "13w.lows",
                    "4w.highs", "4w.lows"]
    assert set(keys) == set(_COUNTS)


def test_universe_body_is_the_stocks_at_or_above_the_floor():
    body = dict(N.requests_for(_cfg(), _DAY))["universe"]
    assert body["filter"] == _BASE + [_PRICE]
    assert body["range"] == [0, 1]
    assert body["columns"] == ["name"]
    assert body["markets"] == ["america"]
    assert body["symbols"] == {"query": {"types": []}}


def test_window_bodies_count_only_what_was_made_today():
    bodies = dict(N.requests_for(_cfg(), _DAY))
    assert bodies["52w.highs"]["filter"] == _BASE + [_PRICE, _TRADED_TODAY, {
        "left": "high", "operation": "greater", "right": "price_52_week_high[1]"}]
    assert bodies["13w.lows"]["filter"] == _BASE + [_PRICE, _TRADED_TODAY, {
        "left": "low", "operation": "less", "right": "Low.3M[1]"}]
    assert bodies["4w.highs"]["filter"][-1]["right"] == "High.1M[1]"


def test_a_tie_is_not_a_new_high():
    # Strictly above / below: a stock whose high only equals its old high (a
    # SPAC pinned at $10.04 every day) made no new high.
    for key, body in N.requests_for(_cfg(), _DAY):
        if key != "universe":
            assert body["filter"][-1]["operation"] in ("greater", "less")


def test_traded_today_is_the_session_date_in_central():
    body = dict(N.requests_for(_cfg(), dt.date(2026, 11, 24)))["4w.lows"]
    assert {"left": "time", "operation": "egreater",
            "right": _MIDNIGHT - 3 * 86400} in body["filter"]


def test_no_price_floor_counts_every_price():
    for _key, body in N.requests_for(_cfg(min_price=0), _DAY):
        assert not any(f["left"] == "close" for f in body["filter"])
    assert dict(N.requests_for(_cfg(min_price=0), _DAY))["universe"]["filter"] == _BASE


def test_exchanges_follow_the_config():
    body = dict(N.requests_for(_cfg(exchanges=["NYSE"]), _DAY))["universe"]
    assert body["filter"][2]["right"] == ["NYSE"]


def _no_ts(obj):
    if isinstance(obj, dict):
        return "ts" not in obj and all(_no_ts(v) for v in obj.values())
    return True


def test_build_full():
    view = N.build(dict(_COUNTS), _cfg(), "2026-11-27")
    assert view["counts"] == {"52w": {"highs": 90, "lows": 10},
                              "13w": {"highs": 168, "lows": 24},
                              "4w": {"highs": 266, "lows": 49}}
    assert "rows" not in view
    assert view["universe"] == 3426
    assert view["session"] == "2026-11-27"
    assert view["source"] == "TradingView"
    assert view["enabled"] is True
    assert view["min_price"] == 5.0
    assert view["exchanges"] == ["NYSE", "NASDAQ", "AMEX"]
    assert view["public"] is True
    # skip_unchanged view: no timestamp of its own anywhere in it.
    assert _no_ts(view)


def test_build_carries_the_public_switch():
    assert N.build(dict(_COUNTS), _cfg(public_show=False), "2026-11-27")["public"] is False


@pytest.mark.parametrize("missing", ["universe", "4w.lows", "52w.highs"])
def test_build_any_none_builds_nothing(missing):
    counts = dict(_COUNTS)
    counts[missing] = None
    assert N.build(counts, _cfg(), "2026-11-27") is None


def test_build_a_missing_key_builds_nothing():
    counts = dict(_COUNTS)
    del counts["13w.highs"]
    assert N.build(counts, _cfg(), "2026-11-27") is None


def test_publish_validates_and_caches():
    from shared.bus import Bus
    bus = Bus()
    N.publish(bus, N.build(dict(_COUNTS), _cfg(), "2026-11-27"))
    got = bus.cache_get("cache:market:new_highs").payload
    assert got["counts"]["13w"]["highs"] == 168


# ── fetching one count ───────────────────────────────────────────────────────
from zoneinfo import ZoneInfo

import requests

_CT = ZoneInfo("America/Chicago")


class _Answer:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code = status
        self._body = body
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("not JSON")
        return self._body


def _answering(answer):
    def fake(url, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer
    return fake


def test_fetch_count_ok(monkeypatch):
    seen = {}

    def fake(url, **kwargs):
        seen.update(url=url, **kwargs)
        return _Answer(body={"totalCount": 90, "data": []})
    monkeypatch.setattr(N, "_post", fake)
    body = dict(N.requests_for(_cfg(), _DAY))["52w.highs"]
    assert N.fetch_count(body, _cfg()) == 90
    assert seen["url"] == "https://scanner.tradingview.com/america/scan"
    assert seen["json"] == body
    assert seen["timeout"] == 10
    assert seen["headers"]["User-Agent"].startswith("Mozilla/5.0")


@pytest.mark.parametrize("answer", [
    _Answer(status=500, body={"totalCount": 3}),
    _Answer(bad_json=True),
    _Answer(body={"error": "x"}),
    _Answer(body={"totalCount": "121"}),
    _Answer(body={"totalCount": -1}),
    _Answer(body={"totalCount": True}),
    _Answer(body=[1, 2]),
    requests.ConnectionError("down"),
    requests.Timeout("slow"),
])
def test_fetch_count_failures(monkeypatch, answer):
    monkeypatch.setattr(N, "_post", _answering(answer))
    assert N.fetch_count({"filter": []}, _cfg()) is None



def test_fetch_count_refuses_an_error_beside_a_count(monkeypatch):
    # A 200 answer that says it failed is not a count, whatever else it carries.
    monkeypatch.setattr(N, "_post", _answering(
        _Answer(body={"error": "Unknown field", "totalCount": 5})))
    assert N.fetch_count({"filter": []}, _cfg()) is None


@pytest.mark.parametrize("answer, why", [
    (_Answer(status=503, body={"totalCount": 3}), "status 503"),
    (_Answer(bad_json=True), "not JSON"),
    (_Answer(body={"error": "Unknown field", "totalCount": 5}), "Unknown field"),
    (_Answer(body={"totalCount": "121"}), "no count"),
    (requests.ConnectionError("down"), "ConnectionError"),
])
def test_fetch_says_why_it_failed(monkeypatch, answer, why):
    monkeypatch.setattr(N, "_post", _answering(answer))
    n, reason = N.fetch({"filter": []}, _cfg())
    assert n is None
    assert why in reason


def test_fetch_ok_has_no_reason(monkeypatch):
    monkeypatch.setattr(N, "_post", _answering(_Answer(body={"totalCount": 7})))
    assert N.fetch({"filter": []}, _cfg()) == (7, "")


@pytest.mark.parametrize("answer", [
    _Answer(body={"totalCount": 7}),
    _Answer(status=500),
    requests.ConnectionError("down"),
])
def test_fetch_count_closes_its_watch(monkeypatch, answer):
    # The Watch holds duplicated socket descriptors; they are closed with the
    # request, not left for garbage collection.
    made = []

    class Recording(N._wd.Watch):
        def __init__(self):
            super().__init__()
            self.closed = False
            made.append(self)

        def close(self):
            self.closed = True
            super().close()
    monkeypatch.setattr(N._wd, "Watch", Recording)
    monkeypatch.setattr(N, "_post", _answering(answer))
    N.fetch_count({"filter": []}, _cfg())
    assert [w.closed for w in made] == [True]


def test_fetch_past_its_deadline_is_no_count(monkeypatch):
    # The deadline timer (timeout x DEADLINE_FACTOR) fires while the request is
    # still running: the answer that arrives after it is refused.
    import time

    def slow(url, **kwargs):
        time.sleep(0.3)
        return _Answer(body={"totalCount": 7})
    monkeypatch.setattr(N, "_post", slow)
    n, reason = N.fetch({"filter": []}, _cfg(timeout_sec=0.02))
    assert n is None
    assert "deadline" in reason
    assert getattr(N._wd.ACTIVE, "watch", None) is None


# ── when a refresh is due ────────────────────────────────────────────────────
def _at(y, m, d, hh, mm, ss=0):
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=_CT)


def test_due_cadence():
    cfg = _cfg()
    tue = (2026, 11, 24)
    assert N.due(_at(*tue, 8, 0), {}, cfg) is False            # before the open
    assert N.due(_at(*tue, 8, 31), {}, cfg) is True             # first at the open
    state = {"next_at": _at(*tue, 8, 31).timestamp() + 60}
    assert N.due(_at(*tue, 8, 31, 30), state, cfg) is False     # not a minute yet
    assert N.due(_at(*tue, 8, 32, 1), state, cfg) is True
    assert N.due(_at(*tue, 15, 5), {}, cfg) is False            # closed, not settled yet
    assert N.due(_at(*tue, 15, 10), {}, cfg) is True            # the settling refresh
    done = {"after_close_done": "2026-11-24"}
    assert N.due(_at(*tue, 15, 10), done, cfg) is False         # once only
    assert N.due(_at(*tue, 20, 0), done, cfg) is False
    assert N.due(_at(2026, 11, 28, 10, 0), {}, cfg) is False    # Saturday
    assert N.due(_at(2026, 11, 26, 10, 0), {}, cfg) is False    # Thanksgiving


def test_due_half_day():
    cfg = _cfg()
    assert N.due(_at(2026, 11, 27, 11, 59), {}, cfg) is True
    assert N.due(_at(2026, 11, 27, 12, 5), {}, cfg) is False    # closed at 12:00
    assert N.due(_at(2026, 11, 27, 12, 10), {}, cfg) is True    # settled at 12:10


def test_due_after_close_waits_for_a_retry():
    # A failed settling refresh is tried again after retry_sec, not every call.
    cfg = _cfg()
    state = {"next_at": _at(2026, 11, 24, 15, 10).timestamp() + 120}
    assert N.due(_at(2026, 11, 24, 15, 11), state, cfg) is False
    assert N.due(_at(2026, 11, 24, 15, 12, 1), state, cfg) is True


# ── the refresh ──────────────────────────────────────────────────────────────
_OPEN = _at(2026, 11, 24, 10, 0)


def _screener(calls, fail_on=None, day=None):
    """A fake screener answering each body with its _COUNTS value and its _ROWS
    list; the call numbered ``fail_on`` (1-based) fails."""
    day = day or _OPEN.date()   # the date refresh is called on: the bodies carry it
    by_body = {repr(b): k for k, b in N.requests_for(_cfg(), day)}

    def fake(url, **kwargs):
        calls.append(kwargs["json"])
        if fail_on is not None and len(calls) == fail_on:
            raise requests.ConnectionError("down")
        key = by_body[repr(kwargs["json"])]
        return _Answer(body={"totalCount": _COUNTS[key], "data": _ROWS.get(key, [])})
    return fake


@pytest.fixture
def degrades(monkeypatch):
    seen = []
    monkeypatch.setattr(N._degrade, "degraded", lambda area, **kw: seen.append(area))
    return seen


def _use(monkeypatch, cfg):
    monkeypatch.setattr(N.cfg_mod, "load", lambda: cfg)


def test_refresh_publishes_all_or_nothing(monkeypatch, degrades):
    from shared.bus import Bus
    bus = Bus()
    _use(monkeypatch, _cfg())
    calls = []
    monkeypatch.setattr(N, "_post", _screener(calls))
    state = {}
    assert N.refresh(bus, state, now=_OPEN) is True
    env = bus.cache_get(N.CACHE)
    assert env.payload["counts"]["4w"] == {"highs": 266, "lows": 49}
    assert env.payload["session"] == "2026-11-24"
    assert state["next_at"] == _OPEN.timestamp() + 60
    first = env.version

    calls.clear()
    later = _OPEN + dt.timedelta(minutes=2)
    monkeypatch.setattr(N, "_post", _screener(calls, fail_on=7))
    assert N.refresh(bus, state, now=later) is False
    assert bus.cache_get(N.CACHE).version == first               # the last full set stays
    assert degrades == ["market.new_highs"]
    assert state["next_at"] == later.timestamp() + 120           # retry_sec


def test_refresh_requests_are_sequential(monkeypatch, degrades):
    from shared.bus import Bus
    _use(monkeypatch, _cfg())
    calls = []
    monkeypatch.setattr(N, "_post", _screener(calls))
    N.refresh(Bus(), {}, now=_OPEN)
    assert calls == [b for _, b in N.requests_for(_cfg(), _OPEN.date())]


def test_refresh_not_due_fetches_nothing(monkeypatch, degrades):
    from shared.bus import Bus
    _use(monkeypatch, _cfg())
    calls = []
    monkeypatch.setattr(N, "_post", _screener(calls))
    assert N.refresh(Bus(), {"next_at": _OPEN.timestamp() + 30}, now=_OPEN) is False
    assert calls == []


def test_refresh_settling_marks_the_day(monkeypatch, degrades):
    from shared.bus import Bus
    _use(monkeypatch, _cfg())
    monkeypatch.setattr(N, "_post", _screener([]))
    state = {}
    assert N.refresh(Bus(), state, now=_at(2026, 11, 24, 15, 10)) is True
    assert state["after_close_done"] == "2026-11-24"


def test_refresh_disabled_then_enabled(monkeypatch, degrades):
    from shared.bus import Bus
    bus = Bus()
    calls = []
    monkeypatch.setattr(N, "_post", _screener(calls))
    _use(monkeypatch, _cfg(enabled=False))
    state = {"next_at": _OPEN.timestamp() + 30}
    assert N.refresh(bus, state, now=_OPEN) is False
    off = bus.cache_get(N.CACHE)
    assert off.payload["enabled"] is False and off.payload["counts"] == {}
    assert N.refresh(bus, state, now=_OPEN) is False
    assert bus.cache_get(N.CACHE).version == off.version         # published once
    assert calls == []
    _use(monkeypatch, _cfg())
    assert N.refresh(bus, state, now=_OPEN) is True              # at once, not after next_at
    assert bus.cache_get(N.CACHE).payload["enabled"] is True


def test_refresh_never_raises(monkeypatch, degrades):
    from shared.bus import Bus

    def boom():
        raise RuntimeError("bad file")
    monkeypatch.setattr(N.cfg_mod, "load", boom)
    assert N.refresh(Bus(), {}, now=_OPEN) is False
    assert degrades == ["market.new_highs"]


def test_the_loop_refreshes_beside_the_poll_not_inside_it():
    """Seven requests in a row can take seconds: handed to the executor
    without an await, they never hold up the three-second poll."""
    import inspect

    from services.market_svc import scheduler
    src = inspect.getsource(scheduler.loop)
    assert "run_in_executor(None, new_highs.refresh" in src
    assert "await loop_.run_in_executor(None, new_highs.refresh" not in src


# ── a switch changed while no refresh is due (review finding 3) ──────────────
_SETTLE = _at(2026, 11, 24, 15, 10)          # Tuesday's settling refresh
_SATURDAY = _at(2026, 11, 28, 10, 0)


def _settled(monkeypatch, bus, state, calls):
    _use(monkeypatch, _cfg())
    monkeypatch.setattr(N, "_post", _screener(calls))
    assert N.refresh(bus, state, now=_SETTLE) is True
    calls.clear()


def test_public_switch_reaches_the_view_with_no_refresh_due(monkeypatch, degrades):
    from shared.bus import Bus
    bus, state, calls = Bus(), {}, []
    _settled(monkeypatch, bus, state, calls)
    _use(monkeypatch, _cfg(public_show=False))
    assert N.refresh(bus, state, now=_SATURDAY) is True
    got = bus.cache_get(N.CACHE).payload
    assert got["public"] is False
    assert got["session"] == "2026-11-24"          # Tuesday's counts, not re-dated
    assert got["counts"]["52w"] == {"highs": 90, "lows": 10}
    assert calls == []                             # nothing fetched on a Saturday
    assert N.refresh(bus, state, now=_SATURDAY) is False   # once, not every call


def test_switching_back_on_restores_the_last_counts_outside_the_session(monkeypatch, degrades):
    from shared.bus import Bus
    bus, state, calls = Bus(), {}, []
    _settled(monkeypatch, bus, state, calls)
    _use(monkeypatch, _cfg(enabled=False))
    N.refresh(bus, state, now=_SATURDAY)
    assert bus.cache_get(N.CACHE).payload["enabled"] is False
    _use(monkeypatch, _cfg())
    assert N.refresh(bus, state, now=_SATURDAY) is True
    got = bus.cache_get(N.CACHE).payload
    assert got["enabled"] is True and got["session"] == "2026-11-24"
    assert calls == []


def test_after_a_restart_the_switch_patches_the_stored_view(monkeypatch, degrades):
    from shared.bus import Bus
    bus, calls = Bus(), []
    _settled(monkeypatch, bus, {}, calls)
    _use(monkeypatch, _cfg(public_show=False))
    assert N.refresh(bus, {}, now=_SATURDAY) is True          # a fresh state
    assert bus.cache_get(N.CACHE).payload["public"] is False


def test_build_carries_its_stale_age():
    # rth_refresh_sec + 2 x retry_sec: one failed refresh and its retry pass
    # before the board greys the counts by age.
    assert N.build(dict(_COUNTS), _cfg(), "2026-11-27")["stale_after_sec"] == 300


def test_a_failed_refresh_says_which_request_and_why(monkeypatch):
    from shared.bus import Bus
    seen = []
    monkeypatch.setattr(N._degrade, "degraded",
                        lambda area, **kw: seen.append((area, kw.get("detail"))))
    _use(monkeypatch, _cfg())
    monkeypatch.setattr(N, "_post", _screener([], fail_on=7))
    assert N.refresh(Bus(), {}, now=_OPEN) is False
    [(area, detail)] = seen
    assert area == "market.new_highs"
    assert "4w.lows" in detail and "ConnectionError" in detail


def test_a_stored_view_from_the_old_definition_is_not_put_back(monkeypatch, degrades):
    """Before 2026-11-27's change the view carried ``rows`` (all stocks and $5
    and up, "at" a high). After a restart outside the session it must not be
    republished as today's shape: the contract would refuse it, and its counts
    mean something else. The board shows nothing until the next refresh."""
    from shared.bus import Bus
    bus = Bus()
    old = {"enabled": True, "source": "TradingView", "universe": 4984,
           "session": "2026-11-24", "public": True,
           "rows": {"all": {"52w": {"highs": 121, "lows": 85}}}}
    bus.cache_set(N.CACHE, old)
    before = bus.cache_get(N.CACHE).version
    _use(monkeypatch, _cfg(public_show=False))
    assert N.refresh(bus, {}, now=_SATURDAY) is False
    assert bus.cache_get(N.CACHE).version == before
    assert degrades == []


# ── the lists behind each count: sectors, then stocks (2026-11-27) ───────────
def _row(sym, industry, close, change, high=None, low=None, exchange="NYSE"):
    return {"s": f"{exchange}:{sym}",
            "d": [sym, f"{sym} Inc.", industry, close, change,
                  close if high is None else high, close if low is None else low]}


# What the screener answered for two of the lists on 2026-11-27 (cut down).
_ROWS = {
    "4w.highs": [_row("ACM", "Engineering & Construction", 84.19, 0.91, high=84.3085),
                 _row("ADP", "Packaged Software", 319.43, 0.86, high=319.92),
                 _row("AFL", "Life/Health Insurance", 110.2, 1.4, high=110.5),
                 _row("ALL", "Property/Casualty Insurance", 205.0, 0.2, high=205.6),
                 _row("BK", "Investment Managers", 90.1, 2.3, high=90.4)],
    "52w.lows": [_row("AMSF", "Property/Casualty Insurance", 18.23, -0.22, low=17.95),
                 _row("CHSCP", "Wholesale Distributors", 24.85, -0.72, low=24.85)],
}


def test_window_bodies_ask_for_the_list():
    bodies = dict(N.requests_for(_cfg(), _DAY))
    body = bodies["4w.highs"]
    # TradingView's own "sector" is its 21 groups, not GICS: the service asks
    # for the industry and maps it to one of the 11 GICS sectors itself.
    assert body["columns"] == ["name", "description", "industry", "close", "change",
                               "high", "low"]
    assert body["range"] == [0, 5000]
    assert body["sort"] == {"sortBy": "name", "sortOrder": "asc"}
    # The universe is still a count alone.
    assert bodies["universe"]["columns"] == ["name"]
    assert bodies["universe"]["range"] == [0, 1]
    assert "sort" not in bodies["universe"]


def test_no_lists_asks_for_counts_alone():
    for _key, body in N.requests_for(_cfg(list_max=0), _DAY):
        assert body["columns"] == ["name"] and body["range"] == [0, 1]
        assert "sort" not in body


def test_parse_rows():
    rows = N.parse_rows([
        _row("ACM", "Engineering & Construction", 84.19, 0.91, high=84.3085),
        {"s": "NYSE:XX", "d": ["XX", "X Co", None, float("nan"), None, None, None]},
        _row("NEW", "Space Tourism", 12.0, 1.0),     # an industry the map lacks
        {"s": "NYSE:BAD"},                       # no values: skipped
        {"s": "NYSE:SHORT", "d": ["SHORT"]},     # too few values: skipped
        "not a row",
        {"s": "NYSE:NONAME", "d": [None, "n", "Packaged Software", 1, 1, 1, 1]},
    ], _GICS)
    assert rows == [
        {"symbol": "ACM", "name": "ACM Inc.", "sector": "Industrials",
         "industry": "Engineering & Construction",
         "price": 84.19, "change_pct": 0.91, "high": 84.3085, "low": 84.19},
        {"symbol": "XX", "name": "X Co", "sector": "Unclassified", "industry": "",
         "price": None, "change_pct": None, "high": None, "low": None},
        {"symbol": "NEW", "name": "NEW Inc.", "sector": "Unclassified",
         "industry": "Space Tourism",
         "price": 12.0, "change_pct": 1.0, "high": 12.0, "low": 12.0},
    ]


def test_parse_rows_with_no_map_classifies_nothing():
    rows = N.parse_rows([_row("ACM", "Engineering & Construction", 1, 1)], {})
    assert rows[0]["sector"] == "Unclassified"
    assert rows[0]["industry"] == "Engineering & Construction"


def test_build_detail_groups_by_sector_most_first():
    lists = {k: N.parse_rows(v, _GICS) for k, v in _ROWS.items()}
    detail = N.build_detail(lists, dict(_COUNTS), _cfg(), "2026-11-27")
    assert detail["enabled"] is True and detail["session"] == "2026-11-27"
    highs = detail["lists"]["4w"]["highs"]
    assert highs["total"] == 266 and highs["listed"] == 5
    # Three insurers and a fund manager are three industries but one sector.
    assert [(g["sector"], g["count"]) for g in highs["sectors"]] == [
        ("Financials", 3), ("Industrials", 1), ("Information Technology", 1)]
    # Highs: the biggest gain first, with the day's high as the level reached.
    assert [r["symbol"] for r in highs["sectors"][0]["stocks"]] == ["BK", "AFL", "ALL"]
    assert highs["sectors"][0]["stocks"][0] == {
        "symbol": "BK", "name": "BK Inc.", "industry": "Investment Managers",
        "price": 90.1, "change_pct": 2.3, "level": 90.4}
    # Lows: the biggest fall first, with the day's low.
    lows = detail["lists"]["52w"]["lows"]
    assert [g["sector"] for g in lows["sectors"]] == ["Financials", "Industrials"]
    assert lows["sectors"][0]["stocks"][0]["level"] == 17.95
    assert lows["sectors"][1]["stocks"][0]["level"] == 24.85
    # Every window and side is present, empty when nothing was listed.
    assert detail["lists"]["13w"]["highs"] == {"total": 168, "listed": 0, "sectors": []}


def test_build_detail_lows_fall_first_within_a_sector():
    lists = {"4w.lows": N.parse_rows([_row("A", "Investment Managers", 10, -0.5, low=9.9),
                                      _row("B", "Investment Managers", 10, -3.0, low=9.0),
                                      _row("C", "Investment Managers", 10, None, low=9.5)],
                                     _GICS)}
    detail = N.build_detail(lists, dict(_COUNTS), _cfg(), "2026-11-27")
    stocks = detail["lists"]["4w"]["lows"]["sectors"][0]["stocks"]
    assert [r["symbol"] for r in stocks] == ["B", "A", "C"]   # no change last
    assert stocks[0]["level"] == 9.0


def test_build_detail_ties_on_count_go_by_name():
    lists = {"4w.highs": N.parse_rows([_row("A", "Electric Utilities", 1, 1),
                                       _row("B", "Oil & Gas Production", 1, 1)], _GICS)}
    detail = N.build_detail(lists, dict(_COUNTS), _cfg(), "2026-11-27")
    assert [g["sector"] for g in detail["lists"]["4w"]["highs"]["sectors"]] == [
        "Energy", "Utilities"]


def test_detail_publish_validates_and_caches():
    from shared.bus import Bus
    bus = Bus()
    lists = {k: N.parse_rows(v, _GICS) for k, v in _ROWS.items()}
    N.publish_detail(bus, N.build_detail(lists, dict(_COUNTS), _cfg(), "2026-11-27"))
    got = bus.cache_get(N.DETAIL).payload
    assert got["lists"]["4w"]["highs"]["sectors"][0]["sector"] == "Financials"


def test_fetch_rows(monkeypatch):
    monkeypatch.setattr(N, "_post", _answering(
        _Answer(body={"totalCount": 266, "data": _ROWS["4w.highs"]})))
    n, rows, why = N.fetch_rows({"filter": []}, _cfg())
    assert n == 266 and why == "" and rows == _ROWS["4w.highs"]


@pytest.mark.parametrize("body", [
    {"totalCount": 3, "data": "x"},
    {"totalCount": 3},
])
def test_fetch_rows_needs_a_list(monkeypatch, body):
    monkeypatch.setattr(N, "_post", _answering(_Answer(body=body)))
    n, rows, why = N.fetch_rows({"filter": []}, _cfg())
    assert n is None and rows is None and why == "no rows"


def test_fetch_rows_a_count_of_zero_needs_no_list(monkeypatch):
    monkeypatch.setattr(N, "_post", _answering(_Answer(body={"totalCount": 0})))
    assert N.fetch_rows({"filter": []}, _cfg()) == (0, [], "")


def test_refresh_publishes_the_lists_before_the_counts(monkeypatch, degrades):
    from shared.bus import Bus
    bus = Bus()
    _use(monkeypatch, _cfg())
    order = []
    real_main, real_detail = N.publish, N.publish_detail
    monkeypatch.setattr(N, "publish", lambda b, p: order.append("counts") or real_main(b, p))
    monkeypatch.setattr(N, "publish_detail",
                        lambda b, p: order.append("lists") or real_detail(b, p))
    monkeypatch.setattr(N, "_post", _screener([]))
    assert N.refresh(bus, {}, now=_OPEN) is True
    assert order == ["lists", "counts"]
    detail = bus.cache_get(N.DETAIL).payload
    assert detail["session"] == "2026-11-24"
    assert detail["lists"]["4w"]["highs"]["listed"] == 5
    # The refresh maps through the configured industry map.
    assert detail["lists"]["4w"]["highs"]["sectors"][0]["sector"] == "Financials"


def test_a_failed_refresh_publishes_no_lists(monkeypatch, degrades):
    from shared.bus import Bus
    bus = Bus()
    _use(monkeypatch, _cfg())
    monkeypatch.setattr(N, "_post", _screener([], fail_on=4))
    assert N.refresh(bus, {}, now=_OPEN) is False
    assert bus.cache_get(N.DETAIL) is None
    assert bus.cache_get(N.CACHE) is None


def test_switched_off_the_lists_go_too(monkeypatch, degrades):
    from shared.bus import Bus
    bus = Bus()
    _use(monkeypatch, _cfg(enabled=False))
    N.refresh(bus, {}, now=_OPEN)
    assert bus.cache_get(N.DETAIL).payload["enabled"] is False


def test_no_lists_publishes_the_lists_off(monkeypatch, degrades):
    from shared.bus import Bus
    bus = Bus()
    _use(monkeypatch, _cfg(list_max=0))

    def fake(url, **kwargs):
        return _Answer(body={"totalCount": 7})
    monkeypatch.setattr(N, "_post", fake)
    assert N.refresh(bus, {}, now=_OPEN) is True
    assert bus.cache_get(N.DETAIL).payload["enabled"] is False
    assert bus.cache_get(N.CACHE).payload["counts"]["4w"]["highs"] == 7
