"""Tests for the beta-adjusted label (Phase 4 — the root-cause fix).

The model's label is ``r_symbol - r_SPY``: a RAW excess return. A high-beta
stock earns positive raw excess whenever the market rises, purely mechanically —
no skill, no signal, just leverage. Fit over a window that was mostly a bull
market, any model built on that label will discover that high-volatility names
"outperform", because on this label they must.

Measured: the composite's IC is +0.16 when SPY's forward 20 days are up and
-0.11 when they are down, and every risk factor's sign flips with the market.
That is not an edge with a caveat; it is beta wearing an edge's clothes.

The textbook label for a market-neutral cross-sectional model is
``r_symbol - beta * r_market``, which prices out exactly the part leverage
explains. These tests pin the two halves of that argument: a pure-beta stock
must score ZERO on the adjusted label, and NOT zero on the raw one.
"""
import numpy as np
import pandas as pd
import pytest

from research import labels as L


def _mkt(n=600, seed=0, vol=0.011):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, vol, n)
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    return pd.Series(100.0 * np.exp(np.cumsum(r)), index=idx), pd.Series(r, index=idx)


class TestRollingBeta:
    def test_a_stock_that_IS_the_market_has_beta_one(self):
        mkt, _ = _mkt()
        b = L.rolling_beta(mkt, mkt).dropna()
        assert b.iloc[-1] == pytest.approx(1.0, abs=1e-6)

    def test_a_stock_moving_twice_the_market_has_beta_two(self):
        mkt, r = _mkt()
        lev = pd.Series(100.0 * np.exp(np.cumsum(2.0 * r)), index=mkt.index)
        b = L.rolling_beta(lev, mkt).dropna()
        assert b.iloc[-1] == pytest.approx(2.0, abs=0.05)

    def test_it_is_causal(self):
        """Same contract as factors.py: bar t may use only data up to t. A
        full-sample beta would leak the future into the LABEL, which is the one
        place a leak is guaranteed to inflate everything downstream."""
        mkt, r = _mkt(800)
        sym = pd.Series(100.0 * np.exp(np.cumsum(1.4 * r)), index=mkt.index)
        full = L.rolling_beta(sym, mkt)
        cut = L.rolling_beta(sym.iloc[:600], mkt.iloc[:600])
        pd.testing.assert_series_equal(full.iloc[:600].dropna(), cut.dropna(),
                                       check_names=False)

    def test_it_is_NaN_through_warmup_rather_than_a_default_of_one(self):
        """Warmup is MIN_PERIODS, not the full window — the estimate is trusted
        from half a year, and the window only bounds how far back it looks. NaN
        rather than 1.0, because an unmeasured beta silently becoming
        'market-like' would leave a leveraged name's exposure inside a label
        that claims to have removed it."""
        mkt, _ = _mkt()
        b = L.rolling_beta(mkt, mkt)
        assert b.iloc[:L.BETA_MIN_PERIODS - 1].isna().all()
        assert b.iloc[L.BETA_MIN_PERIODS:].notna().all()


class TestTheLabelItself:
    def test_a_pure_beta_stock_scores_ZERO_on_the_adjusted_label(self):
        """The whole argument. A stock that is nothing but 2x the market has no
        skill to measure, and the adjusted label must say so."""
        mkt, r = _mkt(900)
        lev = pd.Series(100.0 * np.exp(np.cumsum(2.0 * r)), index=mkt.index)
        adj = L.forward_excess(lev, mkt, horizon=20, beta_adjust=True).dropna()
        assert abs(adj.mean()) < 0.004
        assert adj.abs().mean() < 0.01

    def test_the_RAW_label_rewards_that_same_stock_when_the_market_rises(self):
        """The defect, stated as a test. On the raw label the same skill-free
        stock earns a large positive score in exactly the windows the market
        went up — which is what the fit then learns to chase."""
        mkt, r = _mkt(900)
        lev = pd.Series(100.0 * np.exp(np.cumsum(2.0 * r)), index=mkt.index)
        raw = L.forward_excess(lev, mkt, horizon=20, beta_adjust=False)
        mkt_fwd = (mkt.shift(-20) / mkt - 1.0)
        up = mkt_fwd > 0.02
        assert raw[up].mean() > 0.015
        adj = L.forward_excess(lev, mkt, horizon=20, beta_adjust=True)
        assert abs(adj[up].mean()) < raw[up].mean() / 3

    def test_the_raw_label_is_unchanged_from_the_shipping_definition(self):
        """`beta_adjust=False` must reproduce `fit_swing_model._forward_excess`
        exactly, so the two labels can be compared on one panel."""
        mkt, r = _mkt()
        sym = pd.Series(100.0 * np.exp(np.cumsum(r * 1.3)), index=mkt.index)
        got = L.forward_excess(sym, mkt, horizon=20, beta_adjust=False)
        want = (sym.shift(-20) / sym - 1.0) - (mkt.shift(-20) / mkt - 1.0)
        pd.testing.assert_series_equal(got.dropna(), want.dropna(),
                                       check_names=False)

    def test_a_genuinely_outperforming_stock_still_scores_positive(self):
        """The adjustment must remove beta, not signal. A stock with the
        market's beta plus real drift keeps its score."""
        mkt, r = _mkt(900)
        alpha = pd.Series(100.0 * np.exp(np.cumsum(r + 0.0008)), index=mkt.index)
        adj = L.forward_excess(alpha, mkt, horizon=20, beta_adjust=True).dropna()
        assert adj.mean() > 0.008
