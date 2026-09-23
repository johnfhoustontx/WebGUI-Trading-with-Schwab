"""D1: the straddle/strangle templates stay ANALYSIS ONLY, engine side.
The Strategy Finder builds them for comparison; nothing can open one.

Design: docs/plans/2026-09-12-straddle-strangle-design.md.

The gap assessment's "Don't" list is explicit: *"Don't open undefined-risk
structures in paper. That means naked calls, short straddles and
short strangles."* The templates are buildable on the Calculator and the
Simulator; these are the guarantees that they cannot leak into anything that
trades.

⚠ They live here rather than beside the template tests because the webgui suite
has no ``options-scanner`` on ``sys.path`` — Tier 1 imports no engines, which is
the architecture that makes "analysis only" true rather than merely intended.
"""

FOUR = ("LONG_STRADDLE", "SHORT_STRADDLE", "LONG_STRANGLE", "SHORT_STRANGLE")


def test_the_repricer_cannot_mark_one():
    """With no leg layout there are no Greeks and no mark, so one can never be
    opened into a book and then silently mismarked."""
    import signal_repricer as sr
    for code in FOUR:
        greeks = sr.position_greeks({"strategy": code, "short_strike": 500.0},
                                    {"putExpDateMap": {}, "callExpDateMap": {}})
        assert all(v is None for v in greeks.values()), code


def test_the_scanner_may_show_one_but_the_ledger_cannot_open_one():
    """The Strategy Finder BUILDS these (2026-09-13) so a trader can compare them;
    D1 is about what can be OPENED, and that stays nothing. A scanner row reaches
    the ledger only through ``paper_trader.create_paper_trade``, which opens only
    the credit spreads and ``PAPER_DEBIT_TYPES`` and refuses every other type by
    name - so it raises rather than opening a position."""
    import paper_trader
    for code in FOUR:
        assert code not in paper_trader.PAPER_DEBIT_TYPES, code



def test_an_analysis_only_structure_is_refused_by_name():
    import pytest
    import paper_trader
    for code in FOUR:
        with pytest.raises(ValueError, match="not paper-tradeable"):
            paper_trader.create_paper_trade({"type": code, "symbol": "XYZ"}, 1)


def _straddle_chain(spot=100.0, days=30):
    """Three strikes, both maps, one expiry - enough for all four builders."""
    import datetime as dt
    exp = (dt.date.today() + dt.timedelta(days=days)).isoformat()
    key = f"{exp}:{days}"

    def c(delta, mark):
        return [{"delta": delta, "mark": mark, "bid": mark - 0.05, "ask": mark + 0.05,
                 "theta": -0.02, "vega": 0.10, "gamma": 0.01, "volatility": 28.0,
                 "totalVolume": 500, "openInterest": 1000}]
    return {"underlyingPrice": spot,
            "callExpDateMap": {key: {"95.0": c(0.77, 6.5), "100.0": c(0.53, 3.4),
                                     "105.0": c(0.30, 1.5)}},
            "putExpDateMap": {key: {"95.0": c(-0.23, 1.2), "100.0": c(-0.47, 2.9),
                                    "105.0": c(-0.70, 6.0)}}}


def test_a_real_finder_row_for_each_is_refused_by_name():
    """Proves the docstring above rather than restating it, on rows the Finder
    itself built. ``create_paper_trade`` refuses the type BY NAME before it
    builds a trade dict - explicitly, where it used to rely on the credit branch
    KeyError-ing on a ``short_strike`` these rows lack, which the first row to
    gain that field would have silently removed. It is a pure dict builder - no
    database and no network - which is what makes this safe to call here."""
    import pytest
    import paper_trader
    import strategy_scanner as ss
    rows = {s["type"]: s for s in ss.build_straddles_strangles(
        _straddle_chain(), "XYZ", 100.0, 0.28, 5, 90)}
    assert set(rows) == set(FOUR)
    for code, row in rows.items():
        with pytest.raises(ValueError, match="not paper-tradeable"):
            paper_trader.create_paper_trade(row, 1)


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


def test_none_has_an_exit_rule_table():
    """A ``[structures.*]`` table would imply the app manages one. Absent means
    the plain ``[stops]`` set, which is moot for something never opened - and the
    presence of a table would be the first sign someone made it tradeable."""
    from shared import trade_mgmt
    for code in FOUR:
        assert code not in (trade_mgmt.DEFAULTS.get("structures") or {}), code
