"""D4: the leg editor renders a SHARE leg without a strike or an expiry.

The editor is widgets, so the decisions are extracted as pure helpers and tested
here — the repo's standing pattern. Three of them matter:

* a share leg has **no strike and no expiry** to choose, so those two controls
  must be inert rather than offering a stale option strike;
* its numbers mean different things — ``qty`` is **100-share lots** and
  ``premium`` is the **price paid per share**, not an option premium — so the
  column labels have to change or the reader types the wrong magnitude;
* ⚠ flipping an existing leg's type TO stock must **clear** the strike and
  expiry it was carrying. A stale expiry is not cosmetic: it would join the
  front-expiry computation in ``calc_summary_generic`` and, if it were earlier
  than the option's, price the option leg with time left at the wrong horizon.
"""
import pytest

from pages.options import leg_editor as LE


def _stock(**over):
    leg = {"option_type": "stock", "side": "long", "strike": None,
           "expiry": None, "qty": 1, "premium": 100.0}
    leg.update(over)
    return leg


def _call(**over):
    leg = {"option_type": "call", "side": "short", "strike": 105.0,
           "expiry": "2026-10-16", "qty": 1, "premium": 2.0}
    leg.update(over)
    return leg


# ── the type select ───────────────────────────────────────────────────────

def test_stock_is_an_offered_leg_TYPE():
    """Without it a hand-built covered call is impossible — you could pick the
    template but never turn a leg into shares."""
    assert "stock" in LE.TYPE_OPTIONS


def test_the_option_types_are_still_first():
    """Nearly every leg is an option; stock is the exception and reads last."""
    assert LE.TYPE_OPTIONS[:2] == ["call", "put"]


def test_stock_is_OFF_by_default_for_a_mount():
    """⚠ Gating the strategy MENU is not enough on its own. The Simulator mounts
    the same card layout, so an always-on ``stock`` entry would let a user
    hand-build a share leg on a page whose engines price a ``ContractRow`` off
    the option chain and cannot value one."""
    assert LE.type_options() == ["call", "put"]
    assert LE.type_options(allow_stock=False) == ["call", "put"]


def test_a_mount_can_OPT_IN_to_stock():
    assert LE.type_options(allow_stock=True) == ["call", "put", "stock"]


def test_type_options_returns_a_FRESH_list():
    """It is handed straight to ``ui.select``, which keeps a reference."""
    got = LE.type_options(allow_stock=True)
    got.append("junk")
    assert LE.type_options(allow_stock=True) == ["call", "put", "stock"]


def test_only_the_CALCULATOR_opts_in():
    """The three mounts, checked at source: the Calculator analyses, the
    Simulator cannot price shares, and the Rescue ad-hoc form BOOKS into an
    account that holds shares in ``equity_lots``, not ``paper_positions``."""
    import inspect

    from pages.options import calculator, rescue, simulator
    assert "allow_stock=True" in inspect.getsource(calculator.render)
    assert "allow_stock" not in inspect.getsource(simulator.render)
    assert "allow_stock" not in inspect.getsource(rescue)


# ── strike and expiry are inert for shares ───────────────────────────────

def test_a_share_leg_offers_NO_strikes():
    assert LE.leg_strike_options(_stock(), [95.0, 100.0, 105.0]) == []


def test_an_option_leg_still_offers_the_ladder():
    assert LE.leg_strike_options(_call(), [95.0, 100.0, 105.0]) == [95.0, 100.0,
                                                                    105.0]


def test_a_share_leg_offers_NO_expiries():
    assert LE.leg_expiry_options(_stock(), ["2026-10-16", "2026-11-20"]) == []


def test_an_option_leg_still_offers_the_expiries():
    assert LE.leg_expiry_options(_call(), ["2026-10-16"]) == ["2026-10-16"]


@pytest.mark.parametrize("leg", [_stock(), _call()])
def test_the_option_lists_are_never_None(leg):
    """The widget passes them straight to ``ui.select``, which needs a list."""
    assert isinstance(LE.leg_strike_options(leg, None), list)
    assert isinstance(LE.leg_expiry_options(leg, None), list)


# ── the labels say what the number is ────────────────────────────────────

def test_a_share_legs_quantity_is_labelled_LOTS():
    """⚠ It counts 100-share lots. A reader who thinks it is shares types 100
    and builds a 10,000-share position — every figure 100x."""
    assert LE.leg_labels(_stock())["qty"] == "LOTS"


def test_a_share_legs_premium_is_labelled_as_a_SHARE_PRICE():
    labels = LE.leg_labels(_stock())
    assert labels["premium"] != "PREMIUM"
    assert "SHARE" in labels["premium"].upper()


def test_a_share_leg_dashes_the_STRIKE_and_EXPIRY_labels():
    """There is nothing to put there, and a blank cell reads as a missing value
    rather than as an inapplicable one."""
    labels = LE.leg_labels(_stock())
    assert labels["strike"] == "—"
    assert labels["expiry"] == "—"


def test_an_OPTION_legs_labels_are_UNCHANGED():
    """The control: every existing leg must render exactly as before."""
    assert LE.leg_labels(_call()) == {"type": "TYPE", "side": "SIDE",
                                      "expiry": "EXPIRY", "strike": "STRIKE",
                                      "qty": "QTY", "premium": "PREMIUM"}


@pytest.mark.parametrize("bad", [None, {}, {"option_type": None}, 7])
def test_the_labels_never_raise_on_a_junk_leg(bad):
    assert LE.leg_labels(bad)["qty"] in ("QTY", "LOTS")


# ── flipping a leg's type clears what no longer applies ──────────────────

def test_switching_a_leg_TO_stock_clears_its_strike_and_expiry():
    """⚠ The load-bearing one. A stale expiry would join the front-expiry
    computation and could price the option leg at the wrong horizon."""
    was = _call()
    now = LE.retype_leg(was, "stock")
    assert now["option_type"] == "stock"
    assert now["strike"] is None
    assert now["expiry"] is None


def test_switching_a_leg_AWAY_from_stock_leaves_them_None_to_be_repicked():
    """It has no remembered strike to restore, and inventing one would put a
    number the user never chose into a priced leg."""
    now = LE.retype_leg(_stock(), "call")
    assert now["option_type"] == "call"
    assert now["strike"] is None and now["expiry"] is None


def test_retyping_between_CALL_and_PUT_keeps_the_strike_and_expiry():
    """The pre-D4 behaviour, unchanged: both are option legs on the same ladder."""
    now = LE.retype_leg(_call(), "put")
    assert now["option_type"] == "put"
    assert now["strike"] == 105.0
    assert now["expiry"] == "2026-10-16"


def test_retyping_keeps_SIDE_QTY_and_PREMIUM():
    now = LE.retype_leg(_call(qty=3, premium=1.25), "stock")
    assert now["side"] == "short" and now["qty"] == 3 and now["premium"] == 1.25


def test_retyping_does_not_MUTATE_the_original_leg():
    was = _call()
    LE.retype_leg(was, "stock")
    assert was["option_type"] == "call" and was["strike"] == 105.0


def test_retyping_keeps_the_normalized_key_set():
    now = LE.retype_leg(_call(), "stock")
    assert set(now) == {"option_type", "side", "strike", "expiry", "qty",
                        "premium"}


# ── "set all legs to this expiry" must skip the share leg ────────────────

def test_setting_ALL_legs_to_an_expiry_SKIPS_the_share_leg():
    """⚠ The Calculator's top-level Expiry propagates to every leg, which would
    stamp a date onto shares — the same stale-expiry hazard ``retype_leg``
    guards, arriving from a different direction. Nothing else on the page can
    write that field."""
    legs = [_stock(), _call()]
    out = LE.set_legs_expiry(legs, "2026-12-18")
    stock = next(l for l in out if l["option_type"] == "stock")
    opt = next(l for l in out if l["option_type"] == "call")
    assert stock["expiry"] is None
    assert opt["expiry"] == "2026-12-18"


def test_setting_all_legs_to_an_expiry_is_UNCHANGED_for_options():
    """The control: an option-only set still gets every leg stamped."""
    out = LE.set_legs_expiry([_call(), _call(strike=95.0)], "2026-12-18")
    assert [l["expiry"] for l in out] == ["2026-12-18", "2026-12-18"]
