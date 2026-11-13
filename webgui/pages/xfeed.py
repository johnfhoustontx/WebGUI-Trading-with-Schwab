"""X Feed — the watched X accounts' posts (``/news/x``), a tab of Market News.

Tier-1: nicegui + bus_client + shell + ``pages.*`` only. It reads two views,
``cache:xfeed:latest`` (``xfeed_view.VIEW_LATEST``: the newest rows across every
watched account, with the top tickers) and ``cache:xfeed:accounts``
(``VIEW_ACCOUNTS``: the accounts, per-account post counts, the poll interval and
this page's tunables), each on its own ``watch_view``. It WRITES only through
``cmd:xfeed`` (``add_account``, ``remove_account``, ``set_group``, ``query``),
in ONE place, ``XFeedPage._send``, behind ``shell.may_enqueue()``; where this
process may not enqueue, no control that would send is drawn either.

Commands get no reply on the stream. Each carries a fresh ``request_id``, and
the service answers on ``cache:xfeed:answer:{request_id}`` (``answer_view``);
the page polls that one key every ``answer_poll_s`` until it lands or
``answer_timeout_s`` passes (``TIMEOUT``). Both come from the accounts view's
``page`` block (config/xfeed.toml ``[page]``).

Data flow, as the old page's ``loadFeed`` / ``loadMore``: with no group, no
filter and nothing older loaded, the list IS the latest view and repaints when
its version moves. Anything else is a ``query`` (``PAGE`` rows, as today);
while the list is a query, a new latest re-runs it quietly (merged into what is
loaded when older pages are). "Load older posts" (a button, and a sentinel that
fires as it scrolls near) sends ``query`` with ``before`` / ``before_key`` from
the last post row and appends.

Layout, in the Headlines page's style: two columns at ``lg``, stacking below
it. LEFT: a collapsible filter card (group tabs with counts, search - a
``$TICKER`` searches cashtags - the All / Posts / Reposts / Replies picker, one
chip per account), the MOST MENTIONED tickers and "N posts shown", then one
line per post with day dividers and gap markers (``xfeed_parts``). A click opens
the post under its line. RIGHT: the collapsible accounts panel (add, the
accounts by group, change group, stop watching, where the interval is set).

⚠ Post text, names and URLs are THIRD-PARTY text: see ``xfeed_parts``.
"""
from __future__ import annotations

import re
import time
from uuid import uuid4

from nicegui import run, ui

import bus_client
from pages import ui_kit as kit
from pages import view_watch
from pages import xfeed_parts as xp
from pages import xfeed_view as xv
from pages.options import theme as _t
from pages.ui_guard import guard, guard_async

PAGE = 300                 # rows a query asks for (the old page's PAGE)
TICKER_CHIPS = 8           # chips on the MOST MENTIONED line (as today)
TICK_S = 5.0               # "checked Ns ago" and the NEW highlight's age move on this clock
STATUS_POLL_S = 10.0       # how often the status line re-reads the accounts view's stamp
SYMBOL_ROUTE = "/symbol"
SETTINGS_ROUTE = "/settings"

NO_ACCOUNTS = "No accounts yet — add one in the panel."
NO_MATCH = "No posts match these filters."
COLD = "No X posts yet — the X feed has not published since it started."
NO_ONE = "Not watching anyone yet. Try @Reuters or @zerohedge."
TIMEOUT = "The X feed service did not answer - try again."
LOAD_OLDER = "Load older posts"
SEARCH_HINT = "Search posts, or $TICKER"
NEW_ACCENT = xp.NEW_ACCENT
_DOLLAR = re.compile(r"^\$[A-Za-z]")


def now_ms() -> int:
    """The page's clock in epoch ms - the one place it is read, so a test can hold it."""
    return int(time.time() * 1000)


def monotonic() -> float:
    """The clock an answer's deadline runs on (a test moves it)."""
    return time.monotonic()


# ── styling: fixed Tailwind classes (the filter and panel pieces: xfeed_parts) ──
_GRID = "w-full grid grid-cols-1 lg:grid-cols-3 gap-4 items-start"
_LEFT = "lg:col-span-2 min-w-0 w-full flex flex-col gap-3 flex-nowrap"
_RIGHT = "min-w-0 w-full flex flex-col gap-4 flex-nowrap"
_CARD = f"{_t.CARD} w-full min-w-0 gap-3 flex-nowrap"
_SEARCH = "flex-1 min-w-[200px]"
_SEARCH_PROPS = 'debounce=400 clearable input-class="placeholder:text-[#6d76a0]"'
_COUNT = "ml-auto text-[12px] tabular-nums text-[#6d76a0] whitespace-nowrap"
_STATUS = "text-[12.5px] whitespace-nowrap"
_ADD_ERR = "text-[12px] text-rose-300"
_SETTINGS_LINK = "text-[12px] text-[#7d93ff] no-underline hover:underline"


class XFeedPage:
    """The page: its widgets, its state and its handlers, built once per visit.
    The painters it calls live in ``xfeed_parts``; the decisions in
    ``xfeed_view``."""

    def __init__(self, _may_enqueue, *, symbol_linked):
        self._may_enqueue = _may_enqueue
        self.symbol_linked = symbol_linked
        self.f = {"group": None, "account": "", "type": "", "q": "", "ticker": ""}
        self.latest = None
        self.accounts_view = None
        self.cfg = dict(xp.DEFAULT_PAGE_CFG)
        self.rows, self.tickers = [], []
        self.exhausted, self.older = True, False
        self.gen = 0                  # bumped by every reload; an older load's answer is dropped
        self.feed_rid = None          # the query whose answer the list waits for
        self.loading_more = False
        self.open_key, self.detail = None, None
        self.lines = {}               # key -> (row, its line) of the painted posts
        self.list_card, self.last_painted = None, None
        self.painted_new = set()
        self.acct_sig, self.acct_status = None, {}
        self.collapsed_groups = set()
        self.stale = False
        self.search = self.group_box = self.type_box = self.chip_box = None
        self.more = self.handle_in = None
        self.target = None            # the handle a dialog is about
        self._build()
        self._take_accounts(bus_client.read(xv.VIEW_ACCOUNTS), paint_feed=False)
        self._take_latest(bus_client.read(xv.VIEW_LATEST))
        meta = bus_client.read_meta(xv.VIEW_ACCOUNTS)
        self._set_stale(meta[1] if meta else None)
        view_watch.watch_view(xv.VIEW_LATEST, self._reread_latest)
        view_watch.watch_view(xv.VIEW_ACCOUNTS, self._reread_accounts)
        ui.timer(TICK_S, self._tick, immediate=False)
        ui.timer(STATUS_POLL_S, self._poll_status, immediate=False)

    # ── build ────────────────────────────────────────────────────────────
    def _build(self):
        with kit.page():
            head = kit.header("X Feed", view=xv.VIEW_LATEST)
            with head.actions, ui.row().classes("items-center gap-1.5 flex-nowrap"):
                self.status_dot = ui.element("span").classes(xp.STATUS_DOT_BASE)
                self.status = ui.label("").classes(_STATUS)
            self.timer_host = ui.element("div")
            with ui.element("div").classes(_GRID):
                with ui.column().classes(_LEFT):
                    if self._may_enqueue:
                        self._build_filters()
                    with ui.row().classes("w-full items-center gap-2 flex-wrap sm:flex-nowrap"):
                        self.trend_box = ui.row().classes("items-center gap-2 flex-wrap min-w-0 flex-1")
                        self.count = ui.label("").classes(_COUNT)
                    self.region = kit.region("Loading the X feed…")
                    self.more_row = ui.column().classes("w-full items-center gap-0")
                    with self.more_row:
                        if self._may_enqueue:
                            self.more = kit.button(LOAD_OLDER, kind="secondary", icon="expand_more",
                                                   on_click=self._more)
                            # Fires as the button scrolls within 400px of view, like the
                            # old page's scroll listener.
                            ui.element("q-intersection").props('margin="0px 0px 400px 0px"') \
                                .classes("w-full h-px").on("visibility", self._on_sentinel)
                    self.more_row.set_visibility(False)
                with ui.column().classes(_RIGHT):
                    self._build_accounts()
            self._build_dialogs()

    def _build_filters(self):
        with ui.column().classes(_CARD):
            _l, _n, self.summary, body = xp.collapsible("Filters", note=False)
            with body:
                self.group_box = ui.row().classes(xp.SEG)
                with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                    self.search = ui.input(placeholder=SEARCH_HINT) \
                        .props(f"{kit.FIELD_PROPS} {_SEARCH_PROPS}").classes(_SEARCH)
                    with self.search.add_slot("prepend"):
                        ui.icon("search").classes("text-[18px]")
                    self.type_box = ui.row().classes(xp.SEG)
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    ui.label("ACCOUNTS").classes(f"{xp.CAPS} mr-1")
                    self.chip_box = ui.row().classes("items-center gap-2 flex-wrap")
        self.search.on_value_change(self._on_search)

    def _build_accounts(self):
        with ui.column().classes(_CARD):
            _l, self.acct_note, _x, body = xp.collapsible("Accounts")
            with body:
                if self._may_enqueue:
                    with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                        self.handle_in = ui.input(placeholder="@handle or x.com link") \
                            .props(kit.FIELD_PROPS).classes("flex-1 min-w-[150px]")
                        self.group_in = ui.input(placeholder="Group (optional)", autocomplete=[]) \
                            .props(kit.FIELD_PROPS).classes("w-36")
                        self.add_btn = kit.button("Add", kind="primary", on_click=self._add)
                    self.handle_in._xfeed_add = "handle"
                    self.group_in._xfeed_add = "group"
                    self.handle_in.on("keydown.enter", self._add)
                    self.add_err = ui.label("").classes(_ADD_ERR)
                    self.add_err.set_visibility(False)
                self.acct_list = ui.column().classes("w-full gap-0 flex-nowrap")
                self.settings_link = ui.link("", SETTINGS_ROUTE).classes(_SETTINGS_LINK)

    def _build_dialogs(self):
        if not self._may_enqueue:
            return
        self.remove_dlg = kit.confirm("Stop watching?", confirm_text="Stop watching",
                                      danger=True, on_confirm=self._remove_confirmed)
        self.group_dlg = kit.confirm("New group", confirm_text="Move",
                                     on_confirm=self._new_group_confirmed)
        with self.group_dlg.content:
            self.group_name_in = ui.input(placeholder="Group name") \
                .props(kit.FIELD_PROPS).classes("w-full")

    # ── commands and their answers ───────────────────────────────────────
    def _send(self, type_, args, on_done):
        """Put one command on ``cmd:xfeed`` with a fresh ``request_id`` and watch
        its answer key; ``on_done(answer)`` runs once with the answer, or with
        ``{"ok": False, "error": TIMEOUT, "timed_out": True}``. Returns the id,
        or None where this process may not enqueue."""
        if self._may_enqueue:
            rid = uuid4().hex
            bus_client.request("xfeed", {"type": type_, "args": {**args, "request_id": rid}})
            self._await_answer(rid, on_done)
            return rid
        return None

    def _await_answer(self, rid, on_done):
        started, state = monotonic(), {"done": False}
        timeout = self.cfg["answer_timeout_s"]

        @guard_async
        async def poll():
            if state["done"]:
                return
            payload = await run.io_bound(bus_client.read, xv.answer_view(rid))
            if state["done"]:
                return
            if isinstance(payload, dict) and payload.get("request_id") == rid:
                answer = payload
            elif monotonic() - started > timeout:
                answer = {"request_id": rid, "ok": False, "error": TIMEOUT, "timed_out": True}
            else:
                return
            state["done"] = True
            timer.cancel()
            on_done(answer)

        # On a host no repaint clears: a timer deletes itself with its parent.
        with self.timer_host:
            timer = ui.timer(self.cfg["answer_poll_s"], poll)
        timer._xfeed_answer = True

    @staticmethod
    def _error(answer, what):
        return TIMEOUT if answer.get("timed_out") else f"{what}: {answer.get('error') or 'failed'}"

    # ── the list ─────────────────────────────────────────────────────────
    def _is_live(self):
        return self.f["group"] is None and not xv.is_filtered(self.f) and not self.older

    def _accounts(self):
        got = (self.accounts_view or {}).get("accounts")
        return [a for a in got if isinstance(a, dict)] if isinstance(got, list) else []

    def _counts(self):
        got = (self.accounts_view or {}).get("counts")
        return got if isinstance(got, dict) else {}

    def _set_filter(self, patch):
        new = {**self.f, **patch}
        if new == self.f:
            return
        self.f = new
        self._reload()

    def _reload(self):
        self.gen += 1
        self.older, self.open_key, self.loading_more = False, None, False
        if self.more is not None:
            kit.set_busy(self.more, False)
        self._paint_filters()
        self._load()

    def _load(self, *, merge=False, quiet=False):
        if self._is_live():
            self.feed_rid = None
            self._from_latest()
            return
        quiet = quiet and self.feed_rid is None    # a re-send keeps a waiting reader's spinner
        if not quiet:
            self.region.busy.show("Loading posts…")
        self.feed_rid = self._send("query", xv.query_args(self.f, limit=PAGE),
                                   lambda a: self._took_feed(a, merge=merge, quiet=quiet))

    def _from_latest(self):
        p = self.latest if isinstance(self.latest, dict) else {}
        self.rows = [r for r in p.get("rows") or [] if isinstance(r, dict)]
        self.tickers = [t for t in p.get("tickers") or [] if isinstance(t, dict)]
        self.exhausted = len(xp.post_rows(self.rows)) < PAGE
        self._paint_feed()

    def _took_feed(self, answer, *, merge, quiet):
        if answer.get("request_id") != self.feed_rid:
            return                      # a newer load won
        self.feed_rid = None
        self.region.busy.hide()
        if not answer.get("ok"):
            if not quiet:
                self._paint_message(self._error(answer, "Could not load the feed"))
            return
        rows = [r for r in answer.get("rows") or [] if isinstance(r, dict)]
        if merge:
            self.rows = xp.merge_rows(self.rows, rows)
            self.tickers = xp.top_tickers(self.rows)
        else:
            self.rows = rows
            self.tickers = [t for t in answer.get("tickers") or [] if isinstance(t, dict)]
            self.exhausted = len(xp.post_rows(rows)) < PAGE
        self._paint_feed()

    @guard
    def _more(self, _e=None):
        if self.loading_more or self.exhausted or self.more is None:
            return
        last = next((r for r in reversed(self.rows) if r.get("kind") == "post"), None)
        if last is None:
            return
        self.loading_more = True
        kit.set_busy(self.more, timeout=self.cfg["answer_timeout_s"] + 5)
        gen = self.gen
        self._send("query", xv.query_args(self.f, before_row=last, limit=PAGE),
                   lambda a: self._took_more(a, gen))

    def _took_more(self, answer, gen):
        if gen != self.gen:
            return                      # the filters changed meanwhile
        self.loading_more = False
        kit.set_busy(self.more, False)
        if not answer.get("ok"):
            kit.toast("error", self._error(answer, "Could not load older posts"))
            return
        rows = [r for r in answer.get("rows") or [] if isinstance(r, dict)]
        have = {xp.row_key(r) for r in self.rows}
        fresh = [r for r in rows if xp.row_key(r) not in have]
        self.rows.extend(fresh)
        self.exhausted = len(xp.post_rows(rows)) < PAGE
        self.older = True
        self._paint_feed(append=fresh)

    @guard
    def _on_sentinel(self, e):
        args = e.args[0] if isinstance(e.args, list) and e.args else e.args
        if args is True:
            self._more()

    @guard
    def _on_search(self, e):
        v = e.value.strip() if isinstance(e.value, str) else ""
        if _DOLLAR.match(v):
            self._set_filter({"ticker": v[1:].upper(), "q": ""})
        else:
            self._set_filter({"q": v, "ticker": ""})

    def _set_ticker(self, tag):
        self._set_filter({"ticker": tag, "q": ""})
        if self.search is not None:
            self.search.value = f"${tag}" if tag else ""

    # ── painting ─────────────────────────────────────────────────────────
    def _empty_text(self):
        if self.accounts_view is None:
            return COLD
        if not self._accounts():
            return NO_ACCOUNTS
        if xv.is_filtered(self.f):
            return NO_MATCH
        if self._is_live() and self.latest is None:
            return COLD
        return xp.watching(len(self._accounts()))

    def _paint_message(self, text):
        self.region.content.clear()
        self.list_card, self.lines, self.detail, self.painted_new = None, {}, None, set()
        if text:
            with self.region.content:
                kit.empty(text)

    def _today(self):
        return xv.with_dividers([{"sort_at": now_ms()}])[0][1]

    def _paint_feed(self, *, append=None):
        """Draw the list - all of it, or only ``append`` below what is drawn."""
        self.region.busy.hide()
        self._paint_trend()
        posts = len(xp.post_rows(self.rows))
        self.count.text = xp.shown(posts, self.exhausted) if self.rows else ""
        self.more_row.set_visibility(bool(self.more) and bool(self.rows) and not self.exhausted)
        if append is None or self.list_card is None:
            if not self.rows:
                self._paint_message(self._empty_text())
                return
            self._paint_message(None)
            with self.region.content:
                self.list_card = ui.column().classes(xp.LIST_CARD)
            items = xv.with_dividers(self.rows)
            if items and items[0] == ("day", self._today()):
                items = items[1:]           # no "today" divider above the first row (as today)
        elif not append:
            return
        else:
            items = xv.with_dividers([self.last_painted, *append])[2:]
        now, hl = now_ms(), self.cfg["new_highlight_s"]
        on_ticker = self._set_ticker if self._may_enqueue else None
        with self.list_card:
            for kind, item in items:
                if kind == "day":
                    ui.label(item).classes(xp.DAY)
                elif kind == "gap":
                    xp.draw_gap(item)
                else:
                    line = xv.row_line(item, now_ms=now, new_highlight_s=hl)
                    key = xp.row_key(item)
                    el = xp.draw_line(item, line, on_open=lambda k=key: self._toggle(k),
                                      on_ticker=on_ticker)
                    el._xfeed_key = key
                    self.lines[key] = (item, el)
                    if line["is_new"]:
                        self.painted_new.add(key)
        self.last_painted = self.rows[-1]
        if append is None and self.open_key in self.lines:
            self._open(self.open_key)       # a repaint keeps the opened post open

    def _toggle(self, key):
        same = self.open_key == key and self.detail is not None
        self._close()
        if not same:
            self._open(key)

    def _close(self):
        if self.detail is not None:
            self.detail.delete()
        self.detail, self.open_key = None, None

    def _open(self, key):
        row, el = self.lines[key]
        self.open_key = key
        idx = self.list_card.default_slot.children.index(el)
        with self.list_card:
            self.detail = ui.column().classes(xp.DETAIL)
        self.detail.move(self.list_card, idx + 1)
        names = {a.get("handle"): a.get("display_name") for a in self._accounts()}
        with self.detail:
            xp.draw_detail(row, account_name=names.get(row.get("account_handle"))
                           or row.get("account_handle") or "",
                           on_ticker=self._set_ticker if self._may_enqueue else None,
                           symbol_linked=self.symbol_linked)

    def _paint_trend(self):
        self.trend_box.clear()
        pairs = [t for t in self.tickers if isinstance(t.get("tag"), str)][:TICKER_CHIPS]
        with self.trend_box:
            xp.draw_trend(pairs, self.f["ticker"].upper(),
                          self._set_ticker if self._may_enqueue else None)

    def _option(self, box, text, on, patch, count=None):
        with box:
            return xp.draw_option(text, on, lambda: self._set_filter(patch), count)

    def _paint_filters(self):
        if self.group_box is None:
            return
        tabs = xv.group_tabs(self.accounts_view or {})
        self.group_box.clear()
        self.group_box.set_visibility(bool(tabs))
        for tab in tabs:
            self._option(self.group_box, tab["label"], self.f["group"] == tab["key"],
                         {"group": tab["key"], "account": ""}, tab["count"])
        self.type_box.clear()
        for key, text in xp.TYPE_OPTIONS:
            opt = self._option(self.type_box, text, self.f["type"] == key, {"type": key})
            opt._xfeed_type = key
        counts = self._counts()
        mine = [a for a in self._accounts() if xp.in_group(a, self.f["group"])]
        self.chip_box.clear()
        with self.chip_box:
            self._chip("All", sum(counts.get(a.get("handle"), 0) for a in mine),
                       not self.f["account"], "")
            for a in mine:
                h = a.get("handle") or ""
                on = self.f["account"] == h
                self._chip(f"@{h}", counts.get(h, 0), on, "" if on else h)
        self.summary.clear()
        with self.summary:
            xp.draw_summary(xp.filter_summary(self.f))

    def _chip(self, text, n, on, account):
        xp.draw_chip(text, n, on, lambda: self._set_filter({"account": account}))

    # ── accounts ─────────────────────────────────────────────────────────
    def _take_accounts(self, payload, *, paint_feed=True):
        self.accounts_view = payload if isinstance(payload, dict) else None
        self.cfg = xp.page_cfg(self.accounts_view)
        keys = [t["key"] for t in xv.group_tabs(self.accounts_view or {})]
        g = self.f["group"]
        gone = g is not None and not any(
            k == g if g == "" else (isinstance(k, str) and k.lower() == g.lower()) for k in keys)
        handles = {a.get("handle") for a in self._accounts()}
        if gone:
            self.f["group"] = None          # its last account moved or left: back to All
        if self.f["account"] and self.f["account"] not in handles:
            self.f["account"] = ""
            gone = True
        self._paint_filters()
        self._paint_accounts()
        self._show_status()
        if gone:
            self._reload()
        elif paint_feed and not self.rows:
            self._paint_feed()              # the empty line depends on the accounts

    @guard_async
    async def _reread_accounts(self):
        self._take_accounts(await run.io_bound(bus_client.read, xv.VIEW_ACCOUNTS))

    def _take_latest(self, payload):
        self.latest = payload if isinstance(payload, dict) else None
        if self._is_live():
            self._from_latest()
        elif self._may_enqueue:
            self._load(merge=self.older, quiet=True)

    @guard_async
    async def _reread_latest(self):
        self._take_latest(await run.io_bound(bus_client.read, xv.VIEW_LATEST))

    def _group_names(self):
        return [t["key"] for t in xv.group_tabs(self.accounts_view or {})
                if isinstance(t["key"], str) and t["key"]]

    def _paint_accounts(self):
        accounts, now = self._accounts(), now_ms()
        n = len(accounts)
        self.acct_note.text = f"{n} account{'' if n == 1 else 's'}" if self.accounts_view else ""
        secs = (self.accounts_view or {}).get("interval_s")
        self.settings_link.text = f"Checking every {secs}s · change in Settings" if secs else ""
        names = self._group_names()
        if self.handle_in is not None:
            self.group_in.set_autocomplete(names)
        sig = (tuple((a.get("handle"), a.get("display_name"), a.get("avatar_url"),
                      a.get("group_name"), a.get("last_error")) for a in accounts),
               frozenset(self.collapsed_groups), self.accounts_view is None)
        if sig == self.acct_sig:
            self._refresh_checked(now)      # only the "checked …" times moved
            return
        self.acct_sig, self.acct_status = sig, {}
        self.acct_list.clear()
        with self.acct_list:
            if self.accounts_view is None:
                kit.empty(COLD)
                return
            if not accounts:
                kit.empty(NO_ONE)
                return
            for title, members in xv.accounts_by_group(self.accounts_view):
                if title and xp.draw_group_head(title, members, title in self.collapsed_groups,
                                                  self._toggle_group):
                    continue                # collapsed
                for a in members:
                    self.acct_status[a.get("handle")] = xp.draw_account(
                        a, now=now, groups=names, on_group=self._move,
                        on_new_group=self._ask_new_group,
                        on_remove=self._ask_remove if self._may_enqueue else None)

    def _toggle_group(self, title):
        self.collapsed_groups ^= {title}
        self._paint_accounts()

    def _refresh_checked(self, now):
        for a in self._accounts():
            label = self.acct_status.get(a.get("handle"))
            if label is not None:
                label.text = xp.account_status(a, now)[0]

    def _set_stale(self, ts):
        self.stale = xp.is_stale(ts, now_ms(), (self.accounts_view or {}).get("interval_s"))
        self._show_status()

    def _show_status(self):
        text, kind = xv.status_text(self.accounts_view or {},
                                    stale=self.stale or self.accounts_view is None)
        self.status.text = text
        self.status.classes(replace=f"{_STATUS} {xp.STATUS_TEXT[kind]}")
        self.status_dot.classes(replace=f"{xp.STATUS_DOT_BASE} {xp.STATUS_DOT[kind]}")

    @guard_async
    async def _poll_status(self):
        meta = await run.io_bound(bus_client.read_meta, xv.VIEW_ACCOUNTS)
        if meta is not None:                # None = the app is stopping
            self._set_stale(meta[1])

    @guard
    def _tick(self):
        now = now_ms()
        self._refresh_checked(now)
        if self.painted_new:
            hl = self.cfg["new_highlight_s"]
            still = {k for k in self.painted_new if k in self.lines and xv.row_line(
                self.lines[k][0], now_ms=now, new_highlight_s=hl)["is_new"]}
            if still != self.painted_new:
                self._paint_feed()          # a NEW mark has aged out

    # ── account actions ──────────────────────────────────────────────────
    @guard
    def _add(self, _e=None):
        text = (self.handle_in.value or "").strip()
        if not text:
            return
        group = (self.group_in.value or "").strip()
        self.add_err.set_visibility(False)
        # Cleared at once so the next handle can be typed while this one is added.
        self.handle_in.value = ""
        kit.set_busy(self.add_btn, timeout=self.cfg["answer_timeout_s"] + 5)
        self._send("add_account", {"handle": text, "group": group or None},
                   lambda a: self._added(a, text))

    def _added(self, answer, text):
        kit.set_busy(self.add_btn, False)
        if answer.get("ok"):
            if not self._is_live():
                self._reload()
            return
        err = TIMEOUT if answer.get("timed_out") else answer.get("error") or "failed"
        self.add_err.text = f"{text}: {err}"
        self.add_err.set_visibility(True)
        if not self.handle_in.value:
            self.handle_in.value = text     # give it back to fix

    def _move(self, handle, group):
        self._send("set_group", {"handle": handle, "group": group},
                   lambda a: self._moved(a))

    def _moved(self, answer):
        if not answer.get("ok"):
            kit.toast("error", self._error(answer, "Could not change the group"))
        elif self.f["group"] is not None:
            self._load()

    def _ask_new_group(self, handle):
        self.target = handle
        self.group_dlg.title.text = f"New group for @{handle}"
        self.group_name_in.value = ""
        self.group_dlg.open()

    def _new_group_confirmed(self):
        name = (self.group_name_in.value or "").strip()
        if not name or not self.target:
            return False
        self._move(self.target, name)
        return None

    def _ask_remove(self, handle):
        self.target = handle
        self.remove_dlg.title.text = f"Stop watching @{handle}?"
        self.remove_dlg.open()

    def _remove_confirmed(self):
        handle = self.target
        if handle:
            self._send("remove_account", {"handle": handle}, lambda a: self._removed(a, handle))

    def _removed(self, answer, handle):
        if not answer.get("ok"):
            kit.toast("error", self._error(answer, f"Could not stop watching @{handle}"))
        elif self.f["account"] == handle:
            self._set_filter({"account": ""})
        elif not self._is_live():
            self._reload()


def render() -> None:
    """Build the X Feed page (the private app only)."""
    import shell as _shell
    _may_enqueue = _shell.may_enqueue()
    XFeedPage(_may_enqueue, symbol_linked=_shell.can_navigate(SYMBOL_ROUTE))
