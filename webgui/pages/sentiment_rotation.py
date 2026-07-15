"""Sector Rotation page — RRG-vs-SPY assessment (under Sentiment).

Tier-3 reader: this page holds **no engine calls and no app ``scoring`` import**.
The assessment, S&P weights and risk threshold are computed in
``services/sentiment_svc`` (the only process where ``import scoring`` resolves to
sentiment's package rather than the options-scanner ``scoring.py`` — the
documented cross-app collision). The service caches them; this page only
**formats** them. Cache view read:

* ``sentiment:rotation`` → ``{"assessment", "weights", "risk_threshold", "error"}``
  (see ``services/sentiment_svc/handlers.refresh_rotation``).

Pure builders (``quadrant_color``, ``headline_parts``, ``side_rows``,
``rotation_rows``, ``rrg_scatter_figure``) are unit-tested. ``render()`` wires
widgets, a Refresh button that enqueues a ``cmd:sentiment`` ``refresh_rotation``
command, and a fetch-free version-poll ``ui.timer`` that repaints when the bus
cache version changes.
"""
import bus_client
from pages.options.theme import BTN_3D
from pages.ui_guard import guard, guard_async  # noqa: F401

CLR_GREEN = "#66bb6a"
CLR_RED = "#ef5350"
CLR_YELLOW = "#ffd54f"
CLR_CYAN = "#3fb6c7"
CLR_FLAT = "#9e9e9e"

# LOCAL Tailwind text-class map (Phase 5) — mirrors this page's OWN 5-color
# palette EXACTLY (same local set as sentiment.py; intentionally NOT the theme
# tokens — yellow/cyan have no token + the flat differs). The hex `*_color`
# helpers feed the Highcharts RRG figure; the `*_class` helpers feed `.classes()`.
TXT_G = "text-[#66bb6a]"
TXT_R = "text-[#ef5350]"
TXT_Y = "text-[#ffd54f]"
TXT_CY = "text-[#3fb6c7]"
TXT_FLAT = "text-[#9e9e9e]"
# Remove-set for the reactive in-place `headline_lbl` recolor (covers every
# class it can apply, so colors don't stack across the page's ~2s auto-refresh).
SENT_TEXT_CLASSES = " ".join([TXT_G, TXT_R, TXT_Y, TXT_CY, TXT_FLAT])
_HEX_TO_TXT = {CLR_GREEN: TXT_G, CLR_RED: TXT_R, CLR_YELLOW: TXT_Y,
               CLR_CYAN: TXT_CY, CLR_FLAT: TXT_FLAT}

# Fallback when the service-supplied risk threshold is absent (cold cache).
DEFAULT_RISK_THRESHOLD = 1.5

_QUAD_COLOR = {"Leading": CLR_GREEN, "Improving": CLR_CYAN,
               "Weakening": CLR_YELLOW, "Lagging": CLR_RED}


def quadrant_color(q):
    return _QUAD_COLOR.get(q, CLR_FLAT)


def quadrant_text_class(q):
    return _HEX_TO_TXT[quadrant_color(q)]


def _regime_color(regime):
    return {"Risk-ON": CLR_GREEN, "Risk-OFF": CLR_RED}.get(regime, CLR_YELLOW)


def regime_text_class(regime):
    return _HEX_TO_TXT[_regime_color(regime)]


def headline_parts(a, risk_threshold=DEFAULT_RISK_THRESHOLD):
    """(regime, color, text, detail) from an assessment dict.

    ``risk_threshold`` arrives from the service-cached value (the engine's
    ``RISK_THRESHOLD``); it falls back to ``DEFAULT_RISK_THRESHOLD`` so the page
    never needs to import the engine."""
    h = a.get("headline") or {}
    regime = h.get("regime", "—")
    text = h.get("text", "")
    spread = h.get("spread")
    rt = risk_threshold if risk_threshold is not None else DEFAULT_RISK_THRESHOLD
    if spread is not None:
        detail = (f"cyclical RS-Mom {h.get('cyclical_mom_mean', 0):.2f} vs "
                  f"defensive {h.get('defensive_mom_mean', 0):.2f} "
                  f"(spread {spread:+.1f}; threshold ±{rt})")
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


def _hex_to_rgba(hex_color, alpha):
    """'#66bb6a' + 0.28 -> 'rgba(102, 187, 106, 0.28)'."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _sector_trace(sec):
    """One RRG series for a sector: a faded trail line (spline) + a single bright
    head marker (the current point), labeled with the ETF. Built from the sampled
    tail; falls back to a single head point when the sector has no tail.

    Returned as a Highcharts series: the series ``color`` is the faded trail-line
    rgba; only the head point carries an enabled marker (bright quadrant fill) and
    a dataLabel."""
    tail = sec.get("tail") or []
    color = quadrant_color(sec.get("quadrant"))
    if tail:
        xs = [p["rs_ratio"] for p in tail]
        ys = [p["rs_momentum"] for p in tail]
    else:
        r, m = sec.get("rs_ratio"), sec.get("rs_momentum")
        if r is None or m is None:
            return None
        xs, ys = [r], [m]
    n = len(xs)
    data = []
    for i, (x, y) in enumerate(zip(xs, ys)):
        if i == n - 1:                                   # head = current point
            data.append({
                "x": x, "y": y,
                "marker": {"enabled": True, "radius": 7,
                           "fillColor": color, "lineColor": color},
                "dataLabels": {"enabled": True, "format": sec.get("etf") or "",
                               "verticalAlign": "bottom", "y": -6,
                               "style": {"fontSize": "10px", "color": "#e6e6e6",
                                         "textOutline": "none"}},
            })
        else:                                            # trail point: no marker
            data.append({"x": x, "y": y, "marker": {"enabled": False}})
    return {
        "type": "spline",
        "name": f"{sec.get('name')} ({sec.get('etf')})",
        "color": _hex_to_rgba(color, 0.4),               # faded trail line
        "lineWidth": 1.6,
        "marker": {"enabled": False},
        "data": data,
        "showInLegend": False,
        "custom": {"quadrant": sec.get("quadrant")},
        "tooltip": {"headerFormat": "",
                    "pointFormat": (f"{sec.get('name')} ({sec.get('etf')}) — "
                                    f"{sec.get('quadrant')}<br>RS-Ratio "
                                    "{point.x:.2f} · RS-Mom {point.y:.2f}")},
    }


def rrg_scatter_figure(a):
    """Highcharts RRG: one spline series per sector (faded trail + single head
    dot), 100/100 crosshair plotLines, and native hover-isolation (hovering one
    sector dims the rest via ``states.inactive``). Series order matches the
    sectors order."""
    secs = a.get("sectors") or []
    series = [t for t in (_sector_trace(s) for s in secs) if t is not None]
    cross = {"value": 100, "color": "rgba(255,255,255,0.25)", "width": 1, "zIndex": 1}
    axis = {"gridLineColor": "rgba(255,255,255,0.06)",
            "lineColor": "rgba(255,255,255,0.15)",
            "labels": {"style": {"color": "#bdbdbd"}}}
    return {
        "chart": {"type": "spline", "backgroundColor": "transparent",
                  "height": 560, "spacing": [8, 12, 36, 8]},
        "title": {"text": None},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        # NB: the per-sector trails are 2D paths (x = RS-Ratio wanders, not
        # monotonic), so Highcharts logs advisory warning #15 (unsorted data).
        # Benign — splines render in data order, which is the temporal trail.
        "legend": {"enabled": False},
        "xAxis": {**axis, "title": {"text": "RS-Ratio", "style": {"color": "#bdbdbd"}},
                  "plotLines": [cross]},
        "yAxis": {**axis, "title": {"text": "RS-Momentum", "style": {"color": "#bdbdbd"}},
                  "plotLines": [cross]},
        # Native hover-isolation: hovering one series dims all others.
        "plotOptions": {"series": {"states": {"inactive": {"opacity": 0.12}}}},
        "series": series,
    }


def render():
    from nicegui import ui

    state = {"ver": None}

    with ui.row().classes("items-center gap-3 w-full"):
        ui.label("Sector Rotation").classes("text-h6")
        ui.label("RRG vs SPY").classes("opacity-60 text-sm")
        as_of = ui.label("").classes("opacity-70 text-sm")
        ui.space()
        ui.button("Refresh", icon="refresh", color=None,
                  on_click=lambda: _request_refresh()).props("no-caps").classes(BTN_3D)

    headline_lbl = ui.label("").classes("text-subtitle1 text-bold")
    detail_lbl = ui.label("").classes("opacity-70 text-sm")
    msg_lbl = ui.label("").classes("text-warning text-sm")
    # Top: Full Quadrant Map (left) + ROTATING FROM/INTO (right).
    with ui.row().classes("w-full no-wrap gap-6 items-start q-mt-md"):
        with ui.column().classes("flex-[1.4] min-w-0"):
            ui.label("Full Quadrant Map (sorted by RS-Momentum)").classes("text-subtitle2")
            table_box = ui.column().classes("w-full q-gutter-none")
        with ui.column().classes("flex-1 min-w-0"):
            cols_box = ui.row().classes("no-wrap items-start gap-8 w-full")
    ui.label("Pairing is ordinal — strongest relative-selling vs strongest "
             "relative-buying pressure, not literal cash flow.").classes("opacity-50 text-xs q-mt-sm")
    # The RRG chart now lives on its own tab (/sentiment/rrg, pages.sentiment_rrg),
    # which reads the same sentiment:rotation cache view.

    QCOLS = [("name", "Sector", 150), ("etf", "ETF", 55), ("rs_ratio", "RS-Ratio", 90),
             ("rs_momentum", "RS-Mom", 90), ("quadrant", "Quadrant", 110),
             ("direction", "Dir", 60)]
    # Single source of truth for the quadrant-map column widths so the header
    # (driven by QCOLS) and the data-row cells never drift.
    QCOL_W = {field: w for field, _label, w in QCOLS}

    def _render(a, weights, risk_threshold):
        regime, color, text, detail = headline_parts(a, risk_threshold)
        as_of.text = f"as of {a.get('date')}"
        headline_lbl.text = f"{regime} — {text}"
        headline_lbl.classes(remove=SENT_TEXT_CLASSES, add=regime_text_class(regime))
        detail_lbl.text = detail
        msg_lbl.text = ""
        cols_box.clear()
        with cols_box:
            for side, title, tcls in (("rotating_from", "ROTATING FROM", TXT_R),
                                      ("rotating_into", "ROTATING INTO", TXT_G)):
                rows, total = side_rows(a, side, weights)
                with ui.column().classes("items-start"):
                    ui.label(f"{title}  ·  {total:.0f}% of S&P").classes(f"text-bold text-sm {tcls}")
                    for r in rows:
                        with ui.row().classes("items-center no-wrap gap-1 text-sm"):
                            ui.label(str(r["name"] or ""))
                            ui.label(f"({r['quadrant']})").classes(
                                quadrant_text_class(r['quadrant']))
                            ui.label(f"{r['weight']:.1f}%" if r['weight'] else "").classes("opacity-70")
        table_box.clear()
        with table_box:
            with ui.row().classes("items-center w-full no-wrap gap-2 opacity-60 text-xs"):
                for _f, hdr, w in QCOLS:
                    ui.label(hdr).classes(f"w-[{w}px]")
            for r in rotation_rows(a):
                # row-level color sets the default text color for all cells
                with ui.row().classes(
                        "items-center w-full no-wrap gap-2 text-sm "
                        + quadrant_text_class(r.get("quadrant"))):
                    ui.label(str(r.get("name") or "")).classes(f"w-[{QCOL_W['name']}px]")
                    ui.label(str(r.get("etf") or "")).classes(f"w-[{QCOL_W['etf']}px]")
                    ui.label(f"{r.get('rs_ratio'):.2f}").classes(f"w-[{QCOL_W['rs_ratio']}px]")
                    ui.label(f"{r.get('rs_momentum'):.2f}").classes(f"w-[{QCOL_W['rs_momentum']}px]")
                    ui.label(str(r.get("quadrant") or "")).classes(f"w-[{QCOL_W['quadrant']}px]")
                    ui.label(str(r.get("direction") or "")).classes(f"w-[{QCOL_W['direction']}px]")

    @guard
    def _apply():
        rot = bus_client.read("sentiment:rotation") or {}
        a = rot.get("assessment")
        weights = rot.get("weights") or {}
        rt = rot.get("risk_threshold")
        err = rot.get("error")
        if a:
            _render(a, weights, rt)
        elif err:
            as_of.text = ""
            headline_lbl.text = ""
            detail_lbl.text = ""
            msg_lbl.text = err
        else:
            as_of.text = ""
            headline_lbl.text = "Waiting for sentiment service…"
            detail_lbl.text = ""
            msg_lbl.text = ""

    @guard
    def _request_refresh():
        bus_client.request("sentiment", {"type": "refresh_rotation"})
        ui.notify("Refresh requested")

    @guard
    def _maybe_repaint():
        # Fetch-free: repaint only when the bus cache version changes.
        ver = bus_client.read_version("sentiment:rotation")
        if ver == state["ver"]:
            return
        state["ver"] = ver
        _apply()

    state["ver"] = bus_client.read_version("sentiment:rotation")
    _apply()
    ui.timer(2.0, _maybe_repaint)
