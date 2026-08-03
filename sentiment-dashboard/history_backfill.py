"""Rolling 30-day sentiment history backfill.

Replays the live scoring path against Schwab daily history so the
History tab and sparkline have meaningful data the first time the
dashboard opens. Pure-ish — the only side effect is the Schwab API
calls performed via the injected client. No tk imports.
"""
from __future__ import annotations

import logging
import sys
import pathlib
from datetime import datetime
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

from scoring import WEIGHTS
from scoring import vix as vix_score
from scoring import put_call as pc_score
from scoring import breadth as breadth_score
from scoring import rotation as rotation_score
from scoring import sector_perf as sector_score
from scoring import credit_pulse as credit_score
from scoring.composite import blend


logger = logging.getLogger(__name__)


VIX_SYMBOL_CANDIDATES = ("$VIX", "VIX", "$VIX.X")
VIX1D_SYMBOL_CANDIDATES = ("$VIX1D",)
VIX9D_SYMBOL_CANDIDATES = ("$VIX9D",)
CPCE_SYMBOL_CANDIDATES = ("$CPCE",)
SPXA50R_SYMBOL_CANDIDATES = ("$SPXA50R", "$NYA50R", "$MMFI")
ADVN_CANDIDATES = ("$ADVN",)
DECN_CANDIDATES = ("$DECN",)
NYHGH_CANDIDATES = ("$NYHGH", "$NYHGH.X", "$NEWH")
NYLOW_CANDIDATES = ("$NYLOW", "$NYLOW.X", "$NEWL")

CREDIT_LOOKBACK = 60  # bars credit_pulse needs ending at date d


def _bias_from_score(s: float) -> str:
    if s >= 7.0:
        return "Bullish"
    if s >= 5.5:
        return "Mild Bullish"
    if s >= 4.5:
        return "Neutral"
    if s >= 3.0:
        return "Mild Bearish"
    return "Bearish"


def _size_modifier(s: float) -> str:
    if s >= 7.5:
        return "1.25x"
    if s >= 6.5:
        return "1.10x"
    if s >= 5.0:
        return "1.00x"
    if s >= 3.5:
        return "0.75x"
    return "0.50x"


def _fetch_history(schwab, candidates, months: int = 6):
    """Try each candidate symbol until one returns a usable DataFrame."""
    for sym in candidates:
        try:
            df = schwab.get_daily_history(sym, months=months)
        except Exception as e:
            logger.debug("history %s: %s", sym, e)
            df = None
        if df is not None and len(df) >= 2:
            logger.info("history %s: %d bars", sym, len(df))
            return sym, df
    logger.warning("history: no symbol returned data from %s", candidates)
    return None, None


def _date_indexed(df):
    """Return df with a `date` (python date) column added and indexed by
    that date, dropping duplicates (keep last). None-safe."""
    if df is None:
        return None
    out = df.copy()
    out["date"] = out["datetime"].dt.date
    out = out.drop_duplicates(subset="date", keep="last")
    out = out.set_index("date").sort_index()
    return out


def _close_at(df, d):
    if df is None or d not in df.index:
        return None
    v = df.loc[d, "close"]
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _closes_up_to(df, d, n):
    """Return list of `n` closes ending at d inclusive, or None if not enough."""
    if df is None or d not in df.index:
        return None
    pos = df.index.get_loc(d)
    if pos + 1 < n:
        return None
    closes = df["close"].iloc[pos + 1 - n: pos + 1].tolist()
    return [float(c) for c in closes if c is not None]


def _pct_change_at(df, d, n):
    """% change from n sessions before d to d, or None."""
    if df is None or d not in df.index:
        return None
    pos = df.index.get_loc(d)
    if pos < n:
        return None
    prev = float(df["close"].iloc[pos - n])
    last = float(df["close"].iloc[pos])
    if prev == 0:
        return None
    return (last - prev) / prev * 100.0


def _ratio_score(advn, decn):
    if advn is None and decn is None:
        return None
    a = advn or 0
    d = decn or 0
    if d > 0:
        return a / d
    if a > 0:
        return float("inf")
    return None


def _score_one_day(d, frames, sector_data, min_components: int = 4):
    """Build a complete history snapshot for trading date `d`. Returns
    None when the day does not meet the component-quality threshold
    (`min_components` components must produce a score > 0). Anchor
    inputs (VIX close, sector ETF day data) are always required."""
    vix = _close_at(frames["vix"], d)
    if vix is None:
        return None
    vix_closes = _closes_up_to(frames["vix"], d, 10)
    vix_ma = sum(vix_closes) / len(vix_closes) if vix_closes else 0.0

    v1d = _close_at(frames["vix1d"], d) or 0.0
    v9d = _close_at(frames["vix9d"], d) or 0.0

    v1d_prior = 0.0
    if frames["vix1d"] is not None and d in frames["vix1d"].index:
        pos = frames["vix1d"].index.get_loc(d)
        if pos >= 1:
            try:
                v1d_prior = float(frames["vix1d"]["close"].iloc[pos - 1])
            except (TypeError, ValueError):
                v1d_prior = 0.0

    term = vix_score.score_term(vix, vix_ma)
    v1d_r = vix_score.score_vix1d(v1d, vix, v1d_prior)
    slope = vix_score.score_term_slope(v9d, vix)
    vix_complex = vix_score.score_complex(term, v1d_r, slope)

    # --- Put/Call -----------------------------------------------------
    cpce = _close_at(frames["cpce"], d)
    if cpce is not None:
        cpce_window = _closes_up_to(frames["cpce"], d, 10) or []
        cpce_ma = (
            sum(cpce_window) / len(cpce_window)) if cpce_window else 0.0
        pc_res = pc_score.score(pc_equity=cpce, pc_ma=cpce_ma)
    else:
        from scoring.types import ScoreResult
        cpce = 0.0
        cpce_ma = 0.0
        pc_res = ScoreResult(score=0, confidence=0.0, interp="")

    # --- Breadth ------------------------------------------------------
    advn = _close_at(frames["advn"], d)
    decn = _close_at(frames["decn"], d)
    highs = _close_at(frames["nyhgh"], d) or 0
    lows = _close_at(frames["nylow"], d) or 0
    pct50 = _close_at(frames["spxa50r"], d)
    ratio = _ratio_score(advn, decn)
    net = ((advn or 0) - (decn or 0)) if (advn or decn) else None
    pct50_str = f"{pct50:.1f}" if pct50 is not None else ""
    br_res = breadth_score.score(
        pct_above_50=pct50_str,
        nyse_ad="Neutral",
        new_highs=float(highs),
        new_lows=float(lows),
        breadth_ratio=ratio,
        breadth_numeric=net,
    )

    # --- Rotation + Sector Perf --------------------------------------
    last_quotes: Dict[str, dict] = {}
    sector_trends: Dict[str, dict] = {}
    for row in sector_data:
        if row.get("kind") != "sector":
            continue
        etf = row.get("etf")
        df = frames["sectors"].get(etf) if etf else None
        if df is None or d not in df.index:
            continue
        day_pct = _pct_change_at(df, d, 1)
        d3_pct = _pct_change_at(df, d, 3)
        wk_pct = _pct_change_at(df, d, 5)
        if day_pct is not None:
            last_quotes[etf] = {"change_pct": day_pct}
        if d3_pct is not None or wk_pct is not None:
            sector_trends[etf] = {"day3_pct": d3_pct, "week_pct": wk_pct}
    rot = rotation_score.compute_rotation(
        sector_data, sector_trends, last_quotes)
    if not rot:
        # Anchor: rotation needs at least one sector with a day_pct.
        return None
    rot_score = float(rot.get("score", 0) or 0)
    rot_conf = float(rot.get("confidence", 0.0) or 0.0)

    sec_score = sector_score.sectors_score(sector_data, last_quotes)
    if sec_score <= 0:
        # Anchor: sector perf is the largest weight (25%); if it can't
        # be computed at all, the day isn't useful.
        return None
    n_sectors = sum(
        1 for r in sector_data
        if r.get("kind") == "sector" and r.get("etf") in last_quotes)
    sec_conf = (n_sectors / 11.0) ** 0.5

    # --- Credit Pulse -------------------------------------------------
    hyg_window = _closes_up_to(frames["hyg"], d, CREDIT_LOOKBACK)
    iei_window = _closes_up_to(frames["iei"], d, CREDIT_LOOKBACK)
    if hyg_window is not None and iei_window is not None:
        credit_hist = {
            "hyg_closes": hyg_window, "iei_closes": iei_window}
        cr_res = credit_score.score(credit_hist)
    else:
        from scoring.types import ScoreResult
        cr_res = ScoreResult(score=0, confidence=0.0, interp="")

    # --- Quality gate -------------------------------------------------
    scored_components = sum(1 for s in (
        vix_complex.score, pc_res.score, br_res.score,
        rot_score, sec_score, cr_res.score) if s > 0)
    if scored_components < min_components:
        return None

    # --- Composite ----------------------------------------------------
    scores = {
        "vix_complex":  float(vix_complex.score),
        "put_call":     float(pc_res.score),
        "breadth":      float(br_res.score),
        "rotation":     rot_score,
        "sector_perf":  float(sec_score),
        "credit_pulse": float(cr_res.score),
    }
    confs = {
        "vix_complex":  float(vix_complex.confidence),
        "put_call":     float(pc_res.confidence),
        "breadth":      float(br_res.confidence),
        "rotation":     rot_conf,
        "sector_perf":  sec_conf,
        "credit_pulse": float(cr_res.confidence),
    }
    composite, agg_conf = blend(scores, confs, WEIGHTS)

    date_str = d.isoformat()
    return {
        "date": date_str,
        "source": "backfill",
        "saved_at": datetime.now().isoformat(),
        "volatility": {
            "vix_level": f"{vix:.2f}",
            "vix_ma": f"{vix_ma:.2f}" if vix_ma else "",
            "vix_term": "Flat",
            "vvix_level": "",
            "score": str(int(vix_complex.score)),
            "interpretation": vix_complex.interp,
        },
        "options": {
            "pc_equity": f"{cpce:.3f}",
            "pc_ma": f"{cpce_ma:.3f}" if cpce_ma else "",
            "pc_index": "",
            "skew": "Normal",
            "score": str(int(pc_res.score)),
            "interpretation": pc_res.interp,
        },
        "rotation": {
            "xly_xlp": "Neutral",
            "smh_spy": "Neutral",
            "iwm_spy": "Neutral",
            "qqq_spy": "Neutral",
            "score": str(int(rot_score)),
            "interpretation": (f"Day {rot.get('day_score')} · "
                               f"3d {rot.get('3d_score')} · "
                               f"Wk {rot.get('week_score')}"),
        },
        "breadth": {
            "pct_above_50": pct50_str,
            "nyse_ad": "Neutral",
            "new_highs": float(highs),
            "new_lows": float(lows),
            "score": str(int(br_res.score)),
            "interpretation": br_res.interp,
        },
        "flow": {
            "hyg_lqd": "Neutral",
            "tlt_trend": "Flat",
            "usd_trend": "Flat",
            "score": "0",
            "interpretation": "",
        },
        "composite": {
            "total_score": f"{composite:.2f}",
            "size_modifier": _size_modifier(composite),
            "bias": _bias_from_score(composite),
            "contrarian": "No",
            "yesterday_score": "",
            "score_change": "—",
            "aggregate_confidence": round(agg_conf, 3),
        },
        "interpretation": {
            "main": "",
            "watch_for": "",
        },
        "component_scores": {
            "vix_complex":  scores["vix_complex"],
            "put_call":     scores["put_call"],
            "breadth":      scores["breadth"],
            "rotation":     scores["rotation"],
            "sector_perf":  scores["sector_perf"],
            "credit_pulse": scores["credit_pulse"],
        },
        "component_confidence": {
            k: round(v, 3) for k, v in confs.items()
        },
    }


def _sector_symbols(sector_data) -> List[str]:
    return sorted({
        r["etf"] for r in sector_data
        if r.get("kind") == "sector" and r.get("etf")
        and r["etf"] != "n/a" and len(r["etf"]) <= 6
    })


def backfill_history(
    schwab,
    sector_data,
    existing_history: List[dict],
    days: int = 30,
) -> Tuple[List[dict], dict]:
    """Compute up to `days` trading-day sentiment snapshots from Schwab
    daily history. Returns (new_entries, stats).

    `stats` keys: requested, produced, skipped, missing_symbols (list).
    """
    stats = {
        "requested": days,
        "produced": 0,
        "skipped": 0,            # day failed the component-quality gate
        "skipped_existing": 0,   # day already present in existing_history
        "missing_symbols": [],
    }
    if schwab is None:
        logger.warning("backfill: no Schwab client")
        return [], stats

    # Fetch ~6mo to give credit_pulse 60-bar lookback room.
    frames: Dict[str, object] = {}
    _, vix_df = _fetch_history(schwab, VIX_SYMBOL_CANDIDATES, months=6)
    if vix_df is None:
        stats["missing_symbols"].append("VIX")
        return [], stats
    frames["vix"] = _date_indexed(vix_df)

    _, v1d_df = _fetch_history(schwab, VIX1D_SYMBOL_CANDIDATES, months=3)
    frames["vix1d"] = _date_indexed(v1d_df)
    if v1d_df is None:
        stats["missing_symbols"].append("$VIX1D")

    _, v9d_df = _fetch_history(schwab, VIX9D_SYMBOL_CANDIDATES, months=3)
    frames["vix9d"] = _date_indexed(v9d_df)
    if v9d_df is None:
        stats["missing_symbols"].append("$VIX9D")

    _, cpce_df = _fetch_history(schwab, CPCE_SYMBOL_CANDIDATES, months=3)
    frames["cpce"] = _date_indexed(cpce_df)
    if cpce_df is None:
        stats["missing_symbols"].append("$CPCE")

    _, advn_df = _fetch_history(schwab, ADVN_CANDIDATES, months=3)
    frames["advn"] = _date_indexed(advn_df)
    _, decn_df = _fetch_history(schwab, DECN_CANDIDATES, months=3)
    frames["decn"] = _date_indexed(decn_df)
    _, nyh_df = _fetch_history(schwab, NYHGH_CANDIDATES, months=3)
    frames["nyhgh"] = _date_indexed(nyh_df)
    _, nyl_df = _fetch_history(schwab, NYLOW_CANDIDATES, months=3)
    frames["nylow"] = _date_indexed(nyl_df)
    _, p50_df = _fetch_history(schwab, SPXA50R_SYMBOL_CANDIDATES, months=3)
    frames["spxa50r"] = _date_indexed(p50_df)

    # Sector ETFs
    frames["sectors"] = {}
    for sym in _sector_symbols(sector_data):
        try:
            df = schwab.get_daily_history(sym, months=3)
        except Exception as e:
            logger.debug("sector history %s: %s", sym, e)
            df = None
        if df is not None and len(df) >= 6:
            frames["sectors"][sym] = _date_indexed(df)
        else:
            stats["missing_symbols"].append(sym)

    # Credit pulse 6 months
    try:
        hyg_df = schwab.get_daily_history("HYG", months=6)
    except Exception:
        hyg_df = None
    try:
        iei_df = schwab.get_daily_history("IEI", months=6)
    except Exception:
        iei_df = None
    frames["hyg"] = _date_indexed(hyg_df)
    frames["iei"] = _date_indexed(iei_df)
    if hyg_df is None:
        stats["missing_symbols"].append("HYG")
    if iei_df is None:
        stats["missing_symbols"].append("IEI")

    # Common trading dates from VIX + first sector ETF.
    candidate_dates = list(frames["vix"].index)
    if frames["sectors"]:
        any_sec = next(iter(frames["sectors"].values()))
        # Keep VIX dates that also exist in a sector ETF history.
        sec_set = set(any_sec.index)
        candidate_dates = [d for d in candidate_dates if d in sec_set]

    # Drop today and future (live capture owns today). Also defensively
    # drop any candidate date that isn't an NYSE trading day — Schwab
    # daily history already excludes holidays, but a stray candle with a
    # holiday timestamp would otherwise pollute the rolling 30-day view.
    try:
        from shared.market_calendar import is_trading_day
    except ImportError:
        is_trading_day = lambda _d: True  # noqa: E731
    today = datetime.now().date()
    candidate_dates = [
        d for d in candidate_dates
        if d < today and is_trading_day(d)
    ]

    # Most recent `days` first; reverse to chronological for output.
    target_dates = candidate_dates[-days:]

    existing_dates = {h.get("date") for h in existing_history}

    out: List[dict] = []
    for d in target_dates:
        if d.isoformat() in existing_dates:
            stats["skipped_existing"] += 1
            continue
        try:
            entry = _score_one_day(d, frames, sector_data)
        except Exception as e:
            logger.warning("backfill %s failed: %s", d, e)
            entry = None
        if entry is None:
            stats["skipped"] += 1
            continue
        out.append(entry)
    stats["produced"] = len(out)
    logger.info(
        "backfill: produced=%d skipped=%d missing=%s",
        stats["produced"], stats["skipped"], stats["missing_symbols"])
    return out, stats
