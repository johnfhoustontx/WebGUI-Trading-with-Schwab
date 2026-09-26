"""FRED: observations (fredgraph.csv or the official API) and release times
(the release-calendar HTML), plus redaction of the API key.

Pure over bytes - no network. Every parser returns observations ASCENDING by
date as ``{"obs_date": "YYYY-MM-DD", "value": float | None}``; FRED's missing
value (``"."``, or the empty cell fredgraph.csv serves today) and anything
non-finite is ``None``, never 0.

Failure contract: ``parse_csv`` / ``parse_api_observations`` /
``parse_api_release_dates`` raise ``ValueError`` - and only ``ValueError`` -
when the body is not the document asked for (a CSV for another series, an
error envelope, a blocked page): that is a failed fetch, not data, and the
caller keeps its last good result. One bad row is skipped, never the batch.
``parse_calendar_html`` and ``redact`` never raise.

⚠ The API key is a QUERY PARAMETER, so it is in every API URL, and both
``http_fetch`` and ``requests`` echo URLs into exception text. Anything built
from such text (a source error, a degrade detail, a log line, a published
status) goes through ``redact`` first.

⚠ Redacting the MESSAGE is not enough: ``http_fetch`` raises
``FetchError(...) from exc``, so ``__cause__`` still holds the original
exception - unredacted URL and all - and any log call with ``exc_info``
(``_degrade.degraded`` logs one) prints the whole chain. The caller rule, for
any exception raised while a key-bearing URL was in play:

* re-raise it as ``raise fred.safe_error(exc, key) from None``, or
* log it with ``exc_info=False`` and ``detail=fred.redact(exc, key)``.

Never log or re-raise the original exception object itself.
"""
import csv
import html
import io
import json
import math
import re
from datetime import date, datetime, timezone
from urllib.parse import quote, quote_plus, urlencode
from zoneinfo import ZoneInfo

FREDGRAPH = "https://fred.stlouisfed.org/graph/fredgraph.csv"
CALENDAR = "https://fred.stlouisfed.org/releases/calendar"
# The calendar page never states its zone; it renders CENTRAL (7:30 am there
# is BLS's 08:30 US-Eastern for the same releases - see the research doc).
CALENDAR_TZ = ZoneInfo("America/Chicago")
MASK = "***"

# fredgraph.csv's first header: ``observation_date`` today, ``DATE`` before.
_DATE_COLS = ("observation_date", "DATE")
_DAY = re.compile(r'<span style="font-weight:\s*bold;?">([^<]*)</span>', re.I)
_ROW = re.compile(
    r'<td nowrap[^<>]*>(?P<time>[^<]*)</td>\s*<td[^<>]*>\s*'
    r'<a href="/release\?rid=(?P<rid>\d+)">(?P<name>[^<]*)</a>', re.I)
_TOKEN = re.compile(_DAY.pattern + "|" + _ROW.pattern, re.I)
_TIME = re.compile(r"^(\d{1,2}):(\d{2})\s*([ap])\.?\s*m\.?$", re.I)
# Any api_key query value, whatever key the caller passed (or none).
_KEY_PARAM = re.compile(r"(api_key=)[^&\s#'\"<>]*", re.I)


# ---- values ------------------------------------------------------------

def _value(raw):
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if raw in ("", "."):
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _iso_date(raw):
    try:
        return date.fromisoformat(raw.strip()).isoformat()
    except (AttributeError, TypeError, ValueError):
        return None


def _ascending(pairs):
    """``(date, raw)`` pairs -> ascending observations, one per date (the
    last seen wins), rows with no usable date dropped."""
    by_date = {}
    for d, raw in pairs:
        iso = _iso_date(d)
        if iso is not None:
            by_date[iso] = _value(raw)
    return [{"obs_date": d, "value": by_date[d]} for d in sorted(by_date)]


def _text(body):
    if not isinstance(body, (bytes, bytearray)):
        raise ValueError("FRED body is not bytes")
    try:
        return bytes(body).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("FRED body is not UTF-8") from exc


def parse_csv(body, series):
    """fredgraph.csv (``observation_date,<SERIES>``) -> ascending observations.
    The second header must BE ``series``: a body for another series is a
    failure, not data."""
    rows = csv.reader(io.StringIO(_text(body)))
    try:
        header = next(rows, None)
        if not header or len(header) < 2 or header[0].strip() not in _DATE_COLS \
                or header[1].strip() != str(series):
            raise ValueError(f"fredgraph.csv is not series {series!r}")
        return _ascending((r[0], r[1]) for r in rows if len(r) >= 2)
    except csv.Error as exc:            # e.g. an unterminated quote past the field limit
        raise ValueError(f"fredgraph.csv is malformed: {exc}") from None


def _json(body, key):
    try:
        doc = json.loads(_text(body))
    except json.JSONDecodeError as exc:
        raise ValueError("FRED body is not JSON") from exc
    except RecursionError:              # b"[" * 100000 nests past the stack
        raise ValueError("FRED body nests too deeply") from None
    if not isinstance(doc, dict) or "error_code" in doc:
        raise ValueError("FRED returned an error envelope")
    rows = doc.get(key)
    if not isinstance(rows, list):
        raise ValueError(f"FRED body has no {key} list")
    return [r for r in rows if isinstance(r, dict)]


def parse_api_observations(body):
    """``fred/series/observations`` JSON (any sort order) -> ascending."""
    return _ascending((r.get("date"), r.get("value"))
                      for r in _json(body, "observations"))


def parse_api_release_dates(body):
    """``fred/release/dates`` or ``fred/releases/dates`` JSON ->
    ``[{"rid", "date"}]`` sorted by date. Dates only - the API has no time."""
    out = set()
    for r in _json(body, "release_dates"):
        rid, iso = r.get("release_id"), _iso_date(r.get("date"))
        if isinstance(rid, int) and not isinstance(rid, bool) and iso:
            out.add((iso, rid))
    return [{"rid": rid, "date": d} for d, rid in sorted(out)]


# ---- calendar HTML -------------------------------------------------------

def _clock(raw):
    """``"7:30 am"`` -> (7, 30); blank -> ``""`` (repeat the row above);
    ``"N/A"`` or anything unreadable -> None (a date-only row)."""
    raw = html.unescape(raw).strip()
    if not raw:
        return ""
    m = _TIME.match(raw)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (1 <= hh <= 12 and 0 <= mm <= 59):
        return None
    return (hh % 12 + (12 if m.group(3).lower() == "p" else 0), mm)


def parse_calendar_html(body, *, rid):
    """The release-calendar page -> ``[{"id", "rid", "name", "date", "at"}]``
    in page order. ``at`` is an aware UTC ISO instant (the page is Central),
    or None for a row with no time. A blank time cell means "same as the row
    above" - tracked over EVERY row, since the row above is usually another
    release - and resets at each day header. ``rid=None`` keeps every rid."""
    try:
        text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else ""
    except Exception:  # noqa: BLE001 - a parser over foreign bytes never raises
        return []
    out, day, clock = [], None, None
    for m in _TOKEN.finditer(text):
        if m.group(1) is not None:                       # a day header
            try:
                day = datetime.strptime(" ".join(html.unescape(m.group(1)).split()),
                                        "%A %B %d, %Y").date()
            except ValueError:
                day = None
            clock = None
            continue
        if day is None:
            continue
        this = _clock(m.group("time"))
        if this != "":
            clock = this
        try:
            row_rid = int(m.group("rid"))
        except ValueError:
            continue
        if rid is not None and row_rid != rid:
            continue
        at = None
        if clock:
            at = (datetime(day.year, day.month, day.day, clock[0], clock[1],
                           tzinfo=CALENDAR_TZ).astimezone(timezone.utc).isoformat())
        out.append({"id": f"fred:{row_rid}:{day.isoformat()}", "rid": row_rid,
                    "name": " ".join(html.unescape(m.group("name")).split()),
                    "date": day.isoformat(), "at": at})
    return out


# ---- URLs --------------------------------------------------------------

def fredgraph_url(series, start):
    """One series per call - a multi-series ``id=A,B`` ignores ``cosd``."""
    return f"{FREDGRAPH}?{urlencode({'id': series, 'cosd': start})}"


def calendar_url(rid, start, end):
    return (f"{CALENDAR}?{urlencode({'rid': rid, 'y': str(start)[:4], 'view': 'year'})}"
            f"&{urlencode({'vs': start, 've': end})}")


def api_observations_url(base, series, key, limit=15):
    """``limit`` 15 - enough rows for the prior of a y/y figure."""
    q = {"series_id": series, "api_key": key, "file_type": "json",
         "sort_order": "desc", "limit": limit}
    return f"{str(base).rstrip('/')}/series/observations?{urlencode(q)}"


def api_release_dates_url(base, rid, key):
    q = {"release_id": rid, "api_key": key, "file_type": "json",
         "include_release_dates_with_no_data": "true"}
    return f"{str(base).rstrip('/')}/release/dates?{urlencode(q)}"


# ---- redaction -----------------------------------------------------------

def safe_error(exc, key):
    """A NEW exception carrying ``redact(exc, key)`` as its message (and
    ``exc``'s ``status`` when it has one). Its type is ``type(exc)`` when
    ``exc`` is a ``FetchError`` subclass (``TooLarge`` stays ``TooLarge``, so a
    caller's ``except TooLarge`` still means "not an outage"), falling back to
    ``FetchError`` when that type cannot be built this way; anything else
    becomes a plain ``FetchError``.

    It is returned with no ``__cause__`` / ``__context__`` and with
    ``__suppress_context__`` set. ⚠ Raised INSIDE an ``except`` block, Python
    sets ``__context__`` again at raise time (to the exception being handled,
    key and all); it stays out of a printed traceback only because
    ``__suppress_context__`` hides it. So raise it outside the ``except``
    block, or ``raise fred.safe_error(exc, key) from None``. Never raises."""
    from services.news_svc.fetch import FetchError
    status = getattr(exc, "status", None)
    if isinstance(status, bool) or not isinstance(status, int):
        status = None
    message = redact(exc, key)
    cls = type(exc) if isinstance(exc, FetchError) else FetchError
    try:
        safe = cls(message, status=status)
        if not isinstance(safe, FetchError):
            raise TypeError("not a FetchError")
    except Exception:  # noqa: BLE001 - a subclass with another signature: the base
        safe = FetchError(message, status=status)
    safe.__cause__ = None
    safe.__context__ = None
    safe.__suppress_context__ = True
    safe.__traceback__ = None
    return safe


def redact(text, key):
    """``text`` (any object - an exception is fine) as a string with the key,
    every URL-encoded spelling of it, and ANY ``api_key=`` query value
    replaced by ``***``. Never raises."""
    try:
        if isinstance(text, (bytes, bytearray)):
            s = bytes(text).decode("utf-8", "replace")
        else:
            s = str(text)
    except Exception:  # noqa: BLE001 - a __str__ that raises must not leak or raise
        s = "<unprintable>"
    s = _KEY_PARAM.sub(lambda m: m.group(1) + MASK, s)
    if isinstance(key, str) and key:
        forms = {key, quote_plus(key), quote(key), quote(key, safe=""),
                 quote_plus(key, safe="")}
        for form in sorted(forms, key=len, reverse=True):
            s = s.replace(form, MASK)
            s = s.replace(form.lower(), MASK) if form.lower() != form else s
    return s
