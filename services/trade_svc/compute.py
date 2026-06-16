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

**Fundamentals are not wired (MVP).** This repo has no fundamentals feed (the
Schwab proxy exposes none; ``finvizfinance`` is not installed), so an empty
``Fundamentals`` is passed and ``InvestorVerdict`` degrades to an
"Insufficient fundamental data" HOLD. Wiring a feed (a proxy ``/instruments``
fundamentals endpoint + ``parse_schwab_fundamentals``) is a clean follow-up.

Every engine-call function is defensive: per the page/service convention it
catches and degrades (returns ``None`` / an ``errors`` payload) rather than
raising, so one bad symbol can never crash the service.
"""
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from repo_paths import TRADE_ANALYZER
from services import _proxy

# ── isolated engine imports (separate process — no cross-app name collision) ──
_ANALYSIS_LIB = TRADE_ANALYZER.parent / "shared" / "analysis_lib"
if str(_ANALYSIS_LIB) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS_LIB))
if str(TRADE_ANALYZER) not in sys.path:
    sys.path.insert(0, str(TRADE_ANALYZER))

import technical  # noqa: E402  (shared indicator lib, imported standalone to dodge the package __init__)
from src.analysis.fundamentals import Fundamentals  # noqa: E402
from src.analysis.recommendation import (  # noqa: E402
    InvestorInputs, InvestorVerdict, PositionInputs, PositionVerdict)
from src.analysis.sector_strength import (  # noqa: E402
    SectorStrength, compute_sector_strength)

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
    "daily": ("year", 1, "daily", 1),
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
    """Fetch every configured timeframe; skip any that come back empty."""
    out = {}
    for tf, (pt, p, ft, f) in _TIMEFRAME_PARAMS.items():
        df = _price_history(symbol, pt, p, ft, f)
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


def _sector_strength_dict(ss):
    return {
        "score": ss.score,
        "in_confirmed_downtrend": ss.in_confirmed_downtrend,
        "sector_above_50ema": ss.sector_above_50ema,
        "rs_3m_percentile": ss.rs_3m_percentile,
    }


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

    spy = _price_history("SPY", "year", 1, "daily", 1)

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

    # MACD on daily; "prev" histogram = MACD on the series minus the last bar.
    macd_hist = float(technical.calculate_macd(daily).get("histogram", 0.0))
    if len(daily) > 1:
        macd_prev = float(technical.calculate_macd(daily.iloc[:-1]).get("histogram", 0.0))
    else:
        macd_prev = macd_hist

    # Sector strength from the symbol's sector ETF vs SPY (neutral if unknown).
    sect = resolve_sector(symbol)
    sector_hist = None
    if sect["etf"]:
        sector_hist = _price_history(sect["etf"], "year", 1, "daily", 1)
    if sector_hist is not None and spy is not None and not spy.empty:
        ss = compute_sector_strength(sector_hist, spy)
    else:
        ss = _neutral_sector_strength()

    # Fundamentals not wired (MVP) -> InvestorVerdict degrades to HOLD.
    fundamentals = Fundamentals()

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

    sym_close = daily["close"]
    spy_close = spy["close"] if spy is not None and not spy.empty else None
    sect_close = sector_hist["close"] if sector_hist is not None else None
    inv_inputs = InvestorInputs(
        fundamentals=fundamentals, sector_pe_median=None,
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

    return {
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
        "fundamentals_available": False,
        "timestamp": _now_iso(),
        "errors": [],
    }
