"""Handle normalisation and FxEmbed status mapping (port of handle.js / mapStatus.js).

JS regex semantics are preserved: ``re.ASCII`` keeps ``\b`` ASCII-only and
``fullmatch`` avoids Python's ``$`` matching before a trailing newline.
"""

import re

_RESERVED = frozenset({"home", "explore", "search", "i", "settings", "notifications", "messages"})
_VALID = re.compile(r"[a-z0-9_]{1,15}")
_URL = re.compile(r"^(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/([^/?#]*)", re.IGNORECASE)
_CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z0-9.]{0,9})\b", re.ASCII)


def normalize_handle(text):
    """Turn user input (@name, name, x.com/name, full URL) into a lowercase handle, or None."""
    if not isinstance(text, str):
        return None
    s = text.strip()
    m = _URL.match(s)
    if m:
        s = m.group(1)
    if s.startswith("@"):
        s = s[1:]
    s = s.lower()
    if not _VALID.fullmatch(s) or s in _RESERVED:
        return None
    return s


def extract_cashtags(text):
    """Unique, uppercase cashtags (without $) in order of first appearance."""
    out = []
    for m in _CASHTAG.finditer(str(text or "")):
        tag = m.group(1).rstrip(".").upper()
        if tag and tag not in out:
            out.append(tag)
    return out


def _map_media(media):
    result = []
    for m in (media or {}).get("all") or []:
        mtype = m.get("type")
        kind = "photo" if mtype == "photo" else "gif" if mtype == "gif" else "video"
        result.append(
            {
                "type": kind,
                "url": m.get("url"),
                "thumb": m.get("url") if kind == "photo" else (m.get("thumbnail_url") or None),
                "width": m.get("width") or None,
                "height": m.get("height") or None,
                "alt": m.get("altText") or None,
            }
        )
    return result


def _map_quote(q):
    if not q:
        return None
    author = q.get("author") or {}
    return {
        "author_name": author.get("name") or "",
        "author_handle": author.get("screen_name") or "",
        "text": q.get("text") or "",
        "url": q.get("url"),
    }


def _count(value):
    return 0 if value is None else value


def _scrub_str(s):
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        # A lone UTF-16 surrogate (json.loads keeps one from FxEmbed's JSON) becomes
        # U+FFFD, as Node writes it; a valid pair round-trips unchanged.
        return s.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def scrub(value):
    """``value`` with every string (dict keys included) free of lone surrogates,
    which SQLite cannot store (``UnicodeEncodeError``)."""
    if isinstance(value, str):
        return _scrub_str(value)
    if isinstance(value, dict):
        return {scrub(k): scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def map_status(status, watched):
    """Map an FxEmbed status into the post dict for the watched account.

    Returns None for other people's tweets and for replies to other people
    (only self-thread replies are kept). Every string, ``raw`` included, is
    scrubbed of lone surrogates first.
    """
    status = scrub(status)
    author = status.get("author") or {}
    if status.get("reposted_by"):
        kind = "repost"
    elif (author.get("screen_name") or "").lower() != watched:
        # The with_replies timeline mixes in other people's tweets as conversation context.
        return None
    elif status.get("replying_to"):
        if ((status["replying_to"].get("screen_name")) or "").lower() != watched:
            return None
        kind = "reply"
    else:
        kind = "post"

    return {
        "status_id": str(status.get("id")),
        "account_handle": watched,
        "type": kind,
        "text": status.get("text") or "",
        "author_name": author.get("name") or "",
        "author_handle": author.get("screen_name") or "",
        "author_avatar": author.get("avatar_url") or None,
        "url": status.get("url"),
        "created_at": int(round((status.get("created_timestamp") or 0) * 1000)),
        "cashtags": extract_cashtags(status.get("text")),
        "media": _map_media(status.get("media")),
        "quote": _map_quote(status.get("quote")),
        "counts": {
            "replies": _count(status.get("replies")),
            "reposts": _count(status.get("reposts")),
            "likes": _count(status.get("likes")),
            "views": _count(status.get("views")),
        },
        "raw": status,
    }
