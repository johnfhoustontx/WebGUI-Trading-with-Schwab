"""Port of D:\\X-Posts\\test\\poller.test.js (26 tests, same data and expectations) plus
the Python-only Poller tests (catch-up after start, add vs. a held lock, interval floor).

``StubFx`` is the Node ``stubFx``: page i is returned for cursor ``c{i}``; ``status`` is
"ok", "not_found", an error string, or a callable ``(n, cursor, with_replies)`` that may
return an override response (None = serve the page).
"""
import itertools
import threading

from services.xfeed_svc import config
from services.xfeed_svc.poller import (
    Poller,
    assign_sort_times,
    backoff_ms,
    poll_account,
    take_baseline,
)
from services.xfeed_svc.store import Store

MIN = 60_000
CFG = dict(config.DEFAULTS["poller"])      # interval_s 60 -> the Node tests' INTERVAL = MIN


def no_sleep(_s):
    return None


_seq = itertools.count(5000)


def st(id=None, author="acct", created=0, repost=False, reply_to=None):
    sid = id if id is not None else str(next(_seq))
    return {
        "id": sid,
        "url": f"https://x.com/{author}/status/{sid}",
        "text": f"text {sid}",
        "author": {"screen_name": author, "name": author.upper(), "avatar_url": f"https://img/{author}.jpg"},
        "created_timestamp": created // 1000,          # Math.floor(created / 1000)
        "replying_to": {"screen_name": reply_to, "status": "1"} if reply_to else None,
        "reposted_by": {"screen_name": "acct", "name": "ACCT", "avatar_url": "https://img/acct.jpg"} if repost else None,
        "media": None,
        "quote": None,
    }


def ts_ms(status):
    return status["created_timestamp"] * 1000


class StubFx:
    def __init__(self, pages, status="ok"):
        self.pages = pages
        self.status = status
        self.calls = []

    def fetch_statuses(self, handle, *, cursor=None, with_replies=True):
        self.calls.append({"handle": handle, "cursor": cursor, "with_replies": with_replies})
        if callable(self.status):
            r = self.status(len(self.calls), cursor, with_replies)
            if r:
                return r
        elif self.status != "ok":
            return {"status": "not_found"} if self.status == "not_found" else {"status": "error", "error": self.status}
        i = int(cursor[1:]) if cursor else 0
        results = self.pages[i] if i < len(self.pages) else []
        return {"status": "ok", "results": results,
                "cursor_bottom": f"c{i + 1}" if i + 1 < len(self.pages) else None}


def baselined(page1, now=0):
    repo = Store(":memory:")
    r = take_baseline(repo, StubFx([page1]), "acct", now=now)
    assert r["ok"] is True
    return repo


def feed(repo):
    return [r for r in repo.query_feed() if r["kind"] == "post"]


def poll(repo, fx, now, cfg=CFG, **kw):
    return poll_account(repo, fx, repo.get_account("acct"), now=now, cfg=cfg, sleep=no_sleep, **kw)


def make_poller(repo, fx, clock, cfg=CFG):
    return Poller(repo, fx, load_cfg=lambda: {"poller": cfg}, now_ms=lambda: clock[0], sleep=no_sleep)


# -- the 26 Node tests ---------------------------------------------------------

def test_take_baseline_stores_account_and_hides_page1_posts():
    repo = baselined([st(id="1", created=-MIN), st(id="2", created=-2 * MIN)], 1000)
    a = repo.get_account("acct")
    assert a["display_name"] == "ACCT"
    assert a["avatar_url"] == "https://img/acct.jpg"
    assert a["last_ok_at"] == 1000
    assert len(feed(repo)) == 0
    assert sorted(repo.seen_ids("acct", ["1", "2"])) == ["1", "2"]


def test_take_baseline_uses_reposted_by_for_name_when_account_only_reposts():
    repo = Store(":memory:")
    take_baseline(repo, StubFx([[st(author="other", repost=True)]]), "acct", now=1)
    assert repo.get_account("acct")["display_name"] == "ACCT"


def test_take_baseline_not_found_saves_nothing():
    repo = Store(":memory:")
    r = take_baseline(repo, StubFx([], status="not_found"), "ghost", now=1)
    assert r == {"ok": False, "reason": "not_found"}
    assert repo.get_account("ghost") is None


def test_first_poll_after_baseline_with_nothing_new():
    page = [st(id="1")]
    repo = baselined(page)
    r = poll(repo, StubFx([page]), MIN)
    assert len(r["new_posts"]) == 0
    assert repo.get_account("acct")["last_ok_at"] == MIN


def test_live_one_new_post_on_page1_within_3_intervals():
    old = st(id="1", created=0)
    repo = baselined([old])
    now = 2 * MIN
    fresh = st(id="2", created=now - 30_000)
    r = poll(repo, StubFx([[fresh, old]]), now)
    assert len(r["new_posts"]) == 1
    assert r["new_posts"][0]["origin"] == "live"
    assert r["new_posts"][0]["sort_at"] == now
    assert r["new_posts"][0]["created_at"] == ts_ms(fresh)


def test_force_catchup_makes_page1_items_catchup_with_sort_at_created_at():
    old = st(id="1", created=0)
    repo = baselined([old])
    now = 2 * MIN
    fresh = st(id="2", created=now - 30_000)
    r = poll(repo, StubFx([[fresh, old]]), now, force_catchup=True)
    assert r["new_posts"][0]["origin"] == "catchup"
    assert r["new_posts"][0]["sort_at"] == ts_ms(fresh)


def test_catchup_follows_cursor_until_seen_id():
    old = st(id="1", created=0)
    repo = baselined([old])
    now = 6 * 60 * MIN  # 6 h later -> not live
    p1 = [st(created=now - 1 * MIN), st(created=now - 2 * MIN)]
    p2 = [st(created=now - 3 * MIN), st(created=now - 4 * MIN), old]
    fx = StubFx([p1, p2, [st(created=-10 * MIN)]])
    r = poll(repo, fx, now)
    assert len(fx.calls) == 2
    assert len(r["new_posts"]) == 4
    assert all(p["origin"] == "catchup" for p in r["new_posts"])
    assert all(p["sort_at"] == p["created_at"] for p in r["new_posts"])
    assert r["gap"] is None


def test_catchup_stops_at_a_post_older_than_last_ok_at_even_without_seen_id():
    repo = baselined([st(id="1", created=0)], 120 * MIN)
    now = 6 * 60 * MIN
    p1 = [st(created=now - MIN), st(created=5 * MIN)]  # second is well before lastOkAt
    fx = StubFx([p1, [st(created=now - 2 * MIN)]])
    r = poll(repo, fx, now)
    assert len(fx.calls) == 1
    assert len(r["new_posts"]) == 1, "old unseen post is not shown as new"


def test_unseen_old_post_eg_newly_pinned_is_stored_hidden_in_live_mode():
    repo = baselined([st(id="1", created=0)], 0)
    now = 2 * MIN
    pinned = st(id="9", created=-30 * 24 * 60 * MIN)
    r = poll(repo, StubFx([[pinned, st(id="1")]]), now)
    assert len(r["new_posts"]) == 0
    assert list(repo.seen_ids("acct", ["9"])) == ["9"]


def test_cap_reached_creates_gap():
    repo = baselined([st(id="1", created=0)], 0)
    now = 6 * 60 * MIN
    pages = [[st(created=now - (2 * i + 1) * MIN), st(created=now - (2 * i + 2) * MIN)] for i in range(3)]
    fx = StubFx(pages)
    r = poll(repo, fx, now, cfg={**CFG, "max_pages": 2})
    assert len(fx.calls) == 2
    assert r["gap"]
    assert r["gap"]["from_at"] == 0
    assert r["gap"]["to_at"] == now - 4 * MIN
    assert len(r["new_posts"]) == 4
    assert len([x for x in repo.query_feed() if x["kind"] == "gap"]) == 1


def test_error_part_way_through_catchup_records_a_gap_instead_of_silently_dropping():
    repo = baselined([st(id="1", created=0)], 0)
    now = 6 * 60 * MIN
    pages = [[st(created=now - MIN), st(created=now - 2 * MIN)], [st(created=now - 3 * MIN)]]
    fx = StubFx(pages, status=lambda n, cursor, wr: {"status": "error", "error": "HTTP 502"} if cursor else None)
    r = poll(repo, fx, now)
    assert len(r["new_posts"]) == 2
    assert r["gap"], "gap recorded"
    assert r["gap"]["to_at"] == now - 2 * MIN


def test_catchup_does_not_stop_at_a_seen_thread_parent_near_the_top_of_page1():
    parent = st(id="p", created=0)
    repo = baselined([parent], 0)
    now = 6 * 60 * MIN
    # with_replies groups a new self-reply right above its (already seen) parent
    p1 = [st(created=now - MIN, reply_to="acct"), parent, *[st(created=now - i * MIN) for i in (2, 3, 4)]]
    p2 = [st(created=now - 5 * MIN), st(created=now - 6 * MIN), st(id="old", created=-MIN)]
    fx = StubFx([p1, p2])
    r = poll(repo, fx, now)
    assert len(fx.calls) == 2, "followed to page 2"
    assert len(r["new_posts"]) == 6
    assert r["gap"] is None


def test_old_reposts_that_slide_in_below_the_last_seen_item_are_not_shown():
    seen_a = st(id="a", created=-MIN)
    seen_b = st(id="b", created=-2 * MIN)
    repo = baselined([seen_a, seen_b], 0)
    now = 2 * MIN
    fresh = st(id="n", created=now - 1000)
    tail = st(id="rt", author="someone", created=-3 * 24 * 60 * MIN, repost=True)
    r = poll(repo, StubFx([[fresh, seen_a, seen_b, tail]]), now)
    assert [p["status_id"] for p in r["new_posts"]] == ["n"]
    assert list(repo.seen_ids("acct", ["rt"])) == ["rt"], "tail stored hidden so it never shows later"


def test_new_repost_above_the_last_seen_item_is_shown_live():
    seen_a = st(id="a", created=-MIN)
    repo = baselined([seen_a], 0)
    now = 2 * MIN
    rt = st(id="rt", author="someone", created=-9 * 24 * 60 * MIN, repost=True)
    r = poll(repo, StubFx([[rt, seen_a]]), now)
    assert [[p["status_id"], p["origin"], p["sort_at"]] for p in r["new_posts"]] == [["rt", "live", now]]


def test_fallback_timeline_old_reposts_not_backfilled_and_page2_keeps_using_plain_timeline():
    seen_a = st(id="a", created=-MIN)
    repo = baselined([seen_a], 0)
    now = 6 * 60 * MIN
    p1 = [st(created=now - MIN), st(created=now - 2 * MIN)]
    p2 = [st(created=now - 3 * MIN), seen_a, st(id="old-rt", author="x", created=-5 * 24 * 60 * MIN, repost=True),
          st(id="old", created=-6 * 24 * 60 * MIN)]
    fx = StubFx([p1, p2], status=lambda n, cursor, wr: {"status": "not_found"} if wr else None)
    r = poll(repo, fx, now)
    assert [c["with_replies"] for c in fx.calls] == [True, False, False]
    assert len(r["new_posts"]) == 3
    assert r["gap"] is None


def test_unseen_post_older_than_last_ok_at_but_newer_than_baseline_is_shown_as_catchup_not_hidden():
    repo = baselined([st(id="a", created=0)], 0)
    repo.record_check("acct", now=120 * MIN, ok=True, next_check_at=0)
    now = 121 * MIN
    late = st(id="late", created=60 * MIN)  # 60 min before lastOkAt, after baseline
    r = poll(repo, StubFx([[late, st(id="a")]]), now)
    assert [[p["status_id"], p["origin"], p["sort_at"]] for p in r["new_posts"]] == [["late", "catchup", 60 * MIN]]


def test_run_cycle_survives_a_throwing_on_change():
    repo = Store(":memory:")
    take_baseline(repo, StubFx([[st(id="1")]]), "acct", now=0)
    poller = make_poller(repo, StubFx([[st(id="1")]]), [MIN])

    def boom():
        raise RuntimeError("boom")

    poller.run_cycle(boom)  # must not raise


def test_replies_to_others_and_context_tweets_are_not_stored_self_replies_are():
    repo = baselined([st(id="1", created=0)], 0)
    now = 2 * MIN
    page = [
        st(created=now - 1000, reply_to="acct"),
        st(created=now - 2000, reply_to="someone"),
        st(created=now - 3000, author="someone"),
        st(id="1"),
    ]
    r = poll(repo, StubFx([page]), now)
    assert [p["type"] for p in r["new_posts"]] == ["reply"]


def test_falls_back_to_timeline_without_replies_when_with_replies_fails():
    repo = baselined([st(id="1", created=0)], 0)
    now = 2 * MIN
    fx = StubFx([[st(created=now - 1000), st(id="1")]],
                status=lambda n, cursor, wr: {"status": "error", "error": "HTTP 502"} if wr else None)
    r = poll(repo, fx, now)
    assert len(r["new_posts"]) == 1
    assert [c["with_replies"] for c in fx.calls] == [True, False]
    assert repo.get_account("acct")["last_error"] is None


def test_assign_sort_times_live_gives_first_seen_at_to_all():
    items = [{"type": "post", "created_at": 5}, {"type": "repost", "created_at": 1}]
    assert assign_sort_times(items, live=True, first_seen_at=100, last_ok_at=0) == [100, 100]


def test_repost_in_catchup_gets_newer_neighbour_time_clamped():
    items = [
        {"type": "post", "created_at": 90},
        {"type": "repost", "created_at": 3},
        {"type": "post", "created_at": 60},
        {"type": "repost", "created_at": 999},
    ]
    # repost #1 -> newer neighbour 90; repost #3 -> newer = index 2 (60)
    assert assign_sort_times(items, live=False, first_seen_at=100, last_ok_at=50) == [90, 90, 60, 60]
    # clamping: neighbour 20 is below lastOkAt 50
    assert assign_sort_times([{"type": "post", "created_at": 20}, {"type": "repost", "created_at": 1}],
                             live=False, first_seen_at=100, last_ok_at=50) == [20, 50]


def test_assign_sort_times_repost_with_no_neighbours_uses_last_ok_at():
    assert assign_sort_times([{"type": "repost", "created_at": 1}], live=False, first_seen_at=100, last_ok_at=40) == [40]


def test_backoff_ms_schedule():
    assert backoff_ms(1) == 60_000
    assert backoff_ms(2) == 120_000
    assert backoff_ms(3) == 300_000
    assert backoff_ms(9) == 300_000


def test_error_sets_backoff():
    repo = baselined([st(id="1")], 0)
    fx = StubFx([], status="HTTP 500")
    for expected_fail, expected_delay in [(1, 60_000), (2, 120_000), (3, 300_000), (4, 300_000)]:
        poll(repo, fx, 1000)
        a = repo.get_account("acct")
        assert a["fail_count"] == expected_fail
        assert a["next_check_at"] == 1000 + expected_delay
        assert a["last_error"] == "HTTP 500"


def test_404_three_polls_in_a_row_marks_not_found_success_resets_streak():
    page = [st(id="1")]
    repo = baselined(page, 0)
    nf = StubFx([], status="not_found")
    poll(repo, nf, 1)
    poll(repo, nf, 2)
    assert repo.get_account("acct")["last_error"] != "not found"
    poll(repo, nf, 3)
    assert repo.get_account("acct")["last_error"] == "not found"
    assert repo.get_account("acct")["not_found_streak"] == 3
    poll(repo, StubFx([page]), 4)
    assert repo.get_account("acct")["not_found_streak"] == 0
    assert repo.get_account("acct")["last_error"] is None


def test_run_cycle_skips_accounts_whose_next_check_at_is_in_the_future_and_reports_inserted_rows():
    # Node's onNew(rows) / onAccountUpdate(accounts) are one on_change(); the rows it
    # carried are read back from the store instead.
    repo = Store(":memory:")
    clock = [0]
    take_baseline(repo, StubFx([[st(id="1", author="acct")]]), "acct", now=0)
    take_baseline(repo, StubFx([[st(id="50", author="later")]]), "later", now=0)
    repo.record_check("later", now=0, ok=False, error="x", next_check_at=10 * MIN)

    clock[0] = MIN
    fresh = st(id="2", author="acct", created=clock[0] - 1000)
    pages = [[fresh, st(id="1", author="acct")]]
    fx = StubFx(pages)
    changes = []
    poller = make_poller(repo, fx, clock)
    poller.run_cycle(lambda: changes.append(1))
    assert [c["handle"] for c in fx.calls] == ["acct"]
    got = feed(repo)
    assert len(got) == 1
    assert got[0]["status_id"] == "2"
    assert got[0]["kind"] == "post"
    assert got[0]["origin"] == "catchup", "first poll after process start is catch-up"
    assert len(changes) >= 1

    # second cycle: live now
    clock[0] = 2 * MIN
    fresh2 = st(id="3", author="acct", created=clock[0] - 1000)
    pages[0] = [fresh2, fresh]
    poller.run_cycle(lambda: changes.append(1))
    newest = feed(repo)[0]
    assert newest["status_id"] == "3"
    assert newest["origin"] == "live"


# -- Python-only Poller tests ---------------------------------------------------

def test_first_cycle_after_start_is_catchup():
    # The migration case: the old feed's last good check was a minute ago, well inside
    # the live window, so only the forced first-cycle catch-up keeps these off "live".
    now = 10 * 60 * MIN
    repo = baselined([st(id="1", created=0)], now - MIN)
    assert repo.get_account("acct")["last_ok_at"] == now - MIN
    a = st(id="a", created=now - 20_000)
    b = st(id="b", created=now - 40_000)
    clock = [now]
    poller = make_poller(repo, StubFx([[a, b, st(id="1")]]), clock)
    poller.run_cycle(lambda: None)
    rows = feed(repo)
    assert sorted(r["status_id"] for r in rows) == ["a", "b"]
    assert all(r["origin"] == "catchup" for r in rows)
    assert all(r["sort_at"] == r["created_at"] for r in rows)
    assert not any(r["origin"] == "live" for r in rows)


def test_failed_account_catches_up_after_recovery():
    t0 = 10 * 60 * MIN
    repo = Store(":memory:")
    clock = [t0]
    pages = [[st(id="1", created=0)]]
    poller = make_poller(repo, StubFx(pages), clock)
    assert poller.add_account("acct", None)["ok"] is True       # now in polled-since-start
    repo.record_check("acct", now=t0 + 30_000, ok=False, error="HTTP 502", next_check_at=0)
    a = repo.get_account("acct")
    assert a["fail_count"] == 1 and a["next_check_at"] == 0 and a["last_ok_at"] == t0
    clock[0] = t0 + MIN                                         # inside the live window
    fresh = st(id="f", created=t0 + 40_000)
    pages[0] = [fresh, st(id="1", created=0)]
    poller.run_cycle(lambda: None)
    rows = feed(repo)
    assert [(r["status_id"], r["origin"], r["sort_at"]) for r in rows] == [("f", "catchup", ts_ms(fresh))]


def test_live_only_from_page_one():
    old = st(id="1", created=0)
    repo = baselined([old], 0)
    now = 2 * MIN                                               # live poll
    p1 = [st(id="n1", created=now - 10_000), st(id="n2", created=now - 20_000)]
    p2 = [st(id="n3", created=now - 30_000), old]
    fx = StubFx([p1, p2])
    r = poll(repo, fx, now)
    assert len(fx.calls) == 2
    assert r["gap"] is None
    assert {p["status_id"]: p["origin"] for p in r["new_posts"]} == {"n1": "live", "n2": "live", "n3": "catchup"}


def test_add_does_not_wait_for_other_accounts_poll():
    repo = Store(":memory:")
    poller = make_poller(repo, StubFx([[st(id="1", author="b")]]), [MIN])
    held = threading.Event()
    release = threading.Event()

    def hold():
        with poller.account_lock("a"):
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert held.wait(1)
    result = {}
    adder = threading.Thread(target=lambda: result.update(r=poller.add_account("b", None)), daemon=True)
    adder.start()
    adder.join(1)
    release.set()
    holder.join(1)
    assert not adder.is_alive(), "add_account waited on another account's lock"
    assert result["r"]["ok"] is True
    assert result["r"]["account"]["handle"] == "b"


def test_interval_floor():
    poller = Poller(Store(":memory:"), StubFx([]), load_cfg=lambda: {"poller": {**CFG, "interval_s": 5}},
                    now_ms=lambda: 0, sleep=no_sleep)
    assert poller.interval_s() == 30


def test_stop_mid_cycle_polls_no_further_accounts_and_reports_no_change_for_them():
    repo = Store(":memory:")
    for h in ("a", "b", "c"):
        take_baseline(repo, StubFx([[st(id=h, author=h)]]), h, now=0)
    fx = StubFx([[st(id="z", author="a")]])
    poller = make_poller(repo, fx, [MIN])
    changes = []

    def on_change():
        changes.append(1)
        poller.stop()          # shutdown arrives during the first account

    poller.run_cycle(on_change)
    assert len(fx.calls) == 1
    assert len(changes) == 1

    poller.resume()
    fx.calls.clear()
    poller.run_cycle(lambda: None)
    assert len(fx.calls) == 3   # resume lets a later cycle poll everyone


# -- a fault inside poll_account (final review A2) ------------------------------

def test_a_lone_surrogate_in_a_status_is_stored_as_the_replacement_character():
    bad = {**st(id="1"), "text": "hi \ud83d"}
    bad["author"] = {**bad["author"], "name": "N \ud83d"}
    repo = baselined([bad], 0)                     # take_baseline: upsert + insert, no UnicodeEncodeError
    assert repo.get_account("acct")["display_name"] == "N �"
    now = 2 * MIN
    new = {**st(id="2", created=now - 1000), "text": "new \ud83d"}
    r = poll(repo, StubFx([[new, bad]]), now)
    assert r["status"] == "ok"
    assert [p["text"] for p in r["new_posts"]] == ["new �"]
    assert repo.get_account("acct")["last_ok_at"] == now


def test_an_unexpected_poll_fault_is_recorded_backed_off_and_counted(monkeypatch):
    from services import _degrade
    from services.xfeed_svc import poller as poller_mod

    repo = baselined([st(id="1")], 0)
    clock = [MIN]

    def boom(*a, **k):
        raise RuntimeError("secret-handle exploded")

    monkeypatch.setattr(poller_mod, "poll_account", boom)
    _degrade.reset()
    changes = []
    make_poller(repo, StubFx([]), clock).run_cycle(lambda: changes.append(1))
    a = repo.get_account("acct")
    assert a["last_error"] == "internal error"
    assert a["fail_count"] == 1
    assert a["last_checked_at"] == MIN
    assert a["next_check_at"] == MIN + 60_000          # the first backoff step
    assert _degrade.counts()["xfeed.poller"] == 1
    assert changes == [1]


def test_a_fault_recording_the_poll_fault_never_breaks_the_cycle(monkeypatch):
    from services import _degrade
    from services.xfeed_svc import poller as poller_mod

    repo = Store(":memory:")
    for h in ("a", "b"):
        take_baseline(repo, StubFx([[st(id=h, author=h)]]), h, now=0)
    polled = []

    def boom(store, fx, account, **k):
        polled.append(account["handle"])
        raise RuntimeError("poll exploded")

    def record_boom(*a, **k):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(poller_mod, "poll_account", boom)
    monkeypatch.setattr(repo, "record_check", record_boom)
    _degrade.reset()
    make_poller(repo, StubFx([]), [MIN]).run_cycle(lambda: None)    # must not raise
    assert polled == ["a", "b"]                                   # the second account still ran
    assert _degrade.counts()["xfeed.poller"] == 4                 # each poll fault + each record fault
