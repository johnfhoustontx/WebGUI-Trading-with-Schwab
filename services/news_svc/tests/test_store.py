from services.news_svc import items, store

NOW = "2026-09-26T00:00:00+00:00"


def _item(url, title="t", public=True, published="2026-09-25T20:00:00+00:00", tickers=()):
    return items.make_item(source="S", title=title, url=url, published_at=published,
                           public=public, now=NOW, tickers=tickers)


def _src(it, source):
    it["source"] = source
    return it


# ── the plan's tests ────────────────────────────────────────────────────────

def test_insert_is_idempotent_on_id(tmp_path):
    db = store.Store(tmp_path / "n.db")
    assert db.insert_many([_item("https://a/1"), _item("https://a/1?utm_x=2")]) == 1
    assert db.insert_many([_item("https://a/1")]) == 0
    assert len(db.newest(10)) == 1


def test_same_story_from_a_second_feed_merges_sources(tmp_path):
    # The plan used "Apple rallies" / "Apple Rallies!" - two tokens, which
    # title_key refuses (None = never merge by title), so the title is longer.
    db = store.Store(tmp_path / "n.db")
    a = _item("https://a/1", title="Apple rallies on iPhone demand"); a["source"] = "Yahoo Finance"
    b = _item("https://b/2", title="Apple Rallies on iPhone demand!"); b["source"] = "Google"
    db.insert_many([a]); db.insert_many([b])
    rows = db.newest(10)
    assert len(rows) == 1 and rows[0]["sources"] == ["Yahoo Finance", "Google"]


def test_newest_is_ordered_and_public_filter_is_by_flag(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_item("https://a/old", published="2026-09-25T10:00:00+00:00", public=False),
                    _item("https://a/new", published="2026-09-25T20:00:00+00:00")])
    assert [r["url"] for r in db.newest(10)] == ["https://a/new", "https://a/old"]
    assert [r["url"] for r in db.newest(10, public_only=True)] == ["https://a/new"]


def test_tickers_round_trip_and_prune(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_item("https://a/1", tickers=["NVDA"], published="2026-09-01T00:00:00+00:00")])
    assert db.newest(10)[0]["tickers"] == ["NVDA"]
    assert db.prune(keep_days=7, now=NOW) == 1
    assert db.newest(10) == []


def test_feed_state_round_trips(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.set_feed_state("MarketWatch", etag="abc", last_ok=NOW, error=None)
    assert db.feed_state("MarketWatch")["etag"] == "abc"
    db.set_feed_state("MarketWatch", error="boom")
    st = db.feed_state("MarketWatch")
    assert st["etag"] == "abc" and st["error"] == "boom"


def test_seen_accessions(tmp_path):
    db = store.Store(tmp_path / "n.db")
    assert db.unseen_accessions(["a", "b"]) == ["a", "b"]
    db.mark_accessions(["a"])
    assert db.unseen_accessions(["a", "b"]) == ["b"]


# ── 1/2: a title too short to key is stored, and never merged ──────────────

def test_a_short_title_with_no_key_is_stored(tmp_path):
    db = store.Store(tmp_path / "n.db")
    assert items.title_key("Stocks rise") is None
    assert db.insert_many([_item("https://a/1", title="Stocks rise")]) == 1
    assert [r["title"] for r in db.newest(10)] == ["Stocks rise"]


def test_short_titles_from_two_feeds_are_not_merged(tmp_path):
    db = store.Store(tmp_path / "n.db")
    a = _src(_item("https://a/1", title="Stocks rise"), "Yahoo Finance")
    b = _src(_item("https://b/2", title="Stocks rise"), "Google")
    assert db.insert_many([a, b]) == 2
    assert len(db.newest(10)) == 2


# ── 3: title merge only across DIFFERENT sources ───────────────────────────

def test_two_stories_from_the_same_feed_with_one_title_are_both_kept(tmp_path):
    db = store.Store(tmp_path / "n.db")
    mon = _src(_item("https://r/mon", title="Morning Bid: markets wait",
                     published="2026-09-25T06:00:00+00:00"), "Reuters")
    tue = _src(_item("https://r/tue", title="Morning Bid: Markets wait!",
                     published="2026-09-25T20:00:00+00:00"), "Reuters")
    assert db.insert_many([mon]) == 1
    assert db.insert_many([tue]) == 1
    rows = db.newest(10)
    assert [r["url"] for r in rows] == ["https://r/tue", "https://r/mon"]
    assert all(r["sources"] == ["Reuters"] for r in rows)


def test_a_feed_already_merged_into_a_row_does_not_merge_its_next_story(tmp_path):
    db = store.Store(tmp_path / "n.db")
    y = _src(_item("https://y/1", title="Morning Bid: markets wait",
                   published="2026-09-25T06:00:00+00:00"), "Yahoo Finance")
    g1 = _src(_item("https://g/1", title="Morning Bid: markets wait",
                    published="2026-09-25T06:05:00+00:00"), "Google")
    g2 = _src(_item("https://g/2", title="Morning Bid: markets wait",
                    published="2026-09-25T20:00:00+00:00"), "Google")
    assert db.insert_many([y, g1]) == 1
    assert db.insert_many([g2]) == 1
    assert len(db.newest(10)) == 2


# ── 4: tickers union on an id or title match ───────────────────────────────

def test_same_id_on_two_symbol_feeds_unions_tickers(tmp_path):
    db = store.Store(tmp_path / "n.db")
    nvda = _item("https://y/story", title="Chip stocks jump on demand", tickers=["NVDA"])
    amd = _item("https://y/story", title="Chip stocks jump on demand", tickers=["AMD", "NVDA"])
    assert db.insert_many([nvda]) == 1
    assert db.insert_many([amd]) == 0
    rows = db.newest(10)
    assert len(rows) == 1
    assert rows[0]["tickers"] == ["NVDA", "AMD"]
    assert rows[0]["sources"] == ["S"]


def test_same_id_within_one_batch_unions_tickers(tmp_path):
    db = store.Store(tmp_path / "n.db")
    assert db.insert_many([_item("https://y/s", tickers=["NVDA"]),
                           _item("https://y/s", tickers=["AMD"])]) == 1
    assert db.newest(10)[0]["tickers"] == ["NVDA", "AMD"]


def test_title_merge_unions_tickers_and_adds_the_source_once(tmp_path):
    db = store.Store(tmp_path / "n.db")
    a = _src(_item("https://a/1", title="Apple rallies on iPhone demand", tickers=["AAPL"]),
             "Yahoo Finance")
    b = _src(_item("https://b/2", title="Apple rallies on iPhone demand", tickers=["QQQ", "AAPL"]),
             "Google")
    c = _src(_item("https://c/3", title="Apple rallies on iPhone demand", tickers=["SPY"]),
             "Reuters")
    again = _src(_item("https://b/2", title="Apple rallies on iPhone demand", tickers=["MSFT"]),
                 "Google")
    assert db.insert_many([a]) == 1
    assert db.insert_many([b]) == 0
    assert db.insert_many([c]) == 0
    assert db.insert_many([again]) == 0
    rows = db.newest(10)
    assert len(rows) == 1
    assert rows[0]["tickers"] == ["AAPL", "QQQ", "SPY", "MSFT"]
    assert rows[0]["sources"] == ["Yahoo Finance", "Google", "Reuters"]


def test_title_merge_needs_the_stories_within_a_day(tmp_path):
    db = store.Store(tmp_path / "n.db")
    a = _src(_item("https://a/1", title="Apple rallies on iPhone demand",
                   published="2026-09-20T20:00:00+00:00"), "Yahoo Finance")
    b = _src(_item("https://b/2", title="Apple rallies on iPhone demand"), "Google")
    assert db.insert_many([a, b]) == 2


# ── 5: newest(sources=...) ─────────────────────────────────────────────────

def test_newest_sources_filter_hides_a_feed_no_longer_public(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([
        _src(_item("https://x/1", published="2026-09-25T22:00:00+00:00"), "WasPublic"),
        _src(_item("https://m/1", published="2026-09-25T21:00:00+00:00"), "MarketWatch"),
        _src(_item("https://m/2", published="2026-09-25T20:00:00+00:00"), "MarketWatch"),
    ])
    assert [r["url"] for r in db.newest(10, public_only=True)][0] == "https://x/1"
    got = db.newest(1, public_only=True, sources={"MarketWatch"})
    assert [r["url"] for r in got] == ["https://m/1"]          # limit AFTER the filter
    got = db.newest(10, public_only=True, sources=["MarketWatch"])
    assert [r["url"] for r in got] == ["https://m/1", "https://m/2"]


def test_newest_with_an_empty_sources_collection_is_empty(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_item("https://a/1")])
    assert db.newest(10, sources=[]) == []
    assert db.newest(10, sources=set()) == []
    assert len(db.newest(10, sources=None)) == 1


# ── 6: newest_for_ticker ───────────────────────────────────────────────────

def test_newest_for_ticker_filters_by_ticker_and_flags(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([
        _src(_item("https://a/1", tickers=["NVDA", "AMD"], published="2026-09-25T22:00:00+00:00"), "Y"),
        _src(_item("https://a/2", tickers=["AMD"], published="2026-09-25T21:00:00+00:00",
                   public=False), "Y"),
        _src(_item("https://a/3", tickers=["NVDAX"], published="2026-09-25T20:00:00+00:00"), "Y"),
        _src(_item("https://a/4", tickers=["NVDA"], published="2026-09-25T19:00:00+00:00"), "G"),
    ])
    assert [r["url"] for r in db.newest_for_ticker("NVDA", 10)] == ["https://a/1", "https://a/4"]
    assert [r["url"] for r in db.newest_for_ticker("AMD", 10)] == ["https://a/1", "https://a/2"]
    assert [r["url"] for r in db.newest_for_ticker("AMD", 10, public_only=True)] == ["https://a/1"]
    assert [r["url"] for r in db.newest_for_ticker("NVDA", 10, sources={"G"})] == ["https://a/4"]
    assert db.newest_for_ticker("NVDA", 10, sources=[]) == []
    assert [r["url"] for r in db.newest_for_ticker("NVDA", 1)] == ["https://a/1"]


# ── 7: storage, path and ordering ──────────────────────────────────────────

def test_wal_and_busy_timeout(tmp_path):
    db = store.Store(tmp_path / "n.db")
    assert db._c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db._c.execute("PRAGMA busy_timeout").fetchone()[0] > 0


def test_db_path_none_resolves_news_db_at_call_time(tmp_path, monkeypatch):
    import repo_paths
    target = tmp_path / "sub" / "late.db"
    monkeypatch.setattr(repo_paths, "NEWS_DB", target)
    store.Store()
    assert target.exists()


def test_a_str_path_is_accepted(tmp_path):
    store.Store(str(tmp_path / "deep" / "n.db"))
    assert (tmp_path / "deep" / "n.db").exists()


def test_same_instant_orders_by_first_seen_desc(tmp_path):
    db = store.Store(tmp_path / "n.db")
    a = _item("https://a/1"); a["first_seen"] = "2026-09-25T20:01:00+00:00"
    b = _item("https://a/2"); b["first_seen"] = "2026-09-25T20:05:00+00:00"
    db.insert_many([a, b])
    assert [r["url"] for r in db.newest(10)] == ["https://a/2", "https://a/1"]


def test_fractional_seconds_order_by_instant_not_by_string(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_item("https://a/frac", published="2026-09-25T20:00:00.500000+00:00"),
                    _item("https://a/later", published="2026-09-25T20:00:01+00:00")])
    assert [r["url"] for r in db.newest(10)] == ["https://a/later", "https://a/frac"]


# ── 8: prune by published_at OR first_seen ─────────────────────────────────

def test_prune_removes_an_unparseable_published_at_once_first_seen_is_old(tmp_path):
    db = store.Store(tmp_path / "n.db")
    old = _item("https://a/old", published="not a date"); old["first_seen"] = "2026-09-01T00:00:00+00:00"
    fresh = _item("https://a/fresh", published="not a date")
    keep = _item("https://a/keep")
    db.insert_many([old, fresh, keep])
    assert db.prune(keep_days=7, now=NOW) == 1
    assert sorted(r["url"] for r in db.newest(10)) == ["https://a/fresh", "https://a/keep"]


def test_prune_removes_a_row_first_seen_long_ago_even_with_a_future_date(tmp_path):
    db = store.Store(tmp_path / "n.db")
    it = _item("https://a/1", published="2027-01-01T00:00:00+00:00")
    it["first_seen"] = "2026-09-01T00:00:00+00:00"
    db.insert_many([it])
    assert db.prune(keep_days=7, now=NOW) == 1


def test_prune_drops_old_accessions(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.mark_accessions(["old"], now="2026-09-01T00:00:00+00:00")
    db.mark_accessions(["new"], now=NOW)
    db.prune(keep_days=7, now=NOW)
    assert db.unseen_accessions(["old", "new"]) == ["old"]


# ── 9: JSON round trip ─────────────────────────────────────────────────────

def test_everything_round_trips_including_non_ascii_detail(tmp_path):
    db = store.Store(tmp_path / "n.db")
    it = items.make_item(source="Reuters", title="Nestlé cuts guidance on cocoa — ☕",
                         url="https://a/n", published_at="2026-09-25T20:00:00+00:00",
                         public=False, now=NOW, kind="edgar_form4",
                         original_source="Reuters Wire", teaser="Zürich 日本",
                         tickers=["NSRGY"], topics=["Earnings", "Guidance"],
                         detail={"note": "café 株", "n": [1, 2.5, None], "ok": True})
    db.insert_many([it])
    row = db.newest(10)[0]
    expected = dict(it, sources=["Reuters"])
    assert row == expected
    assert set(row) == set(items.FIELDS) | {"sources"}


def test_a_title_merged_item_repolled_next_cycle_is_not_a_new_row(tmp_path):
    db = store.Store(tmp_path / "n.db")
    a = _src(_item("https://a/1", title="Apple rallies on iPhone demand"), "Yahoo Finance")
    b = _src(_item("https://b/2", title="Apple rallies on iPhone demand"), "Google")
    assert db.insert_many([a, b]) == 1
    for _ in range(3):
        assert db.insert_many([_src(_item("https://b/2", title="Apple rallies on iPhone demand"),
                                    "Google")]) == 0
    assert len(db.newest(10)) == 1


def test_prune_forgets_aliases_of_pruned_rows(tmp_path):
    db = store.Store(tmp_path / "n.db")
    a = _src(_item("https://a/1", title="Apple rallies on iPhone demand",
                   published="2026-09-01T00:00:00+00:00"), "Yahoo Finance")
    b = _src(_item("https://b/2", title="Apple rallies on iPhone demand",
                   published="2026-09-01T00:00:00+00:00"), "Google")
    db.insert_many([a, b])
    assert db.prune(keep_days=7, now=NOW) == 1
    assert db._c.execute("SELECT COUNT(*) FROM aliases").fetchone()[0] == 0


# ── review of 51807d7: 1. a failed batch writes NOTHING ────────────────────

def test_a_failed_batch_writes_nothing_and_leaves_no_open_transaction(tmp_path):
    db = store.Store(tmp_path / "n.db")
    good = _item("https://a/good")
    bad = _item("https://a/bad")
    bad["detail"] = {"x": object()}                      # json.dumps raises mid-batch
    try:
        db.insert_many([good, bad])
    except TypeError:
        pass
    else:
        raise AssertionError("the unserialisable detail should have raised")
    assert db._c.in_transaction is False
    db.set_feed_state("Yahoo Finance", error="boom")     # the caller's except path commits
    assert db.newest(10) == []
    other = store.Store(tmp_path / "n.db")
    other._c.execute("PRAGMA busy_timeout=200")          # a held lock fails fast, not in 10 s
    other.set_feed_state("Google", error=None)           # would raise "database is locked"
    assert other.insert_many([_item("https://a/other")]) == 1
    assert [r["url"] for r in db.newest(10)] == ["https://a/other"]


def test_the_other_writers_roll_back_on_failure(tmp_path):
    db = store.Store(tmp_path / "n.db")
    for call in (lambda: db.mark_accessions(["a", object()]),
                 lambda: db.set_feed_state("F", etag=object()),
                 lambda: db.prune(keep_days=object(), now=NOW)):
        try:
            call()
        except Exception:
            pass
        else:
            raise AssertionError("expected a failure")
        assert db._c.in_transaction is False
    assert db.unseen_accessions(["a"]) == ["a"]
    assert db.all_feed_states() == []


# ── 2. the public view reads CURRENT feed flags ────────────────────────────

_STORY = "Apple rallies on iPhone demand"


def test_public_view_hides_a_row_whose_stored_text_came_from_a_non_public_feed(tmp_path):
    # Coordinator decision (fail closed): the stored url / title / teaser /
    # original_source are the PRIMARY feed's, so a row first stored by a
    # non-public feed never enters the public view - even once a public feed
    # carries the same story. Accepted loss.
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_src(_item("https://p/1", title=_STORY, public=False), "Private")])
    db.insert_many([_src(_item("https://y/1", title=_STORY), "Yahoo Finance")])
    assert db.newest(10, public_sources={"Yahoo Finance"}) == []
    rows = db.newest(10, public_sources={"Private", "Yahoo Finance"})   # once Private is public
    assert len(rows) == 1
    assert rows[0]["source"] == "Private"
    assert rows[0]["sources"] == ["Private", "Yahoo Finance"]
    assert rows[0]["public"] is True
    assert db.newest(10)[0]["sources"] == ["Private", "Yahoo Finance"]   # owner view intact


def test_public_view_never_names_a_non_public_feed(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_src(_item("https://y/1", title=_STORY), "Yahoo Finance")])
    db.insert_many([_src(_item("https://p/1", title=_STORY, public=False), "Private")])
    db.insert_many([_src(_item("https://g/1", title=_STORY), "Google")])
    rows = db.newest(10, public_sources=["Google", "Yahoo Finance"])
    assert [(r["source"], r["sources"]) for r in rows] == [
        ("Yahoo Finance", ["Yahoo Finance", "Google"])]


def test_public_view_drops_a_feed_turned_non_public(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_src(_item("https://m/1", published="2026-09-25T21:00:00+00:00"), "MarketWatch"),
                    _src(_item("https://y/1", published="2026-09-25T20:00:00+00:00"), "Yahoo Finance")])
    assert len(db.newest(10, public_sources={"MarketWatch", "Yahoo Finance"})) == 2
    got = db.newest(10, public_sources={"Yahoo Finance"})           # MarketWatch flipped off
    assert [r["url"] for r in got] == ["https://y/1"]
    assert db.newest(10, public_sources=set()) == []
    assert db.newest(10, public_sources=[]) == []


def test_public_view_limit_applies_after_the_filter(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_src(_item("https://p/1", published="2026-09-25T22:00:00+00:00"), "Private"),
                    _src(_item("https://y/1", published="2026-09-25T21:00:00+00:00"), "Yahoo Finance"),
                    _src(_item("https://y/2", published="2026-09-25T20:00:00+00:00"), "Yahoo Finance")])
    got = db.newest(1, public_sources={"Yahoo Finance"})
    assert [r["url"] for r in got] == ["https://y/1"]


def test_public_view_for_ticker(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_src(_item("https://p/1", title=_STORY, tickers=["AAPL"], public=False),
                         "Private")])
    db.insert_many([_src(_item("https://y/1", title=_STORY, tickers=["QQQ"]), "Yahoo Finance")])
    db.insert_many([_src(_item("https://m/1", tickers=["AAPL"], published="2026-09-25T19:00:00+00:00"),
                         "MarketWatch")])
    # the merged row's url / title are Private's, so it stays out of the public view
    assert db.newest_for_ticker("AAPL", 10, public_sources={"Yahoo Finance"}) == []
    rows = db.newest_for_ticker("AAPL", 10, public_sources={"Yahoo Finance", "MarketWatch"})
    assert [(r["url"], r["source"], r["sources"]) for r in rows] == [
        ("https://m/1", "MarketWatch", ["MarketWatch"])]
    y = _src(_item("https://y/2", title="Nvidia beats on data center sales", tickers=["NVDA"]),
             "Yahoo Finance")
    p2 = _src(_item("https://p/2", title="Nvidia beats on data center sales", tickers=["AAPL"],
                    public=False), "Private")
    db.insert_many([y]); db.insert_many([p2])
    rows = db.newest_for_ticker("AAPL", 10, public_sources={"Yahoo Finance"})
    assert [(r["url"], r["source"], r["sources"]) for r in rows] == [
        ("https://y/2", "Yahoo Finance", ["Yahoo Finance"])]
    assert db.newest_for_ticker("AAPL", 10, public_sources={"Nobody"}) == []
    assert db.newest_for_ticker("AAPL", 10, public_sources=()) == []


def test_a_merge_sets_the_public_column_to_existing_or_incoming(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_src(_item("https://p/1", title=_STORY, public=False), "Private")])
    db.insert_many([_src(_item("https://y/1", title=_STORY, public=True), "Yahoo Finance")])
    db.insert_many([_src(_item("https://q/1", title=_STORY, public=False), "Private2")])
    assert db.newest(10)[0]["public"] is True
    assert len(db.newest(10, public_only=True)) == 1
    db.insert_many([_item("https://s/1", public=False), _item("https://s/1", public=False)])
    assert [r["public"] for r in db.newest(10) if r["url"] == "https://s/1"] == [False]


# ── 3. write batches take the write lock BEFORE they look anything up ──────

def test_write_batches_open_with_begin_immediate(tmp_path):
    db = store.Store(tmp_path / "n.db")
    seen = []
    db._c.set_trace_callback(seen.append)
    db.insert_many([_src(_item("https://y/1", title=_STORY), "Yahoo Finance")])
    first_read = next(i for i, s in enumerate(seen) if s.lstrip().upper().startswith("SELECT"))
    begins = [i for i, s in enumerate(seen) if s.strip().upper().startswith("BEGIN")]
    assert begins and seen[begins[0]].strip().upper() == "BEGIN IMMEDIATE"
    assert begins[0] < first_read


def test_two_stores_racing_on_one_title_make_one_row(tmp_path):
    import threading
    import time
    a = store.Store(tmp_path / "n.db")
    b = store.Store(tmp_path / "n.db")
    looked, release = threading.Event(), threading.Event()
    real = a._title_match

    def slow_match(it, key):                 # A has looked and found nothing - now B tries
        row = real(it, key)
        looked.set()
        release.wait(5)
        return row

    a._title_match = slow_match
    errors = []

    def run(db, it):
        try:
            db.insert_many([it])
        except Exception as exc:             # noqa: BLE001 - surfaced by the assert below
            errors.append(exc)

    ta = threading.Thread(target=run, args=(a, _src(_item("https://y/1", title=_STORY), "Yahoo Finance")))
    tb = threading.Thread(target=run, args=(b, _src(_item("https://g/1", title=_STORY), "Google")))
    ta.start()
    assert looked.wait(5)
    tb.start()
    time.sleep(0.3)                          # B is now waiting on A's write lock
    release.set()
    ta.join(10); tb.join(10)
    assert errors == []
    rows = a.newest(10)
    assert len(rows) == 1 and rows[0]["sources"] == ["Yahoo Finance", "Google"]


# ── 4. an item already past the keep window is not inserted ────────────────

def test_min_published_skips_stale_items_and_does_not_count_them(tmp_path):
    db = store.Store(tmp_path / "n.db")
    cutoff = "2026-09-19T00:00:00+00:00"
    got = db.insert_many([_item("https://a/stale", published="2026-09-01T00:00:00+00:00"),
                          _item("https://a/fresh", published="2026-09-25T00:00:00+00:00"),
                          _item("https://a/zulu", published="2026-09-18T23:59:59Z"),
                          _item("https://a/odd", published="not a date")],
                         min_published=cutoff)
    assert got == 2
    assert sorted(r["url"] for r in db.newest(10)) == ["https://a/fresh", "https://a/odd"]
    assert db.insert_many([_item("https://a/stale", published="2026-09-01T00:00:00+00:00")],
                          min_published=cutoff) == 0
    assert db.insert_many([_item("https://a/stale", published="2026-09-01T00:00:00+00:00")]) == 1


# ── 5. one instant written two ways ties on first_seen ─────────────────────

def test_same_instant_as_z_and_offset_tie_breaks_by_first_seen(tmp_path):
    db = store.Store(tmp_path / "n.db")
    z = _item("https://a/z"); z["published_at"] = "2026-09-25T20:00:00Z"
    z["first_seen"] = "2026-09-25T20:01:00+00:00"
    off = _item("https://a/off"); off["published_at"] = "2026-09-25T20:00:00+00:00"
    off["first_seen"] = "2026-09-25T20:05:00+00:00"
    db.insert_many([z, off])
    assert [r["url"] for r in db.newest(10)] == ["https://a/off", "https://a/z"]


# ── 6. a bare str is one name, not its characters ──────────────────────────

def test_a_bare_str_names_one_feed(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_src(_item("https://g/1", tickers=["NVDA"]), "G"),
                    _src(_item("https://x/1", tickers=["NVDA"],
                               published="2026-09-25T19:00:00+00:00"), "GY")])
    assert [r["url"] for r in db.newest(10, sources="G")] == ["https://g/1"]
    assert [r["url"] for r in db.newest(10, public_sources="GY")] == ["https://x/1"]
    assert [r["url"] for r in db.newest_for_ticker("NVDA", 10, sources="GY")] == ["https://x/1"]
    assert db.newest(10, sources="Y") == []


# ── 7. nits ────────────────────────────────────────────────────────────────

def test_newest_is_served_by_the_published_expression_index(tmp_path):
    db = store.Store(tmp_path / "n.db")
    names = {r[0] for r in db._c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_items_published" not in names
    plan = " ".join(r[3] for r in db._c.execute(
        f"EXPLAIN QUERY PLAN SELECT * FROM items {store._ORDER} LIMIT 5"))
    assert "USING INDEX idx_items_published_jd" in plan


def test_an_old_plain_published_index_is_dropped(tmp_path):
    import sqlite3
    c = sqlite3.connect(str(tmp_path / "n.db"))
    c.executescript(store.SCHEMA)
    c.execute("CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at)")
    c.commit(); c.close()
    db = store.Store(tmp_path / "n.db")
    names = {r[0] for r in db._c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_items_published" not in names


def test_unseen_accessions_queries_only_the_batch_in_chunks(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.mark_accessions([f"x{i}" for i in range(50)] + ["a7", "a999"])
    batch = [f"a{i}" for i in range(1200)] + ["a7"]
    seen = []
    db._c.set_trace_callback(seen.append)
    got = db.unseen_accessions(batch)
    assert got == [a for a in batch if a not in ("a7", "a999")]
    selects = [s for s in seen if "seen_accessions" in s]
    assert selects and all(" IN (" in s for s in selects)
    assert all(s.count("?") <= 500 or s.count("'") // 2 <= 500 for s in selects)
    assert len(selects) == 3                               # 1201 names, 500 a query
    assert db.unseen_accessions([]) == []


def test_close_and_context_manager(tmp_path):
    import sqlite3
    with store.Store(tmp_path / "n.db") as db:
        assert db.insert_many([_item("https://a/1")]) == 1
    try:
        db.newest(1)
    except sqlite3.ProgrammingError:
        pass
    else:
        raise AssertionError("a closed store should refuse")
    db2 = store.Store(tmp_path / "n.db")
    assert len(db2.newest(10)) == 1
    db2.close()
    db2.close()                                           # idempotent


def test_one_busy_timeout(tmp_path):
    db = store.Store(tmp_path / "n.db")
    assert db._c.execute("PRAGMA busy_timeout").fetchone()[0] == store._BUSY_TIMEOUT_MS


# ── review of 2af76d2/df28bb7: 1. a literal "now" date ─────────────────────

def test_a_literal_now_published_at_is_stored_as_first_seen(tmp_path):
    # julianday('now') is non-deterministic, so the expression index refuses the
    # row and the WHOLE batch rolled back - every cycle, for good.
    db = store.Store(tmp_path / "n.db")
    batch = []
    for i, raw in enumerate(("now", "NOW", " Now ")):
        it = _item(f"https://a/now{i}", published=raw)
        it["first_seen"] = f"2026-09-25T2{i}:00:00+00:00"
        batch.append(it)
    keep = _item("https://a/keep", published="2026-09-25T19:00:00+00:00")
    assert db.insert_many(batch + [keep]) == 4
    rows = db.newest(10)
    assert [(r["url"], r["published_at"]) for r in rows] == [
        ("https://a/now2", "2026-09-25T22:00:00+00:00"),
        ("https://a/now1", "2026-09-25T21:00:00+00:00"),
        ("https://a/now0", "2026-09-25T20:00:00+00:00"),
        ("https://a/keep", "2026-09-25T19:00:00+00:00")]
    assert batch[0]["published_at"] == "now"               # the caller's dict is untouched
    assert db._c.in_transaction is False


def test_a_literal_now_is_stale_and_merged_by_its_first_seen(tmp_path):
    db = store.Store(tmp_path / "n.db")
    old = _item("https://a/old", published="now")
    old["first_seen"] = "2026-09-01T00:00:00+00:00"
    assert db.insert_many([old], min_published="2026-09-19T00:00:00+00:00") == 0
    y = _src(_item("https://y/1", title=_STORY, published="NOW"), "Yahoo Finance")
    y["first_seen"] = "2026-09-25T20:00:00+00:00"
    g = _src(_item("https://g/1", title=_STORY, published="2026-09-25T20:30:00+00:00"), "Google")
    assert db.insert_many([y, g]) == 1
    assert db.newest(10)[0]["sources"] == ["Yahoo Finance", "Google"]
    assert db.prune(keep_days=7, now=NOW) == 0


def test_a_now_first_seen_as_well_falls_back_to_the_unparseable_sentinel(tmp_path):
    db = store.Store(tmp_path / "n.db")
    it = _item("https://a/both", published="now")
    it["first_seen"] = "now"
    dated = _item("https://a/dated")
    assert db.insert_many([it, dated]) == 2
    rows = db.newest(10)
    assert [r["url"] for r in rows] == ["https://a/dated", "https://a/both"]   # sorts last
    assert rows[1]["published_at"] == store._UNDATED


# ── 2. a failed COMMIT rolls back and leaves no open transaction ───────────

class _CommitFailsOnce:
    """Delegates to a real connection; its first commit() raises WITHOUT committing."""

    def __init__(self, conn):
        self._conn = conn
        self.failed = False

    def commit(self):
        if not self.failed:
            self.failed = True
            import sqlite3
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_failed_commit_rolls_back_and_the_next_write_succeeds(tmp_path):
    import sqlite3
    db = store.Store(tmp_path / "n.db")
    real = db._c
    db._c = _CommitFailsOnce(real)
    try:
        db.insert_many([_item("https://a/lost")])
    except sqlite3.OperationalError as exc:
        assert "disk I/O" in str(exc)
    else:
        raise AssertionError("the failed commit should have re-raised")
    assert db._c.failed is True
    assert real.in_transaction is False
    assert db.insert_many([_item("https://a/next")]) == 1          # BEGIN IMMEDIATE works again
    assert real.in_transaction is False
    db._c = real
    assert [r["url"] for r in db.newest(10)] == ["https://a/next"]  # the failed batch is gone
    other = store.Store(tmp_path / "n.db")
    other._c.execute("PRAGMA busy_timeout=200")
    assert other.insert_many([_item("https://a/other")]) == 1       # no lock left behind


def test_a_failed_commit_in_the_other_writers_rolls_back_too(tmp_path):
    import sqlite3
    db = store.Store(tmp_path / "n.db")
    real = db._c
    for call in (lambda: db.mark_accessions(["a"]),
                 lambda: db.set_feed_state("F", etag="x"),
                 lambda: db.prune(keep_days=7, now=NOW)):
        db._c = _CommitFailsOnce(real)
        try:
            call()
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("expected the commit failure")
        assert real.in_transaction is False
    db._c = real
    assert db.unseen_accessions(["a"]) == ["a"]
    assert db.all_feed_states() == []


# ── 3. a limit that is not a positive int returns nothing ──────────────────

def test_a_non_positive_or_non_int_limit_returns_nothing(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_item("https://a/1", tickers=["NVDA"]), _item("https://a/2", tickers=["NVDA"])])
    for bad in (0, -1, -5, None, "5", 2.5, 1.0, True, False):
        assert db.newest(bad) == [], bad
        assert db.newest_for_ticker("NVDA", bad) == [], bad
        assert db.newest(bad, public_sources={"S"}) == [], bad
    assert len(db.newest(1)) == 1
    assert len(db.newest_for_ticker("NVDA", 2)) == 2


# ── review of b125120: 1. every unparseable published_at is normalised ─────

_UNUSABLE_DATES = ("now", "subsec", "SUBSECOND", " Subsec ", "garbage", "",
                   "2026-09-25T20:00:00",          # naive: no zone, not an instant
                   "20260925T200000Z")             # aware, but julianday() cannot read it


def test_every_unusable_published_at_becomes_first_seen(tmp_path):
    # "subsec" / "subsecond" read as the clock in julianday() (SQLite >= 3.42),
    # so the expression index refused them and rolled the whole batch back.
    db = store.Store(tmp_path / "n.db")
    batch = []
    for i, raw in enumerate(_UNUSABLE_DATES):
        it = _item(f"https://a/{i}", published=raw)
        it["first_seen"] = f"2026-09-25T1{i}:00:00+00:00"
        batch.append(it)
    good = _item("https://a/good", published="2026-09-25T20:00:00.5+00:00")
    assert db.insert_many(batch + [good]) == len(batch) + 1
    got = {r["url"]: r["published_at"] for r in db.newest(20)}
    for i, _ in enumerate(_UNUSABLE_DATES):
        assert got[f"https://a/{i}"] == f"2026-09-25T1{i}:00:00+00:00", _UNUSABLE_DATES[i]
    assert got["https://a/good"] == "2026-09-25T20:00:00.5+00:00"      # kept verbatim
    assert batch[1]["published_at"] == "subsec"                           # caller's dict untouched
    assert db._c.in_transaction is False


def test_an_unusable_published_at_with_an_unusable_first_seen_is_undated(tmp_path):
    db = store.Store(tmp_path / "n.db")
    batch = []
    for i, raw in enumerate(_UNUSABLE_DATES):
        it = _item(f"https://a/{i}", published=raw)
        it["first_seen"] = _UNUSABLE_DATES[(i + 1) % len(_UNUSABLE_DATES)]
        batch.append(it)
    assert db.insert_many(batch + [_item("https://a/dated")]) == len(batch) + 1
    rows = db.newest(20)
    assert rows[0]["url"] == "https://a/dated"
    assert all(r["published_at"] == store._UNDATED for r in rows[1:])


def test_a_non_str_published_at_is_normalised_too(tmp_path):
    db = store.Store(tmp_path / "n.db")
    it = _item("https://a/1"); it["published_at"] = None
    it["first_seen"] = "2026-09-25T20:00:00+00:00"
    assert db.insert_many([it]) == 1
    assert db.newest(10)[0]["published_at"] == "2026-09-25T20:00:00+00:00"


# ── 2. an unusable first_seen is our clock, so the row still ages out ──────

def test_an_unusable_first_seen_is_stored_as_the_current_utc_time(tmp_path, monkeypatch):
    import datetime as dt
    db = store.Store(tmp_path / "n.db")
    before = dt.datetime.now(dt.timezone.utc)
    batch = []
    for i, raw in enumerate(_UNUSABLE_DATES + (None,)):
        it = _item(f"https://a/{i}"); it["first_seen"] = raw
        batch.append(it)
    db.insert_many(batch)
    after = dt.datetime.now(dt.timezone.utc)
    for r in db.newest(20):
        seen = dt.datetime.fromisoformat(r["first_seen"])
        assert seen.tzinfo is not None and before <= seen <= after, r["url"]


def test_a_row_with_an_unusable_first_seen_is_pruned_once_old(tmp_path, monkeypatch):
    # julianday('now') reads the clock, so a stored first_seen of "now" was
    # never older than any cutoff: the row could never be pruned.
    db = store.Store(tmp_path / "n.db")
    monkeypatch.setattr(store, "_utcnow", lambda: "2026-09-01T00:00:00+00:00")
    rows = []
    for i, raw in enumerate(_UNUSABLE_DATES):
        both = _item(f"https://a/both{i}", published=raw); both["first_seen"] = raw
        future = _item(f"https://a/future{i}", published="2027-01-01T00:00:00+00:00")
        future["first_seen"] = raw
        rows += [both, future]
    db.insert_many(rows)
    assert all(r["first_seen"] == "2026-09-01T00:00:00+00:00" for r in db.newest(50))
    assert db.prune(keep_days=7, now=NOW) == len(rows)
    assert db.newest(50) == []


# ── 3. a failed rollback leaves a trace ────────────────────────────────────

class _RollbackFails:
    """Delegates to a real connection; rollback() raises WITHOUT rolling back."""

    def __init__(self, conn):
        self._conn = conn

    def rollback(self):
        import sqlite3
        raise sqlite3.OperationalError("rollback exploded")

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_failed_rollback_is_logged_and_the_original_error_still_raised(tmp_path, caplog):
    import logging
    db = store.Store(tmp_path / "n.db")
    real = db._c
    db._c = _RollbackFails(real)
    bad = _item("https://a/bad"); bad["detail"] = {"x": object()}
    with caplog.at_level(logging.WARNING, logger=store.__name__):
        try:
            db.insert_many([bad])
        except TypeError:
            pass
        else:
            raise AssertionError("the original error must be the one raised")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    failed = [r for r in warnings if r.exc_info and "rollback exploded" in str(r.exc_info[1])]
    assert len(failed) == 1
    wedged = [r for r in warnings if "transaction" in r.getMessage() and not r.exc_info]
    assert len(wedged) == 1 and real.in_transaction is True
    real.rollback()
    db._c = real
    assert db.insert_many([_item("https://a/next")]) == 1


def test_a_successful_rollback_logs_nothing(tmp_path, caplog):
    import logging
    db = store.Store(tmp_path / "n.db")
    bad = _item("https://a/bad"); bad["detail"] = {"x": object()}
    with caplog.at_level(logging.WARNING, logger=store.__name__):
        try:
            db.insert_many([bad])
        except TypeError:
            pass
    assert [r for r in caplog.records if r.name == store.__name__] == []
