from scoring.daily_direction import daily_direction_score


def _bars(closes, vols=None):
    vols = vols or [1_000_000] * len(closes)
    return [{"open": c, "high": c * 1.005, "low": c * 0.995, "close": c,
             "volume": vols[i]} for i, c in enumerate(closes)]


def test_strong_uptrend_scores_high():
    assert daily_direction_score(_bars([100 + i for i in range(220)])) > 70


def test_strong_downtrend_scores_low():
    assert daily_direction_score(_bars([320 - i for i in range(220)])) < 30


def test_flat_scores_near_50():
    assert 40 <= daily_direction_score(_bars([100 + (i % 2) for i in range(220)])) <= 60


def test_insufficient_bars_neutral():
    assert daily_direction_score(_bars([100, 101, 102])) == 50.0
