"""The PUBLIC Market News screen (``live.neuralstrike.co/news``).

The private /news page less every owner control: no Refresh, no watchlist
switch, no Symbol-dossier links, and no "seen" stamp written to the owner's
settings store. Reached through ``news.render(public=True)``, which hands off
here before the private page builds anything. It is the one Tools screen that
WRITES NOTHING - there is no command site in this module, so it has no enqueue
gate to hold.

Same layout as the private page: the headline list on the left, the SEC /
EDGAR panel top right, the calendar tiles bottom right.

⚠ IT READS THREE VIEWS AND NO OTHER - ``nv.VIEW_PUBLIC`` (headlines),
``nv.VIEW_SEC_PUBLIC`` (the SEC panel) and ``nv.VIEW_CAL_PUBLIC`` (the
calendar) - and that is the whole of what keeps private items off this origin.
The service writes each public key with only what may be published (the items
of feeds flagged ``public`` in config/news.toml; the calendar less the owner's
extra dividend symbols), while the private keys (the unsuffixed feed, SEC and calendar views, plus
the calendar's status view, which carries error text) hold everything. The public process's Redis user reads ``~cache:*``, which covers
every one of those keys - a wildcard grant cannot exclude one key - so the ACL
does not stand behind this rule; the code does. ``tests/test_news_live.py``
pins it at the source (every bus read names a public view; no private view is
ever spelled here) and by rendering the page against a bus that serves a
different item under each key.

Rows, filings and tiles are drawn by ``news.draw_rows`` / ``draw_sec_rows`` /
``draw_calendar`` - the private page's own painters, with ``linked=False`` -
so the two origins cannot drift. The impact filter is drawn too: it only hides
rows already on the page. A ticker chip filters this
page (this origin serves no dossier). ``?symbol=`` seeds that filter, through
``shared.symbols.clean_symbol`` like every other ticker a URL can carry;
anything it refuses seeds nothing.

⚠ A title, teaser or detail line is THIRD-PARTY TEXT: it reaches the page only
through ``news.draw_rows`` (labels and links, which escape; hrefs through
``news_view.safe_href``). This module calls ``ui.html`` nowhere.
"""
from __future__ import annotations

import datetime as _dt

import bus_client
from nicegui import context, run, ui
from shared.symbols import clean_symbol

from pages import news
from pages import news_view as nv
from pages import ui_kit as kit
from pages.options import theme as _t
from pages.ui_guard import guard, guard_async
from pages.view_watch import watch_view

TITLE = "Market News"
# The private page's own empty-feed line, never a copy of it.
EMPTY_FEED = news.EMPTY_FEED


def seed_symbol(raw) -> str:
    """The ticker a ``?symbol=`` value may seed the filter with, or ``""``.

    Only a string the shared allow-list accepts; anything else - a refused
    string, a list, a number - seeds nothing rather than a filter that matches
    nothing."""
    if not isinstance(raw, str):
        return ""
    return clean_symbol(raw) or ""


def _query_symbol():
    """The raw ``?symbol=`` value of this page's request, or ``None``."""
    try:
        return context.client.request.query_params.get("symbol")
    except Exception:  # noqa: BLE001 - no request (a test, a capture) = no seed
        return None


def render():
    """Build the public Market News page."""
    state = {"payload": None, "rows": [], "sources": [],
             "symbol": seed_symbol(_query_symbol()), "min_band": None,
             "shown": news.PAGE_SIZE, "sec_payload": None, "cal_payload": None,
             "sec_shown": news.SEC_PAGE_SIZE}

    with kit.page():
        kit.header(TITLE, view=nv.VIEW_PUBLIC)
        with ui.element("div").classes(news._GRID):
            # LEFT: the headline list, its control bar and Trending filter.
            with ui.column().classes(news._LEFT):
                with kit.control_bar():
                    src_sel = kit.select_field("Sources", [], value=[], multiple=True,
                                               width="w-72").props("use-chips clearable")
                    sym_in = kit.text_field("Ticker", value=state["symbol"],
                                            width="w-[110px]")
                    band_sel = kit.select_field("Impact", news.BAND_OPTIONS,
                                                value="all", width="w-32")
                with ui.row().classes("w-full items-center gap-2 flex-wrap") as trend_box:
                    pass
                status = kit.status_line("")
                with ui.column().classes(news._LIST):
                    region = kit.region("Loading the news…")
                    more = kit.button("Show more", kind="secondary", icon="expand_more")
                    more.set_visibility(False)
            # RIGHT: the SEC panel on top, the calendar below.
            with ui.column().classes(news._RIGHT):
                with ui.column().classes(news._PANEL):
                    kit.section_title(news.SEC_TITLE)
                    sec_region = kit.region("Loading filings…")
                    sec_more = kit.button("Show more", kind="secondary",
                                          icon="expand_more")
                    sec_more.set_visibility(False)
                with ui.column().classes(news._PANEL):
                    kit.section_title(news.CAL_TITLE)
                    cal_region = kit.region("Loading the calendar…")

    def _filtered():
        return nv.filter_rows(state["rows"], sources=state["sources"] or None,
                              symbol=state["symbol"] or None,
                              min_band=state["min_band"])

    @guard
    def _pick_ticker(t):
        # A second click on the chip already in force clears it. Setting the
        # input fires ``_on_symbol``, which repaints.
        sym_in.value = "" if state["symbol"] == t else t

    def _paint_trending():
        trend_box.clear()
        if state["payload"] is None:
            return
        pairs = nv.trending(state["payload"],
                            now=_dt.datetime.now(_dt.timezone.utc),
                            window_h=news.trending_window_h())[:12]
        if not pairs:
            return
        with trend_box:
            ui.label("Trending").classes(_t.EYEBROW)
            for t, n in pairs:
                cls = news._TREND_ON if state["symbol"] == t else news._TREND_OFF
                chip = ui.label(f"{t} {n}").classes(cls)
                chip.on("click", guard(lambda _e=None, t=t: _pick_ticker(t)))

    def _paint():
        region.busy.hide()
        if state["payload"] is None:
            news.draw_rows(region.content, [], linked=False, on_ticker=_pick_ticker)
            with region.content:
                kit.empty(news.WAITING)
            status.text = ""
            more.set_visibility(False)
            trend_box.clear()
            return
        matched = _filtered()
        shown = matched[:state["shown"]]
        news.draw_rows(region.content, shown, linked=False, on_ticker=_pick_ticker)
        if not matched:
            with region.content:
                kit.empty(news.NO_MATCH if state["rows"] else EMPTY_FEED)
        status.text = news.status_text(len(state["rows"]), len(matched), len(shown))
        more.set_visibility(len(matched) > state["shown"])
        _paint_trending()

    def _take(payload):
        state["payload"] = payload if isinstance(payload, dict) else None
        state["rows"] = nv.rows(state["payload"],
                                now=_dt.datetime.now(_dt.timezone.utc))
        opts = nv.sources_present(state["rows"])
        if list(src_sel.options) != opts:
            src_sel.options = opts
            kept = [s for s in (src_sel.value or []) if s in opts]
            if kept != list(src_sel.value or []):
                src_sel.value = kept
                state["sources"] = kept
            src_sel.update()
        _paint()

    @guard
    def _on_sources(e):
        state["sources"] = list(e.value or [])
        state["shown"] = news.PAGE_SIZE
        _paint()

    @guard
    def _on_symbol(e):
        state["symbol"] = (e.value or "").strip().upper()
        state["shown"] = news.PAGE_SIZE
        _paint()

    @guard
    def _on_band(e):
        v = e.value if e.value in news.BAND_OPTIONS else "all"
        state["min_band"] = None if v == "all" else v
        state["shown"] = news.PAGE_SIZE
        _paint()

    @guard
    def _more():
        state["shown"] += news.PAGE_SIZE
        _paint()

    def _paint_sec():
        sec_region.busy.hide()
        payload = state["sec_payload"]
        if payload is None:
            news.draw_sec_rows(sec_region.content, [], linked=False,
                               on_ticker=_pick_ticker)
            with sec_region.content:
                kit.empty(news.SEC_WAITING)
            sec_more.set_visibility(False)
            return
        srows = nv.sec_rows(payload, now=_dt.datetime.now(_dt.timezone.utc))
        news.draw_sec_rows(sec_region.content, srows[:state["sec_shown"]],
                           linked=False, on_ticker=_pick_ticker)
        if not srows:
            with sec_region.content:
                kit.empty(news.SEC_EMPTY)
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
                kit.empty(news.CAL_WAITING)
            return
        news.draw_calendar(cal_region.content,
                           nv.calendar_groups(payload,
                                              now=_dt.datetime.now(_dt.timezone.utc)))

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
        state["sec_shown"] += news.SEC_PAGE_SIZE
        _paint_sec()

    src_sel.on_value_change(_on_sources)
    sym_in.on_value_change(_on_symbol)
    band_sel.on_value_change(_on_band)
    more.on_click(_more)
    sec_more.on_click(_sec_more)

    @guard_async
    async def _reread():
        # The bus read is blocking; keep it off the event loop (house pattern).
        _take(await run.io_bound(bus_client.read, nv.VIEW_PUBLIC))

    _take(bus_client.read(nv.VIEW_PUBLIC))
    watch_view(nv.VIEW_PUBLIC, _reread)

    @guard_async
    async def _reread_sec():
        _take_sec(await run.io_bound(bus_client.read, nv.VIEW_SEC_PUBLIC))

    @guard_async
    async def _reread_cal():
        _take_cal(await run.io_bound(bus_client.read, nv.VIEW_CAL_PUBLIC))

    _take_sec(bus_client.read(nv.VIEW_SEC_PUBLIC))
    _take_cal(bus_client.read(nv.VIEW_CAL_PUBLIC))
    watch_view(nv.VIEW_SEC_PUBLIC, _reread_sec)
    watch_view(nv.VIEW_CAL_PUBLIC, _reread_cal)
    ui.timer(news.REPAINT_SEC, _repaint_held, immediate=False)
