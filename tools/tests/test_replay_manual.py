"""Tests for the manual (Claude Chat) path of the debate-replay harness.

Running the debate by hand instead of through the API changes exactly one thing
that matters and several that do not. The one that matters is **alignment**: the
verdicts come back as pasted text, and if a verdict is matched to the wrong
signal every outcome in the sample attaches to the wrong call. Position-matching
would do that silently, so the round trip is keyed on ``signal_id`` end to end
and these tests are mostly about that.

What is pinned here:

  * the emitted prompt leaks no grade, no score, no outcome — the same blind
    property the API path has, now crossing a copy-paste boundary;
  * every signal in a batch appears in its prompt, exactly once, and batches
    partition the sample with nothing lost or doubled;
  * parsing matches on id, never on order, and survives the prose a chat model
    wraps around a table;
  * an id that was never sent (a hallucinated row) is REFUSED, and an id that
    never came back is DROPPED — neither is defaulted to TAKE.

Run from the repo root:
    .venv\\Scripts\\python -m pytest tools\\tests\\test_replay_manual.py -v
"""
import pytest

from tools import replay_scoring as RS


def _view(sid, symbol="AAPL"):
    return {"signal_id": sid, "symbol": symbol, "strategy": "put_credit_spread",
            "short_strike": 180.0, "long_strike": 175.0, "width": 5.0,
            "dte_at_entry": 30, "entry_credit": 1.25, "entry_max_loss": 3.75,
            "entry_short_delta": 0.22, "entry_iv_rank": 45.0,
            "entry_underlying": 190.0, "first_seen_date": "2026-06-18"}


def _views(n):
    return [_view(f"{i:08x}", f"SYM{i}") for i in range(n)]


# ── batching ────────────────────────────────────────────────────────────────

def test_batches_partition_the_sample_with_nothing_lost_or_doubled():
    views = _views(57)
    batches = RS.batch_views(views, 25)
    assert [len(b) for b in batches] == [25, 25, 7]
    seen = [v["signal_id"] for b in batches for v in b]
    assert seen == [v["signal_id"] for v in views]
    assert len(set(seen)) == 57


def test_batch_size_of_one_gives_the_faithful_single_signal_form():
    """Not a special case in the code — a way to check whether seeing a whole
    batch at once changes the model's calls."""
    assert [len(b) for b in RS.batch_views(_views(3), 1)] == [1, 1, 1]


def test_batching_an_empty_sample_yields_no_batches():
    assert RS.batch_views([], 25) == []


# ── the emitted prompt ──────────────────────────────────────────────────────

def test_prompt_never_contains_a_grade_score_or_outcome():
    """The blind property has to survive the trip through a chat window."""
    text = RS.render_prompt(_views(5), batch_no=1, batch_total=3).lower()
    for banned in ("grade", "entry_score", "realized", "pnl", "outcome",
                   "marginal", "won", "lost"):
        assert banned not in text, f"{banned!r} leaked into the manual prompt"


def test_prompt_lists_every_signal_in_the_batch_exactly_once():
    views = _views(8)
    text = RS.render_prompt(views, batch_no=1, batch_total=1)
    for v in views:
        assert text.count(v["signal_id"]) == 1


def test_prompt_states_the_required_output_format_and_the_ids_to_use():
    text = RS.render_prompt(_views(3), batch_no=2, batch_total=4)
    assert "verdict" in text.lower()
    # TAKE/PASS are the parse tokens, so they must appear literally uppercase.
    assert "TAKE" in text and "PASS" in text
    assert "batch 2 of 4" in text.lower()


def test_prompt_does_not_reveal_how_many_should_be_taken():
    """A hint at the target rate turns the exercise into following an
    instruction. The base rate is the thing being tested against."""
    low = RS.render_prompt(_views(10), batch_no=1, batch_total=1).lower()
    for banned in ("half", "50%", "base rate", "76", "most of these"):
        assert banned not in low


# ── parsing what comes back ─────────────────────────────────────────────────

def test_parse_results_reads_a_clean_table():
    got = RS.parse_results("0043e544 | TAKE\n00535765 | PASS")
    assert got == {"0043e544": "TAKE", "00535765": "PASS"}


def test_parse_results_survives_the_prose_a_chat_wraps_around_a_table():
    text = (
        "Happy to work through these. Here are my verdicts:\n\n"
        "```\n"
        "0043e544 | TAKE\n"
        "00535765 | PASS\n"
        "```\n\n"
        "Let me know if you'd like the reasoning for any of them."
    )
    assert RS.parse_results(text) == {"0043e544": "TAKE", "00535765": "PASS"}


def test_parse_results_accepts_the_separators_a_model_actually_uses():
    for line in ("0043e544 | TAKE", "0043e544: TAKE", "0043e544 - TAKE",
                 "0043e544\tTAKE", "| 0043e544 | TAKE |"):
        assert RS.parse_results(line) == {"0043e544": "TAKE"}, line


def test_parse_results_matches_on_id_not_on_order():
    """The whole reason the id is in the output format."""
    got = RS.parse_results("00535765 | PASS\n0043e544 | TAKE")
    assert got["0043e544"] == "TAKE" and got["00535765"] == "PASS"


def test_parse_results_ignores_a_line_with_no_verdict():
    assert RS.parse_results("0043e544 | still thinking\n00535765 | TAKE") == {
        "00535765": "TAKE"}


def test_parse_results_handles_empty_and_junk_without_raising():
    for junk in ("", None, "no table here at all", "|||"):
        assert RS.parse_results(junk) == {}


# ── applying them ───────────────────────────────────────────────────────────

def test_apply_results_refuses_an_id_that_was_never_sent():
    """A hallucinated row must not enter the sample."""
    cases = [{"signal_id": "0043e544", "entry_grade": "Good",
              "outcome": "win", "verdict": None}]
    stats = RS.apply_results(cases, {"0043e544": "TAKE", "deadbeef": "TAKE"})
    assert cases[0]["verdict"] == "TAKE"
    assert stats["unknown"] == 1
    assert stats["applied"] == 1


def test_apply_results_leaves_a_missing_id_unjudged_rather_than_defaulting():
    """Silence is 'not measured', never 'TAKE'."""
    cases = [{"signal_id": "a", "entry_grade": "Good", "outcome": "win",
              "verdict": None},
             {"signal_id": "b", "entry_grade": "Good", "outcome": "loss",
              "verdict": None}]
    stats = RS.apply_results(cases, {"a": "PASS"})
    assert cases[1]["verdict"] is None
    assert stats["missing"] == 1
    assert RS.score(cases)["dropped"] == 1


def test_apply_results_rejects_a_verdict_word_it_does_not_recognise():
    cases = [{"signal_id": "a", "entry_grade": "Good", "outcome": "win",
              "verdict": None}]
    stats = RS.apply_results(cases, {"a": "MAYBE"})
    assert cases[0]["verdict"] is None
    assert stats["applied"] == 0


def test_apply_results_is_idempotent_so_re_pasting_a_batch_is_safe():
    cases = [{"signal_id": "a", "entry_grade": "Good", "outcome": "win",
              "verdict": None}]
    RS.apply_results(cases, {"a": "TAKE"})
    RS.apply_results(cases, {"a": "TAKE"})
    assert cases[0]["verdict"] == "TAKE"


# ── the round trip ──────────────────────────────────────────────────────────

def test_emit_then_paste_back_scores_the_right_outcomes():
    """End to end, with the answers deliberately returned in a shuffled order."""
    views = _views(4)
    cases = [{"signal_id": v["signal_id"], "entry_grade": "Good",
              "outcome": out, "verdict": None}
             for v, out in zip(views, ["win", "loss", "win", "loss"])]

    prompt = RS.render_prompt(views, batch_no=1, batch_total=1)
    for v in views:
        assert v["signal_id"] in prompt

    # The model vetoes exactly the two losers, answering out of order.
    pasted = (f"{views[3]['signal_id']} | PASS\n"
              f"{views[0]['signal_id']} | TAKE\n"
              f"{views[2]['signal_id']} | TAKE\n"
              f"{views[1]['signal_id']} | PASS")
    RS.apply_results(cases, RS.parse_results(pasted))

    s = RS.score(cases)
    assert s["approved_n"] == 2
    assert s["approved_rate"] == pytest.approx(1.0)
    assert s["vetoed"]["would_have_lost"] == 2
