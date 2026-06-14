"""Options strategy Calculator page.

Faithful port of the Tk calculator's two visuals: a colored summary-tile panel
(``calc_summary``) and a colored P&L heatmap grid (``calc_spread_pnl``: price ×
eval-date pairs of $ and %). Math lives in ``options_calculator``; this module
marshals + colors. Pure transforms (banding, grid mapping, formatting) are
unit-tested; ``render()`` wires the form + visuals.
"""
import math

# (key, label, option_type, side) per strategy — mirrors dashboard leg inputs.
LEG_SPECS = {
    "PCS": [("short_put", "Short Put (write)", "put", "short"),
            ("long_put", "Long Put (buy)", "put", "long")],
    "CCS": [("short_call", "Short Call (write)", "call", "short"),
            ("long_call", "Long Call (buy)", "call", "long")],
    "IC": [("short_put", "Short Put (write)", "put", "short"),
           ("long_put", "Long Put (buy)", "put", "long"),
           ("short_call", "Short Call (write)", "call", "short"),
           ("long_call", "Long Call (buy)", "call", "long")],
    "LONG_PUT": [("long_put", "Long Put (buy)", "put", "long")],
    "NAKED_PUT": [("short_put", "Short Put (sell)", "put", "short")],
    "LONG_CALL": [("long_call", "Long Call (buy)", "call", "long")],
    "NAKED_CALL": [("short_call", "Short Call (sell)", "call", "short")],
}


def _band(frac):
    """Map a 0..1 magnitude fraction to a 1..5 shade band."""
    frac = max(0.0, min(1.0, frac))
    return max(1, min(5, math.ceil(frac / 0.2)))


def pnl_cell_class(value, g_max, g_min):
    """CSS class for a P&L cell: p1..p5 (profit), l1..l5 (loss), or neutral.

    Shade intensity is relative to the grid's global max-profit / max-loss.
    """
    if not isinstance(value, (int, float)) or value == 0:
        return "neutral"
    if value > 0:
        frac = value / g_max if isinstance(g_max, (int, float)) and g_max > 0 else 0.0
        return f"p{_band(frac)}"
    frac = value / g_min if isinstance(g_min, (int, float)) and g_min < 0 else 0.0
    return f"l{_band(frac)}"


def grid_extremes(pnl_data):
    """(global_max, global_min) over every P&L value in the grid."""
    vals = [p for r in (pnl_data or []) for p in (r.get("pnl") or [])
            if isinstance(p, (int, float))]
    if not vals:
        return 0.0, 0.0
    return max(vals), min(vals)


def grid_rows(pnl_data):
    """[{price, cells:[{pnl, pnl_pct}, ...]}, ...] from calc_spread_pnl output."""
    rows = []
    for r in pnl_data or []:
        pnls = r.get("pnl") or []
        pcts = r.get("pnl_pct") or []
        cells = [{"pnl": pnls[i] if i < len(pnls) else None,
                  "pnl_pct": pcts[i] if i < len(pcts) else None}
                 for i in range(len(pnls))]
        rows.append({"price": r.get("price"), "cells": cells})
    return rows


def eval_date_labels(dates):
    return [d.strftime("%m/%d") if hasattr(d, "strftime") else str(d) for d in dates or []]


def fmt_dollar(v):
    return f"{v:+,.0f}" if isinstance(v, (int, float)) else "—"


def fmt_pct(v):
    return f"{v:+.1f}%" if isinstance(v, (int, float)) else "—"


def render():
    ui_placeholder()


def ui_placeholder():
    from nicegui import ui
    ui.label("Calculator").classes("text-h5")
    ui.label("Strategy P&L summary tiles and a colored P&L heatmap grid.").classes("opacity-70")
    ui.label("(render wired in Task B2)").classes("text-sm opacity-50")
