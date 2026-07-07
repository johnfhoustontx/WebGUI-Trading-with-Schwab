from scoring.daily_direction import (
    daily_direction_score,
    reconstruct_state_series,
)


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


def test_uptrend_reconstructs_state_late():
    bars = _bars([100 + i for i in range(260)],
                 vols=[1_000_000 + (i % 2) * 800_000 for i in range(260)])
    states = reconstruct_state_series(bars)
    assert len(states) == len(bars)
    assert all(s is None for s in states[:200])
    assert states[-1] in {"bullish", "lack_of_bullishness", "neutral",
                          "lack_of_bearishness", "bearish"}


def test_series_deterministic():
    bars = _bars([200 + (i % 5) for i in range(260)])
    assert reconstruct_state_series(bars) == reconstruct_state_series(bars)
