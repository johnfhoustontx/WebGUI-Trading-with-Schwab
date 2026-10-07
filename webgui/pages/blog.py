"""Blog (/blog) - upload an entry, preview the draft, publish it to the site.

The page asks; the blog service does. An uploaded HTML document goes to the
service as a command on ``cmd:blog``; the service cleans it (scripts, forms and
outside requests are taken out) and files it as a DRAFT. Nothing reaches the
site's Blog until the owner presses Publish here, and Unpublish takes an entry
back off. (On the site the list is ``neuralstrike.co/blog.html``, which
``neuralstrike.co/blog`` redirects to at the edge, and an entry is at
``/blog/<address>/``.) Three cache views carry what the page shows: the drafts
waiting, the entries published, and the service's answer to the last command.

Tier-1: the stream name, the command builders, the validators, the frame policy
and the two file reads all come from ``shared.blog_inbox`` - the module the
service itself imports - so the page and the service cannot disagree about what
an address or a draft id is. Nothing here imports the service, parses HTML or
opens a database. PRIVATE only: ``bus_client.request`` refuses on the public
read-only process, and neither the route nor the two preview routes are
registered there.

The preview is the cleaned document, served from the service's staging folder
by ``main.py`` through ``preview_document`` / ``preview_font`` below, inside a
sandboxed frame that grants no scripts.
"""
from __future__ import annotations

import datetime as _dt
import pathlib
import re
import time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from nicegui import ui

import bus_client
from pages import fmt
from pages import ui_kit as kit
from pages.options import theme as _t
from pages.ui_guard import guard, guard_async
from pages.view_watch import watch_view
from repo_paths import SITE_HOST
from shared import blog_inbox

_CT = ZoneInfo("America/Chicago")

# The "this is a new entry" choice in the Replace list. Not an address - an
# address has no asterisk - so it can never collide with an entry's.
NEW_ENTRY = "*new"
NEW_ENTRY_LABEL = "No, this is a new entry"

BLANK = "about:blank"
UNREACHABLE = "Could not reach the blog service."

# What cleaning took out, in the owner's words: ``(one, several)`` per count the
# service reports. Anything it reports that has no word here is summed as
# "other elements". The order is the order the sentence lists them in.
REMOVED_WORDS = {
    "script": ("script", "scripts"),
    "form": ("form", "forms"),
    "handler": ("event handler", "event handlers"),
    "link": ("outside link to a stylesheet or font",
             "outside links to stylesheets or fonts"),
    "css": ("style rule", "style rules"),
    "img": ("image", "images"),
    "iframe": ("embedded frame", "embedded frames"),
    "href": ("unsafe link address", "unsafe link addresses"),
}
_OTHER = ("other element", "other elements")

_SOURCES = {"upload": "Uploaded file", "chat": "Claude Chat"}

# A host name as ``repo_paths.SITE_HOST`` spells one, with an optional port.
# Checked because it is written into a link the owner clicks.
_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::\d{1,5})?\Z")

# The Published column shows the date in words and sorts on the stamp itself.
POST_COLUMNS = [
    {"name": "title", "label": "Title", "field": "title"},
    {"name": "published", "label": "Published", "field": "published_at",
     ":format": "(val, row) => row.published"},
    {"name": "address", "label": "Address", "field": "address"},
    {"name": "actions", "label": "", "field": "slug"},
]

# Each button acts on the row it sits in: Unpublish emits that row's own
# address, never the table's selection.
#
# ⚠ ``no-wrap`` on each button is load-bearing. A labelled q-btn lets its icon
# and label wrap, so the narrowest it reports to the table is the label ALONE;
# an auto-width cell is sized from that, comes out an icon narrower per button
# than what is drawn in it, and the buttons spill over the next column
# (measured: 38px, as a sideways scrollbar under the table).
_ACTIONS_SLOT = """
<q-td :props="props" auto-width>
  <q-btn v-if="props.row.url" dense flat no-caps no-wrap size="sm" icon="open_in_new"
         color="primary" label="Open on the site"
         :href="props.row.url" target="_blank" rel="noopener"></q-btn>
  <q-btn dense flat no-caps no-wrap size="sm" icon="unpublished" color="negative"
         label="Unpublish" :disable="props.row._busy" :loading="props.row._busy"
         @click.stop="() => $parent.$emit('unpublish', props.row.slug)"></q-btn>
</q-td>
"""

PREVIEW_NOTE = ("This is the cleaned document, exactly as it would be published. "
                "Scripts never run, here or on the site.")
FRAME_TITLE = "Draft preview"


# ── pure helpers ────────────────────────────────────────────────────────────
def _listed(payload, key):
    """The dicts under ``payload[key]``; ``[]`` for anything else. PURE."""
    items = payload.get(key) if isinstance(payload, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _str(value):
    return value if isinstance(value, str) else ""


def _moment(iso):
    """An ISO stamp as a Central-time datetime, or None. A naive stamp is UTC,
    as the bus and the service write one."""
    if not isinstance(iso, str) or not iso:
        return None
    try:
        when = _dt.datetime.fromisoformat(iso)
        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        return when.astimezone(_CT)
    except (ValueError, OverflowError):
        return None


def when_text(iso) -> str:
    """A UTC stamp in Central time: ``Oct 6, 2:03 PM``. ``""`` on junk. PURE."""
    local = _moment(iso)
    if local is None:
        return ""
    clock = local.strftime("%I:%M %p").lstrip("0")
    return f"{local.strftime('%b')} {local.day}, {clock}"


def date_text(iso) -> str:
    """A UTC stamp as its Central date: ``Oct 6, 2026``. ``""`` on junk. PURE."""
    local = _moment(iso)
    return "" if local is None else f"{local.strftime('%b')} {local.day}, {local.year}"


def size_text(n) -> str:
    """A size in bytes as ``35 KB`` or ``1.5 MB``; ``""`` when it is not a
    size. A real file is never shown as 0 KB. PURE."""
    size = fmt.num(n) if isinstance(n, (int, float)) else None
    if size is None or size < 0:
        return ""
    if size >= 1024 * 1024:
        return f"{fmt.plain(round(size / (1024 * 1024), 1))} MB"
    kb = max(1, round(size / 1024)) if size > 0 else 0
    return f"{fmt.plain(kb)} KB"


def source_text(source) -> str:
    """Where a draft came from, from the owner's side. PURE."""
    return _SOURCES.get(source, "Unknown") if isinstance(source, str) else "Unknown"


def removed_text(removed) -> str:
    """What cleaning took out, as one sentence. PURE.

    ``{"script": 2, "form": 1}`` -> ``Removed 2 scripts and 1 form.``; a count
    that is given and empty -> ``Nothing was removed.``; anything that is not a
    count at all -> ``""``, because "not known" is not "nothing"."""
    if not isinstance(removed, dict):
        return ""

    def count(value):
        ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
        return value if ok else 0

    parts = []
    for key, (one, many) in REMOVED_WORDS.items():
        n = count(removed.get(key))
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    other = sum(count(v) for k, v in removed.items()
                if not (isinstance(k, str) and k in REMOVED_WORDS))
    if other:
        parts.append(f"{other} {_OTHER[0] if other == 1 else _OTHER[1]}")
    if not parts:
        return "Nothing was removed."
    listed = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
    return f"Removed {listed}."


def decode_upload(data) -> str | None:
    """An uploaded file as text: UTF-8, a byte-order mark tolerated. ``None``
    for anything else. PURE.

    A NUL is refused as well: UTF-16 of plain ASCII with no mark IS valid
    UTF-8, a NUL between every letter, and it is not an HTML document."""
    if not isinstance(data, (bytes, bytearray)):
        return None
    try:
        text = bytes(data).decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text


def preview_src(draft_id) -> str:
    """The address the preview frame loads; ``""`` for a bad id. PURE."""
    if not blog_inbox.is_id(draft_id):
        return ""
    return f"/blog/preview/{draft_id}/{blog_inbox.DOC_NAME}"


def public_url(site_host, slug) -> str:
    """Where an entry is on the public site; ``""`` when either part could not
    be part of a link. PURE."""
    slug = blog_inbox.existing_slug(slug)
    if not slug or not isinstance(site_host, str) or not _HOST_RE.match(site_host):
        return ""
    return f"https://{site_host}/blog/{slug}/"


def publish_fields(title, summary, address, tags_text) -> dict:
    """The form's four strings as the ``fields`` a publish command takes. PURE.

    Nothing is cleaned here: ``blog_inbox.owner_command`` runs ``clean_fields``
    on it, and the service runs it again."""
    tags = [t.strip() for t in _str(tags_text).split(",")]
    return {"title": _str(title), "summary": _str(summary), "slug": _str(address),
            "tags": [t for t in tags if t]}


def address_problem(address, *, existing=False) -> str:
    """``""`` when ``address`` can be an entry's address; else one sentence
    saying why not. PURE.

    The verdict is ``blog_inbox.clean_slug``'s - the check the service runs -
    so the page can never hold Publish over an address the service would take,
    or offer one it would refuse. ``existing=True`` is for an entry that is
    already published (a replacement): it is held to the limit's ceiling, not
    today's setting, so an older, longer address still reads as usable."""
    check = blog_inbox.existing_slug if existing else blog_inbox.clean_slug
    if check(address) is not None:
        return ""
    text = address.strip(" ") if isinstance(address, str) else ""
    if not text:
        return "Enter an address for this entry."
    shaped = text.isascii() and blog_inbox.SLUG_RE.match(text.lower()) is not None
    if not shaped:
        return "Use only letters, digits and single hyphens, with no spaces."
    if text.lower() in blog_inbox.RESERVED_SLUGS:
        return f'The address "{text.lower()}" is reserved by the site, so choose another.'
    cap = (blog_inbox.SLUG_CHARS_CEILING if existing
           else blog_inbox.limits()["slug_chars"])
    return f"An address can be at most {cap} characters, and this one has {len(text)}."


# The service says the same of a publish that reaches it with no title.
NO_TITLE = "Give the entry a title before publishing."


def publish_problem(title, address, *, existing=False) -> str:
    """``""`` when a draft with this title and address can be published; else
    the FIRST reason it cannot, as one sentence. PURE.

    The one verdict behind both things a card shows: the red line, and whether
    Publish is enabled. Two separate checks let the button be greyed out with
    nothing on the card saying why (a blank title had no sentence). The title
    is asked first because it is the first field on the card; ``existing`` is
    ``address_problem``'s."""
    if not _str(title).strip():
        return NO_TITLE
    return address_problem(address, existing=existing)


def draft_rows(payload) -> list:
    """The drafts view as one normalised dict per draft, in the order it was
    published (newest first). PURE; never raises.

    A draft whose id is not an id is dropped: the id becomes part of the
    preview's address and of a command. Every other field is replaced by an
    empty value when it is the wrong type; ``removed`` and ``bytes`` become
    ``None``, which the page shows as nothing rather than as zero."""
    rows, seen = [], set()
    for d in _listed(payload, "drafts"):
        draft_id = d.get("id")
        if not blog_inbox.is_id(draft_id) or draft_id in seen:
            continue
        seen.add(draft_id)
        tags, removed, size = d.get("tags"), d.get("removed"), d.get("bytes")
        sized = isinstance(size, int) and not isinstance(size, bool) and size >= 0
        rows.append({
            "id": draft_id,
            "source": _str(d.get("source")),
            "revises": blog_inbox.existing_slug(d.get("revises")) or "",
            "slug": _str(d.get("slug")),
            "title": _str(d.get("title")),
            "summary": _str(d.get("summary")),
            "tags": [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else [],
            "removed": dict(removed) if isinstance(removed, dict) else None,
            "font_note": _str(d.get("font_note")),
            "bytes": size if sized else None,
            "received_at": _str(d.get("received_at")),
        })
    return rows


def post_rows(payload, site_host) -> list:
    """The published view as table rows, newest first as published. PURE;
    never raises.

    An entry whose address could not be one is dropped. ``url`` is the entry
    on the public site (``""`` when ``site_host`` is not a host name),
    ``address`` the path alone, ``published`` its Central date."""
    rows, seen = [], set()
    for p in _listed(payload, "entries"):
        slug = blog_inbox.existing_slug(p.get("slug"))
        if slug is None or slug in seen:
            continue
        seen.add(slug)
        published = date_text(p.get("published_at"))
        rows.append({
            "slug": slug,
            "title": _str(p.get("title")).strip() or slug,
            "summary": _str(p.get("summary")),
            "published": published,
            "published_at": p.get("published_at") if published else "",
            "address": f"/blog/{slug}/",
            "url": public_url(site_host, slug),
        })
    return rows


# ── the two preview reads (main.py's routes are thin wrappers) ──────────────
def preview_document(draft_id, drafts_payload, data_dir) -> bytes | None:
    """The cleaned document of a draft that is WAITING, or ``None``.

    Everything is checked before the disk is touched: ``draft_id`` must be an
    id, and the drafts view must list it - the view supplies the SHA-256 of the
    document, and ``blog_inbox.read_document`` returns only the file with that
    digest. So what is previewed is what the service cleaned, and a folder left
    on disk by a draft that was discarded is not served. Never raises."""
    if not blog_inbox.is_id(draft_id):
        return None
    digest = next((d.get("digest") for d in _listed(drafts_payload, "drafts")
                   if d.get("id") == draft_id), None)
    if not isinstance(digest, str):
        return None
    try:
        folder = pathlib.Path(data_dir) / blog_inbox.STAGING_DIR / draft_id
    except TypeError:
        return None
    return blog_inbox.read_document(folder, digest)


def preview_font(name, data_dir) -> bytes | None:
    """A typeface the previewed document asks for, or ``None``. The name is
    checked before it is joined to a path, and the file is served only when
    its content is what its name says. Never raises."""
    if not isinstance(name, str) or not blog_inbox.FONT_NAME_RE.match(name):
        return None
    try:
        folder = pathlib.Path(data_dir) / blog_inbox.FONTS_DIR
    except TypeError:
        return None
    return blog_inbox.read_font(folder, name)


# ── page ────────────────────────────────────────────────────────────────────
def render() -> None:
    max_bytes = blog_inbox.limits()["max_html_kb"] * 1024
    too_large = (f"That file is larger than {size_text(max_bytes)}, the most the "
                 f"blog accepts.")
    state = {
        "drafts": None,    # what the draft cards were last drawn from
        "posts": None,     # the rows the published table was last drawn from
        "edits": {},       # draft id -> {field: what the owner typed}
        "cards": {},       # draft id -> that card's handles
        "pending": {},     # request id -> (what is held, until when, which button)
        "target": None,    # the draft or entry the open confirm is about
    }

    with kit.page():
        kit.header("Blog", view=blog_inbox.VIEW_DRAFTS)
        kit.notice("Nothing here is public until you press Publish. An uploaded "
                   "document is cleaned first, and its scripts never run.")

        with ui.column().classes(f"{_t.CARD} w-full gap-3"):
            kit.section_title("Add a draft")
            with ui.row().classes("w-full items-end gap-x-4 gap-y-3 flex-wrap"):
                upload = ui.upload(auto_upload=True, max_file_size=max_bytes,
                                   label="HTML file") \
                    .props('accept=".html,.htm,text/html" flat bordered') \
                    .classes("w-72 max-w-full")
                replace = kit.select_field(
                    "Replace an existing entry", {NEW_ENTRY: NEW_ENTRY_LABEL},
                    value=NEW_ENTRY, width="w-72 max-w-full")
            ui.label(f"One self-contained HTML document of up to "
                     f"{size_text(max_bytes)}. It appears below as a draft once "
                     f"it has been cleaned.").classes(f"text-xs {_t.MUTED}")

        kit.section_title("Drafts waiting")
        drafts_box = ui.column().classes("w-full gap-3")
        kit.section_title("Published")
        posts_box = ui.column().classes("w-full gap-2")

    # Dialogs live at the page's own level: one built inside a box a repaint
    # clears would be deleted with it.
    preview = kit.info_dialog("Preview", width="w-[1100px]")
    with preview.content:
        ui.label(PREVIEW_NOTE).classes(f"text-[12px] {_t.MUTED}")
        # The document is a page of its own with its own scroll. The height is
        # the window less the dialog's margin, head and note, so the dialog
        # never grows a second scrollbar around it. White behind it, as a
        # browser gives a document that sets no background.
        frame = ui.element("iframe").classes(
            "w-full h-[calc(100vh-220px)] min-h-[240px] border-0 rounded bg-white").props(
            f'sandbox="{blog_inbox.ENTRY_SANDBOX}" title="{FRAME_TITLE}" '
            f'referrerpolicy="no-referrer"')
    frame._props["src"] = BLANK
    publish_dlg = kit.confirm("Publish this entry?", "", confirm_text="Publish",
                              on_confirm=lambda: _send_publish())
    discard_dlg = kit.confirm("Discard this draft?", "", confirm_text="Discard",
                              danger=True, on_confirm=lambda: _send_discard())
    unpublish_dlg = kit.confirm("Unpublish this entry?", "", confirm_text="Unpublish",
                                danger=True, on_confirm=lambda: _send_unpublish())

    # ── what is waiting on the service ──────────────────────────────────────
    def _held(key):
        """Whether a command about ``key`` is still waiting for its answer."""
        now = time.monotonic()
        return any(k == key and until > now for k, until, _b in state["pending"].values())

    def _send(command, key, button=None):
        """Enqueue ``command`` and note what it holds. True when it was sent."""
        if command is None:
            kit.toast("warn", "That request could not be built, so nothing was sent.")
            return False
        try:
            bus_client.request(blog_inbox.OWNER_DOMAIN, command)
        except Exception:         # noqa: BLE001 - reported on screen, not raised
            kit.toast("error", UNREACHABLE)
            return False
        now = time.monotonic()
        pending = state["pending"]
        # An answer that never came is forgotten long after its hold ran out.
        for request_id in [r for r, (_k, until, _b) in pending.items() if until + 600 < now]:
            del pending[request_id]
        # Stamped BEFORE the kit's own backstop is armed, so when that fires
        # this hold has already run out and the control comes back enabled.
        pending[command["args"]["request_id"]] = (key, now + kit.BUSY_TIMEOUT_SEC, button)
        return True

    def _release(key):
        """Hand back whatever ``key`` was holding."""
        if key == ("upload",):
            kit.set_busy(upload, False)
        elif key[0] == "draft":
            card = state["cards"].get(key[1])
            if card is not None:
                for button in (card.publish, card.discard):
                    kit.set_busy(button, False)
        else:
            _paint_posts()

    @guard
    def _on_result():
        answer = bus_client.read(blog_inbox.VIEW_RESULT)
        request_id = answer.get("request_id") if isinstance(answer, dict) else None
        # Only an answer to a request THIS page sent: the view holds the last
        # command's answer whoever sent it, and whenever.
        if not isinstance(request_id, str) or request_id not in state["pending"]:
            return
        key, _until, _button = state["pending"].pop(request_id)
        ok = answer.get("ok") is True
        message = _str(answer.get("message")).strip()
        kit.toast("info" if ok else "warn", message or (
            "The blog service finished that request." if ok
            else "The blog service refused that request."))
        _release(key)

    # ── upload ──────────────────────────────────────────────────────────────
    @guard_async
    async def _on_upload(e):
        data = await e.file.read()
        upload.reset()
        if len(data) > max_bytes:
            kit.toast("warn", too_large)
            return
        html = decode_upload(data)
        if html is None:
            kit.toast("warn", "That file is not UTF-8 text, so it cannot be read "
                              "as an HTML document.")
            return
        if not html.strip():
            kit.toast("warn", "That file is empty, so there is nothing to make "
                              "a draft from.")
            return
        if not blog_inbox.html_ok(html):
            kit.toast("warn", too_large)
            return
        revises = None
        chosen = replace.value
        if chosen not in (None, NEW_ENTRY):
            # Checked against what is published NOW: a replacement of an entry
            # that has gone must not be filed quietly as a new one.
            revises = blog_inbox.existing_slug(chosen)
            live = {r["slug"] for r in
                    post_rows(bus_client.read(blog_inbox.VIEW_POSTS), SITE_HOST)}
            if revises is None or revises not in live:
                kit.toast("warn", "The entry you chose to replace is no longer "
                                  "published, so nothing was sent.")
                return
        # The builder puts the entry being replaced into the command, and
        # refuses to build one around an address that could not be an entry's.
        # Nothing here writes into the command it returns.
        command = blog_inbox.submit_command(html, {}, source="upload",
                                            request_id=blog_inbox.new_id(),
                                            revises=revises)
        if command is None:
            kit.toast("warn", "That file could not be turned into a draft, so "
                              "nothing was sent.")
            return
        if not _send(command, ("upload",)):
            return
        replace.value = NEW_ENTRY          # one upload, one replacement
        kit.set_busy(upload)
        kit.toast("info", "Sent to the blog service. The draft appears below "
                          "once it has been cleaned.")

    @guard
    def _on_rejected(_e=None):
        kit.toast("warn", f"Only one HTML file of up to {size_text(max_bytes)} "
                          f"can be uploaded.")

    # ── drafts ──────────────────────────────────────────────────────────────
    def _set_frame(src):
        if frame._props.get("src") != src:
            frame._props["src"] = src
            frame.update()

    def _card(row, replaced_title):
        """One draft's card. A function of its own so every handler below is
        bound to THIS draft, not to whichever the loop ended on."""
        draft_id, key = row["id"], ("draft", row["id"])
        replacing = bool(row["revises"])
        typed = state["edits"].setdefault(draft_id, {})
        start = {"title": row["title"], "summary": row["summary"],
                 "address": row["revises"] or row["slug"],
                 "tags": ", ".join(row["tags"]), **typed}

        with ui.column().classes(f"{_t.CARD} w-full gap-3"):
            with ui.row().classes("w-full items-baseline gap-x-3 gap-y-1 flex-wrap"):
                ui.label(source_text(row["source"])).classes(
                    f"text-sm font-semibold {_t.LABEL}")
                ui.label(" · ".join(t for t in (
                    size_text(row["bytes"]), when_text(row["received_at"])) if t)
                ).classes(f"text-xs {_t.MUTED}")
            if replacing:
                ui.label(f"Replaces {replaced_title}").classes(f"text-sm {_t.TXT_WARN}")
            removed = removed_text(row["removed"])
            if removed:
                ui.label(removed).classes(f"text-sm {_t.LABEL}")
            if row["font_note"]:
                ui.label(row["font_note"]).classes(f"text-sm {_t.TXT_WARN}")

            title = kit.text_field("Title", value=start["title"], width="w-full")
            summary = kit.text_field("Summary", value=start["summary"], width="w-full")
            # The public address and the reason Publish is held (a blank title
            # or an address that cannot be used) sit UNDER the row, full width:
            # a long public address beside the field would push Tags sideways,
            # by a different amount on every card.
            with ui.column().classes("w-full gap-1"):
                with ui.row().classes("w-full items-start gap-x-4 gap-y-2 flex-wrap"):
                    address = kit.text_field("Address", value=start["address"],
                                             width="w-80 max-w-full")
                    tags = kit.text_field("Tags, separated by commas",
                                          value=start["tags"], width="w-80 max-w-full")
                where = ui.label("").classes(f"text-xs break-all {_t.MUTED}")
                problem = ui.label("").classes(f"text-xs {_t.TXT_NEG}")
            if replacing:
                # A replacement keeps the address of the entry it replaces.
                address.props("readonly")

            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                preview_btn = kit.button("Preview", icon="visibility")
                ui.space()
                discard_btn = kit.button("Discard", kind="danger", icon="delete")
                publish_btn = kit.button("Publish", kind="primary", icon="publish")

        def fields():
            return publish_fields(title.value, summary.value, address.value, tags.value)

        @guard
        def sync(_e=None):
            # ONE verdict for the red line and for the button, so Publish is
            # never greyed out with nothing on the card saying why.
            why = publish_problem(title.value, address.value, existing=replacing)
            problem.text = why
            problem.set_visibility(bool(why))
            # The public address depends on the address alone: a blank title
            # does not make a good address stop being shown.
            bad_address = address_problem(address.value, existing=replacing)
            url = "" if bad_address else public_url(SITE_HOST, address.value)
            where.text = f"Public address: {url}" if url else ""
            where.set_visibility(bool(url))
            held = _held(key)
            discard_btn.set_enabled(not held)
            publish_btn.set_enabled(not held and not why)

        def track(field, name):
            def changed(e):
                typed[name] = e.value
                sync()
            field.on_value_change(guard(changed))

        for field, name in ((title, "title"), (summary, "summary"),
                            (address, "address"), (tags, "tags")):
            track(field, name)

        @guard
        def ask_publish(_e=None):
            shown = _str(title.value).strip()
            url = public_url(SITE_HOST, address.value) or "its address"
            state["target"] = draft_id
            publish_dlg.body.text = (
                f'"{shown}" replaces the entry at {url} at once.' if replacing
                else f'"{shown}" becomes public at once at {url}.')
            publish_dlg.open()

        @guard
        def ask_discard(_e=None):
            state["target"] = draft_id
            discard_dlg.body.text = (
                f'The draft "{_str(title.value).strip() or "Untitled"}" and its '
                f"cleaned document are deleted, and nothing public changes.")
            discard_dlg.open()

        @guard
        def open_preview(_e=None):
            preview.title.text = f"Preview: {_str(title.value).strip() or 'Untitled'}"
            _set_frame(preview_src(draft_id) or BLANK)
            preview.open()

        preview_btn.on_click(open_preview)
        discard_btn.on_click(ask_discard)
        publish_btn.on_click(ask_publish)
        # The kit's backstop re-enables a button outright; routed through sync
        # so a Publish the fields do not allow stays held.
        publish_btn._kit_gate = sync
        discard_btn._kit_gate = sync
        card = SimpleNamespace(publish=publish_btn, discard=discard_btn,
                               fields=fields, sync=sync)
        # A card rebuilt while its command is still out is rebuilt held.
        now = time.monotonic()
        for k, until, button in state["pending"].values():
            if k == key and until > now and button in ("publish", "discard"):
                kit.set_busy(getattr(card, button), timeout=until - now)
        sync()
        return card

    @guard
    def _paint_drafts():
        rows = draft_rows(bus_client.read(blog_inbox.VIEW_DRAFTS))
        titles = {p["slug"]: p["title"] for p in state["posts"] or []}
        shown = [(r, titles.get(r["revises"], r["revises"])) for r in rows]
        # The service republishes its views on a timer. Rebuilding a card takes
        # the cursor out of the field being typed in, so an unchanged view
        # rebuilds nothing.
        if shown == state["drafts"]:
            return
        state["drafts"] = shown
        waiting = {r["id"] for r in rows}
        state["edits"] = {k: v for k, v in state["edits"].items() if k in waiting}
        state["cards"] = {}
        drafts_box.clear()
        with drafts_box:
            if not shown:
                kit.empty("No drafts are waiting.")
            for row, replaced_title in shown:
                state["cards"][row["id"]] = _card(row, replaced_title)

    def _send_draft(kind, extra):
        """Send the open confirm's draft a ``publish`` or a ``discard``;
        ``extra(card)`` is that command's own arguments."""
        draft_id = state["target"]
        card = state["cards"].get(draft_id)
        if card is None:
            kit.toast("warn", "That draft is no longer waiting, so nothing was sent.")
            return
        command = blog_inbox.owner_command(kind, blog_inbox.new_id(),
                                           draft_id=draft_id, **extra(card))
        if not _send(command, ("draft", draft_id), kind):
            return
        kit.set_busy(getattr(card, kind))
        card.sync()
        kit.toast("info", "Sent to the blog service. Its answer appears here "
                          "in a moment.")

    def _send_publish():
        _send_draft("publish", lambda card: {"fields": card.fields()})

    def _send_discard():
        _send_draft("discard", lambda card: {})

    # ── published ───────────────────────────────────────────────────────────
    def _paint_posts():
        """Redraw the published table and the Replace list. True when either
        changed."""
        rows = post_rows(bus_client.read(blog_inbox.VIEW_POSTS), SITE_HOST)
        for row in rows:
            row["_busy"] = _held(("post", row["slug"]))
        if rows == state["posts"]:
            return False
        state["posts"] = rows
        options = {NEW_ENTRY: NEW_ENTRY_LABEL, **{r["slug"]: r["title"] for r in rows}}
        replace.set_options(
            options, value=replace.value if replace.value in options else NEW_ENTRY)
        posts_box.clear()
        with posts_box:
            if rows:
                table = kit.table(POST_COLUMNS, rows, row_key="slug")
                table.add_slot("body-cell-actions", _ACTIONS_SLOT)
                table.on("unpublish", _ask_unpublish)
            else:
                kit.empty("Nothing has been published yet.")
        return True

    @guard
    def _repaint_posts():
        if _paint_posts():
            _paint_drafts()            # a "Replaces ..." line names an entry

    @guard
    def _ask_unpublish(e):
        # The address comes from the browser, so it is believed only when it
        # names a row this page is showing.
        slug = blog_inbox.existing_slug(e.args) if isinstance(e.args, str) else None
        row = next((r for r in state["posts"] or [] if r["slug"] == slug), None)
        if row is None:
            return
        state["target"] = slug
        unpublish_dlg.body.text = (
            f'"{row["title"]}" is removed from the public site and '
            f'{row["url"] or row["address"]} stops working.')
        unpublish_dlg.open()

    def _send_unpublish():
        slug = state["target"]
        command = blog_inbox.owner_command("unpublish", blog_inbox.new_id(), slug=slug)
        if not _send(command, ("post", slug)):
            return
        _paint_posts()                 # the row's own button is held
        hold.active = True
        kit.toast("info", "Sent to the blog service. Its answer appears here "
                          "in a moment.")

    @guard
    def _check_holds():
        """The backstop for a held table row, which has no button of its own
        for the kit to time: once its hold has run out, redraw it enabled."""
        now = time.monotonic()
        if not any(k[0] == "post" and until > now
                   for k, until, _b in state["pending"].values()):
            hold.active = False
        if any(r["_busy"] != _held(("post", r["slug"])) for r in state["posts"] or []):
            _paint_posts()

    @guard
    def _on_preview_toggle(e):
        if not e.value:
            _set_frame(BLANK)          # nothing keeps loading behind a closed dialog

    upload.on_upload(_on_upload)
    upload.on_rejected(_on_rejected)
    preview.dialog.on_value_change(_on_preview_toggle)
    with posts_box.parent_slot:
        hold = ui.timer(1.0, _check_holds, active=False)

    _paint_posts()
    _paint_drafts()
    watch_view(blog_inbox.VIEW_DRAFTS, _paint_drafts)
    watch_view(blog_inbox.VIEW_POSTS, _repaint_posts)
    watch_view(blog_inbox.VIEW_RESULT, _on_result)
