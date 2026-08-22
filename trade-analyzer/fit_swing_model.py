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


def build_panel(hist, spy_close, sector_closes, horizon=HORIZON, universe=None):
    """Assemble a (date, symbol) factor panel + an aligned forward-excess Series.

    ``universe`` is the symbol -> sector-ETF map; it defaults to this module's
    curated one, and the Phase-4 research harness passes an expanded map."""
    universe = universe if universe is not None else UNIVERSE_SECTOR
    frames, fwds = [], []
    used = 0
    for sym, df in hist.items():
        if df is None or len(df) < 300:
            continue
        sec_close = sector_closes.get(universe.get(sym))
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


def _xs_norm(panel, lo=0.02, hi=0.98):
    """Per-factor cross-sectional norm: the time-average of the per-date
    (winsorized) cross-sectional mean and std. This is the basis on which the
    calibration was built (zscore_by_date winsorizes+standardizes per date), so a
    live raw factor mapped through it lands on the SAME scale as the calibration
    bands (independent of the live cross-section size)."""
    out = {}
    for c in panel.columns:
        s = panel[c].dropna()
        if s.empty:
            out[c] = {"mean": 0.0, "std": 1.0}
            continue
        def _w(g):
            return g.clip(lower=g.quantile(lo), upper=g.quantile(hi))
        gw = s.groupby(level="date").transform(_w)
        means = gw.groupby(level="date").mean()
        stds = gw.groupby(level="date").std(ddof=0)
        stds = stds[stds > 0]
        out[c] = {"mean": float(means.mean()),
                  "std": float(stds.mean()) if len(stds) else 1.0}
    return out


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
    weights = B.signed_ic_weights(ics)
    z = B.zscore_by_date(panel)
    comp = B.composite(z, weights)
    calib = B.calibrate(comp, forward, n_bands=5)
    wf = B.walk_forward(panel, forward, train=TRAIN, test=TEST, step=STEP)
    norm = _xs_norm(panel)
    # per-horizon IC for the report
    horizon_ic = {}
    for h in REPORT_HORIZONS:
        fwd_h = pd.concat([
            pd.Series(_forward_excess(_close(hist[s]), spy_close, h).to_numpy(),
                      index=panel.loc[(slice(None), s), :].index)
            for s in {sym for _, sym in panel.index} if hist.get(s) is not None
        ]).reindex(panel.index)
        horizon_ic[h] = {c: B.factor_ic(panel[c], fwd_h)["mean_ic"] for c in cols}

    # Per-regime weight sets (Phase 4). `build_regimes` always emits "all", adds
    # a key only for a regime it had a full trading year of data to estimate,
    # and calibrates every block OUT OF SAMPLE — see research/artifact.py. It
    # supersedes the single hand-built "all" block this script used to write.
    from src.analysis import regime as R
    from research import artifact as ART
    regimes = R.classify(spy_close)
    regime_blocks = ART.build_regimes(panel, forward, regimes,
                                      train=TRAIN, test=TEST, step=STEP)
    if not regime_blocks:                       # never ship an unscoreable artifact
        regime_blocks = {"all": {
            "weights": weights,
            "factor_ic": {c: {k: ics[c][k] for k in ("mean_ic", "icir", "n_days")} for c in cols},
            "norm": norm, "calibration": calib,
            "calibration_basis": "in-sample",
            "oos_ic": wf["oos_ic"], "oos_ic_by_fold": wf["oos_ic_by_fold"],
            "n_folds": wf["n_folds"],
        }}

    artifact = {
        "version": date.today().isoformat(),
        "fit_universe_n": used, "fit_universe": sorted(UNIVERSE_SECTOR), "horizon": HORIZON,
        "regimes": regime_blocks,
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
    lines += ["", "## Regime weight sets", "",
              "A key exists only for a regime the fit had a full trading year to "
              "estimate; anything thinner is omitted and the scorer falls back to "
              "`all`.", "",
              "| regime | days | OOS IC | neg folds | kept |",
              "|---|---:|---:|---:|---:|"]
    for key, blk in artifact["regimes"].items():
        kept = sum(1 for v in blk.get("weights", {}).values() if v)
        neg = sum(1 for x in blk.get("oos_ic_by_fold", []) if x < 0)
        lines.append(f"| {key} | {blk.get('n_days', '—')} | {blk.get('oos_ic', 0):+.4f} | "
                     f"{neg}/{blk.get('n_folds', 0)} | {kept} |")
    lines.append("")
    for key, blk in artifact["regimes"].items():
        top = {k: round(v, 3) for k, v in
               sorted(blk.get("weights", {}).items(), key=lambda kv: -abs(kv[1])) if v}
        lines.append(f"- **{key}** — {top}")

    basis = reg.get("calibration_basis", "in-sample")
    lines += ["", f"## Calibration bands, `all` (composite score -> forward excess)",
              "", f"Basis: **{basis}**. Out-of-sample bands see only walk-forward "
              "test windows, so their mean-forward and hit-rate are what the model "
              "actually delivered on unseen data — the in-sample equivalents "
              "(kept in the artifact as `calibration_insample`) are fitted on the "
              "rows the weights came from and flatter the model by an unknown "
              "amount.", "",
              "⚠ A `pooled` band was MERGED with a neighbour by the isotonic "
              "smoother, meaning the raw score->outcome relationship was "
              "non-monotone there. Several pooled bands in a row is the "
              "signature of a ranking with no ordering at all — after smoothing "
              "that is indistinguishable from a genuinely flat curve.", "",
              "| band | score range | mean fwd | hit-rate | n | pooled |",
              "|---:|---|---:|---:|---:|---|"]
    for b in reg.get("calibration", calib):
        lines.append(f"| {b['band']} | [{b['score_lo']:+.2f}, {b['score_hi']:+.2f}] | "
                     f"{b['mean_fwd']:+.4f} | {b['hit_rate']:.2%} | {b['n']} | "
                     f"{'**yes**' if b.get('pooled') else 'no'} |")
    ins = reg.get("calibration_insample")
    if ins:
        lines += ["", "In-sample, for comparison:", "",
                  "| band | mean fwd | hit-rate |", "|---:|---:|---:|"]
        for b in ins:
            lines.append(f"| {b['band']} | {b['mean_fwd']:+.4f} | {b['hit_rate']:.2%} |")
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
    kept = sum(1 for v in weights.values() if v != 0)
    print(f"OOS IC={wf['oos_ic']:+.4f} · folds={wf['n_folds']} · "
          f"kept {kept}/{len(ics)} factors · universe {used} symbols")
    print(f"wrote {SWING_MODEL}")
    print(f"wrote {SWING_MODEL_REPORT}")


if __name__ == "__main__":
    main()
