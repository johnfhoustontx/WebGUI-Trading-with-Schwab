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
from src.grouping import group_positions, group_value

# The em-dash placeholder used everywhere a value is missing / not applicable.
DASH = "—"

# Holdings table columns (order = left-to-right display order).
HOLDINGS_COLUMNS = [
    {"key": "symbol", "header": "Symbol"},
    {"key": "sector", "header": "Sector"},
    {"key": "quantity", "header": "Qty"},
    {"key": "market_value", "header": "Market Value"},
    {"key": "day_pl", "header": "Day P/L"},
    {"key": "total_pl", "header": "Unrealized P/L"},
    {"key": "vs_sector", "header": "vs Sector (RS)"},
    {"key": "since_purchase", "header": "Since Purchase"},
]

# Sectors table columns.
SECTOR_COLUMNS = [
    {"key": "sector", "header": "Sector"},
    {"key": "weight", "header": "Weight"},
    {"key": "benchmark_delta", "header": "vs Benchmark"},
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
    """Format a *fraction* as a signed percent: ``0.08`` -> ``"+8.00%"``.

    ``None`` -> ``"—"``. Zero renders as ``"+0.00%"``. Two places, like every
    percentage on a screen (the webgui's ``pages/fmt.py`` states the rule).
    """
    if fraction is None:
        return DASH
    pct = fraction * 100
    sign = "-" if pct < 0 else "+"
    return f"{sign}{abs(pct):.2f}%"


def format_weight(fraction) -> str:
    """Format a *fraction* as an unsigned percent: ``0.30`` -> ``"30.00%"``.

    ``None`` -> ``"—"``.
    """
    if fraction is None:
        return DASH
    return f"{fraction * 100:.2f}%"


def format_vs_sector(rs) -> str:
    """Format a vs-sector RS dict like ``{"1M": 110.0, "3M": 95.0}``.

    Renders each present window as ``"<window> <value>"`` joined by ``" / "``
    (e.g. ``"1M 110.00 / 3M 95.00"``). A ``None`` value for a window renders the
    DASH placeholder for that window (e.g. ``"1M 110.00 / 3M —"``). ``None`` or an
    empty dict -> ``"—"``.
    """
    if not rs:
        return DASH
    parts = [
        f"{window} {DASH if value is None else f'{value:.2f}'}"
        for window, value in rs.items()
    ]
    return " / ".join(parts) if parts else DASH


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
    ``sector``, ``weight``, ``benchmark_delta`` — all formatted as
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


# --- the Holdings tab, by account --------------------------------------------

# The Options table under each account. One row per option GROUP: a vertical
# spread is one row (both legs), anything unpaired is its own row.
OPTION_COLUMNS = [
    {"key": "underlying", "header": "Symbol"},
    {"key": "strategy", "header": "Strategy"},
    {"key": "strikes", "header": "Strikes"},
    {"key": "expiry", "header": "Expiration"},
    {"key": "contracts", "header": "Contracts"},
    {"key": "entry", "header": "Entry"},
    {"key": "mark", "header": "Mark"},
    {"key": "market_value", "header": "Market Value"},
    {"key": "day_pl", "header": "Day P/L"},
    {"key": "total_pl", "header": "Unrealized P/L"},
]


def pl_tone(value) -> str:
    """``"pos"`` / ``"neg"`` / ``"flat"`` for a dollar P/L: the sign the page
    colours the cell by. Under half a cent prints ``+$0.00`` and is flat."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "flat"
    if v >= 0.005:
        return "pos"
    if v <= -0.005:
        return "neg"
    return "flat"


def _price(value) -> str:
    """A per-share option price, two places; ``None`` -> ``"—"``."""
    if value is None:
        return DASH
    return f"{value:,.2f}"


def _strikes_cell(group) -> str:
    if group.get("kind") == "spread":
        return (f"Short {group['short_strike']:,.2f} / "
                f"Long {group['long_strike']:,.2f}")
    strikes = group.get("strikes") or ()
    return f"{strikes[0]:,.2f}" if strikes else DASH


def _option_row(account_key, i, group, today=None) -> dict:
    v = group_value(group)
    return {
        "day_pl_tone": pl_tone(v["day_pl"]),
        "total_pl_tone": pl_tone(v["total_pl"]),
        # 0 DTE: the page highlights the date. ``today`` is the caller's
        # (Central) date; with none, nothing is flagged.
        "expires_today": bool(today) and group.get("expiry") == today,
        "row_id": f"{account_key}:opt:{i}",
        "underlying": group.get("underlying") or DASH,
        "strategy": group.get("label") or DASH,
        "strikes": _strikes_cell(group),
        "expiry": group.get("expiry") or DASH,
        "contracts": _fmt_qty(abs(float(group.get("contracts") or 0))),
        "entry": _price(v["entry"]),
        "mark": _price(v["mark"]),
        "market_value": format_signed_currency(v["market_value"]),
        "day_pl": format_signed_currency(v["day_pl"]),
        "total_pl": format_signed_currency(v["total_pl"]),
    }


def _share_row(account_key, pos, by_symbol) -> dict:
    """A Shares (or Other) row in the Holdings shape. The sector comparisons are
    per SYMBOL across the whole book, so they come from the merged holding."""
    h = by_symbol.get(pos.get("symbol")) or {}
    return {
        "day_pl_tone": pl_tone(pos.get("day_pl")),
        "total_pl_tone": pl_tone(pos.get("total_pl")),
        "row_id": f"{account_key}:{pos.get('symbol')}",
        "symbol": pos.get("symbol") or DASH,
        "sector": h.get("sector") or DASH,
        "quantity": _fmt_qty(pos.get("quantity")),
        "market_value": format_currency(pos.get("market_value")),
        "day_pl": format_signed_currency(pos.get("day_pl")),
        "total_pl": format_signed_currency(pos.get("total_pl")),
        "vs_sector": format_vs_sector(h.get("vs_sector_rs")),
        "since_purchase": format_signed_pct(h.get("since_purchase_excess")),
    }


def format_account_groups(model, today=None) -> list[dict]:
    """One section per account for the Holdings tab.

    Reads ``model["accounts"]`` (``[{"label", "positions"}]``, the service's
    per-account fetch); a model without it (an older build, or the per-account
    fetch failed) gets ONE section, "All accounts", over the merged holdings,
    so the tab is never empty while there are positions. Each section:

    ``{"account", "market_value", "day_pl", "total_pl", "shares_rows",
    "option_rows", "other_rows"}`` - the totals over every position in the
    account, the share rows in :data:`HOLDINGS_COLUMNS` shape, the option
    groups in :data:`OPTION_COLUMNS` shape (a vertical spread is one row).
    Every cell is a display string. Each P/L also carries its sign as
    ``<field>_tone`` (:func:`pl_tone`), and each option row ``expires_today``
    when its expiration is ``today`` (ISO, the caller's Central date).
    """
    model = model or {}
    holdings = model.get("holdings") or []
    by_symbol = {h.get("symbol"): h for h in holdings}
    accounts = model.get("accounts")
    if not accounts:
        accounts = [{"label": "All accounts", "positions": holdings}] if holdings else []

    out = []
    for n, acct in enumerate(accounts):
        positions = acct.get("positions") or []
        key = f"a{n}"
        groups = group_positions(positions)

        def _sum(field):
            return sum(float(p.get(field) or 0.0) for p in positions)

        out.append({
            "account": acct.get("label") or f"Account {n + 1}",
            # A value, not a gain: no "+" (a net-short account still shows "-").
            "market_value": (format_currency(_sum("market_value"))
                             if _sum("market_value") >= 0
                             else format_signed_currency(_sum("market_value"))),
            "day_pl": format_signed_currency(_sum("day_pl")),
            "total_pl": format_signed_currency(_sum("total_pl")),
            "day_pl_tone": pl_tone(_sum("day_pl")),
            "total_pl_tone": pl_tone(_sum("total_pl")),
            "shares_rows": [_share_row(key, p, by_symbol) for p in groups["shares"]],
            "option_rows": [_option_row(key, i, g, today)
                            for i, g in enumerate(groups["options"])],
            "other_rows": [_share_row(key, p, by_symbol) for p in groups["other"]],
        })
    return out
