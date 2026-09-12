"""B8: the driver's dollar caps must follow equity, or they stop meaning anything.

Design: docs/plans/2026-09-12-position-awareness-design.md.

Measured on the live driver book, 2026-09-12 — equity **$13,347** after a 46.6%
drawdown from $25,000:

| cap | the config comment says | it actually is |
|---|---|---|
| `per_trade_max_risk` $3,000 | *"~12% of the book"* | **22.5%** |
| `daily_risk_budget` $12,000 | *"~half the book"* | **89.9%** |

Nothing was mis-set. The numbers simply stopped meaning what they were chosen to
mean, which is the whole of B8. ``scale_to_equity`` is ``min(dollars, pct ×
equity)`` so the change can only ever **tighten** — it restores the comments' own
intent rather than imposing a new appetite, and it needs no decision about how
aggressive the driver should be (which is still the operator's open question).

(The MANUAL book needed nothing: $250 per trade is 1.03% of $24,184 and
`MAX_SESSION_DRAWDOWN` $2,500 is 10.3% — both within a whisker of their intent —
and B3 already made its book-level ceiling a percentage.)
"""
import math

import pytest

from shared import driver_limits as dl


@pytest.fixture(autouse=True)
def _fresh():
    dl.reset_cache()
    yield
    dl.reset_cache()


BASE = {"per_trade_max_risk": 3000.0, "daily_risk_budget": 12000.0,
        "per_trade_max_risk_pct": 0.12, "daily_risk_budget_pct": 0.48,
        "max_concurrent": 10}


# ── the arithmetic ───────────────────────────────────────────────────────────

def test_a_drawn_down_book_tightens_both_caps():
    got = dl.scale_to_equity(BASE, 13346.80)
    assert got["per_trade_max_risk"] == pytest.approx(1601.62, abs=0.01)
    assert got["daily_risk_budget"] == pytest.approx(6406.46, abs=0.01)


def test_the_book_it_was_WRITTEN_for_is_left_alone():
    """At $25,000 the percentages reproduce the dollar figures, so this is a
    no-op on the book the caps were chosen against - which is the evidence that
    12% / 48% really are the comments' stated intent and not a new policy."""
    got = dl.scale_to_equity(BASE, 25000.0)
    assert got["per_trade_max_risk"] == pytest.approx(3000.0)
    assert got["daily_risk_budget"] == pytest.approx(12000.0)


def test_a_GROWN_book_can_never_loosen_a_cap():
    """⚠ The load-bearing property: ``min`` in one direction only. A book up 50%
    must not silently authorise a $4,500 trade — raising appetite is a decision,
    and this change is not allowed to make it."""
    got = dl.scale_to_equity(BASE, 40000.0)
    assert got["per_trade_max_risk"] == 3000.0
    assert got["daily_risk_budget"] == 12000.0


def test_every_other_key_is_carried_through_untouched():
    got = dl.scale_to_equity(BASE, 13346.80)
    assert got["max_concurrent"] == 10
    assert got["per_trade_max_risk_pct"] == 0.12


def test_the_input_dict_is_not_mutated():
    """The caller hands in ``settings.limits()``; mutating it would leak a
    tightened cap into a later cycle at a different equity."""
    original = dict(BASE)
    dl.scale_to_equity(BASE, 13346.80)
    assert BASE == original


# ── absence: a cap that cannot be scaled must not change ────────────────────

@pytest.mark.parametrize("equity", [None, 0, -1.0, float("nan"), float("inf"), "13000"])
def test_an_unusable_equity_leaves_the_dollar_caps_ALONE(equity):
    """A fraction of an unknown cannot be enforced, and a zero denominator would
    refuse every trade forever — which reads as a broken driver, not as a cap.
    The same rule as ``max_deployed_risk_pct`` in the paper engine."""
    got = dl.scale_to_equity(BASE, equity)
    assert got["per_trade_max_risk"] == 3000.0
    assert got["daily_risk_budget"] == 12000.0


@pytest.mark.parametrize("pct", [None, 0, 0.0, float("nan"), "0.12", -0.1])
def test_an_unusable_PERCENTAGE_leaves_its_cap_alone(pct):
    """0 means off, not "cap at zero" — and a NaN percentage must not slip past
    into a comparison. Each cap opts in independently."""
    got = dl.scale_to_equity({**BASE, "per_trade_max_risk_pct": pct}, 13346.80)
    assert got["per_trade_max_risk"] == 3000.0
    # ...while the sibling still scales, proving they are independent.
    assert got["daily_risk_budget"] < 12000.0


def test_limits_with_no_pct_keys_at_all_are_returned_unchanged():
    """Opt-in by DATA, so a caller (or a driver.toml) from before B8 behaves
    exactly as it did."""
    pre = {"per_trade_max_risk": 3000.0, "daily_risk_budget": 12000.0}
    assert dl.scale_to_equity(pre, 13346.80) == pre


def test_a_missing_dollar_cap_is_not_invented():
    """Scaling is a TIGHTENING of an existing ceiling. With no ceiling there is
    nothing to tighten, and writing one in would be a new policy."""
    got = dl.scale_to_equity({"per_trade_max_risk_pct": 0.12}, 13346.80)
    assert "per_trade_max_risk" not in got


@pytest.mark.parametrize("bad", [None, "x", float("nan")])
def test_a_non_numeric_dollar_cap_is_left_as_it_is(bad):
    got = dl.scale_to_equity({**BASE, "per_trade_max_risk": bad}, 13346.80)
    assert got["per_trade_max_risk"] is bad or got["per_trade_max_risk"] == bad \
        or (isinstance(bad, float) and math.isnan(bad)
            and math.isnan(got["per_trade_max_risk"]))


def test_non_dict_input_degrades_to_an_empty_dict_rather_than_raising():
    """This sits on the driver's decision path, which must never raise."""
    assert dl.scale_to_equity(None, 13346.80) == {}
    assert dl.scale_to_equity("nope", 13346.80) == {}


# ── the shipped config ───────────────────────────────────────────────────────

def test_the_shipped_percentages_match_the_comments_they_replace():
    r = dl.risk()
    assert r["per_trade_max_risk_pct"] == pytest.approx(0.12)
    assert r["daily_risk_budget_pct"] == pytest.approx(0.48)


def test_the_shipped_percentages_reproduce_the_dollar_caps_at_25k():
    """The consistency check that makes this a restoration rather than a retune."""
    r = dl.risk()
    assert r["per_trade_max_risk_pct"] * 25000.0 == pytest.approx(
        r["per_trade_max_risk"], rel=0.01)
    assert r["daily_risk_budget_pct"] * 25000.0 == pytest.approx(
        r["daily_risk_budget"], rel=0.01)


def test_the_toml_and_the_defaults_agree_on_both_new_keys():
    """``shared.config_toml`` deep-merges, so a key present in the defaults and
    absent from the TOML is invisible to an operator editing the file."""
    import tomllib

    from repo_paths import DRIVER_TOML
    raw = tomllib.loads(DRIVER_TOML.read_text(encoding="utf-8")).get("risk") or {}
    for key in ("per_trade_max_risk_pct", "daily_risk_budget_pct"):
        assert key in raw, key
        assert raw[key] == dl.risk()[key], key


def test_the_effective_caps_on_the_live_book_are_what_the_design_doc_claims():
    """Pins the numbers the CHANGELOG and design doc publish, so a later edit to
    the percentages cannot leave those documents quietly wrong."""
    got = dl.scale_to_equity(dl.risk(), 13346.80)
    assert round(got["per_trade_max_risk"]) == 1602
    assert round(got["daily_risk_budget"]) == 6406
