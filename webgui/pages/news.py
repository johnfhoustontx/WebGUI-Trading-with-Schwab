"""Market News — the newest headlines, filings and insider buys, tagged by ticker.

Tier-1: nicegui + bus_client + shell + app_settings + ``shared.news_config`` +
``pages.*`` only. Reads ``cache:news:feed`` (``news_view.VIEW``); the ONE write
is Refresh → ``news_refresh`` on cmd:news, built only when
``shell.may_enqueue()`` says this process may. ``render(public=True)`` hands
off to ``news_live`` before anything here builds.

Every fact comes from ``pages/news_view.py`` (pure); this module holds widgets
and wiring. ``draw_rows`` is module-level because the public screen draws the
same rows.

⚠ A title, teaser or detail line is THIRD-PARTY TEXT. It reaches the page only
through ``ui.label`` / ``ui.link``, which escape — never ``ui.html``, and a
source-level test pins that this module calls ``ui.html`` nowhere. A link's
target is a feed's URL too, and ``ui.link`` does NOT escape an href, so only
what ``news_view.safe_href`` passes (http/https with a host) becomes a link;
anything else (a ``javascript:`` URL included) renders the title as plain text.

Built on the page kit (``pages/ui_kit.py``): the header line carries the title,
the Updated stamp and Refresh; the filters sit in a control bar; the status line
carries counts only; one region holds the rows a publish replaces. The stamp is
not ``stale``-aware: the collector's cadence moves with the session (5 min in
regular hours, 60 at the weekend), so an old stamp on a Sunday is not a fault.
"""
from __future__ import annotations

import datetime as _dt
import math
from urllib.parse import quote

from pages import copy as _copy  # the ONE copy (pages/copy.py)
from pages import news_view as nv
from pages.options import theme as _t

PAGE_SIZE = 60
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

# ── row styling (fixed Tailwind classes; no inline style) ───────────────────
_ROW = "w-full gap-1 py-2 border-b border-[#213152]/60"
# w-28 holds a dated stamp ("Sep 25 10:43 AM") on one line; the second line
# indents by that width plus the row's gap-2.
_WHEN = f"text-xs tabular-nums whitespace-nowrap {_t.MUTED} w-28 shrink-0"
_CHIP = "text-[11px] font-semibold px-1.5 py-0.5"
_TICKER_LINK = f"{_CHIP} {_t.BADGE_ACCENT} no-underline hover:underline"
_TICKER_CHIP = f"{_CHIP} {_t.BADGE_ACCENT} cursor-pointer hover:underline"
_SOURCE = f"text-[10.5px] px-1.5 py-0.5 {_t.BADGE_MUTED}"
_HEADLINE = f"text-sm {_t.LABEL} no-underline hover:underline min-w-0"
_SECOND = f"text-xs {_t.MUTED} pl-[120px] line-clamp-2"
_TREND_ON = f"{_CHIP} {_t.BADGE_ACCENT} cursor-pointer"
_TREND_OFF = f"{_CHIP} {_t.BADGE_MUTED} cursor-pointer hover:underline"


# A remainder after the headline shorter than this is a publisher tag, not a
# sentence (Google News: "<headline>  <publisher>").
_ECHO_TAIL_CHARS = 40
_TAIL_PUNCT = " -–—|:·•,"


def _norm(text) -> str:
    return " ".join(text.split()).casefold() if isinstance(text, str) else ""


def _echoes_title(teaser, row) -> bool:
    """True when ``teaser`` is only the headline again, or the headline plus a
    publisher's name — Google News writes its description that way, which on
    screen reads as the headline printed twice. A teaser that merely OPENS with
    the headline and carries on is a real first line and is kept."""
    title, text = _norm(row.get("title")), _norm(teaser)
    if not title or not text.startswith(title):
        return False
    tail = text[len(title):].strip(_TAIL_PUNCT)
    if not tail or len(tail) < _ECHO_TAIL_CHARS:
        return True
    names = [row.get("original_source"), row.get("source"),
             *(row.get("sources") if isinstance(row.get("sources"), list) else [])]
    return any(_norm(n) and _norm(n) == tail for n in names)


def second_line(row) -> str:
    """The muted line under a headline: its teaser, else a filing's summary.

    A teaser that only echoes the headline (plus, at most, its publisher) is
    dropped; see ``_echoes_title``."""
    if not isinstance(row, dict):
        return ""
    teaser = (row.get("teaser") or "").strip() if isinstance(row.get("teaser"), str) else ""
    if teaser and _echoes_title(teaser, row):
        teaser = ""
    return teaser or nv.detail_line(row)


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


def draw_rows(container, rows, *, linked, on_ticker):
    """Clear ``container`` and draw one entry per row.

    Each entry: the time (``row["when"]``), a chip per ticker, the headline as a
    link opening in a new tab, a small badge per source, and a muted second line
    (the teaser, or a filing's summary). A ticker links to its Symbol dossier
    when ``linked``; otherwise it is a chip that calls ``on_ticker(TICKER)`` —
    the public screen, which has no dossier, filters by it instead."""
    from nicegui import ui

    from pages.ui_guard import guard

    href_base = None
    if linked:
        import shell as _shell
        # None means this origin serves no dossier: the private path would 404
        # here, so the tickers become filter chips rather than dead links.
        href_base = _shell.route_for(SYMBOL_ROUTE)
        linked = href_base is not None
    container.clear()
    with container:
        for r in rows or []:
            with ui.column().classes(_ROW):
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    ui.label(r.get("when") or "").classes(_WHEN)
                    for t in r.get("tickers") or []:
                        if linked:
                            ui.link(t, f"{href_base}?symbol={quote(t, safe='')}") \
                                .classes(_TICKER_LINK)
                        else:
                            chip = ui.label(t).classes(_TICKER_CHIP)
                            chip.on("click", guard(lambda _e=None, t=t: on_ticker(t)))
                    title = r.get("title") or "(untitled)"
                    url = nv.safe_href(r.get("url"))
                    if url:
                        ui.link(title, url, new_tab=True).classes(_HEADLINE)
                    else:
                        ui.label(title).classes(_HEADLINE)
                    for s in r.get("sources") or []:
                        ui.label(s).classes(_SOURCE)
                extra = second_line(r)
                if extra:
                    ui.label(extra).classes(_SECOND)


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
             "watchlist_only": False, "shown": PAGE_SIZE}
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
                kit.toast("info", "Checking every feed now — new items appear "
                                  "here as the check finishes, usually within "
                                  "a few minutes.")
            with head.actions:
                refresh_btn = kit.button(
                    "Refresh", kind="secondary", icon="refresh", on_click=_refresh,
                    tooltip="Poll every news feed now instead of waiting for "
                            "the next scheduled check.")
        with kit.control_bar():
            src_sel = kit.select_field("Sources", [], value=[], multiple=True,
                                       width="w-80").props("use-chips clearable")
            # No placeholder: in the boxed field it renders as bright as a value,
            # so "NVDA" there read as a filter already in force.
            # Debounced: each keystroke would otherwise rebuild up to 60 rows.
            sym_in = kit.text_field("Ticker", width="w-[110px]").props("debounce=300")
            with kit.field("Watchlist"):
                wl = ui.switch("Watchlist only")
        with ui.row().classes("w-full items-center gap-2 flex-wrap") as trend_box:
            pass
        status = kit.status_line("")
        region = kit.region("Loading the news…")
        more = kit.button("Show more", kind="secondary", icon="expand_more")
        more.set_visibility(False)

    def _filtered():
        return nv.filter_rows(state["rows"], sources=state["sources"] or None,
                              symbol=state["symbol"] or None,
                              watchlist=watchlist if state["watchlist_only"] else None)

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
                kit.empty(NO_MATCH if state["rows"] else
                          "The feed is up but carries no items right now.")
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
    def _more():
        state["shown"] += PAGE_SIZE
        _paint()

    src_sel.on_value_change(_on_sources)
    sym_in.on_value_change(_on_symbol)
    wl.on_value_change(_on_watchlist)
    more.on_click(_more)

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
    if refresh_btn is not None:
        # Every poll ends with a status publish - even one that found nothing
        # new, which leaves the feed view (skip_unchanged) untouched.
        watch_view(nv.VIEW_STATUS, guard(lambda: kit.set_busy(refresh_btn, False)))
