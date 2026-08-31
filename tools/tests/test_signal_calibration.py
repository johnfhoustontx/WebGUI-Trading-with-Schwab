"""Tests for the calibration CLI -- SQL, rendering, argument wiring.

The bucket arithmetic moved to ``shared/calibration.py`` on 2026-08-25 (a second
consumer arrived: ``options_svc``), and its tests moved with it to
``shared/tests/test_calibration.py``. What stays here is what this file still
owns: reading the database, rendering the table, and the ``main`` wiring.

Run from the repo root:
    .venv\\Scripts\\python -m pytest tools\\tests\\test_signal_calibration.py -v
"""
import itertools
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

    # A Tuesday, mid-session. `_db` defaults to it so the tests that are not
    # about the session filter are unaffected by it.
    RTH_TS = "2026-06-16T10:14:02.101010-05:00"
    # A sentinel, NOT None: an explicit first_seen_ts=None has to reach the
    # INSERT as a SQL NULL, which is a legacy row shape worth covering.
    DEFAULT_TS = object()

    @staticmethod
    def _db(tmp_path, *, with_outcome=True, first_seen_ts=DEFAULT_TS):
        p = tmp_path / "signals.db"
        conn = sqlite3.connect(p)
        conn.executescript("""
            CREATE TABLE signals (signal_id TEXT PRIMARY KEY, entry_grade TEXT,
              entry_score REAL, entry_credit REAL, entry_max_loss REAL,
              entry_short_delta REAL, entry_iv_rank REAL, width REAL,
              dte_at_entry INT, scanner_type TEXT, strategy TEXT, symbol TEXT,
              first_seen_date TEXT, first_seen_ts TEXT);
            CREATE TABLE signal_outcomes (signal_id TEXT PRIMARY KEY, realized_pnl REAL,
              exit_reason TEXT, close_date TEXT);
        """)
        conn.execute(
            "INSERT INTO signals VALUES "
            "('a','Good',63.1,0.56,1.44,-0.221,94.5,2.0,4,'0DTE','CCS','QQQ',"
            "'2026-06-14',?)",
            (TestLoadRows.RTH_TS if first_seen_ts is TestLoadRows.DEFAULT_TS
             else first_seen_ts,))
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


class TestOnlyInSessionCapturesAreCalibrated:
    """Signals captured outside the regular cash session are excluded from the
    sample.

    They are not merely mistimed, they are mispriced: Schwab pins a chain's
    `underlyingPrice` to the PRIOR CLOSE outside regular hours, so a pre-open
    scan chose its strikes, delta and credit off yesterday's price and the open
    then gapped away from all of them. Measured in prod on 2026-08-30, **223 of
    819** closed rows in this join were captured out of session — a 27%
    contaminated sample feeding the one independent estimate of `p` the app has.

    `signal_recorder` refuses such captures now, so this filter is about the
    HISTORY that predates the gate. It is a read-side filter and nothing is
    deleted: the rows stay in signals.db for audit and for the paper record.
    """

    IN_SESSION = "2026-06-16T10:14:02.101010-05:00"   # Tuesday 10:14 CT
    PRE_OPEN = "2026-06-16T08:02:33.270199-05:00"     # the 08:00 scan slot
    POST_CLOSE = "2026-06-16T15:02:29.741769-05:00"   # the 15:00 scan slot
    SATURDAY = "2026-06-20T10:14:02.101010-05:00"     # a manual weekend scan

    _seq = itertools.count()

    def _db(self, tmp_path, ts):
        """A fresh database per call — `TestLoadRows._db` is not idempotent (it
        CREATEs), and several tests here need two of them to compare."""
        d = tmp_path / f"db{next(self._seq)}"
        d.mkdir()
        return TestLoadRows._db(d, first_seen_ts=ts)

    @pytest.mark.parametrize("ts", [PRE_OPEN, POST_CLOSE, SATURDAY],
                             ids=["pre_open", "post_close", "weekend"])
    def test_an_out_of_session_capture_is_excluded(self, tmp_path, ts):
        assert C.load_rows(self._db(tmp_path, ts)) == []

    def test_an_in_session_capture_is_kept(self, tmp_path):
        assert len(C.load_rows(self._db(tmp_path, self.IN_SESSION))) == 1

    def test_the_weekend_case_needs_no_rule_of_its_own(self, tmp_path):
        """A time-of-day comparison would keep a Saturday 10:14 capture. The
        filter reuses `market_calendar.is_regular_hours`, which is trading-day
        gated, so weekends and holidays come free rather than as a second rule
        that can drift from the first."""
        assert C.load_rows(self._db(tmp_path, self.SATURDAY)) == []
        assert len(C.load_rows(self._db(tmp_path, self.IN_SESSION))) == 1

    def test_a_timestamp_that_cannot_be_read_is_excluded(self, tmp_path):
        """The filter's contract is 'every row is a PROVEN in-session capture',
        so an unreadable stamp fails it. Prod has none — all 855 rows carry the
        same 32-char ISO form — but the policy has to be stated, not left to
        whichever way `fromisoformat` happens to fall over."""
        for bad in ("", "not-a-timestamp", None):   # None reaches SQL as NULL
            assert C.load_rows(self._db(tmp_path, bad)) == [], f"kept {bad!r}"

    def test_the_history_is_still_reachable_on_request(self, tmp_path):
        """Excluding by default must not mean the rows become unreadable — a
        before/after comparison is exactly how you check what this filter did."""
        db = self._db(tmp_path, self.PRE_OPEN)
        assert C.load_rows(db) == []
        assert len(C.load_rows(db, regular_hours_only=False)) == 1

    def test_it_composes_with_the_where_clause(self, tmp_path):
        """The session filter and the SQL predicate are ANDed, not alternatives."""
        db = self._db(tmp_path, self.IN_SESSION)
        assert len(C.load_rows(db, "o.close_date >= ?", ("2026-01-01",))) == 1
        assert C.load_rows(db, "o.close_date >= ?", ("2026-07-01",)) == []

    def test_the_cli_can_ask_for_the_unfiltered_history(self, tmp_path, capsys):
        db = self._db(tmp_path, self.PRE_OPEN)
        assert C.main(["--db", str(db)]) == 0
        assert "no closed signals" in capsys.readouterr().out

        assert C.main(["--db", str(db), "--include-out-of-hours"]) == 0
        assert "no closed signals" not in capsys.readouterr().out


class TestTheNightlyCacheInheritsTheFilter:
    """The CLI report is a research tool; `cache:options:calibration` is what the
    Trade detail panel actually shows. Filtering `load_rows` covers both, and
    this is the test that says so — a filter applied only in the CLI would leave
    the published EV reading off the contaminated sample."""

    def test_load_and_build_excludes_out_of_session_rows(self, tmp_path):
        from services.options_svc.calibration import load_and_build

        first = tmp_path / "out"
        first.mkdir()
        db = TestLoadRows._db(first, first_seen_ts="2026-06-16T08:02:33.270199-05:00")
        assert load_and_build(db, min_n=1)["rows"] == 0

        second = tmp_path / "in"
        second.mkdir()
        db2 = TestLoadRows._db(second, first_seen_ts="2026-06-16T10:14:02.101010-05:00")
        assert load_and_build(db2, min_n=1)["rows"] == 1
