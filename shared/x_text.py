"""X's text rules — weighted length, hashtags, fitting a post into 280.

Stdlib only: Tier 1 imports it so the /x page's live count is the SAME
computation the service posts with (pinned by shared/tests/test_x_text.py).

X counts a URL as 23 (t.co wrapping) and weights characters outside a few Latin
and punctuation ranges as 2 (twitter-text's v3 config), so an emoji or a CJK
glyph costs two.

Emoji sequences (ZWJ joins, skin tones) are over-counted relative to X - the safe direction.
"""
import re

LIMIT = 280
URL_WEIGHT = 23
DEFAULT_MAX_TAGS = 4
# A link as X sees one: a scheme, a www. prefix, or a bare domain on a common
# TLD (optionally with a /path). The bare form is deliberately narrow, so a
# word ending a sentence ("end. Next") is never taken for a link.
_TLDS = "com|co|net|org|io|ai|app|dev|me|us|uk|xyz|info|biz"
_URL = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|(?<![\w.@-])(?:[a-z0-9-]+\.)+(?:" + _TLDS + r")(?![\w-])(?:/\S*)?",
    re.IGNORECASE,
)
# Trailing punctuation is prose, not part of the link; it counts normally.
_URL_TRAIL = ".,;:!?)]'\""
# twitter-text v3: these code point ranges weigh 1; everything else weighs 2.
_LIGHT = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
_TAG_OK = re.compile(r"^[A-Za-z0-9_]+$")


def _char_weight(ch):
    o = ord(ch)
    return 1 if any(lo <= o <= hi for lo, hi in _LIGHT) else 2


def weighted_len(text):
    text = str(text or "")
    n, pos = 0, 0
    for m in _URL.finditer(text):
        end = m.start() + len(m.group().rstrip(_URL_TRAIL))
        if end <= m.start():
            continue
        n += sum(_char_weight(c) for c in text[pos:m.start()]) + URL_WEIGHT
        pos = end
    return n + sum(_char_weight(c) for c in text[pos:])


def _norm_tag(raw):
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.startswith("$"):
        word = s[1:]
        return f"${word.upper()}" if word and _TAG_OK.match(word) else None
    word = s.lstrip("#")
    return f"#{word}" if word and _TAG_OK.match(word) else None


def max_tags_from(value):
    """``x.max_tags`` as a tag count - the ONE reader of that setting. A missing,
    malformed or bool value (a TOML typo, ``true``) is the default, never an
    exception; a negative one is 0."""
    if value is None or isinstance(value, bool):
        return DEFAULT_MAX_TAGS
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_MAX_TAGS
    return max(0, n)


def hashtags(derived, configured, *, max_tags=DEFAULT_MAX_TAGS):
    """Derived tags first (a trade's own cashtag matters most), then configured,
    normalised, de-duplicated case-insensitively, at most ``max_tags``
    (read through ``max_tags_from``: ``None`` means the default, not zero)."""
    max_tags = max_tags_from(max_tags)
    out, seen = [], set()
    for raw in list(derived or []) + list(configured or []):
        tag = _norm_tag(raw)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out[:max_tags]


def _assemble(body, link, tags):
    tail = "\n".join(p for p in (link, " ".join(tags)) if p)
    return "\n\n".join(p for p in (body, tail) if p)


def fit_text(body, link="", tags=(), *, limit=LIMIT):
    """``body`` + link + tags within ``limit``. Tags drop from the END first;
    only with no tag left is the body cut, with an ellipsis. The link survives.

    Precondition: the link must fit with room to spare (an ellipsis, the
    separators and the link). A link that cannot is returned over the limit
    rather than cut - callers pass one short site link, which always fits."""
    body, link, tags = str(body or "").strip(), str(link or "").strip(), list(tags or [])
    while tags and weighted_len(_assemble(body, link, tags)) > limit:
        tags.pop()
    out = _assemble(body, link, tags)
    if weighted_len(out) <= limit:
        return out
    while body and weighted_len(_assemble(body + "…", link, [])) > limit:
        body = body[:-1]
    return _assemble(body.rstrip() + "…", link, [])
