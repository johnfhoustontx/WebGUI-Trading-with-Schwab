"""Tests for the nightly journal labeler script (Phase 6).

The script itself is I/O; what is worth pinning is the decisions it makes around
that I/O, because each one can silently corrupt a store that cannot be rebuilt:

  * it must not label without a market reference — every label is relative to
    SPY, so a missing SPY means "come back tomorrow", not "assume flat";
  * a symbol with no history stays UNLABELLED rather than being marked done;
  * `--dry-run` must write nothing;
  * `labeled_at` is what stops a row being revisited, so a row where nothing has
    matured must not be stamped.

Run from the repo root:
    .venv\\Scripts\\python -m pytest tools\\tests\\test_label_journal.py -v
"""
import pandas as pd
import pytest

from tools import label_journal as LJ
from services.trade_svc import rec_journal


def _closes(n=320, start="2025-06-02", step=1.0, base=100.0):
    """Long enough for a trailing beta.

    ⚠ `research.labels.rolling_beta` needs BETA_MIN_PERIODS (126) bars before it
    returns anything, so a short fixture yields beta=None and silently takes the
    "unknown beta" path — the adjusted label comes back None and the test looks
    like a code failure. The readings under test sit ~230 bars in."""
    idx = pd.bdate_range(start, periods=n)
    return pd.Series([base + i * step for i in range(n)], index=idx)


@pytest.fixture
def journal(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    monkeypatch.setattr(rec_journal, "DEFAULT_DB_PATH", db)
    conn = rec_journal.init_db(db)
    rec_journal.record(conn, {"symbol": "AAPL", "reading_date": "2026-05-04",
                              "composite": 0.5})
    rec_journal.record(conn, {"symbol": "MSFT", "reading_date": "2026-05-04",
                              "composite": -0.3})
    rec_journal.close_db(conn)
    return db


def _rows(db):
    conn = rec_journal.init_db(db)
    try:
        return {r["symbol"]: dict(r)
                for r in conn.execute("SELECT * FROM readings")}
    finally:
        rec_journal.close_db(conn)


class TestItLabels:
    def test_a_matured_reading_gains_all_three_labels(self, journal, monkeypatch):
        monkeypatch.setattr(LJ, "_history",
                            lambda s, years=2: _closes(step=1.0 if s != "SPY" else 0.5))
        LJ.run(log=lambda *_: None, db_path=journal)
        row = _rows(journal)["AAPL"]
        assert row["fwd_20d"] is not None
        assert row["fwd_20d_ba"] is not None
        assert row["mkt_fwd_20d"] is not None
        assert row["beta"] is not None
        assert row["labeled_at"]

    def test_dry_run_writes_nothing(self, journal, monkeypatch):
        monkeypatch.setattr(LJ, "_history", lambda s, years=2: _closes())
        LJ.run(dry_run=True, log=lambda *_: None, db_path=journal)
        assert _rows(journal)["AAPL"]["labeled_at"] is None


class TestItRefusesToGuess:
    def test_no_SPY_means_no_labelling_at_all(self, journal, monkeypatch):
        """Every label is relative to the market. Labelling without it would
        write numbers that mean something else under the same column names —
        and `labeled_at` would then stop anyone revisiting them."""
        monkeypatch.setattr(LJ, "_history",
                            lambda s, years=2: None if s == "SPY" else _closes())
        assert LJ.run(log=lambda *_: None, db_path=journal) == 0
        assert _rows(journal)["AAPL"]["labeled_at"] is None

    def test_a_symbol_with_no_history_stays_unlabelled(self, journal, monkeypatch):
        monkeypatch.setattr(
            LJ, "_history",
            lambda s, years=2: None if s == "MSFT" else _closes(
                step=1.0 if s != "SPY" else 0.5))
        LJ.run(log=lambda *_: None, db_path=journal)
        rows = _rows(journal)
        assert rows["AAPL"]["labeled_at"]
        assert rows["MSFT"]["labeled_at"] is None

    def test_a_reading_with_nothing_matured_is_not_stamped_done(self, journal,
                                                                monkeypatch):
        """`labeled_at` is a one-way door: stamping a row whose horizons have
        not matured would freeze it permanently unlabelled."""
        monkeypatch.setattr(LJ, "_history",
                            lambda s, years=2: _closes(n=3, start="2026-05-04"))
        LJ.run(log=lambda *_: None, db_path=journal)
        assert _rows(journal)["AAPL"]["labeled_at"] is None


class TestMaturityGate:
    def test_a_reading_from_today_is_not_even_considered(self, tmp_path,
                                                         monkeypatch):
        import datetime as dt
        db = tmp_path / "j.db"
        monkeypatch.setattr(rec_journal, "DEFAULT_DB_PATH", db)
        conn = rec_journal.init_db(db)
        rec_journal.record(conn, {"symbol": "NEW",
                                  "reading_date": dt.date.today().isoformat(),
                                  "composite": 0.1})
        rec_journal.close_db(conn)
        calls = []
        monkeypatch.setattr(LJ, "_history",
                            lambda s, years=2: (calls.append(s), _closes())[1])
        assert LJ.run(log=lambda *_: None, db_path=db) == 0
        assert calls == []          # not even SPY — there was nothing due


class TestItRefusesToTouchTheRealStoreFromATest:
    def test_run_with_no_db_path_is_skipped_under_pytest(self, monkeypatch):
        """The near-miss this guard exists for: `rec_journal.init_db`'s default
        argument is bound at DEFINITION, so monkeypatching the module attribute
        does not redirect it — an early version of these tests opened the real
        journal. The bus is fakeredis; SQLite is not."""
        opened = []
        monkeypatch.setattr(rec_journal, "init_db",
                            lambda p=None: opened.append(p))
        assert LJ.run(log=lambda *_: None) == 0
        assert opened == []
