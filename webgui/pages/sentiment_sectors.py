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

**On the page kit since 2026-09-19** (the consistency standard,
``docs/plans/2026-09-19-app-ui-consistency-design.md``): the header line carries
the name and the Updated stamp, the eyebrow and the regime line are the status
row, the two grid controls sit in a control bar and ONE ``kit.region`` covers
the grid a repaint replaces. The page's own ground, two faces and grey ladder
are gone; it wears the app surface like every other screen.

⚠ **The DATA colours are untouched and must stay so.** The oklch heat ramp
(``sector_heat.HEAT_BG`` / ``HEAT_TXT``) never lived in the TOML at all, and
``[sectors] up`` / ``dn`` / ``warn`` are the regime word, the regime dot and the
put-heavy amber. Row hairlines, the header rule and the industry indent are
chart FURNITURE and take app border tokens.

⚠ **The eyebrow no longer carries a clock.** It used to end ``· 16:00 ET`` —
the only Eastern clock in an app whose every other stamp is Central. The header
stamp owns the time now, in CT; the eyebrow keeps the market's DATE.

Tailwind-first per house rules, and this page needs **no** ``ui.add_css``
escape-hatch at all — the fractional column tracks, the flush tiles, the
truncation and the scroll wrapper are all utilities.
"""
import bus_client
from nicegui import ui
from pages import sector_heat as H
from pages import ui_kit as kit
from pages.options import theme
from pages.options.theme import SECTOR_TOKENS as _T
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

_P = theme.THEME["palette"]
# Chart furniture, not data. A row hairline and the header rule draw a FRAME, so
# they take app border tokens — and the header rule and the industry indent sit
# one step brighter, on the button border, for the reason Task 1 gave the RRG's
# crosshair: each marks a structural boundary, where a row hairline only
# separates two rows of the same kind.
_ROW_EDGE = f"border-[{_P['card_border']}]"
_HEAD_RULE = f"border-[{_P['btn_border']}]"
_INDENT_RULE = f"bg-[{_P['btn_border']}]"
# The faint end of the app's text ladder — the colour ``kit.EYEBROW`` wears.
_FAINT = f"text-[{_P['icon']}]"

# Regime headline tone → text/background class. A finite map, per the house rule
# on data-driven colour. ⚠ ``up`` / ``down`` / ``warn`` are DATA — the TOML
# names ``[sectors] up`` as the "risk-on regime word + dot" — and must not move.
# ``muted`` is the ABSENCE of a reading, so it takes the app's muted grey.
_TONE_TXT = {"up": _T["SC_UP"], "down": _T["SC_DN"],
             "warn": _T["SC_WARN"], "muted": theme.MUTED}
_TONE_BG = {"up": _T["SC_UP_BG"], "down": _T["SC_DN_BG"],
            "warn": _T["SC_WARN_BG"], "muted": f"bg-[{_P['muted']}]"}
_PCR_TXT = {"warn": _T["SC_WARN"], "plain": theme.MUTED, "muted": _FAINT}

_LABEL = (f"{_FAINT} text-[9.5px] tracking-[.2em] uppercase leading-none "
          "whitespace-nowrap")
# ``tabular-nums`` is what aligns the digits down a column; it is a numeric
# variant of whatever face is in use, so the band stays aligned on the app font
# now that the page-scoped mono face is gone.
_TILE = "flex items-center justify-center h-full tabular-nums leading-none"


def render():
    # Whether this render may command the sentiment service at all — resolved
    # ONCE, so the button and its handler cannot disagree. False on the public
    # live origin, where a Refresh is eleven sector chains plus their histories
    # per click against the owner's Schwab budget. See ``shell.may_enqueue``.
    import shell as _shell
    _may_enqueue = _shell.may_enqueue()

    state = {"sector": None, "industries": {}, "sector_at": None, "summary": {},
             "expanded": set(), "sort": "day", "desc": True}

    def _read_cache():
        payload = bus_client.read(VIEW) or {}
        state["sector"] = payload.get("sector")
        state["industries"] = payload.get("industries") or {}
        state["sector_at"] = payload.get("sector_at")
        state["summary"] = payload.get("summary") or {}

    _read_cache()

    with kit.page():
        # No description line: what this grid is and how to read it is the
        # opening of page_help.HELP_MD["/sentiment/sectors"].
        #
        # stale=False although the view is on a SCHEDULE: sentiment_svc
        # publishes it hourly (``scheduler.SECTORS_MINUTE = 38``) and
        # ``alerts.STALE_OVERRIDES`` has no entry for it, so the 600 s default
        # would paint the stamp amber for ~50 minutes of every hour. Turning it
        # on needs an override of ~70 min first.
        head = kit.header("Sector & Industry", view=VIEW, stale=False)
        # Not drawn on the public live origin — see shell.may_enqueue.
        if _may_enqueue:
            with head.actions:
                kit.button("Refresh", kind="secondary", icon="refresh",
                           on_click=lambda: _request_refresh())
        # Expand all / Collapse are NOT page actions: they command nothing and
        # operate on the grid, so they sit in the control bar under the header
        # rather than beside Refresh. They stay on the public origin too — they
        # are the only way to read the industries under a sector.
        with kit.control_bar():
            kit.button("Expand all", kind="secondary", icon="unfold_more",
                       on_click=lambda: _expand_all())
            kit.button("Collapse", kind="secondary", icon="unfold_less",
                       on_click=lambda: _collapse_all())

        # Keeps its runtime text ("MARKET STRUCTURE · AUG 17, 2026"): that is
        # the READING's date, not the page's freshness, which the header stamp
        # answers. The clock that used to follow it is gone.
        eyebrow_lbl = kit.status_line()
        with ui.row().classes("items-center w-full no-wrap gap-2.5"):
            regime_dot = ui.element("div").classes(
                f"w-[7px] h-[7px] rounded-full shrink-0 {_TONE_BG['muted']}")
            regime_lbl = ui.label("").classes(
                f"{_TONE_TXT['muted']} text-[13.5px] font-semibold "
                "leading-none whitespace-nowrap")
            regime_detail = ui.label("").classes(
                f"{_FAINT} text-[11.5px] leading-none tabular-nums "
                "whitespace-nowrap")
            ui.space()
            # Breadth + the cap-weighted move: the grid is UNWEIGHTED, so
            # eight green micro-sectors against three red mega-caps read
            # bullish there and are not. Neither number is recoverable from
            # the tiles, which is why the line survives the redesign.
            summary_lbl = ui.label("").classes(
                f"{_FAINT} text-[11px] leading-none tabular-nums "
                "whitespace-nowrap")

        # ── grid ────────────────────────────────────────────────────────────
        # The spinner lives on the region's OUTER element, so ``_render_rows``'
        # ``grid_box.clear()`` cannot delete it. That was this page's bug from
        # the 2026-08-17 rebuild until now, and worse here than on the sibling
        # screens: ``_render_rows`` is also the repaint for ``_toggle``,
        # ``_sort_by``, ``_expand_all`` and ``_collapse_all``, so the scrim died
        # on every interaction, not only on the build paint.
        grid = kit.region("Refreshing sectors…")
        with grid.content:
            with ui.element("div").classes("w-full overflow-x-auto"):
                with ui.column().classes(f"{MIN_W} w-full gap-0"):
                    head_box = ui.element("div").classes(
                        f"{GRID} h-[34px] border-b {_HEAD_RULE}")
                    grid_box = ui.column().classes("w-full gap-0")

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
                cls = ("text-[9.5px] tracking-[.2em] uppercase "
                       "leading-none whitespace-nowrap flex items-center "
                       "justify-center cursor-pointer select-none "
                       + (theme.LABEL if active else _FAINT))
                ui.label(_HEAD[field] + mark).classes(cls) \
                    .on("click", lambda _e, f=field: _sort_by(f))

    # ── rows ────────────────────────────────────────────────────────────────
    def _render_rows():
        grid_box.clear()
        sec = state["sector"]
        if not sec:
            with grid_box:
                kit.empty(_copy.WAITING_SENTIMENT)
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
                            f"{_FAINT} text-[11px] pl-[52px] py-2.5 "
                            f"border-b {_ROW_EDGE}")

    def _industry_rows(sd, sector_name):
        ind = (state["industries"] or {}).get(sector_name) or {}
        return industry_rows(sd, sector_name, ind.get("quotes"),
                             ind.get("trends"), ind.get("pcr"),
                             ind.get("quadrants"))

    def _sector_row(r, index, total, scales):
        name = r["sector"]
        expanded = name in state["expanded"]
        row = ui.element("div").classes(
            f"{GRID} h-[66px] border-b {_ROW_EDGE} cursor-pointer "
            "hover:bg-white/[0.025]")
        row.on("click", lambda _e, s=name: _toggle(s))
        with row:
            with ui.column().classes("justify-center gap-[5px] h-full pl-7 pr-3 "
                                     "overflow-hidden"):
                with ui.row().classes("items-center no-wrap gap-1.5"):
                    ui.icon("expand_more" if expanded else "chevron_right") \
                        .classes(f"{_FAINT} text-[17px] -ml-[22px]")
                    ui.label(str(name or "")).classes(
                        f"{theme.LABEL} text-[14.5px] font-semibold leading-tight")
                ui.label(H.rank_line(index, total, state["sort"])).classes(
                    f"{_FAINT} text-[8.5px] tracking-[.14em] leading-none")
            ui.label(str(r["etf"] or "")).classes(
                f"{theme.MUTED} text-[11px] tracking-[.06em] flex items-center")
            ui.label(str(r["desc"] or "")).classes(
                f"{_FAINT} text-[12px] flex items-center truncate pr-4")
            ui.label(H.fmt_pcr(r["pcr"])).classes(
                f"{_PCR_TXT[H.pcr_tone(r['pcr'])]} text-[12px] "
                "tabular-nums flex items-center justify-end pr-4")
            for field in COLS:
                bg, txt = H.heat_classes(r[field], scales[field],
                                         H.FLAT_BAND[field])
                ui.label(H.fmt_pct(r[field])).classes(
                    f"{_TILE} {bg} {txt} text-[13px] font-medium")

    def _industry_row(ir, scales):
        with ui.element("div").classes(
                f"{GRID} h-[34px] border-b {_ROW_EDGE} hover:bg-white/[0.02]"):
            with ui.row().classes("items-center no-wrap h-full pl-7 pr-3 "
                                  "overflow-hidden"):
                # The rule is the indent: it ties the industry to the sector
                # above it without stealing a whole column to say so.
                ui.element("div").classes(
                    f"w-px h-[22px] {_INDENT_RULE} mr-3.5 shrink-0")
                ui.label(str(ir["label"] or "")).classes(
                    f"{theme.MUTED} text-[12.5px] leading-none truncate")
            ui.label(str(ir["etf"] or "")).classes(
                f"{_FAINT} text-[10px] tracking-[.06em] flex items-center")
            ui.label(str(ir["desc"] or "")).classes(
                f"{_FAINT} text-[11px] flex items-center truncate pr-4")
            ui.label(H.fmt_pcr(ir["pcr"])).classes(
                f"{_PCR_TXT[H.pcr_tone(ir['pcr'])]} text-[11px] "
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
        if not _may_enqueue:
            return          # no button either — see shell.may_enqueue
        bus_client.request("sentiment", {"type": "refresh"})
        # No toast: the region's spinner already says the page is waiting, and
        # the standard keeps a toast for the OUTCOME of an action.
        grid.busy.show()

    # ── paint ───────────────────────────────────────────────────────────────
    def _apply():
        grid.busy.hide()
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
        # The word wears its OWN tone. It used to be painted one flat colour
        # whatever the regime was: ``_TONE_TXT``'s values were in the remove
        # set and never in the add, so the map was half-live.
        regime_lbl.classes(remove=" ".join(dict.fromkeys(_TONE_TXT.values())),
                           add=_TONE_TXT[tone])
        _render_head()
        _render_rows()

    _apply()
    # Version-gated repaint + seed, in one line (pages/view_watch.py).
    # This idiom -- probe :ver, compare, re-read, repaint, hang it on a
    # 2 s timer -- was written out longhand on 22 pages.
    watch_view(VIEW, lambda: (_read_cache(), _apply()))
