"""Tests for the debate-replay scoring layer (the falsification harness).

The harness exists to answer ONE question — does a bull/bear debate improve on
``entry_grade``? — and every test here pins a way that question can be answered
wrongly in a direction that flatters the debate:

  * **Leakage.** The debater must never see an outcome. ``build_case`` is a
    WHITELIST, not a blacklist, because a blacklist silently admits every column
    added to ``signals`` after this file was written.
  * **The null model.** 78% of these signals won. A debater that approves
    everything scores 78% and looks skilled. Every statistic here is reported
    against that base rate, and ``lift`` is the only headline.
  * **Degenerate splits.** A debate that approves ~everything or ~nothing has
    not been measured, it has been observed agreeing. That is a named status,
    not a number.
  * **Small cells.** The interesting subsets (vetoed winners, promoted losers)
    are the smallest ones. A rate computed on four rows is noise printed to two
    decimals, so cells below a floor report ``None`` and keep their count.

Run from the repo root:
    .venv\\Scripts\\python -m pytest tools\\tests\\test_replay_scoring.py -v
"""
import pytest

from tools import replay_scoring as RS


# ── fixtures ────────────────────────────────────────────────────────────────

def _signal(**over):
    """A signals-table row as ``dict`` (sqlite3.Row is Mapping-compatible)."""
    row = {
        "signal_id": 1, "scanner_type": "SWING", "symbol": "AAPL",
        "strategy": "put_credit_spread", "short_strike": 180.0,
        "long_strike": 175.0, "width": 5.0, "expiration": "2026-07-18",
        "dte_at_entry": 30, "entry_credit": 1.25, "entry_max_loss": 3.75,
        "entry_score": 72.0, "entry_grade": "Good", "entry_short_delta": 0.22,
        "entry_iv_rank": 45.0, "entry_underlying": 190.0,
        "first_seen_date": "2026-06-18",
        # Fields that must NEVER reach a prompt:
        "status": "closed", "be_armed": 1, "mode": "paper",
    }
    row.update(over)
    return row


def _case(grade="Good", verdict="TAKE", outcome="win"):
    return {"entry_grade": grade, "verdict": verdict, "outcome": outcome}


def _cases(spec):
    """``spec`` is a list of ``(grade, verdict, outcome, n)`` tuples."""
    out = []
    for grade, verdict, outcome, n in spec:
        out += [_case(grade, verdict, outcome) for _ in range(n)]
    return out


# ── leakage ─────────────────────────────────────────────────────────────────

def test_build_case_never_carries_an_outcome_field():
    """The whole harness is void if the debater can see what happened."""
    row = _signal()
    row["realized_pnl"] = 250.0        # an outcome column joined in by mistake
    row["exit_reason"] = "TARGET_HIT"
    case = RS.build_case(row)
    blob = repr(case).lower()
    for banned in ("realized", "exit_reason", "pnl", "close_date", "outcome"):
        assert banned not in blob, f"{banned!r} leaked into the debater's view"


def test_build_case_is_a_whitelist_not_a_blacklist():
    """A column added to `signals` tomorrow must not appear by default."""
    row = _signal()
    row["some_future_column"] = "surprise"
    assert "some_future_column" not in RS.build_case(row)


def test_build_case_keeps_the_entry_context_the_debate_needs():
    case = RS.build_case(_signal())
    for wanted in ("symbol", "strategy", "dte_at_entry", "entry_credit",
                   "entry_short_delta", "entry_iv_rank", "entry_underlying"):
        assert wanted in case, f"{wanted} is entry context and must be shown"


def test_build_case_hides_the_grade_and_score_it_is_being_compared_against():
    """Showing the debater the grade makes agreement the trivial outcome.

    The comparison is debate-vs-grade; anchoring the debate on the grade would
    measure how well an LLM echoes a number it was handed."""
    case = RS.build_case(_signal())
    assert "entry_grade" not in case
    assert "entry_score" not in case


# ── outcome derivation ──────────────────────────────────────────────────────

@pytest.mark.parametrize("pnl,expected", [
    (250.0, "win"), (-100.0, "loss"), (0.0, "loss"),
    (None, None), (float("nan"), None),
])
def test_outcome_of_classifies_realized_pnl(pnl, expected):
    """Exactly zero is not a win: it paid commissions to find out nothing."""
    assert RS.outcome_of({"realized_pnl": pnl}) == expected


def test_outcome_of_returns_none_for_an_open_signal():
    assert RS.outcome_of(None) is None
    assert RS.outcome_of({}) is None


# ── verdict parsing ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("VERDICT: TAKE", "TAKE"),
    ("verdict: pass", "PASS"),
    ("...reasoning...\nVERDICT: PASS\n", "PASS"),
    ("VERDICT: TAKE — the credit compensates", "TAKE"),
])
def test_verdict_from_text_reads_the_call(text, expected):
    assert RS.verdict_from_text(text) == expected


@pytest.mark.parametrize("text", ["", None, "I am not sure", "VERDICT: MAYBE"])
def test_verdict_from_text_refuses_to_guess(text):
    """An unparseable judgement is dropped from the sample, never defaulted.

    Defaulting to TAKE would silently pad the approve side with every malformed
    response — and the approve side is the one being measured against the base
    rate."""
    assert RS.verdict_from_text(text) is None


def test_verdict_from_text_takes_the_last_verdict_line():
    """Models restate the format before answering; the final line is the call."""
    assert RS.verdict_from_text(
        "I will end with VERDICT: TAKE or VERDICT: PASS.\nVERDICT: PASS"
    ) == "PASS"


# ── the statistics ──────────────────────────────────────────────────────────

def test_score_reports_the_base_rate_as_the_null_model():
    """Approve-everything is the thing to beat, so it is computed first."""
    r = RS.score(_cases([("Good", "TAKE", "win", 78),
                         ("Good", "TAKE", "loss", 22)]))
    assert r["n"] == 100
    assert r["base_rate"] == pytest.approx(0.78)


def test_score_lift_is_the_headline_and_is_zero_when_the_debate_adds_nothing():
    """A debate that approves everything cannot beat the base rate, by identity."""
    r = RS.score(_cases([("Good", "TAKE", "win", 78),
                         ("Good", "TAKE", "loss", 22)]))
    assert r["approved_rate"] == pytest.approx(0.78)
    assert r["lift"] == pytest.approx(0.0)


def test_score_credits_a_debate_that_vetoes_losers():
    """40 losses vetoed, every winner kept -> approved subset is perfect."""
    r = RS.score(_cases([("Good", "TAKE", "win", 60),
                         ("Good", "PASS", "loss", 40)]))
    assert r["base_rate"] == pytest.approx(0.60)
    assert r["approved_rate"] == pytest.approx(1.0)
    assert r["lift"] == pytest.approx(0.40)
    assert r["vetoed"]["n"] == 40
    assert r["vetoed"]["would_have_lost"] == 40


def test_score_penalises_a_debate_that_vetoes_winners():
    r = RS.score(_cases([("Good", "TAKE", "win", 50),
                         ("Good", "PASS", "win", 30),
                         ("Good", "TAKE", "loss", 20)]))
    assert r["approved_rate"] == pytest.approx(50 / 70)
    assert r["lift"] < 0
    assert r["vetoed"]["would_have_won"] == 30


def test_score_flags_a_degenerate_split_rather_than_printing_a_lift():
    """Approving ~everything is agreement, not measurement."""
    r = RS.score(_cases([("Good", "TAKE", "win", 99),
                         ("Good", "PASS", "loss", 1)]))
    assert r["status"] == "degenerate_approve_all"

    r = RS.score(_cases([("Good", "PASS", "win", 99),
                         ("Good", "TAKE", "loss", 1)]))
    assert r["status"] == "degenerate_pass_all"


def test_score_calls_a_thin_sample_thin_rather_than_reporting_a_rate():
    """Mirrors live_ic: too little data is an answer, not a small effect."""
    r = RS.score(_cases([("Good", "TAKE", "win", 5),
                         ("Good", "PASS", "loss", 3)]))
    assert r["status"] == "thin"
    assert r["n"] == 8


def test_score_is_ok_on_a_real_split_of_adequate_size():
    r = RS.score(_cases([("Good", "TAKE", "win", 50),
                         ("Good", "TAKE", "loss", 10),
                         ("Good", "PASS", "loss", 25),
                         ("Good", "PASS", "win", 15)]))
    assert r["status"] == "ok"


def test_lift_is_suppressed_when_the_approved_cell_is_too_small():
    """The headline obeys the same floor as every other cell.

    Caught on a real run: the debate approved ONE trade of 25, it happened to
    win, and the report printed ``lift 20.0%`` as its headline — a rate off a
    cell of one, sitting directly above a NOT A RESULT banner. Suppressing a
    rate on a 3-row cell while headlining one off a 1-row cell was incoherent."""
    cases = _cases([("Good", "PASS", "win", 19), ("Good", "PASS", "loss", 5),
                    ("Good", "TAKE", "win", 1)])
    r = RS.score(cases)
    assert r["approved_n"] == 1
    assert r["lift"] is None
    assert r["approved_rate"] is None
    # The informative cell is the veto, and it is big enough to speak.
    assert r["vetoed"]["n"] == 24
    assert r["vetoed"]["accuracy"] == pytest.approx(5 / 24)


def test_lift_survives_once_the_approved_cell_clears_the_floor():
    r = RS.score(_cases([("Good", "TAKE", "win", 10),
                         ("Good", "PASS", "loss", 15)]))
    assert r["approved_n"] == 10
    assert r["lift"] is not None


def test_score_suppresses_a_rate_on_a_cell_too_small_to_carry_one():
    """The disagreement cells are the smallest and the most over-read."""
    r = RS.score(_cases([("Good", "TAKE", "win", 60),
                         ("Good", "TAKE", "loss", 20),
                         ("Good", "PASS", "loss", 3),
                         ("Marginal", "TAKE", "win", 17)]))
    assert r["vetoed"]["n"] == 3
    assert r["vetoed"]["accuracy"] is None, "3 rows cannot carry a rate"


def test_score_breaks_out_by_grade_so_promotion_and_veto_are_separable():
    """'Debate helps' can mean two different things; they are reported apart."""
    r = RS.score(_cases([("Good", "TAKE", "win", 40),
                         ("Good", "PASS", "loss", 20),
                         ("Marginal", "TAKE", "win", 25),
                         ("Marginal", "PASS", "loss", 15)]))
    assert set(r["by_grade"]) == {"Good", "Marginal"}
    assert r["by_grade"]["Marginal"]["n"] == 40
    assert r["by_grade"]["Good"]["approved_rate"] == pytest.approx(1.0)


def test_score_ignores_cases_with_no_outcome_or_no_verdict():
    """An unmatured signal and an unparseable answer are both 'not measured'."""
    cases = _cases([("Good", "TAKE", "win", 30), ("Good", "PASS", "loss", 30)])
    cases.append(_case("Good", "TAKE", None))     # still open
    cases.append(_case("Good", None, "win"))      # unparseable verdict
    r = RS.score(cases)
    assert r["n"] == 60
    assert r["dropped"] == 2


def test_score_handles_an_empty_sample_without_raising():
    r = RS.score([])
    assert r["n"] == 0 and r["status"] == "thin"
    assert r["lift"] is None
