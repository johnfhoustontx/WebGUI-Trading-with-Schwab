"""Portfolio page (Tier-3 reader) — sector breakdown, vs-sector perf, live P&L.

This page holds **no engine call**. The model build (sector breakdown, the four
sector comparisons, per-symbol baselines), the live quote stream, and all display
formatting live in ``services/portfolio_svc``; the page reads the cached
``PortfolioModel`` (already-formatted display rows) and renders three tabs —
Holdings / Sectors / Performance — plus a per-position suggestion detail pane.

* **Refresh** → ``{"type":"refresh"}`` on ``cmd:portfolio`` — the service
  rebuilds the model and restarts the stream on the fresh holdings.

A version-poll on ``portfolio:positions`` repaints from the cache (the service
re-publishes on each throttled stream tick, so P&L updates live); the state
persists across navigation (single-user). The pure display builders
(``proxy_status``/``stream_status``/``suggestion_text``/``status_line``) are
unit-tested.
"""
import bus_client
import page_help as _page_help
from nicegui import ui

from pages import ui_kit as kit
from pages.ui_guard import guard
from pages.options import theme as _t

VIEW = "portfolio:positions"

# The proxy up/down and stream live/manual indicators are a STATE reading, not
# P&L — so they take the app's semantic state colours rather than a palette of
# this page's own. Held as BOTH the hex and the matching Tailwind class because
# ``proxy_status``/``stream_status`` return a colour and ``*_status_class`` maps
# it back by comparing against the same constant; building both halves from one
# value is what stops the two drifting.
UP_COLOR = _t.THEME["semantic"]["positive"]
DOWN_COLOR = _t.THEME["semantic"]["negative"]
MUTED_COLOR = _t.THEME["palette"]["muted"]

TXT_UP = _t.TXT_POS
TXT_DOWN = _t.TXT_NEG
TXT_MUTED = _t.MUTED
# Full set to strip on a reactive (in-place) recolor so classes don't stack.
STATUS_TEXT_CLASSES = f"{TXT_UP} {TXT_DOWN} {TXT_MUTED}"

# NiceGUI table columns. The service formats every cell to a display string via
# portfolio-analyzer's ``view_model`` (HOLDINGS/SECTOR/PERFORMANCE_COLUMNS), so
# these only map row keys → labels; the page does no further formatting.
HOLDINGS_COLS = [
    {"name": "symbol", "label": "Symbol", "field": "symbol", "align": "left"},
    {"name": "sector", "label": "Sector", "field": "sector", "align": "left"},
    {"name": "quantity", "label": "Shares", "field": "quantity"},
    {"name": "market_value", "label": "Market Value", "field": "market_value"},
    {"name": "day_pl", "label": "Day P/L", "field": "day_pl"},
    {"name": "total_pl", "label": "Total P/L", "field": "total_pl"},
    {"name": "vs_sector", "label": "vs Sector (RS)", "field": "vs_sector",
     "align": "left"},
    {"name": "since_purchase", "label": "Since Purchase",
     "field": "since_purchase"},
]

SECTOR_COLS = [
    {"name": "sector", "label": "Sector", "field": "sector", "align": "left"},
    {"name": "weight", "label": "Weight", "field": "weight"},
    {"name": "benchmark_delta", "label": "vs Benchmark",
     "field": "benchmark_delta"},
]

PERF_COLS = [
    {"name": "symbol", "label": "Symbol", "field": "symbol", "align": "left"},
    {"name": "grade_return", "label": "Return", "field": "grade_return"},
    {"name": "grade_capital", "label": "Capital", "field": "grade_capital"},
    {"name": "grade_risk", "label": "Risk", "field": "grade_risk"},
    {"name": "grade_execution", "label": "Entry", "field": "grade_execution"},
    {"name": "composite", "label": "Overall", "field": "composite"},
    {"name": "ann_return", "label": "Ann. Return", "field": "ann_return"},
    {"name": "vs_sector", "label": "vs Sector", "field": "vs_sector"},
    {"name": "drawdown", "label": "Off Peak", "field": "drawdown"},
    {"name": "top_action", "label": "Suggestion", "field": "top_action",
     "align": "left"},
]

# Which columns hold a NUMBER. Quasar's own column default is RIGHT and the
# kit's is LEFT, so anything numeric that is not named here silently moves left.
# Read off ``portfolio-analyzer/src/view_model.py``, which is what formats every
# one of these cells.
HOLDINGS_NUMERIC = ("quantity", "market_value", "day_pl", "total_pl",
                    "since_purchase")
SECTOR_NUMERIC = ("weight", "benchmark_delta")
# ⚠ Return / Capital / Risk / Entry are NOT numbers and are deliberately absent:
# ``view_model._grade_cell`` renders a 0–4 score as a LETTER (``grade_letter``
# → F..A) or an em dash, so each cell is one character and takes the same left
# alignment every other text column in the app takes. ``composite`` stays right
# — it is ``"3.4 (A)"``, a magnitude with a fixed-width suffix, so the decimal
# points still line up.
PERF_NUMERIC = ("composite", "ann_return", "vs_sector", "drawdown")


def proxy_status(payload):
    """(text, color) for the proxy indicator."""
    up = bool((payload or {}).get("proxy_up"))
    return ("● Proxy up", UP_COLOR) if up else ("● Proxy down", DOWN_COLOR)


def stream_status(payload):
    """(text, color) for the live-stream indicator."""
    live = bool((payload or {}).get("streaming"))
    return ("● live", UP_COLOR) if live else ("manual refresh", MUTED_COLOR)


def proxy_status_class(payload):
    """(text, Tailwind text-color class) for the proxy indicator."""
    text, color = proxy_status(payload)
    return (text, TXT_UP if color == UP_COLOR else TXT_DOWN)


def stream_status_class(payload):
    """(text, Tailwind text-color class) for the live-stream indicator."""
    text, color = stream_status(payload)
    return (text, TXT_UP if color == UP_COLOR else TXT_MUTED)


def suggestion_text(suggestions, symbol):
    """Full suggestion reasons for ``symbol`` (one per line), or a prompt."""
    if not symbol:
        return "Select a position to see its suggestions."
    sugs = (suggestions or {}).get(symbol) or []
    if not sugs:
        return f"{symbol}: no suggestions."
    return "\n".join(f"[{s.get('action')}] {s.get('reason')}" for s in sugs)


def status_line(payload):
    """Short status: holding count (+ first error, if any)."""
    payload = payload or {}
    n = len(payload.get("holdings_rows") or [])
    base = f"{n} holding(s)"
    errs = payload.get("errors") or []
    if errs:
        base += f" · {errs[0]}"
    return base


def render():
    """Portfolio page: Holdings / Sectors / Performance tabs + suggestion pane."""
    import shell as _shell

    state = {"payload": None, "ver": None, "selected": None}

    with kit.page():
        # ⚠ ``stale=False`` is a DECISION, not a default. ``alerts.py`` measures
        # this view's off-hours cadence twice and the two disagree — line 116
        # says 620 s against a 600 s threshold on a Sunday ("it flaps in and out
        # of stale"), line 146 says 23 s (the 2 s loop) on that SAME Sunday. A
        # stamp that may go amber nightly on a healthy stack is worse than no
        # stamp at all, and the nav badge's own threshold is where that
        # contradiction gets settled — not here.
        head = kit.header("Portfolio", view=VIEW, stale=False)
        with head.actions:
            refresh_btn = kit.button("Refresh", kind="secondary", icon="refresh")

        with ui.row().classes("items-center gap-4 flex-wrap"):
            status_lbl = kit.status_line()
            proxy_lbl = ui.label("").classes("text-sm")
            stream_lbl = ui.label("").classes("text-sm")

        # Holdings / Sectors / Performance as folder-style TABS at the top of the
        # page (2026-07-12 — like the Options pages): rendered into
        # shell.subtab_slot() so they sit under the header in the same position
        # as the group subtabs; falls back inline if the slot is absent.
        def _build_tabs():
            with ui.tabs().classes("compact-tabs").props(
                    "dense no-caps inline-label align=left") as t:
                for _key in ("Holdings", "Sectors", "Performance"):
                    with ui.tab(_key):
                        ui.tooltip(_page_help.subtab_help("/portfolio", _key)
                                   ).props("delay=350 max-width=340px")
            return t

        _slot = _shell.subtab_slot()
        if _slot is not None:
            with _slot:
                tabs = _build_tabs()
        else:
            tabs = _build_tabs()
        _shell.bind_breadcrumb_leaf(tabs, initial="Holdings")   # default on tab_panels

        # Refresh rebuilds the whole model service-side (holdings + baselines +
        # the four comparisons), so all three tabs are stale until it lands. The
        # spinner sits on the region's OUTER element, so nothing a repaint does
        # can delete it — and here nothing clears ``content`` at all: the three
        # tables are written in place (``.rows``/``.update()``) on every
        # throttled stream tick.
        wait = kit.region("Rebuilding the portfolio…")
        with wait.content:
            panels = ui.tab_panels(tabs, value="Holdings") \
                .classes("w-full flush-panels")
            with panels:
                with ui.tab_panel("Holdings"):
                    holdings_tbl = kit.table(HOLDINGS_COLS, row_key="symbol",
                                             numeric=HOLDINGS_NUMERIC)
                with ui.tab_panel("Sectors"):
                    sectors_tbl = kit.table(SECTOR_COLS, row_key="sector",
                                            numeric=SECTOR_NUMERIC)
                with ui.tab_panel("Performance"):
                    perf_tbl = kit.table(PERF_COLS, row_key="symbol",
                                         numeric=PERF_NUMERIC)
                    ui.label("Select a row to see its suggestions.") \
                        .classes(f"text-xs {_t.MUTED} q-mt-sm")
                    detail = ui.label(suggestion_text({}, None)) \
                        .classes(f"text-sm whitespace-pre-wrap {_t.LABEL}")

        def _show_detail():
            detail.text = suggestion_text(
                (state["payload"] or {}).get("suggestions"), state["selected"])

        @guard
        def _on_perf_click(e):
            row = e.args[1] if e.args and len(e.args) > 1 else None
            if row and row.get("symbol") and row["symbol"] != "—":
                state["selected"] = row["symbol"]
                _show_detail()

        perf_tbl.on("rowClick", _on_perf_click)

        def _repaint():
            wait.busy.hide()
            kit.set_busy(refresh_btn, False)
            p = state["payload"] or {}
            holdings_tbl.rows = p.get("holdings_rows") or []
            holdings_tbl.update()
            sectors_tbl.rows = p.get("sector_rows") or []
            sectors_tbl.update()
            perf_tbl.rows = p.get("performance_rows") or []
            perf_tbl.update()
            ptext, pclass = proxy_status_class(p)
            proxy_lbl.text = ptext
            proxy_lbl.classes(remove=STATUS_TEXT_CLASSES, add=pclass)
            stext, sclass = stream_status_class(p)
            stream_lbl.text = stext
            stream_lbl.classes(remove=STATUS_TEXT_CLASSES, add=sclass)
            status_lbl.text = status_line(p)
            _show_detail()

        @guard
        def _refresh():
            # The button holds its own spinner and the region scrims the tabs.
            # The old status write said the same thing a third time, in the line
            # that carries the holding count — and nothing put the count back
            # until the next repaint.
            bus_client.request("portfolio", {"type": "refresh"})
            kit.set_busy(refresh_btn)
            wait.busy.show()

        refresh_btn.on_click(_refresh)

        @guard
        def _poll():
            v = bus_client.read_version(VIEW)
            if v != state["ver"]:
                state["ver"] = v
                state["payload"] = bus_client.read(VIEW) or None
                _repaint()

        # Initial paint from cache (graceful-empty when the service is cold).
        state["ver"] = bus_client.read_version(VIEW)
        state["payload"] = bus_client.read(VIEW) or None
        _repaint()
        ui.timer(2.0, _poll)
