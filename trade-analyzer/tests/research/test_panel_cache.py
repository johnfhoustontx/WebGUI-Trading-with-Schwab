"""Tests for the research panel cache (Phase 4, task 4.0).

The cache exists so that every variant in the Phase-4 study is measured against
the SAME panel. Fetching ~150 symbols x 5yr takes minutes and the data moves
intraday, so re-fetching per variant would confound a methodology change with a
fetch-date change — which is precisely the mistake Phase 0's refit warned about
(it moved OOS IC 44% with NO methodology change at all).

The key is therefore the load-bearing part: anything that would change the
panel's CONTENT must change the key, or a stale cache silently answers for a
different experiment.
"""
import numpy as np
import pandas as pd
import pytest

from research import panel_cache as pc


def _panel(dates=3, syms=("AAA", "BBB")):
    idx = pd.MultiIndex.from_product(
        [pd.date_range("2026-01-01", periods=dates), list(syms)],
        names=["date", "symbol"])
    rng = np.arange(len(idx), dtype="float64")
    return (pd.DataFrame({"mom": rng, "vol": rng * -0.5}, index=idx),
            pd.Series(rng * 0.01, index=idx))


class TestTheKeyTracksTheExperiment:
    def test_a_different_universe_is_a_different_key(self):
        a = pc.panel_key(["AAPL", "MSFT"], years=5, horizon=20, factors=["mom"])
        b = pc.panel_key(["AAPL", "MSFT", "NVDA"], years=5, horizon=20, factors=["mom"])
        assert a != b

    def test_symbol_ORDER_does_not_change_the_key(self):
        """The universe is a set. A dict that iterates differently must not
        force a re-fetch of an identical panel."""
        a = pc.panel_key(["AAPL", "MSFT"], years=5, horizon=20, factors=["mom"])
        b = pc.panel_key(["MSFT", "AAPL"], years=5, horizon=20, factors=["mom"])
        assert a == b

    def test_a_new_FACTOR_is_a_different_key(self):
        """The subtle one. Add a factor to the registry and a cached panel
        lacks its column — the variant would then be scored on a panel that
        cannot contain the thing under test, and nothing would say so."""
        a = pc.panel_key(["AAPL"], years=5, horizon=20, factors=["mom"])
        b = pc.panel_key(["AAPL"], years=5, horizon=20, factors=["mom", "semivol"])
        assert a != b

    def test_factor_ORDER_does_not_change_the_key(self):
        a = pc.panel_key(["AAPL"], years=5, horizon=20, factors=["mom", "vol"])
        b = pc.panel_key(["AAPL"], years=5, horizon=20, factors=["vol", "mom"])
        assert a == b

    @pytest.mark.parametrize("kw", [{"years": 3}, {"horizon": 40}])
    def test_window_and_label_changes_are_different_keys(self, kw):
        base = dict(symbols=["AAPL"], years=5, horizon=20, factors=["mom"])
        assert pc.panel_key(**base) != pc.panel_key(**{**base, **kw})


class TestRoundTrip:
    def test_a_panel_survives_save_and_load_intact(self, tmp_path):
        panel, forward = _panel()
        pc.save(tmp_path / "p.pkl", panel, forward, meta={"universe_n": 2})
        got_panel, got_fwd, meta = pc.load(tmp_path / "p.pkl")
        pd.testing.assert_frame_equal(panel, got_panel)
        pd.testing.assert_series_equal(forward, got_fwd)
        assert meta["universe_n"] == 2

    def test_the_multiindex_names_survive(self):
        """`walk_forward` addresses the index by the NAME 'date'. A round trip
        that dropped the names would fail deep inside a fold, not here."""
        panel, forward = _panel()
        assert list(panel.index.names) == ["date", "symbol"]

    def test_loading_a_missing_cache_returns_None_rather_than_raising(self, tmp_path):
        assert pc.load(tmp_path / "nope.pkl") is None
