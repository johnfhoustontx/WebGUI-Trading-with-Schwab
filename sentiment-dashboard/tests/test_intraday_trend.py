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
