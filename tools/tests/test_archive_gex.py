"""tools/archive_gex.py: a backup copy in, an archive out, and the copy untouched."""
import datetime as dt
import sqlite3

from tools import archive_gex

gh = archive_gex.gh  # the tool puts options-scanner on sys.path


def _backup(path):
    conn = sqlite3.connect(path)
    conn.executescript(gh._SCHEMA + gh.TERM_SCHEMA_SQL)
    start, _end = gh._local_unix_range(dt.date(2026, 10, 28))
    conn.execute("INSERT INTO snapshots (symbol, view, ts, spot, gex_json) "
                 "VALUES ('SPY', 'gex', ?, 100.0, ?)",
                 (start + 9 * 3600, gh._encode_grid({100.0: {"call": 1.0, "put": -1.0, "net": 0.0}})))
    conn.commit()
    conn.close()


def test_a_copy_is_archived_and_a_rerun_adds_nothing(tmp_path, capsys):
    src, out = tmp_path / "copy.db", tmp_path / "out.db"
    _backup(src)
    before = src.read_bytes()

    assert archive_gex.main(["--db", str(src), "--archive", str(out)]) == 0
    assert "archived 2026-10-28: 1 snapshot rows, 0 term rows" in capsys.readouterr().out
    assert archive_gex.main(["--db", str(src), "--archive", str(out)]) == 0
    assert "0 new, 1 sessions held" in capsys.readouterr().out

    assert src.read_bytes() == before
    conn = sqlite3.connect(out)
    assert conn.execute("SELECT session_date FROM archived_sessions").fetchall() == [("2026-10-28",)]


def test_the_commit_is_recorded_or_none_but_never_fails_the_run(monkeypatch):
    def no_git(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr(archive_gex.subprocess, "run", no_git)
    assert archive_gex.code_version() is None
