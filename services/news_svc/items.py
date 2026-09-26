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

# Query keys that identify a click, not a page (any case: UTM_Source too).
_TRACKING = re.compile(
    r"^(utm_|mod$|ref$|src$|cmpid$|ncid$|guccounter$|oc$|fbclid$|gclid$|\.tsrc$)",
    re.I)

# ONE pattern for every explicit form, so matches arrive in document order:
#   cash  ``$NVDA`` / ``$BRK.B``             (a cashtag, one ``.X`` class suffix)
#   pre   ``(NASDAQ: META)`` / ``(NYSE:F)``  (exchange-PREFIXED)
#   bare  ``(META)`` / ``(META:NASDAQ)``     (bare, or exchange-suffixed)
# A bare parenthesis needs 3+ letters: ``(AI)``, ``(IT)``, ``(F)`` are far more
# often abbreviations than tickers, so a 1-2 letter symbol tags only from a
# cashtag or an exchange prefix, where the text itself says "this is a ticker".
_SYM = r"[A-Z]{1,6}(?:\.[A-Z])?"
_EXPLICIT = re.compile(
    rf"\$(?P<cash>{_SYM})\b"
    rf"|\((?i:NYSEARCA|NASDAQ|NYSE|AMEX|CBOE)\s*:\s*(?P<pre>{_SYM})\)"
    r"|\((?P<bare>[A-Z]{3,6}(?:\.[A-Z])?)(?::[A-Z]+)?\)")
_NOISE = re.compile(r"[^\w ]+")
# Scripts written without spaces between words (CJK ideographs, kana, Thai):
# each character counts as one token, or "日本株が上昇" would read as a single
# token and be refused as too short.
_UNSPACED = re.compile(
    r"[฀-๿぀-ヿ㐀-䶿一-鿿豈-﫿]")
_MIN_TITLE_TOKENS = 3


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


def title_key(title: str):
    """A case- and punctuation-insensitive key for spotting one story under
    two URLs, or ``None``.

    ``None`` means "do not merge by title": the title is empty, punctuation
    only, or shorter than ``_MIN_TITLE_TOKENS`` words ("Stocks rise"), so it
    cannot tell one story from another. Unicode word characters are kept, so
    "Nestlé" stays "nestlé" and a non-Latin title keeps a real key rather
    than collapsing to "" and merging with every other such title.
    """
    tokens = _NOISE.sub(" ", (title or "").casefold()).split()
    count = sum(len(_UNSPACED.findall(t)) + (1 if _UNSPACED.sub("", t) else 0)
                for t in tokens)
    if count < _MIN_TITLE_TOKENS:
        return None
    return " ".join(tokens)


def extract_tickers(text: str, universe) -> list:
    """Tickers named EXPLICITLY (``$NVDA`` or ``(NVDA)`` / ``(META:NASDAQ)``)
    and in ``universe``, in the order they appear. A company name never tags —
    precision over recall."""
    allowed = set(universe)
    found = []
    for m in _EXPLICIT.finditer(text or ""):
        sym = clean_symbol(m.group("cash") or m.group("pre") or m.group("bare"))
        if sym and sym in allowed and sym not in found:
            found.append(sym)
    return found


def make_item(*, source, title, url, published_at, public, now, kind="rss",
              original_source="", teaser="", tickers=(), topics=(), detail=None):
    """Build one item. Raises ``ValueError`` without a url: every item needs a
    real id, and adapters skip link-less entries before calling this."""
    if not str(url or "").strip():
        raise ValueError("a news item needs a url")
    # A lone string is ONE value, never an iterable of characters.
    if isinstance(tickers, str):
        tickers = [tickers]
    if isinstance(topics, str):
        topics = [topics]
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
