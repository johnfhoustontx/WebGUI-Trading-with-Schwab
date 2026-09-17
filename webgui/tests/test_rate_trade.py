"""The Rate my trade verdict - PURE (design 2026-09-16, section 2)."""
import pytest

from pages.options import rate_trade as R


@pytest.mark.parametrize("grade,state,word", [
    ("Strong", "pos", "BUY"), ("Good", "pos", "BUY"),
    ("Strong", "warn", "CAUTION"), ("Good", "muted", "CAUTION"),
    ("Strong", "neg", "PASS"), ("Good", "neg", "PASS"),
    ("Marginal", "pos", "CAUTION"), ("Marginal", "warn", "PASS"),
    ("Marginal", "muted", "PASS"), ("Marginal", "neg", "PASS"),
    ("Weak", "pos", "PASS"), ("Weak", "warn", "PASS"),
    ("Weak", "muted", "PASS"), ("Weak", "neg", "PASS"),
])
def test_the_verdict_table(grade, state, word):
    assert R.verdict_word(grade, state) == word


def test_no_grade_is_pass_never_caution():
    for g in (None, "", "unscored", "Excellent?"):
        assert R.verdict_word(g, "pos") == "PASS"


def test_an_unknown_checklist_state_counts_as_a_caution():
    assert R.verdict_word("Strong", None) == "CAUTION"
    assert R.verdict_word("Strong", "weird") == "CAUTION"


def test_reasons_name_failed_gates_cautions_blocks_and_unknown_structure():
    row = {"grade": "Weak", "grade_reason": "Fails: liquidity, PoP",
           "structure_known": False, "vol_gate_blocks": True}
    items = [{"key": "earnings", "label": "Earnings", "tone": "warn", "text": "reports Oct 2"},
             {"key": "book", "label": "Paper book", "tone": "neg", "text": "over the symbol cap"},
             {"key": "vol", "label": "Vol rank", "tone": "pos", "text": "ok"}]
    reasons = R.reasons(row, items)
    assert "Fails: liquidity, PoP" in reasons
    assert "Paper book: over the symbol cap" in reasons
    assert "Earnings: reports Oct 2" in reasons
    # a block is named before a caution
    assert reasons.index("Paper book: over the symbol cap") < reasons.index("Earnings: reports Oct 2")
    assert any("custom structure" in r for r in reasons)
    assert any("volatility" in r.lower() for r in reasons)
    assert not any(r.startswith("Vol rank") for r in reasons)      # a clear line is no reason


def test_a_passing_grade_reason_is_not_a_reason():
    assert R.reasons({"grade_reason": "Excellent on all quality gates"}, []) == []


def test_a_check_that_could_not_run_is_named_as_a_caution():
    items = [{"label": "Wall", "tone": "muted", "text": "no dealer data"}]
    assert any("could not run" in r for r in R.reasons({}, items))


def test_banner_view_carries_word_tone_grade_and_score_never_a_zero():
    v = R.banner_view({"grade": "Good", "composite_score": 71.24}, "pos", [])
    assert v["word"] == "BUY" and v["tone"] == R.WORD_TONE["BUY"]
    assert v["grade"] == "Good" and v["score"] == "71"
    blank = R.banner_view({}, "pos", [])
    assert blank["word"] == "PASS" and blank["score"] == "—" and blank["grade"] == "—"
    nan = R.banner_view({"grade": "Good", "composite_score": float("nan")}, "pos", [])
    assert nan["score"] == "—"


def test_word_tones_are_a_finite_static_map():
    assert set(R.WORD_TONE) == {"BUY", "CAUTION", "PASS"}
    for cls in R.WORD_TONE.values():
        assert " " not in cls and cls.startswith("text-")


def test_only_the_open_requests_answer_paints():
    assert R.request_matches({"request_id": "a"}, "a")
    assert not R.request_matches({"request_id": "b"}, "a")
    assert not R.request_matches(None, "a")
    assert not R.request_matches({"request_id": "a"}, None)
    assert not R.request_matches({"request_id": None}, None)
