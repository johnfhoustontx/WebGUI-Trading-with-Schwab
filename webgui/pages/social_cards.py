"""Social Cards (/social) - every blog and trade idea card, and the X composer.

One row per card, newest first: the card image, what it is and when, and three
buttons - Download (the JPEG), Copy caption, and Post to X, which FILLS the
composer below with the card, the caption and the link. Nothing is posted until
the composer's own confirm. The composer and Recent posts are Post to X's
(``x_post.build``), absorbed into this page.

Tier-1: the card list is two cache views (``shared.social_cards``, the module
``blog_svc`` and ``options_svc`` publish them with); image bytes reach the
browser through ``main.py``'s private ``/social/card/<key>/<fmt>`` route, which
calls ``serve`` below and reads only a file the card list names. Nothing here
draws an image, opens a database or imports a service. PRIVATE only: the route
is never registered in ``live_main``.
"""
from __future__ import annotations

import datetime as _dt
import json
import urllib.parse

from nicegui import run, ui

import bus_client
import repo_paths
from pages import ui_kit as kit
from pages import x_post
from pages.options import theme as _t
from pages.ui_guard import guard, guard_async
from pages.view_watch import watch_view
from shared import social_cards as cards

FORMATS = {"png": "image/png", "jpg": "image/jpeg"}
EMPTY_TEXT = "No cards yet."
SITE_OFF_TEXT = ("Trade idea cards appear here only while trade ideas are "
                 "published to the site (Settings → General).")
COPIED_TEXT = "Caption copied."
REFUSED_TEXT = ("The browser would not write to the clipboard. Allow "
                "clipboard access for this site and try again.")
NO_IMAGE_TEXT = "That card's image could not be read - the composer has the caption only."

_KIND_LABEL = {"blog": "Blog", "idea": "Trade idea"}
# A finite state -> a FIXED class (never a runtime-built colour).
_KIND_CHIP = {"blog": "bg-sky-500/20 text-sky-300",
              "idea": "bg-emerald-500/20 text-emerald-300"}
_CHIP = "text-xs font-semibold rounded px-2 py-0.5"


# ── pure helpers ────────────────────────────────────────────────────────────
def rows_for(blog_payload, ideas_payload):
    """The listed cards from both views, newest first. PURE."""
    return cards.merged(blog_payload, ideas_payload)


def site_switch_off(ideas_payload):
    """True only when the ideas view SAYS trade ideas are not published to the
    site; a view not published yet says nothing. PURE."""
    return isinstance(ideas_payload, dict) and ideas_payload.get("site_enabled") is False


def filename(key, fmt):
    """``blog:alpha`` + ``png`` -> ``blog-alpha.png``. PURE."""
    return f"{str(key).replace(':', '-')}.{fmt}"


def card_url(key, fmt, download=False):
    """The route's address for one card image. PURE."""
    url = f"/social/card/{urllib.parse.quote(str(key), safe=':')}/{fmt}"
    return url + "?download=1" if download else url


def serve(key, fmt, download, blog_payload, ideas_payload, site_root):
    """What the image route answers: ``(bytes, media type, Content-Disposition
    or None)``, or ``None`` for a 404.

    The key and format are LOOKUP keys: the key must be a well-formed card key
    that a LISTED row carries, the format one of ``FORMATS``, and the file
    comes from ``read_card``, which serves only a path that row names."""
    if fmt not in FORMATS or not isinstance(key, str) or not cards.KEY_RE.fullmatch(key):
        return None
    rows = rows_for(blog_payload, ideas_payload)
    row = next((r for r in rows if r.get("key") == key), None)
    if row is None:
        return None
    data = cards.read_card(site_root, row.get(fmt), rows)
    if data is None:
        return None
    disposition = f'attachment; filename="{filename(key, fmt)}"' if download else None
    return data, FORMATS[fmt], disposition


def copy_js(text):
    """The click's browser handler: writes ``text`` to the clipboard IN THE
    BROWSER, inside the click (a write sent from the server after the round
    trip has lost the click, and Safari and a phone refuse it), and reports
    back whether it could."""
    return ("() => { const c = navigator.clipboard; "
            "if (!c || !c.writeText) { emit({ok: false}); return; } "
            f"c.writeText({json.dumps(text)}).then(() => emit({{ok: true}}), "
            "() => emit({ok: false})); }")


def copied(args):
    """Whether the browser handler reported a successful write. PURE."""
    return isinstance(args, dict) and args.get("ok") is True


def _when(at):
    """A post time as Central ``Nov 10 09:00``; a naive time is Central. PURE."""
    try:
        t = _dt.datetime.fromisoformat(str(at))
    except (TypeError, ValueError):
        return ""
    local = t.replace(tzinfo=cards.CENTRAL) if t.tzinfo is None else t.astimezone(cards.CENTRAL)
    return f"{local.strftime('%b')} {local.day} {local.strftime('%H:%M')}"


# ── page ────────────────────────────────────────────────────────────────────
def render() -> None:
    state = {"rows": []}

    with kit.page():
        kit.header("Social Cards")
        kit.section_title("Cards")
        box = ui.column().classes("w-full gap-2")
        kit.section_title("Post to X")
        composer = x_post.build()

    @guard
    def _copied(e):
        if copied(getattr(e, "args", None)):
            kit.toast("ok", COPIED_TEXT)
        else:
            kit.toast("warn", REFUSED_TEXT)

    def _download(row):
        @guard
        def go(_e=None):
            ui.download(card_url(row["key"], "jpg", download=True))
        return go

    def _post(row):
        @guard_async
        async def go(_e=None):
            png = await run.io_bound(cards.read_card, repo_paths.SITE_ROOT,
                                     row.get("png"), state["rows"])
            if png is None:
                kit.toast("warn", NO_IMAGE_TEXT)
            composer.fill(row.get("caption") or "", row.get("link") or "", "", png,
                          filename(row["key"], "png"))
            ui.run_javascript(
                f"document.getElementById('c{composer.card.id}')"
                ".scrollIntoView({behavior: 'smooth'})")
        return go

    def _row(row):
        kind = row.get("kind")
        with ui.row().classes(f"{_t.CARD} w-full items-center gap-4 flex-nowrap"):
            ui.image(card_url(row["key"], "jpg")).classes("w-48 shrink-0 rounded")
            with ui.column().classes("gap-1 grow min-w-0"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(_KIND_LABEL.get(kind, "Card")).classes(
                        f"{_CHIP} {_KIND_CHIP.get(kind, _t.MUTED)}")
                    ui.label(_when(row.get("at"))).classes(f"text-xs {_t.MUTED}")
                ui.label(str(row.get("title") or "")).classes(
                    f"text-sm font-semibold break-words {_t.LABEL}")
            with ui.column().classes("gap-2 shrink-0"):
                kit.button("Download", kind="secondary", icon="download",
                           on_click=_download(row))
                kit.button("Copy caption", kind="secondary", icon="content_copy") \
                    .on("click", _copied, js_handler=copy_js(row.get("caption") or ""))
                kit.button("Post to X", kind="primary", icon="send",
                           on_click=_post(row))

    def _paint(blog_payload, ideas_payload):
        rows = rows_for(blog_payload, ideas_payload)
        state["rows"] = rows
        box.clear()
        with box:
            if not rows:
                kit.empty(EMPTY_TEXT)
            if site_switch_off(ideas_payload):
                ui.label(SITE_OFF_TEXT).classes(f"w-full text-center text-sm {_t.MUTED}")
            for row in rows:
                _row(row)

    @guard_async
    async def _repaint():
        blog_payload = await run.io_bound(bus_client.read, cards.VIEW_BLOG)
        ideas_payload = await run.io_bound(bus_client.read, cards.VIEW_IDEAS)
        _paint(blog_payload, ideas_payload)

    _paint(bus_client.read(cards.VIEW_BLOG), bus_client.read(cards.VIEW_IDEAS))
    watch_view(cards.VIEW_BLOG, _repaint)
    watch_view(cards.VIEW_IDEAS, _repaint)
