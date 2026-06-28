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
from nicegui import ui

from pages.ui_guard import guard

UP_COLOR = "#2e9e6b"
DOWN_COLOR = "#e24b4a"
MUTED_COLOR = "#888888"

# Tailwind arbitrary-value text-color classes for the status indicators (local —
# NOT the theme TXT_* tokens; these labels keep their own palette).
TXT_UP = "text-[#2e9e6b]"
TXT_DOWN = "text-[#e24b4a]"
TXT_MUTED = "text-[#888888]"
# Full set to strip on a reactive (in-place) recolor so classes don't stack.
STATUS_TEXT_CLASSES = f"{TXT_UP} {TXT_DOWN} {TXT_MUTED}"

# NiceGUI table columns. The service formats every cell to a display string via
# portfolio-analyzer's ``view_model`` (HOLDINGS/SECTOR/PERFORMANCE_COLUMNS), so
# these only map row keys → labels; the page does no further formatting.
HOLDINGS_COLS = [
    {"name": "symbol", "label": "Symbol", "field": "symbol", "align": "left"},
    {"name": "sector", "label": "Sector", "field": "sector", "align": "left"},
    {"name": "quantity", "label": "Qty", "field": "quantity"},
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
    {"name": "tailwind", "label": "Tailwind", "field": "tailwind"},
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
    ui.label("Portfolio Analyzer").classes("text-h5")
    ui.label("Sector breakdown, vs-sector performance, and live-streaming P&L. "
             "Reads the portfolio service — start it (and the proxy) to "
             "populate.").classes("text-xs opacity-60")

    state = {"payload": None, "ver": None, "selected": None}

    with ui.row().classes("items-center gap-4 flex-wrap"):
        refresh_btn = ui.button("Refresh", icon="refresh")
        proxy_lbl = ui.label("").classes("text-sm")
        stream_lbl = ui.label("").classes("text-sm")
        status_lbl = ui.label("").classes("opacity-70 text-sm")

    with ui.tabs().classes("w-full") as tabs:
        ui.tab("Holdings")
        ui.tab("Sectors")
        ui.tab("Performance")
    with ui.tab_panels(tabs, value="Holdings").classes("w-full"):
        with ui.tab_panel("Holdings"):
            holdings_tbl = ui.table(columns=HOLDINGS_COLS, rows=[],
                                    row_key="symbol").classes("w-full").props("dense")
        with ui.tab_panel("Sectors"):
            sectors_tbl = ui.table(columns=SECTOR_COLS, rows=[],
                                   row_key="sector").classes("w-full").props("dense")
        with ui.tab_panel("Performance"):
            perf_tbl = ui.table(columns=PERF_COLS, rows=[], row_key="symbol") \
                .classes("w-full").props("dense")
            ui.label("Select a row to see its suggestions.") \
                .classes("text-xs opacity-60 q-mt-sm")
            detail = ui.label(suggestion_text({}, None)) \
                .classes("text-sm whitespace-pre-wrap")

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
        bus_client.request("portfolio", {"type": "refresh"})
        status_lbl.text = "Refreshing…"

    refresh_btn.on_click(_refresh)

    @guard
    def _poll():
        v = bus_client.read_version("portfolio:positions")
        if v != state["ver"]:
            state["ver"] = v
            state["payload"] = bus_client.read("portfolio:positions") or None
            _repaint()

    # Initial paint from cache (graceful-empty when the service is cold).
    state["ver"] = bus_client.read_version("portfolio:positions")
    state["payload"] = bus_client.read("portfolio:positions") or None
    _repaint()
    ui.timer(2.0, _poll)
