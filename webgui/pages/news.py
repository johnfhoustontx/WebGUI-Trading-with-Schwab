"""Market News — headlines, SEC filings and the economic calendar.

Tier-1: nicegui + bus_client + shell + app_settings + ``shared.news_config`` +
``pages.*`` only. Reads three views - ``cache:news:feed`` (``news_view.VIEW``,
the headline list), ``cache:news:sec`` (``VIEW_SEC``, the SEC / EDGAR panel)
and ``cache:news:calendar`` (``VIEW_CAL``, the calendar tiles) - each on its
own ``watch_view``. The ONE write is Refresh → ``news_refresh`` on cmd:news,
built only when ``shell.may_enqueue()`` says this process may.
``render(public=True)`` hands off to ``news_live`` before anything here builds.

Layout (design §3): the headline list in the LEFT column (its control bar and
Trending filter it alone); the SEC panel in the RIGHT column's top half, the
calendar in its bottom half, each scrolling on its own at ``lg``. Below ``lg``
the three stack in that DOM order. Each headline is ONE line - time (Central),
impact pill, tickers, the headline truncated with an ellipsis, source badges;
the teaser (or a filing's summary) is on the headline's hover.

Every fact comes from ``pages/news_view.py`` (pure); this module holds widgets
and wiring. ``draw_rows`` / ``draw_sec_rows`` / ``draw_calendar`` are
module-level because the public screen draws the same regions.

⚠ A title, teaser, detail line or calendar title is THIRD-PARTY TEXT. It reaches
the page only through ``ui.label`` / ``ui.link``, which escape — never
``ui.html``, and a source-level test pins that this module calls ``ui.html``
nowhere. A link's target is a feed's URL too, and ``ui.link`` does NOT escape
an href, so only what ``news_view.safe_href`` passes (http/https with a host)
becomes a link; anything else (a ``javascript:`` URL included) renders the
title as plain text.

Built on the page kit (``pages/ui_kit.py``): the header line carries the title,
the Updated stamp and Refresh; the filters sit in a control bar; the status line
carries counts only; one region per panel holds what a publish replaces. The
stamp is not ``stale``-aware: the collector's cadence moves with the session (5
min in regular hours, 60 at the weekend), so an old stamp on a Sunday is not a
fault.
"""
from __future__ import annotations

import datetime as _dt
import math
from urllib.parse import quote

from pages import copy as _copy  # the ONE copy (pages/copy.py)
from pages import news_view as nv
from pages.options import theme as _t

PAGE_SIZE = 60
# How often an open tab redraws what it already holds. A headline's stamp drops
# its date at midnight Central, filings age and calendar events pass while a
# tab sits open; the views only republish when their CONTENT changes. A redraw
# from the held payloads - never a bus read.
REPAINT_SEC = 60
# Tickers drawn inline on one headline row; the rest collapse into "+N" with
# the full list on hover (four chips squeezed the headline to nothing).
MAX_ROW_TICKERS = 2
# How long Refresh may spin before the backstop gives the button back. One poll
# of every feed walks the Yahoo per-ticker feeds and SEC's paced requests, so it
# is minutes, not seconds; the release is ``news:status``, published at the end
# of every poll.
REFRESH_TIMEOUT_SEC = 240.0
SYMBOL_ROUTE = "/symbol"
SEEN_KEY = "news_seen_ts"          # app_settings; read by the rail badge later
DEFAULT_WINDOW_H = 6               # config/news.toml [trending] window_h

# The feed has published NOTHING: the ONE shared line (pages/copy.py), which the
# Desk strip and the Symbol band show too. It is worded apart from the no-match
# line below, because a cold collector and a filter that excludes everything
# must never read the same.
WAITING = _copy.WAITING_NEWS
NO_MATCH = "Nothing matches those filters. Clear one to see more."
# The feed is up and published an empty list: the ONE line for that, drawn by
# this page and by the public copy (pages/news_live.py) alike.
EMPTY_FEED = "The feed is up but carries no items right now."

# ── row styling (fixed Tailwind classes; no inline style) ───────────────────
# One line per item: nothing wraps; the headline gives way with an ellipsis.
# overflow-hidden: on a phone the fixed-width cells would otherwise push the
# headline to 0px and the row off the side of the card.
_ROW = ("w-full items-center gap-2 flex-nowrap overflow-hidden py-1.5 "
        "border-b border-[#213152]/60")
# w-28 holds a dated stamp ("Sep 25 10:43 AM") on one line.
_WHEN = f"text-xs tabular-nums whitespace-nowrap {_t.MUTED} w-28 shrink-0"
_CHIP = "text-[11px] font-semibold px-1.5 py-0.5"
_TICKER_LINK = f"{_CHIP} {_t.BADGE_ACCENT} no-underline hover:underline shrink-0"
_TICKER_CHIP = f"{_CHIP} {_t.BADGE_ACCENT} cursor-pointer hover:underline shrink-0"
# Source badges are the first thing to go below ``sm``: the headline matters more.
_SOURCE = (f"text-[10.5px] px-1.5 py-0.5 {_t.BADGE_MUTED} whitespace-nowrap shrink-0 "
           "hidden sm:inline-flex")
_MORE_TICKERS = f"{_CHIP} {_t.BADGE_MUTED} whitespace-nowrap shrink-0 cursor-default"
_HEADLINE = f"text-sm {_t.LABEL} no-underline hover:underline truncate min-w-0 flex-1"
# The impact pill: a fixed-width slot, so a row with no band keeps the columns.
_PILL_SLOT = "w-5 shrink-0"
_PILL = ("w-5 shrink-0 text-center text-[10.5px] font-bold rounded-[4px] "
         "py-0.5 cursor-default")
_TREND_ON = f"{_CHIP} {_t.BADGE_ACCENT} cursor-pointer"
_TREND_OFF = f"{_CHIP} {_t.BADGE_MUTED} cursor-pointer hover:underline"

# ── the right column: SEC panel (top) and calendar (bottom) ─────────────────
# Each panel is half the viewport tall at lg and scrolls on its own; below lg
# the three regions stack in DOM order at natural height.
_GRID = "w-full grid grid-cols-1 lg:grid-cols-5 gap-3 items-start"
_LEFT = "lg:col-span-3 min-w-0 w-full gap-3"
_RIGHT = "lg:col-span-2 min-w-0 w-full flex flex-col gap-3"
_PANEL = f"{_t.CARD} w-full min-w-0 gap-2 lg:h-[calc(50vh-5rem)] overflow-y-auto flex-nowrap"
# The panel's own background (the CARD token's ``bg-``), so rows scrolling
# under the sticky column header do not show through it.
_PANEL_BG = next((c for c in _t.CARD.split() if c.startswith("bg-")), "bg-[#101a30]")
_SEC_HEAD = (f"w-full items-center gap-2 flex-nowrap {_t.EYEBROW} "
             f"sticky top-0 z-10 {_PANEL_BG} py-1")
_SEC_SYM = "w-14 shrink-0"
_SEC_CELL = "min-w-0 flex-1 items-center gap-1.5 flex-nowrap overflow-hidden"
_SEC_TITLE = f"text-sm {_t.LABEL} no-underline hover:underline truncate min-w-0"
_SEC_DETAIL = f"text-xs {_t.MUTED} truncate min-w-0 max-w-[45%] shrink-0"
_SEC_DOT = f"text-xs {_t.MUTED} shrink-0"
_CAL_GROUP = "w-full gap-1.5"
_CAL_TILES = "w-full grid grid-cols-1 sm:grid-cols-2 gap-2"
_CAL_TILE = "gap-0.5 min-w-0 rounded-[8px] border border-[#213152] bg-white/[0.02] px-2.5 py-2"
_CAL_TITLE = f"text-sm font-semibold {_t.LABEL} truncate w-full"
_CAL_WHEN = f"text-xs tabular-nums {_t.MUTED}"
_CAL_LINE = f"text-xs {_t.MUTED}"
_CAL_IND = f"text-xs {_t.LABEL} tabular-nums"
_CAL_NOTE = f"text-xs {_t.TXT_WARN}"

SEC_TITLE = "SEC / EDGAR"
CAL_TITLE = "Calendar"
SEC_PAGE_SIZE = 40
# The SEC and calendar views have published NOTHING (never "nothing to report").
SEC_WAITING = "No filings yet — the SEC feed hasn't published this session."
SEC_EMPTY = "No SEC filings or insider buys right now."
CAL_WAITING = "No calendar yet — it hasn't been published this session."
# The impact filter's options; "all" is no filter.
BAND_OPTIONS = {"all": "All", "high": "High", "med": "High + Med"}


def _norm(text) -> str:
    return " ".join(text.split()).casefold() if isinstance(text, str) else ""


# A remainder after the headline shorter than this is a publisher tag, not a
# sentence (Google News: "<headline>  <publisher>").
_ECHO_TAIL_CHARS = 40
_TAIL_PUNCT = " -–—|:·•,"


def _teaser_echoes(teaser, row) -> bool:
    """True when ``teaser`` is only the headline again, or the headline plus a
    publisher's name - Google News writes its description that way, which on
    hover reads as the headline printed twice. A teaser that merely OPENS with
    the headline and carries on is a real first line and is kept.

    The headline must end on a word boundary in the teaser - whitespace,
    punctuation or the end - so "Fed" is not echoed by "Federal Reserve holds".
    (The rule the two-line v1 rows used, restored for the hover.)"""
    title, text = _norm(row.get("title")), _norm(teaser)
    if not title or not text.startswith(title):
        return False
    if len(text) > len(title) and text[len(title)].isalnum():
        return False
    tail = text[len(title):].strip(_TAIL_PUNCT)
    if not tail or len(tail) < _ECHO_TAIL_CHARS:
        return True
    names = [row.get("original_source"), row.get("source"),
             *(row.get("sources") if isinstance(row.get("sources"), list) else [])]
    return any(_norm(n) and _norm(n) == tail for n in names)


def teaser_text(row) -> str:
    """The row's teaser, or ``""`` when there is none or it only echoes the
    headline (see ``_teaser_echoes``)."""
    if not isinstance(row, dict):
        return ""
    teaser = row.get("teaser").strip() if isinstance(row.get("teaser"), str) else ""
    return "" if not teaser or _teaser_echoes(teaser, row) else teaser


def hover_text(row) -> str:
    """What a headline shows on hover: its teaser, else a filing's summary."""
    if not isinstance(row, dict):
        return ""
    return teaser_text(row) or nv.detail_line(row)


def trending_window_h() -> float:
    """``[trending] window_h`` from config/news.toml, or the default when that
    is not a positive, finite number (a bool is not a number here)."""
    from shared import news_config as nc
    try:
        v = (nc.load().get("trending") or {}).get("window_h")
    except (AttributeError, TypeError):
        return DEFAULT_WINDOW_H
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return DEFAULT_WINDOW_H
    if not math.isfinite(v) or v <= 0:
        return DEFAULT_WINDOW_H
    return v


def status_text(n_all, n_match, n_shown) -> str:
    """Counts only — the time lives in the header stamp."""
    if not n_all:
        return ""
    if n_match == n_all:
        head = f"{n_all} item{'' if n_all == 1 else 's'}"
    else:
        head = f"{n_match} of {n_all} items match"
    return head if n_shown >= n_match else f"{head} · showing {n_shown}"


def _symbol_base(linked):
    """The dossier route this origin serves, or ``None`` (tickers then filter)."""
    if not linked:
        return None
    import shell as _shell
    # None means this origin serves no dossier: the private path would 404
    # here, so the tickers become filter chips rather than dead links.
    return _shell.route_for(SYMBOL_ROUTE)


def _ticker(t, href_base, on_ticker):
    """One ticker: a dossier link when ``href_base``, else a filter chip."""
    from nicegui import ui

    from pages.ui_guard import guard
    if href_base is not None:
        return ui.link(t, f"{href_base}?symbol={quote(t, safe='')}").classes(_TICKER_LINK)
    chip = ui.label(t).classes(_TICKER_CHIP)
    chip.on("click", guard(lambda _e=None, t=t: on_ticker(t)))
    return chip


def _pill(band, reasons):
    """The impact pill (``H`` / ``M`` / ``L``, reasons on hover), or an empty
    slot of the same width for an item with no band - never a guessed "low"."""
    from nicegui import ui
    letter = nv.BAND_LETTER.get(band) if isinstance(band, str) else None
    if not letter:
        return ui.element("span").classes(_PILL_SLOT)
    pill = ui.label(letter).classes(f"{_PILL} {nv.BAND_CLASSES[band]}")
    words = nv.reason_text(reasons)
    if words:
        with pill:
            ui.tooltip(words)
    return pill


def _headline(row, classes, *, href=nv.safe_href, hover=hover_text):
    """The headline as a new-tab link (only what ``href`` passes), else plain
    text; ``hover(row)`` on hover. Every string is escaped."""
    from nicegui import ui
    title = row.get("title") or "(untitled)"
    url = href(row.get("url"))
    el = (ui.link(title, url, new_tab=True) if url else ui.label(title)).classes(classes)
    extra = hover(row)
    if extra:
        with el:
            with ui.tooltip().classes("max-w-[420px]"):
                ui.label(extra)
    return el


def draw_rows(container, rows, *, linked, on_ticker):
    """Clear ``container`` and draw ONE LINE per row: the time
    (``row["when"]``, Central), the impact pill, a chip per ticker, the headline
    (truncated with an ellipsis) and a badge per source.

    A ticker links to its Symbol dossier when ``linked``; otherwise it is a chip
    that calls ``on_ticker(TICKER)`` - the public screen, which has no dossier,
    filters by it instead."""
    from nicegui import ui

    href_base = _symbol_base(linked)
    container.clear()
    with container:
        for r in rows or []:
            with ui.row().classes(_ROW):
                ui.label(r.get("when") or "").classes(_WHEN)
                _pill(r.get("band"), r.get("reasons") or [])
                tickers = r.get("tickers") or []
                for t in tickers[:MAX_ROW_TICKERS]:
                    _ticker(t, href_base, on_ticker)
                rest = tickers[MAX_ROW_TICKERS:]
                if rest:
                    with ui.label(f"+{len(rest)}").classes(_MORE_TICKERS):
                        ui.tooltip(", ".join(rest))
                _headline(r, _HEADLINE)
                for s in r.get("sources") or []:
                    ui.label(s).classes(_SOURCE)


def draw_sec_rows(container, rows, *, linked, on_ticker):
    """Clear ``container`` and draw the SEC panel: a column header, then one
    line per ``news_view.sec_rows`` row - Date/Time · Symbol · Headline/Details,
    the impact pill leading the third cell and ``details`` muted after a dot.

    A filing's title links only to an ``https`` sec.gov address
    (``news_view.sec_href``); its hover carries the teaser alone, never the
    detail line already printed inline.

    Where this origin serves no Symbol dossier (``linked`` False, or the route
    is not published here) the symbol is a filter chip - and ``on_ticker``
    filters the HEADLINE list only: the SEC panel itself is not filtered by it."""
    from nicegui import ui

    href_base = _symbol_base(linked)
    container.clear()
    with container:
        with ui.row().classes(_SEC_HEAD):
            ui.label("Date/Time").classes("w-28 shrink-0")
            ui.label("Symbol").classes(_SEC_SYM)
            ui.label("Headline/Details").classes("min-w-0 flex-1")
        for r in rows or []:
            with ui.row().classes(_ROW):
                ui.label(r.get("when") or "").classes(_WHEN)
                with ui.element("div").classes(_SEC_SYM):
                    if r.get("symbol"):
                        _ticker(r["symbol"], href_base, on_ticker)
                with ui.row().classes(_SEC_CELL):
                    _pill(r.get("band"), r.get("reasons") or [])
                    _headline(r, _SEC_TITLE, href=nv.sec_href, hover=teaser_text)
                    if r.get("details"):
                        ui.label("·").classes(_SEC_DOT)
                        ui.label(r["details"]).classes(_SEC_DETAIL)


def _tile_when(tile):
    when = tile.get("when") or ""
    if tile.get("indicators") is not None and when and when != nv.NO_NEXT:
        return f"Next {when}"
    return when


def draw_calendar(container, groups):
    """Clear ``container`` and draw ``news_view.calendar_groups``: per group its
    header, a muted note when its sources failed, then its tiles - or the
    group's own plain sentence when it has none."""
    from nicegui import ui

    container.clear()
    with container:
        for g in groups or []:
            with ui.column().classes(_CAL_GROUP):
                ui.label(g.get("title") or "").classes(_t.EYEBROW)
                if g.get("note"):
                    ui.label(g["note"]).classes(_CAL_NOTE)
                tiles = g.get("tiles") or []
                if not tiles:
                    ui.label(g.get("empty") or "").classes(_CAL_LINE)
                    continue
                with ui.element("div").classes(_CAL_TILES):
                    for tile in tiles:
                        with ui.column().classes(_CAL_TILE):
                            ui.label(tile.get("title") or "").classes(_CAL_TITLE)
                            when = _tile_when(tile)
                            if when:
                                ui.label(when).classes(_CAL_WHEN)
                            for line in tile.get("lines") or []:
                                ui.label(line).classes(_CAL_LINE)
                            for ind in tile.get("indicators") or []:
                                if ind.get("label"):
                                    ui.label(ind["label"]).classes(_CAL_LINE)
                                ui.label(f"Actual {ind.get('actual') or nv.DASH}"
                                         f" · Prior {ind.get('prior') or nv.DASH}") \
                                    .classes(_CAL_IND)
                                if ind.get("state") == "awaiting":
                                    ui.label("Awaiting the release").classes(_CAL_LINE)


def render(public=False):
    """Build the Market News page (the private app)."""
    if public:
        from pages import news_live
        return news_live.render()
    import app_settings
    import bus_client
    import shell as _shell
    from nicegui import run, ui

    from pages import ui_kit as kit
    from pages.ui_guard import guard, guard_async
    from pages.view_watch import watch_view
    from shared import news_config as nc

    _may_enqueue = _shell.may_enqueue()
    linked = _shell.can_navigate(SYMBOL_ROUTE)
    state = {"payload": None, "rows": [], "sources": [], "symbol": "",
             "watchlist_only": False, "min_band": None, "shown": PAGE_SIZE,
             "sec_payload": None, "cal_payload": None, "sec_shown": SEC_PAGE_SIZE}
    try:
        watchlist = set(nc.ticker_set())
    except Exception:  # noqa: BLE001 - a broken config must not blank the page
        watchlist = set()

    refresh_btn = None
    with kit.page():
        head = kit.header("Market News", view=nv.VIEW)
        if _may_enqueue:
            @guard
            def _refresh():
                # Both, not either: the button is not drawn where this process
                # may not enqueue, AND the handler declines (the total proof
                # tests/test_live_commands.py asks of a published module).
                if not _may_enqueue:
                    return
                bus_client.request("news", {"type": "news_refresh"})
                kit.set_busy(refresh_btn, timeout=REFRESH_TIMEOUT_SEC)
                kit.toast("info", "Checking every feed and the calendar now — "
                                  "new items appear here as the check "
                                  "finishes, usually within a few minutes.")
            with head.actions:
                refresh_btn = kit.button(
                    "Refresh", kind="secondary", icon="refresh", on_click=_refresh,
                    tooltip="Poll every news feed and re-check the calendar now "
                            "instead of waiting for the next scheduled check.")
        with ui.element("div").classes(_GRID):
            # LEFT: the headline list. The control bar and Trending filter it
            # alone, so they sit inside this column.
            with ui.column().classes(_LEFT):
                with kit.control_bar():
                    src_sel = kit.select_field("Sources", [], value=[], multiple=True,
                                               width="w-72").props("use-chips clearable")
                    # No placeholder: in the boxed field it renders as bright as a
                    # value, so "NVDA" there read as a filter already in force.
                    # Debounced: each keystroke would otherwise rebuild up to 60 rows.
                    sym_in = kit.text_field("Ticker", width="w-[110px]") \
                        .props("debounce=300")
                    band_sel = kit.select_field("Impact", BAND_OPTIONS, value="all",
                                                width="w-32")
                    with kit.field("Watchlist"):
                        wl = ui.switch("Watchlist only")
                with ui.row().classes("w-full items-center gap-2 flex-wrap") as trend_box:
                    pass
                status = kit.status_line("")
                region = kit.region("Loading the news…")
                more = kit.button("Show more", kind="secondary", icon="expand_more")
                more.set_visibility(False)
            # RIGHT: the SEC panel on top, the calendar below; each scrolls.
            with ui.column().classes(_RIGHT):
                with ui.column().classes(_PANEL):
                    kit.section_title(SEC_TITLE)
                    sec_region = kit.region("Loading filings…")
                    sec_more = kit.button("Show more", kind="secondary",
                                          icon="expand_more")
                    sec_more.set_visibility(False)
                with ui.column().classes(_PANEL):
                    kit.section_title(CAL_TITLE)
                    cal_region = kit.region("Loading the calendar…")

    def _filtered():
        return nv.filter_rows(state["rows"], sources=state["sources"] or None,
                              symbol=state["symbol"] or None,
                              watchlist=watchlist if state["watchlist_only"] else None,
                              min_band=state["min_band"])

    @guard
    def _pick_ticker(t):
        # A second click on the chip already in force clears it.
        sym_in.value = "" if state["symbol"] == t else t

    def _paint_trending():
        trend_box.clear()
        payload = state["payload"]
        if payload is None:
            return
        pairs = nv.trending(payload, now=_dt.datetime.now(_dt.timezone.utc),
                            window_h=trending_window_h())[:12]
        if not pairs:
            return
        with trend_box:
            ui.label("Trending").classes(_t.EYEBROW)
            for t, n in pairs:
                cls = _TREND_ON if state["symbol"] == t else _TREND_OFF
                chip = ui.label(f"{t} {n}").classes(cls)
                chip.on("click", guard(lambda _e=None, t=t: _pick_ticker(t)))

    def _paint():
        region.busy.hide()
        if state["payload"] is None:
            draw_rows(region.content, [], linked=linked, on_ticker=_pick_ticker)
            with region.content:
                kit.empty(WAITING)
            status.text = ""
            more.set_visibility(False)
            trend_box.clear()
            return
        matched = _filtered()
        shown = matched[:state["shown"]]
        draw_rows(region.content, shown, linked=linked, on_ticker=_pick_ticker)
        if not matched:
            with region.content:
                kit.empty(NO_MATCH if state["rows"] else EMPTY_FEED)
        status.text = status_text(len(state["rows"]), len(matched), len(shown))
        more.set_visibility(len(matched) > state["shown"])
        _paint_trending()

    def _take(payload):
        state["payload"] = payload if isinstance(payload, dict) else None
        state["rows"] = nv.rows(state["payload"],
                                now=_dt.datetime.now(_dt.timezone.utc))
        opts = nv.sources_present(state["rows"])
        if list(src_sel.options) != opts:
            src_sel.options = opts
            # A source that stopped appearing would leave a filter with no visible
            # cause; drop it from the selection.
            kept = [s for s in (src_sel.value or []) if s in opts]
            if kept != list(src_sel.value or []):
                src_sel.value = kept
                state["sources"] = kept
            src_sel.update()
        _paint()
        if state["payload"] is not None:
            # Written on EVERY publish, visible tab or not; the rail badge that
            # will read it may want "seen while visible" instead.
            app_settings.set(SEEN_KEY, _dt.datetime.now(_dt.timezone.utc).isoformat())

    @guard
    def _on_sources(e):
        state["sources"] = list(e.value or [])
        state["shown"] = PAGE_SIZE
        _paint()

    @guard
    def _on_symbol(e):
        state["symbol"] = (e.value or "").strip().upper()
        state["shown"] = PAGE_SIZE
        _paint()

    @guard
    def _on_watchlist(e):
        state["watchlist_only"] = bool(e.value)
        state["shown"] = PAGE_SIZE
        _paint()

    @guard
    def _on_band(e):
        v = e.value if e.value in BAND_OPTIONS else "all"
        state["min_band"] = None if v == "all" else v
        state["shown"] = PAGE_SIZE
        _paint()

    @guard
    def _more():
        state["shown"] += PAGE_SIZE
        _paint()

    def _paint_sec():
        sec_region.busy.hide()
        payload = state["sec_payload"]
        if payload is None:
            draw_sec_rows(sec_region.content, [], linked=linked, on_ticker=_pick_ticker)
            with sec_region.content:
                kit.empty(SEC_WAITING)
            sec_more.set_visibility(False)
            return
        srows = nv.sec_rows(payload, now=_dt.datetime.now(_dt.timezone.utc))
        draw_sec_rows(sec_region.content, srows[:state["sec_shown"]],
                      linked=linked, on_ticker=_pick_ticker)
        if not srows:
            with sec_region.content:
                kit.empty(SEC_EMPTY)
        sec_more.set_visibility(len(srows) > state["sec_shown"])

    def _take_sec(payload):
        state["sec_payload"] = payload if isinstance(payload, dict) else None
        _paint_sec()

    def _paint_cal():
        cal_region.busy.hide()
        payload = state["cal_payload"]
        if payload is None:
            cal_region.content.clear()
            with cal_region.content:
                kit.empty(CAL_WAITING)
            return
        draw_calendar(cal_region.content,
                      nv.calendar_groups(payload, now=_dt.datetime.now(_dt.timezone.utc)))

    def _take_cal(payload):
        state["cal_payload"] = payload if isinstance(payload, dict) else None
        _paint_cal()

    @guard
    def _repaint_held():
        # The slow clock: redraw what is already held (stamps, filings and
        # calendar dates age while a tab stays open). No bus read here, and no
        # "seen" stamp - nothing new has arrived.
        if state["payload"] is not None:
            state["rows"] = nv.rows(state["payload"],
                                    now=_dt.datetime.now(_dt.timezone.utc))
        _paint()
        _paint_sec()
        _paint_cal()

    @guard
    def _sec_more():
        state["sec_shown"] += SEC_PAGE_SIZE
        _paint_sec()

    src_sel.on_value_change(_on_sources)
    sym_in.on_value_change(_on_symbol)
    wl.on_value_change(_on_watchlist)
    band_sel.on_value_change(_on_band)
    more.on_click(_more)
    sec_more.on_click(_sec_more)

    @guard_async
    async def _reread():
        # The bus read is blocking; keep it off the event loop (house pattern).
        _take(await run.io_bound(bus_client.read, nv.VIEW))

    payload = bus_client.read(nv.VIEW)
    if payload is not None:
        _take(payload)
    else:
        _paint()
    watch_view(nv.VIEW, _reread)

    @guard_async
    async def _reread_sec():
        _take_sec(await run.io_bound(bus_client.read, nv.VIEW_SEC))

    @guard_async
    async def _reread_cal():
        _take_cal(await run.io_bound(bus_client.read, nv.VIEW_CAL))

    _take_sec(bus_client.read(nv.VIEW_SEC))
    _take_cal(bus_client.read(nv.VIEW_CAL))
    watch_view(nv.VIEW_SEC, _reread_sec)
    watch_view(nv.VIEW_CAL, _reread_cal)
    ui.timer(REPAINT_SEC, _repaint_held, immediate=False)
    if refresh_btn is not None:
        # Every poll ends with a status publish - even one that found nothing
        # new, which leaves the feed view (skip_unchanged) untouched.
        watch_view(nv.VIEW_STATUS, guard(lambda: kit.set_busy(refresh_btn, False)))
