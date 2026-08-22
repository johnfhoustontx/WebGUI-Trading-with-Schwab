from scoring.daily_direction import (
    daily_direction_score,
    reconstruct_state_series,
    forward_returns,
    per_state_stats,
    ordinal_ic,
    STATE_ORDINAL,
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


def test_forward_returns_causal():
    fr = forward_returns([100, 110, 121], horizon=1)
    assert abs(fr[0] - 0.10) < 1e-9 and abs(fr[1] - 0.10) < 1e-9 and fr[2] is None


def test_per_state_stats():
    st = per_state_stats(["bullish", "bearish", "bullish", "bearish"],
                         [0.02, -0.03, 0.04, -0.01])
    assert abs(st["bullish"]["mean"] - 0.03) < 1e-9 and st["bullish"]["n"] == 2
    assert st["bullish"]["hit_rate"] == 1.0 and st["bearish"]["hit_rate"] == 0.0


def test_ordinal_ic_positive():
    states = ["bearish", "lack_of_bullishness", "neutral",
              "lack_of_bearishness", "bullish"]
    fwd = [-0.02, -0.01, 0.0, 0.01, 0.02]
    assert ordinal_ic(states, fwd) > 0.9
    assert STATE_ORDINAL == {"bearish": -2, "lack_of_bullishness": -1,
                             "neutral": 0, "lack_of_bearishness": 1,
                             "bullish": 2}


def test_stats_ignore_none():
    assert per_state_stats([None, "bullish"], [None, 0.01])["bullish"]["n"] == 1


# --- NaN inputs are MISSING, never a reading (the _clamp-pins-the-bound trap) ---
NAN = float("nan")


def test_all_nan_closes_score_neutral_not_bullish():
    assert daily_direction_score(_bars([NAN] * 220)) == 50.0


def test_a_nan_close_reads_as_a_missing_bar():
    clean = [100 + i for i in range(220)]
    dirty = list(clean)
    dirty[100] = NAN
    assert (daily_direction_score(_bars(dirty))
            == daily_direction_score(_bars(clean[:100] + clean[101:])))


def test_nan_price_yields_no_forward_return():
    assert forward_returns([100, NAN, 121], horizon=1) == [None, None, None]


def test_nan_forward_return_is_excluded_from_state_stats():
    st = per_state_stats(["bullish", "bullish"], [0.02, NAN])
    assert st["bullish"]["n"] == 1
    assert st["bullish"]["mean"] == 0.02


def test_nan_forward_return_is_excluded_from_ic():
    states = ["bearish", "lack_of_bullishness", "neutral",
              "lack_of_bearishness", "bullish", "bullish"]
    # Dropping the NaN pair leaves five perfectly monotone pairs -> IC exactly
    # 1.0. Ranking THROUGH the NaN instead degrades it to ~0.9856.
    assert ordinal_ic(states, [-0.02, -0.01, 0.0, 0.01, 0.02, NAN]) > 0.999
