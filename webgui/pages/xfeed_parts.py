"""The X Feed page's parts (``pages/xfeed.py``): pure helpers and the painters
for the cards and filters, one post line, a gap marker, an opened post and
one watched account.

Split from the page the way ``news_view`` / ``symbol_facts`` are: nothing here
reads the bus or sends a command - the page owns the state, the views and the
one ``cmd:xfeed`` sender - so each painter can be built and tested on its own.
Tier-1: ``nicegui`` (imported inside the painters), ``pages.*`` and the stdlib.

⚠ Every post text, name and URL is THIRD-PARTY text. It reaches the page only
through ``ui.label`` / ``ui.link`` / ``ui.image`` - never ``ui.html`` (a
source-level test pins that) - and a URL becomes a link or an image only when
``news_view.safe_href`` passes it (http/https with a host).
"""
from __future__ import annotations

import datetime as _dt
import re
from zoneinfo import ZoneInfo

from pages import xfeed_view as xv
from pages.news_view import safe_href
from pages.options import theme as _t

TOP_TICKERS = 10           # tickers counted for a merged list (the service's top_tickers)
STALE_FACTOR = 3           # the accounts view older than 3 intervals = not answering
DEFAULT_PAGE_CFG = {"new_highlight_s": 60, "answer_poll_s": 0.25, "answer_timeout_s": 20}
TYPE_OPTIONS = (("", "All"), ("post", "Posts"), ("repost", "Reposts"), ("reply", "Replies"))
_TYPE_NAME = dict(TYPE_OPTIONS)
_CT = ZoneInfo("America/Chicago")


# ── styling: fixed Tailwind classes, colours from finite maps ────────────────
# ⚠ ``max-sm:hidden``, never ``hidden sm:flex`` (Quasar's ``.hidden`` is
# ``!important`` - see pages/news.py).
_SM_ONLY = "max-sm:hidden"
_CAPS = "text-[10.5px] font-semibold uppercase tracking-[.14em] text-[#6d76a0] whitespace-nowrap"

# ── the post list ────────────────────────────────────────────────────────────
LIST_CARD = ("w-full gap-0 flex-nowrap rounded-[12px] border border-[#1c2340] "
              "bg-[#0f1428] overflow-hidden")
# A phone gets tighter gaps and narrower cells so the text keeps ~160px.
_ROW = ("w-full items-center gap-2 sm:gap-3 flex-nowrap overflow-hidden pl-3 sm:pl-3.5 "
        "pr-3 sm:pr-4 py-2.5 "
        "border-l-2 border-b border-b-[#1c2340] cursor-pointer hover:bg-white/[0.03]")
# A NEW row's left accent and wash; every other row keeps a transparent border
# so all rows' text starts at the same x (news.py's ROW_ACCENT).
NEW_ACCENT = "border-l-emerald-400 bg-emerald-400/[0.07]"
OLD_ACCENT = "border-l-transparent"
_STAMP = "w-10 sm:w-11 shrink-0 text-[13px] tabular-nums text-[#8891ab] whitespace-nowrap"
_WORD = "w-9 sm:w-11 shrink-0 text-[10.5px] font-bold tracking-[.08em] whitespace-nowrap"
WORD_TEXT = {"NEW": "text-emerald-300", "POST": "text-[#6d76a0]", "RT": "text-sky-300",
             "REPLY": "text-violet-300"}
# A repost's "↻ @author:" may shrink (it never pushes the handle off the row) and
# is dropped on a phone, where the RT word says enough.
_PREFIX = "shrink min-w-0 text-[13px] text-[#8891ab] whitespace-nowrap max-w-[40%] truncate"
_REPOST_PREFIX = f"{_PREFIX} {_SM_ONLY}"
_HEADLINE = "text-[14px] font-medium text-[#eef1f6] truncate min-w-0 flex-1"
_MARKS = f"shrink-0 flex items-center gap-1.5 flex-nowrap text-[#8891ab] {_SM_ONLY}"
_MARK_ICON = "text-[15px]"
_MARK_N = "text-[11.5px] tabular-nums"
_TICKERS = f"shrink-0 flex items-center gap-1.5 flex-nowrap {_SM_ONLY}"
_TCHIP = ("text-[11px] font-semibold px-1.5 py-px rounded-[4px] border border-[#2a3358] "
          "bg-[#141a30] text-[#a9b6ff] shrink-0 whitespace-nowrap")
_MORE_TICKERS = ("text-[11px] font-semibold px-1.5 py-px rounded-[4px] border "
                 "border-[#2a3358] text-[#c7cee6] shrink-0 whitespace-nowrap cursor-default")
_HANDLE = ("w-28 max-sm:w-[4.5rem] shrink-0 text-right text-[12.5px] max-sm:text-[11.5px] "
           "text-[#7d93ff] truncate")
DAY = (f"{_CAPS} w-full px-3.5 pt-3 pb-1.5 border-b border-b-[#1c2340] "
        "bg-[#0c1124]")
_GAP = (f"w-full items-center gap-2 flex-nowrap px-3.5 py-2 border-b border-b-[#1c2340] "
        f"bg-amber-400/[0.06] {_t.TXT_WARN}")
_GAP_TEXT = "text-[12.5px] min-w-0"
DETAIL = ("w-full gap-3 flex-nowrap px-4 py-3.5 border-l-2 border-l-[#6b86ff] "
           "border-b border-b-[#1c2340] bg-[#121937]")
_WHO = "items-baseline gap-1.5 flex-wrap text-[13px] text-[#8891ab]"
_WHO_NAME = "font-semibold text-[#eef1f6]"
_FULL = "w-full whitespace-pre-wrap break-words text-[14px] leading-relaxed text-[#dfe4f2]"
_LINK = "inline text-[#7d93ff] no-underline hover:underline break-all"
_CASHTAG = "inline font-semibold text-[#a9b6ff] cursor-pointer hover:underline"
_MEDIA = "w-full items-start gap-2 flex-wrap"
_PHOTO = "w-44 h-32 rounded-[8px] border border-[#252c46]"
_QUOTE = ("w-full gap-1 flex-nowrap rounded-[10px] border border-[#2a3358] bg-[#0f1428] "
          "px-3 py-2.5 no-underline")
_QUOTE_TEXT = "w-full whitespace-pre-wrap break-words text-[13px] text-[#c7cee6]"
_STATS = "w-full items-center gap-x-4 gap-y-1 flex-wrap text-[12.5px] text-[#8891ab]"
_STAT = "items-center gap-1 flex-nowrap"
_OPEN_X = "ml-auto text-[12.5px] font-semibold text-[#7d93ff] no-underline hover:underline"

# ── the cards, the filters and MOST MENTIONED ────────────────────────────────
CAPS = _CAPS
_CARD_HEAD = "w-full items-center gap-2 flex-nowrap min-h-[28px]"
_CARD_NOTE = "text-[11.5px] text-[#6d76a0] whitespace-nowrap"
SEG = ("items-center gap-1 flex-wrap rounded-[10px] border border-[#252c46] "
       "bg-[#141a30] p-1")
_SEG_OPT = ("items-center gap-1.5 flex-nowrap rounded-[7px] px-3 py-1 text-[13px] "
            "cursor-pointer select-none")
_SEG_ON = "bg-[#232b4d] text-[#eef1f6]"
_SEG_OFF = "text-[#8891ab] hover:text-[#eef1f6]"
_CHIP = ("items-center gap-1.5 flex-nowrap rounded-[8px] border px-2.5 py-1 "
         "cursor-pointer select-none text-[13px]")
_CHIP_OFF = "border-[#252c46] bg-[#141a30] text-[#c7cee6] hover:border-[#3a4570]"
_CHIP_ON = "border-[#6b86ff] bg-[#6b86ff]/15 text-[#eef1f6]"
_CHIP_N = "text-[11px] text-[#6d76a0] tabular-nums"
_SUMMARY_CHIP = ("text-[12px] rounded-[6px] border border-[#6b86ff] bg-[#6b86ff]/15 "
                 "text-[#eef1f6] px-2 py-px whitespace-nowrap")
_TREND = "items-center gap-1.5 flex-nowrap rounded-[6px] border px-2 py-0.5 select-none"
_TREND_OFF = "border-[#2a3358] bg-[#141a30] hover:border-[#3a4570]"
_TREND_ON = "border-[#6b86ff] bg-[#6b86ff]/15"
_TREND_SYM = "text-[12px] font-bold text-[#eef1f6]"

# ── the accounts panel ───────────────────────────────────────────────────────
_GROUP_HEAD = ("w-full items-center gap-1.5 flex-nowrap pt-2.5 pb-1 cursor-pointer "
               "select-none border-b border-b-[#1c2340]")
_GROUP_NAME = "text-[11.5px] font-semibold uppercase tracking-[.1em] text-[#c7cee6] truncate"
_ERR_DOT = "w-1.5 h-1.5 rounded-full bg-rose-400 shrink-0"
_ACCT = ("w-full items-center gap-2.5 flex-nowrap py-2 border-b border-b-[#1c2340] "
         "last:border-b-0")
_AVATAR = "w-8 h-8 rounded-full shrink-0"
_INITIALS = ("w-8 h-8 rounded-full shrink-0 bg-[#232b4d] text-[#c7cee6] text-[11px] "
             "font-bold flex items-center justify-center")
_ACCT_NAME = "text-[13px] font-semibold text-[#eef1f6] truncate w-full"
_ACCT_LINE = "items-center gap-1 flex-nowrap min-w-0 w-full text-[12px]"
_ACCT_HANDLE = "text-[#7d93ff] no-underline hover:underline shrink-0"
STATUS_TEXT = {"ok": "text-[#6d76a0]", "error": _t.TXT_NEG}
STATUS_DOT_BASE = "w-2 h-2 rounded-full shrink-0"
STATUS_DOT = {"ok": "bg-emerald-400", "error": "bg-rose-400"}


# ── pure helpers ─────────────────────────────────────────────────────────────
def row_key(r) -> str:
    """One row's identity (app.js ``keyOf``): a gap by id, a post by account and id."""
    if r.get("kind") == "gap":
        return f"gap:{r.get('id')}"
    return f"{r.get('account_handle')}:{r.get('status_id')}"


def post_rows(rows) -> list:
    return [r for r in rows if isinstance(r, dict) and r.get("kind") == "post"]


def merge_rows(have, fresh) -> list:
    """``have`` plus the rows of ``fresh`` it lacks, newest first; on a tie a post
    sorts before a gap (the store's order)."""
    keys = {row_key(r) for r in have}
    merged = list(have) + [r for r in fresh if isinstance(r, dict) and row_key(r) not in keys]
    return sorted(merged, key=lambda r: (-int(r.get("sort_at") or 0), r.get("kind") == "gap"))


def top_tickers(rows, n=TOP_TICKERS) -> list:
    """``[{"tag", "n"}]`` over the posts in ``rows`` (the service's ``top_tickers``)."""
    counts: dict = {}
    for r in post_rows(rows):
        for t in r.get("cashtags") or []:
            if isinstance(t, str):
                counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"tag": t, "n": c} for t, c in ranked[:n]]


def compact(n) -> str:
    """A count as the old page wrote it: 950, 1.5K, 12K, 3.4M."""
    n = n if isinstance(n, (int, float)) and not isinstance(n, bool) else 0
    if n >= 1e6:
        return f"{n / 1e6:.{0 if n >= 1e7 else 1}f}M"
    if n >= 1e3:
        return f"{n / 1e3:.{0 if n >= 1e4 else 1}f}K"
    return str(int(n))


def posted_text(ms) -> str:
    """"Posted Fri 13 Nov 14:05 CT" - the post's own time, Central."""
    try:
        d = _dt.datetime.fromtimestamp(int(ms) / 1000, tz=_CT)
    except (TypeError, ValueError, OverflowError, OSError):
        return "Posted —"
    return f"Posted {d.strftime('%a')} {d.day} {d.strftime('%b %H:%M')} CT"


def account_status(account, now) -> tuple:
    """``checked_text``, with an error worded as the old page did."""
    text, kind = xv.checked_text(account, now)
    if kind == "error":
        text = "account not found" if text == "not found" else f"error: {text}"
    return text, kind


def initials(account) -> str:
    name = account.get("display_name") or account.get("handle") or ""
    words = re.sub(r"[^A-Za-z0-9 ]", "", str(name)).split()
    return "".join(w[0] for w in words)[:2].upper() or "@"


def is_stale(ts, now, interval_s) -> bool:
    """The accounts view's last-confirmed stamp is older than STALE_FACTOR
    intervals (or missing). ``now`` is epoch ms."""
    try:
        when = _dt.datetime.fromisoformat(str(ts)) if ts else None
    except ValueError:
        when = None
    if when is None:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    secs = interval_s if isinstance(interval_s, int) and interval_s > 0 else 60
    return now / 1000 - when.timestamp() > STALE_FACTOR * secs


def page_cfg(accounts_view) -> dict:
    """The page's tunables from the accounts view (config/xfeed.toml [page])."""
    got = accounts_view.get("page") if isinstance(accounts_view, dict) else None
    cfg = dict(DEFAULT_PAGE_CFG)
    for k in cfg:
        v = got.get(k) if isinstance(got, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            cfg[k] = v
    return cfg


def page_limit(payload, fallback) -> int:
    """The row cap the service used for ``payload`` (the latest view or a query
    answer names it as ``limit``); a page holding that many posts has more
    behind it. ``fallback`` (the page's ask) when the payload does not say."""
    v = payload.get("limit") if isinstance(payload, dict) else None
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else fallback


def filter_summary(f) -> list:
    """The active filters as short words (the collapsed filter card's line)."""
    parts = []
    if f.get("group") is not None:
        parts.append(f["group"] or "Ungrouped")
    if f.get("account"):
        parts.append(f"@{f['account']}")
    if f.get("type"):
        parts.append(_TYPE_NAME.get(f["type"], f["type"]))
    if f.get("ticker"):
        parts.append(f"${f['ticker']}")
    if f.get("q"):
        parts.append(f"“{f['q']}”")
    return parts


def in_group(account, group) -> bool:
    if group is None:
        return True
    name = account.get("group_name") or ""
    return not name if group == "" else name.lower() == group.lower()


def watching(n) -> str:
    return f"Watching {n} account{'' if n == 1 else 's'}. New posts appear here as they are posted."


def shown(posts, exhausted) -> str:
    return f"{posts}{'' if exhausted else '+'} post{'' if posts == 1 else 's'} shown"


# ── painters: the cards, the filters, MOST MENTIONED ─────────────────────────
def collapsible(title, note=True):
    """A card head - title, note, a show/hide toggle - and the body it hides.
    Returns ``(title label, note label or None, extra row, body)``; ``extra``
    shows only while the body is hidden (the Filters card's summary)."""
    from nicegui import ui

    from pages import ui_kit as kit
    from pages.ui_guard import guard
    with ui.row().classes(_CARD_HEAD):
        label = ui.label(title).classes(_CAPS)
        note_lbl = ui.label("").classes(_CARD_NOTE) if note else None
        extra = ui.row().classes("items-center gap-1.5 flex-wrap min-w-0")
        ui.space()
        toggle = kit.icon_button("expand_less", tooltip=f"Show or hide the {title.lower()}")
    body = ui.column().classes("w-full gap-3 flex-nowrap")

    @guard
    def flip(_e=None):
        body.set_visibility(not body.visible)
        toggle.props(f"icon={'expand_less' if body.visible else 'expand_more'}")
        extra.set_visibility(not body.visible)
    toggle.on_click(flip)
    extra.set_visibility(False)
    return label, note_lbl, extra, body


def draw_option(text, on, on_pick, count=None):
    """One option of a segmented picker (group tab, post type); returns it."""
    from nicegui import ui

    from pages.ui_guard import guard
    with ui.row().classes(f"{_SEG_OPT} {_SEG_ON if on else _SEG_OFF}") as opt:
        ui.label(text)
        if count is not None:
            ui.label(str(count)).classes(_CHIP_N)
    opt.on("click", guard(lambda _e=None: on_pick()))
    return opt


def draw_chip(text, n, on, on_pick):
    """One account chip with its post count."""
    from nicegui import ui

    from pages.ui_guard import guard
    with ui.row().classes(f"{_CHIP} {_CHIP_ON if on else _CHIP_OFF}") as chip:
        ui.label(text)
        ui.label(str(n)).classes(_CHIP_N)
    chip.on("click", guard(lambda _e=None: on_pick()))


def draw_summary(words):
    """The folded Filters card's summary: one chip per active filter."""
    from nicegui import ui
    for w in words:
        ui.label(w).classes(_SUMMARY_CHIP)
    if not words:
        ui.label("None, showing everything").classes(_CARD_NOTE)


def draw_trend(pairs, chosen, on_pick):
    """MOST MENTIONED: ``pairs`` of ``{"tag", "n"}``; the ``chosen`` tag is lit and
    a click on it clears it. ``on_pick(tag)`` is None where nothing may send."""
    from nicegui import ui

    from pages.ui_guard import guard
    if not pairs:
        return
    ui.label("MOST MENTIONED").classes(f"{_CAPS} mr-1")
    for t in pairs:
        tag = t["tag"]
        on = chosen == tag
        cls = f"{_TREND} {_TREND_ON if on else _TREND_OFF}"
        with ui.row().classes(cls + (" cursor-pointer" if on_pick else "")) as chip:
            ui.label(f"${tag}").classes(_TREND_SYM)
            ui.label(str(t.get("n") or 0)).classes(_CHIP_N)
        if on_pick:
            chip.on("click", guard(lambda _e=None, t=tag, on=on: on_pick("" if on else t)))


def draw_group_head(title, members, collapsed, on_toggle):
    """A group's heading in the accounts panel; returns ``collapsed``."""
    from nicegui import ui

    from pages.ui_guard import guard
    errors = sum(1 for a in members if a.get("last_error"))
    with ui.row().classes(_GROUP_HEAD) as head:
        ui.icon("chevron_right" if collapsed else "expand_more").classes("text-[16px] text-[#6d76a0]")
        ui.label(title).classes(_GROUP_NAME)
        ui.space()
        if collapsed and errors:
            with ui.element("span").classes(_ERR_DOT):
                ui.tooltip(f"{errors} with errors")
        ui.label(str(len(members))).classes(_CHIP_N)
    head.on("click", guard(lambda _e=None: on_toggle(title)))
    return collapsed


# ── painters: one post line, a gap, an opened post ───────────────────────────
def draw_line(r, line, *, on_open, on_ticker):
    """One post as one line; ``line`` is ``xfeed_view.row_line``. ``on_ticker``
    None draws the tickers as plain chips. Returns the row element."""
    from nicegui import ui

    from pages.ui_guard import guard
    word = "NEW" if line["is_new"] else line["type_word"]
    with ui.row().classes(f"{_ROW} {NEW_ACCENT if line['is_new'] else OLD_ACCENT}") as el:
        ui.label(line["time"]).classes(_STAMP)
        ui.label(word).classes(f"{_WORD} {WORD_TEXT.get(word, '')}")
        if r.get("type") == "repost":
            ui.label(f"↻ @{r.get('author_handle') or ''}:").classes(_REPOST_PREFIX)
        elif r.get("type") == "reply":
            ui.label("↳").classes(_PREFIX)
        ui.label(line["text"]).classes(_HEADLINE)
        if line["photos"] or line["videos"] or line["has_quote"]:
            with ui.element("div").classes(_MARKS):
                if line["photos"]:
                    ui.icon("image").classes(_MARK_ICON)
                    ui.label(str(line["photos"])).classes(_MARK_N)
                if line["videos"]:
                    ui.icon("play_circle").classes(_MARK_ICON)
                if line["has_quote"]:
                    ui.icon("format_quote").classes(_MARK_ICON)
        with ui.element("div").classes(_TICKERS):
            for t in line["chips"]:
                chip = ui.label(t).classes(_TCHIP)
                if on_ticker is not None:
                    chip.classes("cursor-pointer hover:underline")
                    chip.on("click.stop", guard(lambda _e=None, t=t: on_ticker(t)))
            if line["more"]:
                rest = [t for t in r.get("cashtags") or [] if isinstance(t, str)][2:]
                with ui.label(f"+{line['more']}").classes(_MORE_TICKERS):
                    ui.tooltip(", ".join(rest))
        ui.label(line["handle"]).classes(_HANDLE)
    el.on("click", guard(lambda _e=None: on_open()))
    return el


def draw_gap(gap):
    from nicegui import ui
    with ui.row().classes(_GAP):
        ui.icon("warning_amber").classes("text-[16px] shrink-0")
        ui.label(xv.gap_text(gap)).classes(_GAP_TEXT)


def _rich(text, *, on_ticker, symbol_linked):
    """The post's text as labels and links (``xfeed_view.split_text``). A
    cashtag opens a small menu: filter the feed by it, or open its Symbol page."""
    from nicegui import ui

    from pages.ui_guard import guard
    with ui.element("div").classes(_FULL):
        for seg in xv.split_text(text):
            if seg["kind"] == "text":
                ui.label(seg["text"]).classes("inline")
            elif seg["kind"] in ("url", "mention"):
                ui.link(seg["text"], seg["href"], new_tab=True).classes(_LINK)
            else:
                tag = seg["text"][1:].upper()
                with ui.label(seg["text"]).classes(_CASHTAG):
                    with ui.menu():
                        if on_ticker is not None:
                            ui.menu_item(f"Filter the feed by ${tag}",
                                         on_click=guard(lambda _e=None, t=tag: on_ticker(t)))
                        if symbol_linked:
                            with ui.menu_item():
                                ui.link("Open Symbol page", xv.symbol_href(tag)) \
                                    .classes("no-underline text-inherit")


def _media(r):
    from nicegui import ui
    media = [m for m in r.get("media") or [] if isinstance(m, dict)]
    if not media:
        return
    post_href = safe_href(r.get("url"))
    with ui.row().classes(_MEDIA):
        for m in media:
            photo = m.get("type") == "photo"
            href = safe_href(m.get("url")) if photo else post_href
            thumb = safe_href(str(m.get("thumb") or "").replace("name=orig", "name=small"))
            if thumb:
                box = ui.link(target=href, new_tab=True) if href else ui.element("div")
                with box.classes("shrink-0"):
                    img = ui.image(thumb).classes(_PHOTO).props("fit=cover referrerpolicy=no-referrer")
                    img.props["alt"] = m.get("alt") or ""
            if not photo and post_href:
                ui.link("Play on X ↗", post_href, new_tab=True).classes(_LINK)


def _quote(q):
    from nicegui import ui
    if not isinstance(q, dict):
        return
    href = safe_href(q.get("url"))
    box = ui.link(target=href, new_tab=True) if href else ui.element("div")
    with box.classes(_QUOTE):
        with ui.row().classes(_WHO):
            ui.label(q.get("author_name") or "").classes(_WHO_NAME)
            ui.label(f"@{q.get('author_handle') or ''}")
        if q.get("text"):
            ui.label(q["text"]).classes(_QUOTE_TEXT)


def draw_detail(r, *, account_name, on_ticker, symbol_linked):
    """The opened post: who, the full text, photos, video, the quoted post,
    the counts, "Posted …" and "Open on X ↗"."""
    from nicegui import ui
    with ui.row().classes(_WHO):
        if r.get("type") == "repost":
            ui.label(f"↻ @{r.get('account_handle') or ''} reposted")
            ui.label(r.get("author_name") or "").classes(_WHO_NAME)
            ui.label(f"@{r.get('author_handle') or ''}")
        else:
            ui.label(r.get("author_name") or account_name).classes(_WHO_NAME)
            ui.label(f"@{r.get('author_handle') or ''}")
            if r.get("type") == "reply":
                ui.label("· thread reply")
    if r.get("text"):
        _rich(r["text"], on_ticker=on_ticker, symbol_linked=symbol_linked)
    _media(r)
    _quote(r.get("quote"))
    c = r.get("counts") if isinstance(r.get("counts"), dict) else {}
    with ui.row().classes(_STATS):
        for icon, key in (("chat_bubble_outline", "replies"), ("repeat", "reposts"),
                          ("favorite_border", "likes"), ("visibility", "views")):
            with ui.row().classes(_STAT):
                ui.icon(icon).classes("text-[15px]")
                ui.label(compact(c.get(key)))
        ui.label(posted_text(r.get("created_at")))
        href = safe_href(r.get("url"))
        if href:
            ui.link("Open on X ↗", href, new_tab=True).classes(_OPEN_X)


def draw_account(a, *, now, groups, on_group, on_new_group, on_remove):
    """One watched account: avatar, name, @handle, checked / error, and - when
    ``on_remove`` is given - the group menu and ✕. Returns its status label."""
    from nicegui import ui

    from pages import ui_kit as kit
    from pages.ui_guard import guard
    handle = a.get("handle") or ""
    with ui.row().classes(_ACCT):
        avatar = safe_href(a.get("avatar_url"))
        if avatar:
            ui.image(avatar).classes(_AVATAR).props("referrerpolicy=no-referrer")
        else:
            ui.label(initials(a)).classes(_INITIALS)
        with ui.column().classes("min-w-0 flex-1 gap-0"):
            ui.label(a.get("display_name") or handle).classes(_ACCT_NAME)
            with ui.row().classes(_ACCT_LINE):
                ui.link(f"@{handle}", f"https://x.com/{handle}", new_tab=True).classes(_ACCT_HANDLE)
                ui.label("·").classes("text-[#6d76a0] shrink-0")
                text, kind = account_status(a, now)
                status = ui.label(text).classes(f"truncate min-w-0 {STATUS_TEXT[kind]}")
                if kind == "error":
                    with status:
                        ui.tooltip(a.get("last_error") or "")
        if on_remove is not None:
            current = a.get("group_name") or ""
            with kit.icon_button("drive_file_move", tooltip=f"Change @{handle}'s group"):
                with ui.menu():
                    for g in groups:
                        ui.menu_item(f"{g}{'  ✓' if g == current else ''}",
                                     on_click=guard(lambda _e=None, g=g: on_group(handle, g)))
                    ui.menu_item("Ungrouped" if current else "No group  ✓",
                                 on_click=guard(lambda _e=None: on_group(handle, None)))
                    ui.menu_item("New group…",
                                 on_click=guard(lambda _e=None: on_new_group(handle)))
            kit.icon_button("close", tooltip=f"Stop watching @{handle}",
                            on_click=guard(lambda _e=None: on_remove(handle)))
    return status
