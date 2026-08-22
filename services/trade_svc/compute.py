"""Trade compute module — NiceGUI-free engine-call layer for ``trade_svc``.

The service-side orchestration that the legacy desktop ``trade_analyzer.py``
``analyze()`` performed: fetch a symbol's multi-timeframe market data through
the shared proxy, compute the technical indicators, build the ``PositionInputs``
/ ``InvestorInputs``, and score the two verdict engines. This module must NOT
import ``nicegui`` or anything from ``webgui/`` — it depends only on the shared
``services._proxy`` accessor, the shared ``analysis_lib`` indicator library, and
the copied ``trade-analyzer`` verdict engines.

**Isolated top-level imports.** ``trade-analyzer`` exposes its engines as
``src.analysis.*`` and the shared indicator library lives at
``shared/analysis_lib/technical.py``. The shared package ``__init__`` eagerly
imports a Schwab client that breaks at import time, so ``technical`` is imported
*standalone* (its dir on ``sys.path``) rather than via ``shared.analysis_lib``.
Because ``trade_svc`` runs in its own process, pinning ``technical``/``config``/
``src`` as top-level modules cannot collide with the other domains' engines (the
same isolation ``sentiment_svc`` relies on for ``scoring``).

**Fundamentals** come from the proxy ``/instruments?projection=fundamental``
endpoint (``_proxy.schwab_client.get_fundamentals``) parsed by
``parse_schwab_fundamentals``. When sufficient (≥3 of P/E, rev growth, EPS
growth, ROE) the ``InvestorVerdict`` runs on real data and
``fundamentals_available`` is True; when the fetch fails or the data is thin it
degrades to an "Insufficient fundamental data" HOLD. The instruments payload has
no next-earnings date, so the Position earnings gate cannot fire (``days_to_
earnings`` is None).

Every engine-call function is defensive: per the page/service convention it
catches and degrades (returns ``None`` / an ``errors`` payload) rather than
raising, so one bad symbol can never crash the service.
"""
import os
import sys
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from repo_paths import TRADE_ANALYZER
from services import _proxy
from services._parallel import parallel_map

# ── isolated engine imports (separate process — no cross-app name collision) ──
_ANALYSIS_LIB = TRADE_ANALYZER.parent / "shared" / "analysis_lib"
if str(_ANALYSIS_LIB) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS_LIB))
if str(TRADE_ANALYZER) not in sys.path:
    sys.path.insert(0, str(TRADE_ANALYZER))

import technical  # noqa: E402  (shared indicator lib, imported standalone to dodge the package __init__)
from src.analysis import factors as _factors  # noqa: E402  (pure swing factor library)
from src.analysis import markov as _markov  # noqa: E402
from src.analysis import scoring as _scoring  # noqa: E402  (src.analysis.scoring — namespaced, not the colliding top-level `scoring`)
from src.analysis.fundamentals import Fundamentals, parse_schwab_fundamentals  # noqa: E402
from src.analysis.recommendation import (  # noqa: E402
    InvestorInputs, InvestorVerdict, PositionInputs, PositionVerdict)
from src.analysis.sector_strength import (  # noqa: E402
    SectorStrength, compute_sector_strength)
from services import _degrade  # noqa: E402

# Symbol → (sector name, sector ETF). A small built-in map for common large caps
# in lieu of the legacy finviz scrape; unknown symbols fall back to a neutral
# sector strength (no tailwind/headwind) rather than a wrong sector. ETF history
# drives the sector-strength factor (3-mo RS vs SPY + above-50EMA gate).
_SYMBOL_SECTOR = {
    # Technology
    "AAPL": ("Technology", "XLK"), "MSFT": ("Technology", "XLK"),
    "NVDA": ("Technology", "XLK"), "AVGO": ("Technology", "XLK"),
    "AMD": ("Technology", "XLK"), "CRM": ("Technology", "XLK"),
    "ORCL": ("Technology", "XLK"), "ADBE": ("Technology", "XLK"),
    "CSCO": ("Technology", "XLK"), "INTC": ("Technology", "XLK"),
    "QCOM": ("Technology", "XLK"), "TXN": ("Technology", "XLK"),
    "MU": ("Technology", "XLK"), "AMAT": ("Technology", "XLK"),
    # Communication Services
    "GOOGL": ("Communication Services", "XLC"),
    "GOOG": ("Communication Services", "XLC"),
    "META": ("Communication Services", "XLC"),
    "NFLX": ("Communication Services", "XLC"),
    "DIS": ("Communication Services", "XLC"),
    "T": ("Communication Services", "XLC"),
    "VZ": ("Communication Services", "XLC"),
    # Consumer Discretionary
    "AMZN": ("Consumer Discretionary", "XLY"),
    "TSLA": ("Consumer Discretionary", "XLY"),
    "HD": ("Consumer Discretionary", "XLY"),
    "MCD": ("Consumer Discretionary", "XLY"),
    "NKE": ("Consumer Discretionary", "XLY"),
    "SBUX": ("Consumer Discretionary", "XLY"),
    "LOW": ("Consumer Discretionary", "XLY"),
    # Consumer Staples
    "WMT": ("Consumer Staples", "XLP"), "COST": ("Consumer Staples", "XLP"),
    "PG": ("Consumer Staples", "XLP"), "KO": ("Consumer Staples", "XLP"),
    "PEP": ("Consumer Staples", "XLP"),
    # Financials
    "JPM": ("Financials", "XLF"), "BAC": ("Financials", "XLF"),
    "WFC": ("Financials", "XLF"), "GS": ("Financials", "XLF"),
    "MS": ("Financials", "XLF"), "C": ("Financials", "XLF"),
    "BRK.B": ("Financials", "XLF"), "V": ("Financials", "XLF"),
    "MA": ("Financials", "XLF"), "AXP": ("Financials", "XLF"),
    # Healthcare
    "UNH": ("Healthcare", "XLV"), "JNJ": ("Healthcare", "XLV"),
    "LLY": ("Healthcare", "XLV"), "PFE": ("Healthcare", "XLV"),
    "ABBV": ("Healthcare", "XLV"), "MRK": ("Healthcare", "XLV"),
    "TMO": ("Healthcare", "XLV"), "ABT": ("Healthcare", "XLV"),
    # Industrials
    "BA": ("Industrials", "XLI"), "CAT": ("Industrials", "XLI"),
    "GE": ("Industrials", "XLI"), "HON": ("Industrials", "XLI"),
    "UPS": ("Industrials", "XLI"), "RTX": ("Industrials", "XLI"),
    # Energy
    "XOM": ("Energy", "XLE"), "CVX": ("Energy", "XLE"),
    "COP": ("Energy", "XLE"), "SLB": ("Energy", "XLE"),
    # Materials
    "LIN": ("Materials", "XLB"), "FCX": ("Materials", "XLB"),
    # Utilities
    "NEE": ("Utilities", "XLU"), "DUK": ("Utilities", "XLU"),
    # Real Estate
    "AMT": ("Real Estate", "XLRE"), "PLD": ("Real Estate", "XLRE"),
}

# (timeframe name -> proxy /pricehistory params). Names match the shared
# ``TIMEFRAME_WEIGHTS`` keys so ``calculate_ema_alignment`` weights them. Schwab
# minute candles support frequency 1/5/10/15/30, so the "60min" slot uses 30-min
# candles (the legacy app's mapping).
_TIMEFRAME_PARAMS = {
    "1min": ("day", 2, "minute", 1),
    "5min": ("day", 5, "minute", 5),
    "15min": ("day", 10, "minute", 15),
    "60min": ("day", 10, "minute", 30),
    # 2yr daily so the swing factors with long warmups populate at the last bar:
    # mom_12_1 needs 252+21=273 bars, and pth/low_vol use rolling-252 windows.
    "daily": ("year", 2, "daily", 1),
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _candles_to_df(data):
    """Schwab /pricehistory JSON -> sorted OHLCV DataFrame, or None."""
    if not data or "candles" not in data or not data["candles"]:
        return None
    df = pd.DataFrame(data["candles"])
    if "datetime" not in df.columns:
        return None
    df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
    return df.sort_values("datetime").reset_index(drop=True)


def _price_history(symbol, period_type, period, freq_type, freq):
    """Fetch an OHLCV DataFrame for ``symbol`` via the proxy, or None on failure."""
    try:
        data = _proxy.schwab_client._request("/pricehistory", {
            "symbol": symbol, "periodType": period_type, "period": period,
            "frequencyType": freq_type, "frequency": freq,
        })
        return _candles_to_df(data)
    except Exception:
        return None


def _fetch_timeframes(symbol):
    """Fetch every configured timeframe concurrently; skip any that come back
    empty. The per-timeframe pulls are independent, I/O-bound proxy calls, so a
    thread pool overlaps them instead of serializing ~5 round-trips."""
    def _one(item):
        tf, (pt, p, ft, f) = item
        return tf, _price_history(symbol, pt, p, ft, f)

    out = {}
    for tf, df in parallel_map(_one, list(_TIMEFRAME_PARAMS.items())):
        if df is not None and not df.empty:
            out[tf] = df
    return out


def rs_percentile(sym_close, ref_close, lookback):
    """Relative-strength percentile of ``sym`` vs ``ref`` over ``lookback`` bars.

    Returns 0.5 (neutral) when either series is too short or ``ref`` is missing —
    the legacy ``_rs_percentile_safe`` behavior, so a missing sector/SPY history
    degrades to a neutral RS rather than a spurious extreme.
    """
    if ref_close is None or len(ref_close) < lookback + 1 or len(sym_close) < lookback + 1:
        return 0.5
    sym_ret = sym_close.iloc[-1] / sym_close.iloc[-lookback - 1] - 1
    ref_ret = ref_close.iloc[-1] / ref_close.iloc[-lookback - 1] - 1
    excess = sym_ret - ref_ret
    return float(np.clip(0.5 + excess / 0.40, 0.0, 1.0))


def resolve_sector(symbol):
    """Return ``{"name", "etf"}`` for a symbol; unknown -> neutral (no ETF)."""
    info = _SYMBOL_SECTOR.get((symbol or "").upper())
    if info:
        return {"name": info[0], "etf": info[1]}
    return {"name": "", "etf": ""}


def _neutral_sector_strength():
    return SectorStrength(score=0, in_confirmed_downtrend=False,
                          sector_above_50ema=True, rs_3m_percentile=0.5)


# ── Markov base-score reconstruction (Task 2.1) ──────────────────────────────
# Per-bar daily-only composite score the Markov chain learns from. Uses ONLY the
# nine daily-reconstructable factors (drops the intraday-only vwap/volume_profile
# the live verdict adds), renormalized to a [-100, 100] scale. Vectorized — full
# pandas Series, no per-bar Python loop over history — and defensive (any failure
# returns an empty Series so Markov simply won't run).
_MK_WEIGHTS = {
    "ema": 20, "adx": 10, "rsi": 10, "macd": 10, "rel_vol": 5,
    "dist52": 5, "rs3m": 10, "rs6m": 10, "sector": 10,
}
_MK_WEIGHT_SUM = sum(_MK_WEIGHTS.values())  # 90
_MK_WARMUP = 50  # null the warmup region before long windows are seeded


def _ema_series(close, span):
    return close.ewm(span=span, adjust=False).mean()


def _rsi_series(close, period=14):
    # Wilder's smoothing (RMA), matching technical.calculate_rsi and the
    # reference implementations (TOS/TradingView/StockCharts): smooth gains and
    # losses with ewm(alpha=1/period, adjust=False) rather than a simple rolling
    # mean. (An SMA seed vs. the ewm's own warmup differs only in the earliest
    # bars, which the reconstruction nulls in its warmup region anyway.)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-4)
    return (100 - (100 / (1 + rs))).where(close.notna())


def _adx_series(daily, period=14):
    """Wilder ADX as a Series (mirrors technical.calculate_adx): TR/+DM/-DM and
    the final ADX are all Wilder-smoothed (ewm alpha=1/period), not simple
    rolling means."""
    high, low, close = daily["high"], daily["low"], daily["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = (((up > down) & (up > 0)) * up.clip(lower=0)).fillna(0.0)
    minus_dm = (((down > up) & (down > 0)) * down.clip(lower=0)).fillna(0.0)
    tr = pd.concat([(high - low), (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    _rma = lambda s: s.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    atr = _rma(tr).replace(0, 1e-4)
    plus_di = 100 * _rma(plus_dm) / atr
    minus_di = 100 * _rma(minus_dm) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-4)
    return _rma(dx)


def _aligned_close(hist, target_index):
    """Datetime-aligned close for a reference history, reindexed onto the target
    (symbol) datetime index with forward-fill; None when unavailable. This makes
    RS line up by DATE, not by integer position (SPY/sector may differ in length
    or start date from the symbol)."""
    if hist is None or getattr(hist, "empty", True) or "datetime" not in hist.columns:
        return None
    s = hist.set_index("datetime")["close"].astype(float)
    return s.reindex(target_index).ffill()


def _rs_score_series(sym_close, ref_close, lookback):
    """Per-bar RS percentile (sym vs ref over ``lookback``) -> score series; neutral
    (0) where the reference is missing or the window isn't filled yet."""
    if ref_close is None:
        return pd.Series(0.0, index=sym_close.index)
    sym_ret = sym_close / sym_close.shift(lookback) - 1
    ref_ret = ref_close / ref_close.shift(lookback) - 1
    pct = (0.5 + (sym_ret - ref_ret) / 0.40).clip(0.0, 1.0).fillna(0.5)
    return pct.map(lambda p: _scoring.score_relative_strength_percentile(float(p)))


def reconstruct_daily_composite(daily, spy, sector_hist):
    """Per-bar daily-only composite score Series (the Markov base score).

    Uses only daily-reconstructable factors (renormalized to 100); returns an
    all-NaN Series when history is too short, and an empty Series on any error
    (Markov simply won't run). Vectorized — no per-bar Python loop over history.
    """
    try:
        if daily is None or len(daily) < 60:
            if daily is None:
                return pd.Series([], dtype=float)
            idx = daily["datetime"] if "datetime" in daily.columns else daily.index
            return pd.Series([np.nan] * len(daily), index=idx)
        d = daily.copy()
        if "datetime" in d.columns:
            d = d.set_index("datetime")
        close = d["close"].astype(float)

        emas = [_ema_series(close, p) for p in (12, 21, 50, 200)]
        above = sum((close > e).astype(float) for e in emas) / len(emas)
        ema_score = (above * 2 - 1) * 100
        slope = np.where(ema_score.to_numpy() >= 0, 1, -1)

        # ADX + EMA-alignment tiers are inlined (vectorized) rather than calling
        # the scalar _scoring primitives; keep the 15/20/25→30/60/100 ADX tiers
        # in sync with scoring.score_adx_directional if that ever changes.
        adx = _adx_series(d)
        adx_score = pd.Series(0.0, index=close.index)
        adx_score = (adx_score.mask(adx >= 15, 30.0)
                              .mask(adx >= 20, 60.0)
                              .mask(adx >= 25, 100.0)) * slope

        rsi = _rsi_series(close)
        rsi_score = rsi.map(lambda v: _scoring.score_rsi(float(v)) if pd.notna(v) else 0)

        macd_line = _ema_series(close, 12) - _ema_series(close, 26)
        macd_hist = macd_line - macd_line.ewm(span=9, adjust=False).mean()
        macd_score = pd.Series(
            [_scoring.score_macd(float(h), float(p)) if pd.notna(h) and pd.notna(p) else 0
             for h, p in zip(macd_hist, macd_hist.shift())], index=close.index)

        vol = d["volume"].astype(float)
        rel = vol / vol.rolling(20).mean().replace(0, 1e-4)
        rel_score = pd.Series(
            [_scoring.score_relative_volume(float(r), s) if pd.notna(r) else 0
             for r, s in zip(rel, slope)], index=close.index)

        roll_high = close.rolling(252, min_periods=20).max()
        dist = (roll_high - close) / roll_high.replace(0, np.nan)
        dist_score = dist.map(
            lambda x: _scoring.score_distance_from_52wk_high(float(x)) if pd.notna(x) else 0)

        spy_close = _aligned_close(spy, close.index)
        sec_close = _aligned_close(sector_hist, close.index)
        rs3 = _rs_score_series(close, spy_close, 63)
        rs6 = _rs_score_series(close, spy_close, 126)
        sec_score = _rs_score_series(sec_close, spy_close, 63) if sec_close is not None \
            else pd.Series(0.0, index=close.index)

        w = _MK_WEIGHTS
        weighted = (ema_score * w["ema"] + adx_score * w["adx"]
                    + rsi_score * w["rsi"] + macd_score * w["macd"]
                    + rel_score * w["rel_vol"] + dist_score * w["dist52"]
                    + rs3 * w["rs3m"] + rs6 * w["rs6m"] + sec_score * w["sector"])
        composite = (weighted / _MK_WEIGHT_SUM).clip(-100, 100)
        # A bar with a missing close is NOT an observation: null it so the Markov
        # chain breaks there (a data hole must never read as a bearish state).
        # (Bars immediately AFTER a hole may be marginally contaminated via the
        # rolling/ewm windows — acceptable: daily equity history is gap-free in
        # practice; the hole bars themselves are correctly excluded.)
        composite = composite.where(close.notna())
        if len(composite) > _MK_WARMUP:
            composite.iloc[:_MK_WARMUP] = np.nan
        return composite
    except Exception:
        _degrade.degraded("trade.reconstruct_daily_composite")
        return pd.Series([], dtype=float)


# ── Markov pooled prior (Task 3.2) ───────────────────────────────────────────
# A universe-wide pooled transition prior is the shrinkage target for each
# symbol's own (often thin) transition matrix. It is rebuilt lazily at most once
# per day and cached in Redis; analyze() reads it via get_prior().
_PRIOR_KEY = "cache:trade:markov_prior"
# A curated, sector-diverse universe for the pooled transition prior. Kept small
# (~17) so the once-daily rebuild is fast; ~500 daily transitions/symbol is ample
# for a 5x5 matrix.
_MK_UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "BAC",
                "UNH", "JNJ", "XOM", "CVX", "HD", "WMT", "CAT", "SPY", "QQQ"]

_BUS = None


def _bus():
    """Lazy module-level Bus singleton (the prior cache is the only thing in
    compute that touches Redis directly; analyze() is on-demand so one shared
    connection is fine)."""
    global _BUS
    if _BUS is None:
        from shared.bus import Bus
        _BUS = Bus()
    return _BUS


def _today_ct_str():
    return date.today().isoformat()


def _symbol_band_series(sym):
    """Daily composite -> band index Series for one universe symbol (or None).

    Self-contained (fetches its own daily/SPY/sector history) so it is trivially
    mockable in tests. Defensive: thin/missing history -> None.
    """
    daily = _price_history(sym, "year", 2, "daily", 1)
    if daily is None or len(daily) < 220:
        return None
    spy = daily if sym == "SPY" else _price_history("SPY", "year", 2, "daily", 1)
    sect = resolve_sector(sym)
    sector_hist = _price_history(sect["etf"], "year", 2, "daily", 1) if sect["etf"] else None
    comp = reconstruct_daily_composite(daily, spy, sector_hist).dropna()
    if comp.empty:
        return None
    return comp.map(_markov.classify_band)


def build_pooled_prior(universe):
    """Sum band transitions across the universe -> (prior matrix, n_symbols)."""
    C = np.zeros((5, 5))
    n = 0
    results = parallel_map(lambda s: (s, _symbol_band_series(s)), list(universe))
    for _sym, bands in results:
        if bands is None or getattr(bands, "empty", True):
            continue
        C += _markov.count_matrix([int(b) for b in bands])
        n += 1
    return _markov.pooled_prior(C), n


def _read_prior_cache():
    try:
        env = _bus().cache_get(_PRIOR_KEY)
        return env.payload if env else None
    except Exception:
        return None


def _write_prior_cache(matrix, n):
    try:
        _bus().cache_set(_PRIOR_KEY, {
            "matrix": [[float(x) for x in row] for row in matrix],
            "date": _today_ct_str(), "n_symbols": int(n)})
    except Exception:
        pass


def get_prior():
    """(prior matrix ndarray, version-string). Lazy: reuse today's cached prior;
    else rebuild from the universe and cache it; uniform fallback on failure."""
    cached = _read_prior_cache()
    if cached and cached.get("date") == _today_ct_str() and cached.get("matrix"):
        return np.array(cached["matrix"], dtype=float), cached["date"]
    try:
        matrix, n = build_pooled_prior(_MK_UNIVERSE)
        _write_prior_cache(matrix, n)
        return matrix, _today_ct_str()
    except Exception:
        return np.full((5, 5), 0.2), "uniform"


# ── Swing-model universe factor snapshot (Phase 3, Task 3.3) ─────────────────
# The validated swing model is scored CROSS-SECTIONALLY (a symbol's factor value
# is z-scored against the same factor across the watchlist), matching how the
# offline calibration was built. That requires a snapshot of every factor's
# current value across a representative universe. Like the Markov prior it is
# rebuilt lazily at most once per day and cached in Redis; analyze() reads it via
# get_universe_snapshot(). Uses the model's fit_universe — the SAME cross-section the
# calibration was built on (~78 names) — falling back to the smaller _MK_UNIVERSE when
# the artifact predates the field. Defensive throughout: any failure yields {} so the
# scorer falls back to the artifact's historical per-factor norm (and analyze() to legacy).
_UNIVERSE_KEY = "cache:trade:universe_factors"


def _symbol_factor_row(sym):
    """Latest per-factor values for one universe symbol -> {factor: value}, or {}.

    Self-contained (fetches its own daily/SPY/sector history) so it is trivially
    mockable in tests. Defensive: thin/missing history -> {}. NaN values are
    dropped (the cross-sectional basis tolerates a ragged set of symbols)."""
    try:
        daily = _price_history(sym, "year", 2, "daily", 1)
        if daily is None or len(daily) < 60:
            return {}
        spy = daily if sym == "SPY" else _price_history("SPY", "year", 2, "daily", 1)
        sect = resolve_sector(sym)
        sector_hist = (_price_history(sect["etf"], "year", 2, "daily", 1)
                       if sect["etf"] else None)
        spy_close = (_factors._close(spy)
                     if spy is not None and not spy.empty else None)
        sec_close = (_factors._close(sector_hist)
                     if sector_hist is not None and not sector_hist.empty else None)
        ff = _factors.compute_factor_frame(daily, spy_close=spy_close,
                                           sector_close=sec_close)
        row = {}
        for c in ff.columns:
            last = ff[c].iloc[-1]
            if pd.notna(last):
                row[c] = float(last)
        return row
    except Exception:
        _degrade.degraded("trade._symbol_factor_row")
        return {}


def _swing_universe():
    """Symbols for the swing cross-section snapshot — the SAME set the model was fit
    on (the artifact's ``fit_universe``), so live z-scores sit on the calibration's
    cross-section. Falls back to the smaller _MK_UNIVERSE when the artifact predates
    the field or is unreadable."""
    try:
        from services.trade_svc import swing_model as _sw  # local: _swing is analyze()-scoped
        u = (_sw.load_artifact() or {}).get("fit_universe")
        if u and len(u) >= 10:
            return list(u)
    except Exception:
        pass
    return list(_MK_UNIVERSE)


def build_universe_factor_snapshot():
    """Assemble {factor: [values across the universe]} from the latest per-symbol
    factor rows (NaN/missing dropped). Fetches concurrently; never raises."""
    snapshot = {}
    try:
        results = parallel_map(lambda s: (s, _symbol_factor_row(s)),
                               _swing_universe())
        for _sym, row in results:
            for factor, value in (row or {}).items():
                if value is not None and np.isfinite(value):
                    snapshot.setdefault(factor, []).append(value)
    except Exception:
        return {}
    return snapshot


def _read_universe_snapshot():
    try:
        env = _bus().cache_get(_UNIVERSE_KEY)
        return env.payload if env else None
    except Exception:
        return None


def _write_universe_snapshot(snapshot):
    try:
        _bus().cache_set(_UNIVERSE_KEY, {
            "factors": {k: [float(x) for x in v] for k, v in snapshot.items()},
            "date": _today_ct_str()})
    except Exception:
        pass


def get_universe_snapshot():
    """{factor: [values]} for today. Lazy: reuse today's cached snapshot; else
    rebuild from the universe and cache it; {} on failure (the scorer then uses
    the artifact's historical norm)."""
    cached = _read_universe_snapshot()
    if cached and cached.get("date") == _today_ct_str() and cached.get("factors"):
        return cached["factors"]
    try:
        snapshot = build_universe_factor_snapshot()
        if snapshot:
            _write_universe_snapshot(snapshot)
        return snapshot
    except Exception:
        return {}


# ── Markov forecast block (Task 3.3) ─────────────────────────────────────────
_MK_HORIZONS = [5, 10, 20]
# Denser near-term horizons for the CHART ONLY. The 5/10/20d forecast converges to
# the (bull-leaning) prior stationary, so the chart looked identical for every
# symbol; the near-term transient is the score-specific part. This does NOT feed the
# tilt (still horizon _MK_DRIFT_HORIZON) or the metric cards (still _MK_HORIZONS).
_MK_TRAJECTORY_HORIZONS = [1, 2, 3, 5, 10, 20]
_MK_DRIFT_HORIZON = 10
_MK_ALPHA = 30.0
_MK_K = 0.5
_MK_MAX_PTS = 12.0


# CURRENTLY UNUSED — analyze() no longer builds a Markov block (the Trade page's
# Markov Forecast card was removed). Kept (with reconstruct_daily_composite /
# _symbol_band_series / build_pooled_prior / get_prior and the pure engine) tested
# and revivable rather than deleted.
def build_markov_block(band_series, composite_daily_now, composite_full):
    """Markov forecast + tilt for one symbol from its band-index series, or None
    if it can't be built (too few observations / any error)."""
    try:
        bands = band_series.dropna()
        if bands.empty or len(bands) < 30:
            return None
        prior, version = get_prior()
        C_sym = _markov.count_matrix([int(b) for b in bands])
        P = _markov.shrink(C_sym, prior, alpha=_MK_ALPHA)
        current = _markov.classify_band(composite_daily_now)
        fc = _markov.forecast(P, current, _MK_HORIZONS)
        traj = _markov.forecast(P, current, _MK_TRAJECTORY_HORIZONS)["horizons"]
        conf = _markov.row_confidence(C_sym[current])
        tilt = _markov.drift_tilt(fc, composite_daily_now, _MK_DRIFT_HORIZON,
                                  k=_MK_K, max_pts=_MK_MAX_PTS, confidence=conf)
        h = next((x for x in fc["horizons"] if x["n"] == _MK_DRIFT_HORIZON), None)
        drift = float(h["e_score"] - composite_daily_now) if h else 0.0
        return {
            "current_band": current,
            "band_labels": _markov.BAND_LABELS,
            "transition_row": fc["transition_row"],
            "persistence": fc["persistence"],
            "stationary": fc["stationary"],
            "horizons": fc["horizons"],
            "trajectory": [{"n": h["n"], "dist": h["dist"]} for h in traj],
            "drift": drift,
            "tilt": float(tilt),
            "confidence": float(conf),
            "composite_daily": float(composite_daily_now),
            "markov_adjusted_score": float(np.clip(composite_full + tilt, -100, 100)),
            "prior_version": version,
        }
    except Exception:
        _degrade.degraded("trade.build_markov_block")
        return None


# ── short interest: FINRA numerator, Schwab denominator ─────────────────────
# Schwab ships shortIntToFloat/shortIntDayToCover as a 0.0 sentinel for every
# symbol, so parse_schwab_fundamentals maps them to None and the real values
# are joined in here. Schwab still supplies the FLOAT (``marketCapFloat`` is
# float in SHARES despite the name). See services/trade_svc/short_interest.py.
_SI_REFRESH_DAY = [None]   # one refresh attempt per calendar day, at most


def _short_interest_db_path():
    """Isolated so tests can point the enrichment at a tmp store."""
    from services.trade_svc import short_interest as _si
    return _si.DEFAULT_DB_PATH


def _refresh_short_interest(conn):
    """At most one FINRA cycle check per day. FINRA publishes bi-monthly, so
    this is a no-op on all but ~24 days a year; the daily probe is one cheap
    request that usually finds the store already current."""
    today = _today_ct_str()
    if _SI_REFRESH_DAY[0] == today:
        return
    _SI_REFRESH_DAY[0] = today
    try:
        from services.trade_svc import short_interest as _si
        _si.refresh(conn)
    except Exception:
        _degrade.degraded("trade.refresh_short_interest")


def _enrich_short_interest(fundamentals, symbol, float_shares):
    """Fill the two short-interest fields from FINRA, in place.

    Never raises and never falls back to Schwab's 0.0 — a symbol FINRA does
    not carry (a rename, most often) leaves both fields None, because a
    sentinel reinstated here would silently disable the squeeze gate again."""
    conn = None
    try:
        from services.trade_svc import short_interest as _si
        path = _short_interest_db_path()
        # Same isolation rule the journal and PIT stores carry, and this one
        # matters more: unguarded it opened a SQLite file inside the repo AND
        # pulled a live FINRA cycle — measured, 22,341 rows downloaded during a
        # single suite run. A test that wants the join patches the path, which
        # opts back in.
        if _under_pytest() and path == _si.DEFAULT_DB_PATH:
            return fundamentals
        conn = _si.init_db(path)
        _refresh_short_interest(conn)
        got = _si.for_symbol(conn, symbol, float_shares)
        if got:
            fundamentals.short_int_to_float = got["pct_of_float"]
            fundamentals.short_int_day_to_cover = got["days_to_cover"]
    except Exception:
        _degrade.degraded("trade.enrich_short_interest")
    finally:
        if conn is not None:
            try:
                from services.trade_svc import short_interest as _si2
                _si2.close_db(conn)
            except Exception:
                pass
    return fundamentals


def _fetch_fundamentals(symbol):
    """Fetch + parse Schwab fundamentals for ``symbol`` → ``Fundamentals``.

    Defensive: a proxy/parse failure returns an empty ``Fundamentals`` (so the
    Investor verdict degrades to insufficient-data HOLD) rather than raising.
    Short interest is joined in from FINRA afterwards — see
    :func:`_enrich_short_interest`.
    """
    try:
        raw = _proxy.schwab_client.get_fundamentals(symbol)
    except Exception:
        raw = None
    if not raw:
        return Fundamentals()
    try:
        f = parse_schwab_fundamentals({"fundamental": raw},
                                      as_of=date.today().isoformat())
    except Exception:
        return Fundamentals()
    return _enrich_short_interest(f, symbol, raw.get("marketCapFloat"))


# ── sector P/E median (Phase 1) ──────────────────────────────────────────────
# The Investor verdict's ``valuation`` component is the mean of {P/E vs the
# sector median, PEG}. ``analyze`` passed ``sector_pe_median=None``
# unconditionally, so ``score_pe_vs_sector`` returned its missing-input 0 for
# every symbol — and averaging that structural 0 in HALVED the surviving PEG
# score. The median is computed from the peers ``_SYMBOL_SECTOR`` already names.
#
# Memoized per (sector, day): a sector's median P/E moves on earnings, not on
# the minute, so one fan-out per sector per day is the right cadence — without
# it every analysis would re-fetch a dozen peers.
_SECTOR_PE_CACHE = {}


def reset_sector_pe_cache():
    """Drop the memo (tests, and anything that needs a forced refetch)."""
    _SECTOR_PE_CACHE.clear()


def _sector_peers(sector_name):
    return [s for s, (name, _etf) in _SYMBOL_SECTOR.items() if name == sector_name]


def sector_pe_median(symbol):
    """Median trailing P/E across ``symbol``'s sector peers, or None.

    None means "no basis to compare against" — the Investor verdict then scores
    valuation on PEG alone rather than averaging in a structural zero. Only
    POSITIVE P/Es count: a loss-making peer reports a negative ratio, which is
    not a valuation the median should be dragged by, and a peer with no
    fundamentals contributes nothing rather than a zero. Never raises.
    """
    sect = resolve_sector(symbol)
    name = sect.get("name")
    if not name:
        return None
    today = _today_ct_str()
    hit = _SECTOR_PE_CACHE.get(name)
    if hit and hit[0] == today:
        return hit[1]
    try:
        peers = _sector_peers(name)
        results = parallel_map(_fetch_fundamentals, peers)
        pes = [f.pe_ratio for f in results
               if f is not None and f.pe_ratio is not None and f.pe_ratio > 0]
        median = float(np.median(pes)) if pes else None
    except Exception:
        _degrade.degraded("trade.sector_pe_median")
        return None
    _SECTOR_PE_CACHE[name] = (today, median)
    return median


def _fundamentals_dict(f):
    """JSON-safe view of the fundamentals the page surfaces on the Investor card."""
    return {
        "pe_ratio": f.pe_ratio,
        "peg_ratio": f.peg_ratio,
        "rev_growth_ttm": f.rev_growth_ttm,
        "eps_growth_ttm": f.eps_growth_ttm,
        "roe": f.roe,
        "margin_expanding": f.margin_expanding,
        "days_to_earnings": f.days_to_earnings,
    }


def _sector_strength_dict(ss):
    return {
        "score": ss.score,
        "in_confirmed_downtrend": ss.in_confirmed_downtrend,
        "sector_above_50ema": ss.sector_above_50ema,
        "rs_3m_percentile": ss.rs_3m_percentile,
    }


def _under_pytest():
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def journal_reading(result, db_path=None):
    """Append one analysis to the recommendation journal. Side effect only.

    Returns True when the row was written, False otherwise — and NEVER raises:
    ``analyze`` owes the user an analysis whether or not the journal took the
    row, exactly as the IV/RV store behaves.

    ⚠ With no explicit ``db_path`` the write is SKIPPED under pytest. The bus
    is fakeredis but SQLite is not, and this repo has a documented incident
    where a suite wrote into live data; ``analyze`` is exercised by many tests
    that know nothing about this store. Tests that want the mapping pass their
    own tmp path, which bypasses the guard.
    """
    if db_path is None and _under_pytest():
        return False
    conn = None
    try:
        from services.trade_svc import rec_journal
        sm = (result or {}).get("swing_model") or {}
        pv = (result or {}).get("position_verdict") or {}
        iv = (result or {}).get("investor_verdict") or {}
        row = {
            "symbol": (result or {}).get("symbol"),
            "reading_date": _today_ct_str(),
            "price": (result or {}).get("price"),
            "composite": sm.get("score"),
            "band": sm.get("band"),
            "percentile": sm.get("percentile"),
            "swing_verdict": sm.get("verdict"),
            "position_verdict": pv.get("verdict"),
            "investor_verdict": iv.get("verdict"),
            "investor_score": iv.get("score"),
            "gates": "; ".join(pv.get("gates_triggered") or []),
            "model_version": sm.get("model_version"),
        }
        conn = rec_journal.init_db(db_path or rec_journal.DEFAULT_DB_PATH)
        return rec_journal.record(conn, row)
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                from services.trade_svc import rec_journal as _rj
                _rj.close_db(conn)
            except Exception:
                pass


def snapshot_fundamentals(symbol, fundamentals, sector_name, pe_median,
                          db_path=None):
    """Append today's fundamental INPUTS to the point-in-time store.

    Side effect only; same contract and same pytest guard as
    :func:`journal_reading` — see that docstring for why the guard exists."""
    if db_path is None and _under_pytest():
        return False
    conn = None
    try:
        from services.trade_svc import fundamentals_history as _fh
        f = fundamentals
        row = {
            "symbol": symbol, "snapshot_date": _today_ct_str(),
            "pe_ratio": f.pe_ratio, "peg_ratio": f.peg_ratio,
            "rev_growth_ttm": f.rev_growth_ttm,
            "eps_growth_ttm": f.eps_growth_ttm, "roe": f.roe,
            "margin_expanding": f.margin_expanding, "fcf": f.fcf,
            "short_int_to_float": f.short_int_to_float,
            "short_int_day_to_cover": f.short_int_day_to_cover,
            "days_to_earnings": f.days_to_earnings,
            "sector": sector_name, "sector_pe_median": pe_median,
        }
        conn = _fh.init_db(db_path or _fh.DEFAULT_DB_PATH)
        return _fh.record(conn, row)
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                from services.trade_svc import fundamentals_history as _fh2
                _fh2.close_db(conn)
            except Exception:
                pass


def analyze(symbol):
    """Analyze ``symbol`` end-to-end → a JSON-safe result dict (or None).

    Returns None only for an empty symbol. A data failure (no quote, thin
    history) returns a dict carrying ``errors`` and whatever was computed, never
    raises — the GUI shows the partial/empty state.
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return None

    try:
        quote = _proxy.schwab_client.get_quote(symbol)
    except Exception:
        quote = None
    if not quote or not quote.get("last"):
        return {"symbol": symbol, "errors": ["No quote / price for symbol"],
                "timestamp": _now_iso()}
    price = float(quote["last"])

    data = _fetch_timeframes(symbol)
    daily = data.get("daily")
    if daily is None or len(daily) < 50:
        return {"symbol": symbol, "price": price,
                "errors": ["Insufficient daily history for analysis"],
                "timestamp": _now_iso()}

    # SPY history, the symbol's sector-ETF history, and fundamentals are three
    # independent, I/O-bound proxy fetches — run them concurrently rather than
    # serially. ``resolve_sector`` is a local lookup (no I/O). Each underlying
    # fetch is already defensive (degrades to None / empty Fundamentals).
    sect = resolve_sector(symbol)
    spy, sector_hist, fundamentals = parallel_map(lambda fn: fn(), [
        lambda: _price_history("SPY", "year", 2, "daily", 1),
        lambda: _price_history(sect["etf"], "year", 2, "daily", 1) if sect["etf"] else None,
        lambda: _fetch_fundamentals(symbol),
    ])

    # Multi-timeframe EMA alignment (weighted across the fetched timeframes).
    align = technical.calculate_ema_alignment(data, price)
    alignment_pct = float(align.get("alignment_percentage", 0.0))

    # Momentum/volume technicals on the 5-min series (fall back to daily).
    intraday = data.get("5min")
    if intraday is None or len(intraday) < 20:
        intraday = daily
    rsi = float(technical.calculate_rsi(intraday))
    adx = float(technical.calculate_adx(intraday))
    vwap = technical.calculate_vwap(intraday)
    rel_vol, today_vol = technical.calculate_relative_volume(intraday)
    vp = technical.calculate_volume_profile(intraday)

    # MACD on daily — compute the histogram series ONCE and read the last two
    # values (was two calculate_macd calls = four EMA passes; now two).
    macd_series = technical.macd_histogram_series(daily)
    if macd_series is None:
        macd_hist = macd_prev = 0.0
    else:
        last = macd_series.iloc[-1]
        macd_hist = float(last) if not pd.isna(last) else 0.0
        if len(macd_series) > 1:
            prev = macd_series.iloc[-2]
            macd_prev = float(prev) if not pd.isna(prev) else 0.0
        else:
            macd_prev = macd_hist

    # Sector strength from the symbol's sector ETF vs SPY (neutral if unknown);
    # sector_hist + fundamentals were fetched concurrently above.
    if sector_hist is not None and spy is not None and not spy.empty:
        ss = compute_sector_strength(sector_hist, spy)
    else:
        ss = _neutral_sector_strength()

    spy_for_pos = spy if spy is not None and not spy.empty else daily
    vwap_val = float(vwap) if vwap else float(daily["close"].iloc[-1])
    pos_inputs = PositionInputs(
        daily=daily, hourly=data.get("60min") if data.get("60min") is not None else daily,
        spy_history=spy_for_pos, ema_alignment_pct=alignment_pct,
        rsi=rsi, adx=adx, macd_hist=macd_hist, macd_hist_prev=macd_prev,
        relative_volume=float(rel_vol), vwap=vwap_val, volume_profile=vp,
        sector_strength=ss, days_to_earnings=fundamentals.days_to_earnings,
    )
    try:
        position_verdict = PositionVerdict().score(pos_inputs)
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the service.
        position_verdict = {"verdict": "HOLD", "score": 0, "breakdown": [],
                            "top_reasons": [], "gates_triggered": [f"error: {exc}"]}

    # (The Markov 2.0 forecast + drift tilt used to be built here. It is no longer
    # computed: the Trade page's Markov card was REMOVED, so the block had no
    # reader — and building it cost a pooled-prior rebuild on every request. The
    # engine + helpers below remain, so reviving the card is a re-wire, not a
    # rewrite.)

    # Validated swing-model verdict (Phase 3): score this symbol's current factors
    # cross-sectionally against today's cached universe snapshot (falling back to
    # the artifact's historical per-factor norm), then read the BUY/HOLD/SELL
    # verdict off the calibration band. Fully defensive: any failure leaves
    # swing_block None and the existing verdict/markov untouched.
    swing_block = None
    try:
        from services.trade_svc import swing_model as _swing
        _art = _swing.load_artifact()
        if _art:
            _spy_close = _factors._close(spy) if spy is not None and not spy.empty else None
            _sec_close = (_factors._close(sector_hist)
                          if sector_hist is not None and not sector_hist.empty else None)
            _ff = _factors.compute_factor_frame(daily, spy_close=_spy_close,
                                                sector_close=_sec_close)
            _cur = {c: (float(_ff[c].iloc[-1]) if _ff[c].notna().iloc[-1] else None)
                    for c in _ff.columns}
            _snap = get_universe_snapshot()
            swing_block = _swing.score_symbol(_cur, _snap, _art)
    except Exception:
        swing_block = None

    sym_close = daily["close"]
    spy_close = spy["close"] if spy is not None and not spy.empty else None
    sect_close = sector_hist["close"] if sector_hist is not None else None
    pe_median = sector_pe_median(symbol)
    # Point-in-time record of the INPUTS (not the score) — the only path to
    # ever validating the Investor weights. Side effect only; never raises.
    snapshot_fundamentals(symbol, fundamentals, sect["name"], pe_median)
    inv_inputs = InvestorInputs(
        fundamentals=fundamentals, sector_pe_median=pe_median,
        rs_vs_spy_3m=rs_percentile(sym_close, spy_close, 63),
        rs_vs_spy_6m=rs_percentile(sym_close, spy_close, 126),
        rs_vs_spy_12m=rs_percentile(sym_close, spy_close, 252),
        rs_vs_sector_3m=rs_percentile(sym_close, sect_close, 63),
        rs_vs_sector_6m=rs_percentile(sym_close, sect_close, 126),
        rs_vs_sector_12m=rs_percentile(sym_close, sect_close, 252),
        sector_strength=ss,
    )
    try:
        investor_verdict = InvestorVerdict().score(inv_inputs)
    except Exception as exc:  # noqa: BLE001
        investor_verdict = {"verdict": "HOLD", "score": 0, "breakdown": [],
                            "top_reasons": [], "gates_triggered": [f"error: {exc}"]}

    result = {
        "symbol": symbol,
        "description": quote.get("symbol", symbol),
        "price": price,
        "volume": int(today_vol or quote.get("volume") or 0),
        "bias": align.get("bias", "NEUTRAL"),
        "ema_alignment": align,
        "momentum": {
            "rsi": rsi, "adx": adx, "macd_hist": macd_hist,
            "macd_hist_prev": macd_prev,
            "vwap": float(vwap) if vwap else None,
            "relative_volume": float(rel_vol),
        },
        "volume_profile": vp,
        "sector": {"etf": sect["etf"], "name": sect["name"],
                   "strength": _sector_strength_dict(ss)},
        "position_verdict": position_verdict,
        "investor_verdict": investor_verdict,
        "swing_model": swing_block,
        "fundamentals": _fundamentals_dict(fundamentals),
        "fundamentals_available": fundamentals.is_sufficient(),
        "timestamp": _now_iso(),
        "errors": [],
    }
    # Forward-accruing record of what the model said today. Side effect only —
    # never raises, and skipped under pytest (see journal_reading).
    journal_reading(result)
    return result


# ── EquityDeepDive (migrated) — on-demand quant deep dive + chat-prompt query ──
# The engine + IV/RV store live in ``services/trade_svc/deepdive``; these thin
# wrappers run one symbol through it and hand back a JSON-safe {html|markdown, …}
# payload for the handlers to cache. No Anthropic API calls (the AI *note* is a
# generated query, not an API result). All defensive — never raise.
_DEEPDIVE_ARGS = dict(years=1, no_options=False, strikes=40,
                      from_date=None, to_date=None, lookback=252)


def _open_iv_conn():
    """Open (creating if needed) the IV/RV history SQLite store. Isolated so tests
    can stub it away."""
    from repo_paths import IV_HISTORY_DB
    from services.trade_svc.deepdive import iv_history as ivh
    IV_HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    return ivh.init_db(IV_HISTORY_DB)


def _deep_dive_result(symbol):
    """Run the migrated EquityDeepDive engine for one symbol → (result dict | None,
    normalized symbol). Records an IV/RV snapshot as a side effect. Never raises."""
    from types import SimpleNamespace

    from services.trade_svc.deepdive import engine
    from services.trade_svc.deepdive import iv_history as ivh
    symbol = engine.normalize_symbol((symbol or "").strip().upper())
    if not symbol:
        return None, "?"
    args = SimpleNamespace(**_DEEPDIVE_ARGS)
    conn = None
    try:
        client = engine.SchwabClient()  # proxy mode (repo_paths.PROXY_URL)
        conn = _open_iv_conn()
        result = engine.analyze_symbol(client, symbol, args, conn)
    except Exception:
        result = None
    finally:
        if conn is not None:
            try:
                ivh.close_db(conn)
            except Exception:
                pass
    return result, symbol


def run_deep_dive(symbol):
    """→ ``{'symbol','html','ts'}``. Renders the deep-dive HTML report (or a
    friendly error page). Never raises."""
    from services.trade_svc.deepdive import engine
    result, sym = _deep_dive_result(symbol)
    if not result:
        html = (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<title>Deep Dive — {sym}</title></head>"
                f"<body style='font-family:system-ui;background:#0c0f15;color:#e9edf3;padding:40px'>"
                f"<h3>Could not run a deep dive for {sym}</h3>"
                f"<p>Check the symbol, and that the Schwab proxy (:8100) is up and its "
                f"token is valid (visit <code>http://127.0.0.1:8100/auth</code>).</p>"
                f"</body></html>")
        return {"symbol": sym, "html": html, "ts": _now_iso()}
    try:
        html = engine.render_html(
            result["symbol"], result["quote"], result["technicals"],
            result["fundamentals"], result["options"], result["takeaways"],
            result["ranks"])
    except Exception as exc:
        html = f"<html><body><h3>Deep Dive render failed for {sym}: {exc}</h3></body></html>"
    return {"symbol": sym, "html": html, "ts": _now_iso()}


def build_deep_dive_query(symbol):
    """→ ``{'symbol','markdown','ts'}``. Builds the chat prompt (digest injected,
    HOW-TO stripped) for the user to paste into a chat. NO API call. Never raises."""
    from services.trade_svc.deepdive import chat_prompt
    result, sym = _deep_dive_result(symbol)
    if not result:
        return {"symbol": sym, "ts": _now_iso(),
                "markdown": f"Could not build a query for {sym}. Is the proxy up?"}
    try:
        template_text = chat_prompt.find_template().read_text(encoding="utf-8")
        md = chat_prompt.build_prompt(result, template_text)
    except Exception as exc:
        md = f"Query build failed for {sym}: {exc}"
    return {"symbol": sym, "markdown": md, "ts": _now_iso()}
