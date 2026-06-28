"""Offline: fit the swing factor model and write the artifact + research report.

Pulls ~3-5yr daily history for a liquid fit universe via the schwab-proxy, builds
a (date, symbol) panel with forward EXCESS returns vs SPY, runs the pure backtest
engine, and writes SWING_MODEL (json) + SWING_MODEL_REPORT (markdown). Run
manually/scheduled; NEVER imported by a service. Factors are causal (factors.py);
labels legitimately use the future H-bar return (the prediction target).
"""
import json
import sys
import pathlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # repo root
from repo_paths import PROXY_URL, SWING_MODEL, SWING_MODEL_REPORT       # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))        # for src.analysis
from src.analysis import factors as F                                   # noqa: E402
from src.analysis import backtest as B                                  # noqa: E402

YEARS = 5
HORIZON = 20                       # primary label horizon (~4 weeks)
REPORT_HORIZONS = [10, 20, 40]     # IC reported across these in the research table
TRAIN, TEST, STEP = 378, 63, 63    # walk-forward windows (trading days)

# Curated liquid fit universe -> sector ETF. Fitting on ~70 names (not the live
# 20-name watchlist) makes the cross-sectional IC estimates trustworthy.
UNIVERSE_SECTOR = {
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AVGO": "XLK", "AMD": "XLK",
    "CRM": "XLK", "ORCL": "XLK", "ADBE": "XLK", "CSCO": "XLK", "QCOM": "XLK",
    "TXN": "XLK", "INTC": "XLK", "MU": "XLK", "AMAT": "XLK", "NOW": "XLK",
    "GOOGL": "XLC", "META": "XLC", "NFLX": "XLC", "DIS": "XLC", "T": "XLC",
    "VZ": "XLC", "CMCSA": "XLC",
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY", "MCD": "XLY", "NKE": "XLY",
    "SBUX": "XLY", "LOW": "XLY", "BKNG": "XLY",
    "WMT": "XLP", "COST": "XLP", "PG": "XLP", "KO": "XLP", "PEP": "XLP",
    "MDLZ": "XLP",
    "JPM": "XLF", "BAC": "XLF", "WFC": "XLF", "GS": "XLF", "MS": "XLF",
    "C": "XLF", "V": "XLF", "MA": "XLF", "AXP": "XLF", "BLK": "XLF",
    "UNH": "XLV", "JNJ": "XLV", "LLY": "XLV", "PFE": "XLV", "ABBV": "XLV",
    "MRK": "XLV", "TMO": "XLV", "ABT": "XLV", "DHR": "XLV", "BMY": "XLV",
    "BA": "XLI", "CAT": "XLI", "GE": "XLI", "HON": "XLI", "UPS": "XLI",
    "RTX": "XLI", "DE": "XLI", "LMT": "XLI",
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE", "SLB": "XLE", "EOG": "XLE",
    "LIN": "XLB", "FCX": "XLB", "NEM": "XLB",
    "NEE": "XLU", "DUK": "XLU", "SO": "XLU",
    "AMT": "XLRE", "PLD": "XLRE", "EQIX": "XLRE",
}
SECTOR_ETFS = sorted(set(UNIVERSE_SECTOR.values()))


def fetch_daily(symbol, years=YEARS):
    try:
        r = requests.get(f"{PROXY_URL}/pricehistory", params={
            "symbol": symbol, "periodType": "year", "period": years,
            "frequencyType": "daily", "frequency": 1}, timeout=30)
        j = r.json()
        data = j.get("data", j) if isinstance(j, dict) else {}
        candles = (data or {}).get("candles")
        if not candles:
            return None
        df = pd.DataFrame(candles)
        if "datetime" not in df or "close" not in df:
            return None
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
        return df.sort_values("datetime").reset_index(drop=True)
    except Exception:
        return None


def fetch_all(symbols):
    with ThreadPoolExecutor(max_workers=8) as ex:
        return dict(zip(symbols, ex.map(fetch_daily, symbols)))


def _close(df):
    return pd.Series(df["close"].to_numpy(dtype="float64"),
                     index=pd.to_datetime(df["datetime"]))


def _forward_excess(close, spy_close, horizon):
    spy = spy_close.reindex(close.index).ffill()
    sym_fwd = close.shift(-horizon) / close - 1.0           # FUTURE return (the label)
    spy_fwd = spy.shift(-horizon) / spy - 1.0
    return sym_fwd - spy_fwd


def build_panel(hist, spy_close, sector_closes, horizon=HORIZON):
    """Assemble a (date, symbol) factor panel + an aligned forward-excess Series."""
    frames, fwds = [], []
    used = 0
    for sym, df in hist.items():
        if df is None or len(df) < 300:
            continue
        sec_close = sector_closes.get(UNIVERSE_SECTOR.get(sym))
        ff = F.compute_factor_frame(df, spy_close=spy_close, sector_close=sec_close)
        fwd = _forward_excess(_close(df), spy_close, horizon)
        mi = pd.MultiIndex.from_arrays(
            [ff.index, [sym] * len(ff)], names=["date", "symbol"])
        ff.index = mi
        frames.append(ff)
        fwds.append(pd.Series(fwd.to_numpy(), index=mi))
        used += 1
    panel = pd.concat(frames).sort_index()
    forward = pd.concat(fwds).reindex(panel.index)
    return panel, forward, used


def fit():
    spy_df = fetch_daily("SPY")
    if spy_df is None:
        raise SystemExit("could not fetch SPY")
    spy_close = _close(spy_df)
    sector_closes = {etf: _close(d) for etf, d in fetch_all(SECTOR_ETFS).items() if d is not None}
    hist = fetch_all(list(UNIVERSE_SECTOR))
    panel, forward, used = build_panel(hist, spy_close, sector_closes)

    cols = list(panel.columns)
    ics = {c: B.factor_ic(panel[c], forward) for c in cols}            # IC is rank-based -> raw ok
    weights = B.mean_ic_weights(ics)
    z = B.zscore_by_date(panel)
    comp = B.composite(z, weights)
    calib = B.calibrate(comp, forward, n_bands=5)
    wf = B.walk_forward(panel, forward, train=TRAIN, test=TEST, step=STEP)
    norm = {c: {"mean": float(panel[c].mean()), "std": float(panel[c].std(ddof=0))}
            for c in cols}
    # per-horizon IC for the report
    horizon_ic = {}
    for h in REPORT_HORIZONS:
        fwd_h = pd.concat([
            pd.Series(_forward_excess(_close(hist[s]), spy_close, h).to_numpy(),
                      index=panel.loc[(slice(None), s), :].index)
            for s in {sym for _, sym in panel.index} if hist.get(s) is not None
        ]).reindex(panel.index)
        horizon_ic[h] = {c: B.factor_ic(panel[c], fwd_h)["mean_ic"] for c in cols}

    artifact = {
        "version": date.today().isoformat(),
        "fit_universe_n": used, "horizon": HORIZON,
        "regimes": {"all": {
            "weights": weights,
            "factor_ic": {c: {k: ics[c][k] for k in ("mean_ic", "icir", "n_days")} for c in cols},
            "norm": norm, "calibration": calib,
            "oos_ic": wf["oos_ic"], "oos_ic_by_fold": wf["oos_ic_by_fold"],
            "n_folds": wf["n_folds"],
        }},
    }
    return artifact, ics, weights, horizon_ic, wf, calib, used


def write_artifact(artifact):
    SWING_MODEL.parent.mkdir(parents=True, exist_ok=True)
    SWING_MODEL.write_text(json.dumps(artifact, indent=2), encoding="utf-8")


def write_report(artifact, ics, weights, horizon_ic, wf, calib, used):
    reg = artifact["regimes"]["all"]
    lines = [f"# Swing model research report — {artifact['version']}", "",
             f"Fit universe: **{used}** symbols · horizon **{HORIZON}d** · "
             f"walk-forward train/test/step = {TRAIN}/{TEST}/{STEP} · folds {wf['n_folds']}", "",
             f"**Composite OOS IC: {wf['oos_ic']:+.4f}**  (per fold: "
             + ", ".join(f"{x:+.3f}" for x in wf['oos_ic_by_fold']) + ")", "",
             "## Per-factor IC", "",
             "| factor | mean IC | ICIR | n_days | weight | IC@10 | IC@20 | IC@40 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for c in sorted(ics, key=lambda k: -abs(ics[k]["mean_ic"])):
        lines.append(f"| {c} | {ics[c]['mean_ic']:+.4f} | {ics[c]['icir']:+.3f} | "
                     f"{ics[c]['n_days']} | {weights.get(c,0):.3f} | "
                     f"{horizon_ic[10][c]:+.4f} | {horizon_ic[20][c]:+.4f} | {horizon_ic[40][c]:+.4f} |")
    lines += ["", "## Calibration bands (composite score -> forward excess)", "",
              "| band | score range | mean fwd | hit-rate | n |", "|---:|---|---:|---:|---:|"]
    for b in calib:
        lines.append(f"| {b['band']} | [{b['score_lo']:+.2f}, {b['score_hi']:+.2f}] | "
                     f"{b['mean_fwd']:+.4f} | {b['hit_rate']:.2%} | {b['n']} |")
    lines += ["", "## Limitations",
              "- Survivorship bias: the fit universe is today's liquid names (survivors).",
              "- Regime non-stationarity: a few-year fit may not hold forward.",
              "- Thin LIVE cross-section (the ~20-name watchlist) vs the fit universe.",
              "- Validation reduces self-deception; it does not guarantee forward performance."]
    SWING_MODEL_REPORT.write_text("\n".join(lines), encoding="utf-8")


def main():
    artifact, ics, weights, horizon_ic, wf, calib, used = fit()
    write_artifact(artifact)
    write_report(artifact, ics, weights, horizon_ic, wf, calib, used)
    kept = sum(1 for v in weights.values() if v > 0)
    print(f"OOS IC={wf['oos_ic']:+.4f} · folds={wf['n_folds']} · "
          f"kept {kept}/{len(weights)} factors · universe {used} symbols")
    print(f"wrote {SWING_MODEL}")
    print(f"wrote {SWING_MODEL_REPORT}")


if __name__ == "__main__":
    main()
