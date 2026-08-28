"""The suite must not be able to touch a live on-disk store.

History: a pytest run wrote 24 synthetic signals (SPY @ underlying 500.00,
QQQ @ 430.00, deltas on an exact 32nd ladder) plus 21 rejected paper orders
into BOTH environments' production databases on 2026-07-16. They sat there
until 2026-08-28.

`options-scanner/tests/conftest.py` already carried a fixture written to stop
exactly that, and it had never worked: it does
``monkeypatch.setattr(signal_db, "DEFAULT_DB_PATH", tmp)``, but Python binds a
function default at ``def`` time, so all 13 ``signal_db`` functions kept the
live path regardless. ``paper_account_db`` was never patched at all, which is
how the orders leaked.

The lesson is that redirecting defaults is the wrong layer — it has to be
remembered once per module, per function, forever. These tests pin a backstop
at the one chokepoint every store shares: ``sqlite3.connect``.
"""
import sqlite3
import pytest

import repo_paths  # noqa: F401  (import guard: root must be importable)
from conftest import live_db_paths, is_protected


def test_the_known_leak_paths_are_protected():
    """signals.db and paper_account.db — the two stores that actually leaked."""
    protected = {p.name for p in live_db_paths()}
    assert "signals.db" in protected
    assert "paper_account.db" in protected


def test_connecting_to_a_live_store_raises():
    """The backstop: no test can open a production database, by any route."""
    target = next((p for p in live_db_paths() if p.name == "signals.db"), None)
    assert target is not None, "no live signals.db found to test against"
    with pytest.raises(RuntimeError, match="live database"):
        sqlite3.connect(str(target))


def test_read_only_uri_is_blocked_too():
    """A read-only open is still a dependency on machine state."""
    target = next((p for p in live_db_paths() if p.name == "signals.db"), None)
    with pytest.raises(RuntimeError, match="live database"):
        sqlite3.connect(f"file:{target}?mode=ro", uri=True)


def test_tmp_and_memory_databases_still_work(tmp_path):
    """The guard must not make the suite unable to use real SQLite."""
    con = sqlite3.connect(tmp_path / "scratch.db")
    con.execute("CREATE TABLE t (a)")
    con.close()
    sqlite3.connect(":memory:").close()


def test_is_protected_does_not_flag_an_unrelated_path(tmp_path):
    assert not is_protected(tmp_path / "signals.db")
