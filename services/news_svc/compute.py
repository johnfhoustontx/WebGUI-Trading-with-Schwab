"""The poll cycle: feeds -> adapters -> store -> impact -> the five views. Pure
over an injected ``fetch`` so the whole thing runs in tests with no network.

The five views: ``feed`` / ``feed_public`` (headlines - every kind BUT the SEC
ones), ``sec`` / ``sec_public`` (``edgar_form4`` / ``edgar_filings`` only, their
own ``[collector] sec_view_items`` window, so a busy 424B5 day cannot eat the
headline window) and ``status``. The split is made HERE, by the store's kind
filters, never page-side.

Load-bearing rules, each pinned in ``tests/test_compute.py``:

* **One feed never stops the cycle.** ``poll_feed`` never raises; a failure is
  a ``_degrade`` count, an ``error`` on the feed's state, and the feed's last
  items stay in the store.
* **Nothing is remembered before it is stored.** A feed's etag / last-modified
  and the EDGAR accessions it saw are written only AFTER ``insert_many``
  succeeds - saved first, a failed insert would turn the next poll into a 304 or
  a skip and the batch would be lost for good.
* **The public view re-reads the CURRENT flags at every publish**
  (``news_config.public_feed_names`` -> ``store.newest(public_sources=...)``),
  so switching a feed private hides what it already published on the next poll.
* **User-Agents.** The SEC gets ``sec_user_agent`` (it requires a contact) and
  requests at most every ``_SEC_MIN_GAP_S``; every other feed gets
  ``feed_user_agent`` (Yahoo answers a 404 page to a non-browser agent).
* **One poll at a time** (``_POLL_LOCK``): the scheduler and a ``news_refresh``
  command never overlap; the second caller returns ``{"skipped": "busy"}``.
* **An SEC refusal is an outage, not an answer.** A network failure, a missed
  fetch deadline, a 403 (how the SEC blocks or rate-limits), a 429 or a 5xx
  stops the EDGAR loop and leaves that accession and every later one unseen for
  the next poll. Any other status - and a body over the size cap - is that
  filing's own answer: logged once, marked seen, skipped.
* **A partial poll is not a healthy one.** Whatever was built is stored, but a
  poll that stopped early (an outage, a later form's Atom failing) or in which
  more than half the accessions it read failed records an ``error`` on the feed
  and one degrade.
* **Accessions are remembered PER FEED**, and only those of a form the feed
  asked for, so a second feed on the same Atom - or this feed with a wider
  ``forms`` list - still reads them.
* **Validators belong to a URL.** A feed whose url (or Google query) changed
  sends no ETag / Last-Modified on its next fetch.
* **Impact is scored after the prune, stored uncapped, capped at publish.**
  ``_rescore`` scores every row whose ``impact_ver`` is not the current
  ``impact.fingerprint(config, ticker set)`` (the store's race guard decides
  which scores land). A scoring failure is ONE ``news.impact`` degrade and the
  views still publish, each row carrying whatever impact it had stored. The
  private views cap the STORED band (``cap_stale``: a HIGH older than
  ``stale_after_h`` goes out as MED with ``stale`` appended); the PUBLIC views
  never carry the stored score - it counts private feeds and tickers - so each
  public row is re-scored from the row as the store returns it (``sources`` and
  ``tickers`` already cut to the public ones) and then capped. A private feed's
  name therefore never reaches a public ``source:`` reason.
* **A store failure costs its own step.** A failed prune or view read is a
  degrade; the other views - and always the status view - still publish. If
  even the feed states cannot be read, the status is built from the config
  alone (every row's ``error`` "store unreadable") and one degrade is counted.
"""
import concurrent.futures
import datetime as dt
import functools
import json
import logging
import threading
import time

from services import _degrade
from services.news_svc import handlers, impact, store as _store
from services.news_svc.adapters import edgar, google_news, rss, yahoo_ticker
from services.news_svc.fetch import (  # noqa: F401 (re-exported for tests)
    FetchError, Fetched, TooLarge, http_fetch)
from shared import news_config as nc

log = logging.getLogger("news_svc.compute")

_SEC_MIN_GAP_S = 0.15          # the SEC allows <= 10 req/s; this is ~6.7/s
_YAHOO_WORKERS = 4             # one Yahoo URL per symbol, fetched this many at once
_CIK_TTL_S = 86400
_WARN_LIST = 10                # symbols named in the one per-feed WARNING

_POLL_LOCK = threading.Lock()
_cik_cache = {"ts": 0.0, "map": {}}
_sec_pace = {"last": 0.0}
_sec_lock = threading.Lock()

_EDGAR_KINDS = ("edgar_form4", "edgar_filings")


class FeedConfigError(Exception):
    """The feed's configuration cannot be polled; nothing was fetched."""


class _Poison(Exception):
    """One EDGAR accession whose documents cannot be read. It is marked seen,
    so it is not fetched again every poll."""


class _Partial(Exception):
    """The feed stored what it could, but the poll did not complete. Raised
    AFTER the store writes, so ``poll_feed``'s one error path records it."""

    def __init__(self, message, inserted):
        super().__init__(message)
        self.inserted = inserted


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _collector(cfg, key):
    col = (cfg or {}).get("collector") or {}
    return col.get(key, nc.DEFAULTS["collector"][key])


def _cutoff(now, keep_days) -> str:
    return (dt.datetime.fromisoformat(now) - dt.timedelta(days=keep_days)).isoformat()


# ── SEC ──────────────────────────────────────────────────────────────────────

def _sec_get(fetch, url, ua, timeout):
    """One SEC request, never sooner than ``_SEC_MIN_GAP_S`` after the last."""
    with _sec_lock:
        wait = _SEC_MIN_GAP_S - (time.monotonic() - _sec_pace["last"])
        if wait > 0:
            time.sleep(wait)
        try:
            return fetch(url, user_agent=ua, timeout=timeout)
        finally:
            _sec_pace["last"] = time.monotonic()


def _transient(exc) -> bool:
    """The SEC being unavailable to us: a network failure or missed deadline (no
    status), a 403 (the SEC's block / rate-limit answer), a 429 or a 5xx. Any
    other HTTP status - and a body over the size cap, which a retry would only
    fetch again - is this accession's own answer."""
    if isinstance(exc, TooLarge):
        return False
    status = getattr(exc, "status", None)
    return status is None or status >= 500 or status in (403, 429)


def _cik_to_ticker(fetch, ua, timeout) -> dict:
    """``{cik: TICKER}``, refreshed once a day. A failed or empty refresh keeps
    serving the last good map (a CIK's ticker rarely changes in a day) and is
    not cached, so the next poll tries again; with no map at all, ``{}`` -
    filings are then stored without tickers, never an aborted feed."""
    stale = _cik_cache["map"]
    if stale and time.time() - _cik_cache["ts"] < _CIK_TTL_S:
        return stale
    try:
        got = _sec_get(fetch, edgar.TICKERS_JSON, ua, timeout)
        mapping = edgar.cik_map(json.loads(got.body))
    except Exception as exc:  # noqa: BLE001 - a missing map costs tickers, not the feed
        log.warning("news: SEC company_tickers.json unavailable (%s) - %s", exc,
                    f"using the last good map ({len(stale)} CIKs)" if stale
                    else "filings are stored without tickers this poll")
        return stale
    if mapping:
        _cik_cache.update(ts=time.time(), map=mapping)
        return mapping
    return stale


def _form4_item(e, feed, fetch, *, ua, timeout, now, universe):
    """The item for one Form 4 accession, ``None`` when it holds no open-market
    purchase (the normal case). Raises ``_Poison`` / ``ValueError`` for an
    unreadable filing and ``FetchError`` for a failed request."""
    folder = e["index_url"].rsplit("/", 1)[0]
    listing = json.loads(_sec_get(fetch, f"{folder}/index.json", ua, timeout).body)
    xml = edgar.xml_url(e["index_url"], listing)
    if not xml:
        raise _Poison("no XML document listed in index.json")
    status, detail = edgar.parse_form4_status(_sec_get(fetch, xml, ua, timeout).body)
    if status == "poison":
        raise _Poison(detail)
    if status != "ok":
        return None
    return edgar.form4_item(detail, feed, e["index_url"], now, universe=universe)


def _forms(feed) -> list:
    if feed["kind"] == "edgar_form4":
        return ["4"]
    raw = feed.get("forms")
    raw = [raw] if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
    forms = [f for f in raw if isinstance(f, str) and f]
    if not forms:
        raise FeedConfigError("no forms configured")
    return forms


def _poll_edgar(feed, db, fetch, *, universe, now, ua, timeout):
    """``(items, status, accessions to mark seen, error or None)``. The
    accessions are marked by the CALLER, after the items are stored, and the
    error is recorded by it after that.

    Form 4: only form ``4`` exactly (a ``4/A`` re-reports the same purchase).
    Filings: exact form matches only (``S-3`` never takes ``S-3ASR`` / ``S-3/A``).
    SEC's ``type=`` is a prefix match, so two listed forms can share rows (the
    S-3 Atom lists every S-3ASR); ``handled`` takes each accession once a poll.
    An accession of a form the feed did not ask for is skipped and NOT marked.
    A poison accession is logged once, marked seen, and skipped; an SEC outage
    mid-poll (``_transient``) leaves that accession and every later one unseen,
    and a later form's Atom failing keeps the earlier forms' work - both are
    returned as the error."""
    name, form4 = feed["name"], feed["kind"] == "edgar_form4"
    forms = _forms(feed)
    wanted = {"4"} if form4 else set(forms)
    new_items, seen, handled = [], [], set()
    cik_map, error = None, None
    processed = failed = 0
    for form in forms:
        try:
            entries = edgar.parse_current(_sec_get(fetch, edgar.CURRENT.format(form=form),
                                                   ua, timeout).body)
        except FetchError as exc:
            error = f"SEC current {form} filings unavailable ({exc})"
            log.warning("news feed %s: %s - %d item(s) from earlier forms kept",
                        name, error, len(new_items))
            break
        fresh = set(db.unseen_accessions([e["accession"] for e in entries], feed=name))
        for i, e in enumerate(entries):
            acc = e["accession"]
            if acc not in fresh or acc in handled:
                continue
            handled.add(acc)
            if e["form"] not in wanted:
                continue          # not remembered: widening ``forms`` still picks it up
            try:
                if form4:
                    it = _form4_item(e, feed, fetch, ua=ua, timeout=timeout, now=now,
                                     universe=universe)
                else:
                    if cik_map is None:
                        cik_map = _cik_to_ticker(fetch, ua, timeout)
                    it = edgar.filing_item(e, feed, now, cik_to_ticker=cik_map)
            except FetchError as exc:
                if _transient(exc):
                    left = sum(1 for x in entries[i + 1:]
                               if x["accession"] in fresh and x["form"] in wanted)
                    error = (f"SEC request failed at accession {acc} ({exc}); it and "
                             f"{left} later accession(s) are left for the next poll")
                    log.warning("news feed %s: %s", name, error)
                    break
                log.warning("news feed %s: accession %s skipped and marked seen (%s)",
                            name, acc, exc)
                it, failed = None, failed + 1
            except Exception as exc:  # noqa: BLE001 - one poison filing must not stop the rest
                log.warning("news feed %s: accession %s skipped and marked seen (%s: %s)",
                            name, acc, type(exc).__name__, exc)
                it, failed = None, failed + 1
            processed += 1
            seen.append(acc)
            if it:
                new_items.append(it)
        if error:
            break
    if error is None and failed * 2 > processed:
        error = f"{failed} of {processed} accessions could not be read"
    return new_items, 200, seen, error


# ── Yahoo ────────────────────────────────────────────────────────────────────

def _poll_yahoo(feed, fetch, *, universe, now, ua, timeout):
    """One URL per symbol, fetched and parsed in a small pool; the caller does
    every store write. One symbol failing costs that symbol; the feed fails only
    when EVERY symbol did."""
    pairs = yahoo_ticker.urls(feed, universe)
    if not pairs:
        if any(isinstance(s, str) and not s.startswith("$") for s in universe):
            raise FeedConfigError("no usable {symbol} url configured")
        return [], 200

    def one(sym, url):
        got = fetch(url, user_agent=ua, timeout=timeout)
        return yahoo_ticker.parse(got.body, feed, now, universe=universe, symbol=sym)

    new, failed = [], []
    workers = max(1, min(_YAHOO_WORKERS, len(pairs)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers,
                                               thread_name_prefix="news-yahoo") as pool:
        futures = [(sym, pool.submit(one, sym, url)) for sym, url in pairs]
        for sym, fut in futures:
            try:
                new.extend(fut.result())
            except Exception as exc:  # noqa: BLE001 - summarised below
                failed.append((sym, exc))
    if failed and len(failed) == len(pairs):
        sym, exc = failed[0]
        raise FetchError(f"every symbol failed ({len(pairs)}); first: {sym}: {exc}")
    if failed:
        named = ", ".join(f"{s} ({str(e)[:80]})" for s, e in failed[:_WARN_LIST])
        more = f", +{len(failed) - _WARN_LIST} more" if len(failed) > _WARN_LIST else ""
        log.warning("news feed %s: %d of %d symbols failed this poll: %s%s",
                    feed["name"], len(failed), len(pairs), named, more)
    return new, 200


# ── one feed ─────────────────────────────────────────────────────────────────

def poll_feed(feed, db, fetch, *, universe, now, cfg) -> dict:
    """Poll one feed into the store. Never raises: a failure is recorded on the
    feed's state, counted as a degrade, and the feed's last items stay."""
    name = str(feed.get("name") or "?")
    kind = feed.get("kind")
    try:
        timeout = _collector(cfg, "request_timeout_s")
        feed_ua = _collector(cfg, "feed_user_agent")
        min_published = _cutoff(now, _collector(cfg, "keep_days"))
        validators, seen, partial = {}, [], None
        if kind in ("rss", "google_news"):
            url = feed.get("url") if kind == "rss" else google_news.url(feed)
            if not isinstance(url, str) or not url.strip():
                raise FeedConfigError("no query configured" if kind == "google_news"
                                      else "no url configured")
            st = db.feed_state(name)
            # Validators answer for the URL they were fetched from: after a url
            # or query edit they would ask the NEW url "changed since <old>?".
            same = st.get("url") == url
            got = fetch(url, etag=st["etag"] if same else None,
                        last_modified=st["last_modified"] if same else None,
                        user_agent=feed_ua, timeout=timeout)
            parse = rss.parse if kind == "rss" else google_news.parse
            new = [] if got.status == 304 else parse(got.body, feed, now, universe=universe)
            status = got.status
            validators = {"etag": got.etag, "last_modified": got.last_modified, "url": url}
        elif kind == "yahoo_ticker":
            new, status = _poll_yahoo(feed, fetch, universe=universe, now=now, ua=feed_ua,
                                      timeout=timeout)
        elif kind in _EDGAR_KINDS:
            new, status, seen, partial = _poll_edgar(
                feed, db, fetch, universe=universe, now=now,
                ua=_collector(cfg, "sec_user_agent"), timeout=timeout)
        else:
            raise FeedConfigError(f"unknown feed kind {kind!r}")
        # Stored FIRST; only then is anything remembered (see the module docstring).
        inserted = db.insert_many(new, min_published=min_published,
                                  same_feed_merge_h=nc.same_feed_merge_h(cfg))
        if seen:
            db.mark_accessions(seen, now, feed=name)
        if partial:
            raise _Partial(partial, inserted)
        db.set_feed_state(name, last_ok=now, last_poll=now, error=None, **validators)
        return {"feed": name, "inserted": inserted, "error": None, "status": status}
    except Exception as exc:  # noqa: BLE001 - one feed must not stop the cycle
        expected = isinstance(exc, (FetchError, FeedConfigError, _Partial))
        _degrade.degraded(f"news.feed.{name}", detail=str(exc)[:200], exc_info=not expected)
        error = (str(exc) if expected else f"{type(exc).__name__}: {exc}")[:200]
        try:
            db.set_feed_state(name, last_poll=now, error=error)
        except Exception:  # noqa: BLE001 - the error is still returned to the status view
            log.warning("news feed %s: could not record its error", name, exc_info=True)
        inserted = exc.inserted if isinstance(exc, _Partial) else 0
        return {"feed": name, "inserted": inserted, "error": error, "status": None}


# ── the cycle ────────────────────────────────────────────────────────────────

_STORE_UNREADABLE = "store unreadable"


def status_rows(db, polled, results) -> list:
    """One row per CONFIGURED feed (enabled or not, in file order), then any
    polled feed the config does not name. ``inserted`` is this poll's count.

    ``db=None`` builds the rows from the config alone - no ``last_ok`` /
    ``last_poll``, and ``error`` saying the store could not be read - which is
    what ``run_poll`` publishes when ``feed_state`` fails."""
    by_name = {r["feed"]: r for r in results}
    feeds = {f["name"]: f for f in nc.all_feeds()}
    for f in polled:
        feeds.setdefault(str(f.get("name") or "?"), f)
    rows = []
    for name, f in feeds.items():
        if db is None:
            st = {"last_ok": None, "last_poll": None, "error": _STORE_UNREADABLE}
        else:
            st = db.feed_state(name)
        rows.append({"name": name, "kind": f.get("kind"),
                     "enabled": bool(f.get("enabled", True)),
                     "public": bool(f.get("public", False)),
                     "last_ok": st["last_ok"], "last_poll": st["last_poll"],
                     "error": st["error"],
                     "inserted": by_name.get(name, {}).get("inserted", 0)})
    return rows


def _aware_now(now) -> dt.datetime:
    """``now`` as an AWARE UTC datetime (``cap_stale`` needs one); an unusable or
    naive ``now`` is the current UTC time."""
    if isinstance(now, str):
        try:
            parsed = dt.datetime.fromisoformat(now.strip().replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None:
            return parsed.astimezone(dt.timezone.utc)
    elif isinstance(now, dt.datetime) and now.tzinfo is not None:
        return now.astimezone(dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc)


def _rescore(db, icfg, universe) -> None:
    """Score every row the store says is out of date and store the results in
    one write. Never raises: a failure is one ``news.impact`` degrade and the
    rows keep what they had (a new row stays unscored until the next pass)."""
    try:
        fp = impact.fingerprint(icfg, universe)
        out = []
        for row in db.rows_to_score(fp):
            pts, reasons = impact.score(row, icfg, universe)
            out.append((row["id"], pts, impact.band(pts, icfg), reasons, fp))
        if out:
            db.set_impact(out)
    except Exception:  # noqa: BLE001 - the views still publish with the stored impact
        _degrade.degraded("news.impact")


def _capped(imp, published_at, now, icfg):
    """``imp`` with the staleness cap applied (a copy; ``None`` stays ``None``)."""
    if not isinstance(imp, dict):
        return None
    band, capped = impact.cap_stale(imp.get("band"), published_at, now, icfg)
    reasons = list(imp.get("reasons") or [])
    if capped:
        reasons.append("stale")
    return {"band": band, "score": imp.get("score"), "reasons": reasons}


def _finish(rows, now, icfg, universe, *, public) -> list:
    """The rows as published. Private: the STORED impact, capped. Public: the
    stored impact is never used (the store hands back ``None``); each row is
    re-scored from itself - its ``sources`` / ``tickers`` already cut to the
    public ones - then capped. A public re-score failure publishes the rows
    with ``impact: None`` and one ``news.impact.public`` degrade."""
    out, failed = [], False
    for row in rows:
        row = dict(row)
        if public:
            imp = None
            if not failed:
                try:
                    imp = impact.apply(row, icfg, universe)
                except Exception:  # noqa: BLE001 - the view still publishes, unscored
                    failed = True
                    _degrade.degraded("news.impact.public")
        else:
            imp = row.get("impact")
        row["impact"] = _capped(imp, row.get("published_at"), now, icfg)
        out.append(row)
    return out


def run_poll(bus, db, fetch, *, feeds, universe, now, cfg) -> dict:
    results = [poll_feed(f, db, fetch, universe=universe, now=now, cfg=cfg) for f in feeds]
    try:
        db.prune(keep_days=_collector(cfg, "keep_days"), now=now)
    except Exception:  # noqa: BLE001 - old rows linger a cycle; the views still publish
        _degrade.degraded("news.prune")
    icfg = nc.impact_config()
    _rescore(db, icfg, universe)
    at = _aware_now(now)
    n = _collector(cfg, "view_items")
    sec_n = _collector(cfg, "sec_view_items")
    # The CURRENT flags, re-read at every publish - never the ingest-time copy
    # (inside each public view's own step, so a failure costs that view alone).
    public = nc.public_feed_names
    views = (
        ("news.publish", handlers.publish_feed, False,
         lambda: db.newest(n, exclude_kinds=_EDGAR_KINDS)),
        ("news.publish_public", handlers.publish_feed_public, True,
         lambda: db.newest(n, public_sources=public(), exclude_kinds=_EDGAR_KINDS)),
        ("news.publish_sec", handlers.publish_sec, False,
         lambda: db.newest(sec_n, kinds=_EDGAR_KINDS)),
        ("news.publish_sec_public", handlers.publish_sec_public, True,
         lambda: db.newest(sec_n, public_sources=public(), kinds=_EDGAR_KINDS)),
    )
    for area, publish, is_public, read in views:
        try:
            publish(bus, _finish(read(), at, icfg, universe, public=is_public))
        except Exception:  # noqa: BLE001 - that view's last copy stays; the rest go
            _degrade.degraded(area)
    try:
        rows = status_rows(db, feeds, results)
    except Exception:  # noqa: BLE001 - the status is how the page learns the store is sick
        _degrade.degraded("news.status")
        rows = status_rows(None, feeds, results)
    handlers.publish_status(bus, rows, now)
    return {"results": results}


def poll_now(bus, db=None, fetch=None):
    """What the scheduler and the ``news_refresh`` command both run. One at a
    time: a second caller while a poll runs gets ``{"skipped": "busy"}`` at once."""
    if not _POLL_LOCK.acquire(blocking=False):
        return {"skipped": "busy"}
    try:
        cfg = nc.load()
        if fetch is None:
            fetch = functools.partial(http_fetch,
                                      max_bytes=_collector(cfg, "max_body_bytes"))
        kw = {"feeds": nc.feeds(), "universe": nc.ticker_set(), "now": _now_iso(),
              "cfg": cfg}
        if db is not None:
            return run_poll(bus, db, fetch, **kw)
        with _store.Store() as own:
            return run_poll(bus, own, fetch, **kw)
    finally:
        _POLL_LOCK.release()
