"""A backup nobody has restored is a hypothesis.

Audit AR-04 (2026-10-03): the backup had no restore procedure and had never
been restored. This file is the drill, run on every test run: build a checkout,
back it up with the REAL ``backup_local.main``, restore it with the REAL
``restore_backup.restore`` into an empty directory, and compare the two.
"""
import pathlib
import sqlite3
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import backup_local as bl  # noqa: E402
from tools import restore_backup as rb  # noqa: E402


def _db(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (x)")
    con.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
    con.commit()
    con.close()


def _rows(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[0] for r in con.execute("SELECT x FROM t ORDER BY x")]
    finally:
        con.close()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A checkout holding one of everything the backup carries."""
    repo = tmp_path / "repo"
    _db(repo / "options-scanner" / "data" / "signals.db", [1, 2, 3])
    _db(repo / "options-scanner" / "gex_history.db", [10])
    files = {
        "shared/webgui_auth.json": '{"password_hash": "x"}',
        "shared/tokens.json": '{"token": 1}',
        ".env": "MEMURAI_PASSWORD=p\n",
        "webgui/data/settings.json": '{"voice_enabled": true}',
        "webgui/data/eod/2026-10-02/summary.html": "<html>report</html>",
        "config/local/scanner.toml": "[iv_rank]\nSWING = 45\n",
        "deploy/site/ideas.json": '{"days": []}',
        "deploy/site/ideas/2026-10-02/SPY-0935.webp": "card",
    }
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    monkeypatch.setattr(bl, "REPO_ROOT", repo)

    def fake_redis(dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"REDIS0011")
        return True, "stubbed"

    monkeypatch.setattr(bl, "backup_redis", fake_redis)
    return repo, files


def _backup(tmp_path):
    dest = tmp_path / "backups"
    assert bl.main(["--dest", str(dest), "--no-offsite"]) == 0
    (gen,) = [d for d in dest.iterdir() if d.is_dir()]
    return gen


def test_a_backup_restores_to_an_identical_checkout(tmp_path, checkout):
    repo, files = checkout
    gen = _backup(tmp_path)
    scratch = tmp_path / "scratch"
    summary = rb.restore(gen, scratch, out=lambda line: None)

    assert summary["bad"] == [] and summary["kept"] == []
    assert summary["marked_ok"] is True
    for rel, text in files.items():
        assert (scratch / rel).read_text(encoding="utf-8") == text, rel
    assert _rows(scratch / "options-scanner/data/signals.db") == [1, 2, 3]
    assert _rows(scratch / "options-scanner/gex_history.db") == [10]


def test_the_login_store_comes_back(tmp_path, checkout):
    """The file whose absence locks the owner out of a restored app."""
    gen = _backup(tmp_path)
    scratch = tmp_path / "scratch"
    rb.restore(gen, scratch, out=lambda line: None)
    assert (scratch / "shared/webgui_auth.json").is_file()


def test_the_redis_dump_and_the_marker_are_not_copied_into_the_checkout(tmp_path, checkout):
    gen = _backup(tmp_path)
    assert (gen / "redis").is_dir() and (gen / rb.OK_MARKER).is_file()
    scratch = tmp_path / "scratch"
    rb.restore(gen, scratch, out=lambda line: None)
    assert not (scratch / "redis").exists()
    assert not (scratch / rb.OK_MARKER).exists()


def test_a_file_that_already_exists_is_left_alone(tmp_path, checkout):
    """A restore must never silently replace newer data."""
    gen = _backup(tmp_path)
    scratch = tmp_path / "scratch"
    (scratch / "webgui/data").mkdir(parents=True)
    (scratch / "webgui/data/settings.json").write_text("NEWER", encoding="utf-8")
    summary = rb.restore(gen, scratch, out=lambda line: None)
    assert (scratch / "webgui/data/settings.json").read_text(encoding="utf-8") == "NEWER"
    assert summary["kept"] == [str(pathlib.Path("webgui/data/settings.json"))]


def test_force_replaces_an_existing_file(tmp_path, checkout):
    gen = _backup(tmp_path)
    scratch = tmp_path / "scratch"
    (scratch / "webgui/data").mkdir(parents=True)
    (scratch / "webgui/data/settings.json").write_text("NEWER", encoding="utf-8")
    rb.restore(gen, scratch, force=True, out=lambda line: None)
    assert "voice_enabled" in (scratch / "webgui/data/settings.json").read_text(encoding="utf-8")


def test_a_dry_run_writes_nothing(tmp_path, checkout):
    gen = _backup(tmp_path)
    scratch = tmp_path / "scratch"
    summary = rb.restore(gen, scratch, dry_run=True, out=lambda line: None)
    assert len(summary["restored"]) >= 8
    assert not scratch.exists()


def test_a_damaged_database_is_reported_and_fails_the_restore(tmp_path, checkout):
    gen = _backup(tmp_path)
    (gen / "options-scanner/data/signals.db").write_bytes(b"this is not a database")
    scratch = tmp_path / "scratch"
    assert rb.main([str(gen), "--into", str(scratch)]) == 1


def test_a_generation_from_a_failed_run_is_restored_with_a_warning(tmp_path, checkout):
    gen = _backup(tmp_path)
    (gen / rb.OK_MARKER).unlink()
    said = []
    summary = rb.restore(gen, tmp_path / "scratch", out=said.append)
    assert summary["marked_ok"] is False
    assert any("no BACKUP_OK marker" in line for line in said)


def test_something_that_is_not_a_generation_is_refused(tmp_path):
    assert rb.main([str(tmp_path / "nope"), "--into", str(tmp_path / "x")]) == 2


def test_the_restore_tool_imports_nothing_from_the_repository():
    """It has to run on a box whose checkout is the thing being rebuilt."""
    import ast
    tree = ast.parse((ROOT / "tools" / "restore_backup.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


def test_both_tools_agree_on_the_marker_name():
    assert rb.OK_MARKER == bl.OK_MARKER
