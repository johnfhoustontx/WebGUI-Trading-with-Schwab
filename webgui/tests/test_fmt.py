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
    assert fmt.signed_pct(1.234) == "+1.2%"
    assert fmt.signed_pct(-0.5) == "-0.5%"
    assert fmt.signed_pct(0) == "+0.0%"
    assert fmt.signed_pct(None) == ""


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
