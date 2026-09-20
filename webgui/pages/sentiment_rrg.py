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

**On the page kit since 2026-09-19** (the consistency standard,
``docs/plans/2026-09-19-app-ui-consistency-design.md``): the header line carries
the name and the Updated stamp, the eyebrow is the status line, and ONE
``kit.region`` covers the plot a repaint replaces. The page's own ground, face
and warm-neutral ladder are gone — it wears the app surface like every other
screen. ⚠ The DATA colours are untouched and must stay so: the four quadrant
washes and corner labels, the marker/trail classes, the verdict strip's tint and
every ``TONE`` dot encode a value, and the standard keeps every such colour.
Gridlines, the crosshair and the plot frame are chart FURNITURE and take app
border tokens.
"""
import bus_client
from nicegui import ui
from pages import rrg_view as R
from pages import ui_kit as kit
from pages.options import theme
from pages.rotation_view import TONE, eyebrow
from pages.view_watch import watch_view
from pages.ui_guard import guard
from pages import copy as _copy  # the ONE copy (pages/copy.py)

VIEW = "sentiment:rotation"
PLOT_H = "h-[600px]"

_P = theme.THEME["palette"]
# Chart furniture, not data. The crosshair sits one border step brighter than
# the gridlines because it marks RS-Ratio 100 / RS-Momentum 100 — the plot's one
# fixed reference — where the gridlines are only a scale.
_GRIDLINE = f"bg-[{_P['card_border']}]"
_CROSSHAIR = f"bg-[{_P['btn_border']}]"

_AXIS_TITLE = (f"{theme.MUTED} text-[10px] tracking-[.18em] uppercase "
               "whitespace-nowrap")
_TICK = f"{theme.MUTED} text-[10.5px] whitespace-nowrap absolute"
_CORNER = "absolute text-[11px] tracking-[.2em] uppercase"


def render():
    # Whether this render may command the sentiment service at all — resolved
    # ONCE, so the button and its handler cannot disagree. False on the public
    # live origin, where a Refresh is eleven sector chains plus their histories
    # per click against the owner's Schwab budget. See ``shell.may_enqueue``.
    import shell as _shell
    _may_enqueue = _shell.may_enqueue()

    with kit.page():
        # No description line: "where every sector sits, and which way it is
        # heading" is the first sentence of the page help.
        head = kit.header("RRG", view=VIEW, stale=False)
        # Not drawn on the public live origin — see shell.may_enqueue. A Refresh
        # is eleven sector chains plus their histories, per click.
        if _may_enqueue:
            with head.actions:
                kit.button("Refresh", kind="secondary", icon="refresh",
                           on_click=lambda: _request_refresh())
        # Keeps its runtime text ("Relative Rotation Graph vs SPY · as of …"):
        # that is the READING's date, not the page's freshness, which is what
        # the header stamp answers.
        eyebrow_lbl = kit.status_line()

        # ── verdict strip ───────────────────────────────────────────────────
        strip = ui.row().classes(
            "items-center w-full flex-wrap gap-3.5 px-[18px] py-3.5")
        with strip:
            strip_dot = ui.element("div").classes(
                "w-[9px] h-[9px] rounded-full shrink-0")
            strip_word = ui.label("").classes(
                "text-[12px] tracking-[.16em] uppercase leading-none")
            strip_sentence = ui.label("").classes(
                f"text-[14.5px] {theme.LABEL}")
            strip_stats = ui.label("").classes(
                f"{theme.MUTED} text-[12px] leading-none")

        # ── plot ────────────────────────────────────────────────────────────
        # The spinner lives on the region's OUTER element, so ``_paint_plot``'s
        # ``plot.clear()`` cannot delete it — the bug this page had from the
        # 2026-08-17 rebuild until now: the scrim was mounted inside ``plot``,
        # the first paint removed it, and Refresh has never shown a spinner.
        plot_region = kit.region("Refreshing rotation…")
        with plot_region.content:
            with ui.row().classes("items-stretch w-full flex-wrap gap-[30px]"):
                with ui.row().classes("flex-[1_1_620px] min-w-0 no-wrap gap-3"):
                    with ui.element("div").classes(
                            "flex items-center w-5 shrink-0"):
                        ui.label("RS-Momentum").classes(
                            f"{_AXIS_TITLE} [writing-mode:vertical-rl] rotate-180")
                    ytick_box = ui.element("div").classes(
                        f"w-11 shrink-0 relative {PLOT_H}")
                    with ui.column().classes("flex-1 min-w-0 gap-0"):
                        # The app card IS the plot frame. Its padding does not
                        # move a marker: every child is absolutely positioned,
                        # and those resolve against the PADDING box.
                        plot = ui.element("div").classes(
                            f"relative {PLOT_H} w-full overflow-hidden "
                            f"{theme.CARD}")
                        xtick_box = ui.element("div").classes(
                            "relative h-[26px] w-full")
                        ui.label("RS-Ratio").classes(
                            f"{_AXIS_TITLE} w-full text-center")

                ui.label(
                    "Marker area = share of the S&P 500. Trails show the last "
                    "five readings, oldest faintest.").classes(
                    f"{theme.MUTED} text-[9.5px] tracking-[.1em] uppercase "
                    "leading-[1.7] flex-[1_1_100%] pt-3.5")

        msg_lbl = ui.label("").classes(f"text-[13px] {TONE['down']['txt']}")

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
                    f"absolute top-0 w-px h-full {_GRIDLINE} "
                    f"left-[{R.px(v, dom['x_lo'], dom['x_hi']):.2f}%]")
            for v in R.ticks(dom["y_lo"], dom["y_hi"]):
                if abs(v - R.CENTRE) < 1e-9:
                    continue
                ui.element("div").classes(
                    f"absolute left-0 w-full h-px {_GRIDLINE} "
                    f"top-[{R.py(v, dom['y_lo'], dom['y_hi']):.2f}%]")
            # the crosshair: RS-Ratio 100 / RS-Momentum 100, always dead centre
            ui.element("div").classes(
                f"absolute left-1/2 top-0 w-px h-full {_CROSSHAIR}")
            ui.element("div").classes(
                f"absolute left-0 top-1/2 w-full h-px {_CROSSHAIR}")
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
        plot_region.busy.hide()
        rot = bus_client.read(VIEW) or {}
        a = rot.get("assessment")
        if a:
            _render(a, rot.get("weights") or {}, rot.get("risk_threshold"))
        else:
            _blank(rot.get("error") or _copy.WAITING_SENTIMENT)

    @guard
    def _request_refresh():
        if not _may_enqueue:
            return          # no button either — see shell.may_enqueue
        bus_client.request("sentiment", {"type": "refresh_rotation"})
        # No toast: the region's spinner already says the page is waiting, and
        # the standard keeps a toast for the OUTCOME of an action.
        plot_region.busy.show()

    _apply()
    # Version-gated repaint + seed, in one line (pages/view_watch.py).
    # This idiom -- probe :ver, compare, re-read, repaint, hang it on a
    # 2 s timer -- was written out longhand on 22 pages.
    watch_view(VIEW, lambda: _apply())
