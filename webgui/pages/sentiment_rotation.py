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
from pages import busy as _busy
from pages import rotation_view as V
from pages.options.theme import (
    ROTATION_FONT_HEAD_HTML, ROTATION_TOKENS as _T,
)
from pages.ui_guard import guard, guard_async  # noqa: F401

# Repeated class strings for the verdict strip, named once so the three panels
# cannot drift. The panel's hairline is a 1px RING, not a border: the strip is a
# flex-wrap row with a 1px gap, so ring + gap reads as a single shared rule and
# nothing orphans when a panel wraps to the next line. The ring is written rgba
# because a `shadow-[…]` arbitrary does NOT generate from a hex (documented JIT
# gotcha), and with no spaces because a Tailwind arbitrary value cannot hold one.
_PANEL = (f"flex-[1_1_300px] min-w-0 px-[30px] py-6 "
          f"shadow-[0_0_0_1px_{V.rgba('hair')}]")
_EYEBROW = (f"{_T['RT_MONO']} {V.NT['eyebrow']} text-[11px] tracking-[.16em] "
            "uppercase leading-none")
_FIELD = (f"{_T['RT_MONO']} {V.NT['label']} text-[10px] tracking-[.16em] "
          "uppercase leading-none whitespace-nowrap")
_FIGURE = (f"{_T['RT_MONO']} {V.NT['value']} text-[15px] leading-none "
           "whitespace-nowrap")
# Every dot fill the regime indicator can take, for the reactive `remove=`.
_DOT_CLASSES = " ".join(dict.fromkeys(t["dot"] for t in V.TONE.values()))

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


# Corner labels naming the four RRG quadrants (replaces the old caption line on
# the RRG page). Implemented as TRANSPARENT xAxis plotBands — one band left of
# 100 and one right of 100, twice each — whose labels anchor to the four corners
# of the plot area. Bands render UNDER the series and the text is faint, so the
# labels are visible but unobtrusive.
_QUAD_LABEL_STYLE = {"color": "rgba(255,255,255,0.30)", "fontSize": "11px",
                     "fontWeight": "bold", "letterSpacing": "2px",
                     "textTransform": "uppercase"}


def quadrant_label_bands():
    def band(right, top, text):
        return {
            "from": 100 if right else -1e9,
            "to": 1e9 if right else 100,
            "color": "rgba(0,0,0,0)",           # invisible band — label only
            "zIndex": 0,
            "label": {"text": text,
                      "align": "right" if right else "left",
                      "textAlign": "right" if right else "left",
                      "verticalAlign": "top" if top else "bottom",
                      "x": -10 if right else 10,
                      "y": 18 if top else -12,
                      "style": dict(_QUAD_LABEL_STYLE)},
        }
    return [band(True, True, "Leading"), band(True, False, "Weakening"),
            band(False, False, "Lagging"), band(False, True, "Improving")]


def rrg_scatter_figure(a):
    """Highcharts RRG: one spline series per sector (faded trail + single head
    dot), 100/100 crosshair plotLines, faint corner labels naming each quadrant,
    and native hover-isolation (hovering one sector dims the rest via
    ``states.inactive``). Series order matches the sectors order."""
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
                  "plotLines": [cross], "plotBands": quadrant_label_bands()},
        "yAxis": {**axis, "title": {"text": "RS-Momentum", "style": {"color": "#bdbdbd"}},
                  "plotLines": [cross]},
        # Native hover-isolation: hovering one series dims all others.
        "plotOptions": {"series": {"states": {"inactive": {"opacity": 0.12}}}},
        "series": series,
    }


def render():
    """The Sector Rotation board.

    A verdict strip (regime · diverging spread gauge · the spread and how far
    past its trigger it sits), a weight-proportional flow band, and four
    quadrant panels. All arithmetic is in ``pages/rotation_view.py``; this is
    widgets and wiring. No ``ui.add_css`` — every shape here is a Tailwind
    utility, including the gauge, whose parts are absolutely positioned with
    runtime percentage arbitraries (the documented continuous-value exception).
    """
    from nicegui import ui

    state = {"ver": None}

    ui.add_head_html(ROTATION_FONT_HEAD_HTML)

    wrap = ui.column().classes(
        f"{_T['RT_SANS']} {_T['RT_VOID_BG']} {V.NT['txt']} w-full gap-0 "
        "px-7 pt-9 pb-16 rounded-lg overflow-hidden")

    with wrap:
        # ── header ──────────────────────────────────────────────────────────
        with ui.row().classes("items-end w-full no-wrap gap-8"):
            with ui.column().classes("gap-2.5 min-w-0"):
                eyebrow_lbl = ui.label("").classes(_EYEBROW)
                ui.label("Sector Rotation").classes(
                    "text-[34px] font-semibold leading-none "
                    "tracking-[-0.025em] whitespace-nowrap")
            ui.space()
            ui.button("Refresh", color=None, on_click=lambda: _request_refresh()) \
                .props("flat no-caps dense").classes(
                    f"{_T['RT_MONO']} {V.NT['txt']} text-[11px] tracking-[.1em] "
                    f"uppercase bg-transparent border {V.NE['btn_edge']} "
                    f"px-[17px] h-[38px] leading-none hover:{V.NB['btn_hover']}")

        # ── verdict strip ───────────────────────────────────────────────────
        # gap-px + a 1px ring per panel = a shared hairline that survives wrap.
        # A grid with real borders orphans a rule when a panel reflows.
        with ui.row().classes("w-full flex-wrap gap-px mt-[30px] mb-9"):
            with ui.column().classes(f"{_PANEL} gap-2.5"):
                with ui.row().classes("items-center no-wrap gap-3"):
                    regime_dot = ui.element("div").classes(
                        f"w-[11px] h-[11px] rounded-full shrink-0 "
                        f"{V.TONE['flat']['dot']}")
                    regime_lbl = ui.label("").classes(
                        "text-[32px] font-semibold leading-none "
                        "tracking-[-0.03em] whitespace-nowrap "
                        f"{V.TONE['flat']['txt']}")
                regime_txt = ui.label("").classes(
                    f"text-[15px] leading-[1.45] max-w-[34ch] {V.NT['body']}")

            with ui.column().classes(f"{_PANEL} gap-4"):
                with ui.row().classes(
                        "items-baseline w-full no-wrap gap-3.5 justify-between"):
                    ui.label("Cyclical").classes(_FIELD)
                    cyc_lbl = ui.label("—").classes(_FIGURE)
                    ui.label("vs").classes(
                        f"{_T['RT_MONO']} {V.NT['ghost']} text-[10px] "
                        "tracking-[.1em] uppercase leading-none")
                    def_lbl = ui.label("—").classes(_FIGURE)
                    ui.label("Defensive").classes(_FIELD)
                gauge_box = ui.element("div").classes("relative h-[26px] w-full")
                axis_box = ui.row().classes(
                    "items-center justify-between w-full no-wrap")

            with ui.column().classes(f"{_PANEL} gap-2.5"):
                ui.label("Spread").classes(_FIELD)
                with ui.row().classes("items-baseline no-wrap gap-3"):
                    spread_lbl = ui.label("—").classes(
                        f"{_T['RT_MONO']} text-[32px] font-medium leading-none "
                        f"tracking-[-0.03em] {V.TONE['flat']['txt']}")
                    thresh_lbl = ui.label("").classes(
                        f"{_T['RT_MONO']} {V.NT['caption']} text-[12px] "
                        "leading-none")
                note_lbl = ui.label("").classes(f"text-[13px] {V.NT['note']}")

        # ── flow band ───────────────────────────────────────────────────────
        with ui.row().classes(
                "items-baseline justify-between w-full flex-wrap gap-5 mb-3.5"):
            ui.label("Where the S&P 500's weight is moving").classes(
                f"{_T['RT_MONO']} {V.NT['caption']} text-[10px] "
                "tracking-[.16em] uppercase leading-none")
            ui.label("Segment width = share of index").classes(
                f"{_T['RT_MONO']} {V.NT['ghost']} text-[10px] "
                "tracking-[.12em] uppercase leading-none")
        band_box = ui.row().classes("w-full no-wrap gap-1.5 mb-2.5")
        foot_box = ui.row().classes("w-full no-wrap gap-1.5 mb-11")

        # ── quadrant panels ─────────────────────────────────────────────────
        with ui.row().classes("items-stretch w-full no-wrap gap-3.5"):
            with ui.element("div").classes("flex items-center w-[22px] shrink-0"):
                ui.label("RS-Momentum →").classes(
                    f"{_T['RT_MONO']} {V.NT['rail']} text-[10px] "
                    "tracking-[.18em] uppercase whitespace-nowrap rotate-180 "
                    "[writing-mode:vertical-rl]")
            with ui.column().classes("flex-1 min-w-0 gap-0"):
                # gap over a grid-coloured ground draws the hairlines, so a
                # panel that reflows can never leave a rule hanging in space.
                quad_box = ui.element("div").classes(
                    "grid grid-cols-[repeat(auto-fit,minmax(320px,1fr))] "
                    f"gap-0.5 w-full border {V.NE['grid']} {V.NB['grid']}")
                with ui.row().classes(
                        "items-center justify-between w-full no-wrap pt-2.5 "
                        f"{_T['RT_MONO']} {V.NT['rail']} text-[10px] "
                        "tracking-[.18em] uppercase"):
                    ui.label("← Weaker relative strength")
                    ui.label("RS-Ratio")
                    ui.label("Stronger relative strength →")

        msg_lbl = ui.label("").classes(
            f"text-[13px] mt-6 {V.TONE['down']['txt']}")
        ui.label("Pairing is ordinal — strongest relative-selling vs strongest "
                 "relative-buying pressure, not literal cash flow.").classes(
            f"{_T['RT_MONO']} {V.NT['ghost']} text-[10px] tracking-[.1em] "
            f"uppercase leading-[1.7] mt-[34px] pt-4 border-t "
            f"{V.NE['note_rule']} w-full")

    rot_busy = _busy.build_busy(quad_box, "Refreshing rotation…")

    # ── painters ────────────────────────────────────────────────────────────
    def _paint_gauge(g, threshold):
        gauge_box.clear()
        axis_box.clear()
        with axis_box:
            for lab in V.gauge_axis(threshold):
                cls = (V.TONE[lab["tone"]]["axis"]
                       if lab["tone"] in ("up", "down") else V.NT["axis"])
                ui.label(lab["text"]).classes(
                    f"{_T['RT_MONO']} {cls} text-[9.5px] tracking-[.12em] "
                    "uppercase leading-none whitespace-nowrap")
        if not g:
            return
        tone = V.TONE[g["tone"]]
        with gauge_box:
            ui.element("div").classes(
                f"absolute left-0 right-0 top-2 h-2.5 {V.NB['track']}")
            ui.element("div").classes(
                f"absolute top-0.5 w-px h-[22px] {V.TONE['down']['tick']} "
                f"left-[{g['lo_pct']:.2f}%]")
            ui.element("div").classes(
                f"absolute top-0.5 w-px h-[22px] {V.TONE['up']['tick']} "
                f"left-[{g['hi_pct']:.2f}%]")
            ui.element("div").classes(
                f"absolute top-0 w-px h-[26px] {V.NB['axis']} "
                f"left-[{g['zero_pct']:.2f}%]")
            ui.element("div").classes(
                f"absolute top-2 h-2.5 {tone['fill']} "
                f"left-[{g['fill_left_pct']:.2f}%] "
                f"w-[{g['fill_width_pct']:.2f}%]")
            ui.element("div").classes(
                f"absolute top-[3px] w-0.5 h-5 -translate-x-px {tone['mark']} "
                f"left-[{g['value_pct']:.2f}%]")

    def _paint_flow(sides):
        band_box.clear()
        foot_box.clear()
        for key, tone_key in (("from", "down"), ("into", "up")):
            side = sides[key]
            tone = V.TONE[tone_key]
            # flex-grow carries the side's total weight, so the two halves of
            # the band are to scale against EACH OTHER, not just internally.
            grow = f"flex-[{side['total']:.2f}_1_0%]"
            with band_box:
                with ui.row().classes(f"{grow} no-wrap gap-0.5 min-w-0"):
                    for seg in side["rows"]:
                        qc = V.quad_classes(seg["quadrant"])
                        with ui.column().classes(
                                f"flex-[{seg['weight']:.2f}_1_0%] h-[68px] "
                                "justify-center gap-1 px-3 min-w-0 "
                                f"overflow-hidden {qc['seg']} border-t-2 "
                                f"{qc['seg_top']}"):
                            if seg["wide"]:
                                ui.label(str(seg["etf"] or "")).classes(
                                    f"{_T['RT_MONO']} {qc['ticker']} "
                                    "text-[11px] tracking-[.08em] leading-none "
                                    "whitespace-nowrap")
                                ui.label(V.fmt_weight(seg["weight"])).classes(
                                    f"{_T['RT_MONO']} {V.NT['bright']} "
                                    "text-[14px] font-medium leading-none "
                                    "whitespace-nowrap")
            with foot_box:
                with ui.row().classes(
                        f"{grow} items-baseline no-wrap gap-2.5 min-w-0 pt-2.5 "
                        f"border-t-2 {tone['foot_edge']}"):
                    ui.label(V.fmt_weight(side["total"])).classes(
                        f"{_T['RT_MONO']} {tone['foot_pct']} text-[20px] "
                        "font-medium leading-none")
                    ui.label(side["label"]).classes(
                        f"{_T['RT_MONO']} {tone['foot_lbl']} text-[10.5px] "
                        "tracking-[.14em] uppercase leading-none "
                        "whitespace-nowrap")

    def _paint_quadrants(panels):
        quad_box.clear()
        with quad_box:
            for p in panels:
                qc = p["classes"]
                with ui.column().classes(
                        f"{_T['RT_PANEL_BG']} px-[26px] pt-[26px] pb-7 "
                        "min-h-[260px] gap-0"):
                    with ui.row().classes(
                            "items-baseline justify-between w-full no-wrap "
                            "gap-3.5 mb-4"):
                        with ui.row().classes("items-center no-wrap gap-2.5"):
                            ui.element("div").classes(
                                "w-[9px] h-[9px] rounded-full shrink-0 "
                                f"{qc['dot']}")
                            ui.label(p["name"]).classes(
                                f"{_T['RT_MONO']} {qc['title']} text-[13px] "
                                "font-medium tracking-[.16em] uppercase "
                                "leading-none")
                        with ui.row().classes("items-baseline no-wrap gap-2"):
                            ui.label(V.fmt_weight(p["weight"])).classes(
                                f"{_T['RT_MONO']} {V.NT['bright']} "
                                "text-[22px] font-medium tracking-[-0.02em] "
                                "leading-none")
                            ui.label("of index").classes(
                                f"{_T['RT_MONO']} {V.NT['of_index']} "
                                "text-[10px] tracking-[.12em] uppercase "
                                "leading-none")
                    ui.label(p["blurb"]).classes(
                        f"text-[12.5px] leading-[1.4] mb-[18px] "
                        f"{V.NT['blurb']}")
                    with ui.element("div").classes(
                            "grid grid-cols-[repeat(auto-fill,minmax(146px,1fr))] "
                            "gap-0.5 w-full"):
                        for s in p["sectors"]:
                            with ui.column().classes(
                                    f"{qc['chip']} px-[13px] pt-[13px] pb-3 "
                                    "gap-0"):
                                with ui.row().classes(
                                        "items-baseline justify-between "
                                        "w-full no-wrap gap-2 mb-[7px]"):
                                    ui.label(str(s["name"] or "")).classes(
                                        "text-[13.5px] font-medium "
                                        "leading-[1.15] tracking-[-0.01em] "
                                        "truncate")
                                    ui.label(str(s["etf"] or "")).classes(
                                        f"{_T['RT_MONO']} {V.NT['rail']} "
                                        "text-[10px] tracking-[.06em] "
                                        "leading-none")
                                with ui.row().classes(
                                        "items-baseline no-wrap gap-[7px] "
                                        "mb-[9px]"):
                                    ui.label(V.fmt_mom(s["mom"])).classes(
                                        f"{_T['RT_MONO']} {qc['mom']} "
                                        "text-[16px] font-medium "
                                        "tracking-[-0.02em] leading-none")
                                    ui.label("RS-Mom").classes(
                                        f"{_T['RT_MONO']} {V.NT['axis']} "
                                        "text-[9.5px] tracking-[.12em] "
                                        "uppercase leading-none")
                                with ui.element("div").classes(
                                        "relative h-[3px] w-full "
                                        f"{V.NB['hair']}"):
                                    ui.element("div").classes(
                                        "absolute left-0 top-0 h-[3px] "
                                        f"{qc['bar']} w-[{s['bar_pct']:.1f}%]")

    def _render(a, weights, threshold):
        h = a.get("headline") or {}
        rt = threshold if threshold is not None else DEFAULT_RISK_THRESHOLD
        word, tone = V.regime_display(h.get("regime"))
        eyebrow_lbl.text = V.eyebrow(a.get("date"))
        regime_lbl.text = word
        regime_lbl.classes(remove=V.TONE_TXT_CLASSES, add=V.TONE[tone]["txt"])
        regime_dot.classes(remove=_DOT_CLASSES, add=V.TONE[tone]["dot"])
        regime_txt.text = V.regime_sentence(h.get("regime"), h.get("text") or "")
        cyc_lbl.text = V.fmt_mom(h.get("cyclical_mom_mean"))
        def_lbl.text = V.fmt_mom(h.get("defensive_mom_mean"))
        spread_lbl.text = V.fmt_spread(h.get("spread"))
        spread_lbl.classes(remove=V.TONE_TXT_CLASSES, add=V.TONE[tone]["txt"])
        thresh_lbl.text = f"threshold ±{rt:.2f}"
        note_lbl.text = V.trigger_note(h.get("spread"), rt)
        msg_lbl.text = ""
        _paint_gauge(V.spread_gauge(h.get("spread"), rt), rt)
        _paint_flow(V.flow_sides(a.get("sectors"), weights))
        _paint_quadrants(V.quadrant_panels(a.get("sectors"), weights))

    def _blank(message):
        eyebrow_lbl.text = V.eyebrow(None)
        regime_lbl.text = "—"
        regime_txt.text = ""
        cyc_lbl.text = def_lbl.text = spread_lbl.text = "—"
        thresh_lbl.text = ""
        note_lbl.text = ""
        msg_lbl.text = message
        _paint_gauge(None, DEFAULT_RISK_THRESHOLD)
        _paint_flow(V.flow_sides([], {}))
        _paint_quadrants(V.quadrant_panels([], {}))

    @guard
    def _apply():
        rot_busy.hide()
        rot = bus_client.read("sentiment:rotation") or {}
        a = rot.get("assessment")
        if a:
            _render(a, rot.get("weights") or {}, rot.get("risk_threshold"))
        else:
            _blank(rot.get("error") or "Waiting for the sentiment service…")

    @guard
    def _request_refresh():
        bus_client.request("sentiment", {"type": "refresh_rotation"})
        ui.notify("Refresh requested")
        rot_busy.show()

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
