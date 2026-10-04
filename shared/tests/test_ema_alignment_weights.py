"""``calculate_ema_alignment`` weights each timeframe from one table, by name.

Audit AC-42 (2026-10-03): the sentiment service passed its daily frame under
the key ``"1day"`` while ``TIMEFRAME_WEIGHTS`` says ``"daily"``. The lookup
defaulted an unknown key to 1.0, so the daily frame - meant to carry three
times the 5-minute frame's weight - carried the same, and nothing said so.
"""
import pandas as pd
import pytest

from shared.analysis_lib import config, technical


def _trend(start, step, n=260):
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame({"open": closes, "high": closes, "low": closes,
                         "close": closes, "volume": [1_000] * n})


# A price of 380 sits ABOVE the rising frame's EMAs (bullish there) and BELOW
# the falling frame's (bearish there), so each frame votes its own way.
RISING = _trend(100.0, 1.0)       # ends 359; EMA12 > EMA21 > EMA50, all below 380
FALLING = _trend(659.0, -1.0)     # ends 400; EMA12 < EMA21 < EMA50, all above 380
PRICE = 380.0


def _status(result):
    return {t["timeframe"]: t["status"] for t in result["timeframes"]}


def test_the_fixture_frames_vote_as_described():
    out = technical.calculate_ema_alignment({"daily": RISING, "5min": FALLING}, PRICE)
    assert _status(out) == {"daily": "BULLISH", "5min": "BEARISH"}


def test_the_daily_frame_outweighs_the_two_intraday_frames_together():
    # +3.0 (daily) - 1.5 (15min) - 1.0 (5min) over 5.5 = +9.09%.
    out = technical.calculate_ema_alignment(
        {"5min": FALLING, "15min": FALLING, "daily": RISING}, PRICE)
    assert out["alignment_percentage"] == pytest.approx(0.5 / 5.5 * 100)


def test_an_unknown_timeframe_name_raises_rather_than_picking_a_weight():
    with pytest.raises(ValueError, match="1day"):
        technical.calculate_ema_alignment({"1day": RISING}, PRICE)


def test_every_weighted_name_is_accepted():
    for name in config.TIMEFRAME_WEIGHTS:
        out = technical.calculate_ema_alignment({name: RISING}, PRICE)
        assert out["alignment_percentage"] == pytest.approx(100.0)
