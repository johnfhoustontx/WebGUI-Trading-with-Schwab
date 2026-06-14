"""Pure presentation helpers: turn the portfolio model into display rows.

This module is deliberately free of any GUI or network imports so it can be
unit-tested in isolation. ``portfolio_analyzer.py`` (the customtkinter shell)
imports these helpers to render its tables; the helpers never touch widgets.

The portfolio model is the ``{"holdings": [...], "sectors": [...]}`` dict
produced by :func:`src.portfolio.build_portfolio`. See that module for the field
contract. Every formatter here is robust to ``None`` (rendered as ``"—"``) and
to missing keys, so a partially-populated model never crashes the UI.

Column definitions
------------------
``HOLDINGS_COLUMNS`` and ``SECTOR_COLUMNS`` are ordered lists of
``{"key", "header"}`` dicts. ``header`` is the human label shown in the table;
``key`` is the dict key of the corresponding cell in the rows returned by
:func:`format_holdings_rows` / :func:`format_sector_rows`.
"""
from __future__ import annotations

from src.evaluation import grade_letter

# The em-dash placeholder used everywhere a value is missing / not applicable.
DASH = "—"

# Holdings table columns (order = left-to-right display order).
HOLDINGS_COLUMNS = [
    {"key": "symbol", "header": "Symbol"},
    {"key": "sector", "header": "Sector"},
    {"key": "quantity", "header": "Qty"},
    {"key": "market_value", "header": "Market Value"},
    {"key": "day_pl", "header": "Day P/L"},
    {"key": "total_pl", "header": "Total P/L"},
    {"key": "vs_sector", "header": "vs Sector (RS)"},
    {"key": "since_purchase", "header": "Since Purchase"},
]

# Sectors table columns.
SECTOR_COLUMNS = [
    {"key": "sector", "header": "Sector"},
    {"key": "weight", "header": "Weight"},
    {"key": "benchmark_delta", "header": "vs Benchmark"},
    {"key": "tailwind", "header": "Tailwind"},
]

# Performance tab columns.
PERFORMANCE_COLUMNS = [
    {"key": "symbol", "header": "Symbol"},
    {"key": "grade_return", "header": "Return"},
    {"key": "grade_capital", "header": "Capital"},
    {"key": "grade_risk", "header": "Risk"},
    {"key": "grade_execution", "header": "Entry"},
    {"key": "composite", "header": "Overall"},
    {"key": "ann_return", "header": "Ann. Return"},
    {"key": "vs_sector", "header": "vs Sector"},
    {"key": "drawdown", "header": "Off Peak"},
    {"key": "top_action", "header": "Suggestion"},
]


# --- scalar formatters -----------------------------------------------------

def format_currency(value) -> str:
    """Format a dollar amount as ``"$1,750.00"``; ``None`` -> ``"—"``."""
    if value is None:
        return DASH
    return f"${value:,.2f}"


def format_signed_currency(value) -> str:
    """Format a signed dollar amount as ``"+$120.50"`` / ``"-$50.00"``.

    ``None`` -> ``"—"``. Zero renders as ``"+$0.00"``.
    """
    if value is None:
        return DASH
    sign = "-" if value < 0 else "+"
    return f"{sign}${abs(value):,.2f}"


def format_signed_pct(fraction) -> str:
    """Format a *fraction* as a signed percent: ``0.08`` -> ``"+8.0%"``.

    ``None`` -> ``"—"``. Zero renders as ``"+0.0%"``.
    """
    if fraction is None:
        return DASH
    pct = fraction * 100
    sign = "-" if pct < 0 else "+"
    return f"{sign}{abs(pct):.1f}%"


def format_weight(fraction) -> str:
    """Format a *fraction* as an unsigned percent: ``0.30`` -> ``"30.0%"``.

    ``None`` -> ``"—"``.
    """
    if fraction is None:
        return DASH
    return f"{fraction * 100:.1f}%"


def format_vs_sector(rs) -> str:
    """Format a vs-sector RS dict like ``{"1M": 110.0, "3M": 95.0}``.

    Renders each present window as ``"<window> <value>"`` joined by ``" / "``
    (e.g. ``"1M 110.0 / 3M 95.0"``). A ``None`` value for a window renders the
    DASH placeholder for that window (e.g. ``"1M 110.0 / 3M —"``). ``None`` or an
    empty dict -> ``"—"``.
    """
    if not rs:
        return DASH
    parts = [
        f"{window} {DASH if value is None else f'{value:.1f}'}"
        for window, value in rs.items()
    ]
    return " / ".join(parts) if parts else DASH


def format_tailwind(tailwind) -> str:
    """Format a tailwind dict ``{"score": 112, "rank": 1}`` -> ``"112 (#1)"``.

    ``None`` or an empty dict -> ``"—"``.
    """
    if not tailwind:
        return DASH
    score = tailwind.get("score")
    rank = tailwind.get("rank")
    if score is None:
        return DASH
    score_str = f"{score:g}" if isinstance(score, float) else str(score)
    if rank is None:
        return score_str
    return f"{score_str} (#{rank})"


def _fmt_qty(value) -> str:
    """Format a share quantity as a plain integer-ish string; ``None`` -> ``—``."""
    if value is None:
        return DASH
    # Whole-share quantities render without a trailing ".0".
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# --- row builders ----------------------------------------------------------

def format_holdings_rows(model) -> list[dict]:
    """Build display-ready holding rows from ``model["holdings"]``.

    Each returned dict has the keys named in :data:`HOLDINGS_COLUMNS`:
    ``symbol``, ``sector``, ``quantity``, ``market_value``, ``day_pl``,
    ``total_pl``, ``vs_sector``, ``since_purchase`` — all already formatted as
    display strings. Missing values / keys render as ``"—"``.
    """
    holdings = (model or {}).get("holdings") or []
    rows = []
    for h in holdings:
        rows.append(
            {
                "symbol": h.get("symbol") or DASH,
                "sector": h.get("sector") or DASH,
                "quantity": _fmt_qty(h.get("quantity")),
                "market_value": format_currency(h.get("market_value")),
                "day_pl": format_signed_currency(h.get("day_pl")),
                "total_pl": format_signed_currency(h.get("total_pl")),
                "vs_sector": format_vs_sector(h.get("vs_sector_rs")),
                "since_purchase": format_signed_pct(h.get("since_purchase_excess")),
            }
        )
    return rows


def format_sector_rows(model) -> list[dict]:
    """Build display-ready sector rows from ``model["sectors"]``.

    Each returned dict has the keys named in :data:`SECTOR_COLUMNS`:
    ``sector``, ``weight``, ``benchmark_delta``, ``tailwind`` — all formatted as
    display strings. Missing values / keys render as ``"—"``.
    """
    sectors = (model or {}).get("sectors") or []
    rows = []
    for s in sectors:
        rows.append(
            {
                "sector": s.get("sector") or DASH,
                "weight": format_weight(s.get("weight")),
                "benchmark_delta": format_signed_pct(s.get("benchmark_delta")),
                "tailwind": format_tailwind(s.get("tailwind")),
            }
        )
    return rows


def _grade_cell(score) -> str:
    """Render a 0-4 numeric grade as its letter; ``None`` -> ``"—"``."""
    return grade_letter(score) or DASH


def format_performance_rows(cards, suggestions) -> list[dict]:
    """Build display-ready performance rows from ``evaluate_portfolio`` cards.

    Each returned dict has the keys named in :data:`PERFORMANCE_COLUMNS` —
    all already formatted as display strings. ``suggestions`` maps symbol to
    that position's suggestion list (from :func:`src.suggestions.suggest`);
    ``top_action`` shows the first suggestion's action. Rows are sorted worst
    composite first; cards without a composite sink to the end. Missing
    values / keys render as ``"—"``.
    """

    def sort_key(card):
        comp = card.get("composite")
        return (comp is None, comp if comp is not None else 0.0)

    rows = []
    for card in sorted((cards or {}).values(), key=sort_key):
        grades = card.get("grades") or {}
        comp = card.get("composite")
        sugs = (suggestions or {}).get(card.get("symbol")) or []
        rows.append(
            {
                "symbol": card.get("symbol") or DASH,
                "grade_return": _grade_cell(grades.get("return")),
                "grade_capital": _grade_cell(grades.get("capital")),
                "grade_risk": _grade_cell(grades.get("risk")),
                "grade_execution": _grade_cell(grades.get("execution")),
                "composite": (f"{comp:.1f} ({grade_letter(comp)})"
                              if comp is not None else DASH),
                "ann_return": format_signed_pct(card.get("ann_return")),
                "vs_sector": format_signed_pct(card.get("vs_sector")),
                "drawdown": format_weight(card.get("drawdown")),
                "top_action": sugs[0]["action"] if sugs else DASH,
            }
        )
    return rows
