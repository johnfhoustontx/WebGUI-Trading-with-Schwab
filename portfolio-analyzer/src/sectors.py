"""Sector classification + portfolio grouping.

This is a *pure-logic* module by default: its public functions accept an
injected ``classify`` callable so unit tests stay offline and never pull the
heavy ``shared/analysis_lib/sector_analysis`` stack (pandas/config).

The real classifier (``_default_classify``) lazily imports
``get_sector_info`` from the shared module only when invoked, adapting the
``IndustryInfo`` dataclass into the simple ``{"sector_etf", "sector"}`` dict the
grouping logic expects.

Two responsibilities:

- ``classify_positions`` enriches each position with ``sector`` /
  ``sector_etf``. Futures are bucketed under ``"Futures"`` without touching the
  classifier; everything else (equities, options) is classified by its
  ``underlying`` so options roll into the same sector as their stock.
- ``sector_weights`` turns classified rows into sector weights. The denominator
  is the **sum of absolute market values** so short / negative positions can't
  produce nonsense weights (e.g. a denominator near zero from longs cancelling
  shorts).
"""
from __future__ import annotations


# Lazily bound reference to ``sector_analysis.calculate_stock_vs_sector_rs``.
# Kept ``None`` at import time so the heavy ``shared/analysis_lib`` stack
# (pandas/config) is never pulled in at module load. ``holding_vs_sector``
# resolves it on first use and caches it here. Tests monkeypatch this module
# global directly (``monkeypatch.setattr(src.sectors,
# "calculate_stock_vs_sector_rs", fake)``) to bypass the import and stay offline.
calculate_stock_vs_sector_rs = None


# FinViz/Yahoo-style sector names -> GICS names used by the benchmark TOML.
# The shared classifier returns GICS names for tickers in its STOCK_SECTOR_MAP,
# but FinViz/Yahoo-style names for tickers resolved via the FinViz fallback
# (e.g. "Consumer Cyclical", "Financial Services", "Basic Materials"). Those do
# NOT match the keys in config/benchmark_weights.toml (which uses GICS spellings),
# so comparison #2 (weights_vs_benchmark) would treat each as its own 100%-
# overweight bucket. Normalizing to the GICS spelling here keeps the buckets
# aligned. GICS names already in use (Technology, Healthcare, Energy, etc.) are
# not in the map and pass through unchanged; "Unknown" also passes through.
_SECTOR_NAME_GICS = {
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Financial": "Financials",
    "Financial Services": "Financials",
    "Basic Materials": "Materials",
}


def normalize_sector_name(name: str) -> str:
    """Map a FinViz/Yahoo-style sector name to its GICS spelling.

    Pure helper (no imports, no network). Names already in GICS form — or any
    name not in the mapping, including ``"Unknown"`` — pass through unchanged.
    The target spellings match the keys in ``config/benchmark_weights.toml`` so
    classified holdings line up with the benchmark in comparison #2.
    """
    return _SECTOR_NAME_GICS.get(name, name)


def _default_classify(underlying: str) -> dict:
    """Classify ``underlying`` via the shared sector_analysis module.

    Imported lazily so the test suite (and any pure consumer) does not pull
    pandas/config at import time. Adapts ``IndustryInfo`` to the simple dict
    ``{"sector_etf", "sector"}``. Falls back to ``{"SPY", "Unknown"}`` when the
    symbol is unknown or has no meaningful sector name.
    """
    import sys
    import pathlib

    # repo root, then the analysis_lib folder (sector_analysis does
    # ``from config import ...`` internally, so it must be importable by name).
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
    from repo_paths import SHARED

    sys.path.insert(0, str(SHARED / "analysis_lib"))
    from sector_analysis import get_sector_info

    info = get_sector_info(underlying)
    if info is None or not info.sector_name or info.sector_name == "Unknown":
        return {"sector_etf": "SPY", "sector": "Unknown"}
    # Normalize the name to GICS so it matches benchmark_weights.toml keys; the
    # ETF is left as-is. FinViz-fallback tickers come back with FinViz/Yahoo
    # spellings that would otherwise mis-bucket in weights_vs_benchmark.
    return {
        "sector_etf": info.sector_etf,
        "sector": normalize_sector_name(info.sector_name),
    }


def classify_positions(positions: list[dict], classify=_default_classify) -> list[dict]:
    """Enrich each position with ``sector`` and ``sector_etf``.

    Args:
        positions: position dicts carrying at least ``underlying``,
            ``asset_type`` and ``market_value``.
        classify: callable ``underlying -> {"sector_etf", "sector"}``. Defaults
            to the real (lazy) classifier; tests inject a fake to stay offline.

    Returns:
        New dicts (inputs are not mutated): every original key, plus ``sector``
        and ``sector_etf``. Futures get ``sector="Futures"`` /
        ``sector_etf=None`` without calling ``classify``. All other rows are
        classified by their ``underlying``, so options roll into their
        underlying's sector.
    """
    rows: list[dict] = []
    for position in positions:
        row = dict(position)  # shallow copy — never mutate the caller's dict
        if position.get("asset_type") == "FUTURE":
            row["sector"] = "Futures"
            row["sector_etf"] = None
        else:
            info = classify(position["underlying"])
            row["sector"] = info["sector"]
            row["sector_etf"] = info["sector_etf"]
        rows.append(row)
    return rows


def sector_weights(rows: list[dict]) -> dict[str, float]:
    """Weight portfolio ``market_value`` by ``sector``.

    Each sector's weight is ``sum(market_value for that sector) / total_abs``,
    where ``total_abs`` is the sum of **absolute** market values across all
    rows. Using the absolute total as the denominator keeps weights sensible
    when shorts/negatives are present (a signed total could be near zero and
    blow weights up). Individual sector weights may still be negative if that
    sector is net short.

    Args:
        rows: dicts carrying ``sector`` and ``market_value`` (e.g. the output
            of :func:`classify_positions`).

    Returns:
        ``{sector: weight}``. Empty input or a zero absolute total yields
        ``{}``.
    """
    totals: dict[str, float] = {}
    total_abs = 0.0
    for row in rows:
        mv = float(row["market_value"])
        sector = row["sector"]
        totals[sector] = totals.get(sector, 0.0) + mv
        total_abs += abs(mv)

    if total_abs == 0:
        return {}
    return {sector: value / total_abs for sector, value in totals.items()}


def weights_vs_benchmark(
    my_weights: dict[str, float], benchmark: dict[str, float]
) -> dict[str, float]:
    """Comparison #2: my sector weights minus a benchmark's sector weights.

    For every sector present in *either* dict, computes
    ``my_weights.get(sector, 0.0) - benchmark.get(sector, 0.0)``. A positive
    value means the portfolio is **overweight** that sector relative to the
    benchmark; negative means underweight.

    Args:
        my_weights: ``{sector: weight}`` for the portfolio (e.g. the output of
            :func:`sector_weights`).
        benchmark: ``{sector: weight}`` benchmark (e.g. S&P 500 GICS weights from
            :func:`src.benchmark.load_benchmark_weights`).

    Returns:
        ``{sector: over_under}`` covering every sector in either input. Returns
        ``{}`` if ``benchmark`` is empty — an empty benchmark signals that
        comparison #2 should be skipped.
    """
    if not benchmark:
        return {}
    sectors = set(my_weights) | set(benchmark)
    return {
        sector: my_weights.get(sector, 0.0) - benchmark.get(sector, 0.0)
        for sector in sectors
    }


def tailwind_headwind(ranker) -> dict[str, dict]:
    """Comparison #4: per-sector tailwind/headwind from a sector ranker.

    Thin pass-through over an *already-constructed* ranker (the shared
    ``sector_analysis.SectorRanker`` in production; a fake in tests). This module
    never imports or constructs the real ranker — wiring happens elsewhere — so
    no network or heavy pandas/config import is pulled here.

    Expected ranker interface (matches ``SectorRanker``):
        ``ranker.get_rankings()`` returns a list of objects, each having
        ``.symbol`` (sector ETF, e.g. ``"XLK"``), ``.composite_rs`` (the
        composite relative-strength score) and ``.rank`` (1 = strongest).

    Args:
        ranker: object exposing ``get_rankings()`` as described above.

    Returns:
        ``{symbol: {"score": float, "rank": int}}`` keyed by sector ETF. A
        higher score / lower rank is a tailwind; the reverse is a headwind.
    """
    return {
        r.symbol: {"score": r.composite_rs, "rank": r.rank}
        for r in ranker.get_rankings()
    }


def _load_calculate_stock_vs_sector_rs():
    """Lazily import ``calculate_stock_vs_sector_rs`` from sector_analysis.

    Imported only on first call so the heavy ``shared/analysis_lib`` stack
    (pandas/config) is not pulled at module import time. ``sector_analysis``
    does ``from config import ...`` internally, so the analysis_lib folder must
    be importable by name.
    """
    import sys
    import pathlib

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
    from repo_paths import SHARED

    sys.path.insert(0, str(SHARED / "analysis_lib"))
    from sector_analysis import calculate_stock_vs_sector_rs as _rs

    return _rs


def holding_vs_sector(stock_df, sector_df, periods=None) -> dict:
    """Comparison #1: holding's relative strength vs its sector ETF.

    Thin wrapper over ``sector_analysis.calculate_stock_vs_sector_rs``. The
    callable is resolved lazily and cached in the module global
    ``calculate_stock_vs_sector_rs`` (kept ``None`` at import to avoid the heavy
    import). Tests can monkeypatch that global to inject a fake and stay offline.

    Args:
        stock_df: Stock daily price DataFrame.
        sector_df: Sector ETF daily price DataFrame.
        periods: Optional lookback periods (days) forwarded to the RS function.

    Returns:
        ``{period_label: rs_value}`` where 100 == parity with the sector.
    """
    global calculate_stock_vs_sector_rs
    if calculate_stock_vs_sector_rs is None:
        calculate_stock_vs_sector_rs = _load_calculate_stock_vs_sector_rs()
    return calculate_stock_vs_sector_rs(stock_df, sector_df, periods)


def since_purchase_vs_sector(stock_return: float, sector_return: float) -> float:
    """Comparison #3: holding's since-entry return minus the sector's return.

    Both args are fractional returns over the same span (e.g. ``0.20`` for
    +20%). A positive result means the holding outperformed its sector ETF
    since purchase.
    """
    return stock_return - sector_return


def compute_since_purchase_returns(price_df, entry_date: str):
    """Fractional return from the entry-date close to the latest close.

    The entry close is the ``close`` on the first row whose ``datetime`` date is
    on/after ``entry_date`` (the first trading day on/after entry). The return is
    ``(last_close / entry_close) - 1.0``.

    Args:
        price_df: Daily price history with ``datetime`` and ``close`` columns,
            as produced by ``PortfolioData.get_daily_history``.
        entry_date: ISO ``YYYY-MM-DD`` purchase date.

    Returns:
        The fractional return as a float, or ``None`` if ``price_df`` is
        None/empty or no row falls on/after ``entry_date``.
    """
    import pandas as pd

    if price_df is None or len(price_df) == 0:
        return None

    entry_ts = pd.Timestamp(entry_date)
    on_or_after = price_df[pd.to_datetime(price_df["datetime"]) >= entry_ts]
    if len(on_or_after) == 0:
        return None

    entry_close = float(on_or_after["close"].iloc[0])
    last_close = float(price_df["close"].iloc[-1])
    if entry_close == 0:
        return None
    return (last_close / entry_close) - 1.0
