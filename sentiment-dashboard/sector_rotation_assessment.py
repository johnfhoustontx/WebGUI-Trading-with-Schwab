"""
sector_rotation_assessment - Daily GICS sector rotation report (RRG-based)

Version: 1.0.0
Last Updated: 2026-06-08

Produces a recurring "rotating FROM / rotating INTO" assessment across the 11
GICS sector ETFs measured against SPY, using a Relative Rotation Graph (RRG)
methodology. Money is never literally observed moving — RS is price-based and
measures relative *demand pressure*, not dollar flow.

Data source: the local Schwab proxy on :8100 (source of truth). ETFs carry no
'$' prefix. We pull periodType=year / daily candles, align all series on common
dates, and drop incomplete rows before computing RS-Ratio / RS-Momentum.

Version 1.0.0 Changes:
- Initial implementation
"""
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime

import requests
import pandas as pd
import numpy as np

# Cross-app ports/paths come from the repo-root repo_paths.py — never hard-code
# ports or D:\ paths in this monorepo.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import PROXY_URL  # noqa: E402

#############################################
# LOGGING SETUP
#############################################

logger = logging.getLogger(__name__)

#############################################
# CONSTANTS
#############################################

HERE = Path(__file__).resolve().parent

# Schwab proxy (source of truth) — never call Schwab directly.
PROXY_BASE = PROXY_URL
PRICEHISTORY_PATH = "/pricehistory"
HTTP_TIMEOUT = 30

# Price-history request shape (one trading year of daily candles).
PERIOD_TYPE = "year"
PERIOD = 1
FREQUENCY_TYPE = "daily"
FREQUENCY = 1

# RRG windows — all configurable. If quadrant separation is too tight,
# try NORM_WINDOW = 40 to make the standardized scores more sensitive.
RS_WINDOW = 10      # SMA window for the RS-Ratio centering term
MOM_WINDOW = 10     # ROC lookback + rolling mean window for RS-Momentum
NORM_WINDOW = 60    # rolling-std normalization window (try 40 if too tight)

# Minimum aligned bars before the MOST RECENT bar has a valid RS-Momentum.
# The rolling windows compound: RS-Ratio needs NORM_WINDOW warmup, the ROC
# adds MOM_WINDOW (the shift), then the ROC rolling-std adds another
# NORM_WINDOW. So the latest point only becomes valid once there are
# 2*NORM_WINDOW + MOM_WINDOW - 1 bars (= 129 with the defaults).
MIN_BARS = 2 * NORM_WINDOW + MOM_WINDOW - 1

BENCHMARK = "SPY"

# Meteor-tail length: how many trailing daily RRG points to retain per sector
# for the RRG scatter trail (oldest -> newest, last == current reading).
TAIL_LENGTH = 30

# 11 GICS sector SPDR ETFs → display names (no '$' prefix on ETFs).
SECTOR_ETFS = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLY":  "Discretionary",
    "XLI":  "Industrials",
    "XLB":  "Materials",
    "XLC":  "Communication",
    "XLP":  "Staples",
    "XLU":  "Utilities",
    "XLV":  "Health Care",
    "XLRE": "Real Estate",
}

# Risk-on / risk-off grouping for the headline.
CYCLICAL_ETFS = frozenset({"XLK", "XLF", "XLE", "XLY", "XLI", "XLB", "XLC"})
DEFENSIVE_ETFS = frozenset({"XLP", "XLU", "XLV", "XLRE"})

# Headline regime threshold on (cyclical mean RS-Mom − defensive mean RS-Mom).
RISK_THRESHOLD = 1.5

# Quadrant → rotation direction. Clockwise: Improving → Leading → Weakening
# → Lagging. RS-Momentum > 100 means strengthening regardless of RS-Ratio.
QUADRANT_DIRECTION = {
    "Leading":   "INTO",   # RS-Ratio > 100, RS-Mom > 100
    "Improving": "INTO",   # RS-Ratio < 100, RS-Mom > 100
    "Weakening": "FROM",   # RS-Ratio > 100, RS-Mom < 100
    "Lagging":   "FROM",   # RS-Ratio < 100, RS-Mom < 100
}

#############################################
# DATA FETCH
#############################################

def fetch_daily_closes(symbol):
    """Fetch a year of daily closes for one symbol from the Schwab proxy.

    Args:
        symbol: ticker (ETFs take no '$' prefix)

    Returns:
        pandas Series of closes indexed by date (normalized to midnight),
        or None on request/parse failure or empty candles.
    """
    params = {
        "symbol": symbol,
        "periodType": PERIOD_TYPE,
        "period": PERIOD,
        "frequencyType": FREQUENCY_TYPE,
        "frequency": FREQUENCY,
    }
    try:
        resp = requests.get(
            f"{PROXY_BASE}{PRICEHISTORY_PATH}", params=params, timeout=HTTP_TIMEOUT)
    except Exception as e:
        logger.error("proxy request failed for %s: %s", symbol, e)
        return None

    if resp.status_code != 200:
        logger.error("proxy %s for %s: %s", resp.status_code, symbol, resp.text[:200])
        return None

    try:
        candles = resp.json().get("candles") or []
    except Exception as e:
        logger.error("bad JSON for %s: %s", symbol, e)
        return None

    if not candles:
        logger.warning("no candles returned for %s", symbol)
        return None

    df = pd.DataFrame(candles)
    if "close" not in df or "datetime" not in df:
        logger.warning("candles missing close/datetime for %s", symbol)
        return None

    # Schwab datetimes are epoch milliseconds; normalize to the calendar date
    # so cross-symbol alignment is exact.
    idx = pd.to_datetime(df["datetime"], unit="ms").dt.normalize()
    s = pd.Series(df["close"].astype(float).values, index=idx, name=symbol)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    logger.debug("%s: %d daily closes (%s -> %s)",
                 symbol, len(s), s.index[0].date(), s.index[-1].date())
    return s


def build_aligned_frame(symbols):
    """Fetch all symbols and align on common trading dates.

    Args:
        symbols: list of tickers (benchmark + sectors)

    Returns:
        (DataFrame indexed by date with one column per symbol, list of
        symbols that failed to fetch). Rows with any missing value are
        dropped so every series spans identical dates.
    """
    series_map = {}
    missing = []
    for sym in symbols:
        s = fetch_daily_closes(sym)
        if s is None or s.empty:
            missing.append(sym)
            continue
        series_map[sym] = s

    if not series_map:
        return None, missing

    frame = pd.DataFrame(series_map).dropna()
    logger.info("aligned %d symbols over %d common trading days",
                frame.shape[1], frame.shape[0])
    return frame, missing


#############################################
# RRG INDICATORS
#############################################

def compute_rs_ratio(sector_close, bench_close):
    """RS-Ratio series for one sector vs benchmark.

    RS        = 100 * (sector_close / bench_close)
    RS-Ratio  = 100 + (RS − SMA(RS, RS_WINDOW)) / rolling_std(RS, NORM_WINDOW)

    Returns the RS-Ratio Series, or None on insufficient data.
    """
    if sector_close is None or bench_close is None:
        return None
    if len(sector_close) < NORM_WINDOW + 1:
        return None

    rs = 100.0 * (sector_close / bench_close)
    sma = rs.rolling(RS_WINDOW).mean()
    std = rs.rolling(NORM_WINDOW).std()
    rs_ratio = 100.0 + (rs - sma) / std.replace(0.0, np.nan)
    return rs_ratio


def compute_rs_momentum(rs_ratio):
    """RS-Momentum series from an RS-Ratio series.

    ROC          = RS_Ratio − RS_Ratio.shift(MOM_WINDOW)
    RS-Momentum  = 100 + (ROC − mean(ROC, MOM_WINDOW)) / rolling_std(ROC, NORM_WINDOW)

    Returns the RS-Momentum Series, or None on insufficient data.
    """
    if rs_ratio is None or len(rs_ratio) < MIN_BARS:
        return None

    roc = rs_ratio - rs_ratio.shift(MOM_WINDOW)
    roc_mean = roc.rolling(MOM_WINDOW).mean()
    roc_std = roc.rolling(NORM_WINDOW).std()
    rs_mom = 100.0 + (roc - roc_mean) / roc_std.replace(0.0, np.nan)
    return rs_mom


def classify_quadrant(rs_ratio_val, rs_mom_val):
    """Map a (RS-Ratio, RS-Momentum) pair to an RRG quadrant name."""
    strong_ratio = rs_ratio_val >= 100.0
    strong_mom = rs_mom_val >= 100.0
    if strong_ratio and strong_mom:
        return "Leading"
    if not strong_ratio and strong_mom:
        return "Improving"
    if strong_ratio and not strong_mom:
        return "Weakening"
    return "Lagging"


def assess_sector(etf, sector_close, bench_close):
    """Compute the current RRG reading for one sector ETF.

    Returns a dict {etf, name, rs_ratio, rs_momentum, quadrant, direction}
    using the most recent valid (non-NaN) data point, or None on
    insufficient data.
    """
    rs_ratio = compute_rs_ratio(sector_close, bench_close)
    rs_mom = compute_rs_momentum(rs_ratio)
    if rs_ratio is None or rs_mom is None:
        return None

    paired = pd.DataFrame({"ratio": rs_ratio, "mom": rs_mom}).dropna()
    if paired.empty:
        return None

    ratio_val = float(paired["ratio"].iloc[-1])
    mom_val = float(paired["mom"].iloc[-1])
    quadrant = classify_quadrant(ratio_val, mom_val)
    tail_df = paired.tail(TAIL_LENGTH)
    tail = [
        {"rs_ratio": round(float(r), 2), "rs_momentum": round(float(m), 2)}
        for r, m in zip(tail_df["ratio"], tail_df["mom"])
    ]
    return {
        "etf": etf,
        "name": SECTOR_ETFS.get(etf, etf),
        "rs_ratio": round(ratio_val, 2),
        "rs_momentum": round(mom_val, 2),
        "quadrant": quadrant,
        "direction": QUADRANT_DIRECTION[quadrant],
        "tail": tail,
    }


#############################################
# ASSESSMENT ASSEMBLY
#############################################

def build_headline(sectors):
    """Risk-ON / Risk-OFF / Mixed from cyclical vs defensive mean RS-Momentum.

    Returns a dict with the regime label, both means, the spread, and a
    human-readable summary line.
    """
    cyc = [s["rs_momentum"] for s in sectors if s["etf"] in CYCLICAL_ETFS]
    defn = [s["rs_momentum"] for s in sectors if s["etf"] in DEFENSIVE_ETFS]
    cyc_mean = sum(cyc) / len(cyc) if cyc else None
    def_mean = sum(defn) / len(defn) if defn else None

    if cyc_mean is None or def_mean is None:
        return {
            "regime": "Mixed",
            "cyclical_mom_mean": cyc_mean,
            "defensive_mom_mean": def_mean,
            "spread": None,
            "text": "Insufficient sector coverage for a regime call",
        }

    spread = cyc_mean - def_mean
    if spread > RISK_THRESHOLD:
        regime = "Risk-ON"
        text = "Risk-ON rotation - money rotating into cyclicals, out of defensives"
    elif spread < -RISK_THRESHOLD:
        regime = "Risk-OFF"
        text = "Risk-OFF rotation - money rotating into defensives, out of cyclicals"
    else:
        regime = "Mixed"
        text = "Mixed rotation - no decisive cyclical/defensive tilt"

    return {
        "regime": regime,
        "cyclical_mom_mean": round(cyc_mean, 2),
        "defensive_mom_mean": round(def_mean, 2),
        "spread": round(spread, 2),
        "text": text,
    }


def pair_from_into(sectors):
    """Pair rotating-FROM sectors with rotating-INTO sectors by rank.

    FROM sectors are ranked by *lowest* RS-Momentum (strongest relative
    selling pressure first); INTO sectors by *highest* RS-Momentum
    (strongest relative buying pressure first). Pairs are formed in rank
    order up to the shorter list.

    NOTE: this pairing is ORDINAL — it lines up the strongest relative-
    selling-pressure sector against the strongest relative-buying-pressure
    sector, and so on down the ranks. It is NOT a claim of literal cash
    flow from one sector to the other: RS is price-based and measures
    demand pressure, not dollars moved.
    """
    from_sorted = sorted(
        [s for s in sectors if s["direction"] == "FROM"],
        key=lambda s: s["rs_momentum"])               # lowest mom first
    into_sorted = sorted(
        [s for s in sectors if s["direction"] == "INTO"],
        key=lambda s: s["rs_momentum"], reverse=True)  # highest mom first

    pairs = []
    for frm, into in zip(from_sorted, into_sorted):
        pairs.append({"from": frm, "into": into})
    return from_sorted, into_sorted, pairs


def build_assessment(frame, report_date):
    """Assemble the full assessment dict from the aligned price frame."""
    bench_close = frame[BENCHMARK]
    sectors = []
    for etf in SECTOR_ETFS:
        if etf not in frame.columns:
            logger.warning("%s absent from aligned frame - skipping", etf)
            continue
        reading = assess_sector(etf, frame[etf], bench_close)
        if reading is None:
            logger.warning("%s: insufficient data for RRG reading", etf)
            continue
        sectors.append(reading)

    # Sort the full quadrant map by momentum, strongest first.
    sectors.sort(key=lambda s: s["rs_momentum"], reverse=True)

    headline = build_headline(sectors)
    from_sorted, into_sorted, pairs = pair_from_into(sectors)

    return {
        "date": report_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "benchmark": BENCHMARK,
        "params": {
            "rs_window": RS_WINDOW,
            "mom_window": MOM_WINDOW,
            "norm_window": NORM_WINDOW,
        },
        "headline": headline,
        "sectors": sectors,
        "rotating_from": from_sorted,
        "rotating_into": into_sorted,
        "pairs": pairs,
    }


def assess_from_close_series(sector_closes, bench_closes, report_date):
    """Compute the assessment from in-memory close lists (no network).

    For callers (e.g. the dashboard GUI) that already hold daily closes as
    plain lists. Series are tail-aligned to the shortest common length —
    benchmark and sector histories may differ in length — then handed to
    build_assessment unchanged.

    Args:
        sector_closes: {etf: [close, ...]} for sector ETFs.
        bench_closes:  [close, ...] for the benchmark (SPY).
        report_date:   ISO date string used as the assessment display date.

    Returns:
        The assessment dict, or None on insufficient data.
    """
    if not bench_closes or not sector_closes:
        return None

    series = {BENCHMARK: [float(c) for c in bench_closes]}
    for etf, closes in sector_closes.items():
        if etf in SECTOR_ETFS and closes:
            series[etf] = [float(c) for c in closes]

    if len(series) < 2:  # benchmark only — no usable sectors
        return None

    common = min(len(v) for v in series.values())
    if common < MIN_BARS:
        return None

    frame = pd.DataFrame(
        {k: pd.Series(v[-common:]) for k, v in series.items()})
    return build_assessment(frame, report_date)


#############################################
# OUTPUT
#############################################

def _fmt_cell(reading):
    """'Technology (Weakening)' style cell for the FROM/INTO table."""
    if reading is None:
        return ""
    return f"{reading['name']} ({reading['quadrant']})"


def print_report(assessment):
    """Render the human-readable assessment to stdout."""
    headline = assessment["headline"]
    print()
    print(f"SECTOR ROTATION ASSESSMENT  -  {assessment['date']}")
    print(headline["text"])
    if headline["spread"] is not None:
        print(f"(cyclical mean RS-Mom {headline['cyclical_mom_mean']:.2f} vs "
              f"defensive {headline['defensive_mom_mean']:.2f}, "
              f"spread {headline['spread']:+.2f}; "
              f"threshold +/-{RISK_THRESHOLD:g} -> {headline['regime']})")
    print()

    # FROM / INTO paired columns.
    from_sorted = assessment["rotating_from"]
    into_sorted = assessment["rotating_into"]
    col = 28
    print(f"{'ROTATING FROM':<{col}}{'ROTATING INTO'}")
    rows = max(len(from_sorted), len(into_sorted))
    for i in range(rows):
        left = _fmt_cell(from_sorted[i] if i < len(from_sorted) else None)
        right = _fmt_cell(into_sorted[i] if i < len(into_sorted) else None)
        print(f"{left:<{col}}{right}")
    if rows == 0:
        print("(no sectors classified)")
    print()

    # Full quadrant map, sorted by momentum (strongest first).
    print("FULL QUADRANT MAP (sorted by RS-Momentum)")
    print(f"{'SECTOR':<16}{'ETF':<7}{'RS-Ratio':>10}{'RS-Mom':>10}"
          f"  {'QUADRANT':<11}{'DIR'}")
    print("-" * 64)
    for s in assessment["sectors"]:
        print(f"{s['name']:<16}{s['etf']:<7}{s['rs_ratio']:>10.2f}"
              f"{s['rs_momentum']:>10.2f}  {s['quadrant']:<11}{s['direction']}")
    print()


def save_snapshot(assessment, out_dir):
    """Write the dated snapshot JSON and append to the rolling history JSONL."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    snap_path = out_dir / f"rotation_{assessment['date']}.json"
    snap_path.write_text(json.dumps(assessment, indent=2), encoding="utf-8")
    logger.info("snapshot saved -> %s", snap_path)

    hist_path = out_dir / "rotation_history.jsonl"
    with hist_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(assessment) + "\n")
    logger.info("appended to history -> %s", hist_path)


#############################################
# CLI
#############################################

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Daily GICS sector-rotation assessment (RRG vs SPY) via the "
                    "Schwab proxy on :8100.")
    parser.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON to stdout (for SentimentDashboard "
             "ingestion) instead of the human-readable table.")
    parser.add_argument(
        "--no-save", action="store_true",
        help="Do not write the dated snapshot or append to history.")
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose (DEBUG) logging.")
    return parser.parse_args()


#############################################
# MAIN
#############################################

def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s')
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    symbols = [BENCHMARK] + list(SECTOR_ETFS.keys())
    frame, missing = build_aligned_frame(symbols)
    if missing:
        logger.warning("failed to fetch: %s", ", ".join(missing))
    if frame is None or BENCHMARK not in (frame.columns if frame is not None else []):
        logger.error("no benchmark (%s) data - cannot assess rotation", BENCHMARK)
        sys.exit(1)

    report_date = frame.index[-1].date().isoformat()
    assessment = build_assessment(frame, report_date)

    if not assessment["sectors"]:
        logger.error("no sectors could be classified - insufficient data")
        sys.exit(1)

    if not args.no_save:
        save_snapshot(assessment, HERE)

    if args.json:
        print(json.dumps(assessment, indent=2))
    else:
        print_report(assessment)

    logger.info("done - regime: %s", assessment["headline"]["regime"])


if __name__ == "__main__":
    main()
