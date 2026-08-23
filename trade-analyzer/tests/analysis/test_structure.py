"""Tests for the structure matrix (Phase 3, task 3.2).

Which option structure expresses a read depends on three things the app already
knows: which SIDE cleared, whether IV is cheap or rich, and where the dealer
walls sit. This is a pure lookup over that — no scoring, no prediction.

The tenor rule is the one worth stating: the model predicts 20 TRADING days, so
buy 30-45 DTE (the thesis window sits inside the option's life with theta still
shallow) and sell 20-35 DTE. Expressing a 20-day read in 0-DTEs or LEAPS is the
horizon mismatch this whole program has been unpicking.
"""
import pytest

from src.analysis import structure as st


class TestLongSide:
    def test_cheap_iv_BUYS_convexity(self):
        r = st.choose(side="long", iv_state="cheap")
        assert r["structure"] == "call debit spread"
        assert r["action"] == "debit"
        assert 30 <= r["dte_min"] and r["dte_max"] <= 45

    def test_rich_iv_SELLS_premium_below_the_put_wall(self):
        """Rich IV is a reason to be the seller, and the put wall is where
        dealer hedging cushions a decline — so the short strike goes beneath
        it, not above."""
        r = st.choose(side="long", iv_state="rich", put_wall=95.0, spot=100.0)
        assert r["structure"] == "put credit spread"
        assert r["action"] == "credit"
        assert r["short_strike_guidance"] and "below" in r["short_strike_guidance"]
        assert "95" in r["short_strike_guidance"]

    def test_mid_iv_is_a_debit_spread_inside_the_expected_move(self):
        r = st.choose(side="long", iv_state="mid")
        assert r["structure"] == "call debit spread"


class TestShortSide:
    def test_rich_iv_SELLS_premium_above_the_call_wall(self):
        """The mirror: dealer supply sits at the call wall, so a short premium
        strike belongs above it. This is the scanner's own passes_wall rule,
        reused on a new surface."""
        r = st.choose(side="short", iv_state="rich", call_wall=110.0, spot=100.0)
        assert r["structure"] == "call credit spread"
        assert r["action"] == "credit"
        assert "above" in r["short_strike_guidance"]
        assert "110" in r["short_strike_guidance"]

    def test_cheap_iv_BUYS_puts(self):
        r = st.choose(side="short", iv_state="cheap")
        assert r["structure"] == "put debit spread"
        assert r["action"] == "debit"

    def test_credit_structures_use_a_SHORTER_tenor_than_debits(self):
        """Sell into decay, buy ahead of it."""
        credit = st.choose(side="short", iv_state="rich", call_wall=110.0, spot=100.0)
        debit = st.choose(side="short", iv_state="cheap")
        assert credit["dte_max"] < debit["dte_max"]


class TestRelativeOnly:
    def test_an_uncleared_direction_yields_a_PAIR_not_a_naked_position(self):
        """The model predicts EXCESS return vs SPY. When the tape has not
        cleared a directional version of that, the honest expression is the
        relative one the label actually describes."""
        r = st.choose(side="short", iv_state="mid", clearance="relative_only")
        assert r["structure"] == "pair vs a top-decile name"
        assert r["action"] == "relative"
        assert "excess" in r["rationale"].lower() or "relative" in r["rationale"].lower()

    def test_a_blocked_side_proposes_NOTHING(self):
        r = st.choose(side="short", iv_state="rich", clearance="blocked")
        assert r["structure"] is None
        assert r["action"] == "none"

    def test_relative_only_overrides_even_a_rich_iv_credit_setup(self):
        """Clearance is a gate, not a tiebreak — it outranks the IV read."""
        r = st.choose(side="short", iv_state="rich", call_wall=110.0, spot=100.0,
                      clearance="relative_only")
        assert r["action"] == "relative"


class TestIvStateFromRank:
    @pytest.mark.parametrize("rank,expected", [
        (5, "cheap"), (29, "cheap"), (30, "mid"), (60, "mid"),
        (61, "rich"), (95, "rich"),
    ])
    def test_rank_maps_to_a_state(self, rank, expected):
        assert st.iv_state_from_rank(rank) == expected

    def test_an_unknown_rank_is_MID_not_a_guess(self):
        """IV rank builds forward from first run, so 'unknown' is common and
        must not be read as cheap (which would recommend buying premium on no
        information)."""
        assert st.iv_state_from_rank(None) == "mid"
        assert st.iv_state_from_rank(float("nan")) == "mid"


class TestGuidanceDegradesWithoutWalls:
    def test_a_credit_structure_without_a_wall_still_names_the_structure(self):
        """Walls are withheld off-hours by design. The structure survives; only
        the strike guidance goes quiet."""
        r = st.choose(side="short", iv_state="rich", call_wall=None, spot=100.0)
        assert r["structure"] == "call credit spread"
        assert r["short_strike_guidance"] == ""

    def test_no_spot_means_no_strike_guidance(self):
        r = st.choose(side="long", iv_state="rich", put_wall=95.0, spot=None)
        assert r["short_strike_guidance"] == ""


class TestNeverRaises:
    @pytest.mark.parametrize("kwargs", [
        {"side": "sideways", "iv_state": "rich"},
        {"side": "long", "iv_state": "banana"},
        {"side": None, "iv_state": None},
    ])
    def test_nonsense_inputs_degrade(self, kwargs):
        r = st.choose(**kwargs)
        assert set(r) >= {"structure", "action", "rationale"}
