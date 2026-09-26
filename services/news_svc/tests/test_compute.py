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
    assert "ts" not in feed and "ts" not in pub     # the envelope carries the time


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
    handlers.publish_feed_public(bus, [{"id": "x", "public": False}])
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
    # v2: the headline views exclude the SEC kinds and the SEC views take only
    # them (Task 14); both public reads still go through public_sources.
    edgar_kinds = ("edgar_form4", "edgar_filings")
    assert {"exclude_kinds": edgar_kinds} in seen
    assert {"public_sources": [], "exclude_kinds": edgar_kinds} in seen
    assert {"kinds": edgar_kinds} in seen
    assert {"public_sources": [], "kinds": edgar_kinds} in seen


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


@pytest.mark.parametrize("dedupe, expected", [
    (None, 6),                                   # no [dedupe] table -> the default
    ({"same_feed_merge_h": 2}, 2),
    ({"same_feed_merge_h": 0}, 0),               # off
    ({"same_feed_merge_h": "six"}, 6),           # malformed -> the default
    ({"same_feed_merge_h": -1}, 6),
])
def test_insert_many_is_given_the_same_feed_merge_window(tmp_path, monkeypatch, dedupe,
                                                         expected):
    db = store.Store(tmp_path / "n.db")
    got = {}
    real = db.insert_many

    def spy(rows, **kw):
        got.update(kw)
        return real(rows, **kw)

    monkeypatch.setattr(db, "insert_many", spy)
    feed = {"name": "MW", "kind": "rss", "public": True, "url": "https://mw"}
    cfg = _cfg([feed])
    if dedupe is not None:
        cfg["dedupe"] = dedupe
    compute.poll_feed(feed, db, FakeFetch({"https://mw": (FIX / "marketwatch.xml").read_bytes()}),
                      universe=[], now=NOW, cfg=cfg)
    assert got["same_feed_merge_h"] == expected


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


def test_form4_amendments_are_skipped_without_a_fetch_and_not_marked_seen(tmp_path):
    """Changed by the per-feed accession fix: a form the feed did not ask for is
    skipped at no cost but NOT remembered, so widening ``forms`` later still
    picks it up (it was "marked seen" until then)."""
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "F4", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom, fresh = _form4_setup(db, lambda e: e["form"] != "4")
    assert fresh and all(e["form"] == "4/A" for e in fresh)
    fetch = FakeFetch({edgar.CURRENT.format(form="4"): atom})
    res = compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    assert res["inserted"] == 0 and res["error"] is None
    assert fetch.calls == [edgar.CURRENT.format(form="4")]
    accs = [e["accession"] for e in fresh]
    assert db.unseen_accessions(accs, feed="F4") == accs
    compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    assert fetch.calls == [edgar.CURRENT.format(form="4")] * 2   # still no per-filing fetch


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
    # only the wanted form is remembered (per-feed accession fix); the rest stay
    # unseen for this feed, so widening ``forms`` later still reads them
    s3 = [e["accession"] for e in entries if e["form"] == "S-3"]
    other = [e["accession"] for e in entries if e["form"] != "S-3"]
    assert db.unseen_accessions(s3, feed="S3") == []
    assert db.unseen_accessions(other, feed="S3") == other


def test_a_poison_accession_is_logged_once_marked_seen_and_does_not_stop_the_rest(
        tmp_path, caplog):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "F4", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom, fresh = _form4_setup(db, lambda e: e["form"] == "4")
    bad_json, no_xml, bad_xml, sale, good, sale2, sale3 = fresh[:7]
    # sale2 / sale3: readable, so 3 poison of 7 stays under "more than half failed"
    db.mark_accessions([e["accession"] for e in fresh[7:]])
    folder = lambda e: e["index_url"].rsplit("/", 1)[0]  # noqa: E731
    buy = (FIX / "form4_buy.xml").read_bytes()
    assert b"<transactionCode>P</transactionCode>" in buy
    sold = buy.replace(b"<transactionCode>P</transactionCode>",
                       b"<transactionCode>S</transactionCode>")
    bodies = {edgar.CURRENT.format(form="4"): atom,
              f"{folder(sale2)}/index.json": _index(), f"{folder(sale2)}/ownership.xml": sold,
              f"{folder(sale3)}/index.json": _index(), f"{folder(sale3)}/ownership.xml": sold,
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


# ── K: an SEC block or outage is transient: nothing is marked seen ──────────

def _degrades(monkeypatch):
    calls = []
    monkeypatch.setattr(compute._degrade, "degraded",
                        lambda area, **kw: calls.append((area, kw.get("detail"))))
    return calls


@pytest.mark.parametrize("status", [403, 429, 500, 503, None])
def test_an_sec_block_on_every_accession_marks_nothing_seen_and_is_the_feeds_error(
        tmp_path, monkeypatch, status):
    calls = _degrades(monkeypatch)
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "F4", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom = (FIX / "edgar_current_4.xml").read_bytes()
    entries = edgar.parse_current(atom)
    requested = []

    def fetch(url, **kw):
        requested.append(url)
        if url.endswith("/index.json"):
            raise compute.FetchError(f"HTTP {status} {url}", status=status)
        return FakeFetch({edgar.CURRENT.format(form="4"): atom})(url, **kw)

    res = compute.poll_feed(feed, db, fetch, universe=["LEN"], now=NOW, cfg=_cfg([feed]))
    accs = [e["accession"] for e in entries]
    assert db.unseen_accessions(accs, feed="F4") == accs                 # 0 marked seen
    assert sum(u.endswith("/index.json") for u in requested) == 1        # stopped at once
    assert res["error"] and res["inserted"] == 0
    st = db.feed_state("F4")
    assert st["error"] and st["last_ok"] is None and st["last_poll"] == NOW
    assert [a for a, _ in calls] == ["news.feed.F4"]                     # one degrade


def test_an_sec_outage_mid_poll_keeps_the_items_built_so_far_and_reports_it(
        tmp_path, monkeypatch):
    calls = _degrades(monkeypatch)
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "F4", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom, fresh = _form4_setup(db, lambda e: e["form"] == "4")
    good, blocked, later = fresh[:3]
    db.mark_accessions([e["accession"] for e in fresh[3:]])
    g = good["index_url"].rsplit("/", 1)[0]

    def fetch(url, **kw):
        if url.startswith(blocked["index_url"].rsplit("/", 1)[0]):
            raise compute.FetchError("HTTP 403", status=403)
        return FakeFetch({edgar.CURRENT.format(form="4"): atom, f"{g}/index.json": _index(),
                          f"{g}/ownership.xml": (FIX / "form4_buy.xml").read_bytes()})(url, **kw)

    res = compute.poll_feed(feed, db, fetch, universe=["LEN"], now=NOW, cfg=_cfg([feed]))
    assert res["inserted"] == 1 and "403" in res["error"]
    assert db.unseen_accessions([e["accession"] for e in (good, blocked, later)],
                                feed="F4") == [blocked["accession"], later["accession"]]
    assert len(db.newest(10)) == 1
    assert [a for a, _ in calls] == ["news.feed.F4"]


def test_more_than_half_of_the_accessions_failing_is_the_feeds_error(tmp_path, monkeypatch):
    calls = _degrades(monkeypatch)
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "F4", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom, fresh = _form4_setup(db, lambda e: e["form"] == "4")
    bad1, bad2, bad3, good, sale = fresh[:5]
    db.mark_accessions([e["accession"] for e in fresh[5:]])
    folder = lambda e: e["index_url"].rsplit("/", 1)[0]  # noqa: E731
    buy = (FIX / "form4_buy.xml").read_bytes()
    bodies = {edgar.CURRENT.format(form="4"): atom,
              f"{folder(bad1)}/index.json": b"<html>", f"{folder(bad2)}/index.json": b"<html>",
              f"{folder(bad3)}/index.json": b"<html>",
              f"{folder(good)}/index.json": _index(), f"{folder(good)}/ownership.xml": buy,
              f"{folder(sale)}/index.json": _index(),
              f"{folder(sale)}/ownership.xml": buy.replace(
                  b"<transactionCode>P</transactionCode>", b"<transactionCode>S</transactionCode>")}
    res = compute.poll_feed(feed, db, FakeFetch(bodies), universe=["LEN"], now=NOW,
                            cfg=_cfg([feed]))
    assert res["inserted"] == 1 and res["error"] and "3 of 5" in res["error"]
    assert db.unseen_accessions([e["accession"] for e in fresh[:5]], feed="F4") == []
    assert db.feed_state("F4")["error"] == res["error"]
    assert [a for a, _ in calls] == ["news.feed.F4"]


def test_exactly_half_failing_is_not_an_error(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "F4", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom, fresh = _form4_setup(db, lambda e: e["form"] == "4")
    bad, good = fresh[:2]
    db.mark_accessions([e["accession"] for e in fresh[2:]])
    g = good["index_url"].rsplit("/", 1)[0]
    fetch = FakeFetch({edgar.CURRENT.format(form="4"): atom,
                       f"{bad['index_url'].rsplit('/', 1)[0]}/index.json": b"<html>",
                       f"{g}/index.json": _index(),
                       f"{g}/ownership.xml": (FIX / "form4_buy.xml").read_bytes()})
    res = compute.poll_feed(feed, db, fetch, universe=["LEN"], now=NOW, cfg=_cfg([feed]))
    assert res["error"] is None and res["inserted"] == 1


def test_a_later_forms_atom_failure_keeps_the_earlier_forms_work(tmp_path, monkeypatch):
    calls = _degrades(monkeypatch)
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "S", "kind": "edgar_filings", "public": True, "forms": ["S-3", "S-1"]}
    atom = (FIX / "edgar_current_s3.xml").read_bytes()
    s3 = [e["accession"] for e in edgar.parse_current(atom) if e["form"] == "S-3"]
    fetch = FakeFetch({edgar.CURRENT.format(form="S-3"): atom,       # the S-1 Atom 404s
                       edgar.TICKERS_JSON: (FIX / "company_tickers.json").read_bytes()})
    res = compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    assert res["inserted"] == len(s3) and res["error"] and "S-1" in res["error"]
    assert db.unseen_accessions(s3, feed="S") == []                  # not re-done next poll
    assert [a for a, _ in calls] == ["news.feed.S"]


def test_an_oversized_form4_is_poison_not_an_outage(tmp_path, monkeypatch):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "F4", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom, fresh = _form4_setup(db, lambda e: e["form"] == "4")
    huge, good = fresh[:2]
    db.mark_accessions([e["accession"] for e in fresh[2:]])
    h, g = (e["index_url"].rsplit("/", 1)[0] for e in (huge, good))
    inner = FakeFetch({edgar.CURRENT.format(form="4"): atom, f"{h}/index.json": _index(),
                       f"{g}/index.json": _index(),
                       f"{g}/ownership.xml": (FIX / "form4_buy.xml").read_bytes()})

    def fetch(url, **kw):
        if url == f"{h}/ownership.xml":
            raise compute.TooLarge(f"response too large {url}")
        return inner(url, **kw)

    res = compute.poll_feed(feed, db, fetch, universe=["LEN"], now=NOW, cfg=_cfg([feed]))
    assert res["error"] is None and res["inserted"] == 1
    assert db.unseen_accessions([huge["accession"], good["accession"]], feed="F4") == []


# ── L: the CIK map outlives a failed refresh ────────────────────────────────

def test_a_failed_company_tickers_refresh_keeps_the_stale_map_and_retries(tmp_path, monkeypatch):
    real = edgar.cik_map(json.loads((FIX / "company_tickers.json").read_bytes()))
    compute._cik_cache.update(ts=1.0, map=real)                      # long expired
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "S3", "kind": "edgar_filings", "public": True, "forms": ["S-3"]}
    atom = (FIX / "edgar_current_s3.xml").read_bytes()
    fetch = FakeFetch({edgar.CURRENT.format(form="S-3"): atom})      # tickers json 404s
    res = compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    assert res["error"] is None
    assert any(i["tickers"] for i in db.newest(500))                 # the stale map was used
    assert compute._cik_to_ticker(fetch, "ua", 5) == real
    assert fetch.calls.count(edgar.TICKERS_JSON) == 2                # and retried each time


# ── M: validators belong to the URL they were answered for ──────────────────

class ValidatorFetch(FakeFetch):
    def __init__(self, bodies):
        super().__init__(bodies)
        self.sent = []

    def __call__(self, url, *, etag=None, last_modified=None, **kw):
        self.sent.append((url, etag, last_modified))
        return super().__call__(url, etag=etag, last_modified=last_modified, **kw)


def test_a_changed_feed_url_drops_the_saved_validators(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.set_feed_state("MW", etag="e0", last_modified="lm0", url="https://old")
    feed = {"name": "MW", "kind": "rss", "public": True, "url": "https://new"}
    fetch = ValidatorFetch({"https://new": (FIX / "marketwatch.xml").read_bytes()})
    compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    assert fetch.sent == [("https://new", None, None)]
    st = db.feed_state("MW")
    assert (st["etag"], st["url"]) == ("e1", "https://new")
    compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    assert fetch.sent[-1] == ("https://new", "e1", None)             # same url: sent


def test_a_changed_google_query_drops_the_saved_validators(tmp_path):
    from services.news_svc.adapters import google_news
    db = store.Store(tmp_path / "n.db")
    old = {"name": "G", "kind": "google_news", "public": True, "query": "site:wsj.com"}
    new = dict(old, query="site:ft.com")
    db.set_feed_state("G", etag="e0", url=google_news.url(old))
    fetch = ValidatorFetch({google_news.url(new): (FIX / "google_wsj.xml").read_bytes()})
    compute.poll_feed(new, db, fetch, universe=[], now=NOW, cfg=_cfg([new]))
    assert fetch.sent == [(google_news.url(new), None, None)]


# ── N: seen accessions are per feed ─────────────────────────────────────────

def test_two_form4_feeds_each_process_an_accession(tmp_path):
    db = store.Store(tmp_path / "n.db")
    atom, fresh = _form4_setup(db, lambda e: e["form"] == "4")
    first = fresh[0]
    db.mark_accessions([e["accession"] for e in fresh[1:]])
    f = first["index_url"].rsplit("/", 1)[0]
    fetch = FakeFetch({edgar.CURRENT.format(form="4"): atom, f"{f}/index.json": _index(),
                       f"{f}/ownership.xml": (FIX / "form4_buy.xml").read_bytes()})
    big = {"name": "Big buys", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    mine = {"name": "My tickers", "kind": "edgar_form4", "public": False, "min_value_usd": 0}
    for feed in (big, mine):
        compute.poll_feed(feed, db, fetch, universe=["LEN"], now=NOW, cfg=_cfg([big, mine]))
    assert fetch.calls.count(f"{f}/ownership.xml") == 2
    assert db.newest(10)[0]["sources"] == ["Big buys", "My tickers"]
    for name in ("Big buys", "My tickers"):
        assert db.unseen_accessions([first["accession"]], feed=name) == []


def test_widening_forms_picks_up_an_accession_skipped_before(tmp_path):
    db = store.Store(tmp_path / "n.db")
    atom = (FIX / "edgar_current_s3.xml").read_bytes()
    asr = [e for e in edgar.parse_current(atom) if e["form"] == "S-3ASR"]
    fetch = FakeFetch({edgar.CURRENT.format(form="S-3"): atom,
                       edgar.TICKERS_JSON: (FIX / "company_tickers.json").read_bytes()})
    feed = {"name": "S3", "kind": "edgar_filings", "public": True, "forms": ["S-3"]}
    compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    assert db.unseen_accessions([e["accession"] for e in asr], feed="S3") == [
        e["accession"] for e in asr]
    feed["forms"] = ["S-3", "S-3ASR"]
    compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
    assert {i["detail"]["form"] for i in db.newest(500)} == {"S-3", "S-3ASR"}


_EMPTY_ATOM = (b'<?xml version="1.0" encoding="ISO-8859-1" ?>'
               b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Latest Filings</title></feed>')


def test_the_shipped_offerings_forms_take_each_s3asr_exactly_once(tmp_path):
    """SEC's ``type=`` is a PREFIX match, so the S-3 Atom already lists every
    S-3ASR, and the S-3ASR Atom lists them again. The second fetch is kept (each
    Atom is capped at 100 entries, so a busy S-3 day could push an S-3ASR off the
    first); the accession is handled once per poll and marked seen after it."""
    from shared import news_config as nc
    nc.reset_cache()
    shipped = next(f for f in nc.all_feeds() if f["name"] == "SEC Offerings")
    assert shipped["forms"] == ["S-1", "S-3", "424B5", "S-3ASR"]
    db = store.Store(tmp_path / "n.db")
    atom = (FIX / "edgar_current_s3.xml").read_bytes()
    entries = edgar.parse_current(atom)
    asr = [e["accession"] for e in entries if e["form"] == "S-3ASR"]
    assert asr
    fetch = FakeFetch({edgar.CURRENT.format(form="S-1"): _EMPTY_ATOM,
                       edgar.CURRENT.format(form="S-3"): atom,
                       edgar.CURRENT.format(form="424B5"): _EMPTY_ATOM,
                       edgar.CURRENT.format(form="S-3ASR"): atom,   # worst case: overlaps fully
                       edgar.TICKERS_JSON: (FIX / "company_tickers.json").read_bytes()})
    feed = {"name": "SEC Offerings", "kind": "edgar_filings", "public": True,
            "forms": ["S-1", "S-3", "424B5", "S-3ASR"]}
    for _ in range(2):
        res = compute.poll_feed(feed, db, fetch, universe=[], now=NOW, cfg=_cfg([feed]))
        assert res["error"] is None
    stored = db.newest(500)
    forms = [i["detail"]["form"] for i in stored]
    assert forms.count("S-3ASR") == len(asr)
    assert forms.count("S-3") == sum(e["form"] == "S-3" for e in entries)
    assert set(forms) == {"S-3", "S-3ASR"}
    assert db.unseen_accessions(asr, feed="SEC Offerings") == []


# ── O: an unchanged poll does not bump the feed views ───────────────────────

def test_two_polls_with_identical_rows_do_not_change_the_feed_versions(tmp_path, monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    cfg, bodies = _two_public_feeds(monkeypatch)
    kw = {"feeds": nc.feeds(), "universe": [], "cfg": _cfg(nc.feeds())}
    compute.run_poll(bus, db, FakeFetch(bodies), now=NOW, **kw)
    before = {k: bus.cache_version(k) for k in (handlers.CACHE_FEED, handlers.CACHE_PUBLIC,
                                                handlers.CACHE_STATUS)}
    compute.run_poll(bus, db, FakeFetch(bodies), now="2026-09-26T00:05:00+00:00", **kw)
    after = {k: bus.cache_version(k) for k in before}
    assert after[handlers.CACHE_FEED] == before[handlers.CACHE_FEED]
    assert after[handlers.CACHE_PUBLIC] == before[handlers.CACHE_PUBLIC]
    assert after[handlers.CACHE_STATUS] > before[handlers.CACHE_STATUS]   # last_poll moved


# ── P: a store failure costs its own step, not all three views ──────────────

def test_a_failed_prune_still_publishes_every_view(tmp_path, monkeypatch):
    from shared.bus import Bus
    calls = _degrades(monkeypatch)
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    cfg, bodies = _two_public_feeds(monkeypatch)
    monkeypatch.setattr(db, "prune", _boom)
    compute.run_poll(bus, db, FakeFetch(bodies), feeds=nc.feeds(), universe=[], now=NOW,
                     cfg=_cfg(nc.feeds()))
    for key in (handlers.CACHE_FEED, handlers.CACHE_PUBLIC, handlers.CACHE_STATUS):
        assert bus.cache_get(key) is not None, key
    assert bus.cache_get(handlers.CACHE_FEED).payload["items"]
    assert [a for a, _ in calls] == ["news.prune"]


def test_a_failed_newest_still_publishes_the_status(tmp_path, monkeypatch):
    from shared.bus import Bus
    calls = _degrades(monkeypatch)
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    cfg, bodies = _two_public_feeds(monkeypatch)
    monkeypatch.setattr(db, "newest", _boom)
    compute.run_poll(bus, db, FakeFetch(bodies), feeds=nc.feeds(), universe=[], now=NOW,
                     cfg=_cfg(nc.feeds()))
    assert bus.cache_get(handlers.CACHE_FEED) is None
    assert bus.cache_get(handlers.CACHE_PUBLIC) is None
    assert bus.cache_get(handlers.CACHE_SEC) is None            # v2: the SEC views too
    assert bus.cache_get(handlers.CACHE_SEC_PUBLIC) is None
    assert {s["name"] for s in bus.cache_get(handlers.CACHE_STATUS).payload["feeds"]} == {"P", "Q"}
    assert sorted(a for a, _ in calls) == ["news.publish", "news.publish_public",
                                           "news.publish_sec", "news.publish_sec_public"]


def test_an_unreadable_store_still_publishes_a_status_built_from_config(tmp_path, monkeypatch):
    """``feed_state`` failing must not cost the status view: it is how the page
    says the collector is sick, so it is built from the config alone."""
    from shared.bus import Bus
    calls = _degrades(monkeypatch)
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    cfg, bodies = _two_public_feeds(monkeypatch)
    feeds = nc.feeds()
    compute.run_poll(bus, db, FakeFetch(bodies), feeds=feeds, universe=[], now=NOW,
                     cfg=_cfg(feeds))                         # a healthy poll first
    polled = {s["name"]: s["inserted"]
              for s in bus.cache_get(handlers.CACHE_STATUS).payload["feeds"]}
    calls.clear()
    monkeypatch.setattr(db, "feed_state", _boom)
    # a status that could only come from the store is unavailable; the rows
    # still carry what the config says and this poll's counts
    results = [{"feed": n, "inserted": 3, "error": None, "status": 200} for n in polled]
    monkeypatch.setattr(compute, "poll_feed", lambda f, *a, **kw: dict(
        next(r for r in results if r["feed"] == f["name"])))
    compute.run_poll(bus, db, FakeFetch(bodies), feeds=feeds, universe=[], now=NOW,
                     cfg=_cfg(feeds))
    status = bus.cache_get(handlers.CACHE_STATUS).payload
    assert status["ts"] == NOW
    rows = {s["name"]: s for s in status["feeds"]}
    assert set(rows) == set(polled) == {"P", "Q"}
    for name, row in rows.items():
        f = next(f for f in nc.all_feeds() if f["name"] == name)
        assert row == {"name": name, "kind": f.get("kind"),
                       "enabled": bool(f.get("enabled", True)),
                       "public": bool(f.get("public", False)),
                       "last_ok": None, "last_poll": None,
                       "error": "store unreadable", "inserted": 3}
    assert [a for a, _ in calls] == ["news.status"]


# ── Task 14 (news v2): impact on every item, and the SEC split ──────────────

from services import _degrade  # noqa: E402
from services.news_svc import impact, items  # noqa: E402

_EDGAR = {"edgar_form4", "edgar_filings"}
ICFG = {"high_at": 6, "med_at": 3, "stale_after_h": 24, "multi_source": 1, "watchlist": 2,
        "match_teaser": False,
        "keywords": {"tier1": {"points": 5, "words": ["FOMC"]},
                     "tier2": {"points": 3, "words": ["beats"]}},
        "source_points": {"Priv": 3},
        "form4": {"small_usd": 250_000, "small": 1, "large_usd": 1_000_000, "large": 3,
                  "huge_usd": 10_000_000, "huge": 6, "officer": 1},
        "filings": {"424B5": 3, "S-3": 2, "untracked": -1}}


def _impact_config(monkeypatch, feeds, flags=None, icfg=None):
    cfg = _config(monkeypatch, feeds, flags)
    cfg["impact"] = json.loads(json.dumps(icfg or ICFG))
    return cfg


def _it(url, title, source="Pub", kind="rss", published=NOW, tickers=(), detail=None):
    it = items.make_item(source=source, title=title, url=url, published_at=published,
                         public=True, now=NOW, kind=kind, tickers=tickers, detail=detail)
    return it


def _poll(bus, db, now=NOW, universe=()):
    return compute.run_poll(bus, db, FakeFetch({}), feeds=[], universe=list(universe),
                            now=now, cfg=_cfg([]))


def _by_title(bus, key):
    return {i["title"]: i for i in bus.cache_get(key).payload["items"]}


def test_every_row_is_scored_after_a_poll_and_the_view_carries_it(tmp_path, monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    _impact_config(monkeypatch, [{"name": "Pub", "kind": "rss", "url": "u"}],
                   {"Pub": {"public": True}})
    feed = {"name": "Pub", "kind": "rss", "url": "https://p", "public": True}
    body = (b"<rss><channel><item><title>FOMC holds rates steady this afternoon</title>"
            b"<link>https://p/1</link><pubDate>Fri, 25 Sep 2026 23:00:00 GMT</pubDate>"
            b"</item></channel></rss>")
    compute.run_poll(bus, db, FakeFetch({"https://p": body}), feeds=[feed], universe=[],
                     now=NOW, cfg=_cfg([feed]))
    assert db.rows_to_score(impact.fingerprint(nc.impact_config(), [])) == []
    [row] = bus.cache_get(handlers.CACHE_FEED).payload["items"]
    assert row["impact"] == {"band": "med", "score": 5, "reasons": ["kw:tier1:FOMC"]}
    [pub] = bus.cache_get(handlers.CACHE_PUBLIC).payload["items"]
    assert pub["impact"] == {"band": "med", "score": 5, "reasons": ["kw:tier1:FOMC"]}


def test_a_config_change_rescores_stored_rows(tmp_path, monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    cfg = _impact_config(monkeypatch, [{"name": "Pub", "kind": "rss", "url": "u"}])
    db.insert_many([_it("https://p/1", "FOMC holds rates steady this afternoon")])
    _poll(bus, db)
    assert bus.cache_get(handlers.CACHE_FEED).payload["items"][0]["impact"]["score"] == 5
    cfg["impact"]["keywords"]["tier1"]["points"] = 7              # Settings edits the tier
    _poll(bus, db)
    got = bus.cache_get(handlers.CACHE_FEED).payload["items"][0]["impact"]
    assert (got["score"], got["band"]) == (7, "high")
    stored = db.newest(5)[0]["impact"]
    assert (stored["score"], stored["band"]) == (7, "high")


def test_edgar_kinds_publish_to_sec_and_never_to_feed(tmp_path, monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    _impact_config(monkeypatch, [{"name": "Pub", "kind": "rss", "url": "u"},
                                 {"name": "F4", "kind": "edgar_form4"},
                                 {"name": "S3", "kind": "edgar_filings", "forms": ["S-3"]}],
                   {n: {"public": True} for n in ("Pub", "F4", "S3")})
    db.insert_many([
        _it("https://p/1", "Apple beats on iPhone demand this quarter"),
        _it("https://sec/1", "ACME insider purchase", source="F4", kind="edgar_form4",
            tickers=["ACME"], detail={"total_value": 1_200_000, "relationship": "Director"}),
        _it("https://sec/2", "ACME files S-3", source="S3", kind="edgar_filings",
            tickers=["ACME"], detail={"form": "S-3"}),
    ])
    _poll(bus, db)
    feed = bus.cache_get(handlers.CACHE_FEED).payload["items"]
    sec = bus.cache_get(handlers.CACHE_SEC).payload["items"]
    assert {i["kind"] for i in feed}.isdisjoint(_EDGAR)
    assert sec and {i["kind"] for i in sec} <= _EDGAR
    assert {i["kind"] for i in sec} == _EDGAR
    pub = bus.cache_get(handlers.CACHE_PUBLIC).payload["items"]
    sec_pub = bus.cache_get(handlers.CACHE_SEC_PUBLIC).payload["items"]
    assert {i["kind"] for i in pub}.isdisjoint(_EDGAR) and pub
    assert {i["kind"] for i in sec_pub} == _EDGAR
    f4 = next(i for i in sec if i["kind"] == "edgar_form4")
    assert f4["impact"] == {"band": "med", "score": 4, "reasons": ["form4:$1.2M", "officer"]}


def test_the_sec_window_is_its_own(tmp_path, monkeypatch):
    """A busy filings day cannot eat the headline window, nor the reverse."""
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    _impact_config(monkeypatch, [{"name": "Pub", "kind": "rss", "url": "u"}])
    db.insert_many([_it(f"https://sec/{k}", f"Filing number {k} by some issuer", source="S3",
                        kind="edgar_filings", detail={"form": "424B5"}) for k in range(4)]
                   + [_it("https://p/1", "A headline that is only a headline today",
                          published="2026-09-25T00:00:00+00:00")])
    cfg = {"collector": {"keep_days": 7, "view_items": 1, "sec_view_items": 3,
                         "request_timeout_s": 5, "sec_user_agent": "t"}, "feeds": []}
    compute.run_poll(bus, db, FakeFetch({}), feeds=[], universe=[], now=NOW, cfg=cfg)
    assert len(bus.cache_get(handlers.CACHE_FEED).payload["items"]) == 1
    assert len(bus.cache_get(handlers.CACHE_SEC).payload["items"]) == 3


def test_sec_public_uses_the_current_public_flags(tmp_path, monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    cfg = _impact_config(monkeypatch, [{"name": "F4", "kind": "edgar_form4"},
                                       {"name": "S3", "kind": "edgar_filings", "forms": ["S-3"]}],
                         {"F4": {"public": True}, "S3": {"public": True}})
    db.insert_many([
        _it("https://sec/1", "ACME insider purchase", source="F4", kind="edgar_form4",
            tickers=["ACME"], detail={"total_value": 300_000}),
        _it("https://sec/2", "ACME files S-3", source="S3", kind="edgar_filings",
            tickers=["ACME"], detail={"form": "S-3"}),
    ])
    _poll(bus, db)
    assert {i["source"] for i in bus.cache_get(handlers.CACHE_SEC_PUBLIC).payload["items"]} \
        == {"F4", "S3"}
    cfg["feed_flags"]["S3"] = {"public": False}                     # Settings flips S3
    _poll(bus, db)
    assert {i["source"] for i in bus.cache_get(handlers.CACHE_SEC_PUBLIC).payload["items"]} \
        == {"F4"}
    assert {i["source"] for i in bus.cache_get(handlers.CACHE_SEC).payload["items"]} \
        == {"F4", "S3"}


def test_a_stale_high_publishes_as_med_with_the_reason(tmp_path, monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    _impact_config(monkeypatch, [{"name": "Pub", "kind": "rss", "url": "u"}],
                   {"Pub": {"public": True}})
    db.insert_many([
        _it("https://p/1", "FOMC surprise: Apple beats as markets reel",
            published="2026-09-24T20:00:00+00:00"),                         # 28 h old
        _it("https://p/2", "FOMC minutes: Nvidia beats as chips surge",
            published="2026-09-25T20:00:00+00:00"),                         # 4 h old
    ])
    _poll(bus, db)
    for key in (handlers.CACHE_FEED, handlers.CACHE_PUBLIC):
        rows = _by_title(bus, key)
        old = rows["FOMC surprise: Apple beats as markets reel"]["impact"]
        new = rows["FOMC minutes: Nvidia beats as chips surge"]["impact"]
        assert old == {"band": "med", "score": 8,
                       "reasons": ["kw:tier1:FOMC", "kw:tier2:beats", "stale"]}, key
        assert new == {"band": "high", "score": 8,
                       "reasons": ["kw:tier1:FOMC", "kw:tier2:beats"]}, key
    # stored UNCAPPED: the cap is a function of the publish time
    stored = {r["title"]: r["impact"] for r in db.newest(5)}
    assert stored["FOMC surprise: Apple beats as markets reel"]["band"] == "high"


def test_the_public_view_is_rescored_from_the_public_row(tmp_path, monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    _impact_config(monkeypatch, [{"name": "Pub", "kind": "rss", "url": "u"},
                                 {"name": "Priv", "kind": "rss", "url": "v"}],
                   {"Pub": {"public": True}, "Priv": {"public": False}})
    title = "FOMC holds rates steady this afternoon"
    db.insert_many([_it("https://p/1", title, source="Pub")])
    db.insert_many([_it("https://q/1", title, source="Priv", tickers=["NVDA"])])  # merges
    _poll(bus, db, universe=["NVDA"])
    private = _by_title(bus, handlers.CACHE_FEED)[title]
    public = _by_title(bus, handlers.CACHE_PUBLIC)[title]
    assert private["sources"] == ["Pub", "Priv"]
    assert "source:Priv" in private["impact"]["reasons"]
    assert "sources:2" in private["impact"]["reasons"]
    assert "watchlist" in private["impact"]["reasons"]
    assert public["sources"] == ["Pub"] and public["tickers"] == []
    assert public["impact"] == {"band": "med", "score": 5, "reasons": ["kw:tier1:FOMC"]}
    assert not any("Priv" in r for r in public["impact"]["reasons"])
    # lower by the private feed's points, the multi-source boost and the watchlist tag
    assert private["impact"]["score"] - public["impact"]["score"] == 3 + 1 + 2


def test_views_carry_no_timestamp(tmp_path, monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    _impact_config(monkeypatch, [{"name": "Pub", "kind": "rss", "url": "u"}],
                   {"Pub": {"public": True}})
    db.insert_many([_it("https://p/1", "FOMC holds rates steady this afternoon"),
                    _it("https://sec/1", "ACME files S-3", source="Pub", kind="edgar_filings",
                        detail={"form": "S-3"})])
    _poll(bus, db)
    for key in (handlers.CACHE_FEED, handlers.CACHE_PUBLIC, handlers.CACHE_SEC,
                handlers.CACHE_SEC_PUBLIC):
        assert set(bus.cache_get(key).payload) == {"items"}, key
    before = {k: bus.cache_version(k) for k in (handlers.CACHE_SEC, handlers.CACHE_SEC_PUBLIC)}
    _poll(bus, db, now="2026-09-26T00:05:00+00:00")
    assert {k: bus.cache_version(k) for k in before} == before     # skip_unchanged


def test_a_scoring_failure_degrades_and_still_publishes(tmp_path, monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    _impact_config(monkeypatch, [{"name": "Pub", "kind": "rss", "url": "u"}],
                   {"Pub": {"public": True}})
    db.insert_many([_it("https://p/1", "FOMC holds rates steady this afternoon")])
    _poll(bus, db)                                                  # scored and stored
    db.insert_many([_it("https://p/2", "Apple beats on iPhone demand this quarter")])
    _degrade.reset()
    monkeypatch.setattr(impact, "score", _boom)
    _poll(bus, db)
    rows = _by_title(bus, handlers.CACHE_FEED)
    assert rows["FOMC holds rates steady this afternoon"]["impact"]["score"] == 5   # stored
    assert rows["Apple beats on iPhone demand this quarter"]["impact"] is None      # never scored
    assert bus.cache_get(handlers.CACHE_PUBLIC).payload["items"]
    assert bus.cache_get(handlers.CACHE_SEC) is not None
    assert _degrade.counts()["news.impact"] == 1
    assert _degrade.counts().get("news.impact.public") == 1
    _degrade.reset()
