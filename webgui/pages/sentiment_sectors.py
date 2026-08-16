"""Sector & Industry Performance page (under Market Trend & Sentiment).

Tier-3 reader: this page holds **no engine calls and no app ``scoring`` import**.
The sector/industry quotes, trends, P/C, RRG quadrants, rotation and the
cap-weighted summary are computed in ``services/sentiment_svc`` and cached; this
page only **formats** them. Cache view read:

* ``sentiment:sectors`` → ``{"sector", "industries", "sector_at", "summary"}``
  (see ``services/sentiment_svc/handlers`` + ``compute.derive_sector_summary``).

The pure display transforms live in ``pages.sentiment`` (``sector_table_rows``,
``sector_summary``, ``rotation_banner``, ``industry_rows``, the color helpers) —
they are shared with the Sentiment page and unit-tested in
``tests/test_sentiment_sectors.py``. This module reuses them and wires the
expandable table, a Refresh button that enqueues a ``cmd:sentiment`` command, and
a fetch-free version-poll ``ui.timer`` that repaints when the bus cache version
changes.
"""
import bus_client
from pages import busy as _busy
from pages.options.theme import BTN_3D
from pages.sentiment import (
    BORDER_R, SENT_TEXT_CLASSES, industry_rows, pcr_text_class, pct_text_class,
    rotation_banner, rotation_text_class, rrg_text_class, sector_summary,
    sector_table_rows,
)
from pages.ui_guard import guard

SEC_COLS = [("sector", "Sector", 140), ("etf", "ETF", 50),
            ("desc", "Description", 200), ("day", "Day %", 70),
            ("week", "Week %", 70), ("month", "Month %", 70),
            ("pcr", "P/C", 56), ("rrg", "RRG", 90)]
# Single source of truth for column widths so the header (driven by SEC_COLS)
# and the data-row cells never drift. ``desc`` is the flex column (its tuple
# width is unused — both header and rows render it ``flex-1 min-w-[160px]``).
SEC_W = {field: w for field, _label, w in SEC_COLS}


def render():
    from nicegui import ui

    state = {"sector": None, "industries": {}, "sector_summary": {},
             "expanded": set(), "ver": None}

    def _read_cache():
        sectors = bus_client.read("sentiment:sectors") or {}
        state["sector"] = sectors.get("sector")
        state["industries"] = sectors.get("industries") or {}
        state["sector_summary"] = sectors.get("summary") or {}

    _read_cache()

    with ui.row().classes("items-center gap-3 w-full"):
        ui.label("Sector & Industry Performance").classes("text-h6")
        ui.space()
        ui.button("Refresh", icon="refresh", color=None,
                  on_click=lambda: _request_refresh()).props("no-caps").classes(BTN_3D)
        ui.button("Expand All", color=None, on_click=lambda: _expand_all()).props("no-caps").classes(BTN_3D)
        ui.button("Collapse All", color=None, on_click=lambda: _collapse_all()).props("no-caps").classes(BTN_3D)
    with ui.row().classes("items-center gap-3 w-full"):
        summary_lbl = ui.label("").classes("opacity-80 text-sm")
    rotation_lbl = ui.label("").classes("text-sm")
    sector_box = ui.column().classes("w-full q-gutter-none q-mt-sm")

    def _render_sector_table():
        sec = state["sector"]
        sector_box.clear()
        if not sec:
            with sector_box:
                ui.label("Waiting for sentiment service…").classes("opacity-60 text-sm")
            return
        sd = sec["sector_data"]
        rows = sector_table_rows(sd, sec["quotes"], sec["trends"], sec["pcr"], sec["quadrants"])
        with sector_box:
            with ui.row().classes("items-center w-full no-wrap gap-2 opacity-60 text-xs"):
                ui.label("").classes("w-[24px]")
                for _f, hdr, w in SEC_COLS:
                    if _f == "desc":
                        ui.label(hdr).classes("flex-1 min-w-[160px]")
                    else:
                        ui.label(hdr).classes(f"w-[{w}px]")
            for r in rows:
                sector_name = r["sector"]
                expanded = sector_name in state["expanded"]
                dc = pct_text_class(r["day"])  # name/etf/desc share the Day % color
                with ui.row().classes(
                        "items-center w-full no-wrap gap-2 text-sm "
                        "border-b border-white/5 hover:bg-white/[0.04]"):
                    ui.icon("keyboard_arrow_down" if expanded else "keyboard_arrow_right") \
                        .classes(f"cursor-pointer w-[24px] {BORDER_R}") \
                        .on("click", lambda _e, s=sector_name: _toggle_sector(s))
                    ui.label(str(sector_name or "")).classes(f"w-[{SEC_W['sector']}px] {BORDER_R} {dc}")
                    ui.label(str(r["etf"] or "")).classes(f"w-[{SEC_W['etf']}px] {BORDER_R} {dc}")
                    ui.label(str(r["desc"] or "")).classes(
                        f"flex-1 min-w-[160px] {BORDER_R} "
                        f"overflow-hidden text-ellipsis whitespace-nowrap {dc}")
                    for fld in ("day", "week", "month"):
                        v = r[fld]
                        ui.label(f"{v:+.2f}%" if v is not None else "—") \
                            .classes(f"w-[{SEC_W[fld]}px] {BORDER_R} {pct_text_class(v)}")
                    pv = r["pcr"]
                    ui.label(f"{pv:.2f}" if pv is not None else "").classes(
                        f"w-[{SEC_W['pcr']}px] {BORDER_R} {pcr_text_class(pv)}")
                    rv = r["rrg"]
                    ui.label(str(rv or "")).classes(f"w-[{SEC_W['rrg']}px] {rrg_text_class(rv)}")
                if expanded:
                    # Industries come PRECOMPUTED in the sectors cache view
                    # ({"quotes","trends","pcr","quadrants"} per sector name) —
                    # no proxy call here.
                    ind = (state["industries"] or {}).get(sector_name)
                    if not ind:
                        with ui.row().classes("items-center w-full no-wrap gap-2 text-xs opacity-60"):
                            ui.label("").classes("w-[24px]")
                            ui.label("no industry data").classes("w-[200px]")
                    else:
                        for ir in industry_rows(sd, sector_name, ind.get("quotes"), ind.get("trends"), ind.get("pcr"), ind.get("quadrants")):
                            idc = pct_text_class(ir["day"])  # industry name/etf/desc share its Day % color
                            with ui.row().classes(
                                    "items-center w-full no-wrap gap-2 text-xs "
                                    "border-b border-white/5 hover:bg-white/[0.04] bg-white/[0.02]"):
                                ui.label("").classes(f"w-[24px] {BORDER_R}")
                                ui.label(str(ir["label"] or "")).classes(
                                    f"w-[{SEC_W['sector']}px] pl-[14px] {BORDER_R} opacity-85 {idc}")
                                ui.label(str(ir["etf"] or "")).classes(f"w-[{SEC_W['etf']}px] {BORDER_R} {idc}")
                                ui.label(str(ir["desc"] or "")).classes(
                                    f"flex-1 min-w-[160px] {BORDER_R} "
                                    f"overflow-hidden text-ellipsis whitespace-nowrap opacity-80 {idc}")
                                for fld in ("day", "week", "month"):
                                    v = ir[fld]
                                    ui.label(f"{v:+.2f}%" if v is not None else "—") \
                                        .classes(f"w-[{SEC_W[fld]}px] {BORDER_R} {pct_text_class(v)}")
                                pv = ir["pcr"]
                                ui.label(f"{pv:.2f}" if pv is not None else "").classes(
                                    f"w-[{SEC_W['pcr']}px] {BORDER_R} {pcr_text_class(pv)}")
                                rv = ir["rrg"]
                                ui.label(str(rv or "")).classes(f"w-[{SEC_W['rrg']}px] {rrg_text_class(rv)}")

    @guard
    def _toggle_sector(sector_name):
        if sector_name in state["expanded"]:
            state["expanded"].discard(sector_name)
        else:
            state["expanded"].add(sector_name)
        _render_sector_table()

    @guard
    def _expand_all():
        if not state["sector"]:
            return
        for r in sector_table_rows(state["sector"]["sector_data"], state["sector"]["quotes"],
                                   state["sector"]["trends"], state["sector"]["pcr"],
                                   state["sector"]["quadrants"]):
            state["expanded"].add(r["sector"])
        _render_sector_table()

    @guard
    def _collapse_all():
        state["expanded"].clear()
        _render_sector_table()

    sectors_busy = _busy.build_busy(sector_box, "Refreshing sectors…")

    def _apply():
        sectors_busy.hide()
        sec = state["sector"]
        if not sec:
            summary_lbl.text = ""
            rotation_lbl.text = ""
            _render_sector_table()
            return
        sd, quotes = sec["sector_data"], sec["quotes"]
        summary_lbl.text = sector_summary(sd, quotes, state.get("sector_summary"))
        regime, color, detail = rotation_banner(sec["rotation"])
        rotation_lbl.text = f"{regime} — {detail}"
        rotation_lbl.classes(remove=SENT_TEXT_CLASSES, add=rotation_text_class(color))
        _render_sector_table()

    @guard
    def _request_refresh():
        bus_client.request("sentiment", {"type": "refresh"})
        ui.notify("Refresh requested")
        sectors_busy.show()

    @guard
    def _maybe_repaint():
        ver = bus_client.read_version("sentiment:sectors")
        if ver == state["ver"]:
            return
        state["ver"] = ver
        _read_cache()
        _apply()

    state["ver"] = bus_client.read_version("sentiment:sectors")
    _apply()
    ui.timer(2.0, _maybe_repaint)
