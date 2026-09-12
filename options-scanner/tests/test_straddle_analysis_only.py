"""D1: the straddle/strangle templates stay ANALYSIS ONLY, engine side.

Design: docs/plans/2026-09-12-straddle-strangle-design.md.

The gap assessment's "Don't" list is explicit: *"Don't open undefined-risk
structures in paper or the driver. That means naked calls, short straddles and
short strangles."* The templates are buildable on the Calculator and the
Simulator; these are the guarantees that they cannot leak into anything that
trades.

⚠ They live here rather than beside the template tests because the webgui suite
has no ``options-scanner`` on ``sys.path`` — Tier 1 imports no engines, which is
the architecture that makes "analysis only" true rather than merely intended.
"""
import inspect

FOUR = ("LONG_STRADDLE", "SHORT_STRADDLE", "LONG_STRANGLE", "SHORT_STRANGLE")


def test_the_repricer_cannot_mark_one():
    """With no leg layout there are no Greeks and no mark, so one can never be
    opened into a book and then silently mismarked."""
    import signal_repricer as sr
    for code in FOUR:
        greeks = sr.position_greeks({"strategy": code, "short_strike": 500.0},
                                    {"putExpDateMap": {}, "callExpDateMap": {}})
        assert all(v is None for v in greeks.values()), code


def test_the_scanner_emits_no_such_structure():
    """The builders emit a closed set of types; none of these is among them."""
    import strategy_scanner
    src = inspect.getsource(strategy_scanner)
    for code in FOUR:
        assert code not in src, code


def test_the_paper_engine_has_no_leg_layout_for_one():
    """``signal_repricer._LEG_LAYOUT`` is keyed on the canonical structure name,
    and an absent key is what makes the repricer refuse rather than guess."""
    import signal_repricer as sr
    for code in FOUR:
        assert code not in sr._LEG_LAYOUT, code


def test_the_taxonomy_does_not_classify_one_as_tradeable():
    from shared import structures
    for code in FOUR:
        assert not structures.is_single_leg(code), code
        assert not structures.is_covered_call(code), code
        assert not structures.is_short_put(code), code


def test_none_is_in_the_drivers_structure_allowlist():
    from shared.driver_policy import ALLOWED
    for code in FOUR:
        assert code not in ALLOWED, code


def test_none_has_an_exit_rule_table():
    """A ``[structures.*]`` table would imply the app manages one. Absent means
    the plain ``[stops]`` set, which is moot for something never opened - and the
    presence of a table would be the first sign someone made it tradeable."""
    from shared import trade_mgmt
    for code in FOUR:
        assert code not in (trade_mgmt.DEFAULTS.get("structures") or {}), code
