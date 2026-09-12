"""D1: straddle and strangle templates — ANALYSIS ONLY.

Design: docs/plans/2026-09-12-straddle-strangle-design.md.

Four templates on the shared leg model, so the Calculator and the Simulator can
price and chart them. ⚠ **The two SHORT ones are undefined-risk structures**, and
the gap assessment's own "Don't" list is explicit: *"Don't open undefined-risk
structures in paper or the driver. That means naked calls, short straddles and
short strangles."* So they are buildable and chartable and must remain unreachable
by any scanner, any paper book and the driver — which these tests pin, because a
template quietly appearing in an allowlist is exactly how that rule would be lost.
"""
import pytest

from pages.options import strategies as st

FOUR = ("LONG_STRADDLE", "SHORT_STRADDLE", "LONG_STRANGLE", "SHORT_STRANGLE")
SHORTS = ("SHORT_STRADDLE", "SHORT_STRANGLE")


# ── the leg layouts ─────────────────────────────────────────────────────────

def test_a_long_straddle_is_a_long_call_and_a_long_put_at_the_SAME_strike():
    legs = st.STRATEGY_TEMPLATES["LONG_STRADDLE"]
    assert len(legs) == 2
    assert {leg["option_type"] for leg in legs} == {"call", "put"}
    assert {leg["side"] for leg in legs} == {"long"}
    # The defining property: one strike, both rights.
    assert {leg["strike_role"] for leg in legs} == {"atm"}


def test_a_short_straddle_is_the_same_strikes_sold():
    legs = st.STRATEGY_TEMPLATES["SHORT_STRADDLE"]
    assert {leg["side"] for leg in legs} == {"short"}
    assert {leg["strike_role"] for leg in legs} == {"atm"}


def test_a_strangle_straddles_spot_with_DIFFERENT_strikes():
    """That is the whole difference from a straddle: the call sits above spot and
    the put below, so it is cheaper and needs a bigger move."""
    for code in ("LONG_STRANGLE", "SHORT_STRANGLE"):
        legs = st.STRATEGY_TEMPLATES[code]
        roles = {leg["option_type"]: leg["strike_role"] for leg in legs}
        assert roles["call"] == "otm_up_1", code
        assert roles["put"] == "otm_dn_1", code


def test_every_leg_is_one_contract_and_the_near_expiry():
    """A straddle is 1-1, not 1-2 like a butterfly, and single-expiry — a
    two-expiry version is a calendar, which already exists separately."""
    for code in FOUR:
        for leg in st.STRATEGY_TEMPLATES[code]:
            assert leg["qty"] == 1, code
            assert leg["expiry_role"] == "near", code


# ── the UI surfaces ─────────────────────────────────────────────────────────

def test_all_four_appear_in_the_dropdown_groups():
    grouped = {c for _label, codes in st.STRATEGY_GROUPS for c in codes}
    for code in FOUR:
        assert code in grouped, code


def test_all_four_appear_in_the_cascading_menu():
    menu = {c for _family, variants in st.STRATEGY_MENU for _l, c in variants}
    for code in FOUR:
        assert code in menu, code


def test_each_carries_a_label_a_blurb_and_tags():
    for code in FOUR:
        assert st.strategy_label(code) != code, code
        assert st.strategy_blurb(code), code
        assert st.strategy_tags(code), code


def test_the_leg_count_chip_is_derived_not_typed():
    for code in FOUR:
        assert "2 LEGS" in st.strategy_tags(code), code


def test_the_cash_flow_tag_matches_the_direction_of_the_legs():
    """The FIRST tag is the cash-flow direction, which the frame colours."""
    assert st.strategy_tags("LONG_STRADDLE")[0] == "DEBIT"
    assert st.strategy_tags("LONG_STRANGLE")[0] == "DEBIT"
    assert st.strategy_tags("SHORT_STRADDLE")[0] == "CREDIT"
    assert st.strategy_tags("SHORT_STRANGLE")[0] == "CREDIT"


def test_the_two_SHORT_ones_are_tagged_UNDEFINED_RISK():
    """⚠ The one tag that must be there. It is the same word ``NAKED_CALL`` wears,
    and it is what tells a reader on the Calculator that this structure has no
    wing behind it."""
    for code in SHORTS:
        assert "UNDEFINED RISK" in st.strategy_tags(code), code


def test_the_long_ones_are_NOT_tagged_undefined_risk():
    """A long straddle's loss is the premium — defined, and small."""
    for code in ("LONG_STRADDLE", "LONG_STRANGLE"):
        assert "UNDEFINED RISK" not in st.strategy_tags(code), code


# ── analysis only: the rule that must not be lost ───────────────────────────

def test_none_of_the_four_is_in_the_DRIVER_allowlist():
    """⚠ ``shared.driver_policy.ALLOWED`` is the driver's structure allowlist. A
    straddle appearing there would let the autonomous layer sell naked premium."""
    from shared.driver_policy import ALLOWED
    for code in FOUR:
        assert code not in ALLOWED, code


def test_none_of_the_four_is_a_structure_the_taxonomy_treats_as_tradeable():
    """``shared.structures`` is what the paper engine, the repricer and Rescue key
    on. An unknown structure there means "no rules", which is the correct answer
    for an analysis-only template — and the reason the repricer refuses to mark
    one rather than inventing a price."""
    from shared import structures
    for code in FOUR:
        assert not structures.is_single_leg(code), code
        assert not structures.is_put_side(code), code


# ⚠ The two engine-side guarantees (the repricer cannot mark one, the scanner
# emits none) live in ``options-scanner/tests/test_straddle_analysis_only.py``.
# They CANNOT live here: the webgui suite has no options-scanner on ``sys.path``,
# because Tier 1 imports no engines - which is the architecture that makes
# "analysis only" true in the first place.


# ── the calculator prices them numerically, not analytically ────────────────

def test_they_are_summarised_NUMERICALLY_not_by_the_analytic_shortcut():
    """``_ANALYTIC_CODES`` is the exact-formula path for the structures that have
    one. A straddle has no closed-form max-profit (it is unbounded on one side
    and the payoff is a V), so it must take the numeric path — and quietly
    joining the analytic set would produce a confidently wrong summary."""
    legs = st.build_default_legs("LONG_STRADDLE", 100.0,
                                 [95.0, 100.0, 105.0], ["2026-10-16"])
    # "CUSTOM" IS the numeric path - the analytic shortcut is the named set.
    assert st.summary_code("LONG_STRADDLE", legs) == "CUSTOM"
    assert "LONG_STRADDLE" not in st._ANALYTIC_CODES
    # Non-vacuity: the shortcut really does fire for a structure that has one.
    pcs_legs = st.build_default_legs("PCS", 100.0, [95.0, 100.0, 105.0],
                                     ["2026-10-16"])
    assert st.summary_code("PCS", pcs_legs) != "CUSTOM"


def test_the_default_legs_resolve_onto_real_strikes():
    strikes = [90.0, 95.0, 100.0, 105.0, 110.0]
    legs = st.build_default_legs("LONG_STRANGLE", 100.0, strikes, ["2026-10-16"])
    assert len(legs) == 2
    for leg in legs:
        assert leg["strike"] in strikes
    # The call above spot, the put below — the strangle's defining shape.
    call = next(leg for leg in legs if leg["option_type"] == "call")
    put = next(leg for leg in legs if leg["option_type"] == "put")
    assert call["strike"] > put["strike"]


def test_a_straddles_two_legs_land_on_ONE_strike():
    legs = st.build_default_legs("SHORT_STRADDLE", 100.0,
                                 [95.0, 100.0, 105.0], ["2026-10-16"])
    assert len({leg["strike"] for leg in legs}) == 1
