"""Tests for the debate-replay SAMPLER.

These exist because the first version of the harness sampled
``ORDER BY first_seen_date DESC LIMIT N`` — the newest closed signals — and
that is a **survivorship filter**, not a sample.

Within any recent window the trades that have ALREADY closed are the ones that
closed fastest, and the ones that close fastest are the stop-outs; the winners
are still open, waiting on expiry. Measured against prod: the settled
population wins **80.9%**, the unsettled tail wins **13.2%**, and the 150
"newest closed" the harness actually drew won **43.3%**. The first batch, being
the newest 25, came back at **4%** — a sample in which vetoing everything is
96% accurate by construction.

The original suite had a test asserting the newest-first behaviour was correct
(`--limit 60 must mean the most recent 60`). It passed, and it pinned the bug —
a characterization test records what the code does, not what it should do. The
tests here pin the property that actually matters:

  * a signal is only eligible once its COHORT has settled, and the cutoff is
    derived from the data (the earliest still-open signal), never hardcoded;
  * a capped sample is drawn at RANDOM from the eligible set, seeded so a run
    is reproducible and a prompt set can be regenerated;
  * the report carries the POPULATION base rate next to the sample's, so a
    future sampling bug is visible on the face of the output instead of being
    mistaken for a market regime.

Run from the repo root:
    .venv\\Scripts\\python -m pytest tools\\tests\\test_replay_sampling.py -v
"""
import sqlite3

import pytest

from tools import replay_debate as RD
from tools import replay_scoring as RS

SCHEMA = """
CREATE TABLE signals (
  signal_id TEXT PRIMARY KEY, scanner_type TEXT, symbol TEXT, strategy TEXT,
  short_strike REAL, long_strike REAL, width REAL, expiration TEXT,
  dte_at_entry INTEGER, entry_credit REAL, entry_max_loss REAL,
  entry_score REAL, entry_grade TEXT, entry_short_delta REAL,
  entry_iv_rank REAL, entry_underlying REAL, first_seen_date TEXT,
  status TEXT, mode TEXT);
CREATE TABLE signal_outcomes (
  signal_id TEXT PRIMARY KEY, close_date TEXT, realized_pnl REAL,
  exit_reason TEXT);
"""


def _db(tmp_path, rows, outcomes):
    p = tmp_path / "signals.db"
    c = sqlite3.connect(p)
    c.executescript(SCHEMA)
    for sid, date, grade, scanner in rows:
        c.execute("INSERT INTO signals VALUES (" + ",".join("?" * 19) + ")",
                  (sid, scanner, "AAPL", "PCS", 180, 175, 5, "2026-09-04", 10,
                   1.25, 3.75, 70.0, grade, 0.22, 45.0, 190.0, date,
                   "x", "paper"))
    for sid, pnl in outcomes:
        c.execute("INSERT INTO signal_outcomes VALUES (?,?,?,?)",
                  (sid, "2026-09-01", pnl, "EXPIRED"))
    c.commit()
    c.close()
    return p


@pytest.fixture
def mixed_db(tmp_path):
    """Old settled winners, plus a recent tail that is open-or-just-stopped.

    This is prod's shape in miniature: the recent window's closed rows are all
    losers because its winners have not closed yet."""
    rows = [(f"old{i:04d}", "2026-06-10", "Good", "SWING") for i in range(20)]
    rows += [(f"new{i:04d}", "2026-08-20", "Good", "0DTE") for i in range(6)]
    outcomes = [(f"old{i:04d}", 100.0) for i in range(20)]        # all won
    outcomes += [(f"new{i:04d}", -100.0) for i in range(3)]       # fast losers
    # new0003..new0005 are still OPEN -> they define the settlement cutoff
    return _db(tmp_path, rows, outcomes)


# ── the settlement cutoff ───────────────────────────────────────────────────

def test_cutoff_is_the_earliest_still_open_signal(mixed_db):
    conn = RD.open_signals(mixed_db)
    assert RD.settlement_cutoff(conn) == "2026-08-20"


def test_no_open_signals_means_nothing_is_excluded(tmp_path):
    p = _db(tmp_path, [("a", "2026-06-10", "Good", "SWING")], [("a", 50.0)])
    conn = RD.open_signals(p)
    assert RD.settlement_cutoff(conn) is None
    assert len(RD.load_cases(conn)) == 1


def test_load_cases_excludes_the_unsettled_tail_by_default(mixed_db):
    """The three closed losers in the recent window are NOT eligible: their
    cohort's winners have not closed yet, so including them measures
    settlement speed rather than trade quality."""
    conn = RD.open_signals(mixed_db)
    cases = RD.load_cases(conn)
    assert len(cases) == 20
    assert all(c["signal_id"].startswith("old") for c in cases)


def test_the_unsettled_tail_can_be_included_but_only_on_purpose(mixed_db):
    conn = RD.open_signals(mixed_db)
    assert len(RD.load_cases(conn, include_unsettled=True)) == 23


def test_excluding_the_tail_moves_the_base_rate_to_the_honest_one(mixed_db):
    """The whole point, stated as an assertion: 100% vs 87%."""
    conn = RD.open_signals(mixed_db)
    settled = RS.score([dict(c, verdict="TAKE") for c in RD.load_cases(conn)])
    biased = RS.score([dict(c, verdict="TAKE")
                       for c in RD.load_cases(conn, include_unsettled=True)])
    assert settled["base_rate"] == pytest.approx(1.0)
    assert biased["base_rate"] == pytest.approx(20 / 23)
    assert biased["base_rate"] < settled["base_rate"]


# ── drawing a capped sample ─────────────────────────────────────────────────

def test_a_capped_sample_is_random_not_the_newest(tmp_path):
    """Newest-first was the original bug. A cap must not reintroduce it."""
    rows = [(f"s{i:04d}", f"2026-06-{1 + i % 28:02d}", "Good", "SWING")
            for i in range(60)]
    p = _db(tmp_path, rows, [(f"s{i:04d}", 10.0) for i in range(60)])
    conn = RD.open_signals(p)
    drawn = [c["signal_id"] for c in RD.load_cases(conn, limit=10)]
    newest = [c["signal_id"] for c in RD.load_cases(conn)][:10]
    assert len(drawn) == 10
    assert drawn != newest, "a capped sample must not just be the newest rows"


def test_the_sample_is_reproducible_for_a_given_seed(tmp_path):
    rows = [(f"s{i:04d}", "2026-06-10", "Good", "SWING") for i in range(60)]
    p = _db(tmp_path, rows, [(f"s{i:04d}", 10.0) for i in range(60)])
    conn = RD.open_signals(p)
    a = [c["signal_id"] for c in RD.load_cases(conn, limit=10, seed=7)]
    b = [c["signal_id"] for c in RD.load_cases(conn, limit=10, seed=7)]
    c_ = [c["signal_id"] for c in RD.load_cases(conn, limit=10, seed=8)]
    assert a == b, "same seed must redraw the same prompts"
    assert a != c_, "a different seed must draw a different sample"


def test_a_limit_beyond_the_population_returns_everything(mixed_db):
    conn = RD.open_signals(mixed_db)
    assert len(RD.load_cases(conn, limit=999)) == 20


# ── the report must expose a future sampling bug ────────────────────────────

def test_score_carries_the_population_base_rate_for_comparison():
    cases = [{"entry_grade": "Good", "verdict": "TAKE", "outcome": "loss"}
             for _ in range(24)]
    cases.append({"entry_grade": "Good", "verdict": "TAKE", "outcome": "win"})
    s = RS.score(cases, population_base_rate=0.809)
    assert s["population_base_rate"] == pytest.approx(0.809)
    assert s["sample_is_unrepresentative"] is True


def test_a_representative_sample_is_not_flagged():
    cases = [{"entry_grade": "Good", "verdict": "TAKE", "outcome": "win"}
             for _ in range(20)]
    cases += [{"entry_grade": "Good", "verdict": "TAKE", "outcome": "loss"}
              for _ in range(5)]
    s = RS.score(cases, population_base_rate=0.809)
    assert s["sample_is_unrepresentative"] is False


def test_no_population_rate_means_no_claim_either_way():
    s = RS.score([{"entry_grade": "Good", "verdict": "TAKE", "outcome": "win"}])
    assert s["population_base_rate"] is None
    assert s["sample_is_unrepresentative"] is None


def test_render_report_warns_loudly_on_an_unrepresentative_sample():
    text = RD.render_report({
        "mode": "manual", "errors": 0, "model": "m", "ingest": {},
        "score": {"n": 25, "status": "degenerate_pass_all", "base_rate": 0.04,
                  "population_base_rate": 0.809,
                  "sample_is_unrepresentative": True,
                  "approval_rate": 0.04, "approved_rate": 0.0, "lift": -0.04,
                  "dropped": 0, "approved_n": 1,
                  "vetoed": {"n": 24, "would_have_won": 1,
                             "would_have_lost": 23, "accuracy": 0.958},
                  "approved": {"n": 1, "would_have_won": 0,
                               "would_have_lost": 1, "accuracy": None},
                  "by_grade": {}},
    })
    assert "UNREPRESENTATIVE" in text.upper()
    assert "80.9" in text
