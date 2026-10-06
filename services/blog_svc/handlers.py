"""``cmd:blog``: the operator's four commands, the three views, and the answer.

    draft_submit   an uploaded document becomes a DRAFT (cleaned, typefaces copied)
    publish        a draft becomes an entry, and the site is rebuilt
    discard        a draft is removed
    unpublish      an entry is removed, and the site is rebuilt

Every one comes from the private app's Blog page (``shared.blog_inbox`` builds
them) and every one is ANSWERED on ``cache:blog:result`` - a refusal as much as
a success - so the page that sent it is never left waiting.

⚠ **``handle_command`` never raises.** The scaffold dead-letters a command whose
handler raised, WHOLE (``_scaffold._run_command``), and a ``draft_submit``
carries the entire document: a raise here would park somebody's unpublished
draft in a Redis list for anyone with the key to read. An unexpected fault is
answered, counted for ``/health`` (``blog.handlers``) and goes no further.

⚠ **Nothing a person wrote reaches a log, and nothing an exception said reaches
the page.** Messages are this module's own whole sentences (``MESSAGES``),
chosen by a CODE: the cleaner's ``Cleaned.reason``, the store's
``StoreRefusal.code``. An exception's text can quote a path or a fragment of
the document, so it is never shown and never logged; the degrade note says
where a fault was (``_trace.where``), never what.

⚠ **No view carries a document.** ``cache:blog:*`` is readable by the public
live process. ``blog:drafts`` and ``blog:posts`` are metadata; the documents
are files in the store's folder, which nothing serves but the private preview.
Both views are written ``skip_unchanged`` and so hold no timestamp of their
own; ``blog:result`` is written plainly, so a repeated answer still repaints.

The service trusts nothing on the stream: every validator in ``blog_inbox`` is
run again on what is read, and a draft's ``source`` is stamped ``"upload"``
whatever the command claims (the connector that would stamp ``"chat"`` is not
built).

Design: docs/plans/2026-10-06-site-blog-design.md ("Shape", "Rules that keep
it safe").
"""
import datetime as dt
import logging
import threading

from services import _degrade
from services.blog_svc import _trace, clean_bound, fonts, sitewriter
from services.blog_svc import store as store_mod
from shared import blog_inbox

log = logging.getLogger("blog_svc.handlers")

AREA = "blog.handlers"

CACHE_DRAFTS = f"cache:{blog_inbox.VIEW_DRAFTS}"
EVENT_DRAFTS = f"events:{blog_inbox.VIEW_DRAFTS}"
CACHE_POSTS = f"cache:{blog_inbox.VIEW_POSTS}"
EVENT_POSTS = f"events:{blog_inbox.VIEW_POSTS}"
CACHE_RESULT = f"cache:{blog_inbox.VIEW_RESULT}"
EVENT_RESULT = f"events:{blog_inbox.VIEW_RESULT}"

# What one row of each view holds. Named here so that nothing else - a draft's
# typeface list, a column added to the store later - reaches a key the public
# live process can read without being added on purpose.
DRAFT_KEYS = ("id", "source", "revises", "slug", "title", "summary", "tags", "removed",
              "font_note", "bytes", "digest", "received_at")
ENTRY_KEYS = ("slug", "title", "summary", "tags", "published_at", "updated_at")

# A staged document may be this many times the largest upload, plus this much.
# Derived, not tunable on its own: cleaning can multiply a document's size about
# six times (every ``"`` inside an attribute is written ``&quot;``;
# ``clean_bound`` holds the worker to the same factor), and the typeface rules
# added afterwards come to about 300 KB at their own limits. Anything past both
# is not something this service made from an upload it accepted.
STAGED_GROWTH = 6
STAGED_SLACK_KB = 512

# Held while the two views are read from the store and written (``publish_views``).
_VIEWS_LOCK = threading.Lock()

_UNREADABLE = "The file could not be read to its end. It may be cut off or malformed."
_NOTHING = "The file had no content that could be shown."
_CLEANER_FAULT = ("The file could not be cleaned because of a fault in the cleaner. "
                  "It has been logged.")
_NOT_UNDERSTOOD = "That request was not understood."
_NO_DRAFT = "That draft is no longer there."

# Every sentence the page can be shown, by code. Whole plain sentences for the
# operator; none holds an exception's text, a path or anything from a document.
MESSAGES = {
    # the cleaner's (``clean.REFUSALS``)
    "empty": _NOTHING,
    "not_text": _NOTHING,
    "crowded_tag": _UNREADABLE,
    "too_deep": _UNREADABLE,
    "cut_off": _UNREADABLE,
    "too_slow": "Cleaning the file took too long, so it was refused.",
    "did_not_settle": _CLEANER_FAULT,
    "internal": _CLEANER_FAULT,
    # the store's (``store.REFUSAL_CODES``)
    "slug_taken": ("An entry already uses this address. Choose another address, or upload "
                   "the file as a replacement for that entry."),
    "slug_changed": "A replacement must keep the address of the entry it replaces.",
    "no_title": "Give the entry a title before publishing.",
    "no_draft": _NO_DRAFT,
    "document_missing": _NO_DRAFT,
    "busy": "The store is busy finishing an earlier change. Try again in a moment.",
    "bad_name": _NOT_UNDERSTOOD,
    "bad_input": _NOT_UNDERSTOOD,
    # this module's own
    "bad_document": "The file was empty, or larger than the {kb} KB an upload may be.",
    "full": ("There are already {count} drafts waiting. Publish or discard one, then "
             "upload the file again."),
    "too_large": "Once cleaned, the file was too large to keep. Upload a smaller one.",
    "revises_gone": ("The entry this file was meant to replace is not published any more. "
                     "Upload the file as a new entry instead."),
    "bad_address": ("That address cannot be used. Use lower-case letters, digits and "
                    "single hyphens, at most {chars} characters."),
    "not_published": ("The draft could not be published. Its address may already be in "
                      "use, or the draft may have been removed."),
    "no_entry": "That entry is not published.",
    "fault": "The request could not be completed because of a fault in the service. "
             "It has been logged.",
    "dropped": "The request waited too long and was not carried out. Try it again.",
    # how a command that worked ended
    "filed": "The file is waiting as a draft.",
    "published": "The entry is published.",
    "published_site_off": ("The entry is published, but the site is switched off in the "
                           "settings, so nothing was written to it."),
    "published_site_failed": ("Published, but the site could not be written. "
                              "It will be retried."),
    "discarded": "The draft was discarded.",
    "unpublished": "The entry was unpublished.",
    "unpublished_site_failed": ("Unpublished, but the site could not be updated. "
                                "It will be retried."),
}


def open_store():
    """The store on its default paths, for ONE command. A seam: the paths are
    read when it is made, so the suite's redirect holds, and a test can put a
    store of its own here."""
    return store_mod.Store()


def _now() -> str:
    """This module's clock. The store reads none: every time it keeps is one
    handed to it."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ── the answer, and the views ────────────────────────────────────────────────

def answer(bus, request_id, command, ok, message, **extra) -> None:
    """How one command ended, on ``cache:blog:result``:
    ``{"request_id", "command", "ok", "message", "draft_id", "slug"}``.

    Always all six keys; an id or address that is not one is ``""``, and so is
    a ``command`` this module does not have. Written WITHOUT ``skip_unchanged``:
    the same refusal twice is two answers, and the page must see the second."""
    draft_id, slug = extra.get("draft_id"), extra.get("slug")
    payload = {
        "request_id": request_id if blog_inbox.is_id(request_id) else "",
        "command": command if isinstance(command, str) and command in COMMANDS else "",
        "ok": bool(ok),
        "message": message if isinstance(message, str) else "",
        "draft_id": draft_id if blog_inbox.is_id(draft_id) else "",
        "slug": slug if isinstance(slug, str) and blog_inbox.existing_slug(slug) == slug else "",
    }
    bus.cache_set(CACHE_RESULT, payload, event=EVENT_RESULT)


def _row(source, keys) -> dict:
    """``source`` cut to ``keys``; a missing value (a draft that revises
    nothing) is ``""``."""
    return {key: ("" if source.get(key) is None else source.get(key)) for key in keys}


def publish_views(bus, store) -> None:
    """``blog:drafts`` and ``blog:posts`` from the store, each newest first.

    Metadata only - never a document - and no timestamp of the view's own:
    both are ``skip_unchanged``, so an unchanged list bumps no version and
    repaints no page, and "updated at" is the bus's ``:ts`` side key."""
    # Read and write as ONE step. The command thread and the scheduler's pass
    # both come through here: unlocked, the scheduler could read the lists,
    # lose the processor to a publish, and then write its older reading over
    # the newer one - a draft shown as waiting for the next half hour.
    with _VIEWS_LOCK:
        drafts = [_row(draft, DRAFT_KEYS) for draft in store.drafts()]
        entries = [_row(entry, ENTRY_KEYS) for entry in store.entries()]
        bus.cache_set(CACHE_DRAFTS, {"drafts": drafts}, event=EVENT_DRAFTS,
                      skip_unchanged=True)
        bus.cache_set(CACHE_POSTS, {"entries": entries}, event=EVENT_POSTS,
                      skip_unchanged=True)


# ── pieces the commands share ────────────────────────────────────────────────

def _code(exc) -> str:
    """The sentence code for something the store raised. Its ``StoreRefusal``
    carries one; any other ``ValueError`` is an input it would not take. The
    exception's own text is never read."""
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and code in MESSAGES else "bad_input"


def _nothing_in_it(cleaned) -> bool:
    """Whether a document that WAS kept has nothing left to show (one that was
    all script, say)."""
    try:
        return not cleaned.body.strip()
    except (ValueError, AttributeError):
        return True


def _too_large(document, limits) -> bool:
    cap = (STAGED_GROWTH * limits["max_html_kb"] + STAGED_SLACK_KB) * 1024
    if len(document) > cap:              # every character is at least one byte
        return True
    try:
        return len(document.encode("utf-8")) > cap
    except UnicodeEncodeError:
        return True


def _entry_named(store, raw):
    """``(entry, code)`` for the address a replacement names. ``(None, None)``
    when it names none; a code when it names one that cannot be used."""
    if raw is None or raw == "":
        return None, None
    slug = blog_inbox.existing_slug(raw)
    if slug is None or slug != raw:
        return None, "bad_input"
    try:
        entry = store.entry(slug)
    except ValueError:
        return None, "bad_input"
    return (entry, None) if entry is not None else (None, "revises_gone")


def _address(raw_fields, fields, draft):
    """The address a publish asked for, or ``None`` when it asked for none that
    can be used.

    Never made up here. A NEW entry is published only at an address the
    operator's fields carry and ``clean_slug`` accepts; anything else is
    refused and said. A REPLACEMENT has one possible address, its entry's, and
    takes it when the fields carry none: that address may be longer than a new
    one may now be, and ``clean_fields`` turns such a one into ``""`` on the
    way here. (If the fields name a different usable address, the store refuses
    it: a replacement cannot rename what it replaces.)"""
    if fields["slug"]:
        return fields["slug"]
    revises = draft.get("revises")
    typed = raw_fields.get("slug") if isinstance(raw_fields, dict) else None
    if revises and typed in (None, "", revises) and blog_inbox.existing_slug(revises) == revises:
        return revises
    return None


def _prune(store) -> None:
    """Drop typefaces nothing names any more. The change this follows has
    already happened, so a failure here is counted and does not undo it."""
    try:
        store.prune_fonts()
    except Exception as exc:
        _degrade.degraded(AREA, detail=_trace.where("pruning typefaces", exc), exc_info=False)


def _write_site(store, now) -> bool:
    """Rebuild the public files, then prune. True when the site agrees with
    the store. ``sitewriter.rebuild`` never raises and counts its own
    shortfalls (``blog.site``); the scheduler runs it again while one stands."""
    report = sitewriter.rebuild(sitewriter.SITE_ROOT, store, now)
    _prune(store)
    return bool(report.get("ok"))


# ── the commands ─────────────────────────────────────────────────────────────

SUBMIT, PUBLISH, DISCARD, UNPUBLISH = blog_inbox.SUBMIT_TYPE, "publish", "discard", "unpublish"


def _cmd_submit(bus, store, request_id, args) -> None:
    """An uploaded document becomes a draft. Never an entry: nothing here
    writes to the site."""
    limits = blog_inbox.limits()

    def refuse(code, **named):
        answer(bus, request_id, SUBMIT, False, MESSAGES[code].format(**named))

    html = args.get("html")
    if not blog_inbox.html_ok(html):
        return refuse("bad_document", kb=limits["max_html_kb"])
    fields = blog_inbox.clean_fields(args.get("fields"))
    revised, why = _entry_named(store, args.get("revises"))
    if why:
        return refuse(why)
    if len(store.drafts()) >= limits["max_drafts"]:
        return refuse("full", count=limits["max_drafts"])

    cleaned = clean_bound.clean_bounded(html)
    if cleaned.reason:
        return refuse(cleaned.reason if cleaned.reason in MESSAGES else "internal")
    if _nothing_in_it(cleaned):
        return refuse("empty")
    # A replacement that asks for no typefaces keeps the ones its entry has.
    links = tuple(cleaned.font_links) or tuple(revised["font_links"] if revised else ())
    copied = fonts.localize(links)
    # ``apply`` exactly once, and nothing cleans the document after it: the
    # cleaner drops every @font-face it meets, these included.
    staged = fonts.apply(cleaned.html, copied)
    if _too_large(staged, limits):
        return refuse("too_large")

    # What the document says of itself, held to the same limits as a typed field.
    own = blog_inbox.clean_fields({"title": cleaned.title, "summary": cleaned.summary})
    title = fields["title"] or own["title"]
    draft = {
        "id": blog_inbox.new_id(),
        "source": "upload",                  # stamped here, whatever the command says
        "revises": revised["slug"] if revised else None,
        "slug": revised["slug"] if revised else (fields["slug"] or blog_inbox.slugify(title)),
        "title": title,
        "summary": fields["summary"] or own["summary"],
        "tags": fields["tags"],
        "removed": dict(cleaned.removed),
        "font_links": list(links),
        "font_note": copied.note,
        "received_at": _now(),
    }
    try:
        store.add_draft(draft, staged, copied.files)
    except ValueError as exc:
        return refuse(_code(exc))
    publish_views(bus, store)
    answer(bus, request_id, SUBMIT, True, MESSAGES["filed"],
           draft_id=draft["id"], slug=draft["slug"])


def _cmd_publish(bus, store, request_id, args) -> None:
    """A draft becomes an entry (or brings the entry it replaces up to date),
    and the site is rebuilt. If the site cannot be written the entry is STILL
    published in the store, and the answer says so."""
    draft_id = args.get("draft_id")

    def refuse(code, **named):
        answer(bus, request_id, PUBLISH, False, MESSAGES[code].format(**named),
               draft_id=draft_id)

    if not blog_inbox.is_id(draft_id):
        return refuse("bad_input")
    draft = store.draft(draft_id)
    if draft is None:
        return refuse("no_draft")
    fields = blog_inbox.clean_fields(args.get("fields"))
    slug = _address(args.get("fields"), fields, draft)
    if slug is None:
        return refuse("bad_address", chars=blog_inbox.limits()["slug_chars"])
    if not fields["title"]:
        return refuse("no_title")
    fields["slug"] = slug
    now = _now()
    try:
        entry = store.publish(draft_id, fields, now)
    except ValueError as exc:
        code = _code(exc)
        # The draft's id was checked above, so the one NAME the store can still
        # turn down is the address: one that has the shape of an address and is
        # a device name on Windows ("aux", "con"). Said as what it is.
        if code == "bad_name":
            return refuse("bad_address", chars=blog_inbox.limits()["slug_chars"])
        return refuse(code)
    if entry is None:
        return refuse("not_published")

    written = _write_site(store, now)
    publish_views(bus, store)
    if not written:
        code = "published_site_failed"
    elif not blog_inbox.site()["enabled"]:
        code = "published_site_off"
    else:
        code = "published"
    answer(bus, request_id, PUBLISH, written, MESSAGES[code], draft_id=draft_id, slug=slug)


def _cmd_discard(bus, store, request_id, args) -> None:
    draft_id = args.get("draft_id")
    if not blog_inbox.is_id(draft_id):
        return answer(bus, request_id, DISCARD, False, MESSAGES["bad_input"])
    gone = store.discard(draft_id)
    if gone:
        _prune(store)
    publish_views(bus, store)
    answer(bus, request_id, DISCARD, gone, MESSAGES["discarded" if gone else "no_draft"],
           draft_id=draft_id)


def _cmd_unpublish(bus, store, request_id, args) -> None:
    """An entry is removed and the site rebuilt. The rebuild runs even when the
    store held no such entry: whatever the store does not hold must not be on
    the site, and this is the operator asking for exactly that."""
    raw = args.get("slug")
    slug = blog_inbox.existing_slug(raw)
    if slug is None or slug != raw:
        return answer(bus, request_id, UNPUBLISH, False, MESSAGES["bad_input"])
    try:
        gone = bool(store.unpublish(slug))
    except ValueError as exc:
        return answer(bus, request_id, UNPUBLISH, False, MESSAGES[_code(exc)], slug=slug)
    written = _write_site(store, _now())
    publish_views(bus, store)
    if not gone:
        code = "no_entry"
    else:
        code = "unpublished" if written else "unpublished_site_failed"
    answer(bus, request_id, UNPUBLISH, gone and written, MESSAGES[code], slug=slug)


COMMANDS = {SUBMIT: _cmd_submit, PUBLISH: _cmd_publish,
            DISCARD: _cmd_discard, UNPUBLISH: _cmd_unpublish}


# ── dispatch ─────────────────────────────────────────────────────────────────

def _read(command) -> tuple:
    """``(type, args, request_id)`` from whatever arrived. A type that is not
    one of ours is ``None``, args that are not a mapping are ``{}``, and a
    request id that is not one is ``""``. Never raises."""
    kind = getattr(command, "type", None)
    args = getattr(command, "args", None)
    args = args if isinstance(args, dict) else {}
    request_id = args.get("request_id")
    return (kind if isinstance(kind, str) and kind in COMMANDS else None, args,
            request_id if blog_inbox.is_id(request_id) else "")


def _dispatch(bus, kind, args, request_id) -> None:
    if not request_id:
        # Nothing could be told how it ended, and nothing this project builds
        # sends one: not run. Said by kind only - the type is the sender's text.
        log.warning("blog: a %s command with no usable request id was dropped",
                    kind or "unrecognised")
        return
    if kind is None:
        answer(bus, request_id, "", False, MESSAGES["bad_input"])
        return
    with open_store() as store:
        COMMANDS[kind](bus, store, request_id, args)


def handle_command(bus, command) -> None:
    """One ``cmd:blog`` command. NEVER raises (see the module docstring): a
    fault is answered, counted and kept from the dead-letter list."""
    kind, args, request_id = None, {}, ""
    try:
        kind, args, request_id = _read(command)
        _dispatch(bus, kind, args, request_id)
    except Exception as exc:
        _degrade.degraded(AREA, detail=_trace.where("handling a blog command", exc),
                          exc_info=False)
        _answer_fault(bus, request_id, kind, "fault")


def _answer_fault(bus, request_id, kind, code) -> None:
    """Tell the page a command did not run to its end. Guarded on its own: when
    the fault IS the bus there is nobody to tell."""
    if not request_id:
        return
    try:
        answer(bus, request_id, kind or "", False, MESSAGES[code])
    except Exception as exc:
        _degrade.degraded(AREA, detail=_trace.where("answering after a fault", exc),
                          exc_info=False)


def on_dropped(bus, command, why) -> None:
    """The scaffold's notice that a command will never run: stranded by a
    restart, or older than the replay limit by the time its turn came. The page
    that sent it is still waiting, so it is told. Never raises."""
    try:
        kind, _args, request_id = _read(command)
        log.info("blog: a %s command was not run (%s)", kind or "unrecognised",
                 why if why in ("restart", "expired") else "dropped")
        _answer_fault(bus, request_id, kind, "dropped")
    except Exception as exc:
        _degrade.degraded(AREA, detail=_trace.where("answering a dropped command", exc),
                          exc_info=False)
