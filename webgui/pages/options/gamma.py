"""Gamma page — GEX / Charm / DEX / Vanna exposure + intraday heatmap.

Calls ``gamma_tool.GammaEngine`` (pure compute over a live option chain) and
renders horizontal bar charts + an intraday strike×time heatmap with NiceGUI
``ui.plotly``. Figure/transform builders are pure (unit-tested); ``render()``
wires the controls (Task G2/G3).
"""
import sys

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

POS_COLOR = "#66bb6a"
NEG_COLOR = "#ef5350"
SPOT_COLOR = "#ffd54f"
FLIP_COLOR = "#42a5f5"
WALL_COLOR = "#b39ddb"


def bars_from_gex(data, spot, pct=0.02):
    """Per-strike net exposure within ±pct of spot, ascending by strike."""
    gex = (data or {}).get("gex") or {}
    lo, hi = spot * (1 - pct), spot * (1 + pct)
    strikes, nets, colors, hovers = [], [], [], []
    for strike in sorted(gex):
        if not (lo <= strike <= hi):
            continue
        cell = gex[strike] or {}
        net = cell.get("net", 0.0)
        strikes.append(strike)
        nets.append(net)
        colors.append(POS_COLOR if net >= 0 else NEG_COLOR)
        hovers.append(f"{strike:g}: net {net:,.0f} "
                      f"(C {cell.get('call', 0):,.0f} / P {cell.get('put', 0):,.0f})")
    return {"strikes": strikes, "nets": nets, "colors": colors, "hovers": hovers}


def _hline(value, color, dash=None):
    line = {"color": color, "width": 2}
    if dash:
        line["dash"] = dash
    return {"type": "line", "xref": "paper", "x0": 0, "x1": 1,
            "yref": "y", "y0": value, "y1": value, "line": line}


def bar_figure(data, spot, view="GEX", walls=None, flip=None, pct=0.02):
    """Plotly horizontal-bar figure dict for one view."""
    b = bars_from_gex(data, spot, pct)
    shapes = [_hline(spot, SPOT_COLOR)]
    if flip is not None:
        shapes.append(_hline(flip, FLIP_COLOR, dash="dash"))
    for w in (walls or []):
        shapes.append(_hline(w, WALL_COLOR, dash="dot"))
    return {
        "data": [{
            "type": "bar", "orientation": "h",
            "x": b["nets"], "y": b["strikes"],
            "marker": {"color": b["colors"]},
            "hovertext": b["hovers"], "hoverinfo": "text",
        }],
        "layout": {
            "title": f"{view} by strike",
            "xaxis": {"title": view, "zeroline": True},
            "yaxis": {"title": "Strike"},
            "shapes": shapes,
            "margin": {"l": 60, "r": 20, "t": 40, "b": 40},
            "showlegend": False,
        },
    }


def heatmap_matrix(rows):
    """(x=times, y=strikes, z=[y][x]) from gex_history rows.

    Each row is (ts, spot, flip, top_pos, top_neg, net_total, grid_dict) where
    grid_dict maps strike->value.
    """
    if not rows:
        return {"x": [], "y": [], "z": []}
    x = [r[0] for r in rows]
    grids = [r[6] or {} for r in rows]
    strikes = sorted({s for g in grids for s in g})
    z = [[g.get(s) for g in grids] for s in strikes]
    return {"x": x, "y": strikes, "z": z}


def heatmap_figure(rows, view="GEX"):
    m = heatmap_matrix(rows)
    return {
        "data": [{
            "type": "heatmap", "x": m["x"], "y": m["y"], "z": m["z"],
            "colorscale": "RdYlGn", "zmid": 0,
        }],
        "layout": {
            "title": f"{view} intraday (strike × time)",
            "xaxis": {"title": "Time"}, "yaxis": {"title": "Strike"},
            "margin": {"l": 60, "r": 20, "t": 40, "b": 40},
        },
    }


def summary_text(summary, view):
    s = summary or {}
    parts = [f"{view}"]
    if s.get("spot") is not None:
        parts.append(f"spot {s['spot']:,.2f}")
    if s.get("strike_count") is not None:
        parts.append(f"{s['strike_count']} strikes")
    if s.get("net_total") is not None:
        parts.append(f"net {s['net_total']:,.0f}")
    if s.get("flip") is not None:
        parts.append(f"flip {s['flip']:.1f}")
    return "  ·  ".join(parts)


def render():
    from nicegui import ui
    ui.label("Gamma").classes("text-h5")
    ui.label("GEX / Charm / DEX / Vanna exposure + intraday heatmap.").classes("opacity-70")
    ui.label("(render wired in Task G2)").classes("text-sm opacity-50")
