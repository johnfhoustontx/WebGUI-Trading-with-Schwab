import pytest

from scoring.intraday_trend import TrendSub, _clamp


def test_clamp_bounds():
    assert _clamp(150, 0, 100) == 100
    assert _clamp(-5, 0, 100) == 0
    assert _clamp(42.0, 0, 100) == 42.0


def test_trendsub_is_frozen():
    s = TrendSub(score=72.5, confidence=0.8, interp="x")
    assert s.score == 72.5 and s.confidence == 0.8
    import dataclasses, pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.score = 1.0


from scoring.intraday_trend import score_price


def test_score_price_strong_bull():
    s = score_price(alignment_pct=100, price_vs_vwap_pct=0.6, macd_hist=0.5,
                    rsi=70, adx=40, n_timeframes=3)
    assert s.score > 90 and s.confidence == 1.0


def test_score_price_strong_bear():
    s = score_price(alignment_pct=-100, price_vs_vwap_pct=-0.6, macd_hist=-0.5,
                    rsi=30, adx=40, n_timeframes=3)
    assert s.score < 10


def test_score_price_chop_stays_near_neutral():
    s = score_price(alignment_pct=20, price_vs_vwap_pct=0.1, macd_hist=0.1,
                    rsi=55, adx=12, n_timeframes=3)
    assert 45 <= s.score <= 60


def test_score_price_missing_data_low_conf():
    s = score_price(alignment_pct=0, price_vs_vwap_pct=0, macd_hist=0,
                    rsi=50, adx=20, n_timeframes=0)
    assert s.confidence == 0.0


from scoring.intraday_trend import score_breadth_dir


def test_breadth_strong_positive():
    s = score_breadth_dir(net_ad=0.8, pct_above_50=80, new_highs=300, new_lows=20)
    assert s.score > 75 and s.confidence > 0


def test_breadth_strong_negative():
    s = score_breadth_dir(net_ad=-0.8, pct_above_50=20, new_highs=20, new_lows=300)
    assert s.score < 25


def test_breadth_missing_data():
    s = score_breadth_dir(net_ad=None, pct_above_50=None, new_highs=0, new_lows=0)
    assert s.confidence == 0.0 and s.score == 50.0


from scoring.intraday_trend import score_sector_participation


def test_sector_broad_green_cyclical_lead():
    s = score_sector_participation(n_green=10, n_total=11, cyc_def_spread=1.0)
    assert s.score > 75 and s.confidence > 0.9


def test_sector_broad_red_defensive_lead():
    s = score_sector_participation(n_green=1, n_total=11, cyc_def_spread=-1.0)
    assert s.score < 25


def test_sector_no_data():
    s = score_sector_participation(n_green=0, n_total=0, cyc_def_spread=None)
    assert s.confidence == 0.0 and s.score == 50.0


from scoring.intraday_trend import score_vix_context, vol_confidence_factor


def test_vix_low_and_falling_bullish():
    s = score_vix_context(vix=12, vix_change_pct=-6, vix1d=11, vix9d=14)
    assert s.score > 65


def test_vix_high_and_spiking_bearish():
    s = score_vix_context(vix=30, vix_change_pct=8, vix1d=34, vix9d=28)
    assert s.score < 35


def test_vix_missing():
    s = score_vix_context(vix=0, vix_change_pct=0, vix1d=0, vix9d=0)
    assert s.confidence == 0.0


def test_vol_confidence_factor_damps_on_spike():
    assert vol_confidence_factor(0) == 1.0
    assert vol_confidence_factor(15) < 0.7
    assert vol_confidence_factor(-10) == 1.0


from scoring.intraday_trend import blend_trend, TREND_WEIGHTS


def test_trend_weights_sum_to_one():
    assert abs(sum(TREND_WEIGHTS.values()) - 1.0) < 1e-9


def test_blend_all_bull():
    scores = {"price": 90, "breadth": 80, "sector": 85, "vix": 70}
    confs = {"price": 1.0, "breadth": 1.0, "sector": 1.0, "vix": 1.0}
    score, conf = blend_trend(scores, confs)
    assert 80 <= score <= 90 and conf == 1.0


def test_blend_low_conf_cannot_dominate():
    scores = {"price": 0, "breadth": 60, "sector": 60, "vix": 60}
    confs = {"price": 0.01, "breadth": 1.0, "sector": 1.0, "vix": 1.0}
    score, _ = blend_trend(scores, confs)
    assert score > 55


def test_blend_no_confidence_defaults_neutral():
    score, conf = blend_trend({"price": 90}, {"price": 0.0})
    assert score == 50.0 and conf == 0.0


from scoring.intraday_trend import score_to_state, ema_smooth


def test_band_edges():
    assert score_to_state(85) == "bull_trend"
    assert score_to_state(80) == "bull_trend"
    assert score_to_state(75) == "pullback_in_bull"
    assert score_to_state(70) == "pullback_in_bull"
    assert score_to_state(50) == "range"
    assert score_to_state(30) == "range"
    assert score_to_state(25) == "bear_rally"
    assert score_to_state(20) == "bear_rally"
    assert score_to_state(10) == "bear_trend"


def test_ema_smooth_first_value_passthrough():
    assert ema_smooth(None, 70.0, span=3) == 70.0


def test_ema_smooth_moves_toward_new():
    out = ema_smooth(50.0, 80.0, span=3)
    assert out == 65.0


from scoring.trend_regime import commit_state


def test_commit_state_needs_two_reads_to_flip():
    committed, hist = commit_state("range", [], None)
    assert committed == "range"
    committed, hist = commit_state("bull_trend", hist, committed)
    assert committed == "range"
    committed, hist = commit_state("bull_trend", hist, committed)
    assert committed == "bull_trend"


# ── NaN hardening (2026-08-20) ──────────────────────────────────────
# A NaN survives `is not None` and `not x`, then `_clamp(nan, lo, hi)` returns
# the HIGH bound (min(hi, nan) is hi), so an absent reading rendered as a
# confident MAXIMUM one. These pin the missing-input policy each function
# already declares for None.

def test_score_vix_context_treats_a_nan_vix_as_no_reading():
    """A NaN VIX passed `not vix` and `vix <= 0`, then clamped to the top of
    every band: measured score 70.0 at confidence 1.0 -- a confidently bullish
    trend read produced from no data at all."""
    from math import nan
    from scoring.intraday_trend import score_vix_context
    r = score_vix_context(nan, 0.0, None, None)
    assert r.score == 50.0 and r.confidence == 0.0


def test_score_vix_context_treats_a_nan_change_as_no_reading():
    from math import nan
    from scoring.intraday_trend import score_vix_context
    r = score_vix_context(18.0, nan, None, None)
    assert r.score == 50.0 and r.confidence == 0.0


def test_score_breadth_dir_drops_a_nan_component_instead_of_maxing_it():
    """NaN net_ad clamped to +1 at weight 0.4 -> score 100.0 (maximum bullish
    breadth). It must drop out exactly as None does."""
    from math import nan
    from scoring.intraday_trend import score_breadth_dir
    assert score_breadth_dir(nan, None, 0, 0) == score_breadth_dir(None, None, 0, 0)


def test_score_sector_participation_drops_a_nan_spread():
    from math import nan
    from scoring.intraday_trend import score_sector_participation
    assert (score_sector_participation(5, 11, nan)
            == score_sector_participation(5, 11, None))


# ── a saturated 0.0 score is a SCORE, not an absence (2026-08-20) ───────────

def test_blend_trend_keeps_a_score_of_exactly_zero():
    """`scores.get(k, 50.0) or 50.0` replaced a 0.0 score with neutral 50 — and
    0.0 is not a measure-zero corner: score_price clamps to [0,100], so the ENTIRE
    crash-tape region (full misalignment × high ADX) lands on exactly 0.0.
    Measured: the most bearish possible tape blended to 36.5 while one tick off
    the floor blended to 14.0 — a 22.5-point bullish jump at confidence 1.0.
    Only ABSENCE (key missing / None) means neutral."""
    from scoring.intraday_trend import blend_trend
    scores = {"price": 0.0, "breadth": 20.0, "sector": 30.0, "vix": 30.0}
    confs = {"price": 1.0, "breadth": 1.0, "sector": 1.0, "vix": 1.0}
    at_floor, _ = blend_trend(scores, confs)
    near_floor, _ = blend_trend({**scores, "price": 0.01}, confs)
    assert abs(at_floor - near_floor) < 0.01     # continuous at the floor
    assert at_floor < 15.0                       # genuinely bearish, not 36.5


def test_blend_trend_still_defaults_an_absent_or_none_score_to_neutral():
    from scoring.intraday_trend import blend_trend
    confs = {"price": 1.0, "breadth": 1.0}
    missing, _ = blend_trend({"price": 60.0}, confs)             # breadth absent
    explicit_none, _ = blend_trend({"price": 60.0, "breadth": None}, confs)
    assert missing == explicit_none
    # breadth contributes neutral 50 at its weight, not 0
    assert 50.0 < missing < 60.0


def test_score_price_crash_tape_blends_bearish_end_to_end():
    """The reachable path: score_price's own 0.0 must survive into the blend."""
    from scoring.intraday_trend import blend_trend, score_price
    p = score_price(-100, -2.0, -1.0, 10, 45, n_timeframes=3)
    assert p.score == 0.0 and p.confidence == 1.0
    out, conf = blend_trend(
        {"price": p.score, "breadth": 20.0, "sector": 30.0, "vix": 30.0},
        {"price": p.confidence, "breadth": 1.0, "sector": 1.0, "vix": 1.0})
    assert out < 15.0 and conf == 1.0


def test_vix_context_renormalizes_when_vix1d_is_absent():
    """The $VIX1D term carries 0.2 of the weight. With it absent the old code fed
    a literal 0 into the sum, which is not "no opinion" — it structurally shrank
    the sub-score's deflection from 50 by 20% while still reporting confidence
    1.0. $VIX1D does not quote for this account (verified live 2026-08-20), so
    this was a standing bias, not an outage case.

    Same shape vix.score_complex was fixed for this morning: renormalize the
    SCORE over the weight present, and let the confidence carry the absence.
    """
    from scoring.intraday_trend import score_vix_context
    without = score_vix_context(18.0, -2.0, None, 17.0)
    # The two present terms must count at FULL weight: lvl=+0.2, chg=+0.4 ->
    # (0.4*0.2 + 0.4*0.4) / 0.8 = 0.30 -> 65.0. Feeding the absent term in as a
    # literal 0 divided by the full 1.0 instead and returned 62.0 — the same
    # reading, shrunk 20% toward neutral for no reason.
    assert without.score == pytest.approx(65.0)


def test_vix_context_confidence_falls_when_vix1d_is_absent():
    """The absence has to show up somewhere — it belongs in the confidence, which
    is what down-weights the sub-score in blend_trend."""
    from scoring.intraday_trend import score_vix_context
    assert score_vix_context(18.0, -2.0, 16.0, 17.0).confidence == 1.0
    assert score_vix_context(18.0, -2.0, None, 17.0).confidence == pytest.approx(0.8)


def test_vix_context_unchanged_when_every_term_is_present():
    from scoring.intraday_trend import score_vix_context
    r = score_vix_context(18.0, -2.0, 16.0, 17.0)
    lvl = (20.0 - 18.0) / 10.0
    chg = -(-2.0) / 5.0
    term = (18.0 - 16.0) / 2.0
    expected = 50 + 50 * (0.4 * lvl + 0.4 * chg + 0.2 * term)
    assert r.score == pytest.approx(round(expected, 2))
