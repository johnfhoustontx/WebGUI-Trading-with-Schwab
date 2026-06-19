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
