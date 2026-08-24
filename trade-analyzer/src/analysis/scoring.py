"""Normalized sub-score primitives. Each returns int in [-100, +100]."""
from typing import Optional, List


def score_rsi(rsi: float) -> int:
    if rsi < 30:
        return -90
    if rsi < 40:
        return -60
    if rsi < 50:
        return -20
    if rsi < 60:
        return 60
    if rsi <= 70:
        return 30
    return -20


def score_adx_directional(adx: float, ema_slope: float) -> int:
    direction = 1 if ema_slope >= 0 else -1
    if adx >= 25:
        return 100 * direction
    if adx >= 20:
        return 60 * direction
    if adx >= 15:
        return 30 * direction
    return 0


def score_macd(hist: float, hist_prev: float) -> int:
    if hist > 0 and hist > hist_prev:
        return 80
    if hist > 0 and hist <= hist_prev:
        return 30
    if hist <= 0 and hist > hist_prev:
        return -20
    return -80


def score_relative_volume(rv: float, ema_slope: float) -> int:
    direction = 1 if ema_slope >= 0 else -1
    if rv > 1.5:
        return 60 * direction
    if rv >= 1.0:
        return 20 * direction
    if rv < 0.7:
        return -30
    return 0


def score_vwap(price: float, vwap: float) -> int:
    if vwap == 0:
        return 0
    diff_pct = (price - vwap) / vwap
    if diff_pct >= 0.01:
        return 30
    if diff_pct > 0:
        return 60
    if diff_pct > -0.01:
        return -40
    return -80


def score_volume_profile_location(price: float, vp: dict) -> int:
    poc = vp["poc"]
    vah = vp["vah"]
    val = vp["val"]
    if poc != 0 and abs(price - poc) / poc <= 0.005:
        return 0
    if price > poc and price <= vah:
        return 50
    if price < val:
        return -60
    return 0


def score_relative_strength_percentile(percentile: float) -> int:
    return int(round((percentile - 0.5) * 200))


def score_distance_from_52wk_high(distance_pct: float) -> int:
    if distance_pct <= 0.05:
        return 60
    if distance_pct <= 0.15:
        return 20
    if distance_pct <= 0.30:
        return -20
    return -60


def score_pe_vs_sector(pe: Optional[float], sector_pe_median: Optional[float]) -> int:
    if pe is None or sector_pe_median is None or sector_pe_median == 0:
        return 0
    ratio = pe / sector_pe_median
    if ratio <= 0.7:
        return 60
    if ratio <= 1.0:
        return 30
    if ratio <= 1.3:
        return -10
    return -50


def score_peg(peg: Optional[float]) -> int:
    if peg is None:
        return 0
    if peg < 1:
        return 40
    if peg <= 2:
        return 0
    return -40


def score_growth_metric(growth: Optional[float]) -> int:
    if growth is None:
        return 0
    if growth > 0.15:
        return 80
    if growth >= 0.05:
        return 30
    if growth >= 0:
        return 0
    if growth >= -0.05:
        return -30
    return -80


def score_roe(roe: Optional[float]) -> int:
    if roe is None:
        return 0
    if roe > 0.15:
        return 60
    if roe >= 0.05:
        return 20
    return -40


def score_margin_trend(expanding: Optional[bool]) -> int:
    if expanding is None:
        return 0
    return 30 if expanding else -30


def score_earnings_surprise_streak(surprises: Optional[List[float]]) -> int:
    if not surprises:
        return 0
    # CHRONOLOGICAL: `[-1]` is the most recent quarter, so the streak is the
    # LAST four, not the first. `[:4]` was indistinguishable while every caller
    # and fixture passed exactly four entries; the vendor feed carries 100+,
    # where it scored a streak from the 1990s and let a company that just
    # missed its quarter keep an +80.
    if len(surprises) >= 4 and all(s > 0.05 for s in surprises[-4:]):
        return 80
    if surprises[-1] < 0:
        return -60
    return 0


def score_guidance_direction(direction: Optional[str]) -> int:
    if direction is None:
        return 0
    if direction == "RAISED":
        return 40
    if direction in ("LOWERED", "CUT"):
        return -60
    return 0
