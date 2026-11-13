"""``tools/import_x_feed.py`` -- one-shot import of the Node feed's database.

The fixture source is built with raw sqlite3 from the Node ``SCHEMA`` text, the
way ``node:sqlite`` created production's file.
"""
import os
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from services.xfeed_svc.store import SCHEMA  # noqa: E402
from tools import import_x_feed  # noqa: E402

TABLES = ("accounts", "posts", "gaps", "settings")


def _post(handle, sid, origin, n):
    return (handle, sid, 1000 + n, 900 + n, 900 + n, origin, "post", f"text {sid} $AAPL",
            "Name", handle, "http://a/v.png", f"https://x.com/{handle}/status/{sid}",
            '["AAPL"]', "[]", None, '{"likes":1}', '{"k":"v"}')


def _make_source(path):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO accounts (handle, display_name, avatar_url, active, added_at, baseline_at,"
              " last_checked_at, last_ok_at, last_error, fail_count, not_found_streak, next_check_at,"
              " group_name) VALUES ('alice','Alice','http://a/a.png',1,10,20,30,40,NULL,0,0,50,'macro')")
    c.execute("INSERT INTO accounts (handle, display_name, avatar_url, active, added_at, baseline_at,"
              " last_error, fail_count, not_found_streak, next_check_at) "
              "VALUES ('bob','Bob',NULL,0,11,21,'boom',3,2,0)")
    for i, origin in enumerate(("baseline", "catchup", "live", "live")):
        c.execute("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  _post("alice", str(100 + i), origin, i))
    c.execute("INSERT INTO gaps (account_handle, from_at, to_at, created_at) VALUES ('alice',1,2,3)")
    c.execute("INSERT INTO settings VALUES ('intervalMs','60000')")
    c.commit()
    c.close()


def _dump(path):
    c = sqlite3.connect(path)
    try:
        return {t: c.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in TABLES}
    finally:
        c.close()


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "src" / "feed.db"
    p.parent.mkdir()
    _make_source(p)
    return p


def test_imports_every_row_identically(src, tmp_path):
    dst = tmp_path / "dst" / "xfeed.db"
    counts = import_x_feed.import_db(src, dst)
    assert counts == {"accounts": 2, "posts": 4, "gaps": 1, "settings": 1}
    assert _dump(dst) == _dump(src)


def test_refuses_non_empty_target(src, tmp_path, capsys):
    dst = tmp_path / "xfeed.db"
    import_x_feed.import_db(src, dst)
    before = _dump(dst)
    with pytest.raises(import_x_feed.ImportRefused):
        import_x_feed.import_db(src, dst)
    assert _dump(dst) == before
    assert import_x_feed.main(["--source", str(src), "--target", str(dst)]) == 2
    assert "already has" in capsys.readouterr().err


def test_force_replaces(src, tmp_path):
    dst = tmp_path / "xfeed.db"
    import_x_feed.import_db(src, dst)
    c = sqlite3.connect(dst)
    c.execute("INSERT INTO accounts (handle, added_at, baseline_at) VALUES ('stale',1,1)")
    c.commit()
    c.close()
    counts = import_x_feed.import_db(src, dst, force=True)
    assert counts["accounts"] == 2
    assert _dump(dst) == _dump(src)


def test_source_is_untouched(src, tmp_path, capsys):
    dst = tmp_path / "xfeed.db"
    before_bytes = src.read_bytes()
    before_mtime = os.stat(src).st_mtime_ns
    siblings = sorted(p.name for p in src.parent.iterdir())
    assert import_x_feed.main(["--source", str(src), "--target", str(dst)]) == 0
    assert capsys.readouterr().out.strip() == "accounts=2 posts=4 gaps=1 settings=1"
    assert src.read_bytes() == before_bytes
    assert os.stat(src).st_mtime_ns == before_mtime
    assert sorted(p.name for p in src.parent.iterdir()) == siblings


def test_missing_source_exits_2_and_creates_nothing(tmp_path, capsys):
    dst = tmp_path / "dst" / "xfeed.db"
    assert import_x_feed.main(["--source", str(tmp_path / "nope.db"), "--target", str(dst)]) == 2
    assert "not found" in capsys.readouterr().err
    assert not dst.exists()


def test_source_wal_content_is_included(tmp_path):
    p = tmp_path / "wal.db"
    c = sqlite3.connect(p)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    c.execute("PRAGMA wal_autocheckpoint=0")
    c.execute("INSERT INTO settings VALUES ('k','v')")
    c.commit()  # lives only in the -wal file; connection stays open
    try:
        dst = tmp_path / "xfeed.db"
        assert import_x_feed.import_db(p, dst)["settings"] == 1
    finally:
        c.close()


def test_a_sqlite_error_exits_2_with_one_line_and_leaves_the_target_alone(src, tmp_path, capsys):
    dst = tmp_path / "xfeed.db"
    dst.write_bytes(b"this is not a database, it is sixty-odd bytes of plain text....")
    before = dst.read_bytes()
    assert import_x_feed.main(["--source", str(src), "--target", str(dst)]) == 2
    out = capsys.readouterr()
    assert out.out == ""
    lines = out.err.strip().splitlines()
    assert len(lines) == 1 and "Traceback" not in out.err
    assert "not a database" in lines[0]
    assert dst.read_bytes() == before


def test_the_non_empty_check_is_inside_the_write_transaction(src, tmp_path):
    """The target's account count is read under BEGIN IMMEDIATE, so a writer that
    lands between the check and the copy cannot slip past the refusal."""
    import inspect
    body = inspect.getsource(import_x_feed.import_db)
    assert body.index('BEGIN IMMEDIATE') < body.index('SELECT COUNT(*) FROM accounts')
