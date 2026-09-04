"""Sector & Industry Performance (/sentiment/sectors) — the heat-tile grid.

Tier-1 reader: this page holds **no engine calls and no app ``scoring`` import**.
The sector/industry quotes, trends, put/call and rotation are computed in
``services/sentiment_svc`` and cached; this page only **formats** them. Cache
view read: ``sentiment:sectors`` → ``{"sector", "industries", "sector_at",
"summary"}`` (see ``services/sentiment_svc/handlers``).

**The design.** Day / Week / Month render as three adjacent filled tiles flush to
the right edge, so the colour band is continuous across a row and down the page,
and each figure sits inside its own tile. Intensity is normalised *per column*
against that column's own largest magnitude across sectors **and** industries, so
a column always uses its full range and expanding a sector never repaints the
rows above it. Below a per-horizon flat band a cell drops to neutral. All of that
arithmetic is pure and lives in ``pages/sector_heat.py``; this module is widgets
and wiring only.

**Two departures from the earlier table, both deliberate.** The RRG quadrant
column is gone — the relative-rotation read now has two dedicated pages
(``/sentiment/rrg``, ``/sentiment/rotation``) that show it properly, and a
one-word quadrant next to a colour band invited reading it as a fourth
timeframe. Put/call keeps a plain number with an amber tint above 1.5 rather
than a tile, for the same reason: it is a ratio, not a percentage change.

Tailwind-first per house rules, and this page needs **no** ``ui.add_css``
escape-hatch at all — the fractional column tracks, the flush tiles, the
truncation and the scroll wrapper are all utilities. The chrome palette and the
two faces come from ``config/theme.toml [sectors]``.
"""
import bus_client
from nicegui import ui
from pages import busy as _busy
from pages import sector_heat as H
from pages.options.theme import SECTOR_FONT_HEAD_HTML, SECTOR_TOKENS as _T
from pages.sentiment import industry_rows, sector_table_rows
from pages.view_watch import watch_view
from pages.ui_guard import guard
from pages import copy as _copy  # the ONE copy (pages/copy.py)

VIEW = "sentiment:sectors"

# The one column template, shared by the header and every row — written once so
# a sector row and an industry row cannot drift out of alignment. Fractional
# tracks on the two text columns; the three tiles are fixed so the colour band
# keeps a constant width as the viewport changes.
GRID = ("grid grid-cols-[minmax(200px,1.5fr)_58px_minmax(150px,2fr)_70px"
        "_112px_112px_112px] w-full")
# The band must never tear away from its row, so the grid scrolls as a unit
# rather than wrapping.
MIN_W = "min-w-[860px]"

COLS = H.COLUMNS                      # ("day", "week", "month")
_HEAD = {"day": "Day", "week": "Week", "month": "Month"}

# Regime headline tone → text/background class. A finite map, per the house rule
# on data-driven colour.
_TONE_TXT = {"up": _T["SC_UP"], "down": _T["SC_DN"],
             "warn": _T["SC_WARN"], "muted": _T["SC_FAINT"]}
_TONE_BG = {"up": _T["SC_UP_BG"], "down": _T["SC_DN_BG"],
            "warn": _T["SC_WARN_BG"], "muted": _T["SC_DIM_BG"]}
_PCR_TXT = {"warn": _T["SC_WARN"], "plain": _T["SC_DIM"], "muted": _T["SC_FAINT"]}

_LABEL = (f"{_T['SC_MONO']} {_T['SC_FAINT']} text-[9.5px] tracking-[.2em] "
          "uppercase leading-none whitespace-nowrap")
_BTN = (f"{_T['SC_MONO']} {_T['SC_DIM']} hover:{_T['SC_TXT']} border "
        f"{_T['SC_EDGE_HI']} bg-transparent h-[30px] px-4 text-[10px] "
        "tracking-[.16em] leading-none")
_TILE = (f"{_T['SC_MONO']} flex items-center justify-center h-full "
         "tabular-nums leading-none")


def render():
    state = {"sector": None, "industries": {}, "sector_at": None, "summary": {},
             "ver": None, "expanded": set(), "sort": "day", "desc": True}

    ui.add_head_html(SECTOR_FONT_HEAD_HTML)

    def _read_cache():
        payload = bus_client.read(VIEW) or {}
        state["sector"] = payload.get("sector")
        state["industries"] = payload.get("industries") or {}
        state["sector_at"] = payload.get("sector_at")
        state["summary"] = payload.get("summary") or {}

    _read_cache()

    wrap = ui.column().classes(
        f"{_T['SC_SANS']} {_T['SC_VOID_BG']} w-full gap-0 pb-16 rounded-lg "
        "overflow-hidden")

    with wrap:
        # ── header ──────────────────────────────────────────────────────────
        with ui.column().classes("w-full gap-0 px-7 pt-6 pb-5"):
            eyebrow_lbl = ui.label("").classes(_LABEL)
            with ui.row().classes("items-center w-full no-wrap gap-4 mt-3"):
                ui.label("Sector & Industry Performance").classes(
                    f"{_T['SC_TXT']} text-[29px] font-bold leading-tight "
                    "tracking-[-0.01em]")
                ui.space()
                ui.button("Refresh", color=None, on_click=lambda: _request_refresh()) \
                    .props("flat no-caps dense").classes(_BTN)
                ui.button("Expand all", color=None, on_click=lambda: _expand_all()) \
                    .props("flat no-caps dense").classes(_BTN)
                ui.button("Collapse", color=None, on_click=lambda: _collapse_all()) \
                    .props("flat no-caps dense").classes(_BTN)
            with ui.row().classes("items-center w-full no-wrap gap-2.5 mt-5"):
                regime_dot = ui.element("div").classes(
                    f"w-[7px] h-[7px] rounded-full shrink-0 {_TONE_BG['muted']}")
                regime_lbl = ui.label("").classes(
                    f"{_T['SC_TXT']} text-[13.5px] font-semibold leading-none "
                    "whitespace-nowrap")
                regime_detail = ui.label("").classes(
                    f"{_T['SC_MONO']} {_T['SC_FAINT']} text-[11.5px] leading-none "
                    "tabular-nums whitespace-nowrap")
                ui.space()
                # Breadth + the cap-weighted move: the grid is UNWEIGHTED, so
                # eight green micro-sectors against three red mega-caps read
                # bullish there and are not. Neither number is recoverable from
                # the tiles, which is why the line survives the redesign.
                summary_lbl = ui.label("").classes(
                    f"{_T['SC_MONO']} {_T['SC_FAINT']} text-[11px] leading-none "
                    "tabular-nums whitespace-nowrap")

        # ── grid ────────────────────────────────────────────────────────────
        with ui.element("div").classes("w-full overflow-x-auto px-7"):
            with ui.column().classes(f"{MIN_W} w-full gap-0"):
                head_box = ui.element("div").classes(
                    f"{GRID} h-[34px] border-b {_T['SC_EDGE_HI']}")
                grid_box = ui.column().classes("w-full gap-0")

    sectors_busy = _busy.build_busy(grid_box, "Refreshing sectors…")

    # ── header row (rebuilt on a sort change so the marker follows) ─────────
    def _render_head():
        head_box.clear()
        with head_box:
            for text in ("Sector", "ETF", "Composition"):
                ui.label(text).classes(f"{_LABEL} flex items-center"
                                       + (" pl-7" if text == "Sector" else ""))
            ui.label("P/C").classes(f"{_LABEL} flex items-center justify-end pr-4")
            for field in COLS:
                active = state["sort"] == field
                mark = (" ↓" if state["desc"] else " ↑") if active else ""
                cls = (f"{_T['SC_MONO']} text-[9.5px] tracking-[.2em] uppercase "
                       "leading-none whitespace-nowrap flex items-center "
                       "justify-center cursor-pointer select-none "
                       + (_T["SC_TXT"] if active else _T["SC_FAINT"]))
                ui.label(_HEAD[field] + mark).classes(cls) \
                    .on("click", lambda _e, f=field: _sort_by(f))

    # ── rows ────────────────────────────────────────────────────────────────
    def _render_rows():
        grid_box.clear()
        sec = state["sector"]
        if not sec:
            with grid_box:
                ui.label(_copy.WAITING_SENTIMENT).classes(
                    f"{_T['SC_FAINT']} text-[13px] py-8")
            return
        sd = sec["sector_data"]
        rows = sector_table_rows(sd, sec["quotes"], sec["trends"], sec["pcr"],
                                 sec["quadrants"])
        # Industries are precomputed in the cache view, so building every
        # sector's rows here costs no fetch — and it is what lets the colour
        # scale span industries whether or not they are on screen.
        inds = {r["sector"]: _industry_rows(sd, r["sector"]) for r in rows}
        scales = H.column_scales(H.all_heat_values(rows, inds))
        rows = H.sort_rows(rows, state["sort"], state["desc"])
        total = len(rows)

        with grid_box:
            for i, r in enumerate(rows):
                _sector_row(r, i, total, scales)
                if r["sector"] in state["expanded"]:
                    for ir in inds.get(r["sector"]) or []:
                        _industry_row(ir, scales)
                    if not inds.get(r["sector"]):
                        ui.label("No industry data").classes(
                            f"{_T['SC_FAINT']} text-[11px] pl-[52px] py-2.5 "
                            f"border-b {_T['SC_EDGE']}")

    def _industry_rows(sd, sector_name):
        ind = (state["industries"] or {}).get(sector_name) or {}
        return industry_rows(sd, sector_name, ind.get("quotes"),
                             ind.get("trends"), ind.get("pcr"),
                             ind.get("quadrants"))

    def _sector_row(r, index, total, scales):
        name = r["sector"]
        expanded = name in state["expanded"]
        row = ui.element("div").classes(
            f"{GRID} h-[66px] border-b {_T['SC_EDGE']} cursor-pointer "
            "hover:bg-white/[0.025]")
        row.on("click", lambda _e, s=name: _toggle(s))
        with row:
            with ui.column().classes("justify-center gap-[5px] h-full pl-7 pr-3 "
                                     "overflow-hidden"):
                with ui.row().classes("items-center no-wrap gap-1.5"):
                    ui.icon("expand_more" if expanded else "chevron_right") \
                        .classes(f"{_T['SC_FAINT']} text-[17px] -ml-[22px]")
                    ui.label(str(name or "")).classes(
                        f"{_T['SC_TXT']} text-[14.5px] font-semibold leading-tight")
                ui.label(H.rank_line(index, total, state["sort"])).classes(
                    f"{_T['SC_MONO']} {_T['SC_FAINT']} text-[8.5px] "
                    "tracking-[.14em] leading-none")
            ui.label(str(r["etf"] or "")).classes(
                f"{_T['SC_MONO']} {_T['SC_DIM']} text-[11px] tracking-[.06em] "
                "flex items-center")
            ui.label(str(r["desc"] or "")).classes(
                f"{_T['SC_FAINT']} text-[12px] flex items-center truncate pr-4")
            ui.label(H.fmt_pcr(r["pcr"])).classes(
                f"{_T['SC_MONO']} {_PCR_TXT[H.pcr_tone(r['pcr'])]} text-[12px] "
                "tabular-nums flex items-center justify-end pr-4")
            for field in COLS:
                bg, txt = H.heat_classes(r[field], scales[field],
                                         H.FLAT_BAND[field])
                ui.label(H.fmt_pct(r[field])).classes(
                    f"{_TILE} {bg} {txt} text-[13px] font-medium")

    def _industry_row(ir, scales):
        with ui.element("div").classes(
                f"{GRID} h-[34px] border-b {_T['SC_EDGE']} hover:bg-white/[0.02]"):
            with ui.row().classes("items-center no-wrap h-full pl-7 pr-3 "
                                  "overflow-hidden"):
                # The rule is the indent: it ties the industry to the sector
                # above it without stealing a whole column to say so.
                ui.element("div").classes(
                    f"w-px h-[22px] {_T['SC_EDGE_HI'].replace('border-', 'bg-')} "
                    "mr-3.5 shrink-0")
                ui.label(str(ir["label"] or "")).classes(
                    f"{_T['SC_DIM']} text-[12.5px] leading-none truncate")
            ui.label(str(ir["etf"] or "")).classes(
                f"{_T['SC_MONO']} {_T['SC_FAINT']} text-[10px] tracking-[.06em] "
                "flex items-center")
            ui.label(str(ir["desc"] or "")).classes(
                f"{_T['SC_FAINT']} text-[11px] flex items-center truncate pr-4")
            ui.label(H.fmt_pcr(ir["pcr"])).classes(
                f"{_T['SC_MONO']} {_PCR_TXT[H.pcr_tone(ir['pcr'])]} text-[11px] "
                "tabular-nums flex items-center justify-end pr-4")
            for field in COLS:
                bg, txt = H.heat_classes(ir[field], scales[field],
                                         H.FLAT_BAND[field])
                ui.label(H.fmt_pct(ir[field])).classes(
                    f"{_TILE} {bg} {txt} text-[11.5px]")

    # ── interaction ─────────────────────────────────────────────────────────
    @guard
    def _toggle(name):
        state["expanded"].symmetric_difference_update({name})
        _render_rows()

    @guard
    def _sort_by(field):
        # Clicking the active column reverses it; a new column starts descending,
        # which for a performance table is the reading everyone wants first.
        if state["sort"] == field:
            state["desc"] = not state["desc"]
        else:
            state["sort"], state["desc"] = field, True
        _render_head()
        _render_rows()

    @guard
    def _expand_all():
        sec = state["sector"]
        if not sec:
            return
        for r in sector_table_rows(sec["sector_data"], sec["quotes"],
                                   sec["trends"], sec["pcr"], sec["quadrants"]):
            state["expanded"].add(r["sector"])
        _render_rows()

    @guard
    def _collapse_all():
        state["expanded"].clear()
        _render_rows()

    @guard
    def _request_refresh():
        bus_client.request("sentiment", {"type": "refresh"})
        ui.notify("Refreshing — the page updates when the new read lands.")
        sectors_busy.show()

    # ── paint ───────────────────────────────────────────────────────────────
    def _apply():
        sectors_busy.hide()
        eyebrow_lbl.text = H.eyebrow(state["sector_at"])
        sec = state["sector"] or {}
        summary_lbl.text = H.summary_line(sec.get("sector_data"),
                                          sec.get("quotes"), state["summary"])
        rot = (state["sector"] or {}).get("rotation")
        word, tone, detail = H.regime_headline(rot)
        regime_lbl.text = word
        regime_detail.text = detail
        regime_dot.classes(remove=" ".join(dict.fromkeys(_TONE_BG.values())),
                           add=_TONE_BG[tone])
        regime_lbl.classes(remove=" ".join(dict.fromkeys(_TONE_TXT.values())),
                           add=_T["SC_TXT"] if tone != "muted" else _T["SC_FAINT"])
        _render_head()
        _render_rows()

    _apply()
    # Version-gated repaint + seed, in one line (pages/view_watch.py).
    # This idiom -- probe :ver, compare, re-read, repaint, hang it on a
    # 2 s timer -- was written out longhand on 22 pages.
    watch_view(VIEW, lambda: (_read_cache(), _apply()))
