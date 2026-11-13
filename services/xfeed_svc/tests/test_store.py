import itertools
import json
import sqlite3
import threading

import pytest

from services.xfeed_svc.store import SCHEMA, Store

_seq = itertools.count(1000)


def make_post(**over):
    sid = str(next(_seq))
    p = {
        "status_id": sid, "account_handle": "a", "type": "post", "text": f"post {sid}",
        "author_name": "A Name", "author_handle": "a", "author_avatar": None,
        "url": f"https://x.com/a/status/{sid}", "created_at": 1000, "cashtags": [], "media": [],
        "quote": None, "counts": {"replies": 0, "reposts": 0, "likes": 0, "views": 0},
        "raw": {"id": sid}, "first_seen_at": 1000, "sort_at": 1000, "origin": "live",
    }
    p.update(over)
    return p


@pytest.fixture
def repo():
    s = Store(":memory:")
    s.upsert_account("a", display_name="A", avatar_url=None, now=1)
    s.upsert_account("b", display_name="B", avatar_url=None, now=1)
    yield s
    s.close()


def posts(rows):
    return [r for r in rows if r["kind"] == "post"]


def test_baseline_posts_hidden_from_feed(repo):
    repo.insert_posts([make_post(origin="baseline"), make_post(origin="live")])
    assert len(posts(repo.query_feed())) == 1


def test_inactive_account_posts_hidden(repo):
    repo.insert_posts([make_post(), make_post(account_handle="b")])
    repo.deactivate_account("b")
    rows = posts(repo.query_feed())
    assert len(rows) == 1
    assert rows[0]["account_handle"] == "a"
    assert repo.account_counts() == {"a": 1}


def test_same_status_id_for_two_accounts(repo):
    n = repo.insert_posts([make_post(status_id="1"),
                           make_post(status_id="1", account_handle="b", type="repost")])
    assert n == 2
    assert len(posts(repo.query_feed())) == 2
    b_only = posts(repo.query_feed(account="b"))
    assert len(b_only) == 1
    assert b_only[0]["type"] == "repost"


def test_insert_posts_ignores_duplicates(repo):
    p = make_post()
    assert repo.insert_posts([p]) == 1
    assert repo.insert_posts([p]) == 0


def test_query_feed_filters_type_q_ticker(repo):
    repo.insert_posts([
        make_post(text="Nvidia beats", cashtags=["NVDA"]),
        make_post(text="something else", type="repost", author_handle="Zed", author_name="Zed Hedge"),
        make_post(text="thread cont", type="reply"),
    ])
    assert len(posts(repo.query_feed(type="repost"))) == 1
    assert len(posts(repo.query_feed(q="NVIDIA"))) == 1
    assert len(posts(repo.query_feed(q="zed hedge"))) == 1
    assert len(posts(repo.query_feed(ticker="nvda"))) == 1
    assert len(posts(repo.query_feed(ticker="$nvda"))) == 1
    assert len(posts(repo.query_feed(ticker="NV"))) == 0


def test_feed_rows_parse_json_columns_and_are_newest_first(repo):
    repo.insert_posts([
        make_post(sort_at=10, media=[{"type": "photo", "url": "u"}], cashtags=["X"]),
        make_post(sort_at=30),
        make_post(sort_at=20),
    ])
    rows = posts(repo.query_feed())
    assert [r["sort_at"] for r in rows] == [30, 20, 10]
    assert rows[2]["media"] == [{"type": "photo", "url": "u"}]
    assert rows[2]["cashtags"] == ["X"]
    assert "raw" not in rows[2]


def test_before_paginates_and_limit_applies(repo):
    repo.insert_posts([make_post(sort_at=s) for s in (10, 20, 30, 40)])
    assert [r["sort_at"] for r in posts(repo.query_feed(before=30))] == [20, 10]
    assert len(posts(repo.query_feed(limit=2))) == 2


def test_pagination_does_not_skip_rows_that_share_a_sort_at(repo):
    repo.insert_posts([make_post(status_id="1", sort_at=50), make_post(status_id="2", sort_at=50),
                       make_post(status_id="3", sort_at=50), make_post(status_id="4", sort_at=10)])
    first = posts(repo.query_feed(limit=2))
    last = first[-1]
    rest = posts(repo.query_feed(before=last["sort_at"],
                                 before_key=f"{last['status_id']}:{last['account_handle']}"))
    assert sorted(r["status_id"] for r in [*first, *rest]) == ["1", "2", "3", "4"]


def test_upsert_account_reactivates_and_resets_baseline_at(repo):
    repo.record_check("a", now=5, ok=False, error="boom", next_check_at=99)
    repo.deactivate_account("a")
    repo.upsert_account("a", display_name="A2", avatar_url="x", now=50)
    a = repo.get_account("a")
    assert a["active"] is True
    assert a["baseline_at"] == 50
    assert a["added_at"] == 1
    assert a["display_name"] == "A2"
    assert a["last_error"] is None
    assert a["fail_count"] == 0


def test_record_check_ok_and_error(repo):
    repo.record_check("a", now=5, ok=False, error="HTTP 500", next_check_at=65)
    a = repo.get_account("a")
    assert a["last_error"] == "HTTP 500"
    assert a["fail_count"] == 1
    assert a["next_check_at"] == 65
    assert a["last_checked_at"] == 5
    repo.record_check("a", now=9, ok=True, next_check_at=0)
    a = repo.get_account("a")
    assert a["last_ok_at"] == 9
    assert a["last_error"] is None
    assert a["fail_count"] == 0
    assert a["not_found_streak"] == 0


def test_gaps_merged_into_feed_in_order(repo):
    repo.insert_posts([make_post(sort_at=10), make_post(sort_at=30)])
    repo.insert_gap(handle="a", from_at=5, to_at=20, now=40)
    rows = repo.query_feed()
    assert [r["kind"] for r in rows] == ["post", "gap", "post"]
    assert rows[1]["account_handle"] == "a"
    assert rows[1]["sort_at"] == 20


def test_gaps_for_other_accounts_hidden_when_filtering_by_account(repo):
    repo.insert_gap(handle="b", from_at=5, to_at=20, now=40)
    assert repo.query_feed(account="a") == []
    assert repo.query_feed(q="x") == [], "gaps hidden when searching"


def test_seen_ids_returns_only_known_ids_for_that_account(repo):
    repo.insert_posts([make_post(status_id="1"), make_post(status_id="2", account_handle="b")])
    assert repo.seen_ids("a", ["1", "2", "3"]) == {"1"}
    assert repo.seen_ids("a", []) == set()


def test_group_set_on_add_kept_on_readd_and_changeable():
    repo = Store(":memory:")
    repo.upsert_account("z", display_name="Z", avatar_url=None, now=1, group_name="News")
    assert repo.get_account("z")["group_name"] == "News"
    repo.deactivate_account("z")
    repo.upsert_account("z", display_name="Z", avatar_url=None, now=2)
    assert repo.get_account("z")["group_name"] == "News"
    repo.upsert_account("z", display_name="Z", avatar_url=None, now=3, group_name="Finance")
    assert repo.get_account("z")["group_name"] == "Finance"
    repo.set_account_group("z", None)
    assert repo.get_account("z")["group_name"] is None


def test_query_feed_filters_by_group_including_ungrouped(repo):
    repo.upsert_account("c", display_name="C", avatar_url=None, now=1, group_name="News")
    repo.insert_posts([make_post(), make_post(account_handle="c"), make_post(account_handle="b")])
    repo.insert_gap(handle="a", from_at=1, to_at=2, now=3)
    assert [r["account_handle"] for r in repo.query_feed(group="News")] == ["c"]
    assert [r["account_handle"] for r in repo.query_feed(group="news")] == ["c"]
    ungrouped = repo.query_feed(ungrouped=True)
    assert len(posts(ungrouped)) == 2
    assert len([r for r in ungrouped if r["kind"] == "gap"]) == 1


def test_settings_round_trip(repo):
    assert repo.get_setting("intervalMs", 60000) == 60000
    repo.set_setting("intervalMs", 45000)
    assert repo.get_setting("intervalMs", 60000) == 45000


def test_opens_a_node_created_db(tmp_path):
    path = tmp_path / "node.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(SCHEMA)  # the Node SCHEMA text
    raw.execute("INSERT INTO accounts (handle, display_name, avatar_url, active, added_at, baseline_at,"
                " last_checked_at, last_ok_at, last_error, fail_count, not_found_streak, next_check_at, group_name)"
                " VALUES ('n','Node',NULL,1,5,6,7,8,'err',2,1,9,'News')")
    raw.execute("INSERT INTO posts VALUES ('n','77',100,90,95,'live','post','héllo $NVDA','Nm','n',NULL,"
                "'https://x.com/n/status/77',?,?,?,?,?)",
                (json.dumps(["NVDA"]), json.dumps([{"type": "photo", "url": "u"}]),
                 json.dumps({"status_id": "5"}), json.dumps({"likes": 3, "views": 10}), json.dumps({"id": "77"})))
    raw.execute("INSERT INTO gaps (account_handle, from_at, to_at, created_at) VALUES ('n',1,2,3)")
    raw.execute("INSERT INTO settings VALUES ('intervalMs','45000')")
    raw.commit()
    raw.close()

    s = Store(path)
    a = s.get_account("n")
    assert a == {"handle": "n", "display_name": "Node", "avatar_url": None, "active": True, "added_at": 5,
                 "baseline_at": 6, "last_checked_at": 7, "last_ok_at": 8, "last_error": "err",
                 "fail_count": 2, "not_found_streak": 1, "next_check_at": 9, "group_name": "News"}
    rows = s.query_feed()
    assert [r["kind"] for r in rows] == ["post", "gap"]
    p, g = rows
    assert p["text"] == "héllo $NVDA" and p["cashtags"] == ["NVDA"]
    assert p["media"] == [{"type": "photo", "url": "u"}]
    assert p["quote"] == {"status_id": "5"} and p["counts"] == {"likes": 3, "views": 10}
    assert (g["from_at"], g["to_at"], g["sort_at"]) == (1, 2, 2)
    assert s.get_setting("intervalMs") == 45000
    # New rows are written as compact (JSON.stringify-style) text and read back equal.
    s.insert_posts([make_post(account_handle="n", text="u-umlaut ü", media=[{"a": 1}], quote=None)])
    s.close()
    row = sqlite3.connect(str(path)).execute(
        "SELECT media, quote FROM posts WHERE text LIKE 'u-umlaut%'").fetchone()
    assert row == ('[{"a":1}]', "null")


def test_concurrent_threads_do_not_error(tmp_path):
    s = Store(tmp_path / "c.db")
    s.upsert_account("a", display_name="A", avatar_url=None, now=1)
    errors = []

    def work(tag):
        try:
            s.insert_posts([make_post(status_id=f"{tag}{i}") for i in range(200)])
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=work, args=(t,)) for t in ("x", "y")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == []
    assert len(s.query_feed(limit=1000)) == 400
    s.close()
