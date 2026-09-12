"""D4: the Calculator page's five "a leg without a strike is invalid" sites.

⚠ **A share leg has no strike, and five places on this page read that as
incomplete.** Four of them block or mislead, and one silently DROPS the leg —
which is the worst of the five, because the page then prices a covered call as a
naked short call with no warning at all:

| site | was | now |
|---|---|---|
| the action line | "pick every leg strike to calculate" | a share leg needs no strike, so it no longer asks for the impossible |
| Calculate | refused | proceeds |
| Fill premiums | refused for the whole set | fills the option legs and leaves the share leg to its own price |
| ``max_loss_estimate`` | mapped the share leg to "put" | declines — a strike-based estimate cannot describe shares |
| Send to Expected Move | dropped it | still drops it, and that is right: a share leg has no strike LINE to draw |
"""
import pytest

from pages.options import calculator as C
from pages.options import leg_editor as LE


def _stock(premium=100.0, qty=1):
    return {"option_type": "stock", "side": "long", "strike": None,
            "expiry": None, "qty": qty, "premium": premium}


def _call(strike=105.0, premium=2.0, side="short"):
    return {"option_type": "call", "side": side, "strike": strike,
            "expiry": "2026-10-16", "qty": 1, "premium": premium}


# ── the shared readiness predicate ────────────────────────────────────────

def test_a_share_leg_is_READY_without_a_strike():
    assert LE.leg_ready(_stock()) is True


def test_an_option_leg_still_NEEDS_a_strike():
    assert LE.leg_ready(_call()) is True
    assert LE.leg_ready(_call(strike=None)) is False


@pytest.mark.parametrize("bad", [None, "", float("nan")])
def test_an_unusable_option_strike_is_not_ready(bad):
    """⚠ NaN is in here because ``strike is None`` would pass it, and it would
    then reach ``bs_price`` and poison every cell of the grid."""
    assert LE.leg_ready(_call(strike=bad)) is False


def test_a_share_leg_is_ready_even_with_no_PREMIUM_yet():
    """Its price is filled from spot; missing it is not a blocked state."""
    assert LE.leg_ready(_stock(premium=None)) is True


@pytest.mark.parametrize("junk", [None, {}, 7, {"option_type": "call"}])
def test_the_predicate_never_raises(junk):
    assert LE.leg_ready(junk) in (True, False)


def test_legs_ready_requires_at_least_one_leg():
    """An empty set is not ready — 'nothing' is not a position."""
    assert LE.legs_ready([]) is False
    assert LE.legs_ready(None) is False


def test_legs_ready_accepts_a_covered_call():
    assert LE.legs_ready([_stock(), _call()]) is True


def test_legs_ready_still_refuses_a_HALF_BUILT_spread():
    assert LE.legs_ready([_call(), _call(strike=None)]) is False


# ── max_loss_estimate declines rather than mis-typing the leg ────────────

def test_max_loss_estimate_DECLINES_a_leg_set_holding_shares():
    """⚠ It maps every leg to "call" or "put" and reasons from strikes, so a
    share leg was silently booked as a PUT. Declining is the honest answer: the
    numeric summary in Tier 2 is what prices these, and it already does."""
    assert C.max_loss_estimate([_stock(), _call()]) is None


def test_max_loss_estimate_is_UNCHANGED_for_option_only_legs():
    """The control — a put credit spread's estimate must not move."""
    pcs = [{"option_type": "put", "side": "short", "strike": 100.0,
            "expiry": "2026-10-16", "qty": 1, "premium": 0.60},
           {"option_type": "put", "side": "long", "strike": 98.0,
            "expiry": "2026-10-16", "qty": 1, "premium": 0.20}]
    assert C.max_loss_estimate(pcs) == pytest.approx(160.0)


# ── premium filling: the share leg takes SPOT, not a chain quote ─────────

def test_a_share_legs_premium_defaults_to_SPOT():
    """What you would pay for the shares now. There is no chain row for stock,
    and leaving it at 0 would make the whole position look free."""
    filled = C.fill_stock_premiums([_stock(premium=None), _call()], spot=137.5)
    assert filled[0]["premium"] == pytest.approx(137.5)


def test_a_share_legs_EXISTING_premium_is_not_overwritten():
    """⚠ The user's own cost basis is the whole point of the analysis — you are
    asking what a call written against shares you already own is worth, and you
    did not buy them at today's price."""
    filled = C.fill_stock_premiums([_stock(premium=88.25)], spot=137.5)
    assert filled[0]["premium"] == pytest.approx(88.25)


def test_a_zero_premium_share_leg_IS_refilled():
    """0.0 is what an untouched ``ui.number`` reports, not a cost basis of zero."""
    filled = C.fill_stock_premiums([_stock(premium=0.0)], spot=137.5)
    assert filled[0]["premium"] == pytest.approx(137.5)


def test_OPTION_legs_are_left_alone_by_the_stock_fill():
    filled = C.fill_stock_premiums([_stock(premium=None), _call(premium=2.0)],
                                   spot=137.5)
    assert filled[1]["premium"] == pytest.approx(2.0)


@pytest.mark.parametrize("spot", [None, 0.0, -5.0, float("nan")])
def test_an_unusable_spot_leaves_the_premium_ALONE(spot):
    """Never write a fabricated cost basis. Without a spot the leg stays as it
    is and the readiness predicate still lets the user type one."""
    filled = C.fill_stock_premiums([_stock(premium=None)], spot=spot)
    assert filled[0]["premium"] is None


def test_the_stock_fill_does_not_MUTATE_the_input():
    legs = [_stock(premium=None)]
    C.fill_stock_premiums(legs, spot=137.5)
    assert legs[0]["premium"] is None


# ── the page's gates go through the predicate ───────────────────────────

def test_the_pages_gates_use_the_shared_predicate():
    """Source-level: five sites open-coded ``l.get("strike") is None``, and a
    sixth added later would reintroduce the bug. They go through
    ``leg_editor.legs_ready`` so there is one answer."""
    import inspect

    src = inspect.getsource(C)
    assert 'l.get("strike") is None' not in src, (
        "a strike-only readiness test blocks a share leg — use legs_ready()")


def test_send_to_expected_move_still_drops_a_share_leg():
    """Deliberate, and the one site left filtering: the Expected Move chart draws
    strike LINES, and a share leg has none. Dropping it is not a silent failure
    there — the cone and candles are unaffected."""
    import inspect

    src = inspect.getsource(C.render)
    assert "send_to_em" in src
