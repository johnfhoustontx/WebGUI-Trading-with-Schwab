"""Tests for scripts/fix_gex_history_scale.py — the one-time migration
for GEX rows written before the × 0.01 normalization fix."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fix_gex_history_scale as mig  # noqa: E402


def _make_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE snapshots (
            symbol          TEXT    NOT NULL,
            view            TEXT    NOT NULL,
            ts              INTEGER NOT NULL,
            spot            REAL,
            flip            REAL,
            top_pos_strike  REAL,
            top_neg_strike  REAL,
            net_total       REAL,
            dte             INTEGER,
            gex_json        TEXT,
            net_delta_0dte  REAL,
            projected_net_delta_close REAL,
            hedge_pressure  REAL,
            PRIMARY KEY (symbol, view, ts)
        );
    """)
    return conn


def _seed(conn, symbol, view, ts, net_total, grid):
    conn.execute(
        "INSERT INTO snapshots(symbol, view, ts, spot, net_total, gex_json) "
        "VALUES (?,?,?,?,?,?)",
        (symbol, view, ts, 5000.0, net_total, json.dumps(grid)),
    )


def test_purge_removes_only_gex_rows(tmp_path):
    db = tmp_path / "gex_history.db"
    conn = _make_db(db)
    _seed(conn, "SPY", "gex", 1_700_000_000, 1.0e11,
          {"500.0": {"call": 1.0e9, "put": -5.0e8, "net": 5.0e8}})
    _seed(conn, "SPY", "charm", 1_700_000_000, 2.0e8, {"500.0": {"net": 2.0e8}})
    _seed(conn, "SPY", "dex", 1_700_000_000, 5.0e8, {"500.0": {"net": 5.0e8}})
    conn.commit()
    conn.close()

    rc = mig.main(["--purge", "--db", str(db)])
    assert rc == 0

    conn = sqlite3.connect(str(db))
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT view, COUNT(*) FROM snapshots GROUP BY view"
    ).fetchall()}
    conn.close()
    assert counts == {"charm": 1, "dex": 1}


def test_rescale_divides_gex_by_hundred(tmp_path):
    db = tmp_path / "gex_history.db"
    conn = _make_db(db)
    _seed(conn, "SPX", "gex", 1_700_000_000, 1.0e11,
          {"5000.0": {"call": 1.0e9, "put": -5.0e8, "net": 5.0e8}})
    conn.commit()
    conn.close()

    rc = mig.main(["--rescale", "--db", str(db)])
    assert rc == 0

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT net_total, gex_json FROM snapshots WHERE view='gex'"
    ).fetchone()
    conn.close()
    assert abs(row[0] - 1.0e9) < 1e-3
    grid = json.loads(row[1])
    lane = grid["5000.0"]
    assert abs(lane["call"] - 1.0e7) < 1e-3
    assert abs(lane["put"] - -5.0e6) < 1e-3
    assert abs(lane["net"] - 5.0e6) < 1e-3


def test_rescale_leaves_charm_and_dex_untouched(tmp_path):
    db = tmp_path / "gex_history.db"
    conn = _make_db(db)
    _seed(conn, "SPX", "gex", 1_700_000_000, 1.0e11, {"5000.0": {"net": 1.0e9}})
    _seed(conn, "SPX", "charm", 1_700_000_000, 2.0e8, {"5000.0": {"net": 2.0e8}})
    _seed(conn, "SPX", "dex", 1_700_000_000, 5.0e8, {"5000.0": {"net": 5.0e8}})
    conn.commit()
    conn.close()

    mig.main(["--rescale", "--db", str(db)])

    conn = sqlite3.connect(str(db))
    charm = conn.execute("SELECT net_total FROM snapshots WHERE view='charm'").fetchone()
    dex = conn.execute("SELECT net_total FROM snapshots WHERE view='dex'").fetchone()
    conn.close()
    assert abs(charm[0] - 2.0e8) < 1e-3
    assert abs(dex[0] - 5.0e8) < 1e-3


def test_rescale_is_idempotent_via_user_version(tmp_path, capsys):
    db = tmp_path / "gex_history.db"
    conn = _make_db(db)
    _seed(conn, "SPX", "gex", 1_700_000_000, 1.0e11, {"5000.0": {"net": 1.0e9}})
    conn.commit()
    conn.close()

    mig.main(["--rescale", "--db", str(db)])  # first run: scales
    capsys.readouterr()
    mig.main(["--rescale", "--db", str(db)])  # second run: refuses
    out = capsys.readouterr().out
    assert "user_version" in out and "already ran" in out

    conn = sqlite3.connect(str(db))
    net = conn.execute("SELECT net_total FROM snapshots WHERE view='gex'").fetchone()[0]
    conn.close()
    # Would be 1.0e7 if the second run had double-applied. Confirms idempotency.
    assert abs(net - 1.0e9) < 1e-3


def test_force_flag_overrides_user_version(tmp_path):
    db = tmp_path / "gex_history.db"
    conn = _make_db(db)
    _seed(conn, "SPX", "gex", 1_700_000_000, 1.0e11, {"5000.0": {"net": 1.0e9}})
    conn.commit()
    conn.execute(f"PRAGMA user_version = {mig.MIGRATED_USER_VERSION}")
    conn.commit()
    conn.close()

    mig.main(["--rescale", "--db", str(db), "--force"])

    conn = sqlite3.connect(str(db))
    net = conn.execute("SELECT net_total FROM snapshots WHERE view='gex'").fetchone()[0]
    conn.close()
    assert abs(net - 1.0e9) < 1e-3  # scaled


def test_dry_run_leaves_data_untouched(tmp_path, capsys):
    db = tmp_path / "gex_history.db"
    conn = _make_db(db)
    _seed(conn, "SPX", "gex", 1_700_000_000, 1.0e11, {"5000.0": {"net": 1.0e9}})
    conn.commit()
    conn.close()

    mig.main(["--purge", "--db", str(db), "--dry-run"])
    out = capsys.readouterr().out
    assert "dry-run" in out.lower()

    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM snapshots WHERE view='gex'").fetchone()[0]
    conn.close()
    assert n == 1


def test_no_db_returns_zero(tmp_path, capsys):
    db = tmp_path / "doesnotexist.db"
    rc = mig.main(["--purge", "--db", str(db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "No DB found" in out
