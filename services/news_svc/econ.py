"""Economic-calendar builders - PURE: no I/O, no network, no Redis, no SQLite.

The calendar cycle (``econ_calendar``) fetches and stores; this module turns
what it holds into the published payload that ``webgui/pages/news_view.py``
(``calendar_groups`` / ``indicator_state``) reads::

    {"events":    [{"title", "at", "date"}],
     "dividends": [{"symbol", "ex_date", "pay_date", "amount"}],
     "ipos":      [{"symbol", "company", "date", "price", "price_range", "offer_usd"}],
     "data":      [{"key", "label", "tile", "unit", "next_release_at", "next_date",
                    "last_release_at", "latest", "prior"}],
     "sources":   {name: "ok" | "stale" | "never" | "off"},
     "settings":  {"release_watch_min", "actual_fresh_h"}}

``at`` / ``*_release_at`` / ``first_seen`` are AWARE UTC ISO instants (or
``None``); dates are ``YYYY-MM-DD``. ``latest`` / ``prior`` are
``{"obs_date", "value", "first_seen", "bootstrap"}`` or ``None``, and ``value``
is the DERIVED figure (see ``derive``). These are FACTS only - whether the
latest observation is the last release's Actual is a function of ``now``, so
the page decides it.

There is deliberately NO timestamp in the payload: the view is published with
``skip_unchanged`` and "updated" comes from the ``{key}:ts`` side key. Every
list is ordered deterministically, so an unchanged calendar serialises
byte-identically.

Nothing here raises on junk, and nothing prints a zero it did not read: a
missing, NaN, infinite, bool or string value - or a non-positive base for a
percentage - is ``None``.
"""
from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

from services.news_svc.adapters import nasdaq_ipo
from shared.news_config import DEFAULTS as _NEWS_DEFAULTS
from shared.news_config import TRANSFORMS
from shared.symbols import clean_symbol

UTC = dt.timezone.utc
CT = ZoneInfo("America/Chicago")          # a naive ``now``, and FRED ``time_ct``

STATES = ("ok", "stale", "never", "off")
SOURCE_NAMES = ("fed", "bls", "bea", "dividends", "nasdaq_ipo",
                "fred_calendar", "fred_api", "fredgraph")
# The two FRED observation paths: only one runs, so the other reports "off"
# (otherwise the data group could never grey - news_view._group_note).
FRED_OBS_SOURCES = ("fred_api", "fredgraph")
_SPEECH_KINDS = frozenset({"speech", "testimony"})

_CAL = _NEWS_DEFAULTS["calendar"]


# ---- small readers ------------------------------------------------------------

def _finite(v):
    """A finite real number as float, else ``None`` (bools and strings too)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _table(cfg, key):
    v = cfg.get(key) if isinstance(cfg, dict) else None
    return v if isinstance(v, dict) else {}


def _setting(table, key, default):
    """A finite number >= 0 from ``table``, else ``default``."""
    v = _finite(table.get(key)) if isinstance(table, dict) else None
    if v is None or v < 0:
        return default
    return int(v) if v == int(v) else v


def _aware(now):
    """``now`` as an aware datetime (naive = CENTRAL wall clock), or None."""
    if not isinstance(now, dt.datetime):
        return None
    return now if now.tzinfo else now.replace(tzinfo=CT)


def _instant(v):
    """An aware UTC datetime from an ISO string or a datetime (naive = UTC),
    or ``None``. Anything that cannot be carried into UTC is ``None``."""
    if isinstance(v, dt.datetime):
        when = v
    elif isinstance(v, str) and v.strip():
        try:
            when = dt.datetime.fromisoformat(v.strip())
        except ValueError:
            return None
    else:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    try:
        return when.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _iso(when):
    return when.isoformat() if when is not None else None


def _date(v):
    """``YYYY-MM-DD`` (a timestamp keeps its date part) or ``None``."""
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return dt.date.fromisoformat(v.strip()[:10]).isoformat()
    except ValueError:
        return None


def _today_ct(now):
    try:
        return now.astimezone(CT).date().isoformat()
    except (ValueError, OverflowError):
        return None


def _dicts(v):
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _str(v):
    return v.strip() if isinstance(v, str) else ""


# ---- derived figures ----------------------------------------------------------

def _clean_obs(obs):
    """Ascending, one row per usable ``obs_date`` (the last seen wins)."""
    by_date = {}
    for r in _dicts(obs):
        d = _date(r.get("obs_date"))
        if d is not None:
            by_date[d] = r
    return [by_date[d] for d in sorted(by_date)]


def _figure(rows, i, transform):
    cur = _finite(rows[i].get("value"))
    if cur is None:
        return None
    if transform in ("level_pct", "pct_saar"):
        return cur
    if transform == "level_k":
        return cur / 1000.0
    if transform not in ("pct_mom", "change_k") or i < 1:
        return None
    base = _finite(rows[i - 1].get("value"))
    if base is None:
        return None
    if transform == "change_k":
        return cur - base           # PAYEMS is already in thousands
    if base <= 0:
        return None
    return (cur / base - 1.0) * 100.0


def derive(obs, transform):
    """``(latest, prior)``, each ``{"obs_date", "value"}`` or ``None``.

    ``pct_mom`` - % change on the previous observation; ``change_k`` - the
    difference (payrolls, already in thousands: +162); ``level_k`` - the level
    over 1000 (claims 197000 -> 197.0); ``level_pct`` / ``pct_saar`` - as
    published. ``prior`` is the same figure one observation back. A missing
    reading, or a missing / non-positive base, is ``None`` - never 0."""
    rows = _clean_obs(obs)
    if not rows:
        return None, None
    out = [{"obs_date": _date(rows[i]["obs_date"]), "value": _figure(rows, i, transform)}
           for i in range(max(0, len(rows) - 2), len(rows))]
    return out[-1], (out[0] if len(out) == 2 else None)


# ---- release schedules --------------------------------------------------------

def _event_text(ev):
    for k in ("summary", "title", "name"):
        if isinstance(ev.get(k), str):
            return ev[k]
    return ""


def _time_ct(raw):
    """``"07:30"`` -> (7, 30), else None."""
    if not isinstance(raw, str):
        return None
    hh, sep, mm = raw.strip().partition(":")
    if not sep or not (hh.isdigit() and mm.isdigit()):
        return None
    h, m = int(hh), int(mm)
    return (h, m) if 0 <= h <= 23 and 0 <= m <= 59 else None


def _schedule_events(ind, schedules):
    """``[(instant | None, date)]`` for the indicator's own release."""
    ind = ind if isinstance(ind, dict) else {}
    name = ind.get("schedule")
    evs = _dicts(schedules.get(name)) if isinstance(schedules, dict) and isinstance(name, str) else []
    out = []
    if name == "fred":
        rid = ind.get("release_id")
        if isinstance(rid, bool) or not isinstance(rid, int):
            return []
        clock = _time_ct(ind.get("time_ct"))
        for ev in evs:
            if ev.get("rid") != rid or isinstance(ev.get("rid"), bool):
                continue
            d = _date(ev.get("date"))
            when = _instant(ev.get("at"))
            if when is None and d is not None and clock is not None:
                y, mo, dd = (int(x) for x in d.split("-"))
                when = dt.datetime(y, mo, dd, *clock, tzinfo=CT).astimezone(UTC)
            if d is None and when is not None:
                d = when.astimezone(CT).date().isoformat()
            if d is not None:
                out.append((when, d))
        return out
    match = ind.get("match")
    if not (isinstance(match, str) and match.strip()):
        return []
    want = match.casefold()
    for ev in evs:
        if not _event_text(ev).lstrip().casefold().startswith(want):
            continue
        when, d = _instant(ev.get("at")), _date(ev.get("date"))
        if d is None and when is not None:
            d = when.date().isoformat()
        if d is not None:
            out.append((when, d))
    return out


def releases_for(ind, schedules, *, now):
    """``{"last_release_at", "next_release_at", "next_date"}`` for one indicator.

    ``schedules`` is ``{"bls": [ics events], "bea": [ics events], "fred":
    [fred calendar / API release-date rows]}``. BLS / BEA events match the
    indicator's ``match`` as a case-insensitive PREFIX of the summary ("GDP ("
    takes "GDP (Advance Estimate)", never "GDP by Industry"); FRED rows match
    on ``release_id``, and only a FRED row with a date and no time takes the
    indicator's ``time_ct`` (Central). A date-only event can be the NEXT one
    (``next_date``) but never the last (it has no instant to compare with a
    first-seen stamp). No future event -> all next fields ``None`` - the
    January gap is never guessed."""
    none = {"last_release_at": None, "next_release_at": None, "next_date": None}
    now = _aware(now)
    if now is None:
        return none
    today = _today_ct(now)
    last = nxt = None
    for when, d in _schedule_events(ind, schedules):
        if when is not None and when <= now:
            last = when if last is None or when > last else last
            continue
        if when is None and (today is None or d < today):
            continue
        key = (d, _iso(when) or "")
        if nxt is None or key < nxt[0]:
            nxt = (key, when, d)
    return {"last_release_at": _iso(last),
            "next_release_at": _iso(nxt[1]) if nxt else None,
            "next_date": nxt[2] if nxt else None}


def watch_due(st, *, now, cfg):
    """True when an indicator's series should be fetched NOW by the release watch.

    ``st``: ``{"last_release_at", "latest_first_seen", "latest_bootstrap"?,
    "last_watch_poll"}``. Due from the release instant for ``release_watch_min``
    minutes, at most every ``release_poll_min``, and only until an observation
    first seen at or after the release lands (a ``bootstrap`` one does not
    count: the first fill says nothing about this release)."""
    if not isinstance(st, dict):
        return False
    now = _aware(now)
    release = _instant(st.get("last_release_at"))
    if now is None or release is None or now < release:
        return False
    watch = _setting(cfg, "release_watch_min", _CAL["release_watch_min"])
    poll = _setting(cfg, "release_poll_min", _CAL["release_poll_min"])
    if now - release > dt.timedelta(minutes=watch):
        return False
    seen = _instant(st.get("latest_first_seen"))
    if seen is not None and seen >= release and st.get("latest_bootstrap") not in (True, 1):
        return False
    last_poll = _instant(st.get("last_watch_poll"))
    if last_poll is not None and now - last_poll < dt.timedelta(minutes=poll):
        return False
    return True


# ---- groups -------------------------------------------------------------------

def _within(when, d, now, horizon_days):
    """Not in the past, and inside ``horizon_days``."""
    end = now + dt.timedelta(days=horizon_days)
    if when is not None:
        return now <= when <= end
    today, last_day = _today_ct(now), _today_ct(end)
    return today is not None and last_day is not None and today <= d <= last_day


def _event_row(title, when, d):
    return {"title": title, "at": _iso(when), "date": d}


def events_group(*, fed, ics, extra_releases, now, cfg):
    """Fed events plus the ``extra_releases`` from the BLS / BEA schedules.

    Fed speeches and testimony look ``speech_horizon_days`` ahead, everything
    else ``horizon_days``; an extra release looks ``horizon_days`` ahead. A
    tracked indicator's release is NOT an event here (it is a data tile) - only
    a summary starting with an ``extra_releases`` name joins. Ordered by date,
    then instant, then title; exact duplicates collapse."""
    now = _aware(now)
    if now is None:
        return []
    fed_cfg = _table(cfg, "fed")
    horizon = _setting(fed_cfg, "horizon_days", _CAL["fed"]["horizon_days"])
    speech = _setting(fed_cfg, "speech_horizon_days", _CAL["fed"]["speech_horizon_days"])
    rows = {}
    for ev in _dicts(fed):
        title, d, when = _str(ev.get("title")), _date(ev.get("date")), _instant(ev.get("at"))
        if not title or d is None:
            continue
        days = speech if ev.get("kind") in _SPEECH_KINDS else horizon
        if _within(when, d, now, days):
            row = _event_row(title, when, d)
            rows[(d, row["at"] or "", title)] = row
    extras = [x.strip().casefold() for x in extra_releases
              if isinstance(x, str) and x.strip()] if isinstance(extra_releases, list) else []
    for name in ("bls", "bea"):
        evs = _dicts(ics.get(name)) if isinstance(ics, dict) else []
        for ev in evs:
            text = _event_text(ev).strip()
            if not text or not any(text.casefold().startswith(x) for x in extras):
                continue
            when, d = _instant(ev.get("at")), _date(ev.get("date"))
            if d is None:
                continue
            if _within(when, d, now, horizon):
                row = _event_row(text, when, d)
                rows[(d, row["at"] or "", text)] = row
    return [rows[k] for k in sorted(rows)]


def _price(raw):
    """A positive finite float from Nasdaq's price text, else ``None``."""
    if not isinstance(raw, str):
        return None
    try:
        v = float(raw.strip().lstrip("$").replace(",", ""))
    except ValueError:
        return None
    return v if math.isfinite(v) and v > 0 else None


def ipos_group(rows, *, now, cfg):
    """Nasdaq rows (one or more months' pages) -> the IPO list.

    Merged through ``nasdaq_ipo.dedupe`` (one row per deal, priced wins).
    Dropped: an offer below ``min_offer_usd`` or of unknown size, and a deal
    dated more than ``lookback_days`` before today (Central). A priced deal
    carries ``price`` (float); an upcoming one ``price_range`` (display text)."""
    now = _aware(now)
    if now is None:
        return []
    ipo_cfg = _table(cfg, "ipo")
    min_offer = _setting(ipo_cfg, "min_offer_usd", _CAL["ipo"]["min_offer_usd"])
    lookback = _setting(ipo_cfg, "lookback_days", _CAL["ipo"]["lookback_days"])
    floor = _today_ct(now - dt.timedelta(days=lookback))
    if floor is None:
        return []
    out = []
    for r in nasdaq_ipo.dedupe(_dicts(rows)):
        d = _date(r.get("date"))
        offer = r.get("offer_usd")
        if d is None or d < floor:
            continue
        if isinstance(offer, bool) or not isinstance(offer, int) or offer <= 0 \
                or offer < min_offer:
            continue
        priced = r.get("status") == "priced"
        sym = clean_symbol(r["symbol"]) if isinstance(r.get("symbol"), str) else None
        text = _str(r.get("price"))
        out.append(((d, str(r.get("id") or "")), {
            "symbol": sym, "company": _str(r.get("company")), "date": d,
            "price": _price(text) if priced else None,
            "price_range": "" if priced else text,
            "offer_usd": offer}))
    return [row for _, row in sorted(out, key=lambda kv: kv[0])]


def _amount(v):
    v = _finite(v)
    return v if v is not None and v > 0 else None


def dividends_group(rows, *, public_symbols):
    """Store rows -> ``{"symbol", "ex_date", "pay_date", "amount"}`` by ex-date,
    then symbol. ``amount`` is a positive per-payment float or ``None`` (a 0 is
    an unknown, not a zero dividend). ``public_symbols`` (a set) keeps only
    those symbols; ``None`` keeps all."""
    allowed = None
    if public_symbols is not None:
        allowed = {clean_symbol(s) for s in public_symbols if isinstance(s, str)} - {None}
    out = {}
    for r in _dicts(rows):
        sym = clean_symbol(r["symbol"]) if isinstance(r.get("symbol"), str) else None
        ex = _date(r.get("ex_date"))
        if not sym or ex is None or (allowed is not None and sym not in allowed):
            continue
        out[(ex, sym)] = {"symbol": sym, "ex_date": ex, "pay_date": _date(r.get("pay_date")),
                          "amount": _amount(r.get("amount"))}
    return [out[k] for k in sorted(out)]


# ---- sources ------------------------------------------------------------------

def source_state(st, *, enabled):
    """One source's state word from its stored row (``store.cal_source``):
    ``off`` when disabled; ``never`` with no good result; ``stale`` when the
    last fetch failed (an ``error``, or a poll after the last success); else
    ``ok``."""
    if not enabled:
        return "off"
    if not isinstance(st, dict) or st.get("payload") is None:
        return "never"
    last_ok = _instant(st.get("last_ok"))
    if last_ok is None:
        return "never"
    if st.get("error"):
        return "stale"
    last_poll = _instant(st.get("last_poll"))
    if last_poll is not None and last_poll > last_ok:
        return "stale"
    return "ok"


def _sources(raw, fred_obs):
    raw = raw if isinstance(raw, dict) else {}
    out = {n: raw[n] if raw.get(n) in STATES else "never" for n in SOURCE_NAMES}
    if fred_obs in FRED_OBS_SOURCES:
        for n in FRED_OBS_SOURCES:
            if n != fred_obs:
                out[n] = "off"
    return out


# ---- the payload --------------------------------------------------------------

def _obs_fact(raw_by_date, derived):
    if derived is None:
        return None
    raw = raw_by_date.get(derived["obs_date"], {})
    boot = raw.get("bootstrap")
    return {"obs_date": derived["obs_date"], "value": derived["value"],
            "first_seen": _iso(_instant(raw.get("first_seen"))),
            "bootstrap": boot is True or (type(boot) is int and boot == 1)}


def _data_entry(ind, schedules, obs_map, now):
    key, series = _str(ind.get("key")), ind.get("series")
    transform = ind.get("transform") if ind.get("transform") in TRANSFORMS else ""
    label = _str(ind.get("label")) or key
    rows = _clean_obs(obs_map.get(series)) if isinstance(series, str) else []
    latest, prior = derive(rows, transform)
    by_date = {_date(r.get("obs_date")): r for r in rows}
    rel = (releases_for(ind, schedules, now=now) if now is not None else
           {"last_release_at": None, "next_release_at": None, "next_date": None})
    return {"key": key, "label": label, "tile": _str(ind.get("tile")) or label,
            "unit": transform, "next_release_at": rel["next_release_at"],
            "next_date": rel["next_date"], "last_release_at": rel["last_release_at"],
            "latest": _obs_fact(by_date, latest), "prior": _obs_fact(by_date, prior)}


def build_calendar(*, parts, public_symbols):
    """The whole calendar payload (module docstring) from what the cycle holds.

    ``parts``: ``now`` (aware; naive = Central), ``cfg`` (``[calendar]``
    scalars plus its ``fed`` / ``ipo`` tables), ``indicators`` (the enabled
    list, in tile order), ``fed`` (Fed adapter events), ``ics`` (``{"bls",
    "bea"}`` ICS events), ``fred_calendar`` (FRED release rows),
    ``extra_releases``, ``obs`` (``{series: store rows}``), ``ipos`` (Nasdaq
    rows, any number of months), ``dividends`` (store rows), ``sources``
    (``{name: state}``) and ``fred_obs_source`` (``"fred_api"`` or
    ``"fredgraph"`` - the other reports ``off``). Any part missing or junk is
    empty. Without a usable ``now`` nothing time-dependent is built.

    ``public_symbols`` (a set) cuts ONLY the dividends; everything else is
    identical between the private and public views."""
    parts = parts if isinstance(parts, dict) else {}
    now = _aware(parts.get("now"))
    cfg = parts.get("cfg") if isinstance(parts.get("cfg"), dict) else {}
    ics = parts.get("ics") if isinstance(parts.get("ics"), dict) else {}
    schedules = {"bls": ics.get("bls"), "bea": ics.get("bea"),
                 "fred": parts.get("fred_calendar")}
    obs_map = parts.get("obs") if isinstance(parts.get("obs"), dict) else {}
    return {
        "events": events_group(fed=parts.get("fed"), ics=ics,
                               extra_releases=parts.get("extra_releases"), now=now, cfg=cfg),
        "dividends": dividends_group(parts.get("dividends"), public_symbols=public_symbols),
        "ipos": ipos_group(parts.get("ipos"), now=now, cfg=cfg),
        "data": [_data_entry(ind, schedules, obs_map, now)
                 for ind in _dicts(parts.get("indicators"))],
        "sources": _sources(parts.get("sources"), parts.get("fred_obs_source")),
        "settings": {
            "release_watch_min": _setting(cfg, "release_watch_min", _CAL["release_watch_min"]),
            "actual_fresh_h": _setting(cfg, "actual_fresh_h", _CAL["actual_fresh_h"]),
        },
    }
