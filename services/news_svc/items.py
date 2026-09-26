"""The normalized news item and the pure rules over it (no I/O).

An item is a plain dict so it round-trips through SQLite and Redis unchanged:
id · source · original_source · title · teaser · url · published_at ·
first_seen · tickers · kind · topics · detail · public
"""
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from shared.symbols import clean_symbol

FIELDS = ("id", "source", "original_source", "title", "teaser", "url",
          "published_at", "first_seen", "tickers", "kind", "topics", "detail",
          "public")

# Query keys that identify a click, not a page.
_TRACKING = re.compile(r"^(utm_|mod$|ref$|src$|cmpid$|ncid$|guccounter$|oc$)")

# ONE pattern for both explicit forms, so matches arrive in document order:
# group 1 is a cashtag (``$NVDA``), group 2 a parenthesised ticker
# (``(NVDA)`` or ``(META:NASDAQ)``).
_EXPLICIT = re.compile(r"\$([A-Z]{1,6})\b|\(([A-Z]{1,6}(?::[A-Z]+)?)\)")
_NOISE = re.compile(r"[^a-z0-9 ]+")


def canonical_url(url: str) -> str:
    """``url`` with tracking query keys and the fragment removed and the host
    lower-cased. Never raises: an empty, relative or malformed URL comes back
    as-is (stripped), since an id must still be derivable from it."""
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if not _TRACKING.match(k)]
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path,
                           urlencode(query), ""))
    except ValueError:
        return raw


def item_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:16]


def title_key(title: str) -> str:
    return " ".join(_NOISE.sub(" ", (title or "").lower()).split())


def extract_tickers(text: str, universe) -> list:
    """Tickers named EXPLICITLY (``$NVDA`` or ``(NVDA)`` / ``(META:NASDAQ)``)
    and in ``universe``, in the order they appear. A company name never tags —
    precision over recall."""
    allowed = set(universe)
    found = []
    for m in _EXPLICIT.finditer(text or ""):
        raw = m.group(1) or m.group(2)
        sym = clean_symbol(raw.split(":")[0])
        if sym and sym in allowed and sym not in found:
            found.append(sym)
    return found


def make_item(*, source, title, url, published_at, public, now, kind="rss",
              original_source="", teaser="", tickers=(), topics=(), detail=None):
    clean = []
    for raw in tickers:
        sym = clean_symbol(raw)
        if sym and sym not in clean:
            clean.append(sym)
    return {
        "id": item_id(url), "source": source, "original_source": original_source or "",
        "title": (title or "").strip(), "teaser": (teaser or "").strip(),
        "url": canonical_url(url), "published_at": published_at, "first_seen": now,
        "tickers": clean, "kind": kind, "topics": list(topics),
        "detail": dict(detail or {}), "public": bool(public),
    }
