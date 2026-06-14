"""Assemble the full portfolio model from the per-comparison primitives.

``build_portfolio`` is the orchestration layer: it pulls positions and price
history through an injected ``data`` object, classifies positions into sectors,
computes the four comparisons, and emits a plain ``{"holdings", "sectors"}``
dict the UI renders. It owns no I/O of its own — every external dependency
(``data``, ``classify``, ``ranker``, ``benchmark``) is injected, so the whole
thing runs offline in tests.

The four comparisons (see ``src.sectors``):

1. ``vs_sector_rs`` — holding relative strength vs its sector ETF (EQUITY only).
2. ``benchmark_delta`` — portfolio sector weight minus a benchmark weight.
3. ``since_purchase_excess`` — since-entry return minus the sector's, per EQUITY.
4. ``tailwind`` — per-sector tailwind/headwind score+rank from a sector ranker.
"""
from __future__ import annotations

from src.sectors import (
    classify_positions,
    sector_weights,
    weights_vs_benchmark,
    tailwind_headwind,
    holding_vs_sector,
    since_purchase_vs_sector,
    compute_since_purchase_returns,
)
from src.entries import weighted_avg_entry


# Fields copied straight from each classified position onto its holding row.
_PASSTHROUGH = (
    "symbol",
    "asset_type",
    "sector",
    "sector_etf",
    "quantity",
    "avg_price",
    "market_value",
    "day_pl",
    "total_pl",
)


def build_portfolio(
    data,
    trades,
    *,
    classify=None,
    ranker=None,
    benchmark=None,
    months: int = 12,
) -> dict:
    """Assemble the full portfolio model.

    Args:
        data: object exposing ``get_positions() -> list[dict]`` and
            ``get_daily_history(symbol, months) -> DataFrame | None`` (a
            ``PortfolioData`` in production; a fake in tests).
        trades: list of 8-key trade-row dicts (the caller loads the store and
            passes ``store["trades"]``). Used for the cost-weighted entry.
        classify: optional ``underlying -> {"sector_etf", "sector"}`` callable.
            When ``None``, ``classify_positions`` uses its own default (the real
            lazy classifier).
        ranker: optional sector ranker for comparison #4. When ``None``,
            tailwind/headwind is skipped (every sector's ``tailwind`` is None).
        benchmark: optional ``{sector: weight}`` benchmark for comparison #2.
            When ``None`` (or empty) the benchmark delta is skipped (None).
        months: history window (months) requested from ``get_daily_history``.

    Returns:
        ``{"holdings": [...], "sectors": [...]}``.

        Each holding row carries the passthrough position fields plus
        ``vs_sector_rs`` (#1, EQUITY-only else None) and
        ``since_purchase_excess`` (#3, EQUITY-only else None).

        Each sector row is ``{sector, weight, benchmark_delta, tailwind}``.
    """
    positions = data.get_positions()
    if classify is None:
        classified = classify_positions(positions)
    else:
        classified = classify_positions(positions, classify=classify)

    entries = weighted_avg_entry(trades)

    holdings = [
        _build_holding(row, data, entries, months) for row in classified
    ]
    sectors = _build_sectors(classified, benchmark, ranker)
    return {"holdings": holdings, "sectors": sectors}


def _build_holding(row: dict, data, entries: dict, months: int) -> dict:
    """Build one holding row: passthrough fields + comparisons #1 and #3."""
    holding = {key: row.get(key) for key in _PASSTHROUGH}
    holding["vs_sector_rs"] = None
    holding["since_purchase_excess"] = None

    symbol = row.get("symbol")
    sector_etf = row.get("sector_etf")

    # Comparisons #1 and #3 apply to equities with a known sector ETF only.
    if row.get("asset_type") != "EQUITY" or sector_etf is None:
        return holding

    stock_df = data.get_daily_history(symbol, months)
    sector_df = data.get_daily_history(sector_etf, months)

    # #1 — relative strength vs sector ETF.
    if stock_df is not None and sector_df is not None:
        holding["vs_sector_rs"] = holding_vs_sector(stock_df, sector_df)

    # #3 — since-purchase excess return vs sector (guard None: the primitive
    # does a raw subtraction and must never be called with a missing return).
    entry = entries.get(symbol)
    if entry is not None:
        entry_date = entry["entry_date"]
        stock_ret = compute_since_purchase_returns(stock_df, entry_date)
        sector_ret = compute_since_purchase_returns(sector_df, entry_date)
        if stock_ret is not None and sector_ret is not None:
            holding["since_purchase_excess"] = since_purchase_vs_sector(
                stock_ret, sector_ret
            )

    return holding


def _build_sectors(classified: list[dict], benchmark, ranker) -> list[dict]:
    """Build per-sector rows: weight + comparisons #2 and #4."""
    my_weights = sector_weights(classified)
    bench_delta = weights_vs_benchmark(my_weights, benchmark or {})
    tw = tailwind_headwind(ranker) if ranker is not None else {}

    # Map sector NAME -> sector ETF using the classified rows. Tailwind is keyed
    # by ETF symbol (e.g. "XLK") while weights are keyed by sector name (e.g.
    # "Technology"); this bridge lets us look the tailwind up by name.
    name_to_etf = {row["sector"]: row.get("sector_etf") for row in classified}

    sectors = []
    for sector, weight in my_weights.items():
        etf = name_to_etf.get(sector)
        sectors.append(
            {
                "sector": sector,
                "weight": weight,
                "benchmark_delta": bench_delta.get(sector),
                "tailwind": tw.get(etf) if etf is not None else None,
            }
        )
    return sectors
