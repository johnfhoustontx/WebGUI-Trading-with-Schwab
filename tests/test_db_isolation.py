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

from repo_paths import OPTIONS_SCANNER
from conftest import is_protected

# ⚠ These address the live stores by CONSTRUCTED path, never by globbing the
# filesystem. `live_db_paths()` enumerates .db files that actually exist, and
# every one of them is GITIGNORED — so on a fresh clone it returns an empty set
# and these tests failed with "no live signals.db found to test against",
# reporting machine state as a code defect. (Hit on the Linux VPS, 2026-08-29;
# it would fail identically on a fresh Windows clone.)
#
# The guard raises BEFORE it opens anything, so the file need not exist for the
# behaviour under test to be exercised. What matters is that the PATH resolves
# inside a live data directory, and that directory is tracked.
_SIGNALS = OPTIONS_SCANNER / "data" / "signals.db"
_PAPER = OPTIONS_SCANNER / "data" / "paper_account.db"


def test_the_known_leak_paths_are_protected():
    """signals.db and paper_account.db — the two stores that actually leaked."""
    assert is_protected(str(_SIGNALS))
    assert is_protected(str(_PAPER))


def test_connecting_to_a_live_store_raises():
    """The backstop: no test can open a production database, by any route."""
    with pytest.raises(RuntimeError, match="live database"):
        sqlite3.connect(str(_SIGNALS))


def test_read_only_uri_is_blocked_too():
    """A read-only open is still a dependency on machine state."""
    with pytest.raises(RuntimeError, match="live database"):
        sqlite3.connect(f"file:{_SIGNALS}?mode=ro", uri=True)


def test_tmp_and_memory_databases_still_work(tmp_path):
    """The guard must not make the suite unable to use real SQLite."""
    con = sqlite3.connect(tmp_path / "scratch.db")
    con.execute("CREATE TABLE t (a)")
    con.close()
    sqlite3.connect(":memory:").close()


def test_is_protected_does_not_flag_an_unrelated_path(tmp_path):
    assert not is_protected(tmp_path / "signals.db")
