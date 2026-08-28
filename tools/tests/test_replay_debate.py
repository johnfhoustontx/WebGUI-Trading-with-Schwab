"""Tests for the debate-replay harness's I/O decisions.

The arithmetic is pinned in ``test_replay_scoring``. What is worth pinning here
is everything that can either corrupt a store or spend money by accident:

  * the signals DB is opened **read-only** — it is prod's live file;
  * ``--live`` is the ONLY path that reaches the network, and the default is a
    clearly-labelled stub, so a mistyped command costs nothing;
  * a cached response is reused, so re-running the report is free;
  * the sample is closed signals only, and the source file is never written.

Run from the repo root:
    .venv\\Scripts\\python -m pytest tools\\tests\\test_replay_debate.py -v
"""
import sqlite3

import pytest

from tools import replay_debate as RD


# ── a throwaway signals.db shaped like prod's ───────────────────────────────

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


@pytest.fixture
def signals_db(tmp_path):
    p = tmp_path / "signals.db"
    c = sqlite3.connect(p)
    c.executescript(SCHEMA)
    rows = [
        ("0043e544", "SWING", "AAPL", "put_credit_spread", 180, 175, 5, "2026-07-18", 30,
         1.25, 3.75, 72.0, "Good", 0.22, 45.0, 190.0, "2026-06-18", "closed", "paper"),
        ("00535765", "0DTE", "SPY", "call_credit_spread", 600, 605, 5, "2026-06-20", 0,
         0.80, 4.20, 61.0, "Marginal", 0.18, 30.0, 594.0, "2026-06-20", "closed", "paper"),
        ("c15f326b", "SWING", "MSFT", "put_credit_spread", 400, 395, 5, "2026-08-01", 25,
         1.10, 3.90, 68.0, "Good", 0.25, 52.0, 415.0, "2026-07-07", "open", "paper"),
    ]
    c.executemany("INSERT INTO signals VALUES (" + ",".join("?" * 19) + ")", rows)
    c.executemany("INSERT INTO signal_outcomes VALUES (?,?,?,?)", [
        ("0043e544", "2026-07-18", 125.0, "EXPIRED"),
        ("00535765", "2026-06-20", -320.0, "MONEY_STOP"),
        # the MSFT signal is still open — no outcome row
    ])
    c.commit()
    c.close()
    return p


# ── the source file is read-only ────────────────────────────────────────────

def test_signals_db_is_opened_read_only(signals_db):
    """This points at prod's live signals.db. A write must be impossible."""
    conn = RD.open_signals(signals_db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("UPDATE signals SET entry_grade='Strong'")


def test_a_full_run_leaves_the_source_file_byte_identical(signals_db, tmp_path):
    before = signals_db.read_bytes()
    RD.run(signals_db=signals_db, cache_db=tmp_path / "c.db", live=False,
           log=lambda *a: None)
    assert signals_db.read_bytes() == before


# ── sample selection ────────────────────────────────────────────────────────

def test_load_cases_takes_only_closed_signals(signals_db):
    """Signal 3 is open — it has no outcome, so it cannot be scored."""
    conn = RD.open_signals(signals_db)
    cases = RD.load_cases(conn)
    assert sorted(c["signal_id"] for c in cases) == ["0043e544", "00535765"]
    assert all(c["outcome"] in ("win", "loss") for c in cases)


def test_load_cases_returns_newest_first_so_limit_takes_a_recent_sample(signals_db):
    """``--limit 60`` must mean the most recent 60, not the oldest 60."""
    conn = RD.open_signals(signals_db)
    assert [c["signal_id"] for c in RD.load_cases(conn)] == ["00535765", "0043e544"]


def test_load_cases_carries_grade_and_outcome_outside_the_debater_view(signals_db):
    """The grade and outcome must travel with the case for SCORING, but must
    not be inside the payload the prompt is built from."""
    conn = RD.open_signals(signals_db)
    case = next(c for c in RD.load_cases(conn) if c["signal_id"] == "0043e544")
    assert case["entry_grade"] == "Good" and case["outcome"] == "win"
    assert "entry_grade" not in case["view"]
    assert "outcome" not in case["view"]
    assert "realized_pnl" not in repr(case["view"])


def test_load_cases_filters_by_scanner_and_limit(signals_db):
    conn = RD.open_signals(signals_db)
    assert [c["symbol"] for c in RD.load_cases(conn, scanner="0DTE")] == ["SPY"]
    assert len(RD.load_cases(conn, limit=1)) == 1


# ── spending is opt-in ──────────────────────────────────────────────────────

def test_default_run_never_builds_a_network_client(signals_db, tmp_path, monkeypatch):
    """A mistyped command must not cost money."""
    def boom():
        raise AssertionError("built an Anthropic client without --live")
    monkeypatch.setattr(RD, "_make_client", boom)
    report = RD.run(signals_db=signals_db, cache_db=tmp_path / "c.db",
                    live=False, log=lambda *a: None)
    assert report["mode"] == "stub"


def test_stub_mode_is_labelled_so_no_one_reads_it_as_a_result(signals_db, tmp_path):
    lines = []
    RD.run(signals_db=signals_db, cache_db=tmp_path / "c.db", live=False,
           log=lines.append)
    assert any("STUB" in ln for ln in lines), "a stub run must announce itself"


def test_stub_debater_handles_a_hex_signal_id(signals_db, tmp_path):
    """``signal_id`` is a hex TEXT digest in prod, not an integer.

    The first stub build did ``int(signal_id)`` and raised on 793 of prod's 814
    rows — the 21 that survived were the all-digit digests. The fixture had
    declared the column INTEGER, so the suite was green against a schema the
    database does not have."""
    report = RD.run(signals_db=signals_db, cache_db=tmp_path / "c.db",
                    live=False, log=lambda *a: None)
    assert report["errors"] == 0
    assert report["score"]["n"] == 2


def test_the_console_report_is_ascii_safe(signals_db, tmp_path):
    """This prints to a Windows console (cp1252); a stray em-dash renders as a
    replacement glyph in the middle of the headline."""
    report = RD.run(signals_db=signals_db, cache_db=tmp_path / "c.db",
                    live=False, log=lambda *a: None)
    text = RD.render_report(report)
    text.encode("cp1252")            # raises UnicodeEncodeError on a bad glyph
    assert all(ord(ch) < 128 for ch in text), "report must be plain ASCII"

    # The log lines print to the same console and were missed the first time.
    lines = []
    RD.run(signals_db=signals_db, cache_db=tmp_path / "c2.db", live=False,
           log=lines.append)
    for ln in lines:
        assert all(ord(ch) < 128 for ch in ln), f"non-ASCII in log line: {ln!r}"


def test_live_run_without_a_key_degrades_instead_of_raising(signals_db, tmp_path,
                                                            monkeypatch):
    monkeypatch.setattr(RD, "_make_client", lambda: None)
    report = RD.run(signals_db=signals_db, cache_db=tmp_path / "c.db",
                    live=True, log=lambda *a: None)
    assert report["mode"] == "no_client"


# ── the cache ───────────────────────────────────────────────────────────────

def test_a_cached_verdict_is_reused_rather_than_re_billed(signals_db, tmp_path):
    calls = []

    def debater(view):
        calls.append(view["signal_id"])
        return "VERDICT: TAKE"

    cache = tmp_path / "c.db"
    RD.run(signals_db=signals_db, cache_db=cache, live=True, debater=debater,
           log=lambda *a: None)
    assert len(calls) == 2
    RD.run(signals_db=signals_db, cache_db=cache, live=True, debater=debater,
           log=lambda *a: None)
    assert len(calls) == 2, "second run re-billed a cached signal"


def test_cache_is_keyed_on_the_prompt_so_a_changed_prompt_re_runs(signals_db,
                                                                  tmp_path):
    calls = []

    def debater(view):
        calls.append(view["signal_id"])
        return "VERDICT: TAKE"

    cache = tmp_path / "c.db"
    RD.run(signals_db=signals_db, cache_db=cache, live=True, debater=debater,
           prompt_version="v1", log=lambda *a: None)
    RD.run(signals_db=signals_db, cache_db=cache, live=True, debater=debater,
           prompt_version="v2", log=lambda *a: None)
    assert len(calls) == 4, "a changed prompt must invalidate the cache"


def test_a_debater_failure_drops_the_case_instead_of_aborting_the_run(signals_db,
                                                                      tmp_path):
    """One 500 must not cost the whole sample."""
    def debater(view):
        if view["signal_id"] == "0043e544":
            raise RuntimeError("upstream 500")
        return "VERDICT: PASS"

    report = RD.run(signals_db=signals_db, cache_db=tmp_path / "c.db", live=True,
                    debater=debater, log=lambda *a: None)
    assert report["errors"] == 1
    assert report["score"]["dropped"] >= 1


# ── the report ──────────────────────────────────────────────────────────────

def test_report_carries_the_score_and_the_sample_size(signals_db, tmp_path):
    report = RD.run(signals_db=signals_db, cache_db=tmp_path / "c.db",
                    live=False, log=lambda *a: None)
    assert report["score"]["n"] == 2
    assert report["score"]["status"] == "thin"


def test_render_report_states_the_base_rate_before_any_verdict_number():
    """The null model has to be read first or the lift is meaningless."""
    text = RD.render_report({
        "mode": "stub", "errors": 0, "model": None,
        "score": {"n": 100, "status": "ok", "base_rate": 0.78,
                  "approval_rate": 0.60, "approved_rate": 0.85, "lift": 0.07,
                  "dropped": 0, "approved_n": 60,
                  "vetoed": {"n": 40, "would_have_won": 12, "would_have_lost": 28,
                             "accuracy": 0.70},
                  "approved": {"n": 60, "would_have_won": 51,
                               "would_have_lost": 9, "accuracy": 0.15},
                  "by_grade": {}},
    })
    assert "78" in text and "base rate" in text.lower()
    assert text.index("base rate") < text.index("lift")


def test_render_report_refuses_to_headline_a_thin_or_degenerate_run():
    for status in ("thin", "degenerate_approve_all"):
        text = RD.render_report({
            "mode": "stub", "errors": 0, "model": None,
            "score": {"n": 12, "status": status, "base_rate": 0.8,
                      "approval_rate": 0.99, "approved_rate": 0.8, "lift": None,
                      "dropped": 0, "approved_n": 12,
                      "vetoed": {"n": 0, "would_have_won": 0,
                                 "would_have_lost": 0, "accuracy": None},
                      "approved": {"n": 12, "would_have_won": 10,
                                   "would_have_lost": 2, "accuracy": None},
                      "by_grade": {}},
        })
        assert "NOT A RESULT" in text.upper()
