"""The Social Cards contract: view names, config, captions and the card list.

A blog entry and a trade idea each get a preview image (a PNG and a JPEG copy)
and a caption. ``blog_svc`` and ``options_svc`` draw and publish them as the
views below; the private ``/social`` page reads both, merges them with
``merged`` and serves the files through ``read_card``. Both tiers import this
module, so the keys, the caption wording and the file-serving rule cannot drift.

Stdlib, ``repo_paths`` and ``shared.config_toml`` only: Tier 1 imports it and
``shared/tests/test_social_cards.py`` pins that import set.

Missing file / bad TOML / bad value -> the built-in defaults, never a raise.
"""
import datetime as _dt
import pathlib
import re
from zoneinfo import ZoneInfo

from repo_paths import SOCIAL_CARDS_TOML
from shared.config_toml import toml_loader

VIEW_BLOG = "blog:social_cards"
VIEW_IDEAS = "options:social_cards"

CENTRAL = ZoneInfo("America/Chicago")

DEFAULTS = {
    "cards": {"jpeg_quality": 90, "listed_from": "2026-11-09"},
    "blog": {"title_max_px": 50, "title_min_px": 34, "title_max_lines": 4,
             "summary_px": 20, "summary_max_lines": 3},
    "caption": {"max_hashtags": 4},
}

config, reset_cache = toml_loader(SOCIAL_CARDS_TOML, DEFAULTS, label="social_cards.toml")

KEY_RE = re.compile(r"^(blog|idea):[A-Za-z0-9][A-Za-z0-9._-]*$")


def blog_key(slug):
    return f"blog:{slug}"


def idea_key(day, stem):
    return f"idea:{day}-{stem}"


def _parse(at_iso):
    """A timezone-aware datetime for an ISO string, or None. A naive time is Central."""
    try:
        t = _dt.datetime.fromisoformat(str(at_iso))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=CENTRAL)


def listed(at_iso, cfg=None):
    """True when the post's Central date is on or after ``[cards] listed_from``."""
    t = _parse(at_iso)
    if t is None:
        return False
    cfg = cfg if cfg is not None else config()
    raw = (cfg.get("cards") or {}).get("listed_from")
    try:
        first = _dt.date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        first = _dt.date.fromisoformat(DEFAULTS["cards"]["listed_from"])
    return t.astimezone(CENTRAL).date() >= first


def row(key, kind, at, title, caption, link, png, jpg):
    """One card's list entry, with its ``listed`` flag computed."""
    return {"key": key, "kind": kind, "at": at, "title": title, "caption": caption,
            "link": link, "png": png, "jpg": jpg, "listed": listed(at)}


def hashtag(tag):
    """``#`` + the alphanumerics of ``tag`` (``0-DTE`` -> ``#0DTE``), or None when none."""
    body = "".join(ch for ch in str(tag or "") if ch.isalnum())
    return f"#{body}" if body else None


def blog_caption(title, summary, address, tags, max_hashtags):
    """The post text: title, summary (if any), the address, then hashtags (if any)."""
    parts = [str(title).strip()]
    if summary and str(summary).strip():
        parts.append(str(summary).strip())
    parts.append(f"Read it at {address}")
    tags = [h for h in (hashtag(t) for t in (tags or [])) if h][:max(0, int(max_hashtags))]
    if tags:
        parts.append(" ".join(tags))
    return "\n\n".join(parts)


def _payload_rows(payload):
    rows = payload.get("cards") if isinstance(payload, dict) else None
    return rows if isinstance(rows, list) else []


def merged(blog_payload, ideas_payload):
    """Listed rows from both payloads, newest first; malformed rows are dropped."""
    keep = []
    for r in _payload_rows(blog_payload) + _payload_rows(ideas_payload):
        if not isinstance(r, dict) or not r.get("listed"):
            continue
        if not isinstance(r.get("key"), str) or not isinstance(r.get("at"), str):
            continue
        t = _parse(r["at"])
        if t is not None:
            keep.append((t, r))
    keep.sort(key=lambda p: p[0], reverse=True)
    return [r for _, r in keep]


def read_card(site_root, rel_path, rows):
    """The bytes of a card image, or None.

    Only a ``png`` / ``jpg`` value some row in ``rows`` names is served, resolved
    under ``site_root``, and only a regular, non-symlink file ending ``.png`` or
    ``.jpg``: the caller's path is a lookup key, never a path."""
    if not isinstance(rel_path, str) or not rel_path:
        return None
    if not any(isinstance(r, dict) and rel_path in (r.get("png"), r.get("jpg")) for r in rows or []):
        return None
    rel = pathlib.PurePosixPath(rel_path)
    if rel.is_absolute() or ".." in rel.parts or "\\" in rel_path or rel.suffix not in (".png", ".jpg"):
        return None
    try:
        root = pathlib.Path(site_root).resolve()
        path = root.joinpath(*rel.parts)
        if path.is_symlink() or not path.is_file():
            return None
        if root not in path.resolve().parents:
            return None
        return path.read_bytes()
    except OSError:
        return None
