"""X's text rules — weighted length, hashtags, fitting a post into 280.

Stdlib only: Tier 1 imports it so the /x page's live count is the SAME
computation the service posts with (pinned by shared/tests/test_x_text.py).

X counts a URL as 23 (t.co wrapping) and weights characters outside a few Latin
and punctuation ranges as 2 (twitter-text's v3 config), so an emoji or a CJK
glyph costs two.
"""
import re

LIMIT = 280
URL_WEIGHT = 23
_URL = re.compile(r"https?://\S+")
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
        n += sum(_char_weight(c) for c in text[pos:m.start()]) + URL_WEIGHT
        pos = m.end()
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


def hashtags(derived, configured, *, max_tags=4):
    """Derived tags first (a trade's own cashtag matters most), then configured,
    normalised, de-duplicated case-insensitively, at most ``max_tags``."""
    out, seen = [], set()
    for raw in list(derived or []) + list(configured or []):
        tag = _norm_tag(raw)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out[:max(0, int(max_tags or 0))]


def _assemble(body, link, tags):
    tail = "\n".join(p for p in (link, " ".join(tags)) if p)
    return f"{body}\n\n{tail}" if tail else body


def fit_text(body, link="", tags=(), *, limit=LIMIT):
    """``body`` + link + tags within ``limit``. Tags drop from the END first;
    only with no tag left is the body cut, with an ellipsis. The link survives."""
    body, link, tags = str(body or "").strip(), str(link or "").strip(), list(tags or [])
    while tags and weighted_len(_assemble(body, link, tags)) > limit:
        tags.pop()
    out = _assemble(body, link, tags)
    if weighted_len(out) <= limit:
        return out
    while body and weighted_len(_assemble(body + "…", link, [])) > limit:
        body = body[:-1]
    return _assemble(body.rstrip() + "…", link, [])
