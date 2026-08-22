"""Expanded fit universe for the Phase-4 study (task 4.2).

The shipping fit uses 78 names. Cross-sectional IC is estimated from the spread
of ranks WITHIN each date, so the per-date sample size IS the universe size —
doubling it is the cheapest available reduction in the estimator's noise, and at
this signal level (composite OOS IC ~0.02) estimator noise is most of what the
study is fighting.

⚠ **Expanding the universe is not free at runtime.** The artifact's
``fit_universe`` is also the LIVE cross-section: `trade_svc` rebuilds a factor
snapshot over exactly these names once a day, and the first analyze of the day
pays for it. That is the argument against expanding without limit, and the
reason this stops where it does rather than taking every optionable name.

Selection rules, applied deliberately:
  * liquid and optionable — this model feeds an options page;
  * **five years of continuous history**, so no post-2021 IPO and no recent
    spin-off (CEG, GEHC, KVUE, SOLV, VLTO are all excluded for this reason). A
    name with a short history is silently dropped by ``build_panel``'s 300-bar
    floor, so including one would quietly shrink the universe rather than error;
  * roughly sector-balanced, because a universe tilted toward one sector makes
    the cross-sectional z-scores partly a sector bet.
"""
from fit_swing_model import UNIVERSE_SECTOR as _BASE

# Additions only — the base 78 are unchanged, so a symbol's membership never
# depends on which file you read.
_ADDITIONS = {
    "PANW": "XLK", "SNPS": "XLK", "CDNS": "XLK", "KLAC": "XLK", "LRCX": "XLK",
    "ANET": "XLK", "ADI": "XLK", "NXPI": "XLK", "FTNT": "XLK", "IBM": "XLK",
    "ACN": "XLK", "INTU": "XLK",

    "EA": "XLC", "TTWO": "XLC", "WBD": "XLC", "LYV": "XLC", "OMC": "XLC",
    "MTCH": "XLC", "PINS": "XLC", "TMUS": "XLC",

    "TGT": "XLY", "ROST": "XLY", "TJX": "XLY", "ORLY": "XLY", "AZO": "XLY",
    "CMG": "XLY", "MAR": "XLY", "RCL": "XLY", "F": "XLY", "GM": "XLY",

    "KMB": "XLP", "CL": "XLP", "GIS": "XLP", "HSY": "XLP", "STZ": "XLP",
    "KHC": "XLP", "SYY": "XLP", "KR": "XLP",

    "SCHW": "XLF", "USB": "XLF", "PNC": "XLF", "TFC": "XLF", "COF": "XLF",
    "DFS": "XLF", "MET": "XLF", "CB": "XLF", "PGR": "XLF", "SPGI": "XLF",

    "AMGN": "XLV", "GILD": "XLV", "VRTX": "XLV", "REGN": "XLV", "ISRG": "XLV",
    "SYK": "XLV", "BSX": "XLV", "MDT": "XLV", "ZTS": "XLV", "CI": "XLV",

    "MMM": "XLI", "EMR": "XLI", "ETN": "XLI", "ITW": "XLI", "PH": "XLI",
    "CMI": "XLI", "FDX": "XLI", "CSX": "XLI", "UNP": "XLI", "NSC": "XLI",

    "PSX": "XLE", "VLO": "XLE", "MPC": "XLE", "OXY": "XLE", "DVN": "XLE",
    "HAL": "XLE", "KMI": "XLE", "WMB": "XLE",

    "APD": "XLB", "SHW": "XLB", "ECL": "XLB", "DOW": "XLB", "NUE": "XLB",
    "VMC": "XLB", "MLM": "XLB", "ALB": "XLB",

    "D": "XLU", "AEP": "XLU", "EXC": "XLU", "SRE": "XLU", "XEL": "XLU",
    "ED": "XLU",

    "SPG": "XLRE", "O": "XLRE", "PSA": "XLRE", "WELL": "XLRE", "DLR": "XLRE",
    "AVB": "XLRE",
}

EXPANDED = {**_BASE, **_ADDITIONS}
SECTOR_ETFS = sorted(set(EXPANDED.values()))
