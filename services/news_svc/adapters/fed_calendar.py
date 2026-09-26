"""Federal Reserve Board calendar: ``federalreserve.gov/json/calendar.json``.

Pure over bytes - no network, and ``parse`` never raises. Shape of a row::

    {"month": "2026-10", "days": "1, 8", "time": "3:30 p.m.", "type": "Speeches",
     "title": "Discussion - Governor Lisa D. Cook ", "location": "...",
     "description": "&lt;p&gt;...&lt;/p&gt;", "link"/"live": "https://..."}

Traps, each handled here: the body starts with a UTF-8 BOM (``json.loads``
refuses it); ``days`` is a comma list, one event per day; ``time`` may be
``""`` (a date-only event); titles carry trailing spaces; titles, locations
and descriptions carry HTML entities, and descriptions are escaped HTML. Times
are the Board's convention, **Eastern**; ``at`` is stored as an aware UTC ISO
instant, ``date`` as the Eastern calendar date. One bad row is skipped, never
the batch.

A ``time`` that is not an ``h:mm a.m./p.m.`` clock ("noon", "14:00", "TBA")
keeps the row as a DATE-ONLY event (``at`` None, id suffix ``:day``) with a
DEBUG line - the date is still true; a range ("2:00 p.m. - 3:00 p.m.") takes
its start. A clock-shaped time out of range ("25:99 p.m.") is a corrupt row
and is skipped. ``link`` is kept only when it is an ``https://`` URL after
stripping whitespace (the live file has ``live`` values with a leading
space); ``http://``, relative and scheme-less links are dropped DELIBERATELY,
since the page opens them as-is.

Every output string (``title``, ``location``, ``description``, ``link``) is
PLAIN TEXT with entities already unescaped and tags removed - which means it
may now contain a literal ``<`` or ``&``. Render it escaped (``ui.label``,
never ``ui.html``).
"""
import html
import json
import logging
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

URL = "https://www.federalreserve.gov/json/calendar.json"
EASTERN = ZoneInfo("America/New_York")

_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*([ap])\.?\s*m\.?\s*$", re.I)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_SLUG = re.compile(r"[^a-z0-9]+")

# FOMC rows by exact (normalized) title -> (kind, short display label).
_FOMC = {
    "fomc meeting": ("fomc_statement", "FOMC statement"),
    "fomc press conference": ("fomc_press", "Press conference"),
    "fomc minutes": ("fomc_minutes", "FOMC minutes"),
}
_KIND_BY_TYPE = {"beige": "beige", "speeches": "speech", "testimony": "testimony"}


def _text(value) -> str:
    """Unescape entities (twice: descriptions are escaped HTML), drop tags, squeeze space."""
    if not isinstance(value, str):
        return ""
    s = html.unescape(html.unescape(value))
    s = _TAG.sub(" ", s)
    return _SPACE.sub(" ", s).strip()


def _slug(title: str) -> str:
    return _SLUG.sub("-", title.lower()).strip("-") or "event"


def _clock(raw):
    """"2:00 p.m." -> (14, 0); "" -> None (date-only).

    A range takes its start: the text before the first hyphen is tried first,
    then the whole text. A time that is no clock at all ("noon", "14:00") is
    ``None`` too - date-only, with a DEBUG line - because the date is still
    right. A clock-SHAPED time out of range ("25:99 p.m.") raises ValueError:
    that row is corrupt, and the caller skips it."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    m = None
    if isinstance(raw, str):
        head = raw.split("-", 1)[0]
        m = _TIME.match(head) or _TIME.match(raw)
    if not m:
        log.debug("fed_calendar: unreadable time %r - kept as date-only", raw)
        return None
    hour, minute, half = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if not (1 <= hour <= 12 and 0 <= minute <= 59):
        raise ValueError(f"unreadable time {raw!r}")
    hour = hour % 12 + (12 if half == "p" else 0)
    return hour, minute


def _kind_and_title(rtype: str, title: str):
    if rtype.lower() == "fomc":
        return _FOMC.get(title.lower(), ("fomc", title))
    if rtype.lower() == "beige":
        return "beige", "Beige Book"
    return _KIND_BY_TYPE.get(rtype.lower(), _slug(rtype).replace("-", "_")), title


def _link(row) -> str:
    for key in ("link", "live"):
        v = row.get(key)
        v = v.strip() if isinstance(v, str) else ""
        if v.startswith("https://"):
            return v
    return ""


def _row_events(row, wanted):
    if not isinstance(row, dict):
        return []
    rtype = row.get("type")
    if not isinstance(rtype, str) or rtype.strip().lower() not in wanted:
        return []
    title = _text(row.get("title"))
    month = row.get("month")
    days = row.get("days")
    if not title or not isinstance(month, str) or not isinstance(days, str):
        return []
    year, mon = (int(p) for p in month.split("-"))       # ValueError -> row skipped
    clock = _clock(row.get("time"))
    kind, label = _kind_and_title(rtype.strip(), title)
    base = {
        "kind": kind,
        "type": rtype.strip(),
        "title": label,
        "location": _text(row.get("location")),
        "description": _text(row.get("description")),
        "link": _link(row),
        "source": "fed",
    }
    out = []
    for part in days.split(","):
        try:
            d = date(year, mon, int(part.strip()))
        except (TypeError, ValueError):
            log.debug("fed_calendar: skipped day %r of %s %s", part, month, title)
            continue
        if clock is None:
            at, hhmm = None, "day"
        else:
            local = datetime(d.year, d.month, d.day, clock[0], clock[1], tzinfo=EASTERN)
            at = local.astimezone(timezone.utc).isoformat()
            hhmm = f"{clock[0]:02d}:{clock[1]:02d}"
        out.append({**base, "id": f"fed:{d.isoformat()}:{_slug(title)}:{hhmm}",
                    "date": d.isoformat(), "at": at})
    return out


def parse(body, *, types) -> list[dict]:
    """``calendar.json`` bytes -> events of the wanted ``types`` (the file's ``type`` values).

    Each event: ``id`` (stable), ``kind`` (fomc_statement / fomc_press /
    fomc_minutes / beige / speech / testimony / the lower-cased type),
    ``type``, ``title``, ``date`` (Eastern ``YYYY-MM-DD``), ``at`` (UTC ISO or
    ``None`` for a date-only row), ``location``, ``description``, ``link``,
    ``source``. Never raises; junk yields ``[]``. ``types`` given as one str is
    one type. Every string field is plain text: render it escaped, never
    through ``ui.html``.
    """
    try:
        if isinstance(types, str):
            types = (types,)                             # one type, never its letters
        wanted = {t.strip().lower() for t in (types or ()) if isinstance(t, str)}
        wanted.discard("")
        if not wanted or not isinstance(body, (bytes, bytearray, str)):
            return []
        text = body.decode("utf-8-sig") if isinstance(body, (bytes, bytearray)) else body.lstrip("﻿")
        doc = json.loads(text)
        rows = doc.get("events") if isinstance(doc, dict) else None
        if not isinstance(rows, list):
            return []
    except Exception:                                    # bad bytes / bad JSON / bad types
        log.debug("fed_calendar: unreadable body", exc_info=True)
        return []
    events, seen = [], set()
    for row in rows:
        try:
            for ev in _row_events(row, wanted):
                if ev["id"] not in seen:
                    seen.add(ev["id"])
                    events.append(ev)
        except Exception:                                # one bad row, never the batch
            log.debug("fed_calendar: skipped row %r", row, exc_info=True)
    return events
