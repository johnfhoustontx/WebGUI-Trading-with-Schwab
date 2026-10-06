"""The Blog on the public site: entry pages, documents, typefaces, the list.

``rebuild`` makes the served folder agree with the store. It is the ONLY thing
that writes the blog's public files, and publish and unpublish both go through
it, so there is one order of writes and one rule for what may be deleted:

    deploy/site/blog.json                  the list ``assets/blog.js`` draws
    deploy/site/blog/<slug>/index.html     the entry's page: the site menu round a frame
    deploy/site/blog/<slug>/entry.html     the entry itself, as the store holds it
    deploy/site/blog/fonts/<20 hex>.woff2  every typeface a published entry uses
    deploy/site/blog/sitemap.txt           the entries' addresses

All of it is GITIGNORED generated state, like ``ideas/``: tracked, the first
published entry would dirty prod's tree and ``tools/promote.sh`` refuses a
dirty tree. The store (``store.py``) is the source of truth; nothing here is.

The menu is the tracked one
---------------------------
Each entry page carries the ``<nav>`` of the tracked ``blog.html``, byte for
byte, with its icon links, stylesheets and footer (``read_parts``). They are
read each time, so a promote that changes the menu changes every entry page at
the next rebuild (the service runs one at start). A checkout whose ``blog.html``
is missing, or lacks any of those pieces, writes NOTHING and says so: an entry
must never be published under no menu.

The order, and why
------------------
Typefaces, then each entry's document and page, then the sitemap, then
``blog.json`` LAST: a page exists before the list links to it. Removal comes
after that, so on an unpublish the list has stopped naming the entry before
its folder goes. Each file is written to a temporary name in its own folder and
renamed over the real one, and a file whose bytes are already right is not
touched at all - so a rebuild with nothing to do writes nothing.

``blog.json`` carries ``updated``, which is when the LIST last changed, not
when this last ran: it is left alone while the entries in it are the same.

What is never deleted
---------------------
* **A page the store could not supply.** When ``store.entry_html`` has no
  document for an entry the store still lists, that entry's public files are
  left exactly as they are, it stays in the list if its page is there, and the
  rebuild is reported not ok. A store that has lost a document must not take a
  public page down with it. The same for a typeface.
* **Anything this module did not write.** It removes only: ``index.html`` and
  ``entry.html`` (and then the emptied folder) under a ``blog/<name>`` whose
  name is an address the store does not hold; a ``blog/fonts/<name>`` named
  like a typeface that no entry uses; its own half-written temporary files. A
  stray file, a folder named some other way, a file beside an entry's own two:
  all left. A link is never followed, to write or to remove.

``rebuild`` never raises. Every way it can fall short ends as ``ok: False``, is
counted for ``/health`` (``blog.site``) with a note that says where and never
what - an exception's text can quote a path or a document - and is retried by
the scheduler (``pending``).

Design: docs/plans/2026-10-06-site-blog-design.md ("What lands on the site",
"Rules that keep it safe").
"""
import contextlib
import datetime as dt
import html
import json
import logging
import os
import pathlib
import re
import secrets
import threading
from zoneinfo import ZoneInfo

import repo_paths
from services import _degrade
from services.blog_svc import _trace
from shared import blog_inbox

log = logging.getLogger("blog_svc.sitewriter")

SITE_ROOT = pathlib.Path(repo_paths.SITE_ROOT)   # tests redirect this (conftest)
MANIFEST, BLOG_DIR, FONTS_DIR, SITEMAP = "blog.json", "blog", "fonts", "sitemap.txt"
# The tracked page the menu is lifted from, and an entry's two files.
TEMPLATE = "blog.html"
PAGE, DOC = "index.html", "entry.html"
AREA = "blog.site"

# The menu, over the RAW file (deploy/tests/test_site.py pins this expression
# from the site's side, as SERVICE_NAV_RE). Exactly one match is accepted.
NAV_RE = re.compile(r'<nav class="ns-nav.*?</nav>', re.S)
# The rest is looked for with the comments taken out, so a link that is only
# quoted in one is not copied into every entry page.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_ICON_RE = re.compile(r'<link rel="(?:icon|apple-touch-icon)"[^>]*>')
_STYLE_RE = re.compile(r'<link rel="stylesheet"[^>]*>')
_FOOT_RE = re.compile(r'<footer class="ns-foot ns-live-foot">(.*?)</footer>', re.S)
# The stylesheet the entry page's own classes are defined in. A page without it
# is the menu and a frame with no layout.
_SITE_CSS = 'href="assets/site.css"'

_CT = ZoneInfo("America/Chicago")
# Written out, not ``%B``: that one follows the process's locale.
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _tmp_re(*names):
    """This module's own half-written files for ``names``:
    ``.<final name>.<12 hex>.tmp``. Nothing else that ends in ``.tmp``."""
    return re.compile(r"^\.(?:%s)\.[0-9a-f]{12}\.tmp\Z" % "|".join(names))


# One pattern per folder, so a name shaped like one of ours in a folder where
# this module never writes that file is still not ours.
_ROOT_TMP = _tmp_re(re.escape(MANIFEST))
_BLOG_TMP = _tmp_re(re.escape(SITEMAP))
_ENTRY_TMP = _tmp_re(re.escape(PAGE), re.escape(DOC))
_FONT_TMP = _tmp_re(r"[0-9a-f]{20}\.woff2")

# One rebuild at a time: the command thread (a publish) and the scheduler's
# retry are different threads of one process, and each sweeps the other's
# temporary files.
_LOCK = threading.Lock()
# Whether the last rebuild fell short. Read by the scheduler (``pending``).
_STATE = {"ok": True}


# ── the tracked page's pieces ────────────────────────────────────────────────

def read_parts(root) -> dict | None:
    """What an entry page takes from the tracked ``blog.html`` under ``root``:
    ``{"nav", "icons", "styles", "footer"}``, each exactly as that file has it.
    ``None`` when the file is not there or ANY of them is missing.

    Read as bytes and decoded, so no newline is translated: the menu is the
    file's own bytes."""
    try:
        raw = (pathlib.Path(root) / TEMPLATE).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    navs = NAV_RE.findall(raw)
    markup = _COMMENT_RE.sub("", raw)
    icons, styles = _ICON_RE.findall(markup), _STYLE_RE.findall(markup)
    feet = _FOOT_RE.findall(markup)
    if len(navs) != 1 or len(feet) != 1 or not icons:
        return None
    if not any(_SITE_CSS in link for link in styles):
        return None
    pieces = (navs[0], feet[0], *icons, *styles)
    if any("<script" in piece.lower() for piece in pieces):
        return None                 # an entry page runs nothing, whatever the tracked page does
    return {"nav": navs[0], "icons": tuple(icons), "styles": tuple(styles), "footer": feet[0]}


# ── pure: the list, and one entry's page ─────────────────────────────────────

def manifest(entries, updated) -> dict:
    """PURE. What ``blog.json`` holds: each entry's address, title, summary,
    tags and its two dates, newest FIRST publication first (a revision does not
    move an entry up). Never a document."""
    rows = [{"slug": entry["slug"], "title": entry["title"], "summary": entry["summary"],
             "tags": list(entry["tags"]), "published": entry["published_at"],
             "updated": entry["updated_at"]} for entry in entries]
    rows.sort(key=lambda row: row["slug"])
    # Stored times are UTC in one spelling (``store._iso``), so text order is
    # time order.
    rows.sort(key=lambda row: row["published"], reverse=True)
    return {"updated": updated, "entries": rows}


def _day(iso) -> str:
    """``"October 6, 2026"`` for the Central-time day of ``iso``; ``""`` for a
    time that cannot be read or carries no timezone. Central, because that is
    the clock the rest of the site keeps and the one ``blog.js`` dates the list
    in."""
    try:
        when = dt.datetime.fromisoformat(iso)
        if when.utcoffset() is None:
            return ""
        local = when.astimezone(_CT)
    except (TypeError, ValueError, OverflowError):
        return ""
    return f"{_MONTHS[local.month - 1]} {local.day}, {local.year}"


def _esc(value) -> str:
    return html.escape(value if isinstance(value, str) else "", quote=True)


def entry_page(parts, entry, site_host) -> str:
    """PURE. The whole page for one entry: the site's menu, a compact header
    and the entry in a sandboxed frame.

    ``parts`` is ``read_parts``' result and goes in as it is. Every value that
    comes from ``entry`` or ``site_host`` is escaped for an attribute, which is
    also right for text. The page holds no script, and ``<base href="/">`` comes
    before anything it must resolve: the tracked menu's addresses are written
    from the site root."""
    slug, title, summary = _esc(entry.get("slug")), _esc(entry.get("title")), \
        _esc(entry.get("summary"))
    address = f"https://{_esc(site_host)}/{BLOG_DIR}/{slug}/"
    document = f"{BLOG_DIR}/{slug}/{DOC}"
    day = _day(entry.get("published_at"))
    dated = (f'        <time datetime="{_esc(entry.get("published_at"))}">{day}</time> ·\n'
             if day else "")
    head = "\n".join((
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<base href="/">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>NeuralStrike — {title}</title>",
        f'<meta name="description" content="{summary}">',
        f'<link rel="canonical" href="{address}">',
        '<meta property="og:type" content="article">',
        f'<meta property="og:title" content="{title}">',
        f'<meta property="og:description" content="{summary}">',
        f'<meta property="og:url" content="{address}">',
        '<meta name="theme-color" content="#161826">',
        *parts["icons"],
        *parts["styles"],
        "</head>",
    ))
    body = (
        "<body>\n"
        '<div class="ns-page-sub ns-live-page">\n'
        f'  {parts["nav"]}\n'
        '  <main class="ns-live-main ns-report-main">\n'
        '    <div class="ns-live-head ns-report-head">\n'
        '      <p class="ns-eyebrow">Blog</p>\n'
        f'      <h1 class="ns-blog-crumb" title="{title}">{title}</h1>\n'
        '      <p class="ns-report-links">\n'
        f"{dated}"
        f'        <a href="{TEMPLATE}">All entries</a> ·\n'
        f'        <a href="{document}">Open the entry on its own page</a>\n'
        "      </p>\n"
        "    </div>\n"
        f'    <iframe class="ns-report-frame" sandbox="{blog_inbox.ENTRY_SANDBOX}" '
        f'src="{document}" title="{title}"></iframe>\n'
        "  </main>\n"
        f'  <footer class="ns-foot ns-live-foot">{parts["footer"]}</footer>\n'
        "</div>\n"
        "</body>\n"
        "</html>\n"
    )
    return head + "\n" + body


# ── files ────────────────────────────────────────────────────────────────────

def _own(base, name):
    """``base/name`` when that is really where it is; else ``None``.

    Not when it is a link, and not when it resolves to anywhere but a child of
    ``base`` under that same name (a junction, a link further up the name). A
    name reaches here only after its gate - an address, a typeface name - so
    this is the check that holds if something on the DISK is not what this
    module put there. Nothing is written through, or removed from behind, a
    path this refuses."""
    path = base / name
    try:
        if path.is_symlink():
            return None
        real = path.resolve()
        if real.parent != base.resolve() or real.name != name:
            return None
    except OSError:
        return None
    return path


def _listing(folder) -> list:
    """What is in ``folder``, by name; nothing when it is not there."""
    try:
        return sorted(folder.iterdir(), key=lambda child: child.name)
    except OSError:
        return []


def _holds(path, data) -> bool:
    """Whether ``path`` is already a plain file with exactly these bytes.
    Compared by content: a modification time says nothing about what a restore
    or a hand edit left there."""
    try:
        return not path.is_symlink() and path.read_bytes() == data
    except OSError:
        return False


def _write(path, data) -> bool:
    """Put ``data`` at ``path`` whole or not at all: a temporary name in the
    same folder, flushed, then renamed over the real one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)          # served by Caddy, a different user
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return True


def _put(path, data, report) -> None:
    """``_write``, unless the file is already right."""
    if not _holds(path, data) and _write(path, data):
        report["written"] += 1


def _sweep(folder, pattern) -> None:
    """Delete this module's own half-written files in ``folder``."""
    for child in _listing(folder):
        if pattern.match(child.name) and not child.is_symlink() and child.is_file():
            with contextlib.suppress(OSError):
                child.unlink()


def _remove_entry(folder) -> bool:
    """Take an unpublished entry off the site: its page, its document, then the
    folder if that emptied it. True when the folder is gone.

    Only the two files this module writes there (and its own temporary ones).
    A folder that still holds something else is left with it."""
    for name in (PAGE, DOC):
        with contextlib.suppress(FileNotFoundError):
            (folder / name).unlink()
    _sweep(folder, _ENTRY_TMP)
    try:
        folder.rmdir()
    except OSError:
        log.warning("blog site: an unpublished entry's folder could not be removed "
                    "(it may hold files this service did not write); its page and "
                    "document are gone")
        return False
    return True


# ── rebuild ──────────────────────────────────────────────────────────────────

def _fail(report, doing, exc=None) -> None:
    """One way the rebuild fell short: not ok, and counted. The note says where
    (``_trace.where``), never the exception's own text."""
    report["ok"] = False
    _degrade.degraded(AREA, detail=_trace.where(doing, exc) if exc is not None else doing,
                      exc_info=False)


def _rows(store, report) -> list:
    """The store's entries whose address is one, spelled as its gate spells it.
    Any other row is counted and builds no path."""
    rows, refused = [], 0
    for row in store.entries():
        slug = row.get("slug") if isinstance(row, dict) else None
        if isinstance(slug, str) and blog_inbox.existing_slug(slug) == slug:
            rows.append(row)
        else:
            refused += 1
    if refused:
        _fail(report, f"{refused} entry row(s) with a name that is not an address")
    return rows


def _font_names(row) -> list:
    listed = row.get("fonts")
    return [name for name in (listed if isinstance(listed, list) else ())
            if isinstance(name, str) and blog_inbox.FONT_NAME_RE.match(name)]


def _write_fonts(folder, store, names, report) -> None:
    """Every typeface a published entry uses. One the store cannot supply - or
    supplies with bytes that are not what its name says - is skipped and its
    public copy, if there is one, is left as it is."""
    for name in names:
        try:
            data = store.font_bytes(name)
            path = _own(folder, name)
            if path is None or not data or blog_inbox.font_name_for(data) != name:
                report["fonts_skipped"].append(name)
                _fail(report, "a typeface an entry uses could not be supplied")
                continue
            _put(path, bytes(data), report)
        except OSError as exc:
            report["fonts_skipped"].append(name)
            _fail(report, "writing a typeface", exc)


def _write_entry(blog, parts, store, row, report) -> bool:
    """One entry's document, then its page. Returns whether the entry belongs
    in the list: True when it was written, and also when it could not be but
    its page is already there (see "What is never deleted")."""
    slug = row["slug"]
    folder = _own(blog, slug)
    try:
        document = None if folder is None else store.entry_html(slug)
        if document is not None:
            page = entry_page(parts, row, repo_paths.SITE_HOST)
            _put(folder / DOC, document.encode("utf-8"), report)
            _put(folder / PAGE, page.encode("utf-8"), report)
            return True
        _fail(report, "an entry's document could not be supplied; its page was left as it is")
    except (OSError, ValueError) as exc:
        _fail(report, "writing an entry", exc)
    report["skipped"].append(slug)
    return folder is not None and (folder / PAGE).is_file()


def _manifest_bytes(path, listing, updated):
    """The bytes ``blog.json`` should hold, or ``None`` when the one on disk
    already lists exactly these entries (its ``updated`` is then left alone)."""
    try:
        old = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        old = None
    if (isinstance(old, dict) and isinstance(old.get("updated"), str)
            and old.get("entries") == listing["entries"]):
        return None
    return json.dumps(listing, indent=1).encode("utf-8")


def _write_lists(root, listed, updated, report) -> None:
    """The sitemap, then ``blog.json`` - the last write of a rebuild."""
    listing = manifest(listed, updated)
    lines = "".join(f"https://{repo_paths.SITE_HOST}/{BLOG_DIR}/{row['slug']}/\n"
                    for row in listing["entries"])
    try:
        _put(root / BLOG_DIR / SITEMAP, lines.encode("utf-8"), report)
    except OSError as exc:
        _fail(report, "writing the entry sitemap", exc)
    try:
        data = _manifest_bytes(root / MANIFEST, listing, updated)
        if data is not None:
            _put(root / MANIFEST, data, report)
    except OSError as exc:
        _fail(report, "writing the list of entries", exc)


def _prune(root, known, used, report) -> None:
    """Remove what the store no longer holds, and only what this module could
    have written (see "What is never deleted")."""
    blog = root / BLOG_DIR
    for child in _listing(blog):
        name = child.name
        if name in known or blog_inbox.existing_slug(name) != name:
            continue
        folder = _own(blog, name)
        if folder is None or not folder.is_dir():
            continue
        try:
            if _remove_entry(folder):
                report["removed"] += 1
        except OSError as exc:
            _fail(report, "removing an unpublished entry", exc)
    fonts = blog / FONTS_DIR
    for child in _listing(fonts):
        name = child.name
        if name in used or not blog_inbox.FONT_NAME_RE.match(name):
            continue
        path = _own(fonts, name)
        if path is None or not path.is_file():
            continue
        try:
            path.unlink()
            report["removed"] += 1
        except OSError as exc:
            _fail(report, "removing an unused typeface", exc)
    _sweep(root, _ROOT_TMP)
    _sweep(blog, _BLOG_TMP)
    _sweep(fonts, _FONT_TMP)
    for slug in sorted(known):
        folder = _own(blog, slug)
        if folder is not None:
            _sweep(folder, _ENTRY_TMP)


def _rebuild(root, store, updated, report) -> None:
    if not blog_inbox.site()["enabled"]:
        return                      # switched off: nothing written, nothing removed
    parts = read_parts(root)
    if parts is None:
        _fail(report, "the tracked blog page is missing or lacks a piece; nothing was written")
        return
    blog = root / BLOG_DIR
    rows = _rows(store, report)
    known = {row["slug"] for row in rows}
    used = {name for row in rows for name in _font_names(row)}
    _write_fonts(blog / FONTS_DIR, store, sorted(used), report)
    listed = [row for row in rows if _write_entry(blog, parts, store, row, report)]
    _write_lists(root, listed, updated, report)
    # After the list. An unpublish is a take-down, so it goes ahead even when
    # the list could not be rewritten: a link to a page that is gone is the
    # lesser fault, and the rebuild is already reported not ok.
    _prune(root, known, used, report)


def rebuild(root, store, now) -> dict:
    """Make the site's blog files agree with ``store``. NEVER raises.

    ``root`` is the served folder (``None``: ``SITE_ROOT``, read at call time);
    ``now`` is the caller's clock, an ISO string, and becomes the list's
    ``updated`` only when the list changes. Idempotent: run again with nothing
    changed, it writes nothing.

    Returns ``{"ok", "written", "removed", "skipped", "fonts_skipped"}``:
    files written; entry folders and typefaces removed; the addresses of
    entries that were NOT written this time (the store had no document for
    them, or the write failed); the typefaces likewise. ``ok`` is False when
    anything fell short - and then the scheduler runs it again (``pending``).

    With ``[site] enabled = false`` it writes nothing and is ok."""
    report = {"ok": True, "written": 0, "removed": 0, "skipped": [], "fonts_skipped": []}
    try:
        base = SITE_ROOT if root is None else pathlib.Path(root)
        updated = now if isinstance(now, str) else now.isoformat()
        with _LOCK:
            _rebuild(base, store, updated, report)
    except Exception as exc:          # a site write never raises into a command
        _fail(report, "writing the blog to the site", exc)
    _STATE["ok"] = report["ok"]
    return report


def pending() -> bool:
    """Whether the last ``rebuild`` in this process fell short and should be
    run again. False before the first one."""
    return not _STATE["ok"]
