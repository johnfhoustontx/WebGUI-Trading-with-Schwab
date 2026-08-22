"""Tests for the recommendation journal (Phase 1, task 1.4).

The journal is the forward-accruing record every later validation rests on: it
costs almost nothing to write now and cannot be backfilled, because it stores
what the model said *at the time*. The labeler and the live-IC monitor are
Phase 6 — this phase only has to get the write side and the schema right.

Every test passes an explicit ``tmp_path`` database. The store must never touch
the real file under pytest (this repo has a documented incident where a suite
wrote into live data).
"""
import sqlite3

import pytest

from services.trade_svc import rec_journal


@pytest.fixture
def conn(tmp_path):
    c = rec_journal.init_db(tmp_path / "rec.db")
    yield c
    rec_journal.close_db(c)


def _reading(**over):
    base = dict(symbol="AAPL", reading_date="2026-08-22", price=309.69,
                composite=0.096, band=3, percentile=70,
                swing_verdict="HOLD", position_verdict="HOLD",
                investor_verdict="HOLD", investor_score=17,
                gates="", model_version="2026-08-22")
    base.update(over)
    return base


def test_init_db_is_idempotent(tmp_path):
    """Re-opening an existing store must not raise or drop anything — the
    service calls this on every write."""
    path = tmp_path / "rec.db"
    c1 = rec_journal.init_db(path)
    rec_journal.record(c1, _reading())
    rec_journal.close_db(c1)

    c2 = rec_journal.init_db(path)
    assert len(rec_journal.readings(c2)) == 1
    rec_journal.close_db(c2)


def test_record_round_trips_every_field(conn):
    rec_journal.record(conn, _reading())
    rows = rec_journal.readings(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "AAPL"
    assert r["percentile"] == 70
    assert r["composite"] == pytest.approx(0.096)
    assert r["model_version"] == "2026-08-22"


def test_one_reading_per_symbol_per_day_the_last_one_wins(conn):
    """A symbol analyzed five times in a day must not cast five votes in the
    IC — that would silently overweight whatever you happened to look at most.
    The row is keyed (symbol, reading_date) and the latest read replaces it."""
    rec_journal.record(conn, _reading(percentile=70, composite=0.096))
    rec_journal.record(conn, _reading(percentile=74, composite=0.140))

    rows = rec_journal.readings(conn)
    assert len(rows) == 1
    assert rows[0]["percentile"] == 74


def test_different_days_and_symbols_are_separate_rows(conn):
    rec_journal.record(conn, _reading())
    rec_journal.record(conn, _reading(reading_date="2026-08-21"))
    rec_journal.record(conn, _reading(symbol="MSFT"))
    assert len(rec_journal.readings(conn)) == 3


def test_forward_return_columns_exist_and_start_null(conn):
    """Phase 6's labeler fills these in once the horizon matures. They must
    exist NOW so that filling them is an UPDATE, not a migration of rows that
    were written without them."""
    rec_journal.record(conn, _reading())
    r = rec_journal.readings(conn)[0]
    for col in ("fwd_5d", "fwd_10d", "fwd_20d", "labeled_at"):
        assert col in r.keys()
        assert r[col] is None


def test_unlabeled_finds_only_rows_still_missing_a_label(conn):
    rec_journal.record(conn, _reading(symbol="AAPL"))
    rec_journal.record(conn, _reading(symbol="MSFT"))
    rec_journal.apply_label(conn, "AAPL", "2026-08-22",
                            fwd_5d=0.01, fwd_10d=0.02, fwd_20d=0.03)

    pending = rec_journal.unlabeled(conn)
    assert [p["symbol"] for p in pending] == ["MSFT"]

    done = [r for r in rec_journal.readings(conn) if r["symbol"] == "AAPL"][0]
    assert done["fwd_20d"] == pytest.approx(0.03)
    assert done["labeled_at"] is not None


def test_record_never_raises_into_the_caller(conn):
    """``analyze`` calls this for its side effect only. A journal failure must
    never cost the user their analysis — the same contract iv_history has."""
    conn.close()                      # every write from here on will fail
    assert rec_journal.record(conn, _reading()) is False


def test_a_reading_missing_optional_fields_still_records(conn):
    """A degraded analysis (no swing block, no fundamentals) is exactly the
    reading worth keeping — it records what the model could not say."""
    assert rec_journal.record(conn, {"symbol": "ZZZZ",
                                     "reading_date": "2026-08-22"}) is True
    r = rec_journal.readings(conn)[0]
    assert r["symbol"] == "ZZZZ"
    assert r["composite"] is None and r["percentile"] is None


def test_the_default_path_is_under_the_service_data_dir():
    """Kept out of the repo root and beside the other trade_svc store, so the
    gitignore that already covers service data covers this too."""
    p = rec_journal.DEFAULT_DB_PATH
    assert p.name == "rec_journal.db"
    assert p.parent.name == "data" and p.parent.parent.name == "trade_svc"


def test_sqlite_row_factory_so_callers_read_by_name(conn):
    rec_journal.record(conn, _reading())
    assert isinstance(rec_journal.readings(conn)[0], sqlite3.Row)


# ── Phase 6: the labeler needs more than a raw forward return ────────────────
# Phase 4 measured this model at cross-sectional IC +0.16 when the market rises
# and −0.11 when it falls: its edge is beta. A live monitor that scored itself on
# the RAW forward excess would therefore report a healthy IC through any rising
# market and reproduce exactly the illusion Phase 4 dismantled. So the journal
# stores the beta-adjusted forward and the market's own forward beside the raw
# one, and the monitor can split on them.

class TestTheLabelColumns:
    def test_the_beta_aware_columns_exist(self, tmp_path):
        conn = rec_journal.init_db(tmp_path / "j.db")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(readings)")}
        assert {"fwd_5d_ba", "fwd_10d_ba", "fwd_20d_ba"} <= cols
        assert {"mkt_fwd_5d", "mkt_fwd_10d", "mkt_fwd_20d"} <= cols
        assert "beta" in cols

    def test_a_db_created_by_the_OLD_schema_is_migrated_in_place(self, tmp_path):
        """The store cannot be backfilled, so it must never be recreated. An
        existing journal has to gain the columns and keep its rows."""
        import sqlite3
        p = tmp_path / "old.db"
        old = sqlite3.connect(str(p))
        old.executescript("""
            CREATE TABLE readings (
                symbol TEXT NOT NULL, reading_date TEXT NOT NULL,
                recorded_at TEXT, price REAL, composite REAL, band INTEGER,
                percentile INTEGER, swing_verdict TEXT, position_verdict TEXT,
                investor_verdict TEXT, investor_score INTEGER, gates TEXT,
                model_version TEXT, fwd_5d REAL, fwd_10d REAL, fwd_20d REAL,
                labeled_at TEXT, PRIMARY KEY (symbol, reading_date));""")
        old.execute("INSERT INTO readings (symbol, reading_date, composite) "
                    "VALUES ('AAPL', '2026-08-01', 0.5)")
        old.commit()
        old.close()

        conn = rec_journal.init_db(p)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(readings)")}
        assert "fwd_20d_ba" in cols and "beta" in cols
        row = conn.execute("SELECT * FROM readings").fetchone()
        assert row["symbol"] == "AAPL" and row["composite"] == 0.5

    def test_migration_is_idempotent(self, tmp_path):
        p = tmp_path / "j.db"
        rec_journal.close_db(rec_journal.init_db(p))
        conn = rec_journal.init_db(p)          # second open must not raise
        assert conn.execute("SELECT 1").fetchone()[0] == 1


class TestApplyLabelCarriesTheBetaAwareFields:
    def test_it_stores_every_field_it_is_given(self, tmp_path):
        conn = rec_journal.init_db(tmp_path / "j.db")
        rec_journal.record(conn, {"symbol": "AAPL", "reading_date": "2026-08-01",
                         "composite": 0.4})
        rec_journal.apply_label(conn, "AAPL", "2026-08-01", fwd_5d=0.01, fwd_20d=0.03,
                       fwd_20d_ba=0.012, mkt_fwd_20d=0.02, beta=1.4)
        row = conn.execute("SELECT * FROM readings").fetchone()
        assert row["fwd_20d"] == 0.03
        assert row["fwd_20d_ba"] == 0.012
        assert row["mkt_fwd_20d"] == 0.02
        assert row["beta"] == 1.4
        assert row["labeled_at"]

    def test_a_partial_label_leaves_the_others_NULL_rather_than_zero(self):
        """A horizon that has not matured yet is unknown, and 0.0 is a
        measurement. The monitor must be able to tell them apart."""
        conn = rec_journal.init_db(":memory:")
        rec_journal.record(conn, {"symbol": "X", "reading_date": "2026-08-01"})
        rec_journal.apply_label(conn, "X", "2026-08-01", fwd_5d=0.01)
        row = conn.execute("SELECT * FROM readings").fetchone()
        assert row["fwd_5d"] == 0.01
        assert row["fwd_20d"] is None and row["fwd_20d_ba"] is None
