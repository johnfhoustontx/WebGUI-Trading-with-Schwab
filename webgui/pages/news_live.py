"""The PUBLIC Market News screen (``live.neuralstrike.co/news``).

The private /news page less every owner control: no Refresh, no watchlist
switch, no Symbol-dossier links, and no "seen" stamp written to the owner's
settings store. Reached through ``news.render(public=True)``, which hands off
here before the private page builds anything. It is the one Tools screen that
WRITES NOTHING - there is no command site in this module, so it has no enqueue
gate to hold.

Same page as the private one: built by ``news.build_board`` - the headline list
and its filters on the left; the next-on-the-calendar hero, the SEC filings card
and the economic-calendar agenda on the right.

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

Every region is drawn by the private page's own ``news.build_board`` (and so by
its painters ``draw_rows`` / ``draw_sec_rows`` / ``draw_next`` /
``draw_calendar``), with ``linked=False`` - so the two origins cannot drift. It
reads no view itself: this module does every read and hands the payloads over.
The filters are drawn too: they only hide rows already on the page. A ticker
chip filters this page (this origin serves no dossier). ``?symbol=`` seeds that filter, through
``shared.symbols.clean_symbol`` like every other ticker a URL can carry;
anything it refuses seeds nothing.

⚠ A title, teaser or detail line is THIRD-PARTY TEXT: it reaches the page only
through ``news.build_board``'s painters (labels and links, which escape; hrefs
through ``news_view.safe_href``). This module calls ``ui.html`` nowhere.
"""
from __future__ import annotations

import bus_client
from nicegui import context, run
from shared.symbols import clean_symbol

from pages import news
from pages import news_view as nv
from pages import ui_kit as kit
from pages.ui_guard import guard_async
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
    with kit.page():
        kit.header(TITLE, view=nv.VIEW_PUBLIC)
        # The private page's own board: the same regions, filters and painters,
        # with no dossier links and no watchlist chip. It reads nothing itself.
        board = news.build_board(linked=False, symbol=seed_symbol(_query_symbol()),
                                 watchlist=None)

    @guard_async
    async def _reread():
        # The bus read is blocking; keep it off the event loop (house pattern).
        board.take(await run.io_bound(bus_client.read, nv.VIEW_PUBLIC))

    @guard_async
    async def _reread_sec():
        board.take_sec(await run.io_bound(bus_client.read, nv.VIEW_SEC_PUBLIC))

    @guard_async
    async def _reread_cal():
        board.take_cal(await run.io_bound(bus_client.read, nv.VIEW_CAL_PUBLIC))

    board.take(bus_client.read(nv.VIEW_PUBLIC))
    board.take_sec(bus_client.read(nv.VIEW_SEC_PUBLIC))
    board.take_cal(bus_client.read(nv.VIEW_CAL_PUBLIC))
    watch_view(nv.VIEW_PUBLIC, _reread)
    watch_view(nv.VIEW_SEC_PUBLIC, _reread_sec)
    watch_view(nv.VIEW_CAL_PUBLIC, _reread_cal)
