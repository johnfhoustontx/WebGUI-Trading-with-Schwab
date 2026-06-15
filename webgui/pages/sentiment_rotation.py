"""Sector Rotation page — RRG-vs-SPY assessment (under Sentiment).

Thin NiceGUI layer over the copied ``sector_rotation_assessment`` engine.
Pure builders here are unit-tested; ``render()`` (Task 2) wires widgets.
Data is fairly static: cached module-level, manual Refresh only.
"""
import sys

from repo_paths import SENTIMENT

if str(SENTIMENT) not in sys.path:
    sys.path.insert(0, str(SENTIMENT))

import sector_rotation_assessment as rotation_tool  # noqa: E402

CLR_GREEN = "#66bb6a"
CLR_RED = "#ef5350"
CLR_YELLOW = "#ffd54f"
CLR_CYAN = "#3fb6c7"
CLR_FLAT = "#9e9e9e"

_QUAD_COLOR = {"Leading": CLR_GREEN, "Improving": CLR_CYAN,
               "Weakening": CLR_YELLOW, "Lagging": CLR_RED}


def quadrant_color(q):
    return _QUAD_COLOR.get(q, CLR_FLAT)


def _regime_color(regime):
    return {"Risk-ON": CLR_GREEN, "Risk-OFF": CLR_RED}.get(regime, CLR_YELLOW)


def headline_parts(a):
    """(regime, color, text, detail) from an assessment dict."""
    h = a.get("headline") or {}
    regime = h.get("regime", "—")
    text = h.get("text", "")
    spread = h.get("spread")
    if spread is not None:
        detail = (f"cyclical RS-Mom {h.get('cyclical_mom_mean', 0):.2f} vs "
                  f"defensive {h.get('defensive_mom_mean', 0):.2f} "
                  f"(spread {spread:+.1f}; threshold ±{rotation_tool.RISK_THRESHOLD})")
    else:
        detail = ""
    return regime, _regime_color(regime), text, detail


def side_rows(a, side_key, weights):
    """([{name, etf, quadrant, weight}], total_weight) for rotating_from/into."""
    rows = []
    total = 0.0
    for s in a.get(side_key) or []:
        w = float((weights or {}).get(s.get("etf"), 0.0) or 0.0)
        total += w
        rows.append({"name": s.get("name"), "etf": s.get("etf"),
                     "quadrant": s.get("quadrant"), "weight": w})
    return rows, total


def rotation_rows(a):
    """Quadrant-map rows (already rs_momentum-desc from the engine), + color."""
    out = []
    for s in a.get("sectors") or []:
        out.append({**s, "color": quadrant_color(s.get("quadrant"))})
    return out


def rrg_scatter_figure(a):
    """Plotly RRG scatter: x=RS-Ratio, y=RS-Momentum, dot per sector, 100/100 lines."""
    secs = a.get("sectors") or []
    xs = [s.get("rs_ratio") for s in secs]
    ys = [s.get("rs_momentum") for s in secs]
    colors = [quadrant_color(s.get("quadrant")) for s in secs]
    labels = [s.get("etf") for s in secs]
    line = {"color": "rgba(255,255,255,0.25)", "width": 1}
    return {
        "data": [{
            "type": "scatter", "mode": "markers+text",
            "x": xs, "y": ys, "text": labels, "textposition": "top center",
            "marker": {"size": 12, "color": colors},
            "hovertext": [f"{s.get('name')} — {s.get('quadrant')}" for s in secs],
            "hoverinfo": "text",
        }],
        "layout": {
            "margin": {"l": 44, "r": 12, "t": 8, "b": 36}, "height": 360,
            "template": "plotly_dark",
            "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
            "xaxis": {"title": "RS-Ratio", "zeroline": False,
                      "gridcolor": "rgba(255,255,255,0.06)"},
            "yaxis": {"title": "RS-Momentum", "zeroline": False,
                      "gridcolor": "rgba(255,255,255,0.06)"},
            "shapes": [
                {"type": "line", "xref": "x", "yref": "paper", "x0": 100, "x1": 100,
                 "y0": 0, "y1": 1, "line": line},
                {"type": "line", "xref": "paper", "yref": "y", "x0": 0, "x1": 1,
                 "y0": 100, "y1": 100, "line": line},
            ],
        },
    }
