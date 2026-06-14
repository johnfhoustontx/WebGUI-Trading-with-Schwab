"""Tests for tools/db_admin.py — option-trade DB maintenance.

Pure: operates on temp DBs created via each app's own schema. No network,
no proxy, no live processes.
"""
import sqlite3
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # tools/
import db_admin  # noqa: E402


def _seed_trades(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trades (trade_id TEXT PRIMARY KEY, status TEXT, "
        "symbol TEXT, strategy TEXT, entry_time TEXT)"
    )
    conn.execute("CREATE TABLE trade_events (event_id INTEGER PRIMARY KEY, "
                 "timestamp TEXT, event TEXT, trade_id TEXT)")
    conn.executemany(
        "INSERT INTO trades (trade_id, status, symbol, strategy, entry_time) "
        "VALUES (?,?,?,?,?)",
        [("a", "OPEN", "SPY", "PUT", "2026-06-01T09:30:00"),
         ("b", "CLOSED", "QQQ", "PUT", "2026-06-15T10:00:00")],
    )
    conn.commit()
    conn.close()


def _make_target(tmp_path, name, seed):
    path = tmp_path / name
    seed(path)

    def _init(p=path):
        # mimic an app init: recreate the (empty) trades/trade_events schema
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE IF NOT EXISTS trades (trade_id TEXT PRIMARY "
                     "KEY, status TEXT, symbol TEXT, strategy TEXT, "
                     "entry_time TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS trade_events (event_id INTEGER "
                     "PRIMARY KEY, timestamp TEXT, event TEXT, trade_id TEXT)")
        conn.commit()
        conn.close()

    return db_admin.DbTarget(name=name, path=path, init=_init,
                             ts_columns={"trades": "entry_time",
                                         "trade_events": "timestamp"})


def test_status_reports_row_counts_and_date_range(tmp_path):
    target = _make_target(tmp_path, "trades.db", _seed_trades)
    info = db_admin.db_status(target)

    assert info["exists"] is True
    assert info["tables"]["trades"]["rows"] == 2
    assert info["tables"]["trades"]["min_ts"] == "2026-06-01T09:30:00"
    assert info["tables"]["trades"]["max_ts"] == "2026-06-15T10:00:00"
    assert info["tables"]["trade_events"]["rows"] == 0
    assert info["size_bytes"] > 0


def test_reset_wipes_and_reinits_empty_schema(tmp_path):
    target = _make_target(tmp_path, "trades.db", _seed_trades)
    assert db_admin.db_status(target)["tables"]["trades"]["rows"] == 2

    db_admin.reset_db(target, backup=False)

    info = db_admin.db_status(target)
    assert info["exists"] is True               # reinitialised, not just deleted
    assert info["tables"]["trades"]["rows"] == 0  # empty
    assert "trades" in info["tables"]             # schema present


def test_reset_removes_wal_and_shm_sidecars(tmp_path):
    target = _make_target(tmp_path, "trades.db", _seed_trades)
    wal = tmp_path / "trades.db-wal"
    shm = tmp_path / "trades.db-shm"
    wal.write_bytes(b"x")
    shm.write_bytes(b"x")

    db_admin.reset_db(target, backup=False)

    assert not wal.exists()
    assert not shm.exists()


def test_reset_with_backup_creates_timestamped_copy(tmp_path):
    target = _make_target(tmp_path, "trades.db", _seed_trades)
    backup_dir = tmp_path / "backups"

    backup_path = db_admin.reset_db(target, backup=True, backup_dir=backup_dir,
                                    stamp="20260601T120000Z")

    assert backup_path is not None
    assert backup_path.exists()
    assert backup_path.parent == backup_dir
    # backup retains the original 2 rows
    conn = sqlite3.connect(backup_path)
    n = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    conn.close()
    assert n == 2


def test_backup_db_copies_without_wiping(tmp_path):
    target = _make_target(tmp_path, "trades.db", _seed_trades)
    backup_dir = tmp_path / "backups"

    backup_path = db_admin.backup_db(target, backup_dir, stamp="20260601T120000Z")

    assert backup_path.exists()
    # original untouched
    assert db_admin.db_status(target)["tables"]["trades"]["rows"] == 2


def test_status_on_missing_db(tmp_path):
    target = db_admin.DbTarget(name="trades.db", path=tmp_path / "nope.db",
                               init=lambda: None, ts_columns={})
    info = db_admin.db_status(target)
    assert info["exists"] is False


def test_select_targets_subset():
    targets = db_admin.default_targets()
    names = {t.name for t in targets}
    assert names == {"trades.db", "trade_performance.db", "signals.db"}

    subset = db_admin.select_targets(targets, "trades,signals")
    assert {t.name for t in subset} == {"trades.db", "signals.db"}


def test_no_subcommand_is_allowed_and_routes_to_menu():
    # parser must not require a subcommand (so the menu can open)
    args = db_admin.build_parser().parse_args([])
    assert args.command is None


def test_menu_actions_are_all_dispatchable():
    for key, _label in db_admin.MENU_ACTIONS:
        assert key in db_admin._DISPATCH


def test_select_targets_unknown_name_raises():
    targets = db_admin.default_targets()
    try:
        db_admin.select_targets(targets, "bogus")
    except ValueError as e:
        assert "bogus" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown db name")
