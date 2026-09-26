"""The economic-calendar cycle (``services/news_svc/econ_calendar.py``): sources ->
news.db -> the three calendar views. A fake fetcher, no network, tmp_path stores
only (the dividends store included)."""
import copy
import datetime as dt
import json
import logging
import pathlib
import urllib.parse

import pytest

from services import _degrade
from services.news_svc import compute, econ_calendar, handlers, store
from shared import dividends as divs
from shared import news_config as nc
from shared import symbols
from shared.bus import Bus

FIX = pathlib.Path(__file__).parent / "fixtures"
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 26, 15, 0, tzinfo=UTC)        # Saturday, 10:00 CT
LATER = NOW + dt.timedelta(hours=13)
NOW_PLUS_2H = NOW + dt.timedelta(hours=2)
FEED_UA = nc.DEFAULTS["collector"]["feed_user_agent"]
KEY = "K123abcdef"


def _csv(series, extra=()):
    rows = ["2025-12-01,100.0", "2026-01-01,100.5", "2026-06-01,101.0",
            "2026-07-01,101.4", "2026-08-01,101.9", *extra]
    return ("observation_date," + series + "\n" + "\n".join(rows) + "\n").encode()


class FakeFetch:
    """Routes by host + path; records ``(url, user_agent, headers)`` per call."""

    def __init__(self):
        self.calls = []
        self.failing = {}
        self.extra_rows = ()          # observations that "landed" after the first fill

    def fail(self, host, exc):
        self.failing[host] = exc

    def urls(self, needle=""):
        return [u for u, *_ in self.calls if needle in u]

    def __call__(self, url, *, user_agent=None, timeout=20, headers=None, etag=None,
                 last_modified=None):
        self.calls.append((url, user_agent, headers))
        parts = urllib.parse.urlsplit(url)
        host, q = parts.netloc, urllib.parse.parse_qs(parts.query)
        if host in self.failing:
            raise self.failing[host]
        if host == "www.federalreserve.gov":
            body = (FIX / "fed_calendar.json").read_bytes()
        elif host == "www.bls.gov":
            body = (FIX / "bls_sample.ics").read_bytes()
        elif host == "www.bea.gov":
            body = (FIX / "bea_sample.ics").read_bytes()
        elif host == "fred.stlouisfed.org" and parts.path.endswith("/releases/calendar"):
            body = (FIX / "fred_calendar_rid9.html").read_bytes()
        elif host == "fred.stlouisfed.org" and parts.path.endswith("fredgraph.csv"):
            body = _csv(q["id"][0], self.extra_rows)
        elif host == "api.stlouisfed.org" and parts.path.endswith("/series/observations"):
            body = (FIX / "fred_obs_cpi.json").read_bytes()
        elif host == "api.stlouisfed.org" and parts.path.endswith("/release/dates"):
            body = (FIX / "fred_release_dates.json").read_bytes()
        elif host == "api.nasdaq.com":
            name = ("nasdaq_ipo_2026_09.json" if q["date"][0] == "2026-09"
                    else "nasdaq_ipo_empty.json")
            body = (FIX / name).read_bytes()
        else:
            raise compute.FetchError(f"HTTP 404 {url}", status=404)
        return compute.Fetched(status=200, body=body, etag=None, last_modified=None)


@pytest.fixture
def cal_config(monkeypatch, tmp_path):
    cfg = copy.deepcopy(nc.DEFAULTS)
    # Hermetic: the default dividends store is an absent tmp path, never the live one.
    monkeypatch.setattr(divs, "DEFAULT_DB_PATH", tmp_path / "no-store" / "dividends.db")
    cfg["tickers"] = {"extras": ["EXTRA"]}
    monkeypatch.setattr(nc, "load", lambda: cfg)
    monkeypatch.setattr(symbols, "collection_base", lambda: ["$SPX", "JPM"])
    _degrade.reset()
    yield cfg
    _degrade.reset()


@pytest.fixture
def db(tmp_path):
    s = store.Store(tmp_path / "news.db")
    yield s
    s.close()


@pytest.fixture
def bus():
    from shared.bus.client import reset_fake_bus
    reset_fake_bus()
    return Bus(fake=True)


def _dividends_store(tmp_path, rows):
    path = tmp_path / "dividends.db"
    conn = divs.init_db(path)
    divs.upsert(conn, [{"symbol": s, "ex_date": d, "pay_date": None, "amount": 0.5}
                       for s, d in rows])
    divs.close_db(conn)
    return path


def _payload(bus, key):
    env = bus.cache_get(key)
    return env.payload if env is not None else None


# ── the plan's tests ──────────────────────────────────────────────────────────

def test_each_source_gets_its_own_user_agent(db, bus, cal_config):
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={})
    ua = {u.split("/")[2]: a for u, a, _ in fetch.calls}
    assert ua["api.nasdaq.com"].startswith("Mozilla/5.0 (X11") and "Chrome/" in ua["api.nasdaq.com"]
    assert ua["www.bls.gov"] == FEED_UA and ua["fred.stlouisfed.org"] == FEED_UA
    assert ua["www.federalreserve.gov"] == FEED_UA and ua["www.bea.gov"] == FEED_UA
    nasdaq_headers = [h for u, _, h in fetch.calls if "nasdaq" in u]
    assert nasdaq_headers and all(h.get("Accept", "").startswith("application/json")
                                  for h in nasdaq_headers)


def test_nasdaq_is_fetched_for_this_month_and_next(db, bus, cal_config):
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={})
    months = sorted(urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["date"][0]
                    for u in fetch.urls("api.nasdaq.com"))
    assert months == ["2026-09", "2026-10"]


def test_without_a_key_observations_come_from_fredgraph(db, bus, cal_config):
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={})
    assert not any("api.stlouisfed.org" in u for u, *_ in fetch.calls)
    series = {urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["id"][0]
              for u in fetch.urls("fredgraph.csv")}
    assert series == {ind["series"] for ind in nc.indicators()}
    p = _payload(bus, handlers.CACHE_CAL)
    assert p["sources"]["fredgraph"] == "ok" and p["sources"]["fred_api"] == "off"


def test_with_a_key_observations_come_from_the_api(db, bus, cal_config):
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={"FRED_API_KEY": KEY})
    assert any("api.stlouisfed.org" in u for u, *_ in fetch.calls)
    assert not fetch.urls("fredgraph.csv")
    p = _payload(bus, handlers.CACHE_CAL)
    assert p["sources"]["fred_api"] == "ok" and p["sources"]["fredgraph"] == "off"


def test_the_key_never_leaves_the_fetch(db, bus, cal_config, caplog):
    caplog.set_level(logging.DEBUG)
    fetch = FakeFetch()
    fetch.fail("api.stlouisfed.org", compute.FetchError(
        f"HTTP 400 https://api.stlouisfed.org/fred/series/observations?series_id=X&api_key={KEY}",
        status=400))
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={"FRED_API_KEY": KEY})
    assert _degrade.counts().get("news.cal.fred_api", 0) >= 1
    assert KEY not in caplog.text
    assert KEY not in json.dumps(db.cal_source("fred_api"))
    for key in (handlers.CACHE_CAL, handlers.CACHE_CAL_PUBLIC, handlers.CACHE_CAL_STATUS):
        assert KEY not in json.dumps(_payload(bus, key))
    assert "***" in db.cal_source("fred_api")["error"]


def test_the_key_never_leaves_a_failing_calendar_fallback(db, bus, cal_config, caplog):
    """The calendar HTML fails, the key path's release-dates fallback fails too."""
    caplog.set_level(logging.DEBUG)
    fetch = FakeFetch()
    fetch.fail("fred.stlouisfed.org", compute.FetchError("HTTP 403", status=403))
    fetch.fail("api.stlouisfed.org", RuntimeError(f"boom api_key={KEY}&x=1 {KEY}"))
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={"FRED_API_KEY": KEY})
    assert fetch.urls("/release/dates")          # the fallback was tried
    assert KEY not in caplog.text
    for name in ("fred_api", "fred_api_dates", "fred_calendar"):
        assert KEY not in json.dumps(db.cal_source(name))
    assert KEY not in json.dumps(_payload(bus, handlers.CACHE_CAL_STATUS))


def test_the_key_path_falls_back_to_api_release_dates_when_the_html_fails(db, bus, cal_config):
    fetch = FakeFetch()
    fetch.fail("fred.stlouisfed.org", compute.FetchError("HTTP 403", status=403))
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={"FRED_API_KEY": KEY})
    assert db.cal_source("fred_api_dates")["payload"]
    p = _payload(bus, handlers.CACHE_CAL)
    assert p["sources"]["fred_calendar"] == "never"
    assert "fred_api_dates" not in p["sources"]


def test_the_fred_calendar_fetch_reaches_one_release_period_back(db, bus, cal_config):
    """Without the last release in the page, a monthly FRED indicator never
    reads "released" and the watch never fires for it."""
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={})
    urls = fetch.urls("/releases/calendar")
    rids = sorted(int(urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["rid"][0]) for u in urls)
    assert rids == [9, 180]
    for u in urls:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)
        start = dt.date.fromisoformat(q["vs"][0])
        assert (NOW.date() - start).days >= 35
        assert dt.date.fromisoformat(q["ve"][0]) > NOW.date()


def test_one_failing_source_keeps_its_last_good_and_flips_only_its_state(db, bus, cal_config):
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={})              # all ok
    p = _payload(bus, handlers.CACHE_CAL)
    assert p["sources"] == {"fed": "ok", "bls": "ok", "bea": "ok", "dividends": "never",
                            "nasdaq_ipo": "ok", "fred_calendar": "ok", "fred_api": "off",
                            "fredgraph": "ok"}
    fetch.fail("www.bls.gov", compute.FetchError("HTTP 403", status=403))
    econ_calendar.refresh(bus, db, fetch, now=LATER, env={})
    p = _payload(bus, handlers.CACHE_CAL)
    assert p["sources"]["bls"] == "stale" and p["sources"]["fed"] == "ok"
    assert p["sources"]["bea"] == "ok"
    assert any(d["key"] == "cpi" and d["next_release_at"] for d in p["data"])   # last good kept
    assert _degrade.counts()["news.cal.bls"] >= 1
    status = {r["name"]: r for r in _payload(bus, handlers.CACHE_CAL_STATUS)["sources"]}
    assert "403" in status["bls"]["error"] and status["fed"]["error"] is None


def test_a_parse_failure_is_a_failed_source_not_an_empty_one(db, bus, cal_config, monkeypatch):
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={})
    orig = FakeFetch.__call__

    def junk(self, url, **kw):
        got = orig(self, url, **kw)
        if "federalreserve" in url:
            return compute.Fetched(status=200, body=b"<html>blocked</html>", etag=None,
                                   last_modified=None)
        return got
    monkeypatch.setattr(FakeFetch, "__call__", junk)
    before = _payload(bus, handlers.CACHE_CAL)["events"]
    econ_calendar.refresh(bus, db, fetch, now=NOW + dt.timedelta(minutes=61), env={})
    p = _payload(bus, handlers.CACHE_CAL)
    assert p["sources"]["fed"] == "stale"
    assert p["events"] and p["events"] == before


def test_extras_dividends_never_reach_the_public_view(db, bus, cal_config, tmp_path):
    path = _dividends_store(tmp_path, [("JPM", "2026-10-06"), ("EXTRA", "2026-10-07")])
    econ_calendar.refresh(bus, db, FakeFetch(), now=NOW, env={}, dividends_db=path)
    assert {d["symbol"] for d in _payload(bus, handlers.CACHE_CAL)["dividends"]} == {"JPM", "EXTRA"}
    assert {d["symbol"] for d in _payload(bus, handlers.CACHE_CAL_PUBLIC)["dividends"]} == {"JPM"}
    assert "EXTRA" not in json.dumps(_payload(bus, handlers.CACHE_CAL_PUBLIC))
    pub, priv = _payload(bus, handlers.CACHE_CAL_PUBLIC), _payload(bus, handlers.CACHE_CAL)
    assert {k: v for k, v in pub.items() if k != "dividends"} == \
        {k: v for k, v in priv.items() if k != "dividends"}


def test_dividends_outside_the_window_are_left_out(db, bus, cal_config, tmp_path):
    path = _dividends_store(tmp_path, [("JPM", "2026-10-06"), ("JPM", "2027-03-01"),
                                       ("JPM", "2026-08-01")])
    econ_calendar.refresh(bus, db, FakeFetch(), now=NOW, env={}, dividends_db=path)
    assert [d["ex_date"] for d in _payload(bus, handlers.CACHE_CAL)["dividends"]] == ["2026-10-06"]


def test_a_missing_dividends_store_is_never_not_empty(db, bus, cal_config, tmp_path):
    path = tmp_path / "absent" / "dividends.db"
    econ_calendar.refresh(bus, db, FakeFetch(), now=NOW, env={}, dividends_db=path)
    p = _payload(bus, handlers.CACHE_CAL)
    assert p["sources"]["dividends"] == "never" and p["dividends"] == []
    assert not path.exists() and not path.parent.exists()      # never created by us


def test_the_dividends_store_is_opened_read_only(db, bus, cal_config, tmp_path, monkeypatch):
    path = _dividends_store(tmp_path, [("JPM", "2026-10-06")])
    import sqlite3
    seen = []
    real = sqlite3.connect

    def spy(database, *a, **kw):
        seen.append((str(database), kw.get("uri")))
        return real(database, *a, **kw)
    monkeypatch.setattr(econ_calendar.sqlite3, "connect", spy)
    econ_calendar.refresh(bus, db, FakeFetch(), now=NOW, env={}, dividends_db=path)
    assert seen == [(f"file:{path}?mode=ro", True)]
    assert _payload(bus, handlers.CACHE_CAL)["sources"]["dividends"] == "ok"


def test_an_unchanged_calendar_does_not_bump_the_version(db, bus, cal_config):
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={})
    v = bus.cache_version(handlers.CACHE_CAL)
    vp = bus.cache_version(handlers.CACHE_CAL_PUBLIC)
    n = len(fetch.calls)
    econ_calendar.refresh(bus, db, fetch, now=NOW_PLUS_2H, env={})
    assert len(fetch.calls) > n                     # the Fed WAS refetched
    assert bus.cache_version(handlers.CACHE_CAL) == v
    assert bus.cache_version(handlers.CACHE_CAL_PUBLIC) == vp


def test_the_calendar_views_carry_no_timestamp_and_the_status_does(db, bus, cal_config):
    econ_calendar.refresh(bus, db, FakeFetch(), now=NOW, env={})
    for key in (handlers.CACHE_CAL, handlers.CACHE_CAL_PUBLIC):
        p = _payload(bus, key)
        assert set(p) == {"events", "dividends", "ipos", "data", "sources", "settings"}
    assert _payload(bus, handlers.CACHE_CAL_STATUS)["ts"] == NOW.isoformat()


def test_a_source_is_refetched_only_after_its_refresh_min(db, bus, cal_config):
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={})
    first = len(fetch.calls)
    econ_calendar.refresh(bus, db, fetch, now=NOW + dt.timedelta(minutes=30), env={})
    assert len(fetch.calls) == first                               # nothing due
    econ_calendar.refresh(bus, db, fetch, now=NOW + dt.timedelta(minutes=61), env={})
    new = [u for u, *_ in fetch.calls[first:]]
    assert len(new) == 1 and "federalreserve" in new[0]            # fed only (60 min)


def test_a_failed_source_retries_after_the_calendar_refresh_min(db, bus, cal_config):
    fetch = FakeFetch()
    fetch.fail("www.bls.gov", compute.FetchError("HTTP 503", status=503))
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={})
    del fetch.failing["www.bls.gov"]
    econ_calendar.refresh(bus, db, fetch, now=NOW + dt.timedelta(minutes=61), env={})
    assert len(fetch.urls("bls.gov")) == 2                       # not 12 h later
    assert _payload(bus, handlers.CACHE_CAL)["sources"]["bls"] == "ok"


def test_watch_polls_only_due_series(db, bus, cal_config):
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={})
    n = len(fetch.calls)
    cpi_release = dt.datetime(2026, 10, 14, 12, 30, tzinfo=UTC)   # 08:30 ET
    res = econ_calendar.watch(bus, db, fetch, now=cpi_release + dt.timedelta(minutes=3), env={})
    polled = {urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["id"][0]
              for u, *_ in fetch.calls[n:]}
    assert polled == {"CPIAUCSL", "CPILFESL"} and sorted(res["due"]) == sorted(polled)
    # within release_poll_min: nothing again
    m = len(fetch.calls)
    econ_calendar.watch(bus, db, fetch, now=cpi_release + dt.timedelta(minutes=4), env={})
    assert len(fetch.calls) == m


def test_watch_stops_once_a_real_observation_lands(db, bus, cal_config):
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={})
    cpi_release = dt.datetime(2026, 10, 14, 12, 30, tzinfo=UTC)
    fetch.extra_rows = ("2026-09-01,102.3",)
    econ_calendar.watch(bus, db, fetch, now=cpi_release + dt.timedelta(minutes=3), env={})
    cpi = next(d for d in _payload(bus, handlers.CACHE_CAL)["data"] if d["key"] == "cpi")
    assert cpi["latest"]["obs_date"] == "2026-09-01" and cpi["latest"]["bootstrap"] is False
    n = len(fetch.calls)
    econ_calendar.watch(bus, db, fetch, now=cpi_release + dt.timedelta(minutes=10), env={})
    assert len(fetch.calls) == n                     # landed: the watch is over


def test_watch_does_nothing_when_no_series_is_due(db, bus, cal_config):
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={})
    n, v = len(fetch.calls), bus.cache_version(handlers.CACHE_CAL_STATUS)
    assert econ_calendar.watch(bus, db, fetch, now=NOW + dt.timedelta(minutes=5), env={}) == {"due": []}
    assert len(fetch.calls) == n and bus.cache_version(handlers.CACHE_CAL_STATUS) == v


def test_disabled_calendar_fetches_nothing(db, bus, cal_config):
    cal_config["calendar"]["enabled"] = False
    fetch = FakeFetch()
    assert econ_calendar.refresh(bus, db, fetch, now=NOW, env={}) == {"skipped": "disabled"}
    assert econ_calendar.watch(bus, db, fetch, now=NOW, env={}) == {"skipped": "disabled"}
    assert fetch.calls == []
    p = _payload(bus, handlers.CACHE_CAL)
    assert set(p["sources"].values()) == {"off"} and p["events"] == [] and p["data"] == []


def test_a_disabled_source_is_off_and_never_fetched(db, bus, cal_config):
    cal_config["calendar"]["sources"]["nasdaq_ipo"]["enabled"] = False
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW, env={})
    assert not fetch.urls("nasdaq")
    assert _payload(bus, handlers.CACHE_CAL)["sources"]["nasdaq_ipo"] == "off"


def test_a_second_caller_while_a_refresh_runs_is_skipped(db, bus, cal_config):
    assert econ_calendar._CAL_LOCK.acquire(blocking=False)
    try:
        assert econ_calendar.refresh(bus, db, FakeFetch(), now=NOW, env={}) == {"skipped": "busy"}
    finally:
        econ_calendar._CAL_LOCK.release()


def test_the_key_is_read_from_the_environment_at_call_time(db, bus, cal_config, monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", KEY)
    fetch = FakeFetch()
    econ_calendar.refresh(bus, db, fetch, now=NOW)
    assert fetch.urls("api.stlouisfed.org")
