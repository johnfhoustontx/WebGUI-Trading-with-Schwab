# Intraday Market Trend Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the slow daily 5-state SPY trend classifier with a responsive
0–100 directional intraday Market Trend score (recomputed every 15 min), drive the
Sentiment tab's two trend gauges from it, and map it back onto the existing 5-state
vocabulary for the bridge so Options Scanner's `regime_filter` is unchanged.

**Architecture:** A new pure `scoring/intraday_trend.py` module (scalar-in, no
pandas/I/O) computes four directional sub-scores — Price/MTF 45%, Breadth 25%,
Sector 20%, VIX 10% — and confidence-weighted-blends them to 0–100. The sentiment
service (Tier 2) extracts scalars from proxy data (reusing `compute_live`'s sector
fetch), EMA-smooths the needle, applies 2-read hysteresis to the discrete state
(reusing `trend_regime.commit_state`), and publishes to the cache + bridge. The
webgui page renders the score directly (no anchor+nudge). 15-min cadence is gated
in the service scheduler.

**Tech Stack:** Python 3.11, pandas/numpy, `shared/analysis_lib/technical`,
NiceGUI, Redis (Memurai) via `shared.bus`, pytest.

**Design doc:** [2026-06-19-intraday-market-trend-redesign-design.md](2026-06-19-intraday-market-trend-redesign-design.md)

**Reference facts (verified):**
- `ScoreResult` (`sentiment-dashboard/scoring/types.py`) is **int 1–10** — do NOT
  reuse it for the 0–100 trend; define a local `TrendSub(score: float, confidence:
  float, interp: str)` frozen dataclass in `intraday_trend.py`.
- `scoring/composite.blend(scores: dict, confs: dict, weights: dict) -> (composite:
  float, aggregate: float)` — mirror it for `blend_trend`.
- `scoring/trend_regime.commit_state(raw, history, prev_committed) -> (committed,
  new_history)` is **state-string-agnostic** with `HYSTERESIS_DAYS = 2` — reuse it
  verbatim for the 15-min 2-read hysteresis. Reuse `STATE_LABELS` /
  `STATE_DESCRIPTIONS` from the same module for the new states (same vocabulary).
- `technical.calculate_ema_alignment(data: dict[tf -> df], current_price) ->
  {'alignment_percentage': float in [-100,100], 'bias', 'timeframes'}` — needs ≥50
  bars per df.
- `technical.calculate_adx(df, period=14) -> float` (default 20.0),
  `calculate_rsi(df, period=14) -> float`, `calculate_vwap(df) -> Optional[float]`,
  `macd_histogram_series(df, ...) -> pd.Series`.
- Proxy: `get_daily_history(symbol, months) -> DataFrame|None`; intraday bars come
  from `/pricehistory` with `frequencyType=minute` (add a client helper).
- Service scheduler (`services/sentiment_svc/scheduler.py`): one startup full
  refresh, then `handlers.refresh` every `REFRESH_INTERVAL_SEC = 120`.
- Run sentiment service tests from the **repo root**:
  `.venv\Scripts\python -m pytest services\sentiment_svc`. Scoring-module tests run
  from inside the app: `cd sentiment-dashboard ; python -m pytest tests`. webgui:
  `cd webgui ; ..\.venv\Scripts\python -m pytest .`.

---

## Phase 1 — Pure scoring module `scoring/intraday_trend.py`

The testable heart. No pandas, no I/O — scalar in, scalar out. TDD each function.

### Task 1.1: `TrendSub` dataclass + module skeleton

**Files:**
- Create: `sentiment-dashboard/scoring/intraday_trend.py`
- Test: `sentiment-dashboard/tests/test_intraday_trend.py`

**Step 1 — failing test:**
```python
# sentiment-dashboard/tests/test_intraday_trend.py
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
```

**Step 2 — run, expect ImportError:**
`cd sentiment-dashboard ; python -m pytest tests/test_intraday_trend.py -q` → FAIL.

**Step 3 — implement:**
```python
# sentiment-dashboard/scoring/intraday_trend.py
"""Intraday directional Market Trend score (0-100, 50 = neutral).

Pure functions — scalar in, scalar out (no pandas, no tk, no I/O). The sentiment
service extracts scalars from proxy data and calls these; the webgui renders the
result. Distinct from the 1-10 *contrarian* composite: this is *directional*
(100 = max bull, 0 = max bear). Reuses the confidence-weighted blend idiom of
scoring/composite.py and the state vocabulary of scoring/trend_regime.py.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class TrendSub:
    score: float          # 0-100 directional
    confidence: float     # [0.0, 1.0]
    interp: str = ""


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))
```

**Step 4 — run, expect PASS.**

**Step 5 — commit:** `git commit -m "feat(trend): TrendSub dataclass + clamp helper"`

---

### Task 1.2: `score_price` (Price/MTF sub-score, 45%)

**Files:** Modify `scoring/intraday_trend.py`; Test same test file.

**Step 1 — failing tests:**
```python
from scoring.intraday_trend import score_price

def test_score_price_strong_bull():
    # Full bull alignment, price above VWAP, +MACD, RSI 70, strong ADX, all TFs.
    s = score_price(alignment_pct=100, price_vs_vwap_pct=0.6, macd_hist=0.5,
                    rsi=70, adx=40, n_timeframes=3)
    assert s.score > 90 and s.confidence == 1.0

def test_score_price_strong_bear():
    s = score_price(alignment_pct=-100, price_vs_vwap_pct=-0.6, macd_hist=-0.5,
                    rsi=30, adx=40, n_timeframes=3)
    assert s.score < 10

def test_score_price_chop_stays_near_neutral():
    # Mixed alignment + weak ADX => needle hugs 50 regardless of noise.
    s = score_price(alignment_pct=20, price_vs_vwap_pct=0.1, macd_hist=0.1,
                    rsi=55, adx=12, n_timeframes=3)
    assert 45 <= s.score <= 60

def test_score_price_missing_data_low_conf():
    s = score_price(alignment_pct=0, price_vs_vwap_pct=0, macd_hist=0,
                    rsi=50, adx=20, n_timeframes=0)
    assert s.confidence == 0.0
```

**Step 2 — run, expect FAIL.**

**Step 3 — implement:**
```python
def score_price(alignment_pct, price_vs_vwap_pct, macd_hist, rsi, adx,
                n_timeframes) -> TrendSub:
    """0-100 from MTF EMA alignment (dominant), VWAP, MACD sign, RSI; ADX scales
    how far the needle leaves 50 (strong trend -> extremes, chop -> ~50)."""
    a = _clamp(alignment_pct / 100.0, -1.0, 1.0)
    v = _clamp(price_vs_vwap_pct / 0.5, -1.0, 1.0)          # 0.5% above VWAP = full
    m = 1.0 if macd_hist > 0 else -1.0 if macd_hist < 0 else 0.0
    r = _clamp((rsi - 50.0) / 20.0, -1.0, 1.0)              # RSI 70->+1, 30->-1
    direction = 0.5 * a + 0.2 * v + 0.15 * m + 0.15 * r     # [-1, 1]
    adx_factor = _clamp(adx / 40.0, 0.3, 1.0)               # weak trend dampens
    score = _clamp(50.0 + 50.0 * direction * adx_factor, 0.0, 100.0)
    confidence = _clamp(n_timeframes / 3.0, 0.0, 1.0)
    return TrendSub(score=round(score, 2), confidence=round(confidence, 3))
```

**Step 4 — run, expect PASS.**

**Step 5 — commit:** `git commit -m "feat(trend): score_price MTF sub-score"`

---

### Task 1.3: `score_breadth_dir` (Breadth sub-score, 25%)

**Step 1 — failing tests:**
```python
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
```

**Step 3 — implement:**
```python
def score_breadth_dir(net_ad, pct_above_50, new_highs, new_lows) -> TrendSub:
    """net_ad = (advn-decn)/(advn+decn) in [-1,1]; pct_above_50 in [0,100];
    H/L counts. Missing inputs lower confidence; all-missing -> neutral/0 conf."""
    comps, weights = [], []
    if net_ad is not None:
        comps.append(_clamp(net_ad, -1, 1)); weights.append(0.4)
    if pct_above_50 is not None:
        comps.append(_clamp((pct_above_50 - 50.0) / 50.0, -1, 1)); weights.append(0.4)
    hl_total = (new_highs or 0) + (new_lows or 0)
    if hl_total > 0:
        comps.append(_clamp(((new_highs or 0) - (new_lows or 0)) / hl_total, -1, 1))
        weights.append(0.2)
    if not weights:
        return TrendSub(score=50.0, confidence=0.0)
    direction = sum(c * w for c, w in zip(comps, weights)) / sum(weights)
    confidence = _clamp(sum(weights), 0.0, 1.0)             # full data -> 1.0
    return TrendSub(score=round(_clamp(50 + 50 * direction, 0, 100), 2),
                    confidence=round(confidence, 3))
```

**Step 4 — PASS. Step 5 — commit** `feat(trend): score_breadth_dir sub-score`.

---

### Task 1.4: `score_sector_participation` (20%)

**Step 1 — failing tests:**
```python
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
```

**Step 3 — implement:**
```python
def score_sector_participation(n_green, n_total, cyc_def_spread) -> TrendSub:
    """Breadth of sector participation + cyclical-vs-defensive leadership.
    cyc_def_spread in ~[-1,1] (cyclicals leading positive)."""
    if not n_total:
        return TrendSub(score=50.0, confidence=0.0)
    participation = (n_green / n_total - 0.5) * 2.0         # [-1, 1]
    lead = _clamp(cyc_def_spread, -1, 1) if cyc_def_spread is not None else 0.0
    direction = 0.6 * participation + 0.4 * lead
    return TrendSub(score=round(_clamp(50 + 50 * direction, 0, 100), 2),
                    confidence=round(_clamp(n_total / 11.0, 0, 1), 3))
```

**Step 5 — commit** `feat(trend): score_sector_participation sub-score`.

---

### Task 1.5: `score_vix_context` (10%) + `vol_confidence_factor`

**Step 1 — failing tests:**
```python
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
    assert vol_confidence_factor(15) < 0.7      # big VIX spike -> less trust
    assert vol_confidence_factor(-10) == 1.0    # falling VIX does not damp
```

**Step 3 — implement:**
```python
def score_vix_context(vix, vix_change_pct, vix1d, vix9d) -> TrendSub:
    if not vix or vix <= 0:
        return TrendSub(score=50.0, confidence=0.0)
    lvl = _clamp((20.0 - vix) / 10.0, -1, 1)                # vix 10->+1, 30->-1
    chg = _clamp(-vix_change_pct / 5.0, -1, 1)             # falling vix bullish
    term = _clamp((vix - vix1d) / 2.0, -1, 1) if vix1d else 0.0  # vix1d spike = stress
    direction = 0.4 * lvl + 0.4 * chg + 0.2 * term
    return TrendSub(score=round(_clamp(50 + 50 * direction, 0, 100), 2),
                    confidence=1.0)


def vol_confidence_factor(vix_change_pct) -> float:
    """Global confidence multiplier: a sharp VIX *spike* makes any trend read less
    reliable. Falling/flat VIX -> 1.0. -0.04 per % of spike, floored at 0.4."""
    if vix_change_pct <= 0:
        return 1.0
    return round(_clamp(1.0 - 0.04 * vix_change_pct, 0.4, 1.0), 3)
```

**Step 5 — commit** `feat(trend): score_vix_context + vol_confidence_factor`.

---

### Task 1.6: `TREND_WEIGHTS` + `blend_trend`

**Step 1 — failing tests:**
```python
from scoring.intraday_trend import blend_trend, TREND_WEIGHTS

def test_trend_weights_sum_to_one():
    assert abs(sum(TREND_WEIGHTS.values()) - 1.0) < 1e-9

def test_blend_all_bull():
    scores = {"price": 90, "breadth": 80, "sector": 85, "vix": 70}
    confs = {"price": 1.0, "breadth": 1.0, "sector": 1.0, "vix": 1.0}
    score, conf = blend_trend(scores, confs)
    assert 80 <= score <= 90 and conf == 1.0

def test_blend_low_conf_cannot_dominate():
    # A wild bearish price read at near-zero confidence barely moves the blend.
    scores = {"price": 0, "breadth": 60, "sector": 60, "vix": 60}
    confs = {"price": 0.01, "breadth": 1.0, "sector": 1.0, "vix": 1.0}
    score, _ = blend_trend(scores, confs)
    assert score > 55

def test_blend_no_confidence_defaults_neutral():
    score, conf = blend_trend({"price": 90}, {"price": 0.0})
    assert score == 50.0 and conf == 0.0
```

**Step 3 — implement:**
```python
TREND_WEIGHTS = {"price": 0.45, "breadth": 0.25, "sector": 0.20, "vix": 0.10}


def blend_trend(scores, confs, weights=None):
    """Confidence-weighted blend -> (score_0_100, aggregate_confidence).
    Mirrors scoring/composite.blend. den==0 -> neutral 50.0, conf 0.0."""
    weights = weights or TREND_WEIGHTS
    num = den = 0.0
    for k, w in weights.items():
        c = float(confs.get(k, 0.0) or 0.0)
        s = float(scores.get(k, 50.0) or 50.0)
        num += w * s * c
        den += w * c
    if den <= 0:
        return 50.0, 0.0
    return round(num / den, 2), round(den, 3)
```

**Step 5 — commit** `feat(trend): TREND_WEIGHTS + blend_trend`.

---

### Task 1.7: `score_to_state` band mapping + EMA smoothing helper

**Step 1 — failing tests:**
```python
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
    out = ema_smooth(50.0, 80.0, span=3)   # alpha = 2/(3+1) = 0.5
    assert out == 65.0
```

**Step 3 — implement:**
```python
# Score bands -> 5-state vocabulary (range is the dominant 30-70 middle).
def score_to_state(score):
    if score >= 80:
        return "bull_trend"
    if score >= 70:
        return "pullback_in_bull"
    if score >= 30:
        return "range"
    if score >= 20:
        return "bear_rally"
    return "bear_trend"


def ema_smooth(prev, new, span=3):
    """EMA-smooth the published needle (~2-3 fifteen-min reads). prev None ->
    passthrough."""
    if prev is None:
        return round(float(new), 2)
    alpha = 2.0 / (span + 1.0)
    return round(alpha * float(new) + (1 - alpha) * float(prev), 2)
```

**Step 5 — commit** `feat(trend): score_to_state bands + ema_smooth`.

---

### Task 1.8: Hysteresis reuse smoke-test

**Step 1 — test (documents the reuse, no new code):**
```python
from scoring.trend_regime import commit_state

def test_commit_state_needs_two_reads_to_flip():
    committed, hist = commit_state("range", [], None)        # cold start
    assert committed == "range"
    committed, hist = commit_state("bull_trend", hist, committed)   # 1st bull read
    assert committed == "range"                               # not yet
    committed, hist = commit_state("bull_trend", hist, committed)   # 2nd bull read
    assert committed == "bull_trend"                          # flips
```

**Step 2/4 — run, expect PASS immediately** (reusing existing code).
**Step 5 — commit** `test(trend): pin commit_state reuse for 15-min hysteresis`.

---

### Task 1.9: Run the whole scoring suite

Run: `cd sentiment-dashboard ; python -m pytest tests/test_intraday_trend.py -q`
Expected: all PASS. Then `python -m pytest tests -q` — no regressions (the 2 known
date-relative failures elsewhere in the repo do not apply here).
**Commit** if any incidental fixups: `chore(trend): phase-1 scoring suite green`.

---

## Phase 2 — Proxy client intraday history

### Task 2.1: `get_intraday_history`

**Files:**
- Modify: `schwab-proxy/proxy_client.py` (add method after `get_daily_history`)
- Test: `schwab-proxy/tests/test_proxy_client.py` (or the existing client test
  module — locate it first with `ls schwab-proxy/tests`)

**Step 1 — failing test** (mock `_proxy_get` to assert params + DataFrame shape):
```python
def test_get_intraday_history_params_and_shape(monkeypatch):
    from proxy_client import SchwabProxyClient
    c = SchwabProxyClient("http://x")
    captured = {}
    def fake_get(endpoint, params=None):
        captured["endpoint"], captured["params"] = endpoint, params
        return {"candles": [{"datetime": 1700000000000, "open": 1, "high": 2,
                             "low": 1, "close": 2, "volume": 10}]}
    monkeypatch.setattr(c, "_proxy_get", fake_get)
    df = c.get_intraday_history("SPY", minutes=15, days=1)
    assert captured["endpoint"] == "/pricehistory"
    assert captured["params"]["frequencyType"] == "minute"
    assert captured["params"]["frequency"] == 15
    assert captured["params"]["periodType"] == "day"
    assert list(df["close"]) == [2]
```

**Step 3 — implement** (mirror `get_daily_history`):
```python
def get_intraday_history(self, symbol: str, minutes: int = 15, days: int = 1):
    """Intraday minute-bar OHLCV history -> pandas DataFrame or None."""
    import pandas as pd
    data = self._proxy_get("/pricehistory", params={
        "symbol": symbol, "periodType": "day", "period": days,
        "frequencyType": "minute", "frequency": minutes,
    })
    if not data or not data.get("candles"):
        return None
    df = pd.DataFrame(data["candles"])
    df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
    return df
```

**Step 4 — PASS. Step 5 — commit** `feat(proxy): get_intraday_history client helper`.

> Note: `period` for `periodType=day` valid values are 1–10. `minutes` valid: 1,
> 5, 10, 15, 30. Pass 15. If the live proxy rejects `period`, drop it (Schwab
> defaults to 1 day) — verify against the running proxy in Phase 7.

---

## Phase 3 — Service compute

### Task 3.1: `compute_intraday_trend` — scalar extraction + blend

**Files:**
- Modify: `services/sentiment_svc/compute.py`
- Test: `services/sentiment_svc/tests/test_compute.py`

This function does the pandas/proxy work, then calls Phase-1 pure functions. Keep
the extraction defensive (any sub-fetch failure -> that sub-score gets conf 0).

**Step 1 — failing test** (inject a fake schwab with canned intraday/quotes so the
test is deterministic; assert shape + that a clean bull tape yields score > 60):
```python
def test_compute_intraday_trend_shape_and_bull(monkeypatch):
    from services.sentiment_svc import compute
    fake = _FakeBullSchwab()          # returns rising bars, advn>decn, low vix
    sd = _sample_sector_data()
    out = compute.compute_intraday_trend(fake, sd, prior_state_history=[],
                                         prev_smoothed=None)
    assert set(out) >= {"score", "smoothed_score", "state", "label",
                        "description", "sub_scores", "confidence"}
    assert 0 <= out["score"] <= 100
    assert out["score"] > 60 and out["state"] in ("bull_trend", "pullback_in_bull")
    assert set(out["sub_scores"]) == {"price", "breadth", "sector", "vix"}

def test_compute_intraday_trend_defensive_no_data():
    from services.sentiment_svc import compute
    out = compute.compute_intraday_trend(_FakeDeadSchwab(), [], [], None)
    assert out["score"] == 50.0 and out["confidence"] == 0.0
    assert out["state"] == "range"
```

**Step 3 — implement** (sketch — fill extraction against the real proxy DataFrame
columns `open/high/low/close/volume`):
```python
from scoring import intraday_trend as _it
from scoring import trend_regime as _tr   # reuse commit_state + labels

def _intraday_price_inputs(schwab, symbol="SPY"):
    """-> (alignment_pct, price_vs_vwap_pct, macd_hist, rsi, adx, n_timeframes)."""
    import sys, pathlib
    # technical is imported standalone (its dir on sys.path) to dodge the
    # shared.analysis_lib package __init__ — same isolation the service already
    # uses; add ANALYSIS_LIB dir to sys.path once at module import.
    from technical import (calculate_ema_alignment, calculate_vwap,
                           macd_histogram_series, calculate_rsi, calculate_adx)
    frames = {}
    for tf, mins in (("5min", 5), ("15min", 15)):
        df = schwab.get_intraday_history(symbol, minutes=mins, days=10)
        if df is not None and len(df) >= 50:
            frames[tf] = df
    dfd = schwab.get_daily_history(symbol, months=12)
    if dfd is not None and len(dfd) >= 50:
        frames["1day"] = dfd
    if not frames:
        return (0, 0, 0, 50, 20, 0)
    ref = frames.get("15min") or next(iter(frames.values()))
    price = float(ref["close"].iloc[-1])
    align = calculate_ema_alignment(frames, price)["alignment_percentage"]
    vwap = calculate_vwap(frames.get("15min")) if "15min" in frames else None
    vwap_pct = ((price - vwap) / vwap * 100.0) if vwap else 0.0
    hist = macd_histogram_series(ref)
    macd_hist = float(hist.iloc[-1]) if hist is not None and len(hist) else 0.0
    rsi = calculate_rsi(ref); adx = calculate_adx(ref)
    return (align, vwap_pct, macd_hist, rsi, adx, len(frames))

def compute_intraday_trend(schwab, sector_data, prior_state_history,
                           prev_smoothed=None):
    try:
        ap, vp, mh, rsi, adx, ntf = _intraday_price_inputs(schwab)
        price = _it.score_price(ap, vp, mh, rsi, adx, ntf)
    except Exception:
        price = _it.TrendSub(50.0, 0.0)
    # breadth: reuse live_composite's $ADVN/$DECN/$SPXA50R/$NYHGH/$NYLOW fetch,
    #   compute net_ad = (advn-decn)/(advn+decn); pct_above_50; H/L counts.
    breadth = _safe_breadth_dir(schwab)
    # sector: reuse compute_live's sector quotes + dual momentum already fetched.
    sector = _safe_sector_participation(schwab, sector_data)
    # vix: $VIX quote + intraday change (vs prior close) + $VIX1D/$VIX9D.
    vix = _safe_vix_context(schwab)
    scores = {"price": price.score, "breadth": breadth.score,
              "sector": sector.score, "vix": vix.score}
    confs = {"price": price.confidence, "breadth": breadth.confidence,
             "sector": sector.confidence, "vix": vix.confidence}
    raw_score, agg = _it.blend_trend(scores, confs)
    agg *= _it.vol_confidence_factor(_vix_change_pct_cache)   # global vol damper
    smoothed = _it.ema_smooth(prev_smoothed, raw_score, span=3)
    raw_state = _it.score_to_state(smoothed)
    committed, hist = _tr.commit_state(raw_state, prior_state_history, 
                                       prior_state_history[-1] if prior_state_history else None)
    return {
        "score": raw_score, "smoothed_score": smoothed,
        "state": committed, "raw_state": raw_state,
        "label": _tr.STATE_LABELS[committed],
        "description": _tr.STATE_DESCRIPTIONS[committed],
        "confidence": round(agg, 3),
        "sub_scores": {"price": price.score, "breadth": breadth.score,
                       "sector": sector.score, "vix": vix.score},
        "sub_confidence": confs,
        "state_history": hist,
    }
```
Implement `_safe_breadth_dir`, `_safe_sector_participation`, `_safe_vix_context` as
small defensive helpers extracting the scalars (reuse `live_composite._BREADTH`
symbol lists + `_last`). Each returns a `TrendSub`, conf 0 on any failure.

**Step 4 — PASS both tests. Step 5 — commit** `feat(sentiment): compute_intraday_trend`.

---

### Task 3.2: `compute_30d_trend` (second gauge — daily structural)

**Step 1 — failing test:**
```python
def test_compute_30d_trend_from_daily(monkeypatch):
    from services.sentiment_svc import compute
    spy = _rising_daily_closes(220)          # clean uptrend
    out = compute.compute_30d_trend(spy_daily_df=_df_from_closes(spy),
                                    sector_month_pcts={"XLK": 5.0, "XLF": 4.0})
    assert 0 <= out["score"] <= 100 and out["score"] > 60
    assert out["state"] in ("bull_trend", "pullback_in_bull")

def test_compute_30d_trend_insufficient_data():
    from services.sentiment_svc import compute
    out = compute.compute_30d_trend(spy_daily_df=None, sector_month_pcts={})
    assert out["score"] == 50.0
```

**Step 3 — implement:** daily EMA alignment (`{"1day": dfd}`) → price sub-score;
sector month-% breadth → sector sub-score; blend with the price+sector weights
renormalized (no live breadth/vix at 30-day horizon). Map via `score_to_state`. No
smoothing/hysteresis (it's a slow structural read). Defensive → neutral.

**Step 5 — commit** `feat(sentiment): compute_30d_trend structural gauge`.

---

### Task 3.3: Wire into `derive_composite_extras`

**Files:** Modify `services/sentiment_svc/compute.py` (`derive_composite_extras`),
plus `handlers.refresh` to pass `schwab` + persist trend state (see Phase 4).

**Step 1 — update test** `test_derive_composite_extras_*`: assert the returned
`trend` dict now carries `score` + `sub_scores` + `state`, and `trend_30d_ago`
(rename in a follow-up; keep key for page compat) carries the 30-day score.

**Step 3 — implement:** replace the `build_trend_dict(spy)` /
`trend_30d_ago` block with calls to `compute_intraday_trend` (needs `schwab` +
sector_data + persisted state) and `compute_30d_trend`. Keep the `trend` /
`trend_30d_ago` keys so the page contract holds; add new fields. `build_trend_dict`
stays only if still used by the bridge daily back-compat fields (Phase 5) — else
delete (YAGNI).

**Step 5 — commit** `feat(sentiment): derive trend from intraday model`.

---

### Task 3.4: Run service suite

Run: `.venv\Scripts\python -m pytest services\sentiment_svc -q` → all PASS.
**Commit** any fixups.

---

## Phase 4 — 15-min cadence + persisted trend state

### Task 4.1: `trend_due` gate + service state

**Files:**
- Modify: `services/sentiment_svc/scheduler.py`
- Test: `services/sentiment_svc/tests/test_scheduler.py`

**Step 1 — failing test:**
```python
def test_trend_due_15min_gate():
    from services.sentiment_svc import scheduler
    assert scheduler.trend_due(now=900.0, last=0.0) is True      # 15 min elapsed
    assert scheduler.trend_due(now=600.0, last=0.0) is False     # only 10 min
    assert scheduler.trend_due(now=900.0, last=None) is True     # cold start
```

**Step 3 — implement:**
```python
TREND_INTERVAL_SEC = 900   # 15 minutes

def trend_due(now, last):
    return last is None or (now - last) >= TREND_INTERVAL_SEC
```

**Step 4 — PASS.**

**Step 5 — commit** `feat(sentiment): trend_due 15-min cadence gate`.

### Task 4.2: Drive the gate from the loop + persist state

Modify `loop(bus)`: keep a `last_trend = None` and `trend_state = {"history": [],
"smoothed": None}` in the loop scope; each 120 s tick, if `trend_due`, call
`handlers.refresh(..., do_trend=True)` passing/receiving the persisted smoothing +
hysteresis history (store back into `trend_state`). Use a monotonic clock passed in
(don't call `Date.now`-equivalents in pure code — the loop owns time). Add a
`handlers`-level seam so the cache write happens there.

**Test:** extend `test_handlers.py` to assert that when `do_trend=True` the handler
calls `compute.compute_intraday_trend` and threads `prev_smoothed`/`state_history`.

**Commit** `feat(sentiment): 15-min trend refresh wired into scheduler loop`.

---

## Phase 5 — Bridge mapping (regime_filter back-compat)

### Task 5.1: Feed the mapped state + additive fields

**Files:**
- Modify: `sentiment-dashboard/live_composite.py` (`build_bridge_payload`,
  `publish_bridge`) — the `trend` dict now comes from the intraday model.
- Test: `sentiment-dashboard/tests/test_bridge.py` (or `test_live_composite` if
  present).

**Step 1 — failing test:**
```python
def test_bridge_trend_regime_from_intraday():
    from live_composite import build_bridge_payload
    trend = {"state": "bull_trend", "label": "Bull Trend", "description": "...",
             "raw_state": "bull_trend", "trend_score": 84.0,
             "sub_scores": {"price": 88, "breadth": 80, "sector": 82, "vix": 70},
             "sma_50": 500.0, "sma_200": 480.0, "sma_200_slope_pct": 0.1,
             "drawdown_pct": -1.0, "confidence": 0.9}
    p = build_bridge_payload(_snap(), [], [], "2026-06-19T00:00:00Z", trend=trend)
    assert p["trend_regime"]["state"] == "bull_trend"          # regime_filter reads this
    assert p["trend_regime"]["trend_score"] == 84.0            # additive
    assert "sma_50" in p["trend_regime"]                       # back-compat kept
```

**Step 3 — implement:** in `build_bridge_payload`, add `trend_score` + `sub_scores`
to the `trend_regime` block (additive); keep `sma_*`/`drawdown` (now sourced from
the daily classify in `publish_bridge`, retained for `regime_filter`). In
`publish_bridge`, replace the `_tr.classify(spy_closes)` trend dict with the
intraday model output, but ALSO run `_tr.classify` to fill the daily `sma_*` fields
for back-compat.

**Step 4 — PASS.**

**Step 5 — commit** `feat(bridge): publish intraday trend state (5-state mapped)`.

### Task 5.2: Pin `regime_filter` still reads the state

**Test (no prod change):** in `options-scanner/tests/test_regime_filter.py`, add a
case feeding a bridge with the new `trend_regime` block (incl. `trend_score`) and
assert `evaluate_regime()` resolves the state unchanged. Run:
`cd options-scanner ; python -m pytest tests/test_regime_filter.py -q`.
**Commit** `test(regime): pin trend_regime consumption under new bridge fields`.

---

## Phase 6 — webgui page

### Task 6.1: `trend_gauge_value` returns the score directly

**Files:**
- Modify: `webgui/pages/sentiment.py`
- Test: `webgui/tests/test_sentiment.py`

**Step 1 — update tests:** the needle now equals the score (no anchor+nudge).
```python
def test_trend_gauge_value_uses_score_directly():
    from pages.sentiment import trend_gauge_value
    assert trend_gauge_value({"score": 84.0}) == 84.0
    assert trend_gauge_value({"smoothed_score": 62.5}) == 62.5   # prefers smoothed
    assert trend_gauge_value(None) == 50.0
    assert trend_gauge_value({}) == 50.0
```

**Step 3 — implement:**
```python
def trend_gauge_value(trend):
    """0-100 needle = the intraday trend score directly (smoothed if present)."""
    t = trend or {}
    v = t.get("smoothed_score", t.get("score"))
    return _clamp(_safe_float(v, 50.0), 0.0, 100.0) if v is not None else 50.0
```
Delete the now-dead `_TREND_ANCHORS` + nudge logic (YAGNI). Keep `_TREND_SHORT`.

**Step 5 — commit** `feat(sentiment-page): needle = intraday trend score`.

### Task 6.2: Sub-score popup builder

**Step 1 — failing test:**
```python
def test_trend_subscore_rows():
    from pages.sentiment import trend_subscore_rows
    rows = trend_subscore_rows({"sub_scores": {"price": 88, "breadth": 80,
        "sector": 82, "vix": 70}, "sub_confidence": {"price": 1.0, "breadth": 0.9,
        "sector": 1.0, "vix": 1.0}})
    assert {"name": "Price / MTF", "score": "88.0", "weight": "45%",
            "conf": "1.00"} in rows
    assert len(rows) == 4
```

**Step 3 — implement** `trend_subscore_rows(trend)` returning display rows
(name/score/weight/conf) using `intraday_trend.TREND_WEIGHTS`-equivalent labels
hard-coded on the page side (page imports no engine — keep a local
`_TREND_SUB_META = [("price","Price / MTF","45%"), ...]`).

**Step 5 — commit** `feat(sentiment-page): trend sub-score popup builder`.

### Task 6.3: Render wiring (manual verify)

Modify `render()`: gauge 1 from `trend_gauge_value(trend)` + state label/desc; gauge
2 from `trend_gauge_value(trend_30d_ago)` with caption "30-Day"; add the
press-and-hold popup (mirror the component-table `ui.menu().props("no-parent-event")`
pattern) showing `trend_subscore_rows`. No unit test for widgets — verified by
screenshot in Phase 7.

**Step 5 — commit** `feat(sentiment-page): two trend gauges + sub-score popup`.

### Task 6.4: Run webgui suite

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest . -q` → green.

---

## Phase 7 — Integration verify + docs

### Task 7.1: Live verification

- Ensure Memurai + proxy + `sentiment_svc` + webgui are up (see root CLAUDE.md
  "Running"). Confirm the proxy on :8100 serves `/pricehistory?frequencyType=minute`
  (curl/quick script) — if it 400s on `period`, drop the `period` param (Task 2.1
  note) and re-run the proxy test.
- Use the Claude Preview tool: start `webgui`, open `/sentiment`, screenshot the
  Market Trend panel. Verify: live needle sits at a plausible 0–100 (not frozen at
  an anchor), state label matches the band, press-and-hold shows four sub-scores,
  second gauge shows a 30-day structural value. Re-screenshot after ~15 min (or
  force a refresh) to confirm the live needle moves while the bridge state is stable.
- Confirm the bridge: read `shared/sentiment_bridge.json` →
  `trend_regime.state` present + `trend_score` additive field present.

### Task 7.2: Update docs

- Update root `CLAUDE.md`: the `/sentiment` row + the Sentiment "DONE" section to
  describe the new intraday Market Trend model (0–100 directional, 15-min cadence,
  4 sub-scores, 5-state bridge mapping with range 30–70, second gauge = 30-day
  structural). Refresh the test counts.
- Update `sentiment-dashboard/CLAUDE.md` "Trend regime" section: note the new
  `scoring/intraday_trend.py` module supersedes the daily `trend_regime.classify`
  for the GUI/bridge state (daily `classify` retained only for back-compat
  `sma_*`/`drawdown` bridge fields).
- **Commit** `docs: intraday Market Trend model`.

### Task 7.3: Final full-suite sweep

Run each affected suite and paste results into the final summary:
```
cd sentiment-dashboard ; python -m pytest tests -q
.venv\Scripts\python -m pytest services\sentiment_svc -q     # from repo root
cd schwab-proxy ; python -m pytest tests -q
cd options-scanner ; python -m pytest tests/test_regime_filter.py -q
cd webgui ; ..\.venv\Scripts\python -m pytest . -q
```
**Commit** any final fixups.

---

## Notes & guardrails

- **DRY:** reuse `trend_regime.commit_state` + `STATE_LABELS`/`STATE_DESCRIPTIONS`
  and `live_composite._BREADTH`/`_last`; do not re-implement.
- **YAGNI:** no per-session 30-day replay store, no new bridge schema major bump
  (fields are additive), no `regime_filter` change.
- **3-tier rule:** the webgui page must import only nicegui + shared; all pandas /
  proxy / `technical` work stays in the service. The page-side sub-score labels are
  a small local constant, NOT an engine import.
- **`technical` import isolation:** import it standalone (its dir on `sys.path`) in
  the service, exactly as `trade_svc` does, to dodge the `shared.analysis_lib`
  package `__init__`.
- **Don't "fix"** the 2 known date-relative options-scanner failures.
