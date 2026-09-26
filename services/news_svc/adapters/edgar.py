"""EDGAR: the "current filings" Atom feed, Form 4 purchases, shelf offerings.

Pure parsers over bytes - no network, and none of them raises. The FETCH plan
(compute.py): one Atom fetch per form type per poll; for each NEW Form 4
accession, one fetch of the filing folder's ``index.json`` to find the XML,
one fetch of the XML. <= 10 req/s, always with the configured User-Agent - the
SEC blocks anything else.
"""
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from services.news_svc import items
from shared.symbols import clean_symbol

ATOM = "{http://www.w3.org/2005/Atom}"
CURRENT = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
           "&type={form}&owner=include&count=100&output=atom")
TICKERS_JSON = "https://www.sec.gov/files/company_tickers.json"

_TITLE = re.compile(r"^(?P<form>[\w/-]+) - (?P<company>.+?) \((?P<cik>\d+)\) \((?P<role>[^)]+)\)")
_ACCESSION = re.compile(r"/(\d{10}-\d{2}-\d{6})-index\.htm")
# The ONLY links accepted. An href becomes a public url AND the host the poll
# fetches index.json / the XML from, with the SEC User-Agent - so a link off
# this prefix is refused outright. Plain http:// is refused too rather than
# upgraded: the SEC only ever sends https, so anything else is not the SEC.
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/"
# A default namespace on the root (some filers add one) hides every
# un-prefixed path from ElementTree's lookups; the schema has no other.
_XMLNS = re.compile(rb"""\sxmlns=(?:"[^"]*"|'[^']*')""")
# A DTD is how an entity bomb / external entity arrives; the SEC never sends one.
_DTD = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)
# Placeholders filers type where the issuer has no ticker. They pass the
# ticker allow-list, so they are named here.
_NO_SYMBOL = {"NONE", "NA", "N/A"}
_PREFERRED_XML = ("form4", "primary_doc", "ownership")

# Transaction groups kept in one item's detail. A fund's Form 4 can list
# hundreds of lots; every one would ride in every published view. The headline
# total is computed over ALL of them before the cut.
MAX_GROUPS = 10

FORM_LABEL = {"S-1": "IPO / new registration", "S-3": "shelf registration",
              "S-3ASR": "automatic shelf registration",
              "424B5": "prospectus supplement (offering)"}


def form_label(form) -> str:
    """The plain-English label for a form type; any ``.../A`` is an amendment."""
    form = str(form or "")
    if form.upper().endswith("/A"):
        return f"amended {FORM_LABEL.get(form[:-2], 'registration')}"
    return FORM_LABEL.get(form, "registration")


def _symbol(raw) -> str:
    """A real ticker, or ``""`` - never a filer's NONE / N/A placeholder."""
    sym = clean_symbol(raw)
    return "" if not sym or sym in _NO_SYMBOL else sym


def _utc(stamp, now):
    """``stamp`` as a UTC ``+00:00`` ISO string, or ``now``. The store sorts
    and prunes ``published_at`` as TEXT, so the SEC's ``-04:00`` must not leak."""
    try:
        dt = datetime.fromisoformat(str(stamp).strip()) if isinstance(stamp, str) else None
    except ValueError:
        return now
    if dt is None or dt.tzinfo is None:
        return now
    return dt.astimezone(timezone.utc).isoformat()


def _parse_xml(body):
    """The document root, or ``None`` for anything that is not XML."""
    if not isinstance(body, (bytes, bytearray)) or not body.strip():
        return None
    if _DTD.search(bytes(body[:2048])):
        return None
    try:
        return ET.fromstring(body)
    except (ET.ParseError, ValueError):
        return None


def parse_current(body: bytes) -> list:
    """Entries of a current-filings Atom, one per accession (a Form 4 lists the
    filing twice, as Reporting and as Issuer - the Issuer row wins).

    ``cik`` carries no leading zeros, so it keys ``cik_map`` directly."""
    root = _parse_xml(body)
    if root is None:
        return []
    by_acc = {}
    for entry in root.iter(f"{ATOM}entry"):
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        link = entry.find(f"{ATOM}link")
        href = (link.get("href") or "").strip() if link is not None else ""
        m, a = _TITLE.match(title), _ACCESSION.search(href)
        if not m or not a or not href.startswith(SEC_ARCHIVE):
            continue
        acc = a.group(1)
        rec = {"form": m["form"], "company": m["company"], "cik": str(int(m["cik"])),
               "role": m["role"], "accession": acc, "index_url": href,
               "updated": (entry.findtext(f"{ATOM}updated") or "").strip()}
        if acc not in by_acc or m["role"] == "Issuer":
            by_acc[acc] = rec
    return list(by_acc.values())


def xml_url(index_url: str, index_json):
    """The primary Form 4 XML inside a filing folder, from its ``index.json``."""
    if (not isinstance(index_json, dict) or not isinstance(index_url, str)
            or not index_url.startswith(SEC_ARCHIVE)):
        return None
    directory = index_json.get("directory")
    listing = directory.get("item") if isinstance(directory, dict) else None
    if not isinstance(listing, list):
        return None
    names = []
    for f in listing:
        name = f.get("name") if isinstance(f, dict) else None
        if (not isinstance(name, str) or not name.lower().endswith(".xml")
                or "filingsummary" in name.lower()
                or any(c in name for c in ("/", "\\", "..", ":"))):
            continue
        names.append(name)
    if not names:
        return None
    best = next((n for n in names if any(p in n.lower() for p in _PREFERRED_XML)), names[0])
    folder = index_url.rsplit("/", 1)[0]
    return f"{folder}/{best}"


def _num(el, path):
    """A finite number at ``path``, or ``None`` - never a NaN or infinity,
    which would carry straight through a sum into the headline."""
    try:
        v = float((el.findtext(path) or "").strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _flag(rel, tag):
    return (rel.findtext(tag) or "").strip().lower() in ("1", "true")


def parse_form4_status(body):
    """``(status, detail)`` for one Form 4 document:

    * ``("ok", detail)`` - its open-market purchases (``detail`` as
      ``parse_form4`` returns it);
    * ``("no_purchase", None)`` - a readable ownership document with none (a
      sale, a grant - the normal case);
    * ``("poison", reason)`` - not something a retry can read: not XML, a DTD,
      or not an ownership document.

    Never raises. A purchase whose shares or price is missing, non-numeric or
    non-finite is SKIPPED rather than counted as zero-or-NaN: it cannot be
    valued."""
    if isinstance(body, (bytes, bytearray)):
        body = _XMLNS.sub(b"", bytes(body), count=1)
    root = _parse_xml(body)
    if root is None:
        return "poison", "the Form 4 XML does not parse"
    if root.tag.rsplit("}", 1)[-1] != "ownershipDocument":
        return "poison", f"not an ownership document (<{root.tag}>)"
    groups = []
    for tx in root.iter("nonDerivativeTransaction"):
        if (tx.findtext("transactionCoding/transactionCode") or "").strip() != "P":
            continue
        shares = _num(tx, "transactionAmounts/transactionShares/value")
        price = _num(tx, "transactionAmounts/transactionPricePerShare/value")
        if shares is None or price is None:
            continue
        groups.append({"code": "P",
                       "security": (tx.findtext("securityTitle/value") or "").strip(),
                       "shares": shares, "price": price, "value": shares * price,
                       "date": (tx.findtext("transactionDate/value") or "").strip()})
    if not groups:
        return "no_purchase", None
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    labels = []
    if rel is not None:
        if _flag(rel, "isDirector"):
            labels.append("Director")
        if _flag(rel, "isOfficer"):
            labels.append((rel.findtext("officerTitle") or "").strip() or "Officer")
        if _flag(rel, "isTenPercentOwner"):
            labels.append("10% Owner")
    insiders = []
    for owner in root.iter("reportingOwner"):
        name = (owner.findtext("reportingOwnerId/rptOwnerName") or "").strip()
        if name and name not in insiders:
            insiders.append(name)
    return "ok", {"symbol": _symbol(root.findtext("issuer/issuerTradingSymbol")),
                  "company": (root.findtext("issuer/issuerName") or "").strip(),
                  "insider": insiders[0] if insiders else "",
                  "insiders": insiders,
                  "relationship": ", ".join(labels) or "Insider",
                  "groups": groups, "total_value": sum(g["value"] for g in groups),
                  "transaction_date": groups[0]["date"]}


def parse_form4(body: bytes):
    """A Form 4's open-market purchases (code P), or None when it has none -
    or cannot be read (``parse_form4_status`` tells the two apart)."""
    status, detail = parse_form4_status(body)
    return detail if status == "ok" else None


def _money(v):
    """``$136.4M``-style; a negative keeps its sign, a non-number reads $0.

    Each unit is chosen on the ROUNDED figure, so a value that rounds up to
    1000 of one unit reads as 1 of the next (``$1.0M``, never ``$1000K``)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "$0"
    if not math.isfinite(v):
        return "$0"
    sign, a = ("-" if v < 0 else ""), abs(v)
    if round(a) == 0:
        return "$0"
    for scale, suffix, fmt in ((1, "", ".0f"), (1e3, "K", ".0f"), (1e6, "M", ".1f")):
        text = format(a / scale, fmt)
        if float(text) < 1000:
            return f"{sign}${text}{suffix}"
    return f"{sign}${a / 1e9:.1f}B"


def _value(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def form4_item(detail: dict, feed: dict, index_url: str, now: str, *, universe):
    """The item for one Form 4 purchase, or ``None`` when it has no url or is
    neither on a tracked ticker nor at least ``min_value_usd``."""
    if not str(index_url or "").strip():
        return None
    detail = detail or {}
    sym = _symbol(detail.get("symbol"))
    total = _value(detail.get("total_value"))
    floor = _value(feed.get("min_value_usd"))
    if (not sym or sym not in set(universe or ())) and total < floor:
        return None
    who = str(detail.get("insider") or "").strip() or "Insider"
    # Only a detail that CARRIES ``insiders`` gets the "+N" - one without it
    # (an older stored item) renders exactly as before.
    others = detail.get("insiders")
    if isinstance(others, (list, tuple)):
        names = []
        for n in others:
            n = str(n).strip() if n is not None else ""
            if n and n not in names:
                names.append(n)
        extra = len([n for n in names if n != who])
        if extra:
            who = f"{who} +{extra}"
    rel = detail.get("relationship") or "Insider"
    title = f"{sym or detail.get('company') or 'Unknown'} — {who} ({rel}) bought {_money(total)}"
    groups = detail.get("groups")
    if isinstance(groups, list) and len(groups) > MAX_GROUPS:
        detail = dict(detail, groups=groups[:MAX_GROUPS], groups_more=len(groups) - MAX_GROUPS)
    return items.make_item(
        source=feed.get("name", "SEC"), kind="edgar_form4", public=feed.get("public", False),
        title=title, teaser="Form 4 insider purchase filed with the SEC",
        url=index_url, published_at=now, now=now, tickers=[sym] if sym else [],
        topics=["SEC Filing", "Insider Transaction"], detail=detail)


def cik_map(company_tickers) -> dict:
    """``{cik (no leading zeros): TICKER}`` from the SEC's company_tickers.json."""
    out = {}
    for v in (company_tickers or {}).values() if isinstance(company_tickers, dict) else ():
        if (not isinstance(v, dict) or not v.get("ticker")
                or isinstance(v.get("cik_str"), bool)):
            continue
        try:
            out[str(int(v["cik_str"]))] = str(v["ticker"]).upper()
        except (KeyError, TypeError, ValueError):
            continue
    return out


def filing_item(entry: dict, feed: dict, now: str, *, cik_to_ticker):
    """The item for one registration filing, or ``None`` without a url."""
    entry = entry or {}
    url = str(entry.get("index_url") or "").strip()
    if not url:
        return None
    form = entry.get("form") or "filing"
    sym = (cik_to_ticker or {}).get(entry.get("cik") or "")
    title = (f"{entry.get('company') or 'Unknown company'} files {form} "
             f"({form_label(form)})")
    return items.make_item(
        source=feed.get("name", "SEC"), kind="edgar_filings", public=feed.get("public", False),
        title=title, teaser="", url=url,
        published_at=_utc(entry.get("updated"), now), now=now,
        tickers=[sym] if sym else [], topics=["SEC Filing", "Offering"],
        detail={"form": form, "cik": entry.get("cik") or "",
                "accession": entry.get("accession") or ""})
