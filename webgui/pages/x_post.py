"""Post to X (/x) — an ad-hoc marketing post, and the log of every X post.

The page composes; the options service posts. ``Post`` enqueues an ``x_post``
command on ``cmd:options`` and the service fits the text, attaches the image
and posts through ``shared/notify/x_post`` — which logs every attempt (market
reports, hourly trade ideas and posts from here) to ``cache:options:x_log``,
the table at the foot of this page.

Tier-1: the live count and preview are ``shared.x_text`` — the SAME weighted
length and fitting the service posts with, so what the page shows is what goes
out. Nothing here imports ``services.*`` or ``shared.notify``. PRIVATE only:
``bus_client.request`` refuses on the public read-only process, and the route
is never registered in ``live_main``.
"""
from __future__ import annotations

import base64
import datetime as _dt
import re
from zoneinfo import ZoneInfo

from nicegui import ui

import bus_client
from pages import ui_kit as kit
from pages.options import theme as _t
from pages.ui_guard import guard, guard_async
from pages.view_watch import watch_view
from shared import x_text

VIEW = "options:x_log"
DEFAULT_LINK = "https://neuralstrike.co"
DEFAULT_TAGS = "#options #trading"
IMAGE_MAX_BYTES = 5_000_000
TEXT_MAX_CHARS = 80
_CT = ZoneInfo("America/Chicago")

_KIND = {"report": "Market report", "trade_idea": "Trade idea", "marketing": "Marketing"}
_STATUS = {"posted": "Posted", "dry_run": "Dry run", "refused": "Refused",
           "failed": "Failed", "unknown": "Unknown"}

# The counter's two states — a FIXED pair swapped with classes(remove=, add=).
COUNT_OK = _t.MUTED
COUNT_OVER = "text-rose-400"

COLUMNS = [
    {"name": "when", "label": "When", "field": "when"},
    {"name": "kind", "label": "Source", "field": "kind"},
    {"name": "status", "label": "Status", "field": "status"},
    {"name": "text", "label": "Text", "field": "text"},
    {"name": "detail", "label": "Link or reason", "field": "detail"},
]


# ── pure helpers ────────────────────────────────────────────────────────────
def parse_tags(raw):
    """Hashtags typed as one string, split on spaces and commas. PURE."""
    return [t for t in re.split(r"[\s,]+", str(raw or "")) if t]


def preview(text, link, tags, max_tags=x_text.DEFAULT_MAX_TAGS):
    """``(post, weighted length)`` exactly as the service would fit it. PURE."""
    post = x_text.fit_text(text, link, x_text.hashtags([], tags, max_tags=max_tags))
    return post, x_text.weighted_len(post)


def _unfitted(text, link, tags, max_tags):
    """The post BEFORE fitting, in ``fit_text``'s own layout: body, a blank
    line, then the link and the tags on the lines under it."""
    body, link = str(text or "").strip(), str(link or "").strip()
    tail = "\n".join(p for p in (link, " ".join(
        x_text.hashtags([], tags, max_tags=max_tags))) if p)
    return "\n\n".join(p for p in (body, tail) if p)


def over_limit(text, link, tags, max_tags=x_text.DEFAULT_MAX_TAGS):
    """Whether the post as WRITTEN exceeds X's limit - i.e. the service will
    drop tags or cut the text to fit it. PURE."""
    return x_text.weighted_len(_unfitted(text, link, tags, max_tags)) > x_text.LIMIT


def _when(at):
    try:
        when = _dt.datetime.fromisoformat(str(at))
    except (TypeError, ValueError):
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    local = when.astimezone(_CT)
    return f"{local.strftime('%b')} {local.day} {local.strftime('%H:%M')}"


def _short(text, limit=TEXT_MAX_CHARS):
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def log_rows(payload):
    """The log view as table rows, newest first as published. PURE.

    ``detail`` is the post's link when it went out, else why it did not."""
    posts = payload.get("posts") if isinstance(payload, dict) else None
    if not isinstance(posts, list):
        return []
    rows = []
    for p in posts:
        if not isinstance(p, dict):
            continue
        kind = p.get("kind")
        status = p.get("status")
        rows.append({
            "id": len(rows),
            "when": _when(p.get("at")),
            "kind": _KIND.get(kind, str(kind or "").replace("_", " ").capitalize()
                              or "Unknown"),
            "status": _STATUS.get(status, str(status or "unknown")
                                  .replace("_", " ").capitalize()),
            "text": _short(p.get("text")),
            "detail": str(p.get("url") or p.get("reason") or ""),
        })
    return rows


def command(text, tags, link, image):
    """The ``cmd:options`` command for one post. PURE. The image travels as
    base64 only when there is one."""
    args = {"text": text, "tags": list(tags), "link": link}
    if image:
        args["image_b64"] = base64.b64encode(image).decode("ascii")
    return {"type": "x_post", "args": args}


# ── page ────────────────────────────────────────────────────────────────────
def render() -> None:
    state = {"image": None, "name": None, "count_cls": COUNT_OK, "sending": False}

    with kit.page():
        kit.header("Post to X", view=VIEW)
        kit.notice("Posts go out from the options service with the settings in "
                   "shared/notifications.json (x). While x.dry_run is on nothing "
                   "reaches X — the log below shows what would have been posted.")

        with ui.column().classes(f"{_t.CARD} w-full gap-3"):
            with kit.field("Post", grow=True):
                body = ui.textarea(placeholder="What do you want to say?") \
                    .props(f"{kit.FIELD_PROPS} autogrow").classes("w-full")
            with ui.row().classes("w-full items-end gap-x-4 gap-y-2 flex-wrap"):
                link = kit.text_field("Link", value=DEFAULT_LINK, width="w-72")
                tags = kit.text_field("Hashtags", value=DEFAULT_TAGS, width="w-72")
                ui.space()
                count = ui.label("").classes(f"text-sm font-semibold {COUNT_OK}")
            with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                upload = ui.upload(auto_upload=True, max_file_size=IMAGE_MAX_BYTES,
                                   label="Image (PNG or JPEG, up to 5 MB)") \
                    .props('accept="image/png,image/jpeg" flat bordered') \
                    .classes("w-72")
                with ui.column().classes("gap-1") as image_box:
                    image_name = ui.label("").classes(f"text-sm {_t.LABEL}")
                    remove = kit.button("Remove image", kind="secondary", icon="close")
                image_box.set_visibility(False)
                ui.space()
                post_btn = kit.button("Post", kind="primary", icon="send")
            kit.section_title("Preview")
            shown = ui.label("").classes(
                f"text-sm whitespace-pre-wrap break-words {_t.LABEL}")

        kit.section_title("Recent posts")
        log_box = ui.column().classes("w-full gap-2")

    confirm = kit.confirm("Post to X?", "", confirm_text="Post", on_confirm=lambda: _send())

    def _values():
        return (body.value or "", link.value or "", parse_tags(tags.value))

    @guard
    def _repaint_preview(_e=None):
        text, lnk, tg = _values()
        post, n = preview(text, lnk, tg)
        count.text = f"{n} / {x_text.LIMIT}"
        cls = COUNT_OVER if over_limit(text, lnk, tg) else COUNT_OK
        if cls != state["count_cls"]:
            count.classes(remove=state["count_cls"], add=cls)
            state["count_cls"] = cls
        shown.text = post
        if state["sending"]:
            return                    # the button is held until the log moves
        if text.strip() or state["image"]:
            post_btn.enable()
        else:
            post_btn.disable()

    @guard_async
    async def _on_upload(e):
        f = e.file
        data = await f.read()
        if len(data) > IMAGE_MAX_BYTES:
            kit.toast("warn", "That image is over 5 MB - X will not take it")
            return
        state["image"], state["name"] = data, f.name
        image_name.text = f.name
        image_box.set_visibility(True)
        upload.reset()
        _repaint_preview()

    @guard
    def _on_rejected(_e=None):
        kit.toast("warn", "Only a PNG or JPEG of up to 5 MB can be attached")

    @guard
    def _remove_image(_e=None):
        state["image"], state["name"] = None, None
        image_name.text = ""
        image_box.set_visibility(False)
        _repaint_preview()

    def _send():
        text, lnk, tg = _values()
        try:
            bus_client.request("options", command(text, tg, lnk, state["image"]))
        except Exception:         # noqa: BLE001 - reported on screen, not raised
            kit.toast("error", "Could not reach the options service")
            return
        # Held until the log moves (the service's answer) or the kit's backstop.
        state["sending"] = True
        kit.set_busy(post_btn)
        kit.toast("info", "Sent to the options service - the log below shows the result")

    def _release():
        state["sending"] = False
        kit.set_busy(post_btn, False)
        _repaint_preview()

    # The kit's backstop re-enables the button on its own; route it through
    # _release so an empty composer is disabled again rather than left live.
    post_btn._kit_gate = lambda: _release() if state["sending"] else _repaint_preview()

    @guard
    def _ask(_e=None):
        text, lnk, tg = _values()
        post, _n = preview(text, lnk, tg)
        confirm.body.text = post + ("\n\n[with image]" if state["image"] else "")
        confirm.body.classes(add="whitespace-pre-wrap")
        confirm.open()

    @guard
    def _repaint_log():
        if state["sending"]:
            _release()
        rows = log_rows(bus_client.read(VIEW))
        log_box.clear()
        with log_box:
            if rows:
                kit.table(COLUMNS, rows, row_key="id")
            else:
                kit.empty("Nothing posted yet.")

    for field in (body, link, tags):
        field.on_value_change(_repaint_preview)
    upload.on_upload(_on_upload)
    upload.on_rejected(_on_rejected)
    remove.on_click(_remove_image)
    post_btn.on_click(_ask)

    _repaint_preview()
    _repaint_log()
    watch_view(VIEW, _repaint_log)
