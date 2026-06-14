"""Simulator page — What-if price sweep + IV-shock.

Calls the pure ``options_simulator`` engines over a fetched ChainSnapshot and
renders Plotly figures. Figure/transform builders are pure (unit-tested);
``render()`` wires the fetch + contract selector + tabs (Task S2/S3). Replay is
a deferred follow-up.
"""
import sys

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

SPOT_COLOR = "#ffd54f"
TARGET_COLOR = "#42a5f5"
BASE_COLOR = "#42a5f5"
SHOCK_COLOR = "#ffa726"


def _records(df):
    """Normalize a DataFrame or list-of-dicts to a list of dict rows."""
    if hasattr(df, "to_dict"):
        return df.to_dict("records")
    return list(df or [])


def _vline(x, color, dash=None):
    line = {"color": color, "width": 2}
    if dash:
        line["dash"] = dash
    return {"type": "line", "yref": "paper", "y0": 0, "y1": 1,
            "xref": "x", "x0": x, "x1": x, "line": line}


def whatif_figure(df, spot, target_s=None):
    """Plotly curve of underlying price (S) vs position theo price."""
    rows = _records(df)
    xs = [r["S"] for r in rows]
    ys = [r["theo_price"] for r in rows]
    shapes = [
        {"type": "line", "xref": "paper", "x0": 0, "x1": 1, "yref": "y",
         "y0": 0, "y1": 0, "line": {"color": "#888", "width": 1, "dash": "dash"}},
        _vline(spot, SPOT_COLOR),
    ]
    if target_s is not None:
        shapes.append(_vline(target_s, TARGET_COLOR, dash="dash"))
    return {
        "data": [{"type": "scatter", "mode": "lines", "x": xs, "y": ys,
                  "line": {"color": "#66bb6a"}, "name": "Theo"}],
        "layout": {"title": "What-if: price sweep",
                   "xaxis": {"title": "Underlying"}, "yaxis": {"title": "Theo price"},
                   "shapes": shapes, "showlegend": False,
                   "margin": {"l": 60, "r": 20, "t": 40, "b": 40}},
    }


def ivshock_figure(base, shock, mult=1.5):
    """Grouped base-vs-shock bars across the key metrics."""
    cats = ["Price", "Delta", "Gamma×100", "Theta", "Vega"]

    def vals(row):
        return [row.get("theo_price", 0), row.get("delta", 0),
                (row.get("gamma", 0) or 0) * 100, row.get("theta", 0), row.get("vega", 0)]

    return {
        "data": [
            {"type": "bar", "name": "base (×1.0)", "x": cats, "y": vals(base),
             "marker": {"color": BASE_COLOR}},
            {"type": "bar", "name": f"shock (×{mult:g})", "x": cats, "y": vals(shock),
             "marker": {"color": SHOCK_COLOR}},
        ],
        "layout": {"title": "IV shock", "barmode": "group",
                   "xaxis": {"title": "", "categoryarray": cats},
                   "yaxis": {"title": "Value"},
                   "margin": {"l": 60, "r": 20, "t": 40, "b": 40}},
    }


def expiries_of(snapshot):
    """Sorted unique expiries (as ISO strings) in the snapshot."""
    return sorted({str(c.expiry) for c in getattr(snapshot, "contracts", []) or []})


def strikes_of(snapshot, expiry, kind):
    """Sorted unique strikes for an expiry + kind (call/put)."""
    out = {c.strike for c in getattr(snapshot, "contracts", []) or []
           if str(c.expiry) == str(expiry) and c.kind == kind}
    return sorted(out)


def render():
    from nicegui import ui
    ui.label("Simulator").classes("text-h5")
    ui.label("What-if price sweep and IV-shock.").classes("opacity-70")
    ui.label("(render wired in Task S2/S3)").classes("text-sm opacity-50")
