"""Tests for liquidity gates and strike validity in scanner_engine."""
import pytest
import sys
import os
from datetime import date, datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner_engine import (LIQUIDITY_THRESHOLDS, passes_liquidity_gate, screen_spreads,
                            is_strike_in_expected_move_window, detect_strike_increment,
                            calculate_position_size, calc_technicals, MIN_IV_RANK,
                            calc_vix_term_structure,
                            check_earnings_conflict, calc_effective_min_credit)
from scanner_engine import (calc_expected_pnl, calc_contracts_for_target,
                            PROFIT_TARGET, CONTRACT_ROUND_TO, CONTRACT_DEFAULT)
from scanner_engine import (select_best_width, WIDTH_MULTIPLIERS, MAX_WIDTH_DOLLARS)
import scanner_engine


def _opts_from(short_strike, other_marks):
    """Build an opts dict like screen_spreads uses internally.
    short_strike and other_marks = {strike: mark}. All contracts have good liquidity."""
    opts = {}
    # Short leg at short_strike with delta -0.10 (PCS example)
    opts[short_strike] = {
        "strike": short_strike, "delta": -0.10,
        "mark": other_marks.get(short_strike, 1.00),
        "bid": other_marks.get(short_strike, 1.00) - 0.02,
        "ask": other_marks.get(short_strike, 1.00) + 0.02,
        "theta": -0.05, "vega": 0.10, "gamma": 0.01, "iv": 25.0,
        "volume": 200, "oi": 500,
    }
    for k, m in other_marks.items():
        if k == short_strike:
            continue
        opts[k] = {
            "strike": k, "delta": -0.05, "mark": m,
            "bid": m - 0.02, "ask": m + 0.02,
            "theta": -0.02, "vega": 0.05, "gamma": 0.005, "iv": 26.0,
            "volume": 100, "oi": 300,
        }
    return opts


class TestSelectBestWidth:
    def test_accepts_small_0dte_credits(self):
        """Small per-share credits are fine — contracts scale up to hit the target.
        This is the whole point of the 2026-04-21 change: 0-DTE signals produce
        credits of $0.20-$0.75, well below the old $1.00 hard floor."""
        opts = _opts_from(510.0, {510.0: 0.60, 509.0: 0.40, 508.0: 0.25, 505.0: 0.05})
        result = select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                   trade_type="0-DTE", min_cr_pct=0.05)
        assert result is not None
        width, _, credit, _ = result
        assert credit < 1.00  # well below the old gate
        assert width > 0

    def test_returns_none_when_all_widths_fail_min_credit_pct(self):
        """The regime-based min_cr_pct floor still applies."""
        # width=1: credit=0.05, credit/width = 5% (below 10%) — fails
        # width=2: credit=0.10, credit/width = 5% — fails
        opts = _opts_from(510.0, {510.0: 0.50, 509.0: 0.45, 508.0: 0.40})
        result = select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                   trade_type="0-DTE", min_cr_pct=0.10)
        assert result is None

    def test_picks_width_with_highest_total_epnl(self):
        """Contract sizing makes narrow widths competitive with wide ones.
        Short delta=-0.10 -> PoP=0.90, P(loss)=0.10. Realistic fill credits
        (mid − 0.008 with the fixture's ±0.02 quotes):
        Width=5:  credit=0.992, ml=4.008 -> n_target=10, E[PnL]=(0.90·0.992 − 0.10·4.008)·10·100 = $492
        Width=10: credit=1.992, ml=8.008 -> n_target=5,  E[PnL]=(0.90·1.992 − 0.10·8.008)·5·100  = $496
        Width 10 wins (the realistic haircut hurts the doubled contract count more)."""
        opts = _opts_from(510.0, {510.0: 2.50, 505.0: 1.50, 500.0: 0.50})
        result = select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                   trade_type="0-DTE", min_cr_pct=0.10)
        width, _, credit, _ = result
        assert width == 10.0
        assert credit == pytest.approx(1.992, abs=1e-9)

    def test_respects_max_width_cap(self):
        """Widths above MAX_WIDTH_DOLLARS should not be considered."""
        opts = _opts_from(600.0, {600.0: 5.00, 300.0: 0.10})
        result = select_best_width(opts[600.0], opts, "PCS", strike_increment=1.0,
                                   trade_type="0-DTE", min_cr_pct=0.10)
        assert result is None

    def test_rejects_widths_with_illiquid_long_leg(self):
        """Long leg failing the liquidity gate should cause that width to be skipped."""
        opts = _opts_from(510.0, {510.0: 2.00, 505.0: 0.50})
        opts[505.0]["oi"] = 5  # below 20 threshold
        result = select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                   trade_type="0-DTE", min_cr_pct=0.10)
        assert result is None

    def test_risk_cap_limits_contracts(self):
        """When a tiny account forces the risk cap below the target size, the
        function still returns a selection (not None) — the cap just reduces
        E[PnL] but narrow widths can still win."""
        opts = _opts_from(510.0, {510.0: 0.60, 509.0: 0.10})
        result = select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                   trade_type="0-DTE", min_cr_pct=0.05,
                                   account_size=1000, max_risk_pct=0.05)
        assert result is not None

    def test_candidate_widths_constants(self):
        assert 1 in WIDTH_MULTIPLIERS
        assert 100 in WIDTH_MULTIPLIERS
        assert MAX_WIDTH_DOLLARS == 200

    def test_credit_is_realistic_fill_not_mid(self):
        """credit = net_bid + 0.40*(net_ask-net_bid); with the fixture's ±0.02
        quotes that is mid-credit − 0.008 (see fill_model.FILL_FRAC). Only one
        width offered so the assertion is independent of width selection."""
        opts = _opts_from(510.0, {510.0: 2.50, 505.0: 1.50})
        result = select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                   trade_type="0-DTE", min_cr_pct=0.10)
        _, _, credit, _ = result
        assert credit == pytest.approx(0.992, abs=1e-9)

    def test_untradeable_wide_market_is_skipped(self):
        """A long leg quoted absurdly wide makes the only width untradeable."""
        opts = _opts_from(510.0, {510.0: 2.50, 509.0: 1.50})
        opts[509.0]["bid"] = 0.10
        opts[509.0]["ask"] = 2.90   # net market wider than 30% of width
        result = select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                   trade_type="0-DTE", min_cr_pct=0.10)
        assert result is None

    def test_premarket_quotes_fall_back_to_mid_credit(self):
        """bid=ask=0 (pre-market): use mark-mid credit, skip the gate."""
        opts = _opts_from(510.0, {510.0: 2.50, 505.0: 1.50, 500.0: 0.50})
        for leg in opts.values():
            leg["bid"] = 0
            leg["ask"] = 0
        result = select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                   trade_type="0-DTE", min_cr_pct=0.10)
        _, _, credit, _ = result
        assert credit == pytest.approx(1.00)

    def test_rejects_credit_below_delta_margin(self):
        """delta -0.30 needs cr/w >= 0.32 (|d|+0.02). ~0.28 clears MIN_ABS_CREDIT
        and breakeven (0.30) but fails the 0.02 margin -> rejected."""
        opts = _opts_from(510.0, {510.0: 0.69, 509.0: 0.40})
        opts[510.0]["delta"] = -0.30
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                 trade_type="0-DTE", min_cr_pct=0.10) is None

    def test_accepts_credit_above_delta_margin(self):
        """delta -0.20 needs cr/w >= 0.22; ~0.28 clears it."""
        opts = _opts_from(510.0, {510.0: 0.69, 509.0: 0.40})
        opts[510.0]["delta"] = -0.20
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                 trade_type="0-DTE", min_cr_pct=0.10) is not None

    def test_edge_margin_applied_in_explicit_widths_path(self):
        """Both width paths must enforce the delta-margin gate."""
        import inspect
        import scanner_engine
        src = inspect.getsource(scanner_engine.screen_spreads)
        assert "EDGE_MARGIN" in src

    def test_edge_margin_pins_to_002(self):
        """Spread pays cr/w ~0.262 with short delta -0.25: ABOVE the breakeven
        (0.25, so accepted at EDGE_MARGIN=0.00) but BELOW the 0.02 margin (0.27)
        -> rejected. Pins EDGE_MARGIN to ~0.02 (would fail at 0.00)."""
        opts = _opts_from(510.0, {510.0: 0.67, 509.0: 0.40})
        opts[510.0]["delta"] = -0.25
        assert select_best_width(opts[510.0], opts, "PCS", strike_increment=1.0,
                                 trade_type="0-DTE", min_cr_pct=0.10) is None


def test_dead_delta_range_config_removed():
    """DELTA_RANGES / get_delta_range_for_regime were vestigial — the live
    premium passes use the EM window + DELTA_SANITY_MAX, not this table."""
    import scanner_engine
    assert not hasattr(scanner_engine, "get_delta_range_for_regime")
    assert not hasattr(scanner_engine, "DELTA_RANGES")


class TestLiquidityThresholds:
    """Verify LIQUIDITY_THRESHOLDS dict exists with correct structure."""

    def test_thresholds_has_0dte_key(self):
        assert "0-DTE" in LIQUIDITY_THRESHOLDS

    def test_thresholds_has_swing_key(self):
        assert "SWING" in LIQUIDITY_THRESHOLDS

    def test_0dte_min_oi(self):
        assert LIQUIDITY_THRESHOLDS["0-DTE"]["min_oi"] == 100

    def test_0dte_min_volume(self):
        assert LIQUIDITY_THRESHOLDS["0-DTE"]["min_volume"] == 50

    def test_0dte_max_spread_pct(self):
        assert LIQUIDITY_THRESHOLDS["0-DTE"]["max_spread_pct"] == 0.15

    def test_0dte_long_leg_thresholds(self):
        assert LIQUIDITY_THRESHOLDS["0-DTE"]["min_oi_long"] == 20

    def test_swing_min_oi(self):
        assert LIQUIDITY_THRESHOLDS["SWING"]["min_oi"] == 50

    def test_swing_min_volume(self):
        assert LIQUIDITY_THRESHOLDS["SWING"]["min_volume"] == 10

    def test_swing_max_spread_pct(self):
        assert LIQUIDITY_THRESHOLDS["SWING"]["max_spread_pct"] == 0.25

    def test_swing_long_leg_thresholds(self):
        assert LIQUIDITY_THRESHOLDS["SWING"]["min_oi_long"] == 10


class TestPassesLiquidityGate:
    """Unit tests for passes_liquidity_gate() helper.

    `passes_liquidity_gate` deliberately softens the volume + spread checks
    when the options market is closed (8:30-15:00 CT, Mon-Fri). That
    exemption is a UX compromise for pre-market scans where volume=0 on
    every contract. These tests pin the "market open" state so the
    gate's strict-hours logic is what's under test; otherwise the suite
    would be flaky (pass only during market hours, fail on weekends).
    """

    @pytest.fixture(autouse=True)
    def _force_market_open(self, monkeypatch):
        import scanner_engine
        monkeypatch.setattr(scanner_engine, "_is_options_market_open", lambda: True)

    def _make_contract(self, oi=200, volume=100, bid=1.00, ask=1.10):
        return {"oi": oi, "volume": volume, "bid": bid, "ask": ask, "mark": (bid + ask) / 2}

    # --- 0-DTE tests ---
    def test_0dte_passes_good_contract(self):
        c = self._make_contract(oi=150, volume=80, bid=1.00, ask=1.10)
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=True) is True

    def test_0dte_rejects_low_oi(self):
        c = self._make_contract(oi=50, volume=80, bid=1.00, ask=1.10)
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=True) is False

    def test_0dte_rejects_low_volume_short_leg(self):
        c = self._make_contract(oi=200, volume=10, bid=1.00, ask=1.10)
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=True) is False

    def test_0dte_allows_low_volume_long_leg(self):
        """Long leg does not require volume check."""
        c = self._make_contract(oi=200, volume=0, bid=1.00, ask=1.10)
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=False) is True

    def test_0dte_rejects_wide_spread(self):
        c = self._make_contract(oi=200, volume=80, bid=0.80, ask=1.20)
        # spread = 0.40, mid = 1.00, pct = 0.40 > 0.15
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=True) is False

    def test_0dte_accepts_tight_spread(self):
        c = self._make_contract(oi=200, volume=80, bid=0.95, ask=1.05)
        # spread = 0.10, mid = 1.00, pct = 0.10 < 0.15
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=True) is True

    # --- Swing tests ---
    def test_swing_passes_good_contract(self):
        c = self._make_contract(oi=80, volume=20, bid=2.00, ask=2.30)
        assert passes_liquidity_gate(c, "SWING", is_short_leg=True) is True

    def test_swing_rejects_low_oi(self):
        c = self._make_contract(oi=20, volume=20, bid=2.00, ask=2.30)
        assert passes_liquidity_gate(c, "SWING", is_short_leg=True) is False

    def test_swing_rejects_low_volume_short_leg(self):
        c = self._make_contract(oi=80, volume=5, bid=2.00, ask=2.30)
        assert passes_liquidity_gate(c, "SWING", is_short_leg=True) is False

    def test_swing_allows_low_volume_long_leg(self):
        c = self._make_contract(oi=80, volume=0, bid=2.00, ask=2.30)
        assert passes_liquidity_gate(c, "SWING", is_short_leg=False) is True

    def test_0dte_long_leg_relaxed_oi(self):
        """Long leg uses min_oi_long=20, not min_oi=100."""
        c = self._make_contract(oi=25, volume=0, bid=0.90, ask=1.10)
        # spread=0.20, mid=1.00, pct=0.20 < 0.33 → spread OK, OI 25 >= 20 → pass
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=False) is True
        c2 = self._make_contract(oi=15, volume=0, bid=0.90, ask=1.10)
        # OI 15 < 20 → fail
        assert passes_liquidity_gate(c2, "0-DTE", is_short_leg=False) is False

    def test_0dte_long_leg_no_spread_check(self):
        """Long legs are exempt from bid-ask spread check — far OTM options
        have tiny premiums where even a nickel spread gives huge percentages."""
        c = self._make_contract(oi=200, volume=0, bid=0.05, ask=0.10)
        # spread=100% but long leg is exempt → pass (OI 200 >= 20)
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=False) is True

    def test_zero_mid_returns_false(self):
        """If mid is zero (both bid and ask are 0), reject."""
        c = self._make_contract(oi=200, volume=80, bid=0, ask=0)
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=True) is False

    def test_handles_none_bid_ask(self):
        """Schwab returns bid=null for deep OTM options — should not crash."""
        c = {"oi": 200, "volume": 100, "bid": None, "ask": None, "mark": 0.50}
        result = passes_liquidity_gate(c, "0-DTE", is_short_leg=True)
        assert result is False  # mid=0 when bid/ask are None -> reject

    def test_penny_wide_cheap_option_passes(self):
        """A 1¢-wide market on a 5¢ mid is the tightest possible market —
        rejecting it because 1/5 = 20% > 15% would lock out the entire
        far-OTM portion of 0-DTE chains on $700 underlyings.

        Regression test for the 2026-04-28 zero-signals bug."""
        c = self._make_contract(oi=200, volume=80, bid=0.04, ask=0.05)
        # abs_spread = 0.01 (≤ MIN_ABS_SPREAD=0.02) → bypass % check → pass
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=True) is True

    def test_two_cent_wide_at_low_mid_passes(self):
        """abs_spread exactly at MIN_ABS_SPREAD (2¢) bypasses the % gate."""
        c = self._make_contract(oi=200, volume=80, bid=0.04, ask=0.06)
        # abs_spread = 0.02 == MIN_ABS_SPREAD → bypass; pct would be 40% but ignored
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=True) is True

    def test_three_cent_wide_at_low_mid_rejected(self):
        """A 3¢ market is wider than MIN_ABS_SPREAD, so the % gate applies.
        On a $0.06 mid, that's 50% — well above the 15% threshold."""
        c = self._make_contract(oi=200, volume=80, bid=0.045, ask=0.075)
        # abs_spread = 0.03 > MIN_ABS_SPREAD → % gate applies → reject
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=True) is False

    def test_pct_gate_still_applies_for_normal_priced_options(self):
        """Once abs_spread exceeds MIN_ABS_SPREAD, the % gate still rejects
        wide markets. A 40¢ spread on a $1 mid (40%) should still fail."""
        c = self._make_contract(oi=200, volume=80, bid=0.80, ask=1.20)
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=True) is False

    def test_handles_none_bid_ask_long_leg(self):
        """Long leg with None bid/ask should still pass if OI is sufficient."""
        c = {"oi": 25, "volume": 0, "bid": None, "ask": None, "mark": 0.50}
        result = passes_liquidity_gate(c, "0-DTE", is_short_leg=False)
        assert result is True  # Long leg: only OI matters, bid/ask exempt

    def test_handles_none_volume(self):
        """Volume can also be None from API."""
        c = {"oi": 200, "volume": None, "bid": 1.00, "ask": 1.10, "mark": 1.05}
        result = passes_liquidity_gate(c, "0-DTE", is_short_leg=True)
        assert result is False  # None volume treated as 0, below 50 threshold

    def test_unknown_trade_type_returns_true(self):
        """Unknown trade types should not be filtered — return True."""
        c = self._make_contract(oi=1, volume=0, bid=0.01, ask=10.00)
        assert passes_liquidity_gate(c, "UNKNOWN", is_short_leg=True) is True


class TestPassesLiquidityGatePreMarket:
    """When options market is closed, the gate softens volume + spread
    checks so pre-market scans don't return zero signals while volume
    data is still flat across the chain. OI check still applies."""

    @pytest.fixture(autouse=True)
    def _force_market_closed(self, monkeypatch):
        import scanner_engine
        monkeypatch.setattr(scanner_engine, "_is_options_market_open", lambda: False)

    def test_low_volume_passes_pre_market(self):
        c = {"oi": 200, "volume": 0, "bid": 1.00, "ask": 1.10, "mark": 1.05}
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=True) is True

    def test_wide_spread_passes_pre_market(self):
        c = {"oi": 200, "volume": 80, "bid": 0.80, "ask": 1.20, "mark": 1.00}
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=True) is True

    def test_low_oi_still_rejected_pre_market(self):
        """OI is a static-data check that remains valid pre-market."""
        c = {"oi": 10, "volume": 0, "bid": 1.00, "ask": 1.10, "mark": 1.05}
        assert passes_liquidity_gate(c, "0-DTE", is_short_leg=True) is False


class TestScreenSpreadsLiquidityIntegration:
    """Verify screen_spreads rejects illiquid contracts.

    Same rationale as TestPassesLiquidityGate: pin market-open so the
    strict liquidity gate is exercised regardless of when the suite runs.
    """

    @pytest.fixture(autouse=True)
    def _force_market_open(self, monkeypatch):
        import scanner_engine
        monkeypatch.setattr(scanner_engine, "_is_options_market_open", lambda: True)

    def _mock_chain(self, short_oi=200, short_vol=100, long_oi=200, long_vol=50,
                    short_bid=1.50, short_ask=1.60, long_bid=0.30, long_ask=0.33):
        """Build a minimal mock chain with one PCS candidate."""
        exp_key = "2026-04-14:1"
        return {
            "underlyingPrice": 530.0,
            "putExpDateMap": {
                exp_key: {
                    "530.0": [{
                        "strikePrice": 530.0, "delta": -0.15,
                        "mark": (short_bid + short_ask) / 2,
                        "bid": short_bid, "ask": short_ask,
                        "theta": -0.05, "vega": 0.10, "gamma": 0.01,
                        "volatility": 25.0,
                        "totalVolume": short_vol, "openInterest": short_oi,
                    }],
                    "525.0": [{
                        "strikePrice": 525.0, "delta": -0.08,
                        "mark": (long_bid + long_ask) / 2,
                        "bid": long_bid, "ask": long_ask,
                        "theta": -0.02, "vega": 0.05, "gamma": 0.005,
                        "volatility": 26.0,
                        "totalVolume": long_vol, "openInterest": long_oi,
                    }],
                }
            },
            "callExpDateMap": {}
        }

    def test_rejects_low_oi_short_leg(self):
        chain = self._mock_chain(short_oi=10)
        results = screen_spreads(chain, "TEST", 0, 1, -0.25, -0.08, 0.08, 0.25, 0.05, "0-DTE", widths=[5])
        assert len(results) == 0

    def test_rejects_low_volume_short_leg(self):
        chain = self._mock_chain(short_vol=5)
        results = screen_spreads(chain, "TEST", 0, 1, -0.25, -0.08, 0.08, 0.25, 0.05, "0-DTE", widths=[5])
        assert len(results) == 0

    def test_rejects_low_oi_long_leg(self):
        chain = self._mock_chain(long_oi=10)
        results = screen_spreads(chain, "TEST", 0, 1, -0.25, -0.08, 0.08, 0.25, 0.05, "0-DTE", widths=[5])
        assert len(results) == 0

    def test_accepts_liquid_contract(self):
        chain = self._mock_chain()
        results = screen_spreads(chain, "TEST", 0, 1, -0.25, -0.08, 0.08, 0.25, 0.05, "0-DTE", widths=[5])
        assert len(results) >= 1


class TestStrikeValidityWindow:
    """EM window check: 0-DTE uses intraday decay, swing uses DTE curve."""

    def _now_ct(self, hour, minute=0):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(2026, 4, 24, hour, minute, tzinfo=ZoneInfo("America/Chicago"))

    def test_zero_em_passthrough(self):
        from scanner_engine import is_strike_in_expected_move_window
        assert is_strike_in_expected_move_window(
            strike=100.0, spot=530.0, daily_expected_move=0,
            dte=0, trade_type="0-DTE", now_ct=self._now_ct(10, 0),
        ) is True

    # --- 0-DTE intraday decay ---
    def test_0dte_open_window(self):
        # daily_em=$5 at open -> remaining_em=$5 -> window [$3.09, $15.00]
        from scanner_engine import is_strike_in_expected_move_window
        now = self._now_ct(8, 30)
        spot = 100.0
        # distance=10 -> in window
        assert is_strike_in_expected_move_window(
            90.0, spot, 5.0, 0, "0-DTE", now_ct=now) is True
        # distance=2 -> below 0.618x = 3.09
        assert is_strike_in_expected_move_window(
            98.0, spot, 5.0, 0, "0-DTE", now_ct=now) is False
        # distance=16 -> above 3.0x = 15.0
        assert is_strike_in_expected_move_window(
            84.0, spot, 5.0, 0, "0-DTE", now_ct=now) is False

    def test_0dte_decayed_window_midday(self):
        # 12:00 CT, daily_em=$5 -> rem = 5*sqrt(3/6.5) ~ 3.397
        # window: [0.618*3.397, 3.00*3.397] = [2.10, 10.19]
        from scanner_engine import is_strike_in_expected_move_window
        now = self._now_ct(12, 0)
        spot = 100.0
        assert is_strike_in_expected_move_window(
            93.0, spot, 5.0, 0, "0-DTE", now_ct=now) is True   # dist=7, in [2.1, 10.19]
        assert is_strike_in_expected_move_window(
            99.0, spot, 5.0, 0, "0-DTE", now_ct=now) is False  # dist=1 < 2.10

    def test_0dte_after_close_rejects_all(self):
        from scanner_engine import is_strike_in_expected_move_window
        now = self._now_ct(15, 30)
        # rem_em = 0 -> any nonzero distance fails
        assert is_strike_in_expected_move_window(
            90.0, 100.0, 5.0, 0, "0-DTE", now_ct=now) is False

    # --- Swing DTE curve ---
    def test_swing_dte_1_window(self):
        # daily_em=$5, DTE=1 -> period_em=$5, window 1.5x-2.5x = [$7.50, $12.50]
        from scanner_engine import is_strike_in_expected_move_window
        spot = 100.0
        assert is_strike_in_expected_move_window(
            90.0, spot, 5.0, 1, "SWING") is True   # dist=10
        assert is_strike_in_expected_move_window(
            93.0, spot, 5.0, 1, "SWING") is False  # dist=7 < 7.5
        assert is_strike_in_expected_move_window(
            87.0, spot, 5.0, 1, "SWING") is False  # dist=13 > 12.5

    def test_swing_dte_15_window(self):
        # daily_em=$5, DTE=15 -> period_em=5*sqrt(15)~19.36
        # window 0.5x-1.5x ~ [9.68, 29.05]
        from scanner_engine import is_strike_in_expected_move_window
        spot = 100.0
        assert is_strike_in_expected_move_window(
            85.0, spot, 5.0, 15, "SWING") is True   # dist=15
        assert is_strike_in_expected_move_window(
            95.0, spot, 5.0, 15, "SWING") is False  # dist=5 < 9.68
        assert is_strike_in_expected_move_window(
            70.0, spot, 5.0, 15, "SWING") is False  # dist=30 > 29.05

    # --- 0-DTE bucket DTE 1-4 uses dedicated 0.618-floor curve ---
    def test_0dte_bucket_dte_1_uses_zero_dte_curve(self):
        """DTE=1 in the 0-DTE bucket uses min=0.618; max preserved at 2.50."""
        from scanner_engine import is_strike_in_expected_move_window
        spot = 100.0
        # daily_em=$5, DTE=1 -> period_em=$5, window 0.618x-2.5x = [$3.09, $12.50]
        assert is_strike_in_expected_move_window(
            90.0, spot, 5.0, 1, "0-DTE") is True
        assert is_strike_in_expected_move_window(
            98.0, spot, 5.0, 1, "0-DTE") is False  # dist=2 < 3.09
        assert is_strike_in_expected_move_window(
            87.0, spot, 5.0, 1, "0-DTE") is False  # dist=13 > 12.5

    def test_0dte_bucket_dte_4_uses_zero_dte_curve(self):
        """DTE=4 in the 0-DTE bucket: min=0.618 flat, max preserved at 2.10."""
        from scanner_engine import is_strike_in_expected_move_window
        spot = 100.0
        # DTE=4 -> period_em=5*sqrt(4)=10
        # 0-DTE bucket curve: min=0.618, max=2.10 -> window [$6.18, $21.00]
        assert is_strike_in_expected_move_window(
            85.0, spot, 5.0, 4, "0-DTE") is True   # dist=15
        assert is_strike_in_expected_move_window(
            95.0, spot, 5.0, 4, "0-DTE") is False  # dist=5 < 6.18
        assert is_strike_in_expected_move_window(
            78.0, spot, 5.0, 4, "0-DTE") is False  # dist=22 > 21.00


class TestDetectStrikeIncrement:
    """Test strike increment detection from chain strikes."""

    def test_detects_1_dollar_increment(self):
        strikes = [525.0, 526.0, 527.0, 528.0, 529.0, 530.0]
        assert detect_strike_increment(strikes) == 1.0

    def test_detects_5_dollar_increment(self):
        strikes = [500.0, 505.0, 510.0, 515.0, 520.0]
        assert detect_strike_increment(strikes) == 5.0

    def test_detects_half_dollar_increment(self):
        strikes = [29.0, 29.5, 30.0, 30.5, 31.0]
        assert detect_strike_increment(strikes) == 0.5

    def test_empty_returns_none(self):
        assert detect_strike_increment([]) is None

    def test_single_strike_returns_none(self):
        assert detect_strike_increment([530.0]) is None

    def test_handles_mixed_increments(self):
        """When increments vary, return the most common minimum gap."""
        strikes = [525.0, 526.0, 527.0, 530.0, 535.0]
        assert detect_strike_increment(strikes) == 1.0

    def test_handles_floating_point_rounding(self):
        """Strikes that are valid multiples but produce floating-point remainders
        should not be rejected. E.g., 2.5 % 0.5 can produce tiny non-zero values."""
        strikes = [520.0, 522.5, 525.0, 527.5, 530.0]
        inc = detect_strike_increment(strikes)
        assert inc == 2.5


class TestScreenSpreadsStrikeValidity:
    """Verify screen_spreads filters strikes outside expected move window."""

    # The EM window is now sized off the EXPIRATION's own ATM IV (2026-08-07),
    # so the chain's volatility -- not just the `daily_expected_move` argument --
    # determines the window. 28.84 vol at underlying 530 reproduces exactly the
    # daily EM of 8.0 these tests pass and were written against
    # (530 * 0.2884 * sqrt(1/365) = 8.00), keeping every window boundary below
    # identical. The old 25.0 implied 6.94, which moved the 2.50x outer bound
    # from 20.0 to 17.35 and pushed the dist-20 short strike -- deliberately
    # placed ON the boundary -- out of the window.
    _EM8_VOL = 28.84

    def _mock_chain(self, put_strikes, underlying=530.0, marks=None,
                    volatility=_EM8_VOL):
        """Build a chain with given put strikes, all with good liquidity.
        marks: optional list of mark prices corresponding to put_strikes."""
        exp_key = "2026-04-14:1"
        put_map = {}
        for i, s in enumerate(put_strikes):
            mark = marks[i] if marks else 1.50
            put_map[str(float(s))] = [{
                "strikePrice": float(s), "delta": -0.15,
                "mark": mark, "bid": mark - 0.05, "ask": mark + 0.05,
                "theta": -0.05, "vega": 0.10, "gamma": 0.01,
                "volatility": volatility,
                "totalVolume": 200, "openInterest": 500,
            }]
        return {
            "underlyingPrice": underlying,
            "putExpDateMap": {exp_key: put_map},
            "callExpDateMap": {}
        }

    def test_rejects_strikes_beyond_expected_move(self):
        """With spot=530, daily_em=8, 0-DTE max distance = 8.
        Strikes at 480 and 475 are 50+ pts away -> should be filtered."""
        chain = self._mock_chain([480.0, 475.0])
        results = screen_spreads(chain, "TEST", 0, 1, -0.30, -0.01, 0.01, 0.30,
                                 0.01, "0-DTE", spot=530.0, daily_expected_move=8.0, widths=[5])
        assert len(results) == 0

    def test_accepts_strikes_within_expected_move(self):
        """With spot=530, daily_em=8 at 8:30 CT (full remaining EM), 0-DTE window [12, 24].
        Strikes at 510 and 507 (dist=20 and 23) pass window. Short 510 (mark=2.00) minus long 507 (mark=1.00) = $1 credit on $3 width = 33%."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now_ct = datetime(2026, 4, 24, 8, 30, tzinfo=ZoneInfo("America/Chicago"))
        chain = self._mock_chain([510.0, 507.0], marks=[2.00, 1.00])
        results = screen_spreads(chain, "TEST", 0, 1, -0.30, -0.01, 0.01, 0.30,
                                 0.01, "0-DTE", spot=530.0, daily_expected_move=8.0, widths=[3],
                                 now_ct=now_ct)
        assert len(results) >= 1

    def test_long_leg_outside_em_window_still_allowed(self):
        """New 0-DTE window [12, 24] at 8:30 CT applies to short leg only.
        Short at 510 (dist=20, in window) + long at 505 (dist=25, just outside but used as long leg)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now_ct = datetime(2026, 4, 24, 8, 30, tzinfo=ZoneInfo("America/Chicago"))
        strikes = [520.0, 515.0, 510.0, 505.0, 500.0, 495.0]
        marks = [0.30, 0.80, 2.50, 1.20, 0.60, 0.20]
        chain = self._mock_chain(strikes, marks=marks)
        results = screen_spreads(chain, "TEST", 0, 1, -0.30, -0.01, 0.01, 0.30,
                                 0.01, "0-DTE", spot=530.0, daily_expected_move=8.0, widths=[5],
                                 now_ct=now_ct)
        assert len(results) >= 1

    def test_increment_filter_handles_float_rounding(self):
        """Strike increment filter should not drop valid strikes due to float imprecision."""
        strikes = [535.0, 532.5, 530.0, 527.5, 525.0, 522.5]
        marks =   [6.00,  4.50,  2.80,  1.50,  0.60,  0.20]
        chain = self._mock_chain(strikes, underlying=540.0, marks=marks)
        results = screen_spreads(chain, "TEST", 0, 1, -0.90, -0.01, 0.01, 0.90,
                                 0.01, "0-DTE", widths=[2.5])
        assert len(results) >= 1

    def test_no_filtering_without_expected_move(self):
        """When spot/daily_em not provided, no strike filtering occurs."""
        chain = self._mock_chain([480.0, 475.0])
        # Without spot/daily_em, these far strikes should not be filtered
        results = screen_spreads(chain, "TEST", 0, 1, -0.30, -0.01, 0.01, 0.30,
                                 0.01, "0-DTE", widths=[5])
        # They may still fail other checks (delta, credit) but won't fail EM check
        # Just verify no crash
        assert isinstance(results, list)


class TestPositionSizing:
    """Tests for calculate_position_size()."""

    def test_basic_position_size(self):
        """$100k account, 5% risk, $5 max loss = $500 risk per contract -> 10 contracts."""
        assert calculate_position_size(5.0, account_size=100000, max_risk_pct=0.05) == 10

    def test_zero_max_loss_returns_zero(self):
        assert calculate_position_size(0, account_size=100000, max_risk_pct=0.05) == 0

    def test_negative_max_loss_returns_zero(self):
        assert calculate_position_size(-1.0, account_size=100000, max_risk_pct=0.05) == 0

    def test_large_max_loss_returns_small_size(self):
        """$100k account, 5% risk, $50 max loss -> 1 contract."""
        assert calculate_position_size(50.0, account_size=100000, max_risk_pct=0.05) == 1

    def test_fractional_rounds_down(self):
        """$100k account, 5% risk, $7 max loss = $700/ct -> 7.14 -> 7 contracts."""
        assert calculate_position_size(7.0, account_size=100000, max_risk_pct=0.05) == 7


class TestCalcTechnicalsRSI:
    def _make_hist(self, closes):
        candles = [{"close": c, "high": c * 1.01, "low": c * 0.99, "datetime": i} for i, c in enumerate(closes)]
        return {"candles": candles}

    def test_rsi_present_in_result(self):
        closes = [100 + i * 0.5 for i in range(60)]
        result = calc_technicals(self._make_hist(closes))
        assert result is not None
        assert "rsi14" in result
        assert 0 <= result["rsi14"] <= 100

    def test_rsi_high_for_strong_uptrend(self):
        closes = [100 + i * 2.0 for i in range(60)]
        result = calc_technicals(self._make_hist(closes))
        assert result["rsi14"] > 60

    def test_rsi_low_for_strong_downtrend(self):
        closes = [200 - i * 2.0 for i in range(60)]
        result = calc_technicals(self._make_hist(closes))
        assert result["rsi14"] < 40


class TestIVRankFloor:
    def test_min_iv_rank_constants_exist(self):
        assert "0-DTE" in MIN_IV_RANK
        assert "SWING" in MIN_IV_RANK
        assert MIN_IV_RANK["0-DTE"] >= 20
        assert MIN_IV_RANK["SWING"] >= 15


def test_min_iv_rank_floors():
    from scanner_engine import MIN_IV_RANK
    assert MIN_IV_RANK["0-DTE"] == 35
    assert MIN_IV_RANK["SWING"] == 30


class TestDeltaSanityRail:
    def test_constant_value(self):
        from scanner_engine import DELTA_SANITY_MAX
        assert DELTA_SANITY_MAX == 0.40


class TestSuggestedLimitPrice:
    def _mock_chain(self):
        exp_key = "2026-04-14:1"
        return {
            "underlyingPrice": 530.0,
            "putExpDateMap": {
                exp_key: {
                    "525.0": [{"strikePrice": 525.0, "delta": -0.15, "mark": 2.00,
                               "bid": 1.90, "ask": 2.10, "theta": -0.05, "vega": 0.10,
                               "gamma": 0.01, "volatility": 25.0,
                               "totalVolume": 200, "openInterest": 500}],
                    "520.0": [{"strikePrice": 520.0, "delta": -0.08, "mark": 0.50,
                               "bid": 0.45, "ask": 0.55, "theta": -0.02, "vega": 0.05,
                               "gamma": 0.005, "volatility": 26.0,
                               "totalVolume": 100, "openInterest": 300}],
                }
            },
            "callExpDateMap": {}
        }

    def test_suggested_limit_present(self):
        chain = self._mock_chain()
        results = screen_spreads(chain, "TEST", 0, 1, -0.30, -0.01, 0.01, 0.30,
                                 0.01, "0-DTE", widths=[5])
        assert len(results) >= 1
        assert "suggested_limit" in results[0]

    def test_suggested_limit_between_bid_and_mid(self):
        chain = self._mock_chain()
        results = screen_spreads(chain, "TEST", 0, 1, -0.30, -0.01, 0.01, 0.30,
                                 0.01, "0-DTE", widths=[5])
        sig = results[0]
        bid, ask = sig["bid"], sig["ask"]
        mid = (bid + ask) / 2
        assert bid <= sig["suggested_limit"] <= mid


class TestVIXTermStructure:
    def test_contango(self):
        result = calc_vix_term_structure(vix=20, vix9d=18, vix3m=22)
        assert result["structure"] == "CONTANGO"

    def test_backwardation(self):
        result = calc_vix_term_structure(vix=25, vix9d=30, vix3m=22)
        assert result["structure"] == "BACKWARDATION"

    def test_mixed(self):
        result = calc_vix_term_structure(vix=20, vix9d=18, vix3m=18)
        assert result["structure"] == "MIXED"

    def test_missing_data(self):
        result = calc_vix_term_structure(vix=20, vix9d=0, vix3m=0)
        assert result["structure"] == "UNKNOWN"


class TestEarningsAvoidance:
    def test_no_conflict_when_earnings_after_expiration(self):
        assert check_earnings_conflict("2026-05-15", "2026-05-10") is False

    def test_conflict_when_earnings_before_expiration(self):
        assert check_earnings_conflict("2026-05-05", "2026-05-10") is True

    def test_conflict_when_earnings_same_day(self):
        assert check_earnings_conflict("2026-05-10", "2026-05-10") is True

    def test_no_conflict_when_no_earnings_date(self):
        assert check_earnings_conflict(None, "2026-05-10") is False

    def test_no_conflict_when_earnings_far_before_entry(self):
        assert check_earnings_conflict("2026-03-01", "2026-05-10") is False


class TestDynamicMinCredit:
    def test_wide_spread_uses_pct(self):
        min_cr = calc_effective_min_credit(width=25, min_cr_pct=0.15)
        assert min_cr == 0.15

    def test_narrow_spread_uses_abs_floor(self):
        min_cr = calc_effective_min_credit(width=2, min_cr_pct=0.15, min_abs_credit=0.50)
        assert min_cr == 0.25  # 0.50/2 = 0.25 > 0.15

    def test_default_abs_floor(self):
        min_cr = calc_effective_min_credit(width=1, min_cr_pct=0.10)
        assert min_cr == 0.25  # 0.25/1 = 0.25 > 0.10


class TestExpectedPnL:
    def test_breakeven_at_zero_pnl(self):
        """At PoP matching break-even: credit*p = max_loss*(1-p).
        credit=1, max_loss=4 -> breakeven p = 4/5 = 0.8 (80%)."""
        pnl = calc_expected_pnl(credit=1.0, max_loss=4.0, pop_pct=80.0, contracts=10)
        assert abs(pnl) < 1.0

    def test_high_pop_positive_pnl(self):
        """90% PoP: 0.9*1 - 0.1*4 = 0.5 per share * 10 * 100 = $500."""
        pnl = calc_expected_pnl(credit=1.0, max_loss=4.0, pop_pct=90.0, contracts=10)
        assert pnl == pytest.approx(500.0)

    def test_low_pop_negative_pnl(self):
        """50% PoP: 0.5*1 - 0.5*4 = -1.5 per share * 10 * 100 = -$1500."""
        pnl = calc_expected_pnl(credit=1.0, max_loss=4.0, pop_pct=50.0, contracts=10)
        assert pnl == pytest.approx(-1500.0)


class TestContractsForTarget:
    def test_default_10_when_credit_produces_exactly_1000(self):
        """credit=1.0 -> 1000/(1*100)=10."""
        assert calc_contracts_for_target(credit=1.0) == 10

    def test_rounds_to_nearest_multiple_of_5(self):
        """credit=0.40 -> 25 contracts exactly."""
        assert calc_contracts_for_target(credit=0.40) == 25

    def test_rounds_up_when_closer_to_higher(self):
        """credit=0.50 -> 20 contracts exactly."""
        assert calc_contracts_for_target(credit=0.50) == 20

    def test_rounds_to_nearest_when_between(self):
        """credit=0.60 -> 16.66, rounds to 15."""
        result = calc_contracts_for_target(credit=0.60)
        assert result == 15

    def test_minimum_is_5_contracts(self):
        """credit=10.0 -> 1 contract raw, floored to minimum 5."""
        assert calc_contracts_for_target(credit=10.0) == 5

    def test_zero_credit_returns_zero(self):
        assert calc_contracts_for_target(credit=0) == 0

    def test_negative_credit_returns_zero(self):
        assert calc_contracts_for_target(credit=-0.5) == 0

    def test_custom_profit_target(self):
        """$500 target, $1 credit -> 5 contracts."""
        assert calc_contracts_for_target(credit=1.0, profit_target=500) == 5


class TestSignalPnLFields:
    """Verify 0-DTE signals include expected P&L and contract sizing fields."""

    def _mock_chain(self):
        exp_key = "2026-04-19:0"
        return {
            "underlyingPrice": 530.0,
            "putExpDateMap": {
                exp_key: {
                    "510.0": [{"strikePrice": 510.0, "delta": -0.10, "mark": 1.00,
                               "bid": 0.98, "ask": 1.02, "theta": -0.05, "vega": 0.10,
                               "gamma": 0.01, "volatility": 25.0,
                               "totalVolume": 200, "openInterest": 500}],
                    # long mark 0.20 -> credit/width ~0.158, clears the delta
                    # margin (|delta| 0.10 + EDGE_MARGIN 0.02 = 0.12)
                    "505.0": [{"strikePrice": 505.0, "delta": -0.05, "mark": 0.20,
                               "bid": 0.18, "ask": 0.22, "theta": -0.02, "vega": 0.05,
                               "gamma": 0.005, "volatility": 26.0,
                               "totalVolume": 100, "openInterest": 300}],
                }
            },
            "callExpDateMap": {}
        }

    def test_manual_injection_produces_expected_fields(self):
        """Verifies helper functions work end-to-end on a realistic signal.
        Production wiring (next task) does this injection in run_full_scan."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now_ct = datetime(2026, 4, 24, 8, 30, tzinfo=ZoneInfo("America/Chicago"))
        chain = self._mock_chain()
        # spot=530, daily_em=8 at 8:30 CT -> 0-DTE window [12, 24]. Strike 510 dist=20, in window.
        results = screen_spreads(chain, "TEST", 0, 0, -0.30, -0.01, 0.01, 0.30,
                                 0.01, "0-DTE", spot=530.0, daily_expected_move=8.0, widths=[5],
                                 now_ct=now_ct)
        assert len(results) >= 1

        for s in results:
            cr = s["credit"]; ml = s["max_loss"]; pop = s["pop_pct"]
            s["contracts_for_target"] = calc_contracts_for_target(cr)
            s["expected_pnl_10"] = calc_expected_pnl(cr, ml, pop, CONTRACT_DEFAULT)
            s["expected_pnl_target"] = calc_expected_pnl(cr, ml, pop, s["contracts_for_target"])
            s["max_profit_target"] = cr * s["contracts_for_target"] * 100

        sig = results[0]
        assert "contracts_for_target" in sig
        assert "expected_pnl_10" in sig
        assert "expected_pnl_target" in sig
        assert "max_profit_target" in sig
        assert sig["contracts_for_target"] > 0
        assert sig["max_profit_target"] > 0


class TestScreenSpreadsAutoSelectWidth:
    def _build_chain(self, strikes_and_marks, underlying=530.0):
        """Build a chain with the given strikes and marks (put side). Good liquidity."""
        exp_key = "2026-04-19:0"
        put_map = {}
        for sk, mk in strikes_and_marks:
            put_map[str(float(sk))] = [{
                "strikePrice": float(sk), "delta": -0.10,
                "mark": mk, "bid": mk - 0.02, "ask": mk + 0.02,
                "theta": -0.05, "vega": 0.10, "gamma": 0.01,
                "volatility": 25.0,
                "totalVolume": 200, "openInterest": 500,
            }]
        return {"underlyingPrice": underlying, "putExpDateMap": {exp_key: put_map},
                "callExpDateMap": {}}

    def test_auto_select_picks_best_width(self):
        """Short at 510 (mark=2.50). Longs at 505 (width=5) and 500 (width=10).
        Realistic fill credits are mid − 0.008 (±0.02 quotes): 0.992 and 1.992.
        E[PnL] = $492 (w=5, 10 contracts) vs $496 (w=10, 5 contracts) — the
        realistic haircut hurts the doubled contract count more, width 10 wins."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now_ct = datetime(2026, 4, 24, 8, 30, tzinfo=ZoneInfo("America/Chicago"))
        strikes = [(510.0, 2.50), (505.0, 1.50), (500.0, 0.50)]
        chain = self._build_chain(strikes)
        results = screen_spreads(chain, "TEST", 0, 0, -0.30, -0.01, 0.01, 0.30,
                                 0.01, "0-DTE", spot=530.0, daily_expected_move=8.0,
                                 widths=None, now_ct=now_ct)
        sig_510 = next((s for s in results if s["short_strike"] == 510.0), None)
        assert sig_510 is not None, "Expected a signal for short_strike=510"
        assert sig_510["width"] == 10.0
        assert sig_510["credit"] == 1.99

    def test_auto_select_drops_short_when_min_credit_pct_fails(self):
        """All widths produce credit/width below the regime floor -> short dropped."""
        # width=1: credit=0.02 -> 2% fails min 10%
        # width=2: credit=0.04 -> 2% fails
        # width=5: credit=0.06 -> 1.2% fails
        strikes = [(510.0, 0.50), (509.0, 0.48), (508.0, 0.46), (505.0, 0.44)]
        chain = self._build_chain(strikes)
        results = screen_spreads(chain, "TEST", 0, 0, -0.30, -0.01, 0.01, 0.30,
                                 0.10, "0-DTE", spot=530.0, daily_expected_move=8.0,
                                 widths=None)
        assert results == []

    def test_explicit_widths_still_work_for_backward_compat(self):
        """If widths=[5] is passed explicitly, only width-5 spreads are attempted.
        The 510-short has a 500-long available (width=10) but it's ignored with widths=[5]."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now_ct = datetime(2026, 4, 24, 8, 30, tzinfo=ZoneInfo("America/Chicago"))
        strikes = [(510.0, 2.50), (505.0, 1.50), (500.0, 0.50)]
        chain = self._build_chain(strikes)
        results = screen_spreads(chain, "TEST", 0, 0, -0.30, -0.01, 0.01, 0.30,
                                 0.01, "0-DTE", spot=530.0, daily_expected_move=8.0,
                                 widths=[5], now_ct=now_ct)
        # Every signal should have width=5 (no auto-selection, only the explicit list)
        assert len(results) >= 1
        for sig in results:
            assert sig["width"] == 5.0
        # Find the short_strike=510 signal -- with widths=[5], realistic
        # credit = mid 1.00 − 0.008, rounded to 0.99 in the signal dict
        sig_510 = next((s for s in results if s["short_strike"] == 510.0), None)
        assert sig_510 is not None
        assert sig_510["width"] == 5.0
        assert sig_510["credit"] == 0.99

    def test_entry_credit_helper_used_by_both_paths(self):
        """Both width paths must price through _entry_credit — guard against
        the mid-credit expression creeping back into either call site."""
        import inspect
        import scanner_engine
        src = inspect.getsource(scanner_engine.screen_spreads)
        assert 'short["mark"] - lo["mark"]' not in src
        assert "_entry_credit" in src
        src_w = inspect.getsource(scanner_engine.select_best_width)
        assert 'short["mark"] - lo["mark"]' not in src_w
        assert "_entry_credit" in src_w

    def test_short_delta_above_ceiling_is_rejected(self):
        """A 0.38-delta short call must be rejected by MAX_ENTRY_SHORT_DELTA
        even though the EM window / 0.40 sanity rail would admit it."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now_ct = datetime(2026, 4, 24, 8, 30, tzinfo=ZoneInfo("America/Chicago"))
        # call map: short 600 at delta 0.38 (sits ~1 EM above spot 590), long 605.
        # Long mark kept low so credit/width ~0.44 CLEARS the edge-margin gate
        # (|delta| 0.38 + EDGE_MARGIN 0.02 = 0.40) — isolating the delta-ceiling behavior.
        calls = {"2026-04-19:0": {
            "600.0": [{"strikePrice": 600.0, "delta": 0.38, "mark": 3.0,
                       "bid": 2.98, "ask": 3.02, "theta": -0.05, "vega": 0.10,
                       "gamma": 0.01, "volatility": 25.0,
                       "totalVolume": 200, "openInterest": 500}],
            "605.0": [{"strikePrice": 605.0, "delta": 0.20, "mark": 0.80,
                       "bid": 0.78, "ask": 0.82, "theta": -0.03, "vega": 0.08,
                       "gamma": 0.01, "volatility": 25.0,
                       "totalVolume": 150, "openInterest": 400}]}}
        chain = {"underlyingPrice": 590.0, "putExpDateMap": {},
                 "callExpDateMap": calls}
        results = screen_spreads(chain, "TEST", 0, 0, -0.40, -1e-6, 1e-6, 0.40,
                                 0.10, "0-DTE", spot=590.0, daily_expected_move=8.0,
                                 widths=None, now_ct=now_ct)
        assert results == [], "delta ceiling should reject the over-ceiling short -> no signals"

    def test_delta_028_rejected_by_tightened_ceiling(self):
        """A 0.28-delta short (allowed at ceiling 0.30) is now rejected at 0.27."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now_ct = datetime(2026, 4, 24, 8, 30, tzinfo=ZoneInfo("America/Chicago"))
        calls = {"2026-04-19:0": {
            "600.0": [{"strikePrice": 600.0, "delta": 0.28, "mark": 3.0,
                       "bid": 2.98, "ask": 3.02, "theta": -0.05, "vega": 0.10,
                       "gamma": 0.01, "volatility": 25.0,
                       "totalVolume": 200, "openInterest": 500}],
            "605.0": [{"strikePrice": 605.0, "delta": 0.18, "mark": 0.80,
                       "bid": 0.78, "ask": 0.82, "theta": -0.03, "vega": 0.08,
                       "gamma": 0.01, "volatility": 25.0,
                       "totalVolume": 150, "openInterest": 400}]}}
        chain = {"underlyingPrice": 590.0, "putExpDateMap": {}, "callExpDateMap": calls}
        results = screen_spreads(chain, "TEST", 0, 0, -0.40, -1e-6, 1e-6, 0.40,
                                 0.10, "0-DTE", spot=590.0, daily_expected_move=8.0,
                                 widths=None, now_ct=now_ct)
        assert results == [], "0.28-delta short must be rejected at the 0.27 ceiling"


class TestIronCondorWidthTolerance:
    def test_builds_ic_with_mismatched_widths(self):
        """PCS width=5 + CCS width=10 should build an IC — max_loss uses max(widths)."""
        from scanner_engine import build_iron_condors

        pcs = {"symbol": "TEST", "type": "PCS", "expiration": "2026-04-19",
               "dte": 0, "short_strike": 520, "long_strike": 515,
               "short_mark": 1.50, "long_mark": 0.50,
               "width": 5, "credit": 1.00, "max_loss": 4.00, "rr_pct": 25.0,
               "pop_pct": 85.0, "short_delta": -0.15,
               "net_theta": -0.03, "breakeven": 519.0,
               "trade_type": "0-DTE", "underlying_price": 530.0}
        ccs = {"symbol": "TEST", "type": "CCS", "expiration": "2026-04-19",
               "dte": 0, "short_strike": 540, "long_strike": 550,
               "short_mark": 1.20, "long_mark": 0.20,
               "width": 10, "credit": 1.00, "max_loss": 9.00, "rr_pct": 11.1,
               "pop_pct": 85.0, "short_delta": 0.15,
               "net_theta": -0.03, "breakeven": 541.0,
               "trade_type": "0-DTE", "underlying_price": 530.0}

        result = build_iron_condors([pcs, ccs], max_n=2)
        assert len(result) == 1
        ic = result[0]
        # max_loss = max(5, 10) - (1.00 + 1.00) = 8
        assert ic["max_loss"] == 8.0
        assert ic["credit"] == 2.0


class TestSwingEMWindow:
    def test_anchor_dte_1(self):
        from scanner_engine import swing_em_window
        assert swing_em_window(1) == (1.50, 2.50)

    def test_anchor_dte_3(self):
        from scanner_engine import swing_em_window
        assert swing_em_window(3) == (1.25, 2.20)

    def test_anchor_dte_5(self):
        from scanner_engine import swing_em_window
        assert swing_em_window(5) == (1.00, 2.00)

    def test_anchor_dte_7(self):
        from scanner_engine import swing_em_window
        assert swing_em_window(7) == (0.85, 1.85)

    def test_anchor_dte_10(self):
        from scanner_engine import swing_em_window
        assert swing_em_window(10) == (0.70, 1.70)

    def test_anchor_dte_15(self):
        from scanner_engine import swing_em_window
        assert swing_em_window(15) == (0.50, 1.50)

    def test_interpolation_dte_2(self):
        from scanner_engine import swing_em_window
        # Halfway between (1, 1.50, 2.50) and (3, 1.25, 2.20)
        mn, mx = swing_em_window(2)
        assert abs(mn - 1.375) < 1e-9
        assert abs(mx - 2.350) < 1e-9

    def test_interpolation_dte_4(self):
        from scanner_engine import swing_em_window
        # Halfway between (3, 1.25, 2.20) and (5, 1.00, 2.00)
        mn, mx = swing_em_window(4)
        assert abs(mn - 1.125) < 1e-9
        assert abs(mx - 2.100) < 1e-9

    def test_interpolation_dte_12(self):
        from scanner_engine import swing_em_window
        # 2/5 of the way from (10, 0.70, 1.70) to (15, 0.50, 1.50)
        mn, mx = swing_em_window(12)
        assert abs(mn - 0.62) < 1e-9
        assert abs(mx - 1.62) < 1e-9

    def test_clamp_dte_below_min(self):
        from scanner_engine import swing_em_window
        assert swing_em_window(0) == (1.50, 2.50)
        assert swing_em_window(-3) == (1.50, 2.50)

    def test_clamp_dte_above_max(self):
        from scanner_engine import swing_em_window
        assert swing_em_window(20) == (0.50, 1.50)
        assert swing_em_window(45) == (0.50, 1.50)


class TestZeroDteEMWindow:
    def _now_ct(self, hour, minute=0):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        # Use a known trading day (2026-04-24, Friday)
        return datetime(2026, 4, 24, hour, minute, tzinfo=ZoneInfo("America/Chicago"))

    def test_open_full_em(self):
        from scanner_engine import zero_dte_em_window
        rem, mn, mx = zero_dte_em_window(self._now_ct(8, 30), 5.0)
        assert abs(rem - 5.0) < 1e-9
        assert (mn, mx) == (0.618, 3.00)

    def test_midday_decay(self):
        from scanner_engine import zero_dte_em_window
        import math
        # 12:00 CT -> 3.0 hours left -> rem = 5.0 * sqrt(3/6.5)
        rem, _, _ = zero_dte_em_window(self._now_ct(12, 0), 5.0)
        expected = 5.0 * math.sqrt(3.0 / 6.5)
        assert abs(rem - expected) < 1e-6

    def test_late_session_decay(self):
        from scanner_engine import zero_dte_em_window
        import math
        # 14:30 CT -> 0.5 hours left
        rem, _, _ = zero_dte_em_window(self._now_ct(14, 30), 5.0)
        expected = 5.0 * math.sqrt(0.5 / 6.5)
        assert abs(rem - expected) < 1e-6

    def test_pre_open_clamps_to_full(self):
        from scanner_engine import zero_dte_em_window
        rem, _, _ = zero_dte_em_window(self._now_ct(7, 0), 5.0)
        assert abs(rem - 5.0) < 1e-9

    def test_after_close_zero_em(self):
        from scanner_engine import zero_dte_em_window
        rem, _, _ = zero_dte_em_window(self._now_ct(15, 30), 5.0)
        assert rem == 0.0

    def test_at_close_zero_em(self):
        from scanner_engine import zero_dte_em_window
        rem, _, _ = zero_dte_em_window(self._now_ct(15, 0), 5.0)
        assert rem == 0.0

    def test_zero_daily_em_passthrough(self):
        from scanner_engine import zero_dte_em_window
        rem, _, _ = zero_dte_em_window(self._now_ct(10, 0), 0.0)
        assert rem == 0.0


class TestDirectionalEMWindow:
    """0.0x-0.618x of period EM — strike stays slightly-OTM through ATM."""

    def test_directional_window_returns_constant_band(self):
        from scanner_engine import directional_em_window
        # DTE-independent: always (0.0, 0.618)
        assert directional_em_window(1) == (0.0, 0.618)
        assert directional_em_window(4) == (0.0, 0.618)
        assert directional_em_window(15) == (0.0, 0.618)

    def test_is_strike_in_directional_em_window_at_dte_0(self):
        """At DTE=0, directional band uses zero_dte_em_window's remaining_em."""
        from scanner_engine import is_strike_in_expected_move_window
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime(2026, 4, 24, 8, 30, tzinfo=ZoneInfo("America/Chicago"))
        spot = 100.0
        # daily_em=5, at open -> remaining_em=5, directional band [0, 3.09]
        assert is_strike_in_expected_move_window(
            99.0, spot, 5.0, 0, "0-DTE", now_ct=now, mode="DIRECTIONAL") is True   # dist=1
        assert is_strike_in_expected_move_window(
            97.0, spot, 5.0, 0, "0-DTE", now_ct=now, mode="DIRECTIONAL") is True   # dist=3
        assert is_strike_in_expected_move_window(
            96.0, spot, 5.0, 0, "0-DTE", now_ct=now, mode="DIRECTIONAL") is False  # dist=4 > 3.09

    def test_is_strike_in_directional_em_window_at_dte_5_swing(self):
        """At DTE=5 SWING, period_em=5*sqrt(5)~11.18, directional band [0, 6.91]."""
        from scanner_engine import is_strike_in_expected_move_window
        spot = 100.0
        assert is_strike_in_expected_move_window(
            95.0, spot, 5.0, 5, "SWING", mode="DIRECTIONAL") is True   # dist=5
        assert is_strike_in_expected_move_window(
            93.0, spot, 5.0, 5, "SWING", mode="DIRECTIONAL") is False  # dist=7 > 6.91

    def test_premium_mode_unchanged(self):
        """mode='PREMIUM' (default) keeps existing behavior."""
        from scanner_engine import is_strike_in_expected_move_window
        spot = 100.0
        # DTE=1, daily_em=5, SWING bucket premium band 1.5-2.5 -> [7.5, 12.5]
        # dist=2 should be False under PREMIUM
        assert is_strike_in_expected_move_window(
            98.0, spot, 5.0, 1, "SWING") is False
        assert is_strike_in_expected_move_window(
            98.0, spot, 5.0, 1, "SWING", mode="PREMIUM") is False


class TestPositionPerspectiveFields:
    """Scanner must emit position-perspective Greeks and spread-level bid/ask
    so the post-entry analyzer doesn't have to recompute sign conventions.
    See docs/plans/2026-05-27-trade-detail-analysis-fixes-design.md."""

    @pytest.fixture(autouse=True)
    def _force_market_open(self, monkeypatch):
        import scanner_engine
        monkeypatch.setattr(scanner_engine, "_is_options_market_open", lambda: True)

    def _mock_chain(self):
        """Build a minimal liquid PCS candidate (mirrors TestScreenSpreadsLiquidityIntegration)."""
        exp_key = "2026-04-14:1"
        return {
            "underlyingPrice": 530.0,
            "putExpDateMap": {
                exp_key: {
                    "530.0": [{
                        "strikePrice": 530.0, "delta": -0.15,
                        "mark": 1.55, "bid": 1.50, "ask": 1.60,
                        "theta": -0.05, "vega": 0.10, "gamma": 0.01,
                        "volatility": 25.0,
                        "totalVolume": 100, "openInterest": 200,
                    }],
                    "525.0": [{
                        "strikePrice": 525.0, "delta": -0.08,
                        "mark": 0.315, "bid": 0.30, "ask": 0.33,
                        "theta": -0.02, "vega": 0.05, "gamma": 0.005,
                        "volatility": 26.0,
                        "totalVolume": 50, "openInterest": 200,
                    }],
                }
            },
            "callExpDateMap": {}
        }

    def test_signal_includes_position_perspective_greeks(self):
        """Scanner output must include entry_net_delta_position and entry_net_theta_position."""
        chain = self._mock_chain()
        signals = screen_spreads(chain, "TEST", 0, 1, -0.25, -0.08, 0.08, 0.25, 0.05,
                                 "0-DTE", widths=[5])
        assert len(signals) > 0
        sig = signals[0]
        assert "entry_net_delta_position" in sig, "missing entry_net_delta_position field"
        assert "entry_net_theta_position" in sig, "missing entry_net_theta_position field"

        # Sign-convention check: entry_net_theta_position == -(leg-perspective net_theta)
        # because net_theta = short - long, entry_net_theta_position = long - short.
        if "net_theta" in sig:
            assert sig["entry_net_theta_position"] == pytest.approx(
                -sig["net_theta"], abs=1e-6,
            ), "position-theta should be negative of leg-perspective net_theta"

    def test_signal_includes_spread_level_bid_ask(self):
        """Scanner output must include spread_bid and spread_ask."""
        chain = self._mock_chain()
        signals = screen_spreads(chain, "TEST", 0, 1, -0.25, -0.08, 0.08, 0.25, 0.05,
                                 "0-DTE", widths=[5])
        assert len(signals) > 0
        sig = signals[0]
        assert "spread_bid" in sig
        assert "spread_ask" in sig

        # spread_bid <= credit (mark) <= spread_ask
        credit = sig.get("credit", 0)
        assert sig["spread_bid"] <= credit + 1e-6
        assert sig["spread_ask"] >= credit - 1e-6


class TestModeTagging:
    """Every signal produced by screen_spreads carries a `mode` field.

    Task 3 of the Directional Credit Spreads feature: the premium flow
    must explicitly tag its output with mode='PREMIUM' so future
    directional output (mode='DIRECTIONAL') is distinguishable at the
    source, not just at the DB layer.
    """

    @pytest.fixture(autouse=True)
    def _force_market_open(self, monkeypatch):
        import scanner_engine
        monkeypatch.setattr(scanner_engine, "_is_options_market_open", lambda: True)

    def _mock_chain(self):
        """Build a minimal liquid PCS candidate (mirrors TestPositionPerspectiveFields)."""
        exp_key = "2026-04-14:1"
        return {
            "underlyingPrice": 530.0,
            "putExpDateMap": {
                exp_key: {
                    "530.0": [{
                        "strikePrice": 530.0, "delta": -0.15,
                        "mark": 1.55, "bid": 1.50, "ask": 1.60,
                        "theta": -0.05, "vega": 0.10, "gamma": 0.01,
                        "volatility": 25.0,
                        "totalVolume": 100, "openInterest": 200,
                    }],
                    "525.0": [{
                        "strikePrice": 525.0, "delta": -0.08,
                        "mark": 0.315, "bid": 0.30, "ask": 0.33,
                        "theta": -0.02, "vega": 0.05, "gamma": 0.005,
                        "volatility": 26.0,
                        "totalVolume": 50, "openInterest": 200,
                    }],
                }
            },
            "callExpDateMap": {}
        }

    def test_premium_signals_tagged_mode_premium_explicit_widths(self):
        """Explicit-widths branch: every result tagged mode='PREMIUM'."""
        chain = self._mock_chain()
        signals = screen_spreads(chain, "TEST", 0, 1, -0.25, -0.08, 0.08, 0.25, 0.05,
                                 "0-DTE", widths=[5])
        assert len(signals) > 0
        assert all(s.get("mode") == "PREMIUM" for s in signals)

    def test_premium_signals_tagged_mode_premium_auto_width(self):
        """Auto-width branch (widths=None): every result tagged mode='PREMIUM'."""
        chain = self._mock_chain()
        signals = screen_spreads(chain, "TEST", 0, 1, -0.25, -0.08, 0.08, 0.25, 0.05,
                                 "0-DTE", widths=None)
        assert len(signals) > 0
        assert all(s.get("mode") == "PREMIUM" for s in signals)

    def test_iron_condors_tagged_mode_premium(self):
        """build_iron_condors output also tagged mode='PREMIUM' (ICs are premium by design)."""
        from scanner_engine import build_iron_condors
        # Construct paired PCS + CCS signals manually with the minimum fields IC builder needs.
        pcs = {
            "symbol": "TEST", "type": "PCS", "trade_type": "0-DTE",
            "expiration": "2026-04-14", "dte": 1,
            "short_strike": 530.0, "long_strike": 525.0,
            "short_mark": 1.55, "long_mark": 0.32,
            "width": 5, "credit": 1.23, "max_loss": 3.77,
            "rr_pct": 32.6, "pop_pct": 85.0,
            "short_delta": -0.15, "net_theta": -0.03,
            "breakeven": 528.77, "underlying_price": 530.0,
        }
        ccs = {
            "symbol": "TEST", "type": "CCS", "trade_type": "0-DTE",
            "expiration": "2026-04-14", "dte": 1,
            "short_strike": 535.0, "long_strike": 540.0,
            "short_mark": 1.40, "long_mark": 0.28,
            "width": 5, "credit": 1.12, "max_loss": 3.88,
            "rr_pct": 28.9, "pop_pct": 84.0,
            "short_delta": 0.15, "net_theta": -0.03,
            "breakeven": 536.12, "underlying_price": 530.0,
        }
        ics = build_iron_condors([pcs, ccs])
        assert len(ics) > 0
        assert all(ic.get("mode") == "PREMIUM" for ic in ics)


class TestScreenSpreadsEmptyChain:
    """Regression: screen_spreads must return [] cleanly when the inner loops
    never enter — not raise UnboundLocalError on the NO SPREADS log path.

    Trigger cases:
      - putExpDateMap and callExpDateMap both empty
      - Every expiration is outside [dte_min, dte_max]
      - underlyingPrice missing or zero (returns [] earlier, but covered here)
    """

    def _empty_chain(self):
        return {
            "underlyingPrice": 530.0,
            "putExpDateMap": {},
            "callExpDateMap": {},
        }

    def _dte_mismatched_chain(self):
        """Chain with one expiration at DTE=7 — caller will filter DTE 0-4."""
        return {
            "underlyingPrice": 530.0,
            "putExpDateMap": {"2026-04-21:7": {
                "525.0": [{"delta": -0.15, "mark": 1.0, "bid": 0.95,
                           "ask": 1.05, "theta": -0.05, "vega": 0.10,
                           "gamma": 0.01, "volatility": 25.0,
                           "totalVolume": 200, "openInterest": 500}],
            }},
            "callExpDateMap": {},
        }

    def test_empty_chain_returns_empty_list(self):
        """Both exp maps empty — no inner-loop iteration, must not crash."""
        result = screen_spreads(
            self._empty_chain(), "SPY", dte_min=0, dte_max=4,
            put_d_min=-0.20, put_d_max=-0.08,
            call_d_min=0.08, call_d_max=0.20,
            min_cr_pct=0.10, trade_type="0-DTE",
        )
        assert result == []

    def test_dte_filter_excludes_all_expirations(self):
        """Only expiration is DTE=7; caller filters DTE 0-4 — inner loop
        skips every iteration, must not crash."""
        result = screen_spreads(
            self._dte_mismatched_chain(), "SPY", dte_min=0, dte_max=4,
            put_d_min=-0.20, put_d_max=-0.08,
            call_d_min=0.08, call_d_max=0.20,
            min_cr_pct=0.10, trade_type="0-DTE",
        )
        assert result == []


class TestMomentumVeto:
    def _candles(self, closes, last_is_today=True):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Chicago")
        base = datetime(2026, 6, 11, 12, 0, tzinfo=tz)
        out = []
        n = len(closes)
        for i, c in enumerate(closes):
            day_offset = (n - 1 - i) if last_is_today else (n - i)
            dt = base - timedelta(days=day_offset)
            out.append({"datetime": int(dt.timestamp() * 1000), "close": c})
        return {"candles": out}

    def test_prior_close_skips_todays_forming_candle(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime(2026, 6, 11, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
        hist = self._candles([100.0, 101.0, 105.0])  # 105 is today's forming
        assert scanner_engine._prior_close(hist, now) == 101.0

    def test_prior_close_none_when_no_candles(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime(2026, 6, 11, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
        assert scanner_engine._prior_close({"candles": []}, now) is None

    def test_move_ratio_up(self):
        assert scanner_engine.intraday_move_ratio(106.0, 100.0, 10.0) == pytest.approx(0.6)

    def test_move_ratio_none_on_bad_inputs(self):
        assert scanner_engine.intraday_move_ratio(106.0, None, 10.0) is None
        assert scanner_engine.intraday_move_ratio(106.0, 100.0, 0) is None

    def test_veto_suppresses_ccs_when_up_extended(self):
        sigs = [{"type": "CCS"}, {"type": "PCS"}]
        assert [s["type"] for s in scanner_engine._apply_momentum_veto(sigs, 0.7)] == ["PCS"]

    def test_veto_suppresses_pcs_when_down_extended(self):
        sigs = [{"type": "CCS"}, {"type": "PCS"}]
        assert [s["type"] for s in scanner_engine._apply_momentum_veto(sigs, -0.7)] == ["CCS"]

    def test_veto_noop_inside_band_and_on_none(self):
        sigs = [{"type": "CCS"}, {"type": "PCS"}]
        assert scanner_engine._apply_momentum_veto(sigs, 0.3) == sigs
        assert scanner_engine._apply_momentum_veto(sigs, None) == sigs

    def test_veto_boundary_at_threshold(self):
        sigs = [{"type": "CCS"}, {"type": "PCS"}]
        assert [s["type"] for s in scanner_engine._apply_momentum_veto(sigs, 0.6)] == ["PCS"]


def test_run_full_scan_applies_momentum_veto():
    import inspect
    import scanner_engine
    src = inspect.getsource(scanner_engine.run_full_scan)
    assert src.count("_apply_momentum_veto") >= 2
    assert "intraday_move_ratio" in src


class TestGexRegime:
    def _gex(self, cells):
        return {"gex": {float(100 + i): {"net": v} for i, v in enumerate(cells)}}

    def test_band_positive_when_net_nonnegative(self):
        assert scanner_engine.gex_regime_band(self._gex([100, 50, -30])) == "POS"

    def test_band_neg_mild(self):
        # net=-40, gross=240 -> ratio ~-0.167 (between strong-neg -0.30 and 0)
        assert scanner_engine.gex_regime_band(self._gex([100, -90, -50])) == "NEG"

    def test_band_strong_neg(self):
        # net=-180, gross=220 -> ratio -0.818 <= -0.30
        assert scanner_engine.gex_regime_band(self._gex([20, -100, -100])) == "STRONG_NEG"

    def test_band_none_on_empty_or_zero_gross(self):
        assert scanner_engine.gex_regime_band({"gex": {}}) is None
        assert scanner_engine.gex_regime_band(None) is None
        assert scanner_engine.gex_regime_band({"gex": {100.0: {"net": 0}}}) is None

    def test_ccs_must_be_above_call_wall(self):
        walls = {"call_wall": 600.0, "put_wall": 560.0}
        assert scanner_engine.passes_wall({"type": "CCS", "short_strike": 605.0}, walls) is True
        assert scanner_engine.passes_wall({"type": "CCS", "short_strike": 595.0}, walls) is False

    def test_pcs_must_be_below_put_wall(self):
        walls = {"call_wall": 600.0, "put_wall": 560.0}
        assert scanner_engine.passes_wall({"type": "PCS", "short_strike": 555.0}, walls) is True
        assert scanner_engine.passes_wall({"type": "PCS", "short_strike": 565.0}, walls) is False

    def test_ic_must_clear_both_walls(self):
        walls = {"call_wall": 600.0, "put_wall": 560.0}
        ok = {"type": "IC", "short_strike": 555.0, "call_short": 605.0}
        bad = {"type": "IC", "short_strike": 555.0, "call_short": 595.0}
        assert scanner_engine.passes_wall(ok, walls) is True
        assert scanner_engine.passes_wall(bad, walls) is False

    def test_none_wall_side_passes(self):
        assert scanner_engine.passes_wall({"type": "CCS", "short_strike": 605.0},
                                          {"call_wall": None, "put_wall": 560.0}) is True

    def _ctx(self, band, cw=600.0, pw=560.0):
        return {"band": band, "walls": {"call_wall": cw, "put_wall": pw}, "ratio": 0.0}

    def test_strong_neg_drops_all_index(self):
        sigs = [{"symbol": "SPY", "type": "CCS", "short_strike": 999, "composite_score": 99}]
        assert scanner_engine.apply_gex_gate(sigs, {"SPY": self._ctx("STRONG_NEG")}) == []

    def test_neg_requires_wall_and_score(self):
        ctx = {"SPY": self._ctx("NEG")}
        beyond_hi = {"symbol": "SPY", "type": "CCS", "short_strike": 610, "composite_score": 70}
        beyond_lo = {"symbol": "SPY", "type": "CCS", "short_strike": 610, "composite_score": 60}
        inside = {"symbol": "SPY", "type": "CCS", "short_strike": 590, "composite_score": 70}
        assert scanner_engine.apply_gex_gate([beyond_hi], ctx) == [beyond_hi]
        assert scanner_engine.apply_gex_gate([beyond_lo], ctx) == []
        # wall fail alone rejects even with a passing score (inside has score 70)
        assert scanner_engine.apply_gex_gate([inside], ctx) == []

    def test_pos_passes_through(self):
        ctx = {"SPY": self._ctx("POS")}
        s = {"symbol": "SPY", "type": "CCS", "short_strike": 590, "composite_score": 60}
        assert scanner_engine.apply_gex_gate([s], ctx) == [s]

    def test_non_index_and_directional_and_no_context_pass(self):
        ctx = {"SPY": self._ctx("STRONG_NEG")}
        amd = {"symbol": "AMD", "type": "CCS", "short_strike": 1, "composite_score": 50}
        directional = {"symbol": "SPY", "type": "CCS", "short_strike": 1,
                       "composite_score": 50, "mode": "DIRECTIONAL"}
        noctx = {"symbol": "QQQ", "type": "CCS", "short_strike": 1, "composite_score": 50}
        out = scanner_engine.apply_gex_gate([amd, directional, noctx], ctx)
        assert out == [amd, directional, noctx]

    def test_missing_short_strike_fails_wall(self):
        walls = {"call_wall": 600.0, "put_wall": 560.0}
        # a malformed signal with no short_strike must be rejected on every type
        assert scanner_engine.passes_wall({"type": "CCS"}, walls) is False
        assert scanner_engine.passes_wall({"type": "PCS"}, walls) is False
        assert scanner_engine.passes_wall({"type": "IC", "call_short": 605.0}, walls) is False


def test_run_full_scan_applies_gex_gate():
    import inspect
    import scanner_engine
    src = inspect.getsource(scanner_engine.run_full_scan)
    assert "apply_gex_gate" in src
    assert "gex_regime_band" in src
    assert "get_directional_walls" in src


class _StubResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _chain_at(spot, exp, dte):
    """Spot-relative chain: a $1 strike ladder centred on `spot`.

    Same delta/mark conventions as test_directional_screening's
    `_make_chain_symmetric` (OTM = small |delta| + low mark; |delta| ~ 0.5 ATM),
    but that builder hard-codes strikes 470-530, so it only serves spot=500 and
    cannot supply a second symbol at a different price level.

    NOT a realistic surface, deliberately: the 1-DTE and 7-DTE chains carry
    IDENTICAL marks and a flat `volatility: 18.0` (no term structure, no skew).
    That is why 13 of the 16 candidates it yields sit exactly on
    strategy_scoring's GATE_FAIL_CAP (39.0) -- they fail a hard gate, which pins
    the score and makes q_be unobservable on those rows. Don't read score
    realism into fixtures built from this, and when pinning a scoring behaviour
    pick a candidate that is NOT gate-capped or the assertion proves nothing.
    """
    puts, calls = {}, {}
    for i in range(-30, 31):
        k = float(round(spot + i))
        put_delta = max(-0.99, min(-0.01, -0.5 - (k - spot) / 32.0))
        call_delta = max(0.01, min(0.99, 0.5 - (k - spot) / 32.0))
        put_mark = max(0.10, abs(put_delta) * 15.0 + 0.20)
        call_mark = max(0.10, call_delta * 15.0 + 0.20)
        common = {"totalVolume": 500, "openInterest": 1000, "volatility": 18.0,
                  "theta": -0.05, "vega": 0.10, "gamma": 0.01}
        puts[str(k)] = [{"delta": put_delta, "mark": put_mark,
                         "bid": round(put_mark - 0.02, 4),
                         "ask": round(put_mark + 0.02, 4), **common}]
        calls[str(k)] = [{"delta": call_delta, "mark": call_mark,
                          "bid": round(call_mark - 0.02, 4),
                          "ask": round(call_mark + 0.02, 4), **common}]
    return {"underlyingPrice": spot,
            "putExpDateMap": {f"{exp}:{dte}": puts},
            "callExpDateMap": {f"{exp}:{dte}": calls}}


# {symbol: (spot, price-trend direction)}. Both fields are load-bearing, but for
# DIFFERENT tests -- each verified by mutation, so don't collapse either:
#
#   * The two SPOT LEVELS produce the cross-symbol score spread that makes the
#     post-loop sort load-bearing. `score_all` returns each symbol's slice
#     already sorted, so one symbol alone would arrive pre-sorted and no sort
#     could bind. (Levelling the TRENDS does not break this; the spots do it.)
#   * The OPPOSITE TRENDS give each symbol a different inferred market view.
#     That is what keeps QQQ's 7-DTE LONG_CALL off GATE_FAIL_CAP -- the only
#     reason q_be, hence the per-window em_1sd, is observable at all. Setting
#     both to +1 gate-caps the probe at 39.0 and
#     test_swing_window_scored_against_its_own_em fails its vacuity guard.
_FAKE_SYMBOLS = {"SPY": (500.0, +1), "QQQ": (430.0, -1)}


@pytest.fixture
def fake_client(monkeypatch):
    """Schwab client stub for `run_full_scan` (modelled on
    test_directional_screening's `_stub_client`).

    Populates BOTH DTE windows -- 0-DTE (1 DTE) and swing (7 DTE) -- for both
    symbols, so the directional builder's two-window loop, its concatenation,
    and both sorts are all exercised.

    Expirations are anchored to LIVE dates: `strategy_scanner._dte_for` parses
    the expiration STRING, so a hard-coded past date would stamp dte=0 on every
    directional candidate.
    """
    today = date.today()
    exp_0 = (today + timedelta(days=1)).isoformat()
    exp_s = (today + timedelta(days=7)).isoformat()
    chains = {sym: {"0dte": _chain_at(spot, exp_0, 1),
                    "swing": _chain_at(spot, exp_s, 7)}
              for sym, (spot, _trend) in _FAKE_SYMBOLS.items()}
    failed_chain = {"putExpDateMap": {}, "callExpDateMap": {}, "status": "FAILED"}

    # Pin the regime so the scan never reads the live sentiment bridge file.
    import regime_filter
    monkeypatch.setattr(regime_filter, "evaluate_regime", lambda **kw: {
        "active": False, "allow_ccs": True, "allow_pcs": True, "per_symbol": {},
        "bias": "neutral", "trend_state": None, "trend_confidence": None,
        "aggregate_confidence": None, "divergence_flag": None,
        "composite_score": 5.0, "reason": "test",
    })
    # Let the CREDIT-spread path produce signals too, so the "directional never
    # leaks into the credit lists" test has something to actually iterate.
    # Both mirror test_directional_screening: pin market-open for the liquidity
    # gate, and drop the IV Rank floor (the stub iv_data has iv_rank=None, which
    # would otherwise filter every credit spread away).
    monkeypatch.setattr(scanner_engine, "_is_options_market_open", lambda: True)
    monkeypatch.setattr(scanner_engine, "MIN_IV_RANK", {"0-DTE": 0, "SWING": 0})

    class _Stub:
        class Options:
            class ContractType:
                ALL = "ALL"

        def get_quotes(self, symbols):
            return _StubResponse({
                s: {"quote": {"lastPrice": 18.0 if s.startswith("$VIX")
                              else _FAKE_SYMBOLS[s][0]}}
                for s in symbols
            })

        def get_option_chain(self, symbol, **kwargs):
            # Buckets are identified by the from_date run_full_scan asks with:
            # 0-DTE = today, swing = today+5, IV analysis = today+20.
            from_date = kwargs.get("from_date")
            sym_chains = chains.get(symbol)
            if from_date is None or sym_chains is None:
                return _StubResponse(failed_chain)
            if from_date <= today + timedelta(days=4):
                return _StubResponse(sym_chains["0dte"])
            if from_date <= today + timedelta(days=15):
                return _StubResponse(sym_chains["swing"])
            return _StubResponse(failed_chain)   # IV-analysis window

        def get_price_history_every_day(self, symbol):
            spot, trend = _FAKE_SYMBOLS[symbol]
            base = spot - trend * 30
            return _StubResponse({"candles": [
                {"open": base + trend * i * 0.5 - 0.5,
                 "high": base + trend * i * 0.5 + 1.0,
                 "low": base + trend * i * 0.5 - 1.0,
                 "close": base + trend * i * 0.5, "datetime": i}
                for i in range(60)
            ]})

    return _Stub()


class TestPerExpiryExpectedMove:
    """The EM window must size against the EXPIRATION's own IV, not a 30-day IV
    scaled by sqrt(dte). Only the vol INPUT changes — every downstream formula
    (sqrt(dte) scaling, the 0-DTE hours decay, the multiplier curves) is
    untouched, which is why these tests target the substituted daily EM.
    """

    def _chain(self, front_iv, back_iv, underlying=100.0):
        """Put-only ladder 85-99 whose marks DECAY with distance from spot, so
        every 1-wide spread carries a real 0.20 credit (cr/w 0.20, clearing both
        min_cr_pct and the EDGE_MARGIN |delta|+0.02 gate at delta 0.10). An
        equal-mark ladder would give every spread zero credit and the test would
        pass vacuously on an empty result.
        """
        def leg(iv, k):
            mark = 0.20 + (k - 85) * 0.20
            return [{"volatility": iv, "delta": -0.10, "mark": mark,
                     "bid": round(mark - 0.02, 2), "ask": round(mark + 0.02, 2),
                     "theta": -0.02, "vega": 0.05, "gamma": 0.01,
                     "totalVolume": 500, "openInterest": 500}]
        return {
            "underlyingPrice": underlying,
            "callExpDateMap": {},
            "putExpDateMap": {
                "2026-08-10:3":  {f"{k}.0": leg(front_iv, k) for k in range(85, 100)},
                "2026-09-05:30": {f"{k}.0": leg(back_iv, k) for k in range(85, 100)},
            },
        }

    def test_effective_daily_em_uses_the_expirys_own_iv(self):
        chain = self._chain(front_iv=30.0, back_iv=15.0)
        eff = scanner_engine.effective_daily_em(chain, "2026-08-10", 100.0, fallback=1.0)
        import iv_analysis
        assert eff == iv_analysis.expiry_daily_em(100.0, 30.0)
        # Non-vacuity: it must differ from the fallback AND from the 30d reading.
        assert eff != 1.0
        assert eff != iv_analysis.expiry_daily_em(100.0, 15.0)

    def test_effective_daily_em_falls_back_when_the_expiry_has_no_iv(self):
        """No usable per-expiry IV must degrade to the caller's daily EM, never
        to None/0 — a 0 disables the EM filter entirely (accept-all)."""
        chain = self._chain(front_iv=30.0, back_iv=15.0)
        assert scanner_engine.effective_daily_em(
            chain, "2099-01-01", 100.0, fallback=1.23) == 1.23
        assert scanner_engine.effective_daily_em(None, "x", 100.0, fallback=1.23) == 1.23

    def test_kill_switch_restores_the_legacy_thirty_day_em(self, monkeypatch):
        chain = self._chain(front_iv=30.0, back_iv=15.0)
        monkeypatch.setattr(scanner_engine, "USE_EXPIRY_EM", False)
        assert scanner_engine.effective_daily_em(
            chain, "2026-08-10", 100.0, fallback=1.23) == 1.23

    def _short_strikes(self, chain, legacy_daily, monkeypatch=None, use_expiry=True):
        import scanner_engine as se
        orig = se.USE_EXPIRY_EM
        try:
            se.USE_EXPIRY_EM = use_expiry
            got = se.screen_spreads(
                chain, "TEST", 1, 5, -0.40, -0.01, 0.01, 0.40, 0.05, "SWING",
                spot=100.0, daily_expected_move=legacy_daily,
                now_ct=datetime(2026, 8, 7, 10, 0))
        finally:
            se.USE_EXPIRY_EM = orig
        return sorted({s["short_strike"] for s in got})

    def test_screen_spreads_stamps_the_expiry_iv_for_scoring(self):
        """Each signal carries its expiration's ATM IV so scoring can size the
        EM-buffer factor off the same vol the strike window used."""
        chain = self._chain(front_iv=60.0, back_iv=15.0)
        import iv_analysis
        got = scanner_engine.screen_spreads(
            chain, "TEST", 1, 5, -0.40, -0.01, 0.01, 0.40, 0.05, "SWING",
            spot=100.0, daily_expected_move=iv_analysis.expiry_daily_em(100.0, 15.0),
            now_ct=datetime(2026, 8, 7, 10, 0))
        assert got, "fixture produced no spreads"
        # 60.0 is the FRONT expiry's vol, not the 15.0 thirty-day reading.
        assert all(s.get("expiry_iv") == 60.0 for s in got), \
            [s.get("expiry_iv") for s in got]

    def test_kill_switch_also_suppresses_the_expiry_iv_stamp(self):
        """USE_EXPIRY_EM=False must revert BOTH halves: with no stamp, scoring
        falls back to the symbol-level IV, so the switch is a full revert."""
        chain = self._chain(front_iv=60.0, back_iv=15.0)
        import iv_analysis
        import scanner_engine as se
        orig = se.USE_EXPIRY_EM
        try:
            se.USE_EXPIRY_EM = False
            got = se.screen_spreads(
                chain, "TEST", 1, 5, -0.40, -0.01, 0.01, 0.40, 0.05, "SWING",
                spot=100.0, daily_expected_move=iv_analysis.expiry_daily_em(100.0, 15.0),
                now_ct=datetime(2026, 8, 7, 10, 0))
        finally:
            se.USE_EXPIRY_EM = orig
        assert got, "fixture produced no spreads"
        assert all(s.get("expiry_iv") is None for s in got)

    def test_screen_spreads_sizes_the_window_off_the_expiry_iv(self):
        """A front expiry priced RICHER than the 30-day widens ITS OWN EM, so
        the window moves further OTM than the IV30-scaled one.

        The two windows are disjoint by construction (60-vol front -> puts
        88.0-93.2; 15-vol 30-day reading -> puts 97.0-98.3), so this asserts on
        the STRIKE SETS. A count comparison would not distinguish them.
        """
        rich = self._chain(front_iv=60.0, back_iv=15.0)
        import iv_analysis
        legacy_daily = iv_analysis.expiry_daily_em(100.0, 15.0)

        per_expiry = self._short_strikes(rich, legacy_daily, use_expiry=True)
        legacy = self._short_strikes(rich, legacy_daily, use_expiry=False)

        # Non-vacuity: BOTH paths must actually produce spreads.
        assert per_expiry, "per-expiry path produced no spreads — fixture is broken"
        assert legacy, "legacy path produced no spreads — fixture is broken"
        # The richer front EM pushes the short strike strictly further OTM.
        assert max(per_expiry) < min(legacy), (
            f"per-expiry shorts {per_expiry} are not further OTM than legacy {legacy}")
        assert all(88.0 <= k <= 93.5 for k in per_expiry), per_expiry
        assert all(96.5 <= k <= 98.5 for k in legacy), legacy


@pytest.fixture
def unfiltered_directional(monkeypatch):
    """Drop the min-score cut for tests that are about the build/score pipeline.

    Every directional candidate this fixture chain produces grades Weak (the
    fake chain's liquidity/PoP fail the hard gates), so with the production
    ``SINGLE_LEG_MIN_SCORE`` in force the emitted list is empty and every
    assertion about shape, window coverage or ordering goes vacuous. These
    tests predate the cut and are not about it — the cut has its own tests.
    """
    monkeypatch.setattr(scanner_engine, "SINGLE_LEG_MIN_SCORE", 0.0)
    monkeypatch.setattr(scanner_engine, "SINGLE_LEG_EXCLUDED_GRADES", ())


class TestDirectionalSignals:
    """Single-leg directional candidates (LONG/SHORT calls & puts).

    NOTE: unrelated to `mode="DIRECTIONAL"` (a directionally-biased CREDIT
    spread — see test_directional_screening.py). Same word, different concept.
    """

    SYMBOLS = ["SPY", "QQQ"]

    def test_run_full_scan_emits_signals_directional(self, fake_client):
        results = scanner_engine.run_full_scan(fake_client, symbols=self.SYMBOLS)
        assert "signals_directional" in results
        assert isinstance(results["signals_directional"], list)

    def test_directional_signals_carry_strategy_shape(self, fake_client,
                                                      unfiltered_directional):
        results = scanner_engine.run_full_scan(fake_client, symbols=self.SYMBOLS)
        sigs = results["signals_directional"]
        assert sigs, "expected directional candidates from the fake chain"
        for s in sigs:
            assert s["type"] in {"LONG_CALL", "LONG_PUT", "SHORT_CALL", "SHORT_PUT"}
            assert s["family"] == "DIRECTIONAL"
            assert isinstance(s["legs"], list) and len(s["legs"]) == 1
            assert s.get("id")
            assert s.get("composite_score") is not None

    def test_directional_covers_both_dte_windows(self, fake_client,
                                                 unfiltered_directional):
        """Both the 0-DTE and swing windows must contribute candidates.

        Guards the two-window loop AND the non-vacuity of the sort test below:
        a single populated window would leave the accumulated list pre-sorted by
        `score_all`, so neither sort could ever bind.
        """
        sigs = scanner_engine.run_full_scan(
            fake_client, symbols=self.SYMBOLS)["signals_directional"]
        for symbol in self.SYMBOLS:
            dtes = {s["dte"] for s in sigs if s["symbol"] == symbol}
            assert 1 in dtes, f"{symbol}: no 0-DTE-window candidate (got {dtes})"
            assert 7 in dtes, f"{symbol}: no swing-window candidate (got {dtes})"

    def test_directional_sorted_by_score_desc(self, fake_client,
                                              unfiltered_directional):
        sigs = scanner_engine.run_full_scan(
            fake_client, symbols=self.SYMBOLS)["signals_directional"]
        scores = [s.get("composite_score") or 0 for s in sigs]
        # Non-vacuity: `score_all` returns each symbol's slice already sorted, so
        # only a list that is NOT sorted before the post-loop sort proves it runs.
        assert len(set(scores)) > 1, "all scores equal — the ordering assertion is vacuous"
        assert scores == sorted(scores, reverse=True)

    def test_swing_window_scored_against_its_own_em_not_a_one_day_em(
            self, fake_client, unfiltered_directional):
        """Each DTE window is scored against ITS OWN em_1sd.

        Pins the deliberate deviation from the task spec (see the comment in
        scanner_engine.py). strategy_scoring's q_be is
        `clamp((1 - dist/em_1sd) * 100)`, so scoring a 7-DTE candidate against a
        1-day em_1sd shrinks the denominator ~sqrt(7)=2.65x, inflates the ratio
        and drives q_be toward 0 -- under-scoring the swing side of this single
        jointly-sorted list.

        QQQ's 7-DTE LONG_CALL is the probe because it is the ONLY swing
        candidate this fixture produces that is NOT pinned to GATE_FAIL_CAP
        (39.0) by a failed hard gate -- i.e. the only one where q_be is
        observable at all. Measured: 36.2 scored per-window vs 31.6 under the
        spec's single 1-day em_1sd; the 34.0 bar sits between them, so a revert
        to the spec fails here.
        """
        sigs = scanner_engine.run_full_scan(
            fake_client, symbols=self.SYMBOLS)["signals_directional"]
        probe = [s for s in sigs if s["symbol"] == "QQQ"
                 and s["type"] == "LONG_CALL" and s["dte"] == 7]
        assert len(probe) == 1, "probe candidate missing — fixture changed"
        score = probe[0]["composite_score"]
        # Non-vacuity: a gate-capped score is constant and would prove nothing.
        assert score != 39.0, "probe is gate-capped — q_be unobservable, test is vacuous"
        assert score > 34.0, (
            f"swing candidate scored {score}: looks scored against a 1-day em_1sd "
            "rather than its own 7-DTE window")

    def test_directional_never_leaks_into_the_credit_lists(self, fake_client):
        """The driver reads signals_0dte + signals_swing. Directional must not be there."""
        results = scanner_engine.run_full_scan(fake_client, symbols=self.SYMBOLS)
        credit = results["signals_0dte"] + results["signals_swing"]
        # Non-vacuity: an empty credit list would pass the loop below for free.
        assert credit, "fixture produced no credit spreads — the assertion would be vacuous"
        for s in credit:
            assert s["type"] in {"PCS", "CCS", "IC"}

    # --- Minimum-score cut ---------------------------------------------------

    def test_weak_directional_candidates_are_not_emitted(self, fake_client):
        """Every candidate this fixture builds grades Weak (~23-39), so the
        production cut must emit NOTHING — no Weak signal reaches the tab."""
        results = scanner_engine.run_full_scan(fake_client, symbols=self.SYMBOLS)
        assert results["signals_directional"] == []
        # Non-vacuity: the builder must actually have produced candidates for
        # the cut to have anything to drop, else this passes for free.
        assert results["signals_0dte"], "fixture produced no scan at all"

    def test_directional_emits_candidates_at_or_above_the_min_score(
            self, fake_client, monkeypatch):
        """The cut is a floor, not a blanket suppression: >= the threshold survives."""
        import strategy_scoring

        def _fake_score_all(signals, view, atm_iv, em_1sd, market_state=None):
            out = []
            for i, sig in enumerate(signals or []):
                sig["composite_score"] = 49.9 if i % 2 else 50.0
                sig["grade"] = "Marginal"
                out.append(sig)
            return out

        monkeypatch.setattr(strategy_scoring, "score_all", _fake_score_all)
        sigs = scanner_engine.run_full_scan(
            fake_client, symbols=self.SYMBOLS)["signals_directional"]
        assert sigs, "nothing survived — the cut is suppressing qualifying candidates"
        # Boundary: 50.0 is IN, 49.9 is OUT.
        assert {s["composite_score"] for s in sigs} == {50.0}

    def test_directional_min_score_cut_runs_before_the_per_symbol_cap(
            self, fake_client, monkeypatch):
        """The cap must select the best QUALIFYING candidates.

        Score alone cannot show this — the list is sorted descending, so a
        score cut applied after the slice keeps the same rows. The GRADE half
        can: two top-scoring Weak rows would eat both cap slots and then be
        dropped, emitting nothing, if the cut ran after the slice.
        """
        import strategy_scoring
        monkeypatch.setattr(scanner_engine, "SINGLE_LEG_MAX_PER_SYMBOL", 2)

        def _fake_score_all(signals, view, atm_iv, em_1sd, market_state=None):
            out = []
            for i, sig in enumerate(signals or []):
                sig["composite_score"] = 90.0 if i < 2 else 60.0
                sig["grade"] = "Weak" if i < 2 else "Good"
                out.append(sig)
            return out

        monkeypatch.setattr(strategy_scoring, "score_all", _fake_score_all)
        sigs = scanner_engine.run_full_scan(
            fake_client, symbols=self.SYMBOLS)["signals_directional"]
        assert sigs, "the cap was spent on candidates the cut then dropped"
        assert all(s["grade"] == "Good" and s["composite_score"] == 60.0
                   for s in sigs)

    def test_directional_drops_a_weak_grade_regardless_of_score(
            self, fake_client, monkeypatch):
        """Grade and score are checked independently.

        A gate-failed candidate is capped at GATE_FAIL_CAP (39) + at most a +6
        state tilt, so today no Weak signal can reach 50 and the two checks are
        REDUNDANT — no realistic fixture can tell them apart. This synthetic
        probe (grade Weak at score 90) pins the grade half so a future threshold
        change cannot silently let a Weak trade through.
        """
        import strategy_scoring

        def _fake_score_all(signals, view, atm_iv, em_1sd, market_state=None):
            out = []
            for sig in signals or []:
                sig["composite_score"] = 90.0
                sig["grade"] = "Weak"
                out.append(sig)
            return out

        monkeypatch.setattr(strategy_scoring, "score_all", _fake_score_all)
        results = scanner_engine.run_full_scan(fake_client, symbols=self.SYMBOLS)
        assert results["signals_directional"] == []

    def test_directional_degrades_when_builder_raises(self, fake_client, monkeypatch):
        """A directional failure must never break the credit-spread scan."""
        import strategy_scanner
        monkeypatch.setattr(strategy_scanner, "build_directional",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        results = scanner_engine.run_full_scan(fake_client, symbols=self.SYMBOLS)
        assert results["signals_directional"] == []
        # The credit scan must SURVIVE, not merely still be a list: an
        # `isinstance(..., list)` check is a tautology that [] satisfies, so it
        # would pass even if the handler wiped both buckets on the way out.
        assert results["signals_0dte"], "credit scan did not survive the directional failure"
        assert results["signals_swing"], "credit scan did not survive the directional failure"


class TestIvSurfacedOntoSignals:
    """The per-symbol IV block must be surfaced onto the signal dicts.

    `run_iv_analysis` writes current_iv / iv_low_52w / iv_high_52w /
    expected_moves into results["iv_data"][symbol]; nothing ever copied them
    onto the signals, so the webgui Trade detail panel's Expected Move card and
    its 52-week IV range marker (both keyed off the SIGNAL dict) had never
    rendered. These pin the copy.

    The fallback is deliberately None, NOT the `or 0` that `iv_rank` uses: a
    fabricated 0 IV reads as "measured, and it's zero", which is exactly the
    distinction the detail panel exists to make.
    """

    SYMBOLS = ["SPY", "QQQ"]
    SIGNAL_KEYS = ("signals_0dte", "signals_swing", "signals_directional")
    NEW_FIELDS = ("current_iv", "iv_low_52w", "iv_high_52w", "expected_moves")

    _SPY_IV = {
        "symbol": "SPY", "current_iv": 18.4,
        "iv_rank": 42.0, "iv_percentile": 55.0,
        "iv_high_52w": 31.7, "iv_low_52w": 9.2,
        "hv_current": 15.0,
        "expected_moves": {
            "daily": {"dte": 1, "move_dollars": 4.82, "move_pct": 0.96,
                      "upper_1sd": 504.82, "lower_1sd": 495.18,
                      "upper_2sd": 509.64, "lower_2sd": 490.36},
            "weekly": {"dte": 7, "move_dollars": 12.75, "move_pct": 2.55,
                       "upper_1sd": 512.75, "lower_1sd": 487.25,
                       "upper_2sd": 525.50, "lower_2sd": 474.50},
            "monthly": None,
        },
    }

    @pytest.fixture
    def iv_stub(self, monkeypatch):
        """SPY gets a fully-populated IV block; QQQ gets the all-None shape
        `_empty_iv_data` produces when the IV window comes back empty.

        Both halves are load-bearing — SPY proves the copy happens, QQQ proves
        the missing case stays None instead of collapsing to 0.
        """
        import iv_analysis

        def _fake(client, symbol, **kwargs):
            if symbol == "SPY":
                return dict(self._SPY_IV)
            return {
                "symbol": symbol, "current_iv": None,
                "iv_rank": None, "iv_percentile": None,
                "iv_high_52w": None, "iv_low_52w": None,
                "expected_moves": {"daily": None, "weekly": None,
                                   "monthly": None},
            }

        monkeypatch.setattr(iv_analysis, "run_iv_analysis", _fake)

    def _signals(self, results, symbol):
        return [s for key in self.SIGNAL_KEYS for s in results[key]
                if s.get("symbol") == symbol]

    def test_populated_iv_data_is_copied_onto_every_signal(
            self, fake_client, iv_stub, unfiltered_directional):
        results = scanner_engine.run_full_scan(fake_client, symbols=self.SYMBOLS)
        spy = self._signals(results, "SPY")
        assert spy, "fixture produced no SPY signals — assertions would be vacuous"
        for s in spy:
            assert s["current_iv"] == 18.4
            assert s["iv_low_52w"] == 9.2
            assert s["iv_high_52w"] == 31.7
            assert s["expected_moves"] == self._SPY_IV["expected_moves"]

    def test_expected_moves_keeps_its_nested_shape(
            self, fake_client, iv_stub, unfiltered_directional):
        """The detail panel reads em["daily"]["move_dollars"], so the nested
        dict must arrive intact rather than flattened or stringified."""
        results = scanner_engine.run_full_scan(fake_client, symbols=self.SYMBOLS)
        spy = self._signals(results, "SPY")
        assert spy
        em = spy[0]["expected_moves"]
        assert isinstance(em, dict)
        assert em["daily"]["move_dollars"] == 4.82
        assert em["monthly"] is None, "a None horizon must survive the copy"

    def test_missing_iv_data_leaves_the_fields_none_not_zero(
            self, fake_client, iv_stub, unfiltered_directional):
        """QQQ has no IV reading. A 0 here would be a fabricated measurement."""
        results = scanner_engine.run_full_scan(fake_client, symbols=self.SYMBOLS)
        qqq = self._signals(results, "QQQ")
        assert qqq, "fixture produced no QQQ signals — assertions would be vacuous"
        for s in qqq:
            for field in ("current_iv", "iv_low_52w", "iv_high_52w"):
                assert s[field] is None, (
                    f"{field} is {s[field]!r}; a missing IV must stay None, "
                    "never collapse to 0")

    def test_symbol_absent_from_iv_data_still_gets_none_fields(
            self, fake_client, iv_stub, unfiltered_directional, monkeypatch):
        """Not merely None VALUES — the symbol key missing entirely (an IV
        fetch that never returned) must not KeyError and must not leave the
        field absent, or the detail panel's `is not None` probe reads a stale
        key from whatever dict preceded it."""
        results = scanner_engine.run_full_scan(fake_client, symbols=self.SYMBOLS)
        results["iv_data"].clear()
        # Re-run the surfacing over an emptied iv_data by re-invoking the scan
        # with iv_data suppressed at the source.
        import iv_analysis
        monkeypatch.setattr(iv_analysis, "run_iv_analysis",
                            lambda *a, **k: {})
        results = scanner_engine.run_full_scan(fake_client, symbols=self.SYMBOLS)
        sigs = [s for key in self.SIGNAL_KEYS for s in results[key]]
        assert sigs, "fixture produced no signals — assertions would be vacuous"
        for s in sigs:
            for field in self.NEW_FIELDS:
                assert field in s, f"{field} missing from the signal dict"
            assert s["current_iv"] is None
            assert s["expected_moves"] is None

    def test_iv_rank_fallback_is_unchanged(self, fake_client, iv_stub,
                                           unfiltered_directional):
        """iv_rank keeps its `or 0` sentinel — other code depends on it."""
        results = scanner_engine.run_full_scan(fake_client, symbols=self.SYMBOLS)
        qqq = self._signals(results, "QQQ")
        assert qqq
        assert all(s["iv_rank"] == 0 for s in qqq)
        spy = self._signals(results, "SPY")
        assert spy
        assert all(s["iv_rank"] == 42.0 for s in spy)

    def test_surfaced_fields_are_json_serializable(
            self, fake_client, iv_stub, unfiltered_directional):
        """Signals are JSON-serialized into the Redis cache envelope, so a
        numpy scalar leaking out of the IV math would break publish, not this
        loop — assert the real constraint here."""
        import json
        results = scanner_engine.run_full_scan(fake_client, symbols=self.SYMBOLS)
        sigs = [s for key in self.SIGNAL_KEYS for s in results[key]]
        assert sigs
        for s in sigs:
            json.dumps({k: s[k] for k in self.NEW_FIELDS})
