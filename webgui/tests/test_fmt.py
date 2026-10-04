"""Tests for pages/fmt.py — the shared numeric coercion + display helpers.

Measured before consolidating: 11 clone GROUPS across webgui/pages, 32 defs,
~123 removable lines. (The audit's "~60 formatter clones" counted same-NAMED
functions; this counts identical bodies.) The `num` coercion alone was written
out six times, and its own docstring said so.
"""
import math

import pytest

from pages import fmt


# ── num: the reading-or-None coercion ──────────────────────────────────────

def test_num_coerces_real_readings():
    assert fmt.num(3) == 3.0
    assert fmt.num(-2.5) == -2.5
    assert fmt.num("4.25") == 4.25
    assert fmt.num(0) == 0.0          # zero IS a reading


def test_num_rejects_bool_because_it_subclasses_int():
    """`float(True)` is 1.0, so a boolean sails through every numeric guard and
    renders as a rising trend. Rejected ahead of the coercion."""
    assert fmt.num(True) is None
    assert fmt.num(False) is None


def test_num_rejects_non_finite_and_unparseable():
    assert fmt.num(float("nan")) is None
    assert fmt.num(math.inf) is None and fmt.num(-math.inf) is None
    assert fmt.num(None) is None
    assert fmt.num("abc") is None
    assert fmt.num([1]) is None


# ── clamp ──────────────────────────────────────────────────────────────────

def test_clamp_bounds_both_ends_and_passes_the_middle():
    assert fmt.clamp(5, 0, 10) == 5
    assert fmt.clamp(-3, 0, 10) == 0
    assert fmt.clamp(99, 0, 10) == 10


# ── round_or_none: round a number, pass anything else through ──────────────

def test_round_or_none_rounds_numbers_and_passes_others_through():
    assert fmt.round_or_none(1.23456, 2) == 1.23
    assert fmt.round_or_none(None) is None
    assert fmt.round_or_none("n/a") == "n/a"


def test_round_or_none_leaves_bool_alone():
    """`round(True)` is 1 — a bool must pass through as itself, not become a
    number, for the same reason `num` rejects it."""
    assert fmt.round_or_none(True) is True


# ── fixed: the '—' em-dash display formatter ───────────────────────────────

def test_fixed_formats_to_the_requested_places():
    assert fmt.fixed(3.14159, 2) == "3.14"
    assert fmt.fixed(7, 0) == "7"


def test_fixed_shows_an_em_dash_for_no_reading():
    """The dash marks an ABSENT reading; a 0.00 would claim a measurement."""
    for bad in (None, "", "abc", float("nan")):
        assert fmt.fixed(bad) == "—"


def test_signed_pct_always_carries_its_sign():
    assert fmt.signed_pct(1.234) == "+1.23%"
    assert fmt.signed_pct(-0.5) == "-0.50%"
    assert fmt.signed_pct(0) == "+0.00%"
    assert fmt.signed_pct(None) == ""


# ── the display families: two decimals, always ─────────────────────────────

_NO_READING = (None, "", "abc", float("nan"), float("inf"), True)


def test_a_whole_number_price_keeps_its_decimals():
    """The defect this family exists for: 450 printed as ``450``."""
    assert fmt.price(450) == "450.00"
    assert fmt.price(450.5) == "450.50"
    assert fmt.price(6712.814) == "6,712.81"
    assert fmt.price("12.3") == "12.30"


def test_a_strike_has_two_decimals_and_no_separator():
    assert fmt.strike(450) == "450.00"
    assert fmt.strike(452.5) == "452.50"
    assert fmt.strike(5800) == "5800.00"


def test_a_ratio_has_two_decimals():
    assert fmt.ratio(1.5) == "1.50"
    assert fmt.ratio(2) == "2.00"
    assert fmt.ratio(0.666) == "0.67"


def test_a_percentage_has_two_decimals():
    assert fmt.pct(65) == "65.00%"
    assert fmt.pct(1.2) == "1.20%"
    assert fmt.pct(-0.5) == "-0.50%"
    assert fmt.pct(1.2, signed=True) == "+1.20%"
    assert fmt.pct(-1.2, signed=True) == "-1.20%"
    assert fmt.pct(0, signed=True) == "+0.00%"


def test_a_dollar_total_has_two_decimals_and_a_leading_sign():
    assert fmt.money(1250) == "$1,250.00"
    assert fmt.money(-40) == "-$40.00"
    assert fmt.money(0) == "$0.00"
    assert fmt.money(61.5, signed=True) == "+$61.50"
    assert fmt.money(-120, signed=True) == "-$120.00"
    assert fmt.money(0, signed=True) == "$0.00"


def test_an_abbreviated_dollar_total_has_two_decimals():
    assert fmt.money_short(1_200_000) == "$1.20M"
    assert fmt.money_short(45_000) == "$45.00K"
    assert fmt.money_short(950) == "$950.00"
    assert fmt.money_short(2_500_000_000) == "$2.50B"
    assert fmt.money_short(-45_000) == "-$45.00K"
    assert fmt.money_short(45_000, signed=True) == "+$45.00K"


def test_an_abbreviated_total_that_rounds_to_a_thousand_steps_up_a_unit():
    assert fmt.money_short(999_999) == "$1.00M"
    assert fmt.money_short(999.999) == "$1.00K"


def test_scaled_returns_the_figure_and_its_unit_apart():
    assert fmt.scaled(1_200_000) == ("1.20", "M")
    assert fmt.scaled(950) == ("950.00", "")
    assert fmt.scaled(-45_000) == ("45.00", "K")


@pytest.mark.parametrize("fn", [fmt.price, fmt.strike, fmt.ratio, fmt.pct,
                                fmt.money, fmt.money_short])
def test_every_family_shows_the_dash_for_no_reading(fn):
    """A 0.00 would claim a measurement that was never taken."""
    for bad in _NO_READING:
        assert fn(bad) == fmt.NO_READING


# ── float_or: the PERMISSIVE coercion (distinct from num on purpose) ────────

def test_float_or_returns_the_default_for_unparseable_input():
    assert fmt.float_or(None, 0.0) == 0.0
    assert fmt.float_or("x", -1) == -1
    assert fmt.float_or([], None) is None


def test_float_or_coerces_real_numbers():
    assert fmt.float_or("2.5", 0.0) == 2.5
    assert fmt.float_or(7, 0.0) == 7.0


def test_float_or_defaults_to_none():
    assert fmt.float_or(None) is None


def test_float_or_passes_nan_through_unlike_num():
    """Documented divergence, not an oversight: `float_or` is a coercion with a
    fallback and preserves whatever float() produced — including NaN — whereas
    `num` answers "is this a real reading". Callers that must not see a NaN want
    `num`; this test exists so the difference is deliberate and visible."""
    assert math.isnan(fmt.float_or(float("nan"), 0.0))
    assert fmt.num(float("nan")) is None


def test_float_or_passes_bool_through_unlike_num():
    assert fmt.float_or(True, 0.0) == 1.0
    assert fmt.num(True) is None
