"""RRG page — the Relative Rotation Graph vs SPY (under Trend & Sentiment).

Tier-1 reader: no engine calls, no app ``scoring`` import. The rotation
assessment is computed in ``services/sentiment_svc`` and cached; this page only
**formats** it. Cache view read:

* ``sentiment:rotation`` → ``{"assessment", "weights", "risk_threshold", "error"}``
  (see ``services/sentiment_svc/handlers.refresh_rotation``).

**Rebuilt 2026-08-17** from a supplied design, replacing the Highcharts spline
scatter with a hand-drawn plot: absolutely-positioned markers over an SVG trail
layer, on four quadrant washes with a fixed crosshair. All the geometry is pure
and lives in ``pages/rrg_view.py``; this module is widgets and wiring.

**Marker area is the sector's S&P weight** and **each trail is its last five
readings**, oldest faintest — so the plot answers "where is it, how big is it,
and which way is it heading" in one pass.

Tailwind-first: the only non-utility content is the trail layer, a raw
``ui.html()`` SVG string (the documented out-of-scope case, as with
``pages/rings.py``). Marker and tick positions are runtime percentage
arbitraries — the documented continuous-value exception.
"""
import bus_client
from nicegui import ui
from pages import busy as _busy
from pages import rrg_view as R
from pages.options.theme import (
    ROTATION_FONT_HEAD_HTML, ROTATION_TOKENS as _T,
)
from pages.rotation_view import NB, NE, NT, TONE, eyebrow
from pages.ui_guard import guard

VIEW = "sentiment:rotation"
PLOT_H = "h-[600px]"

_MONO = _T["RT_MONO"]
_AXIS_TITLE = (f"{_MONO} {NT['rail']} text-[10px] tracking-[.18em] uppercase "
               "whitespace-nowrap")
_TICK = f"{_MONO} {NT['caption']} text-[10.5px] whitespace-nowrap absolute"
_CORNER = f"{_MONO} absolute text-[11px] tracking-[.2em] uppercase"


def render():
    state = {"ver": None}

    ui.add_head_html(ROTATION_FONT_HEAD_HTML)

    wrap = ui.column().classes(
        f"{_T['RT_SANS']} {_T['RT_VOID_BG']} {NT['txt']} w-full gap-0 "
        "px-7 pt-9 pb-14 rounded-lg overflow-hidden")

    with wrap:
        # ── header ──────────────────────────────────────────────────────────
        with ui.row().classes("items-end w-full no-wrap gap-8 mb-6"):
            with ui.column().classes("gap-2.5 min-w-0"):
                eyebrow_lbl = ui.label("").classes(
                    f"{_MONO} {NT['eyebrow']} text-[11px] tracking-[.16em] "
                    "uppercase leading-none")
                ui.label("Where every sector sits").classes(
                    "text-[34px] font-semibold leading-none "
                    "tracking-[-0.025em] whitespace-nowrap")
            ui.space()
            ui.button("Refresh", color=None, on_click=lambda: _request_refresh()) \
                .props("flat no-caps dense").classes(
                    f"{_MONO} {NT['txt']} text-[11px] tracking-[.1em] uppercase "
                    f"bg-transparent border {NE['btn_edge']} px-[17px] h-[38px] "
                    f"leading-none hover:{NB['btn_hover']}")

        # ── verdict strip ───────────────────────────────────────────────────
        strip = ui.row().classes(
            "items-center w-full flex-wrap gap-3.5 px-[18px] py-3.5 mb-[34px]")
        with strip:
            strip_dot = ui.element("div").classes(
                "w-[9px] h-[9px] rounded-full shrink-0")
            strip_word = ui.label("").classes(
                f"{_MONO} text-[12px] tracking-[.16em] uppercase leading-none")
            strip_sentence = ui.label("").classes(
                f"text-[14.5px] {NT['body']}")
            strip_stats = ui.label("").classes(
                f"{_MONO} {NT['rail']} text-[12px] leading-none")

        # ── plot ────────────────────────────────────────────────────────────
        with ui.row().classes("items-stretch w-full flex-wrap gap-[30px]"):
            with ui.row().classes("flex-[1_1_620px] min-w-0 no-wrap gap-3"):
                with ui.element("div").classes(
                        "flex items-center w-5 shrink-0"):
                    ui.label("RS-Momentum").classes(
                        f"{_AXIS_TITLE} [writing-mode:vertical-rl] rotate-180")
                ytick_box = ui.element("div").classes(
                    f"w-11 shrink-0 relative {PLOT_H}")
                with ui.column().classes("flex-1 min-w-0 gap-0"):
                    plot = ui.element("div").classes(
                        f"relative {PLOT_H} w-full overflow-hidden "
                        f"{_T['RT_PANEL_BG']} border {NE['grid']}")
                    xtick_box = ui.element("div").classes("relative h-[26px] w-full")
                    ui.label("RS-Ratio").classes(
                        f"{_AXIS_TITLE} w-full text-center")

            ui.label(
                "Marker area = share of the S&P 500. Trails show the last five "
                "readings, oldest faintest.").classes(
                f"{_MONO} {NT['ghost']} text-[9.5px] tracking-[.1em] uppercase "
                "leading-[1.7] flex-[1_1_100%] pt-3.5")

        msg_lbl = ui.label("").classes(f"text-[13px] mt-6 {TONE['down']['txt']}")

    rrg_busy = _busy.build_busy(plot, "Refreshing rotation…")

    # ── painters ────────────────────────────────────────────────────────────
    def _paint_axes(dom):
        ytick_box.clear()
        xtick_box.clear()
        with ytick_box:
            for v in R.ticks(dom["y_lo"], dom["y_hi"]):
                ui.label(f"{v:g}").classes(
                    f"{_TICK} right-2 -translate-y-1/2 "
                    f"top-[{R.py(v, dom['y_lo'], dom['y_hi']):.2f}%]")
        with xtick_box:
            for v in R.ticks(dom["x_lo"], dom["x_hi"]):
                ui.label(f"{v:g}").classes(
                    f"{_TICK} top-2 -translate-x-1/2 "
                    f"left-[{R.px(v, dom['x_lo'], dom['x_hi']):.2f}%]")

    def _paint_plot(sectors, weights, dom):
        plot.clear()
        with plot:
            # quadrant washes — the plot's own legend, so a marker's quadrant is
            # readable from where it sits rather than from a colour key
            for q, corner in R.QUADRANT_CORNERS.items():
                vert, horiz = corner.split("-")
                ui.element("div").classes(
                    f"absolute w-1/2 h-1/2 {R.QUAD_WASH[q]} "
                    f"{'top-0' if vert == 'top' else 'top-1/2'} "
                    f"{'left-0' if horiz == 'left' else 'left-1/2'}")
            for v in R.ticks(dom["x_lo"], dom["x_hi"]):
                if abs(v - R.CENTRE) < 1e-9:
                    continue
                ui.element("div").classes(
                    f"absolute top-0 w-px h-full {NB['hair']} "
                    f"left-[{R.px(v, dom['x_lo'], dom['x_hi']):.2f}%]")
            for v in R.ticks(dom["y_lo"], dom["y_hi"]):
                if abs(v - R.CENTRE) < 1e-9:
                    continue
                ui.element("div").classes(
                    f"absolute left-0 w-full h-px {NB['hair']} "
                    f"top-[{R.py(v, dom['y_lo'], dom['y_hi']):.2f}%]")
            # the crosshair: RS-Ratio 100 / RS-Momentum 100, always dead centre
            ui.element("div").classes(
                f"absolute left-1/2 top-0 w-px h-full {NB['btn_hover_edge']}")
            ui.element("div").classes(
                f"absolute left-0 top-1/2 w-full h-px {NB['btn_hover_edge']}")
            # trails, under the markers
            ui.html(R.tail_svg(sectors, dom)).classes(
                "absolute inset-0 w-full h-full pointer-events-none")
            for q, corner in R.QUADRANT_CORNERS.items():
                vert, horiz = corner.split("-")
                ui.label(q).classes(
                    f"{_CORNER} {R.QUAD_CORNER_TXT[q]} "
                    f"{'top-3.5' if vert == 'top' else 'bottom-3.5'} "
                    f"{'left-4' if horiz == 'left' else 'right-4'}")
            for p in R.plot_points(sectors, weights, dom):
                cls = p["classes"]
                d = f"{p['size_px']:.1f}"
                with ui.element("div").classes(
                        f"absolute w-0 h-0 left-[{p['x_pct']:.2f}%] "
                        f"top-[{p['y_pct']:.2f}%]"):
                    ui.element("div").classes(
                        f"absolute left-0 top-0 -translate-x-1/2 -translate-y-1/2 "
                        f"rounded-full w-[{d}px] h-[{d}px] "
                        f"{cls['dot']} {cls['halo']}")
                    # The sector NAME, in the sans face: a proper noun set in a
                    # mono ticker face reads as a code, which is the confusion
                    # this change exists to remove.
                    ui.label(str(p["label"])).classes(
                        f"{cls['label']} absolute whitespace-nowrap "
                        "text-[11.5px] font-medium tracking-[-0.005em] "
                        + ("-translate-y-1/2" if p["anchor"] == "left"
                           else "-translate-x-full -translate-y-1/2")
                        + f" left-[{p['dx']:.1f}px] top-[{p['dy']:.1f}px]")

    def _render(a, weights, threshold):
        h = a.get("headline") or {}
        bar = R.alert_bar(h, threshold)
        tone = TONE[bar["tone"]]
        eyebrow_lbl.text = eyebrow(a.get("date")).replace(
            "RRG vs SPY", "Relative Rotation Graph vs SPY")
        tint = R.STRIP_TINT[bar["tone"]]
        strip.classes(remove=f"{R.STRIP_BG_CLASSES} {R.STRIP_EDGE_CLASSES}",
                      add=f"border {tint['bg']} {tint['edge']}")
        strip_dot.classes(remove=R.STRIP_DOT_CLASSES, add=tone["dot"])
        strip_word.text = bar["word"]
        strip_word.classes(remove=R.STRIP_TXT_CLASSES, add=tint["txt"])
        strip_sentence.text = bar["sentence"]
        strip_stats.text = bar["stats"]
        msg_lbl.text = ""
        sectors = a.get("sectors") or []
        dom = R.domain(sectors)
        _paint_axes(dom)
        _paint_plot(sectors, weights, dom)

    def _blank(message):
        eyebrow_lbl.text = "Relative Rotation Graph vs SPY · awaiting data"
        strip_word.text = "—"
        strip_sentence.text = ""
        strip_stats.text = ""
        msg_lbl.text = message
        dom = R.domain([])
        _paint_axes(dom)
        _paint_plot([], {}, dom)

    @guard
    def _apply():
        rrg_busy.hide()
        rot = bus_client.read(VIEW) or {}
        a = rot.get("assessment")
        if a:
            _render(a, rot.get("weights") or {}, rot.get("risk_threshold"))
        else:
            _blank(rot.get("error") or "Waiting for the sentiment service…")

    @guard
    def _request_refresh():
        bus_client.request("sentiment", {"type": "refresh_rotation"})
        ui.notify("Refresh requested")
        rrg_busy.show()

    @guard
    def _maybe_repaint():
        ver = bus_client.read_version(VIEW)
        if ver == state["ver"]:
            return
        state["ver"] = ver
        _apply()

    state["ver"] = bus_client.read_version(VIEW)
    _apply()
    ui.timer(2.0, _maybe_repaint)
