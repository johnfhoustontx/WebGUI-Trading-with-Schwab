"""Tests for the RANKING variant of the debate replay.

The accept/reject prompt (v1) came back ``degenerate_pass_all`` twice: the
debate declined 24 of 25 on a book that wins 81%, and gave near-identical
verdicts on two samples with opposite outcome distributions. That is either a
finding about debate or a finding about my prompt — v1 told the judge to "pass
on anything you would not actively choose" while showing it no opportunity set,
which is a one-sided instruction. The two explanations are confounded.

Ranking separates them. Asked to ORDER a batch rather than accept or reject
each row, the model cannot decline everything: a permutation always
discriminates. What survives is the question the desk proposal actually needs
answering — **can the debate ORDER trades better than ``entry_grade`` does?**

That is also the question this repo already has a vocabulary for. ``entry_grade``
separates Good 85.3% from Marginal 73.7%, an 11.6-point spread, and that is the
benchmark a ranking has to beat.

What is pinned here:

  * the prompt asks for a permutation and never names a target count — naming
    one would impose the approval rate this test exists to measure;
  * a reply that is not a permutation (a duplicate rank, a missing id) is
    REFUSED as broken rather than partially scored;
  * ranks are normalised to a within-batch percentile before pooling, since a
    rank of 5 means different things in a batch of 25 and a batch of 8;
  * P&L is reported beside win rate, because an 80% win rate on credit spreads
    can still be negative expectancy and the win-rate-only view hides it.

Run from the repo root:
    .venv\\Scripts\\python -m pytest tools\\tests\\test_replay_ranking.py -v
"""
import pytest

from tools import replay_scoring as RS


def _view(sid, symbol="AAPL"):
    return {"signal_id": sid, "symbol": symbol, "strategy": "PCS",
            "short_strike": 180.0, "long_strike": 175.0, "width": 5.0,
            "dte_at_entry": 10, "entry_credit": 1.25, "entry_max_loss": 3.75,
            "entry_short_delta": -0.22, "entry_iv_rank": 45.0,
            "entry_underlying": 190.0, "first_seen_date": "2026-06-18"}


def _views(n):
    return [_view(f"{i:08x}", f"SYM{i}") for i in range(n)]


def _ranked(spec):
    """``spec`` is a list of ``(rank, outcome, pnl)`` in one batch of len(spec)."""
    n = len(spec)
    return [{"signal_id": f"s{i}", "entry_grade": "Good", "outcome": out,
             "realized_pnl": pnl, "rank": rank, "batch_size": n}
            for i, (rank, out, pnl) in enumerate(spec)]


# ── the prompt ──────────────────────────────────────────────────────────────

def test_ranking_prompt_leaks_no_grade_score_or_outcome():
    text = RS.render_ranking_prompt(_views(5), 1, 3).lower()
    for banned in ("grade", "entry_score", "realized", "pnl", "outcome",
                   "won", "lost", "marginal"):
        assert banned not in text, f"{banned!r} leaked into the ranking prompt"


def test_ranking_prompt_lists_every_signal_once_and_asks_for_a_permutation():
    views = _views(9)
    text = RS.render_ranking_prompt(views, 1, 1)
    for v in views:
        assert text.count(v["signal_id"]) == 1
    assert "1 to 9" in text or "1..9" in text or "1-9" in text
    assert "exactly once" in text.lower()


def test_ranking_prompt_never_names_a_target_count():
    """Naming K would impose the approval rate this test exists to measure."""
    low = RS.render_ranking_prompt(_views(20), 1, 1).lower()
    for banned in ("top 5", "top half", "take the best", "how many",
                   "pass on", "reject"):
        assert banned not in low


def test_ranking_prompt_does_not_reuse_the_accept_reject_language():
    """v1's one-sided 'would you actively choose' instruction is the confound."""
    low = RS.render_ranking_prompt(_views(5), 1, 1).lower()
    assert "actively choose" not in low
    assert "take" not in low.split("## required output")[0]


# ── parsing a ranking ───────────────────────────────────────────────────────

def test_parse_rankings_reads_id_and_rank():
    assert RS.parse_rankings("0043e544 | 1\n00535765 | 2") == {
        "0043e544": 1, "00535765": 2}


def test_parse_rankings_survives_prose_and_fencing():
    text = ("Here is my ordering, best first:\n\n```\n"
            "aaa1 | 2\nbbb2 | 1\n```\n\nHappy to explain any of these.")
    assert RS.parse_rankings(text) == {"aaa1": 2, "bbb2": 1}


def test_parse_rankings_ignores_a_line_with_no_rank():
    assert RS.parse_rankings("aaa1 | best\nbbb2 | 1") == {"bbb2": 1}


def test_parse_rankings_handles_junk_without_raising():
    for junk in ("", None, "nothing here", "|||"):
        assert RS.parse_rankings(junk) == {}


# ── a reply must be a permutation ───────────────────────────────────────────

def test_apply_rankings_accepts_a_clean_permutation():
    cases = [{"signal_id": f"s{i}", "rank": None} for i in range(3)]
    stats = RS.apply_rankings(cases, {"s0": 2, "s1": 1, "s2": 3})
    assert stats["ok"] is True
    assert [c["rank"] for c in cases] == [2, 1, 3]
    assert all(c["batch_size"] == 3 for c in cases)


def test_apply_rankings_refuses_a_duplicate_rank():
    """Two trades cannot both be third-best; the reply is broken, not partial."""
    cases = [{"signal_id": f"s{i}", "rank": None} for i in range(3)]
    stats = RS.apply_rankings(cases, {"s0": 1, "s1": 1, "s2": 3})
    assert stats["ok"] is False and stats["reason"] == "not_a_permutation"
    assert all(c["rank"] is None for c in cases), "nothing applied on refusal"


def test_apply_rankings_refuses_a_missing_id():
    cases = [{"signal_id": f"s{i}", "rank": None} for i in range(3)]
    stats = RS.apply_rankings(cases, {"s0": 1, "s1": 2})
    assert stats["ok"] is False and stats["reason"] == "incomplete"


def test_apply_rankings_refuses_an_id_that_was_never_sent():
    cases = [{"signal_id": f"s{i}", "rank": None} for i in range(2)]
    stats = RS.apply_rankings(cases, {"s0": 1, "s1": 2, "ghost": 3})
    assert stats["ok"] is False and stats["reason"] == "unknown_id"


def test_apply_rankings_refuses_ranks_outside_one_to_n():
    cases = [{"signal_id": f"s{i}", "rank": None} for i in range(3)]
    assert RS.apply_rankings(cases, {"s0": 0, "s1": 1, "s2": 2})["ok"] is False


# ── scoring a ranking ───────────────────────────────────────────────────────

def test_rank_percentile_normalises_within_the_batch():
    """Rank 5 means different things in a batch of 25 and a batch of 8."""
    assert RS.rank_percentile(1, 25) == pytest.approx(0.0)
    assert RS.rank_percentile(25, 25) == pytest.approx(1.0)
    assert RS.rank_percentile(1, 1) == pytest.approx(0.0)


def test_score_ranking_separates_a_perfect_ordering():
    spec = ([(i + 1, "win", 100.0) for i in range(10)]
            + [(i + 11, "loss", -100.0) for i in range(10)])
    r = RS.score_ranking(_ranked(spec))
    assert r["n"] == 20
    assert r["top"]["win_rate"] == pytest.approx(1.0)
    assert r["bottom"]["win_rate"] == pytest.approx(0.0)
    assert r["spread"] == pytest.approx(1.0)
    assert r["status"] == "ok"


def test_score_ranking_reports_a_negative_spread_for_an_inverted_ordering():
    spec = ([(i + 1, "loss", -100.0) for i in range(10)]
            + [(i + 11, "win", 100.0) for i in range(10)])
    r = RS.score_ranking(_ranked(spec))
    assert r["spread"] == pytest.approx(-1.0)


def test_score_ranking_reports_about_zero_for_an_uninformative_ordering():
    spec = [(i + 1, "win" if i % 2 else "loss", 0.0) for i in range(20)]
    r = RS.score_ranking(_ranked(spec))
    assert abs(r["spread"]) <= 0.2


def test_score_ranking_reports_pnl_beside_win_rate():
    """An 80% win rate on credit spreads can still be negative expectancy."""
    spec = ([(i + 1, "win", 50.0) for i in range(8)]
            + [(9, "loss", -900.0), (10, "loss", -900.0)]
            + [(i + 11, "win", 50.0) for i in range(10)])
    r = RS.score_ranking(_ranked(spec))
    assert r["top"]["mean_pnl"] == pytest.approx((8 * 50 - 2 * 900) / 10)
    assert r["bottom"]["mean_pnl"] == pytest.approx(50.0)
    assert r["top"]["mean_pnl"] < r["bottom"]["mean_pnl"], (
        "win rate and expectancy can disagree, and the report must show it")


def test_score_ranking_calls_a_thin_sample_thin():
    r = RS.score_ranking(_ranked([(1, "win", 10.0), (2, "loss", -10.0)]))
    assert r["status"] == "thin"
    assert r["spread"] is None


def test_score_ranking_ignores_unranked_and_unclosed_cases():
    cases = _ranked([(i + 1, "win", 10.0) for i in range(20)])
    cases.append({"signal_id": "x", "entry_grade": "Good", "outcome": "win",
                  "realized_pnl": 1.0, "rank": None, "batch_size": 21})
    cases.append({"signal_id": "y", "entry_grade": "Good", "outcome": None,
                  "realized_pnl": None, "rank": 21, "batch_size": 21})
    r = RS.score_ranking(cases)
    assert r["n"] == 20 and r["dropped"] == 2


def test_score_ranking_carries_the_grade_benchmark_to_beat():
    """A ranking is only interesting if it beats the separation already had."""
    spec = ([(i + 1, "win", 10.0) for i in range(10)]
            + [(i + 11, "loss", -10.0) for i in range(10)])
    r = RS.score_ranking(_ranked(spec), grade_spread=0.116)
    assert r["grade_spread"] == pytest.approx(0.116)
    assert r["beats_grade"] is True


def test_score_ranking_says_when_it_does_not_beat_the_grade():
    spec = [(i + 1, "win" if i % 2 else "loss", 0.0) for i in range(20)]
    r = RS.score_ranking(_ranked(spec), grade_spread=0.116)
    assert r["beats_grade"] is False


def test_score_ranking_reports_the_ceiling_a_perfect_ordering_could_reach():
    """A spread of 20% means different things against a ceiling of 36% and 100%.

    With 20 cases of which 4 lost, a perfect ordering puts all 10 winners in
    the top half (100%) and the 4 losers plus 6 winners in the bottom (60%),
    so the best achievable spread is 40 points — not 100. Without this, a
    genuinely strong ordering reads as mediocre."""
    spec = ([(i + 1, "win", 10.0) for i in range(16)]
            + [(i + 17, "loss", -10.0) for i in range(4)])
    r = RS.score_ranking(_ranked(spec))
    assert r["ceiling"] == pytest.approx(0.4)
    assert r["spread"] == pytest.approx(0.4)
    assert r["spread_vs_ceiling"] == pytest.approx(1.0), "a perfect order"


def test_ceiling_is_none_when_the_sample_is_all_one_outcome():
    """Nothing to order: no ceiling, and no claim about one."""
    r = RS.score_ranking(_ranked([(i + 1, "win", 10.0) for i in range(20)]))
    assert r["ceiling"] is None
    assert r["spread_vs_ceiling"] is None


def test_score_ranking_handles_an_empty_sample():
    r = RS.score_ranking([])
    assert r["n"] == 0 and r["status"] == "thin" and r["spread"] is None
