"""The economic-calendar cycle: sources -> ``news.db`` -> the three calendar views.

Named ``econ_calendar``, never ``calendar``: a service module named after a
stdlib module shadows it when ``app.py`` runs as a script (the CLAUDE.md
``secrets.py`` incident). ``econ`` holds the pure builders; this module fetches,
stores and publishes. Pure over an injected ``fetch`` so the whole cycle runs
in tests with no network.

Sources, each a ``[calendar.sources.<name>]`` table (``shared.news_config``):

* ``fed``           - the Board's ``calendar.json``                 (1 request)
* ``bls`` / ``bea`` - the agencies' ICS schedules                    (1 each)
* ``fred_calendar`` - FRED's release-calendar HTML, one page per ``release_id``
  a FRED-scheduled indicator names (retail sales, jobless claims: 2)
* ``nasdaq_ipo``    - this month and next                            (2)
* observations      - one request per enabled indicator's series, through
  ``fred_api`` (with ``FRED_API_KEY``) or key-free ``fredgraph`` (10)
* ``dividends``     - ``shared.dividends`` over ``repo_paths.DIVIDENDS_DB``,
  which ``trade_svc`` writes. A LOCAL read: ``news_svc`` never calls the proxy.

Load-bearing rules, each pinned in ``tests/test_econ_calendar.py``:

* **Each source has its own User-Agent** (``calendar_source(name)["user_agent"]``):
  the repo UA gets 200 from every government host and a refusal from Nasdaq; a
  Chrome UA gets the opposite.
* **Each source fails alone.** A fetch or parse failure is one
  ``news.cal.<source>`` degrade and an ``error`` on the source's row; its last
  GOOD parsed result (``cal_sources.payload``) is what the views keep showing,
  and the source reports ``stale``. A source is fetched again once its
  ``refresh_min`` has passed - a failed one after at most ``[calendar]
  refresh_min``, so a 12-hour schedule does not wait 12 hours to recover.
* ⚠ **The FRED key never leaves the fetch.** It is read from the process
  environment at CALL time (``env``), never from config, and it is a query
  parameter - so every exception raised while a key-bearing URL was in play is
  rebuilt by ``fred.safe_error`` (redacted, no ``__cause__``) before anything
  can log or store it, and logged with ``exc_info=False``. Every stored error
  and degrade detail goes through ``fred.redact`` as well, whatever the source.
* **The public view is BUILT, not filtered**: ``econ.build_calendar(...,
  public_symbols=set(collection_base()))`` - the dividends of ``[tickers]
  extras`` (followed but never published elsewhere) reach only the owner view.
* **A missing dividends store is ``never``, not an empty "no dividends"**, and
  it is opened READ-ONLY (``mode=ro``) and only when the file exists: this
  service must not create or migrate a store another service owns.
* **The views carry no timestamp** (``skip_unchanged``); the status view does.
* **One cycle at a time**: ``refresh`` and ``watch`` each hold their own lock
  (a second caller gets ``{"skipped": "busy"}``) and share one publish lock, so
  an older build can never overwrite a newer one.

Budget, per ``refresh`` with every source due: 1 + 1 + 1 + 2 + 2 + 10 = **17
requests**, but on their own cadences - Fed hourly, BLS / BEA / FRED calendar
twice a day, Nasdaq every 4 h, observations every ``values_refresh_min`` (4 h) -
so a steady day is ~24 + 2 + 2 + 4 + 12 + 60 = ~104 requests. The release watch
adds one request per due series every ``release_poll_min`` for at most
``release_watch_min`` (<= 30 per series per release; CPI day is two series).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3
import threading
from pathlib import Path
from urllib.parse import quote

from services import _degrade
from services.news_svc import econ, handlers, store as _store
from services.news_svc.adapters import fed_calendar, fred, ics, nasdaq_ipo
from services.news_svc.fetch import FetchError, http_fetch
from shared import dividends as _div
from shared import news_config as nc
from shared import symbols as _symbols

log = logging.getLogger("news_svc.econ_calendar")

UTC = dt.timezone.utc
CT = econ.CT
KEY_ENV = "FRED_API_KEY"

# How far back each fetch reaches. The FRED calendar must start at least one
# release period back, or ``econ.releases_for`` finds no LAST release for a
# monthly indicator and its tile never reads "released" (nor does the watch
# fire): 45 days covers a month plus a late release.
FRED_CAL_BACK_DAYS = 45
FRED_CAL_AHEAD_DAYS = 120
OBS_BACK_DAYS = 400             # 13+ monthly rows: a prior, and room for a y/y
_SCHEDULE_SOURCES = ("fed", "bls", "bea", "fred_calendar", "nasdaq_ipo")
_FRED_DATES = "fred_api_dates"  # internal row: the key path's release-date fallback
_WATCH_PREFIX = "watch:"        # internal rows: the release watch's last poll per series

_CAL_LOCK = threading.Lock()
_WATCH_LOCK = threading.Lock()
_PUBLISH_LOCK = threading.Lock()


# ── small helpers ─────────────────────────────────────────────────────────────

def _aware(now) -> dt.datetime:
    if isinstance(now, str):
        now = dt.datetime.fromisoformat(now)
    if not isinstance(now, dt.datetime):
        return dt.datetime.now(UTC)
    return now if now.tzinfo else now.replace(tzinfo=CT)


def _iso(when) -> str:
    return when.astimezone(UTC).isoformat()


def _instant(v):
    """An aware datetime from an ISO string (naive = UTC), else None."""
    if not isinstance(v, str) or not v:
        return None
    try:
        when = dt.datetime.fromisoformat(v)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _today_ct(now) -> dt.date:
    return now.astimezone(CT).date()


def _key(env):
    key = (env or {}).get(KEY_ENV) if hasattr(env, "get") else None
    return key.strip() if isinstance(key, str) and key.strip() else None


def _timeout(cfg) -> float:
    col = cfg.get("collector") if isinstance(cfg, dict) else None
    v = col.get("request_timeout_s") if isinstance(col, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0:
        v = nc.DEFAULTS["collector"]["request_timeout_s"]
    return v


def _cal_table(name):
    """``[calendar.<name>]`` merged over its built-in default (a copy)."""
    default = dict(nc.DEFAULTS["calendar"][name])
    raw = nc.load().get("calendar")
    table = raw.get(name) if isinstance(raw, dict) else None
    if isinstance(table, dict):
        default.update(table)
    return default


def _extra_releases():
    v = _cal_table("events").get("extra_releases")
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _fed_types():
    v = _cal_table("fed").get("types")
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _due(st, refresh_min, retry_min, now) -> bool:
    """Never polled, or ``refresh_min`` since the last poll (``retry_min`` when
    that poll failed)."""
    last = _instant(st.get("last_poll")) if isinstance(st, dict) else None
    if last is None:
        return True
    wait = refresh_min if not st.get("error") else min(refresh_min, retry_min)
    return now - last >= dt.timedelta(minutes=wait)


def _fail(db, name, exc, *, key, now_iso, area=None, in_except=True):
    """Record one source's failure - redacted, the last good payload kept. A
    traceback is logged only with no key in play and a live exception."""
    text = fred.redact(exc, key)[:500]
    _degrade.degraded(area or f"news.cal.{name}", detail=text,
                      exc_info=key is None and in_except)
    try:
        db.set_cal_source(name, last_poll=now_iso, error=text)
    except Exception:  # noqa: BLE001 - the degrade above already says the source failed
        log.warning("calendar: could not record %s's failure", name, exc_info=False)


def _fetch(fetch, url, src, timeout, headers=None):
    got = fetch(url, user_agent=src["user_agent"], timeout=timeout, headers=headers or {})
    if getattr(got, "status", 200) != 200 or not getattr(got, "body", b""):
        raise FetchError(f"HTTP {getattr(got, 'status', '?')} with no body", status=None)
    return got.body


def _fred_api(fetch, url, key, src, timeout, parse):
    """Every FRED API call. ANY exception - the fetch's or the parser's - comes
    out as a redacted ``FetchError`` with no cause chain."""
    try:
        return parse(_fetch(fetch, url, src, timeout))
    except Exception as exc:  # noqa: BLE001 - redact everything, whatever it was
        raise fred.safe_error(exc, key) from None


# ── per-source fetch + parse (each raises on failure) ─────────────────────────

def _get_fed(fetch, src, timeout, now):
    events = fed_calendar.parse(_fetch(fetch, src["url"], src, timeout), types=_fed_types())
    if not events:
        raise ValueError("calendar.json parsed to no events")
    return events


def _get_ics(fetch, src, timeout, now):
    events = ics.parse(_fetch(fetch, src["url"], src, timeout))
    if not events:
        raise ValueError("the ICS schedule parsed to no events")
    return events


def _fred_rids(indicators):
    out = []
    for ind in indicators:
        rid = ind.get("release_id")
        if ind.get("schedule") == "fred" and isinstance(rid, int) \
                and not isinstance(rid, bool) and rid not in out:
            out.append(rid)
    return out


def fred_calendar_window(now):
    """``(start, end)`` ISO dates of the FRED calendar fetch - ``start`` one
    release period back, so the LAST release is in the page."""
    today = _today_ct(now)
    return ((today - dt.timedelta(days=FRED_CAL_BACK_DAYS)).isoformat(),
            (today + dt.timedelta(days=FRED_CAL_AHEAD_DAYS)).isoformat())


def _get_fred_calendar(fetch, src, timeout, now, rids):
    start, end = fred_calendar_window(now)
    rows = []
    for rid in rids:
        url = src["url"].format(rid=rid, year=start[:4], start=start, end=end)
        got = fred.parse_calendar_html(_fetch(fetch, url, src, timeout), rid=rid)
        if not got:
            raise ValueError(f"the FRED calendar for rid {rid} has no rows")
        rows.extend(got)
    return rows


def _get_nasdaq(fetch, src, timeout, now):
    today = _today_ct(now)
    nxt = (today.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    headers = {"Accept": src["accept"]} if isinstance(src.get("accept"), str) else {}
    rows = []
    for month in (today, nxt):
        url = src["url"].format(month=f"{month.year:04d}-{month.month:02d}")
        rows.extend(nasdaq_ipo.parse(_fetch(fetch, url, src, timeout, headers)))
    return nasdaq_ipo.dedupe(rows)


# ── the cycle ─────────────────────────────────────────────────────────────────

def _refresh_schedules(db, fetch, *, now, key, timeout, cal, indicators):
    now_iso = _iso(now)
    retry = cal["refresh_min"]
    getters = {
        "fed": _get_fed, "bls": _get_ics, "bea": _get_ics, "nasdaq_ipo": _get_nasdaq,
        "fred_calendar": lambda f, s, t, n: _get_fred_calendar(f, s, t, n, _fred_rids(indicators)),
    }
    for name in _SCHEDULE_SOURCES:
        src = nc.calendar_source(name)
        if not src.get("enabled"):
            continue
        if name == "fred_calendar" and not _fred_rids(indicators):
            continue
        st = db.cal_source(name)
        if not _due(st, src["refresh_min"], retry, now):
            continue
        try:
            payload = getters[name](fetch, src, timeout, now)
            db.set_cal_source(name, payload=payload, last_ok=now_iso, last_poll=now_iso,
                              error=None)
        except Exception as exc:  # noqa: BLE001 - one source fails alone
            _fail(db, name, exc, key=key, now_iso=now_iso)
            if name == "fred_calendar" and key is not None:
                _fred_dates_fallback(db, fetch, key=key, timeout=timeout, now=now,
                                     indicators=indicators, retry=retry)


def _fred_dates_fallback(db, fetch, *, key, timeout, now, indicators, retry):
    """The key path's release DATES, used only while the calendar HTML has no
    good result (the API carries no time: the indicator's ``time_ct`` fills it)."""
    if db.cal_source("fred_calendar").get("payload") is not None:
        return
    src = nc.calendar_source("fred_api")
    st = db.cal_source(_FRED_DATES)
    if not src.get("enabled") or not _due(st, src["refresh_min"], retry, now):
        return
    now_iso = _iso(now)
    rows = []
    try:
        for rid in _fred_rids(indicators):
            url = fred.api_release_dates_url(src["url"], rid, key)
            rows.extend(_fred_api(fetch, url, key, src, timeout, fred.parse_api_release_dates))
    except Exception as exc:  # noqa: BLE001 - already redacted by _fred_api
        _fail(db, _FRED_DATES, exc, key=key, now_iso=now_iso, area="news.cal.fred_api")
        return
    db.set_cal_source(_FRED_DATES, payload=rows, last_ok=now_iso, last_poll=now_iso, error=None)


def _obs_source(key):
    """``(name, source table)`` of the observation path in use."""
    if key is not None:
        src = nc.calendar_source("fred_api")
        if src.get("enabled"):
            return "fred_api", src
    return "fredgraph", nc.calendar_source("fredgraph")


def _series(indicators):
    out = []
    for ind in indicators:
        s = ind.get("series")
        if isinstance(s, str) and s and s not in out:
            out.append(s)
    return out


def _fetch_series(db, fetch, series_list, *, name, src, key, timeout, now):
    """Fetch and store ``series_list``; one failing series does not stop the
    rest. Returns ``(fetched, first_error)``."""
    start = (_today_ct(now) - dt.timedelta(days=OBS_BACK_DAYS)).isoformat()
    now_iso = _iso(now)
    done, error = [], None
    for series in series_list:
        try:
            if name == "fred_api":
                url = fred.api_observations_url(src["url"], series, key)
                rows = _fred_api(fetch, url, key, src, timeout, fred.parse_api_observations)
            else:
                url = src["url"].format(series=quote(series, safe=""), start=start)
                rows = fred.parse_csv(_fetch(fetch, url, src, timeout), series)
            db.upsert_obs(series, rows, now=now_iso)
            done.append(series)
        except Exception as exc:  # noqa: BLE001 - redacted before it goes anywhere
            error = error or exc
    return done, error


def _refresh_obs(db, fetch, *, now, key, timeout, cal, indicators):
    name, src = _obs_source(key)
    if not src.get("enabled"):
        return
    st = db.cal_source(name)
    if not _due(st, cal["values_refresh_min"], cal["refresh_min"], now):
        return
    done, error = _fetch_series(db, fetch, _series(indicators), name=name, src=src,
                                key=key, timeout=timeout, now=now)
    now_iso = _iso(now)
    if error is not None:
        _fail(db, name, error, key=key, now_iso=now_iso, in_except=False)
        if done:
            db.set_cal_source(name, payload={"series": sorted(done)}, last_ok=now_iso)
        return
    db.set_cal_source(name, payload={"series": sorted(done)}, last_ok=now_iso,
                      last_poll=now_iso, error=None)


def _dividend_symbols():
    return [s for s in nc.ticker_set() if not s.startswith("$")]


def _refresh_dividends(db, *, now, cal, path):
    """Read the store ``trade_svc`` writes. Missing -> ``never`` (no payload,
    an error); unreadable -> ``stale`` with the last good rows kept."""
    div_cfg = nc.dividends_config()
    if not div_cfg["enabled"]:
        return
    st = db.cal_source("dividends")
    if not _due(st, cal["refresh_min"], cal["refresh_min"], now):
        return
    now_iso = _iso(now)
    path = Path(path) if path is not None else Path(_div.DEFAULT_DB_PATH)
    if not path.exists():
        db.set_cal_source("dividends", last_poll=now_iso,
                          error="the dividends store does not exist yet (trade_svc writes it)")
        return
    today = _today_ct(now)
    start = today - dt.timedelta(days=div_cfg["lookback_days"])
    end = today + dt.timedelta(days=div_cfg["horizon_days"])
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = _div.upcoming(conn, _dividend_symbols(), start, end)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - that source alone goes stale
        _fail(db, "dividends", exc, key=None, now_iso=now_iso)
        return
    db.set_cal_source("dividends", payload=rows, last_ok=now_iso, last_poll=now_iso, error=None)


# ── build + publish ───────────────────────────────────────────────────────────

def _payload(db, name):
    return db.cal_source(name).get("payload")


def _states(db, *, cal, key):
    obs_name, _ = _obs_source(key)
    out = {}
    for name in econ.SOURCE_NAMES:
        if name == "dividends":
            enabled = nc.dividends_config()["enabled"]
        else:
            enabled = bool(nc.calendar_source(name).get("enabled"))
        if not cal["enabled"]:
            enabled = False
        out[name] = econ.source_state(db.cal_source(name), enabled=enabled)
    return out, obs_name


def _parts(db, *, now, key, cal, indicators):
    states, obs_name = _states(db, cal=cal, key=key)
    cfg = dict(cal)
    cfg["fed"] = _cal_table("fed")
    cfg["ipo"] = _cal_table("ipo")
    fred_rows = _payload(db, "fred_calendar")
    if fred_rows is None and key is not None:
        fred_rows = _payload(db, _FRED_DATES)
    return {
        "now": now, "cfg": cfg, "indicators": indicators,
        "fed": _payload(db, "fed") or [],
        "ics": {"bls": _payload(db, "bls") or [], "bea": _payload(db, "bea") or []},
        "fred_calendar": fred_rows or [],
        "extra_releases": _extra_releases(),
        "obs": {s: db.obs(s) for s in _series(indicators)},
        "ipos": _payload(db, "nasdaq_ipo") or [],
        "dividends": _payload(db, "dividends") or [],
        "sources": states,
        "fred_obs_source": obs_name,
    }


def status_rows(db) -> list:
    """One row per source for the private status view (error text redacted at
    record time)."""
    out = []
    for name in econ.SOURCE_NAMES:
        st = db.cal_source(name)
        out.append({"name": name, "last_ok": st.get("last_ok"),
                    "last_poll": st.get("last_poll"), "error": st.get("error")})
    return out


def _publish(bus, db, *, now, key, cal, indicators):
    with _PUBLISH_LOCK:
        if cal["enabled"]:
            parts = _parts(db, now=now, key=key, cal=cal, indicators=indicators)
        else:
            parts = {"now": now, "cfg": dict(cal), "indicators": [],
                     "sources": {n: "off" for n in econ.SOURCE_NAMES}}
        steps = (
            ("news.cal.publish", handlers.publish_calendar, None),
            ("news.cal.publish_public", handlers.publish_calendar_public,
             lambda: set(_symbols.collection_base())),
        )
        for area, publish, public in steps:
            try:
                symbols = public() if public is not None else None
                publish(bus, econ.build_calendar(parts=parts, public_symbols=symbols))
            except Exception:  # noqa: BLE001 - that view's last copy stays
                _degrade.degraded(area, exc_info=key is None)
        try:
            rows = status_rows(db)
        except Exception:  # noqa: BLE001 - the status is how the page learns the store is sick
            _degrade.degraded("news.cal.status", exc_info=False)
            rows = []
        handlers.publish_calendar_status(bus, rows)


def refresh(bus, db, fetch, *, now, env=None, dividends_db=None) -> dict:
    """Fetch every DUE source, store, build and publish. ``env`` is where
    ``FRED_API_KEY`` is read (default ``os.environ``, at call time).
    ``dividends_db`` overrides ``repo_paths.DIVIDENDS_DB`` (tests)."""
    if not _CAL_LOCK.acquire(blocking=False):
        return {"skipped": "busy"}
    try:
        now = _aware(now)
        key = _key(os.environ if env is None else env)
        cal = nc.calendar_config()
        if not cal["enabled"]:
            _publish(bus, db, now=now, key=key, cal=cal, indicators=[])
            return {"skipped": "disabled"}
        cfg = nc.load()
        timeout = _timeout(cfg)
        indicators = nc.indicators()
        steps = (
            ("news.cal.schedules", lambda: _refresh_schedules(
                db, fetch, now=now, key=key, timeout=timeout, cal=cal, indicators=indicators)),
            ("news.cal.obs", lambda: _refresh_obs(
                db, fetch, now=now, key=key, timeout=timeout, cal=cal, indicators=indicators)),
            ("news.cal.dividends_read", lambda: _refresh_dividends(
                db, now=now, cal=cal, path=dividends_db)),
        )
        for area, step in steps:
            try:
                step()
            except Exception:  # noqa: BLE001 - e.g. the store; the others still run
                _degrade.degraded(area, exc_info=key is None)
        _publish(bus, db, now=now, key=key, cal=cal, indicators=indicators)
        return {"ok": True}
    finally:
        _CAL_LOCK.release()


def watch_states(db, *, now, indicators, cal):
    """``{series: watch state}`` for every enabled indicator (``econ.watch_due``'s input)."""
    parts_sched = {"bls": _payload(db, "bls") or [], "bea": _payload(db, "bea") or [],
                   "fred": _payload(db, "fred_calendar") or _payload(db, _FRED_DATES) or []}
    out = {}
    for ind in indicators:
        series = ind.get("series")
        if not isinstance(series, str) or not series:
            continue
        rel = econ.releases_for(ind, parts_sched, now=now)
        obs = db.obs(series)
        latest = obs[-1] if obs else {}
        st = {"last_release_at": rel["last_release_at"],
              "latest_first_seen": latest.get("first_seen"),
              "latest_bootstrap": bool(latest.get("bootstrap")),
              "last_watch_poll": db.cal_source(_WATCH_PREFIX + series).get("last_poll")}
        # Two indicators on one series: due when either says so.
        if series not in out or econ.watch_due(st, now=now, cfg=cal):
            out[series] = st
    return out


def watch(bus, db, fetch, *, now, env=None) -> dict:
    """The release watch: fetch only the series whose release just passed and
    whose new observation has not landed (``econ.watch_due``), then republish.
    Does nothing - no fetch, no publish - when no series is due."""
    if not _WATCH_LOCK.acquire(blocking=False):
        return {"skipped": "busy"}
    try:
        now = _aware(now)
        key = _key(os.environ if env is None else env)
        cal = nc.calendar_config()
        if not cal["enabled"]:
            return {"skipped": "disabled"}
        indicators = nc.indicators()
        due = [s for s, st in watch_states(db, now=now, indicators=indicators, cal=cal).items()
               if econ.watch_due(st, now=now, cfg=cal)]
        if not due:
            return {"due": []}
        name, src = _obs_source(key)
        if not src.get("enabled"):
            return {"due": []}
        now_iso = _iso(now)
        for series in due:
            db.set_cal_source(_WATCH_PREFIX + series, last_poll=now_iso)
        _, error = _fetch_series(db, fetch, due, name=name, src=src, key=key,
                                 timeout=_timeout(nc.load()), now=now)
        if error is not None:
            _fail(db, name, error, key=key, now_iso=now_iso, in_except=False)
        _publish(bus, db, now=now, key=key, cal=cal, indicators=indicators)
        return {"due": due}
    finally:
        _WATCH_LOCK.release()


def _run(fn, bus, db, fetch):
    fetch = fetch or http_fetch
    now = dt.datetime.now(UTC)
    if db is not None:
        return fn(bus, db, fetch, now=now)
    with _store.Store() as own:
        return fn(bus, own, fetch, now=now)


def refresh_now(bus, db=None, fetch=None) -> dict:
    """What the scheduler and ``news_refresh`` run: ``refresh`` on the real
    clock, the real store and ``http_fetch``."""
    return _run(refresh, bus, db, fetch)


def watch_now(bus, db=None, fetch=None) -> dict:
    """``watch`` on the real clock, store and fetcher."""
    return _run(watch, bus, db, fetch)
