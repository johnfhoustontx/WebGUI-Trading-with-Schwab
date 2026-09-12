"""B2: the volatility floor reaches the Market Scanner's Directional tab.

Design: docs/plans/2026-09-12-volatility-gate-design.md.

``run_full_scan`` applied ``MIN_IV_RANK`` to ``signals_0dte`` and
``signals_swing`` only, so the Directional tab's **naked shorts** sold premium at
any volatility. That list is where a blanket per-list filter would do real damage:
it carries ``SHORT_PUT`` / ``SHORT_CALL`` beside ``LONG_CALL`` / ``LONG_PUT``, and
cheap volatility is exactly when the long half is the right trade. So the gate is
per signal, on the candidate's own vega sign, and every drop assertion below has a
keep assertion beside it.

The two windows are gated on their OWN floor (0-DTE 35, SWING 30) inside the
window loop, rather than on one key for the merged list: the loop is the only place
that still knows which window a candidate came from.

⚠ **The shared ``fake_client`` fixture sets ``MIN_IV_RANK`` to all zeros**, so
every test here must put a floor back or it asserts nothing. That is exactly the
trap this file is about, one layer up — hence ``_floors`` rather than relying on
the production constant.
"""
import pytest

import iv_analysis
import scanner_engine

from shared import vol_gate

from tests.test_scanner_engine import fake_client  # noqa: F401  (fixture)

SYMBOLS = ["SPY", "QQQ"]

_SHORT = {"SHORT_PUT", "SHORT_CALL"}
_LONG = {"LONG_PUT", "LONG_CALL"}

# The 0-DTE bucket's real upper edge (``zerodte_max_dte`` is a local in
# ``run_full_scan``; 4 is the documented window, not a guess - see the
# "0-DTE bucket spans DTE 0..4" section of CLAUDE.md).
_ZERODTE_MAX_DTE = 4


@pytest.fixture
def uncut(monkeypatch):
    """Lift the single-leg score cut. Most candidates off the fake chain grade
    Weak, so with the production cut in force these assertions go vacuous - the
    same reason ``test_scanner_engine``'s own ``unfiltered_directional`` exists."""
    monkeypatch.setattr(scanner_engine, "SINGLE_LEG_MIN_SCORE", 0.0)
    monkeypatch.setattr(scanner_engine, "SINGLE_LEG_EXCLUDED_GRADES", ())


def _floors(monkeypatch, zero_dte=35, swing=30):
    """Put a real floor back after ``fake_client`` zeroed it."""
    monkeypatch.setattr(scanner_engine, "MIN_IV_RANK",
                        {"0-DTE": zero_dte, "SWING": swing, "INCOME": swing})


def _iv(monkeypatch, rank):
    """Pin every symbol's IV rank. ``run_iv_analysis`` is imported INSIDE
    ``run_full_scan``, so the patch target is the module attribute.

    Wraps rather than replaces, so every other field the scan reads
    (``expected_moves`` feeds the EM windows) stays exactly as the fixture built
    it and only the one axis under test moves.
    """
    real = iv_analysis.run_iv_analysis

    def _patched(*a, **k):
        out = dict(real(*a, **k) or {})
        out["iv_rank"] = rank
        return out

    monkeypatch.setattr(iv_analysis, "run_iv_analysis", _patched)


def _directional(fake_client, monkeypatch, rank, **floors):  # noqa: F811
    _floors(monkeypatch, **floors)
    _iv(monkeypatch, rank)
    return scanner_engine.run_full_scan(
        fake_client, symbols=SYMBOLS)["signals_directional"]


def _types(fake_client, monkeypatch, rank, **floors):  # noqa: F811
    return [s["type"] for s in _directional(fake_client, monkeypatch, rank, **floors)]


def test_the_fixture_produces_both_sides_at_a_healthy_iv_rank(fake_client,  # noqa: F811
                                                              monkeypatch, uncut):
    """Non-vacuity: without BOTH sides present at a passing IV rank, neither
    assertion below can bind."""
    got = _types(fake_client, monkeypatch, 80.0)
    assert _SHORT & set(got), got
    assert _LONG & set(got), got


def test_cheap_volatility_removes_the_naked_shorts(fake_client,  # noqa: F811
                                                   monkeypatch, uncut):
    got = _types(fake_client, monkeypatch, 5.0)
    assert not (_SHORT & set(got)), got


def test_cheap_volatility_KEEPS_the_long_options(fake_client,  # noqa: F811
                                                 monkeypatch, uncut):
    """The point of keying on vega sign rather than filtering the list."""
    got = _types(fake_client, monkeypatch, 5.0)
    assert _LONG & set(got), got


def test_a_rank_between_the_two_window_floors_gates_only_the_tighter_window(
        fake_client, monkeypatch, uncut):  # noqa: F811
    """0-DTE's floor is 35 and SWING's is 30, so at 32 the 0-DTE naked shorts go
    and the swing ones stay. Keys the gate to the window the candidate was built
    in - one key for the merged list could not express this."""
    sigs = _directional(fake_client, monkeypatch, 32.0)
    shorts = [s for s in sigs if s["type"] in _SHORT]
    assert shorts, "fixture produced no naked shorts at all"
    assert all(s["dte"] > _ZERODTE_MAX_DTE for s in shorts), \
        [(s["type"], s["dte"]) for s in shorts]


def test_a_symbol_with_no_iv_rank_is_ungated(fake_client, monkeypatch, uncut):  # noqa: F811
    """``iv_analysis`` returns None when HV history is too short. An absence must
    not read as a refusal."""
    got = _types(fake_client, monkeypatch, None)
    assert _SHORT & set(got), got


def test_a_floor_of_zero_gates_nothing(fake_client, monkeypatch, uncut):  # noqa: F811
    """0 is OFF, not "a floor at zero" - and an IV rank of 0.0 is a real reading."""
    got = _types(fake_client, monkeypatch, 0.0, zero_dte=0, swing=0)
    assert _SHORT & set(got), got


def test_the_existing_credit_spread_floor_still_binds(fake_client, monkeypatch):  # noqa: F811
    """The whole-list filter over 0-DTE/Swing is unchanged - those lists are
    uniformly short premium, so the per-signal form would be the same answer at
    more cost. This test exists so 'refactoring' it away is visible."""
    _floors(monkeypatch)
    _iv(monkeypatch, 1.0)
    res = scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    assert res["signals_0dte"] == []
    assert res["signals_swing"] == []


def test_the_ceiling_ships_off_so_long_premium_survives_expensive_vol(
        fake_client, monkeypatch, uncut):  # noqa: F811
    got = _types(fake_client, monkeypatch, 100.0)
    assert _LONG & set(got), got
    # ...but the mechanism is live and would cut them if a level were set.
    assert vol_gate.blocks(100.0, +0.4, ceiling=65) == vol_gate.IV_TOO_HIGH


def test_module_constants_come_from_config_not_literals():
    """Both bounds resolve through shared.scanner_config, so the TOML is the
    operator's surface for each."""
    from shared import scanner_config
    assert scanner_engine.MIN_IV_RANK == scanner_config.min_iv_rank()
    assert scanner_engine.MAX_IV_RANK == scanner_config.max_iv_rank()
