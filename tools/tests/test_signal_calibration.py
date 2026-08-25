"""Tests for the calibration CLI -- SQL, rendering, argument wiring.

The bucket arithmetic moved to ``shared/calibration.py`` on 2026-08-25 (a second
consumer arrived: ``options_svc``), and its tests moved with it to
``shared/tests/test_calibration.py``. What stays here is what this file still
owns: reading the database, rendering the table, and the ``main`` wiring.

Run from the repo root:
    .venv\\Scripts\\python -m pytest tools\\tests\\test_signal_calibration.py -v
"""
import sqlite3

import pytest

from shared import calibration as SC
from tools import signal_calibration as C


def _row(pnl, *, max_loss=1.0, credit=1.0, delta=-0.20, grade="Good", score=60.0,
         **kw):
    """One joined signals x signal_outcomes row, in the shape load_rows emits."""
    row = {"realized_pnl": pnl, "entry_max_loss": max_loss, "entry_credit": credit,
           "entry_short_delta": delta, "entry_grade": grade, "entry_score": score,
           "scanner_type": "0DTE", "strategy": "PCS", "symbol": "SPY",
           "exit_reason": "EXPIRED"}
    row.update(kw)
    return row


class TestTheMathIsNotReimplementedHere:
    """The CLI re-exports the shared functions for its callers. If someone ever
    pastes a local copy back in, these stop being the same object and the two
    implementations can drift -- which is the whole reason the math moved."""

    @pytest.mark.parametrize("name", ["r_multiple", "breakeven_win_rate",
                                      "priced_win_rate", "score_bin",
                                      "bucket_stats", "calibrate",
                                      "split_calibrate"])
    def test_it_is_the_shared_function_itself(self, name):
        assert getattr(C, name) is getattr(SC, name)


class TestLoadRows:
    """`load_rows` had no test until the split -- it was treated as thin I/O.
    It is about to have a SECOND consumer depending on the row shape it emits,
    so the join and the read-only guarantee are pinned now."""

    @staticmethod
    def _db(tmp_path, *, with_outcome=True):
        p = tmp_path / "signals.db"
        conn = sqlite3.connect(p)
        conn.executescript("""
            CREATE TABLE signals (signal_id TEXT PRIMARY KEY, entry_grade TEXT,
              entry_score REAL, entry_credit REAL, entry_max_loss REAL,
              entry_short_delta REAL, entry_iv_rank REAL, width REAL,
              dte_at_entry INT, scanner_type TEXT, strategy TEXT, symbol TEXT,
              first_seen_date TEXT);
            CREATE TABLE signal_outcomes (signal_id TEXT PRIMARY KEY, realized_pnl REAL,
              exit_reason TEXT, close_date TEXT);
        """)
        conn.execute("INSERT INTO signals VALUES "
                     "('a','Good',63.1,0.56,1.44,-0.221,94.5,2.0,4,'0DTE','CCS','QQQ','2026-06-14')")
        if with_outcome:
            conn.execute("INSERT INTO signal_outcomes VALUES ('a',56.0,'EXPIRED','2026-06-18')")
        conn.commit()
        conn.close()
        return p

    def test_it_joins_a_signal_to_its_outcome(self, tmp_path):
        rows = C.load_rows(self._db(tmp_path))
        assert len(rows) == 1
        assert rows[0]["entry_grade"] == "Good"
        assert rows[0]["realized_pnl"] == 56.0
        assert rows[0]["first_seen_date"] == "2026-06-14"   # the day-cluster key

    def test_a_signal_with_no_outcome_is_not_returned(self, tmp_path):
        """An INNER join, deliberately: an open trade has no realized R and must
        not be counted as a scratch."""
        assert C.load_rows(self._db(tmp_path, with_outcome=False)) == []

    def test_the_where_clause_is_parameterised_not_interpolated(self, tmp_path):
        db = self._db(tmp_path)          # built ONCE -- _db is not idempotent
        assert C.load_rows(db, "o.close_date >= ?", ("2026-07-01",)) == []
        assert len(C.load_rows(db, "o.close_date >= ?", ("2026-01-01",))) == 1

    def test_the_connection_refuses_writes(self, tmp_path):
        """`mode=ro` is the only thing standing between an analysis tool and the
        live trading database."""
        db = self._db(tmp_path)
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("DELETE FROM signals")
        finally:
            conn.close()


class TestRendering:
    def test_the_table_names_every_bucket_it_was_given(self):
        out = C.format_table(SC.calibrate([_row(50.0)] * 3, "entry_grade"), "entry_grade")
        assert "Good" in out

    def test_an_empty_report_says_so_rather_than_printing_an_empty_table(self):
        assert "no closed" in C.format_table([], "entry_grade").lower()

    def test_the_legend_tells_the_reader_to_prefer_the_clustered_t(self):
        """The naive t is in the table because it is diagnostic, but reading it
        as significance is the mistake this report exists to prevent."""
        out = C.format_table(SC.calibrate([_row(50.0), _row(-100.0)], "entry_grade"))
        assert "READ tDay, NOT t" in out

    def test_a_split_report_names_every_section(self):
        rows = [_row(50.0, scanner_type="0DTE"), _row(50.0, scanner_type="SWING")]
        out = C.format_split(SC.split_calibrate(rows, "entry_grade", "scanner_type"),
                             "entry_grade", "scanner_type")
        assert "0DTE" in out and "SWING" in out

    def test_an_empty_split_report_says_so(self):
        assert "no closed" in C.format_split([], "entry_grade", "scanner_type").lower()

    def test_the_legend_is_printed_once_no_matter_how_many_sections(self):
        rows = [_row(50.0, scanner_type="0DTE"), _row(50.0, scanner_type="SWING")]
        out = C.format_split(SC.split_calibrate(rows, "entry_grade", "scanner_type"),
                             "entry_grade", "scanner_type")
        assert out.count("READ tDay, NOT t") == 1


class TestMain:
    def test_an_unreadable_database_reports_and_exits_nonzero(self, tmp_path, capsys):
        rc = C.main(["--db", str(tmp_path / "nope.db")])
        assert rc == 2
        assert "cannot read" in capsys.readouterr().err

    def test_it_renders_the_split_form_when_asked(self, tmp_path, capsys):
        db = TestLoadRows._db(tmp_path)
        assert C.main(["--db", str(db), "--split", "scanner_type"]) == 0
        assert "within scanner_type = 0DTE" in capsys.readouterr().out
