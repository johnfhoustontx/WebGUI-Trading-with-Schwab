#!/usr/bin/env python
"""Offline: validate the five-state market classifier over ~5yr of daily SPY bars.

Pulls SPY (+ $VIX for the regime split) ~5yr daily history via the schwab-proxy,
reconstructs the daily committed five-state series with the REAL sibling scoring
modules (via scoring/daily_direction.py), scores how those states stratify
forward returns (per-state means/hit-rates + ordinal rank IC at 5d/20d + a
VIX-regime split), and writes a markdown report + JSON artifact. Run
manually/periodically; NEVER imported by a service or the request path.

Rooted in sentiment-dashboard so `import scoring` resolves to THIS package, not
the options-scanner scoring.py (the cross-app collision the root CLAUDE.md warns
of), mirroring publish_bridge.py.

    cd sentiment-dashboard && ..\\.venv\\Scripts\\python validate_market_state.py
"""
from __future__ import annotations

import json
import sys
import pathlib
from datetime import datetime, timezone
from statistics import median

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))            # sentiment scoring/ package wins
sys.path.insert(0, str(HERE.parent))     # repo root for repo_paths

import requests  # noqa: E402

from repo_paths import (  # noqa: E402
    PROXY_URL,
    MARKET_STATE_VALIDATION_REPORT,
    MARKET_STATE_VALIDATION_JSON,
)
from scoring.daily_direction import (  # noqa: E402
    reconstruct_state_series,
    forward_returns,
    per_state_stats,
    ordinal_ic,
    STATE_ORDINAL,
)

YEARS = 5
HORIZONS = (5, 20)

# Display order: Bearish -> Lack-of-Bullishness -> Neutral -> Lack-of-Bearishness
# -> Bullish (ascending along STATE_ORDINAL).
STATE_ORDER = ["bearish", "lack_of_bullishness", "neutral",
               "lack_of_bearishness", "bullish"]
STATE_LABELS = {
    "bearish": "Bearish",
    "lack_of_bullishness": "Lack of Bullishness",
    "neutral": "Neutral",
    "lack_of_bearishness": "Lack of Bearishness",
    "bullish": "Bullish",
}

CAVEAT = (
    "**Honest caveat.** This validates the CORE two-axis concept "
    "(direction proxy + effort + rejection/defense) on daily-reconstructable "
    "inputs ONLY. The live classifier's 25-delta risk-reversal SKEW, "
    "cross-sector option-flow P/C delta, intraday session-structure, and "
    "streaming equity/option ORDER-FLOW axes are EXCLUDED here — no historical "
    "record exists to reconstruct them. A positive result is ENCOURAGING but "
    "NOT CONCLUSIVE for the live five-state classifier."
)


# --- fetch (mirrors fit_swing_model.py's /pricehistory call) ------------------
def _ms_to_date(ms):
    """Epoch-ms -> ISO date string (UTC, applied consistently to SPY + $VIX so
    the two series align on the same key)."""
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)\
            .date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _fetch_candles(symbol, years=YEARS):
    """Raw candle list (oldest-first) for a symbol, or None on failure."""
    try:
        r = requests.get(f"{PROXY_URL}/pricehistory", params={
            "symbol": symbol, "periodType": "year", "period": years,
            "frequencyType": "daily", "frequency": 1}, timeout=30)
        j = r.json()
    except Exception as exc:  # noqa: BLE001
        print(f"fetch failed for {symbol}: {exc}")
        return None
    data = j.get("data", j) if isinstance(j, dict) else {}
    candles = (data or {}).get("candles")
    if not candles:
        print(f"no candles for {symbol}")
        return None
    return candles


def fetch_ohlcv(symbol, years=YEARS):
    """SPY-style fetch: parallel (bars, dates) lists, oldest-first. A bad day is
    skipped (both lists stay aligned). Returns (None, None) on total failure."""
    candles = _fetch_candles(symbol, years)
    if candles is None:
        return None, None
    bars, dates = [], []
    for c in candles:
        if not isinstance(c, dict):
            continue
        try:
            bar = {k: float(c[k]) for k in ("open", "high", "low", "close", "volume")}
        except (KeyError, TypeError, ValueError):
            continue
        d = _ms_to_date(c.get("datetime"))
        if d is None:
            continue
        bars.append(bar)
        dates.append(d)
    if not bars:
        print(f"no usable candles for {symbol}")
        return None, None
    return bars, dates


def fetch_vix_map(years=YEARS):
    """{date: vix_close} over the window (for the regime split). {} on failure."""
    candles = _fetch_candles("$VIX", years)
    if candles is None:
        return {}
    out = {}
    for c in candles:
        if not isinstance(c, dict):
            continue
        d = _ms_to_date(c.get("datetime"))
        try:
            close = float(c["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if d is not None:
            out[d] = close
    return out


# --- regime split -------------------------------------------------------------
def vix_regime_split(states, fwd, dates, vix_map):
    """Partition days by VIX vs its median (calm < median, stressed >= median)
    and recompute ordinal IC + per-state stats within each regime. None when the
    VIX series is too sparse to split."""
    aligned = [vix_map.get(d) for d in dates]
    present = [v for v in aligned if v is not None]
    if len(present) < 2:
        return None
    med = median(present)

    def _mask(keep):
        return [s if (aligned[i] is not None and keep(aligned[i])) else None
                for i, s in enumerate(states)]

    calm = _mask(lambda v: v < med)
    stressed = _mask(lambda v: v >= med)
    return {
        "median_vix": med,
        "n_aligned": len(present),
        "calm": {"ic": ordinal_ic(calm, fwd),
                 "stats": per_state_stats(calm, fwd)},
        "stressed": {"ic": ordinal_ic(stressed, fwd),
                     "stats": per_state_stats(stressed, fwd)},
    }


# --- read helpers -------------------------------------------------------------
def _monotonicity(means):
    """Read whether the ordered per-state mean-20d list rises low->high.
    means = list of (state, mean) in STATE_ORDER (only present states)."""
    vals = [m for _, m in means]
    if len(vals) < 2:
        return "insufficient"
    ups = sum(1 for i in range(len(vals) - 1) if vals[i + 1] > vals[i])
    total = len(vals) - 1
    if ups == total:
        return "yes (strictly monotonic low->high)"
    if vals[-1] > vals[0] and ups >= total - 1:
        return "partial (rises overall, minor inversion)"
    if vals[-1] > vals[0]:
        return "partial (rises overall, some inversions)"
    return "no (not ordered low->high)"


def _pct(v):
    return f"{v * 100:+.2f}%" if v is not None else "—"


def _hit(v):
    return f"{v * 100:.1f}%" if v is not None else "—"


# --- study --------------------------------------------------------------------
def run_study(spy_bars, dates, vix_map):
    """Pure over the fetched inputs: reconstruct states, score forward-return
    stratification at each horizon + the VIX-regime split. Returns a dict."""
    states = reconstruct_state_series(spy_bars)
    closes = [b["close"] for b in spy_bars]
    usable = sum(1 for s in states if s is not None)

    horizons = {}
    for h in HORIZONS:
        fwd = forward_returns(closes, h)
        horizons[h] = {
            "stats": per_state_stats(states, fwd),
            "ic": ordinal_ic(states, fwd),
            "fwd": fwd,
        }

    fwd20 = horizons[20]["fwd"]
    regime = vix_regime_split(states, fwd20, dates, vix_map)

    return {
        "symbol": "SPY",
        "years": YEARS,
        "date_range": [dates[0], dates[-1]] if dates else [None, None],
        "n_days": len(spy_bars),
        "n_usable_state_days": usable,
        "horizons": horizons,
        "regime_split": regime,
        "states": states,
    }


# --- output -------------------------------------------------------------------
def build_json(study):
    """JSON-safe artifact (drop the raw per-bar series)."""
    def _stats(st):
        return {s: {"mean": v["mean"], "hit_rate": v["hit_rate"], "n": v["n"]}
                for s, v in st.items()}

    out = {
        "version": datetime.now(timezone.utc).date().isoformat(),
        "symbol": study["symbol"], "years": study["years"],
        "date_range": study["date_range"], "n_days": study["n_days"],
        "n_usable_state_days": study["n_usable_state_days"],
        "horizons": {str(h): {"ic": hd["ic"], "stats": _stats(hd["stats"])}
                     for h, hd in study["horizons"].items()},
    }
    reg = study["regime_split"]
    if reg:
        out["regime_split"] = {
            "median_vix": reg["median_vix"], "n_aligned": reg["n_aligned"],
            "calm": {"ic": reg["calm"]["ic"], "stats": _stats(reg["calm"]["stats"])},
            "stressed": {"ic": reg["stressed"]["ic"],
                         "stats": _stats(reg["stressed"]["stats"])},
        }
    return out


def build_report(study):
    s = study
    ic5 = s["horizons"][5]["ic"]
    ic20 = s["horizons"][20]["ic"]
    st5 = s["horizons"][5]["stats"]
    st20 = s["horizons"][20]["stats"]

    lines = [
        f"# Five-state market classifier — validation study "
        f"({datetime.now(timezone.utc).date().isoformat()})",
        "",
        CAVEAT,
        "",
        f"Coverage: **{s['symbol']}** · {s['date_range'][0]} → {s['date_range'][1]} · "
        f"{s['n_days']} daily bars · {s['n_usable_state_days']} usable state-days.",
        "",
        "## Per-state forward returns",
        "",
        "| state | n | mean 5d | hit% 5d | mean 20d | hit% 20d |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    means20 = []
    for st in STATE_ORDER:
        a = st5.get(st)
        b = st20.get(st)
        n = (b or a or {}).get("n", 0)
        lines.append(
            f"| {STATE_LABELS[st]} | {n} | "
            f"{_pct(a['mean']) if a else '—'} | {_hit(a['hit_rate']) if a else '—'} | "
            f"{_pct(b['mean']) if b else '—'} | {_hit(b['hit_rate']) if b else '—'} |")
        if b:
            means20.append((st, b["mean"]))

    lines += [
        "",
        f"**Ordinal IC** (state ordinal vs forward return): "
        f"5d = **{ic5:+.4f}** · 20d = **{ic20:+.4f}**",
        "",
        f"**Monotonicity (mean 20d along Bearish→…→Bullish):** "
        f"{_monotonicity(means20)}",
    ]

    reg = s["regime_split"]
    lines += ["", "## VIX-regime split (H=20)"]
    if not reg:
        lines.append("")
        lines.append("_VIX series too sparse to split._")
    else:
        lines += [
            f"(median $VIX = {reg['median_vix']:.2f}; {reg['n_aligned']} aligned days)",
            "",
            "| regime | ordinal IC | Bearish mean 20d | Bullish mean 20d |",
            "|---|---:|---:|---:|",
        ]
        for name in ("calm", "stressed"):
            r = reg[name]
            bear = r["stats"].get("bearish")
            bull = r["stats"].get("bullish")
            lines.append(
                f"| {name} | {r['ic']:+.4f} | "
                f"{_pct(bear['mean']) if bear else '—'} | "
                f"{_pct(bull['mean']) if bull else '—'} |")

    lines += [
        "",
        "## Limitations",
        "- Single symbol (SPY) — a market-index proxy, not a cross-section.",
        "- Only the daily-reconstructable axes (direction proxy + effort + "
        "rejection/defense) are exercised; skew / sector-flow / session-structure "
        "/ streaming order-flow are excluded (no historical record).",
        "- ~5yr window: regime non-stationarity; a positive read is encouraging, "
        "not conclusive for the live classifier.",
        "- The daily direction score is a PROXY for the live intraday direction "
        "blend, not the identical computation.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(study):
    MARKET_STATE_VALIDATION_REPORT.parent.mkdir(parents=True, exist_ok=True)
    MARKET_STATE_VALIDATION_REPORT.write_text(build_report(study), encoding="utf-8")
    MARKET_STATE_VALIDATION_JSON.write_text(
        json.dumps(build_json(study), indent=2), encoding="utf-8")


def main():
    spy_bars, dates = fetch_ohlcv("SPY")
    if not spy_bars:
        print("could not fetch SPY daily history — is the proxy up on "
              f"{PROXY_URL}?")
        sys.exit(1)
    vix_map = fetch_vix_map()   # empty -> regime split degrades gracefully

    study = run_study(spy_bars, dates, vix_map)
    write_outputs(study)

    ic20 = study["horizons"][20]["ic"]
    st20 = study["horizons"][20]["stats"]
    bull = st20.get("bullish", {}).get("mean")
    bear = st20.get("bearish", {}).get("mean")
    print(f"wrote {MARKET_STATE_VALIDATION_REPORT}")
    print(f"wrote {MARKET_STATE_VALIDATION_JSON}")
    print(f"HEADLINE: 20d ordinal IC = {ic20:+.4f} · "
          f"Bullish mean-20d = {_pct(bull)} · Bearish mean-20d = {_pct(bear)}")


if __name__ == "__main__":
    main()
