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
