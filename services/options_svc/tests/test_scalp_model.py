"""The Opening Scalp's walk-forward model (scalp_model): pure arithmetic.

The leakage tests are the ones that matter: a morning's prediction may read only
mornings from EARLIER days, so changing a same-day or later outcome must never
move it.
"""
import random

import pytest

from services.options_svc.scalp_model import (
    bucket_of, edge_across_scales, verdict, walk_forward, wilson)


def _day(i):
    return f"2026-09-{i:02d}"


# ── walk_forward ─────────────────────────────────────────────────────────────
def test_walk_forward_never_uses_same_or_later_day():
    rows = [{"day": f"2026-09-{d:02d}", "bucket": "high", "win": 1} for d in range(1, 6)]
    out = walk_forward(rows)
    assert out[0]["p_bucket"] is None and out[0]["n_bucket"] == 0
    assert out[2]["n_bucket"] == 2


def test_walk_forward_rates_are_earlier_rows_only():
    rows = [{"day": _day(1), "bucket": "high", "win": 1},
            {"day": _day(2), "bucket": "low", "win": 0},
            {"day": _day(3), "bucket": "high", "win": 0},
            {"day": _day(4), "bucket": "high", "win": 1},
            {"day": _day(5), "bucket": None, "win": 1}]
    out = walk_forward(rows)
    assert [o["day"] for o in out] == [r["day"] for r in rows]
    # Day 1: nothing earlier, for the bucket or the base.
    assert out[0] == {"day": _day(1), "p_bucket": None, "n_bucket": 0,
                      "p_base": None, "n_base": 0}
    # Day 2 (low): no earlier low; base = day 1 (1 win of 1).
    assert out[1]["p_bucket"] is None and out[1]["n_bucket"] == 0
    assert out[1]["p_base"] == 1.0 and out[1]["n_base"] == 1
    # Day 3 (high): earlier high = day 1; base = days 1-2.
    assert out[2]["p_bucket"] == 1.0 and out[2]["n_bucket"] == 1
    assert out[2]["p_base"] == 0.5 and out[2]["n_base"] == 2
    # Day 4 (high): earlier highs = days 1, 3 (1 of 2); base 1 of 3.
    assert out[3]["p_bucket"] == 0.5 and out[3]["n_bucket"] == 2
    assert out[3]["p_base"] == pytest.approx(1 / 3) and out[3]["n_base"] == 3
    # Day 5 has no bucket: no bucket rate, but the base still counts all four.
    assert out[4]["p_bucket"] is None and out[4]["n_bucket"] == 0
    assert out[4]["p_base"] == 0.5 and out[4]["n_base"] == 4


def test_walk_forward_same_day_rows_never_see_each_other():
    rows = [{"day": _day(1), "bucket": "high", "win": 1},
            {"day": _day(2), "bucket": "high", "win": 1},
            {"day": _day(2), "bucket": "high", "win": 0}]
    out = walk_forward(rows)
    assert out[1]["n_bucket"] == out[2]["n_bucket"] == 1
    assert out[1]["n_base"] == out[2]["n_base"] == 1
    assert out[2]["p_bucket"] == 1.0


def test_walk_forward_prediction_unmoved_by_later_or_same_day_outcomes():
    rng = random.Random(7)
    days = sorted(_day(1 + i // 2) for i in range(40))  # two rows a day
    rows = [{"day": d, "bucket": rng.choice(["low", "mid", "high", None]),
             "win": rng.randint(0, 1)} for d in days]
    base = walk_forward(rows)
    for k in range(len(rows)):
        flipped = [dict(r) for r in rows]
        # Flip every outcome on row k's day or later.
        for j in range(len(rows)):
            if rows[j]["day"] >= rows[k]["day"]:
                flipped[j]["win"] = 1 - flipped[j]["win"]
        again = walk_forward(flipped)
        for j in range(len(rows)):
            if rows[j]["day"] <= rows[k]["day"]:
                assert again[j] == base[j]


def test_walk_forward_refuses_unsorted_rows():
    with pytest.raises(ValueError):
        walk_forward([{"day": _day(2), "bucket": "high", "win": 1},
                      {"day": _day(1), "bucket": "high", "win": 1}])


def test_walk_forward_refuses_a_label_that_is_not_zero_or_one():
    with pytest.raises(ValueError):
        walk_forward([{"day": _day(1), "bucket": "high", "win": None}])


# ── verdict ──────────────────────────────────────────────────────────────────
def _stream(buckets_wins):
    return [{"day": _day(i + 1), "bucket": b, "win": w}
            for i, (b, w) in enumerate(buckets_wins)]


def test_verdict_briers_are_over_the_same_scored_mornings():
    rows = _stream([("low", 0), ("high", 1), ("high", 1), ("low", 0), ("high", 0)])
    scored = walk_forward(rows)
    v = verdict(scored, rows, min_samples=1)
    # High is scored on days 3 and 5 only (day 2 had no earlier high).
    #   day 3: p_bucket 1.0, p_base 0.5 (days 1-2), win 1
    #   day 5: p_bucket 1.0 (days 2,3), p_base 0.5 (days 1-4: 2 of 4), win 0
    hi = v["high"]
    assert hi["n_scored"] == 2
    assert hi["brier_bucket"] == pytest.approx(((1.0 - 1) ** 2 + (1.0 - 0) ** 2) / 2)
    assert hi["brier_base"] == pytest.approx(((0.5 - 1) ** 2 + (0.5 - 0) ** 2) / 2)
    assert hi["edge"] is False   # 0.5 is not below 0.25
    # Low is scored on day 4 only: p_bucket 0.0 (day 1), p_base 2/3, win 0.
    lo = v["low"]
    assert lo["n_scored"] == 1
    assert lo["brier_bucket"] == pytest.approx(0.0)
    assert lo["brier_base"] == pytest.approx((2 / 3) ** 2)
    assert lo["edge"] is True
    # A bucket with no scored morning has no Brier and no edge.
    assert v["mid"] == {"brier_bucket": None, "brier_base": None,
                        "n_scored": 0, "edge": False}


def test_edge_needs_min_samples():
    # High always wins, low always loses: high's Brier beats the base easily.
    rows = _stream([("high", 1), ("low", 0)] * 12)
    scored = walk_forward(rows)
    n_high = verdict(scored, rows, min_samples=1)["high"]["n_scored"]
    assert n_high == 11
    assert verdict(scored, rows, min_samples=n_high)["high"]["edge"] is True
    assert verdict(scored, rows, min_samples=n_high + 1)["high"]["edge"] is False


def test_edge_needs_lower_brier_than_base():
    # Every morning wins whatever its bucket: the bucket knows nothing the base
    # does not, so the two Briers tie and a tie is not an edge.
    rows = _stream([("high", 1), ("low", 1)] * 15)
    scored = walk_forward(rows)
    v = verdict(scored, rows, min_samples=1)
    assert v["high"]["brier_bucket"] == pytest.approx(v["high"]["brier_base"])
    assert v["high"]["edge"] is False
    # A bucket whose rate is WORSE than the base never shows an edge.
    rows = _stream([("high", 1), ("high", 0), ("low", 1), ("low", 0)] * 8)
    scored = walk_forward(rows)
    v = verdict(scored, rows, min_samples=1)
    for b in ("high", "low"):
        assert v[b]["brier_bucket"] >= v[b]["brier_base"]
        assert v[b]["edge"] is False


def test_verdict_refuses_mismatched_lists():
    rows = _stream([("high", 1), ("high", 0)])
    with pytest.raises(ValueError):
        verdict(walk_forward(rows)[:1], rows, min_samples=1)


# ── edge across scales ───────────────────────────────────────────────────────
def test_edge_must_hold_at_every_scale():
    yes = {"edge": True}
    no = {"edge": False}
    out = edge_across_scales({
        0.8: {"high": yes, "mid": yes, "low": no},
        1.0: {"high": yes, "mid": no, "low": no},
        1.25: {"high": yes, "mid": yes, "low": no},
    })
    assert out == {"high": True, "mid": False, "low": False}
    # A bucket absent at one scale has no edge.
    out = edge_across_scales({0.8: {"high": yes}, 1.0: {"low": yes}})
    assert out == {"high": False, "low": False}
    assert edge_across_scales({}) == {}


# ── wilson / bucket_of ───────────────────────────────────────────────────────
def test_wilson_bounds_contain_p():
    for wins, n in ((0, 1), (1, 1), (3, 10), (5, 10), (19, 20), (40, 41), (0, 50)):
        lo, hi = wilson(wins, n)
        assert 0.0 <= lo <= wins / n <= hi <= 1.0
    lo, hi = wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4)
    assert hi == pytest.approx(0.7634, abs=1e-4)
    assert wilson(0, 0) == (0.0, 1.0)


def test_bucket_of_cuts():
    cuts = [0.4, 0.7]
    assert bucket_of(None, cuts) is None
    assert bucket_of(0.0, cuts) == "low"
    assert bucket_of(0.39, cuts) == "low"
    assert bucket_of(0.4, cuts) == "mid"
    assert bucket_of(0.69, cuts) == "mid"
    assert bucket_of(0.7, cuts) == "high"
    assert bucket_of(1.0, cuts) == "high"
    assert bucket_of(float("nan"), cuts) is None
