"""Storage-efficiency behaviour of gex_history_db.

Two independent measures, both driven by a 2026-07-25 audit of the live
``gex_history.db`` (2.2 GB holding only 5 sessions of data):

1. **Grid float rounding** (`_encode_grid`). The per-strike grid blobs are the
   dominant on-disk cost (966 MB of the file). Measured across the live DB, the
   cost is driven by FLOAT ENTROPY, not strike count — all four views average
   ~114 strikes, but bytes/snapshot ran gex 1,110 / dex 1,426 / charm 1,820 /
   vanna 2,172, because values serialize as e.g. ``1.2345678901234e-05``.
   Rounding to `_GRID_SIG_FIGS` significant figures before zlib measured a 52%
   reduction (966 MB -> 459 MB) with no display-visible change.

   Deliberately NOT combined with an insert-time strike crop: the read-time crop
   in ``options_svc.compute._crop_gamma_views`` widens its window to span the
   intraday spot PATH, and at write time the session's eventual range is unknown.
   Measured over 239 symbol-sessions, a +/-20 crop holes the heatmap on 7 of them
   (every $NDX session drifts 42-72 strikes). Rounding has no such failure mode.

2. **Bounded incremental vacuum** (`purge_keep_sessions`). The live DB had
   194,623 free pages (~797 MB, 36% of the file) and ``auto_vacuum=INCREMENTAL``
   enabled, but nothing in the repo ever called ``PRAGMA incremental_vacuum`` —
   the only reclaim path was a full VACUUM that (correctly) refuses to run during
   market hours. Retention DELETEs free pages for reuse but never shrink the file.
"""

import json
import sqlite3
import zlib

import gex_history_db as gh


# ---------------------------------------------------------------- rounding ---

def test_encode_grid_rounds_float_noise_away():
    """Long float tails must not reach the blob."""
    grid = {5000.0: {"net": 1.2345678901234e-05, "call": 2.7182818284590452}}
    decoded = gh._decode_grid(gh._encode_grid(grid))
    assert decoded[5000.0]["net"] == 1.23457e-05
    assert decoded[5000.0]["call"] == 2.71828


def test_encode_grid_preserves_magnitude_of_large_values():
    """GEX dollar magnitudes are large; rounding is SIGNIFICANT figures, not decimals."""
    grid = {5000.0: {"net": -1234567890.12345, "call": 987654321.98765}}
    decoded = gh._decode_grid(gh._encode_grid(grid))
    # 6 sig figs keeps the magnitude, drops only sub-0.001% noise.
    assert decoded[5000.0]["net"] == -1234570000.0
    assert decoded[5000.0]["call"] == 987654000.0
    for k in ("net", "call"):
        assert abs(decoded[5000.0][k] - grid[5000.0][k]) / abs(grid[5000.0][k]) < 1e-5


def test_encode_grid_actually_shrinks_the_blob():
    """The whole point: rounded payloads compress materially better."""
    grid = {
        float(5000 + i): {
            "net": 1.2345678901234e-05 * (i + 1),
            "call": 2.7182818284590452 * (i + 1),
            "put": -3.1415926535897932 * (i + 1),
        }
        for i in range(120)
    }
    rounded = len(gh._encode_grid(grid))
    unrounded = len(zlib.compress(json.dumps(grid).encode("utf-8")))
    assert rounded < unrounded, "rounding did not shrink the blob"
    # Live measurement was ~52%; assert a conservative floor so this can't silently regress.
    assert rounded < unrounded * 0.75


def test_encode_grid_handles_non_float_and_nested_values():
    """Grids carry ints/strings/None/nested dicts — rounding must pass them through."""
    grid = {5000.0: {"net": 1.0, "oi": 1234, "tag": "wall", "missing": None,
                     "sub": {"x": 1.23456789}, "seq": [1.23456789, 2]}}
    decoded = gh._decode_grid(gh._encode_grid(grid))
    cell = decoded[5000.0]
    assert cell["oi"] == 1234 and cell["tag"] == "wall" and cell["missing"] is None
    assert cell["sub"]["x"] == 1.23457
    assert cell["seq"] == [1.23457, 2]
    assert isinstance(cell["oi"], int) and not isinstance(cell["oi"], float)


def test_encode_grid_empty_still_none():
    assert gh._encode_grid({}) is None
    assert gh._encode_grid(None) is None


def test_decode_grid_still_reads_legacy_formats():
    """Forward-only change: pre-rounding rows must keep decoding byte-for-byte."""
    legacy_plain = json.dumps({"5000.0": {"net": 1.2345678901234e-05}})
    assert gh._decode_grid(legacy_plain)[5000.0]["net"] == 1.2345678901234e-05
    legacy_zlib = zlib.compress(legacy_plain.encode("utf-8"))
    assert gh._decode_grid(legacy_zlib)[5000.0]["net"] == 1.2345678901234e-05


# ------------------------------------------------------------------ vacuum ---

def _seeded_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "gex.db"))
    conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
    gh.init_schema(conn)
    return conn


def _free_pages(conn):
    return conn.execute("PRAGMA freelist_count").fetchone()[0]


def _page_count(conn):
    return conn.execute("PRAGMA page_count").fetchone()[0]


def _seed_rows(conn):
    """8 session-days of bulky rows (3 fall outside a keep_sessions=5 window)."""
    day = 86400
    base = 1700000000 - (30 * day)
    big = {float(i): {"net": float(i) * 1.5} for i in range(400)}
    for d in range(8):
        for t in range(40):
            conn.execute(
                "INSERT INTO snapshots (symbol, view, ts, spot, gex_json) VALUES (?,?,?,?,?)",
                ("SPY", "gex", base + d * day + t * 60, 500.0, gh._encode_grid(big)),
            )
    conn.commit()


def test_purge_shrinks_the_file_not_just_the_freelist(tmp_path):
    """The whole point: a DELETE alone leaves page_count flat (pages go to the
    freelist); the bounded incremental_vacuum must actually truncate the file."""
    conn = _seeded_db(tmp_path)
    _seed_rows(conn)
    before_pages = _page_count(conn)

    deleted = gh.purge_keep_sessions(conn, keep_sessions=5)
    assert deleted > 0

    after_pages = _page_count(conn)
    assert after_pages < before_pages, (
        f"file did not shrink ({before_pages} -> {after_pages} pages) - "
        "incremental_vacuum did not run")
    # And the freed pages were genuinely returned, not parked on the freelist.
    assert _free_pages(conn) < 100


def test_purge_without_vacuum_would_not_shrink(tmp_path):
    """Control: proves the assertion above is testing the vacuum, not the DELETE.

    An identical DB purged by raw DELETE keeps its page_count and accumulates a
    large freelist - which is exactly the 797 MB backlog found on the live DB."""
    conn = _seeded_db(tmp_path)
    _seed_rows(conn)
    before_pages = _page_count(conn)

    conn.execute("DELETE FROM snapshots WHERE ts < ?", (1700000000 - (25 * 86400),))
    conn.commit()

    assert _page_count(conn) == before_pages, "raw DELETE unexpectedly shrank the file"
    assert _free_pages(conn) > 100, "expected a large freelist after a bulk DELETE"


def test_purge_is_bounded_and_never_full_vacuums(tmp_path):
    """A bounded reclaim must not hold a long lock; VACUUM must never be auto-run."""
    import inspect
    src = inspect.getsource(gh.purge_keep_sessions)
    assert "incremental_vacuum" in src
    # A bare "VACUUM" (the multi-minute, whole-file lock) must stay out of this path.
    assert "conn.execute(\"VACUUM\")" not in src and "execute('VACUUM')" not in src
    assert gh._VACUUM_MAX_PAGES > 0


def test_purge_no_op_when_nothing_to_delete(tmp_path):
    """No retention work => no vacuum work, and definitely no exception."""
    conn = _seeded_db(tmp_path)
    conn.execute(
        "INSERT INTO snapshots (symbol, view, ts, spot, gex_json) VALUES (?,?,?,?,?)",
        ("SPY", "gex", 1700000000, 500.0, gh._encode_grid({1.0: {"net": 1.0}})),
    )
    conn.commit()
    assert gh.purge_keep_sessions(conn, keep_sessions=5) == 0


def test_purge_survives_db_without_incremental_autovacuum(tmp_path):
    """auto_vacuum=NONE makes incremental_vacuum a no-op; purge must still work."""
    conn = sqlite3.connect(str(tmp_path / "plain.db"))
    conn.execute("PRAGMA auto_vacuum=NONE")
    gh.init_schema(conn)
    day = 86400
    base = 1700000000 - (30 * day)
    for d in range(8):
        conn.execute(
            "INSERT INTO snapshots (symbol, view, ts, spot, gex_json) VALUES (?,?,?,?,?)",
            ("SPY", "gex", base + d * day, 500.0, gh._encode_grid({1.0: {"net": 1.0}})),
        )
    conn.commit()
    assert gh.purge_keep_sessions(conn, keep_sessions=5) > 0
