"""The poll cycle: feeds -> adapters -> store -> the three views. A fake fetcher,
no network, tmp_path stores only."""
import json
import logging
import pathlib
import threading

import pytest

from services.news_svc import compute, handlers, store
from services.news_svc.adapters import edgar
from shared import news_config as nc

FIX = pathlib.Path(__file__).parent / "fixtures"
NOW = "2026-09-26T00:00:00+00:00"


class FakeFetch:
    def __init__(self, bodies):
        self.bodies, self.calls = bodies, []

    def __call__(self, url, *, etag=None, last_modified=None, user_agent=None, timeout=20):
        self.calls.append(url)
        body = self.bodies.get(url)
        if body is None:
            raise compute.FetchError(f"404 {url}")
        if body == "NOT_MODIFIED":
            return compute.Fetched(status=304, body=b"", etag=etag, last_modified=last_modified)
        return compute.Fetched(status=200, body=body, etag="e1", last_modified=None)


def _cfg(feeds):
    return {"collector": {"keep_days": 7, "view_items": 300, "request_timeout_s": 5,
                          "sec_user_agent": "t"}, "feeds": feeds}


@pytest.fixture(autouse=True)
def _no_sleep_and_fresh_caches(monkeypatch):
    """The SEC pacing sleeps are recorded, never slept; the CIK map is per test."""
    sleeps = []
    monkeypatch.setattr(compute.time, "sleep", lambda s: sleeps.append(s))
    compute._cik_cache.update(ts=0.0, map={})
    yield sleeps
    compute._cik_cache.update(ts=0.0, map={})


def _config(monkeypatch, feeds, flags=None):
    """Point shared.news_config at an in-memory config (the publish-time
    re-check reads it)."""
    cfg = {"feeds": [dict(f) for f in feeds], "feed_flags": dict(flags or {})}
    monkeypatch.setattr(nc, "load", lambda: cfg)
    return cfg


# ── the plan's tests ─────────────────────────────────────────────────────────

def test_rss_feed_poll_stores_items_and_records_state(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "MarketWatch", "kind": "rss", "public": True, "url": "https://mw", "enabled": True}
    fetch = FakeFetch({"https://mw": (FIX / "marketwatch.xml").read_bytes()})
    res = compute.poll_feed(feed, db, fetch, universe=["AAPL"], now=NOW, cfg=_cfg([feed]))
    assert res["inserted"] > 0 and res["error"] is None
    assert db.feed_state("MarketWatch")["etag"] == "e1"


def test_not_modified_costs_nothing_and_is_not_an_error(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.set_feed_state("MarketWatch", etag="e0")
    feed = {"name": "MarketWatch", "kind": "rss", "public": True, "url": "https://mw"}
    res = compute.poll_feed(feed, db, FakeFetch({"https://mw": "NOT_MODIFIED"}),
                            universe=[], now=NOW, cfg=_cfg([feed]))
    assert res == {"feed": "MarketWatch", "inserted": 0, "error": None, "status": 304}


def test_a_failing_feed_keeps_its_items_and_reports_the_error(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compute._degrade, "degraded", lambda area, **kw: calls.append(area))
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "MarketWatch", "kind": "rss", "public": True, "url": "https://mw"}
    compute.poll_feed(feed, db, FakeFetch({"https://mw": (FIX / "marketwatch.xml").read_bytes()}),
                      universe=[], now=NOW, cfg=_cfg([feed]))
    before = len(db.newest(500))
    res = compute.poll_feed(feed, db, FakeFetch({}), universe=[], now=NOW, cfg=_cfg([feed]))
    assert res["error"] and len(db.newest(500)) == before
    assert calls == ["news.feed.MarketWatch"]


def test_yahoo_polls_one_url_per_symbol(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "Yahoo Finance", "kind": "yahoo_ticker", "public": True, "url": "https://y?s={symbol}"}
    body = (FIX / "yahoo_nvda.xml").read_bytes()
    fetch = FakeFetch({"https://y?s=NVDA": body, "https://y?s=SPY": body})
    compute.poll_feed(feed, db, fetch, universe=["NVDA", "SPY", "$SPX"], now=NOW, cfg=_cfg([feed]))
    assert sorted(fetch.calls) == ["https://y?s=NVDA", "https://y?s=SPY"]


def test_form4_poll_fetches_only_unseen_accessions(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "SEC Insider Buys", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom = (FIX / "edgar_current_4.xml").read_bytes()
    entries = edgar.parse_current(atom)
    first = entries[0]
    folder = first["index_url"].rsplit("/", 1)[0]
    bodies = {edgar.CURRENT.format(form="4"): atom,
              f"{folder}/index.json": b'{"directory":{"item":[{"name":"ownership.xml"}]}}',
              f"{folder}/ownership.xml": (FIX / "form4_buy.xml").read_bytes()}
    fetch = FakeFetch(bodies)
    db.mark_accessions([e["accession"] for e in entries[1:]])   # everything but the first is old
    res = compute.poll_feed(feed, db, fetch, universe=["LEN"], now=NOW, cfg=_cfg([feed]))
    assert res["inserted"] == 1
    assert fetch.calls.count(f"{folder}/ownership.xml") == 1
    assert db.unseen_accessions([first["accession"]]) == []


def test_run_poll_publishes_both_views_and_status(tmp_path, monkeypatch):
    """Fixture adapted from the plan: the public view re-reads the CURRENT flags,
    so the config names Pub / Priv; and Priv's titles differ as well as its URLs,
    or the store merges its copies into Pub's rows as one story (cross-feed title
    merge) and Priv has no row of its own. Every assertion is the plan's."""
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    feeds = [{"name": "Pub", "kind": "rss", "public": True, "url": "https://p", "enabled": True},
             {"name": "Priv", "kind": "rss", "public": False, "url": "https://q", "enabled": True}]
    _config(monkeypatch, [{k: v for k, v in f.items() if k not in ("public", "enabled")}
                          for f in feeds], {"Priv": {"public": False}})
    body = (FIX / "marketwatch.xml").read_bytes()
    body_q = (body.replace(b"marketwatch.com/story", b"private.example/story")
                  .replace(b"<title>", b"<title>Private desk note: "))
    compute.run_poll(bus, db, FakeFetch({"https://p": body, "https://q": body_q}),
                     feeds=feeds, universe=[], now=NOW, cfg=_cfg(feeds))
    feed = bus.cache_get("cache:news:feed").payload
    pub = bus.cache_get("cache:news:feed_public").payload
    status = bus.cache_get("cache:news:status").payload
    assert {i["source"] for i in feed["items"]} == {"Pub", "Priv"}
    assert {i["source"] for i in pub["items"]} == {"Pub"}
    assert {s["name"] for s in status["feeds"]} == {"Pub", "Priv"}
    assert status["ts"] == NOW


# ── A: the public view re-checks the CURRENT flags at every publish ─────────

def _two_public_feeds(monkeypatch):
    feeds = [{"name": "P", "kind": "rss", "url": "https://p"},
             {"name": "Q", "kind": "rss", "url": "https://q"}]
    cfg = _config(monkeypatch, feeds, {"P": {"enabled": True, "public": True},
                                       "Q": {"enabled": True, "public": True}})
    body = (FIX / "marketwatch.xml").read_bytes()
    body_q = (body.replace(b"marketwatch.com/story", b"q.example/story")
                  .replace(b"<title>", b"<title>Q desk: "))
    return cfg, {"https://p": body, "https://q": body_q}


def test_flipping_a_feed_private_drops_its_items_from_feed_public_on_the_next_poll(
        tmp_path, monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    cfg, bodies = _two_public_feeds(monkeypatch)
    compute.run_poll(bus, db, FakeFetch(bodies), feeds=nc.feeds(), universe=[], now=NOW,
                     cfg=_cfg(nc.feeds()))
    pub = bus.cache_get("cache:news:feed_public").payload
    assert {i["source"] for i in pub["items"]} == {"P", "Q"}

    cfg["feed_flags"]["P"] = {"enabled": True, "public": False}     # Settings flips P
    unchanged = {url: "NOT_MODIFIED" for url in bodies}              # no new items at all
    res = compute.run_poll(bus, db, FakeFetch(unchanged), feeds=nc.feeds(), universe=[],
                           now=NOW, cfg=_cfg(nc.feeds()))
    assert all(r["inserted"] == 0 and r["status"] == 304 for r in res["results"])
    pub = bus.cache_get("cache:news:feed_public").payload
    private = bus.cache_get("cache:news:feed").payload
    assert {i["source"] for i in pub["items"]} == {"Q"}
    assert all("P" not in i["sources"] for i in pub["items"])
    assert {i["source"] for i in private["items"]} == {"P", "Q"}


def test_a_disabled_but_public_feed_keeps_its_items_public_until_they_age_out(
        tmp_path, monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    cfg, bodies = _two_public_feeds(monkeypatch)
    compute.run_poll(bus, db, FakeFetch(bodies), feeds=nc.feeds(), universe=[], now=NOW,
                     cfg=_cfg(nc.feeds()))
    cfg["feed_flags"]["P"] = {"enabled": False, "public": True}
    fetch = FakeFetch(bodies)
    compute.run_poll(bus, db, fetch, feeds=nc.feeds(), universe=[], now=NOW,
                     cfg=_cfg(nc.feeds()))
    assert "https://p" not in fetch.calls
    pub = bus.cache_get("cache:news:feed_public").payload
    assert {i["source"] for i in pub["items"]} == {"P", "Q"}


def test_publish_feed_public_does_not_assert_on_the_ingest_flag():
    from shared.bus import Bus
    bus = Bus()
    handlers.publish_feed_public(bus, [{"id": "x", "public": False}], NOW)
    assert bus.cache_get("cache:news:feed_public").payload["items"][0]["id"] == "x"


def test_run_poll_publishes_the_public_view_through_public_sources(tmp_path, monkeypatch):
    """The public view is the store's ``public_sources`` query over the
    CURRENT public names, never a page-side filter of the private view."""
    from shared.bus import Bus
    seen = []
    db = store.Store(tmp_path / "n.db")
    real = db.newest

    def spy(limit, **kw):
        seen.append(kw)
        return real(limit, **kw)

    monkeypatch.setattr(db, "newest", spy)
    _config(monkeypatch, [{"name": "A", "kind": "rss", "url": "u"}],
            {"A": {"public": False}})
    compute.run_poll(Bus(), db, FakeFetch({}), feeds=[], universe=[], now=NOW,
                     cfg=_cfg([]))
    assert {} in seen
    assert {"public_sources": []} in seen


# ── B: which User-Agent each request carries ─────────────────────────────────

class RecordingFetch(FakeFetch):
    def __init__(self, bodies):
        super().__init__(bodies)
        self.agents = {}

    def __call__(self, url, *, user_agent=None, **kw):
        self.agents[url] = user_agent
        return super().__call__(url, user_agent=user_agent, **kw)


def _ua_cfg(feeds):
    cfg = _cfg(feeds)
    cfg["collector"].update(sec_user_agent="SEC-UA contact@x", feed_user_agent="Mozilla/5.0 test")
    return cfg


def test_sec_requests_carry_the_sec_agent_and_every_other_feed_a_browser_one(tmp_path):
    db = store.Store(tmp_path / "n.db")
    mw = (FIX / "marketwatch.xml").read_bytes()
    atom4 = (FIX / "edgar_current_4.xml").read_bytes()
    atom_s3 = (FIX / "edgar_current_s3.xml").read_bytes()
    first = edgar.parse_current(atom4)[0]
    folder = first["index_url"].rsplit("/", 1)[0]
    db.mark_accessions([e["accession"] for e in edgar.parse_current(atom4)[1:]])
    feeds = [
        {"name": "R", "kind": "rss", "public": True, "url": "https://r"},
        {"name": "G", "kind": "google_news", "public": True, "query": "site:wsj.com"},
        {"name": "Y", "kind": "yahoo_ticker", "public": True, "url": "https://y?s={symbol}"},
        {"name": "F4", "kind": "edgar_form4", "public": True, "min_value_usd": 0},
        {"name": "S3", "kind": "edgar_filings", "public": True, "forms": ["S-3"]},
    ]
    from services.news_svc.adapters import google_news
    fetch = RecordingFetch({
        "https://r": mw, google_news.url(feeds[1]): (FIX / "google_wsj.xml").read_bytes(),
        "https://y?s=NVDA": (FIX / "yahoo_nvda.xml").read_bytes(),
        edgar.CURRENT.format(form="4"): atom4,
        f"{folder}/index.json": b'{"directory":{"item":[{"name":"ownership.xml"}]}}',
        f"{folder}/ownership.xml": (FIX / "form4_buy.xml").read_bytes(),
        edgar.CURRENT.format(form="S-3"): atom_s3,
        edgar.TICKERS_JSON: (FIX / "company_tickers.json").read_bytes(),
    })
    for f in feeds:
        res = compute.poll_feed(f, db, fetch, universe=["NVDA"], now=NOW, cfg=_ua_cfg(feeds))
        assert res["error"] is None, res
    sec = {u for u in fetch.agents if "sec.gov" in u}
    assert sec >= {edgar.CURRENT.format(form="4"), f"{folder}/index.json",
                   f"{folder}/ownership.xml", edgar.CURRENT.format(form="S-3"),
                   edgar.TICKERS_JSON}
    for url, ua in fetch.agents.items():
        want = "SEC-UA contact@x" if url in sec else "Mozilla/5.0 test"
        assert ua == want, (url, ua)


def test_a_config_without_a_feed_agent_uses_the_built_in_one(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "R", "kind": "rss", "public": True, "url": "https://r"}
    fetch = RecordingFetch({"https://r": (FIX / "marketwatch.xml").read_bytes()})
    compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    assert fetch.agents["https://r"] == nc.DEFAULTS["collector"]["feed_user_agent"]


# ── D: validators and accessions are saved only after the insert ────────────

def _boom(*a, **kw):
    raise RuntimeError("disk full")


def test_a_failed_insert_does_not_save_the_etag(tmp_path, monkeypatch):
    monkeypatch.setattr(compute._degrade, "degraded", lambda area, **kw: None)
    db = store.Store(tmp_path / "n.db")
    db.set_feed_state("MW", etag="e0", last_modified="lm0")
    feed = {"name": "MW", "kind": "rss", "public": True, "url": "https://mw"}
    monkeypatch.setattr(db, "insert_many", _boom)
    res = compute.poll_feed(feed, db, FakeFetch({"https://mw": (FIX / "marketwatch.xml").read_bytes()}),
                            universe=[], now=NOW, cfg=_cfg([feed]))
    st = db.feed_state("MW")
    assert res["error"] and st["etag"] == "e0" and st["last_modified"] == "lm0"
    assert st["error"] and st["last_ok"] is None


def test_a_failed_insert_leaves_the_edgar_accessions_unseen(tmp_path, monkeypatch):
    monkeypatch.setattr(compute._degrade, "degraded", lambda area, **kw: None)
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "S3", "kind": "edgar_filings", "public": True, "forms": ["S-3"]}
    atom = (FIX / "edgar_current_s3.xml").read_bytes()
    accs = [e["accession"] for e in edgar.parse_current(atom)]
    fetch = FakeFetch({edgar.CURRENT.format(form="S-3"): atom,
                       edgar.TICKERS_JSON: (FIX / "company_tickers.json").read_bytes()})
    monkeypatch.setattr(db, "insert_many", _boom)
    res = compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    assert res["error"]
    assert db.unseen_accessions(accs) == accs


def test_the_etag_is_saved_once_the_insert_succeeds(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.set_feed_state("MW", etag="e0")
    feed = {"name": "MW", "kind": "rss", "public": True, "url": "https://mw"}
    compute.poll_feed(feed, db, FakeFetch({"https://mw": (FIX / "marketwatch.xml").read_bytes()}),
                      universe=[], now=NOW, cfg=_cfg([feed]))
    st = db.feed_state("MW")
    assert st["etag"] == "e1" and st["last_ok"] == NOW and st["error"] is None


# ── E: stale items are not re-inserted every cycle ──────────────────────────

def test_items_older_than_keep_days_are_not_inserted(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "MW", "kind": "rss", "public": True, "url": "https://mw"}
    later = "2026-10-10T00:00:00+00:00"          # the fixture's items are 15 days old
    res = compute.poll_feed(feed, db, FakeFetch({"https://mw": (FIX / "marketwatch.xml").read_bytes()}),
                            universe=[], now=later, cfg=_cfg([feed]))
    assert res["inserted"] == 0 and res["error"] is None
    assert db.newest(500) == []


def test_insert_many_is_given_the_prune_cutoff(tmp_path, monkeypatch):
    db = store.Store(tmp_path / "n.db")
    got = {}
    real = db.insert_many

    def spy(rows, **kw):
        got.update(kw)
        return real(rows, **kw)

    monkeypatch.setattr(db, "insert_many", spy)
    feed = {"name": "MW", "kind": "rss", "public": True, "url": "https://mw"}
    compute.poll_feed(feed, db, FakeFetch({"https://mw": (FIX / "marketwatch.xml").read_bytes()}),
                      universe=[], now=NOW, cfg=_cfg([feed]))
    assert got["min_published"] == "2026-09-19T00:00:00+00:00"


# ── F: EDGAR ─────────────────────────────────────────────────────────────────

def _form4_setup(db, pick):
    """The Form 4 atom with only the entries ``pick`` selects still unseen."""
    atom = (FIX / "edgar_current_4.xml").read_bytes()
    entries = edgar.parse_current(atom)
    fresh = [e for e in entries if pick(e)]
    db.mark_accessions([e["accession"] for e in entries if e not in fresh])
    return atom, fresh


def _index(name="ownership.xml"):
    return json.dumps({"directory": {"item": [{"name": name}]}}).encode()


def test_form4_amendments_are_skipped_without_a_fetch_and_marked_seen(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "F4", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom, fresh = _form4_setup(db, lambda e: e["form"] != "4")
    assert fresh and all(e["form"] == "4/A" for e in fresh)
    fetch = FakeFetch({edgar.CURRENT.format(form="4"): atom})
    res = compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    assert res["inserted"] == 0 and res["error"] is None
    assert fetch.calls == [edgar.CURRENT.format(form="4")]
    assert db.unseen_accessions([e["accession"] for e in fresh]) == []


def test_filings_keep_exact_form_matches_only(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "S3", "kind": "edgar_filings", "public": True, "forms": ["S-3"]}
    atom = (FIX / "edgar_current_s3.xml").read_bytes()
    entries = edgar.parse_current(atom)
    assert {e["form"] for e in entries} >= {"S-3", "S-3ASR", "S-3/A", "S-3D"}
    fetch = FakeFetch({edgar.CURRENT.format(form="S-3"): atom,
                       edgar.TICKERS_JSON: (FIX / "company_tickers.json").read_bytes()})
    res = compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    stored = db.newest(500)
    assert res["inserted"] == len(stored) == sum(e["form"] == "S-3" for e in entries)
    assert {i["detail"]["form"] for i in stored} == {"S-3"}
    assert db.unseen_accessions([e["accession"] for e in entries]) == []


def test_a_poison_accession_is_logged_once_marked_seen_and_does_not_stop_the_rest(
        tmp_path, caplog):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "F4", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom, fresh = _form4_setup(db, lambda e: e["form"] == "4")
    bad_json, no_xml, bad_xml, sale, good = fresh[:5]
    db.mark_accessions([e["accession"] for e in fresh[5:]])
    folder = lambda e: e["index_url"].rsplit("/", 1)[0]  # noqa: E731
    buy = (FIX / "form4_buy.xml").read_bytes()
    assert b"<transactionCode>P</transactionCode>" in buy
    bodies = {edgar.CURRENT.format(form="4"): atom,
              f"{folder(bad_json)}/index.json": b"<html>not json</html>",
              f"{folder(no_xml)}/index.json": b'{"directory": {"item": []}}',
              f"{folder(bad_xml)}/index.json": _index(),
              f"{folder(bad_xml)}/ownership.xml": b"\x00 not xml",
              f"{folder(sale)}/index.json": _index(),         # a sale: no purchase, normal
              f"{folder(sale)}/ownership.xml": buy.replace(
                  b"<transactionCode>P</transactionCode>",
                  b"<transactionCode>S</transactionCode>"),
              f"{folder(good)}/index.json": _index(),
              f"{folder(good)}/ownership.xml": buy}
    with caplog.at_level(logging.WARNING, logger="news_svc.compute"):
        res = compute.poll_feed(feed, db, FakeFetch(bodies), universe=["LEN"], now=NOW,
                                cfg=_cfg([feed]))
    assert res["error"] is None and res["inserted"] == 1
    assert db.unseen_accessions([e["accession"] for e in fresh]) == []
    text = caplog.text
    for poison in (bad_json, no_xml, bad_xml):
        assert text.count(poison["accession"]) == 1, poison["accession"]
    assert good["accession"] not in text and sale["accession"] not in text


def test_a_4xx_on_one_accession_is_poison_not_an_outage(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "F4", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom, fresh = _form4_setup(db, lambda e: e["form"] == "4")
    gone, good = fresh[:2]
    db.mark_accessions([e["accession"] for e in fresh[2:]])
    g = good["index_url"].rsplit("/", 1)[0]

    def fetch(url, **kw):
        if url.startswith(gone["index_url"].rsplit("/", 1)[0]):
            raise compute.FetchError(f"HTTP 404 {url}", status=404)
        return FakeFetch({edgar.CURRENT.format(form="4"): atom, f"{g}/index.json": _index(),
                          f"{g}/ownership.xml": (FIX / "form4_buy.xml").read_bytes()})(url, **kw)

    res = compute.poll_feed(feed, db, fetch, universe=["LEN"], now=NOW, cfg=_cfg([feed]))
    assert res["inserted"] == 1 and res["error"] is None
    assert db.unseen_accessions([gone["accession"], good["accession"]]) == []


def test_an_sec_outage_mid_poll_leaves_the_unfetched_accessions_for_the_next_poll(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "F4", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom, fresh = _form4_setup(db, lambda e: e["form"] == "4")
    good, down, later = fresh[:3]
    db.mark_accessions([e["accession"] for e in fresh[3:]])
    g = good["index_url"].rsplit("/", 1)[0]
    calls = []

    def fetch(url, **kw):
        calls.append(url)
        if url.startswith(down["index_url"].rsplit("/", 1)[0]):
            raise compute.FetchError("timed out")            # no status: transient
        return FakeFetch({edgar.CURRENT.format(form="4"): atom, f"{g}/index.json": _index(),
                          f"{g}/ownership.xml": (FIX / "form4_buy.xml").read_bytes()})(url, **kw)

    res = compute.poll_feed(feed, db, fetch, universe=["LEN"], now=NOW, cfg=_cfg([feed]))
    assert res["inserted"] == 1
    assert db.unseen_accessions([e["accession"] for e in (good, down, later)]) == [
        down["accession"], later["accession"]]
    assert not any(later["index_url"].rsplit("/", 1)[0] in c for c in calls)


def test_a_company_tickers_failure_stores_filings_without_tickers(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "S3", "kind": "edgar_filings", "public": True, "forms": ["S-3"]}
    atom = (FIX / "edgar_current_s3.xml").read_bytes()
    res = compute.poll_feed(feed, db, FakeFetch({edgar.CURRENT.format(form="S-3"): atom}),
                            universe=[], now=NOW, cfg=_cfg([feed]))
    assert res["error"] is None and res["inserted"] > 0
    assert all(i["tickers"] == [] for i in db.newest(500))


def test_a_company_tickers_body_that_is_not_json_is_not_an_aborted_feed(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "S3", "kind": "edgar_filings", "public": True, "forms": ["S-3"]}
    atom = (FIX / "edgar_current_s3.xml").read_bytes()
    fetch = FakeFetch({edgar.CURRENT.format(form="S-3"): atom, edgar.TICKERS_JSON: b"<html>"})
    res = compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    assert res["error"] is None and res["inserted"] > 0


def test_sec_requests_are_paced(tmp_path, monkeypatch):
    assert compute._SEC_MIN_GAP_S >= 0.15
    events = []
    monkeypatch.setattr(compute.time, "sleep", lambda s: events.append(("sleep", s)))
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "F4", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom, fresh = _form4_setup(db, lambda e: e["form"] == "4")
    db.mark_accessions([e["accession"] for e in fresh[2:]])
    bodies = {edgar.CURRENT.format(form="4"): atom}
    for e in fresh[:2]:
        f = e["index_url"].rsplit("/", 1)[0]
        bodies[f"{f}/index.json"] = _index()
        bodies[f"{f}/ownership.xml"] = (FIX / "form4_buy.xml").read_bytes()
    inner = FakeFetch(bodies)

    def fetch(url, **kw):
        events.append(("fetch", url))
        return inner(url, **kw)

    compute.poll_feed(feed, db, fetch, universe=["LEN"], now=NOW, cfg=_cfg([feed]))
    fetches = [i for i, e in enumerate(events) if e[0] == "fetch"]
    assert len(fetches) == 5
    for a, b in zip(fetches, fetches[1:]):
        gap = [e for e in events[a + 1:b] if e[0] == "sleep"]
        assert gap and 0 < sum(s for _, s in gap) <= compute._SEC_MIN_GAP_S, events


# ── G: Google News without a query, Yahoo's per-symbol failures ─────────────

def test_a_google_feed_without_a_query_records_the_error_and_fetches_nothing(
        tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compute._degrade, "degraded", lambda area, **kw: calls.append(area))
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "WSJ", "kind": "google_news", "public": True, "query": "  "}
    fetch = FakeFetch({})
    res = compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    assert fetch.calls == []
    assert res["error"] == "no query configured"
    assert db.feed_state("WSJ")["error"] == "no query configured"
    assert calls == ["news.feed.WSJ"]


def _yahoo(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "Yahoo Finance", "kind": "yahoo_ticker", "public": True,
            "url": "https://y?s={symbol}"}
    return db, feed, (FIX / "yahoo_nvda.xml").read_bytes()


def test_one_yahoo_symbol_failing_does_not_abort_the_rest(tmp_path, monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(compute._degrade, "degraded", lambda area, **kw: calls.append(area))
    db, feed, body = _yahoo(tmp_path)
    fetch = FakeFetch({"https://y?s=NVDA": body})               # SPY and AMD 404
    with caplog.at_level(logging.WARNING, logger="news_svc.compute"):
        res = compute.poll_feed(feed, db, fetch, universe=["NVDA", "SPY", "AMD"], now=NOW,
                                cfg=_cfg([feed]))
    assert res["error"] is None and res["inserted"] > 0
    assert calls == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "SPY" in warnings[0].getMessage() and "AMD" in warnings[0].getMessage()
    assert db.feed_state("Yahoo Finance")["error"] is None


def test_every_yahoo_symbol_failing_is_the_feeds_error(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compute._degrade, "degraded", lambda area, **kw: calls.append(area))
    db, feed, _ = _yahoo(tmp_path)
    res = compute.poll_feed(feed, db, FakeFetch({}), universe=["NVDA", "SPY"], now=NOW,
                            cfg=_cfg([feed]))
    assert res["error"] and res["inserted"] == 0
    assert db.feed_state("Yahoo Finance")["error"]
    assert calls == ["news.feed.Yahoo Finance"]


def test_a_yahoo_template_without_a_symbol_is_the_feeds_error(tmp_path, monkeypatch):
    monkeypatch.setattr(compute._degrade, "degraded", lambda area, **kw: None)
    db, feed, _ = _yahoo(tmp_path)
    feed["url"] = "https://y/static"
    fetch = FakeFetch({})
    res = compute.poll_feed(feed, db, fetch, universe=["NVDA"], now=NOW, cfg=_cfg([feed]))
    assert res["error"] and fetch.calls == []


def test_an_empty_universe_is_a_quiet_yahoo_poll_not_an_error(tmp_path):
    db, feed, _ = _yahoo(tmp_path)
    res = compute.poll_feed(feed, db, FakeFetch({}), universe=["$SPX"], now=NOW,
                            cfg=_cfg([feed]))
    assert res["error"] is None and res["inserted"] == 0


def test_yahoo_fetches_in_a_small_pool_and_writes_on_the_calling_thread(tmp_path, monkeypatch):
    assert 1 <= compute._YAHOO_WORKERS <= 4
    db, feed, body = _yahoo(tmp_path)
    fetch_threads, write_threads = set(), []
    inner = FakeFetch({f"https://y?s={s}": body for s in ("NVDA", "SPY", "AMD", "QQQ", "IWM")})

    def fetch(url, **kw):
        fetch_threads.add(threading.current_thread().name)
        return inner(url, **kw)

    real = db.insert_many
    monkeypatch.setattr(db, "insert_many", lambda rows, **kw: (
        write_threads.append(threading.current_thread()), real(rows, **kw))[1])
    compute.poll_feed(feed, db, fetch, universe=["NVDA", "SPY", "AMD", "QQQ", "IWM"],
                      now=NOW, cfg=_cfg([feed]))
    assert len(inner.calls) == 5
    assert write_threads == [threading.current_thread()]
    assert threading.current_thread().name not in fetch_threads


# ── H: one poll at a time ────────────────────────────────────────────────────

def test_a_second_poll_while_one_runs_returns_busy_at_once(monkeypatch, tmp_path):
    started, release = threading.Event(), threading.Event()
    opened = []

    real_store = store.Store      # compute._store IS this module: bind before patching

    def fake_store(*a, **kw):
        s = real_store(tmp_path / "n.db")
        opened.append(s)
        return s

    def slow_poll(bus, db, fetch, **kw):
        started.set()
        release.wait(5)
        return {"results": []}

    monkeypatch.setattr(compute._store, "Store", fake_store)
    monkeypatch.setattr(compute, "run_poll", slow_poll)
    first = {}
    t = threading.Thread(target=lambda: first.update(compute.poll_now(object())))
    t.start()
    assert started.wait(5)
    assert compute.poll_now(object()) == {"skipped": "busy"}
    assert len(opened) == 1                                  # the busy call opened no store
    release.set()
    t.join(5)
    assert first == {"results": []}
    assert compute.poll_now.__module__ and not compute._POLL_LOCK.locked()


def test_poll_now_closes_its_store_and_releases_the_lock_on_failure(monkeypatch, tmp_path):
    closed = []

    class Tracked(store.Store):
        def close(self):
            closed.append(True)
            super().close()

    monkeypatch.setattr(compute._store, "Store", lambda *a, **kw: Tracked(tmp_path / "n.db"))
    monkeypatch.setattr(compute, "run_poll", _boom)
    with pytest.raises(RuntimeError):
        compute.poll_now(object())
    assert closed == [True] and not compute._POLL_LOCK.locked()


def test_poll_now_fetches_with_the_configured_cap_and_agents(monkeypatch, tmp_path):
    got = []

    def fake_http(url, **kw):
        got.append(kw)
        raise compute.FetchError("offline")

    monkeypatch.setattr(compute, "http_fetch", fake_http)
    monkeypatch.setattr(compute._degrade, "degraded", lambda area, **kw: None)
    real_store = store.Store
    monkeypatch.setattr(compute._store, "Store", lambda *a, **kw: real_store(tmp_path / "n.db"))
    cfg = {"collector": dict(nc.DEFAULTS["collector"], max_body_bytes=1234,
                             feed_user_agent="Mozilla/5.0 x"),
           "feeds": [{"name": "A", "kind": "rss", "url": "https://a"}], "feed_flags": {}}
    monkeypatch.setattr(nc, "load", lambda: cfg)
    from shared.bus import Bus
    compute.poll_now(Bus())
    assert got and got[0]["max_bytes"] == 1234 and got[0]["user_agent"] == "Mozilla/5.0 x"


# ── I: poll_feed never raises ────────────────────────────────────────────────

def test_poll_feed_never_raises_even_when_the_state_write_fails(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compute._degrade, "degraded", lambda area, **kw: calls.append(area))
    db = store.Store(tmp_path / "n.db")
    monkeypatch.setattr(db, "set_feed_state", _boom)
    feed = {"name": "MW", "kind": "rss", "public": True, "url": "https://mw"}
    res = compute.poll_feed(feed, db, FakeFetch({}), universe=[], now=NOW, cfg=_cfg([feed]))
    assert res["error"] and res["inserted"] == 0
    assert calls and calls[0] == "news.feed.MW"


def test_an_unknown_kind_is_an_error_not_an_edgar_poll(tmp_path, monkeypatch):
    monkeypatch.setattr(compute._degrade, "degraded", lambda area, **kw: None)
    db = store.Store(tmp_path / "n.db")
    fetch = FakeFetch({})
    res = compute.poll_feed({"name": "X", "kind": "carrier_pigeon"}, db, fetch,
                            universe=[], now=NOW, cfg=_cfg([]))
    assert res["error"] and fetch.calls == []


# ── J: the status view lists every configured feed ──────────────────────────

def test_status_lists_every_configured_feed_enabled_or_not(tmp_path, monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    _config(monkeypatch,
            [{"name": "On", "kind": "rss", "url": "https://on"},
             {"name": "Off", "kind": "rss", "url": "https://off"},
             {"name": "Broken", "kind": "rss", "url": "https://broken"}],
            {"Off": {"enabled": False, "public": False}})
    monkeypatch.setattr(compute._degrade, "degraded", lambda area, **kw: None)
    fetch = FakeFetch({"https://on": (FIX / "marketwatch.xml").read_bytes()})
    compute.run_poll(bus, db, fetch, feeds=nc.feeds(), universe=[], now=NOW,
                     cfg=_cfg(nc.feeds()))
    rows = {s["name"]: s for s in bus.cache_get("cache:news:status").payload["feeds"]}
    assert list(rows) == ["On", "Off", "Broken"]
    for row in rows.values():
        assert set(row) >= {"name", "enabled", "public", "last_ok", "last_poll", "error",
                            "inserted"}
    assert rows["On"]["enabled"] and rows["On"]["public"] and rows["On"]["inserted"] > 0
    assert rows["On"]["last_ok"] == NOW and rows["On"]["error"] is None
    assert rows["Off"] == {**rows["Off"], "enabled": False, "public": False, "last_ok": None,
                           "last_poll": None, "error": None, "inserted": 0}
    assert rows["Broken"]["error"] and rows["Broken"]["last_poll"] == NOW
    assert rows["Broken"]["last_ok"] is None


def test_handle_command_runs_a_poll_on_news_refresh(monkeypatch):
    ran = []
    monkeypatch.setattr(compute, "poll_now", lambda bus: ran.append(bus))

    class Cmd:
        type = "news_refresh"

    handlers.handle_command("BUS", Cmd())
    handlers.handle_command("BUS", type("Other", (), {"type": "nope"})())
    assert ran == ["BUS"]
