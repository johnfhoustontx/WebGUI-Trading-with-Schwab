"""The scorer's regime selector (Phase 4, task 4.3 — live half).

The artifact has always carried a `regimes` map with only an `"all"` key; the
fit can now populate `trend` / `chop` / `highvol`. The scorer therefore has to
choose one — and, more importantly, has to SAY which one it chose, because a
verdict scored under regime weights and a verdict scored under pooled weights
are different claims about the same symbol.

The fallback is the load-bearing part. An artifact predating this work, a
regime the fit had too little data to estimate, and a live SPY history too
short to classify must all land on `"all"` rather than on nothing — the model
degrades to what it always did, never to silence.

Run from the repo root:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_swing_regime.py -v
"""
import copy

import pytest

from services.trade_svc import swing_model as sm

_ALL = {
    "weights": {"mom_12_1": 0.5, "low_vol": -0.5},
    "factor_ic": {"mom_12_1": {"mean_ic": 0.04}, "low_vol": {"mean_ic": -0.066}},
    "norm": {"mom_12_1": {"mean": 0.0, "std": 0.1},
             "low_vol": {"mean": -0.02, "std": 0.01}},
    "calibration": [
        {"band": 0, "score_lo": -3, "score_hi": -0.5, "mean_fwd": -0.008,
         "hit_rate": 0.43, "n": 100},
        {"band": 1, "score_lo": -0.5, "score_hi": 0.5, "mean_fwd": 0.0,
         "hit_rate": 0.49, "n": 100},
        {"band": 2, "score_lo": 0.5, "score_hi": 3, "mean_fwd": 0.0135,
         "hit_rate": 0.523, "n": 100}],
    "oos_ic": 0.0206,
}


def _artifact(**regimes):
    art = {"version": "2026-08-22", "horizon": 20, "regimes": {"all": copy.deepcopy(_ALL)}}
    for k, v in regimes.items():
        art["regimes"][k] = v
    return art


def _highvol_block():
    """A regime whose weights are the pooled ones INVERTED — so which key
    scored is visible in the verdict itself, not merely in a label."""
    blk = copy.deepcopy(_ALL)
    blk["weights"] = {k: -v for k, v in _ALL["weights"].items()}
    return blk


CUR = {"mom_12_1": 0.5, "low_vol": -0.05}


class TestItSaysWhichKeyScored:
    def test_the_pooled_fit_reports_all(self):
        out = sm.score_symbol(CUR, None, _artifact())
        assert out["regime_key"] == "all"

    def test_a_named_regime_is_reported_when_it_exists(self):
        out = sm.score_symbol(CUR, None, _artifact(highvol=_highvol_block()),
                              regime="highvol")
        assert out["regime_key"] == "highvol"

    def test_the_selected_regime_actually_changes_the_verdict(self):
        """Reporting the key is worthless if the weights did not follow it."""
        art = _artifact(highvol=_highvol_block())
        pooled = sm.score_symbol(CUR, None, art)
        hv = sm.score_symbol(CUR, None, art, regime="highvol")
        assert pooled["score"] != hv["score"]
        assert pooled["verdict"] == "BUY" and hv["verdict"] == "SELL"


class TestFallback:
    def test_a_regime_key_the_artifact_lacks_falls_back_to_all(self):
        out = sm.score_symbol(CUR, None, _artifact(), regime="highvol")
        assert out["regime_key"] == "all"
        assert out["verdict"] == "BUY"

    def test_no_regime_at_all_falls_back_to_all(self):
        """`current_regime` returns None on a short SPY history — a real case
        the caller must not have to special-case."""
        out = sm.score_symbol(CUR, None, _artifact(), regime=None)
        assert out["regime_key"] == "all"

    @pytest.mark.parametrize("empty", [{}, {"weights": {}}, {"calibration": []}])
    def test_a_regime_present_but_UNFITTED_falls_back_rather_than_scoring_nothing(self, empty):
        """The fit writes a key for every regime it saw, but a regime it could
        not estimate carries no weights. Scoring on that would return None and
        drop the whole card to the legacy verdict — a silent downgrade."""
        out = sm.score_symbol(CUR, None, _artifact(highvol=empty), regime="highvol")
        assert out is not None
        assert out["regime_key"] == "all"

    def test_an_artifact_with_no_regimes_map_at_all_still_degrades_cleanly(self):
        assert sm.score_symbol(CUR, None, {"version": "x"}, regime="trend") is None


# ── Directional exposure (Phase 4) ───────────────────────────────────────────
# Phase 4 measured this composite at cross-sectional IC +0.16 when the market's
# forward 20 days were up and -0.11 when they were down, with the whole
# asymmetry carried by the volatility factors. The live artifact puts 47.6% of
# its absolute weight there. That is a property of the verdict the card has to
# be able to state, so the scorer computes it rather than the page guessing.

_RISKY = {
    "weights": {"low_vol": -0.6, "mom_12_1": 0.4},
    "factor_ic": {}, "norm": {"low_vol": {"mean": -0.02, "std": 0.01},
                              "mom_12_1": {"mean": 0.0, "std": 0.1}},
    "calibration": _ALL["calibration"], "oos_ic": 0.02,
}


class TestRiskShare:
    def test_it_is_the_share_of_ABSOLUTE_weight_on_volatility_factors(self):
        art = {"version": "x", "horizon": 20, "regimes": {"all": _RISKY}}
        out = sm.score_symbol(CUR, None, art)
        assert out["risk_share"] == pytest.approx(0.6)

    def test_a_model_with_no_volatility_weight_reports_zero_not_None(self):
        blk = copy.deepcopy(_RISKY)
        blk["weights"] = {"mom_12_1": 1.0}
        art = {"version": "x", "horizon": 20, "regimes": {"all": blk}}
        out = sm.score_symbol(CUR, None, art)
        assert out["risk_share"] == pytest.approx(0.0)

    def test_the_SIGN_of_the_weight_does_not_matter(self):
        """A model can tilt toward or away from volatility; either way that is
        where its directional exposure sits."""
        blk = copy.deepcopy(_RISKY)
        blk["weights"] = {"low_vol": +0.6, "mom_12_1": 0.4}
        art = {"version": "x", "horizon": 20, "regimes": {"all": blk}}
        assert sm.score_symbol(CUR, None, art)["risk_share"] == pytest.approx(0.6)

    def test_an_unrecognised_factor_name_does_not_count_as_risk(self):
        blk = copy.deepcopy(_RISKY)
        blk["weights"] = {"made_up": -0.6, "mom_12_1": 0.4}
        blk["norm"]["made_up"] = {"mean": 0.0, "std": 1.0}
        art = {"version": "x", "horizon": 20, "regimes": {"all": blk}}
        out = sm.score_symbol(CUR, None, art)
        assert out["risk_share"] == pytest.approx(0.0)


class TestTheBandIndexIsReported:
    def test_the_scorer_returns_the_band_it_landed_in(self):
        """`rec_journal` has a `band` column and `journal_reading` writes
        `sm.get("band")` — which the scorer never returned, so every journalled
        row carried NULL. Phase 6's monitor groups by band, so an always-NULL
        column stops being cosmetic."""
        out = sm.score_symbol(CUR, None, _artifact())
        assert out["band"] == 2               # top band of the 3-band fixture

    def test_a_bottom_band_read_reports_band_zero(self):
        low = {"mom_12_1": -0.5, "low_vol": 0.05}
        out = sm.score_symbol(low, None, _artifact())
        assert out["band"] == 0
        assert out["verdict"] == "SELL"

    def test_the_band_and_the_percentile_agree_about_direction(self):
        hi = sm.score_symbol(CUR, None, _artifact())
        lo = sm.score_symbol({"mom_12_1": -0.5, "low_vol": 0.05}, None, _artifact())
        assert (hi["band"] > lo["band"]) == (hi["percentile"] > lo["percentile"])
