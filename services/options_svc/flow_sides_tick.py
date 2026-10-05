"""Bought/sold tallies for flagged flow-alert contracts: the session state, the
store, the next-day open-interest follow-up and the two published views.

The arithmetic is ``flow_sides`` (pure). This module is the state around it:

``on_chain``      every fetched chain, on the collector's thread. In memory only
                  -- it never opens the database, so it cannot slow or break a
                  poll. Books each contract's new volume for the session.
``after_alerts``  once a minute, after the detectors, on the same thread.
                  Registers newly flagged contracts, writes their rows,
                  resolves the previous session's rows from today's open
                  interest, and publishes ``cache:options:flow_sides`` and
                  ``cache:options:flow_followup``.
``stream_tick``   the stream worker's thread (``flow_stream``): what trades in
                  a flagged contract AFTER its alert.

Everything is an ESTIMATE and is published as one. Volume this service did not
watch print -- before a restart, across a poll gap -- is unlabelled, never
guessed and never dropped. It imports nothing from ``compute``.
Design: docs/plans/2026-10-04-flow-alert-sides-design.md.
"""
import datetime as _dt
import logging
import sys
import threading
from zoneinfo import ZoneInfo

from repo_paths import OPTIONS_SCANNER
from shared import market_calendar as _mc
from shared.numeric import finite as _finite

from services import _degrade
from services.options_svc import collection_tiers, flow_alerts, flow_sides

# ``gex_history_db`` lives under options-scanner; ``compute`` puts it on the
# path too, repeated here so this module imports on its own (a test, a tool).
if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

log = logging.getLogger(__name__)

CACHE_SIDES = "cache:options:flow_sides"
EVENT_SIDES = "events:options:flow_sides"
CACHE_FOLLOWUP = "cache:options:flow_followup"
EVENT_FOLLOWUP = "events:options:flow_followup"

# The alert kinds that name ONE contract. A premium shift, a gamma flip and the
# hedging-flow rows are about a whole symbol and have no tally.
CONTRACT_TYPES = ("uoa", "big_delta")

_CT = ZoneInfo("America/Chicago")

# A symbol whose last fetch is older than this is booked UNLABELLED for that
# step: several minutes of volume must not take one minute's bid/ask label. It
# follows the COLLECTOR, not an operator's choice: a watchlist-only symbol is
# fetched as rarely as every MAX_TAIL_INTERVAL_MIN minutes by design, and that
# normal step must still be labelled. (The hedging-flow model uses 150 s; it
# measures one-minute symbols only.)
MAX_GAP_SEC = int(collection_tiers.MAX_TAIL_INTERVAL_MIN * 60 * 1.5)

# What a view says once its switch is turned off: published ONCE, so a screen
# stops showing figures the operator has just switched off. An empty view is
# never ``public``.
_NO_FOLLOWUP = {"date": None, "public": False, "rows": []}


def _fresh_state(date=None) -> dict:
    return {
        "date": date,           # CT session date the state belongs to
        "books": {},            # {symbol: {contract symbol: flow_sides entry}}
        "seeded": set(),        # symbols with one usable poll this session
        "last_ts": {},          # {symbol: unix ts of its last usable poll}
        "flagged": {},          # {alert id: row identity}, insertion-ordered
        "written": {},          # {alert id: the figures last written}
        # A later alert on a contract already streamed counts "since the alert"
        # from ITS OWN alert: {alert id: the stream tally when it fired}.
        "stream_base": {},
        "pending": {},          # {symbol: {contract symbol: [previous-session rows]}}
        "watch": {},            # {symbol: frozenset of pending contract symbols}
        # Symbols fetched in regular hours WHILE their watch list was in place:
        # only then does "no entry for a watched contract" mean "not in the chain".
        "watched_fetch": set(),
        "resolutions": [],      # resolved, not yet written; kept until the write succeeds
        "restored": False,      # today's flagged rows read back from the store
        "pending_loaded": False,  # the follow-up rows read from the store
        "purged": False,        # retention ran for this session date
        "followup_date": None,  # the session the Previous-session panel shows
        "followup_dirty": False,
        "followup_pub": None,   # (public, follow-up on) as last published
    }


# The collector thread's state: ``on_chain`` and ``after_alerts`` run on ONE
# thread (poll_once loops over its results on the calling thread, then
# run_flow_alerts follows), so it needs no lock.
_S: dict = _fresh_state()

# The stream worker's state, shared with the collector thread: every access is
# under ``_STREAM_LOCK``.
_STREAM_LOCK = threading.Lock()
_STREAM: dict = {"book": {}, "quotes": {}, "wanted": []}

_SCHEMA_READY = False
# The session date the views were last RETRACTED for (the estimate switched off).
_RETRACTED: dict = {"date": None}


def reset() -> None:
    """Drop all state (test helper; also what a new session date does)."""
    global _SCHEMA_READY
    _SCHEMA_READY = False
    _RETRACTED["date"] = None
    _roll(None, force=True)


def _roll(date, force=False) -> None:
    if not force and _S["date"] == date:
        return
    _S.clear()
    _S.update(_fresh_state(date))
    with _STREAM_LOCK:
        _STREAM.update(book={}, quotes={}, wanted=[])


def _now(now):
    """``now`` as an aware Central datetime. None = the scheduler's clock, the
    one ``compute`` and the hedging-flow gate read; a naive value is Central."""
    if now is None:
        from services.options_svc import scheduler as _sched   # lazy: import cycle
        now = _sched._market_now()
    if now.tzinfo is None:
        return now.replace(tzinfo=_CT)
    return now.astimezone(_CT)


def _on(cfg, name) -> bool:
    """A section's ``enabled``: only a literal True switches it on."""
    return flow_alerts.section(cfg, name).get("enabled") is True


def _usable(chain) -> bool:
    """A chain with at least one expiration map. An error body is a truthy
    dict too, and must not count as this symbol's first poll: the next real
    chain would then read the whole day's volume as new."""
    if not isinstance(chain, dict):
        return False
    return any(isinstance(chain.get(k), dict) and chain.get(k)
               for k in ("callExpDateMap", "putExpDateMap"))


def on_chain(symbol, chain, now=None) -> None:
    """Book one fetched chain's new volume. In memory only; never raises."""
    try:
        if not _on(flow_alerts.load_thresholds(), "sides") or not _usable(chain):
            return
        now = _now(now)
        # Nothing before the regular open. Until then a chain can still carry
        # YESTERDAY's volume for a contract that has not traded yet; booked, it
        # would stand as today's and, through the high-water mark, hide today's
        # real volume until that passed it. (Not measured on live data: this is
        # the conservative reading, and the one the hedging-flow model takes.)
        # After the 15:00 close it keeps booking: ETF options trade to 15:15.
        if not _mc.regular_session_has_opened(now):
            return
        _roll(now.date().isoformat())
        ts = int(now.timestamp())
        seeded = symbol in _S["seeded"]
        last = _S["last_ts"].get(symbol)
        # Unseeded: this process has not watched the symbol today, so whatever
        # volume it carries (and whatever a restored row had reached) printed
        # unseen. A gap is the same case, mid-session.
        label = seeded and last is not None and ts - last <= MAX_GAP_SEC
        # Index open interest reads zero outside the regular session.
        read_oi = _mc.is_regular_hours(now)
        watch = _S["watch"].get(symbol, ())
        flow_sides.advance(
            chain, _S["books"].setdefault(symbol, {}), seeded=seeded, label=label,
            read_oi=read_oi, watch=watch)
        _S["seeded"].add(symbol)
        _S["last_ts"][symbol] = ts
        if read_oi and watch:
            _S["watched_fetch"].add(symbol)
    except Exception:
        _degrade.degraded("options.flow_sides.on_chain", detail=symbol)


# ── the stream worker's side ─────────────────────────────────────────────────
# The most contracts the stream will ever hold, whatever the setting says. The
# set travels as ONE query string to the proxy (about 24 characters a contract
# once encoded), and an HTTP request line has a size limit: h11's is 16 KB. 500
# contracts is ~12 KB. Settings -> Configuration offers no more than this.
STREAM_HARD_MAX = 500


def _stream_cap(sides) -> int:
    cap = _finite(sides.get("stream_max_contracts"))
    if cap is None:
        cap = flow_alerts._DEFAULTS["sides"]["stream_max_contracts"]
    return max(0, min(int(cap), STREAM_HARD_MAX))


def wanted_osis() -> list:
    """The contracts the stream worker should hold, oldest alert first. Empty
    while the estimate or its stream is switched off. Never raises."""
    try:
        cfg = flow_alerts.load_thresholds()
        sides = flow_alerts.section(cfg, "sides")
        if sides.get("enabled") is not True or sides.get("stream") is not True:
            return []
        with _STREAM_LOCK:
            return list(_STREAM["wanted"])
    except Exception:
        _degrade.degraded("options.flow_sides.wanted_osis")
        return []


def stream_tick(tick, label=True) -> None:
    """Book one level-one tick from the stream worker. Never raises."""
    try:
        with _STREAM_LOCK:
            flow_sides.advance_tick(_STREAM["book"], _STREAM["quotes"], tick,
                                    label=label)
    except Exception:
        _degrade.degraded("options.flow_sides.stream_tick")


def _want_flagged(sides) -> None:
    """Make every flagged contract wanted, oldest alert first, up to the cap.
    Run every minute, not only when a contract is flagged, so the stream
    switch turned on mid-session picks up what was flagged before it."""
    if sides.get("stream") is not True:
        return
    cap = _stream_cap(sides)
    with _STREAM_LOCK:
        wanted = _STREAM["wanted"]
        for meta in _S["flagged"].values():
            if len(wanted) >= cap:
                break
            if meta["osi"] not in wanted:
                wanted.append(meta["osi"])


def _stream_tally(osi):
    """One contract's stream tally, or None when it has not been streamed."""
    with _STREAM_LOCK:
        entry = _STREAM["book"].get(osi)
        return flow_sides.tally(entry) if entry is not None else None


def _stream_tallies() -> dict:
    """``{contract symbol: tally}`` for every streamed contract, copied under
    the lock so the caller reads a consistent set."""
    with _STREAM_LOCK:
        return {osi: flow_sides.tally(e) for osi, e in _STREAM["book"].items()}


# ── the store ────────────────────────────────────────────────────────────────
_TALLY_KEYS = ("bought", "sold", "unlabelled")
# The poll's tally at the moment the alert was registered: written once with
# the row and read back with it, like the identity. Poll-since-the-alert is the
# running tally minus this, over the same window the stream covers.
_AT_ALERT = tuple(f"at_{k}" for k in _TALLY_KEYS)
_IDENTITY = ("symbol", "osi", "side", "strike", "expiry", "alert_type",
             "fired_ts") + _AT_ALERT


def _restore_today(gh, conn, today) -> None:
    """Read today's flagged rows back (a restart), oldest alert first."""
    for r in gh.load_flow_contract_days(conn, today):
        _S["flagged"][r["alert_id"]] = {k: r[k] for k in _IDENTITY}
        _restore_tally(r)


def _restore_tally(r) -> None:
    """Put a stored row's tallies back after a restart. What was labelled stays
    labelled; everything else the contract has traded is unlabelled."""
    bought, sold = _finite(r.get("poll_bought")) or 0.0, _finite(r.get("poll_sold")) or 0.0
    stored_vol = _finite(r.get("volume")) or 0.0
    book = _S["books"].setdefault(r["symbol"], {})
    entry = book.get(r["osi"])
    if entry is None:
        entry = book[r["osi"]] = flow_sides.new_entry()
    entry[flow_sides.HW] = max(entry[flow_sides.HW], stored_vol)
    entry[flow_sides.BOUGHT], entry[flow_sides.SOLD] = bought, sold
    entry[flow_sides.UNLABELLED] = max(entry[flow_sides.HW] - bought - sold, 0.0)
    if entry[flow_sides.OI] is None:
        entry[flow_sides.OI] = _finite(r.get("oi_prev"))
    stored = {k: _finite(r.get(f"stream_{k}")) for k in _TALLY_KEYS}
    if stored["bought"] is None:
        return
    stored = {k: v or 0.0 for k, v in stored.items()}
    with _STREAM_LOCK:
        entry = _STREAM["book"].get(r["osi"])
        if entry is None:
            entry = _STREAM["book"][r["osi"]] = flow_sides.new_entry()
            # The volume the stream had reached is unknown: None makes the next
            # tick seed it (flow_sides.advance_tick) and keep this tally.
            entry[flow_sides.HW] = None
            entry[flow_sides.BOUGHT] = stored["bought"]
            entry[flow_sides.SOLD] = stored["sold"]
            entry[flow_sides.UNLABELLED] = stored["unlabelled"]
            return
        current = flow_sides.tally(entry)
    # A LATER alert on a contract an earlier row already put back: its stored
    # figure is smaller by what streamed before it fired. Keep that difference.
    base = {k: max(current[k] - stored[k], 0.0) for k in _TALLY_KEYS}
    if any(base.values()):
        _S["stream_base"][r["alert_id"]] = base


def _load_pending(gh, conn, today) -> None:
    """Load the rows still waiting for (or still re-reading) their open interest.

    Only the PREVIOUS trading day's rows are read against today's chain. An
    older row that was never read (the service was down, the follow-up was
    off) gets no reading: set against today it would be several days of
    open-interest change divided by one day's volume."""
    y, m, d = (int(x) for x in today.split("-"))
    previous = _mc.prev_trading_day(_dt.date(y, m, d)).isoformat()
    for r in gh.load_unresolved_flow_days(conn, before=today):
        expiry = r.get("expiry")
        if expiry and str(expiry) < today:
            # Gone before it could be read. On its own alert day that is
            # "expired"; later, the service was down across its last sessions.
            code = "expired" if str(expiry) <= str(r["session_date"]) else "none"
            _queue_resolution(r, None, today, code, None)
        elif r["session_date"] != previous:
            _queue_resolution(r, None, today, "none", None)
        else:
            _pend(r)
    # Read earlier TODAY, before a restart: keep re-reading for the rest of the
    # day. When Schwab's chain picks up the new figure is not yet measured, so
    # the first read may only have repeated yesterday's.
    for r in gh.load_flow_days_read_on(conn, today):
        _pend(r)
    _S["watch"] = {sym: frozenset(by) for sym, by in _S["pending"].items()}
    _S["followup_date"] = gh.latest_flow_session_before(conn, today)
    _S["followup_dirty"] = True


def _pend(row) -> None:
    _S["pending"].setdefault(row["symbol"], {}).setdefault(row["osi"], []).append(row)


def _register(fresh, now_ts) -> None:
    for a in fresh or ():
        if not isinstance(a, dict) or a.get("type") not in CONTRACT_TYPES:
            continue
        aid, osi = a.get("id"), a.get("osi")
        if (not isinstance(aid, str) or not aid or not isinstance(osi, str)
                or not osi or aid in _S["flagged"]):
            continue
        _S["flagged"][aid] = {
            "symbol": a.get("symbol"), "osi": osi, "side": a.get("side"),
            "strike": _finite(a.get("strike")), "expiry": a.get("expiry"),
            "alert_type": a.get("type"),
            "fired_ts": int(_finite(a.get("ts")) or now_ts)}
        # The tally as it stands now, the minute the alert fired. None when the
        # contract has not been booked yet: unknown is not three zeros.
        entry = _S["books"].get(a.get("symbol"), {}).get(osi)
        at = flow_sides.tally(entry) if entry is not None else {}
        _S["flagged"][aid].update({f"at_{k}": at.get(k) for k in _TALLY_KEYS})
        # Already streamed for an earlier alert on the same contract: this
        # alert's "since the alert" starts from here, not from that one.
        base = _stream_tally(osi)
        if base is not None and any(base.values()):
            _S["stream_base"][aid] = base


def _figures(aid, meta, streams) -> dict:
    """One flagged contract's running figures, from the two books."""
    entry = _S["books"].get(meta["symbol"], {}).get(meta["osi"])
    if entry is None:
        poll, volume, oi = {"bought": 0.0, "sold": 0.0, "unlabelled": 0.0}, None, None
    else:
        poll = flow_sides.tally(entry)
        volume, oi = entry[flow_sides.HW], entry[flow_sides.OI]
    stream = streams.get(meta["osi"])
    base = _S["stream_base"].get(aid)
    if stream is not None and base:
        stream = {k: max(stream[k] - base[k], 0.0) for k in _TALLY_KEYS}
    at_alert = (None if meta.get("at_bought") is None
                else {k: meta[f"at_{k}"] for k in _TALLY_KEYS})
    return {"poll": poll, "stream": stream, "volume": volume, "oi_prev": oi,
            "at_alert": at_alert}


def _queue_resolution(row, oi_next, today, code, ratio) -> None:
    row.update(oi_next=oi_next, oi_next_date=today, verdict=code, oi_ratio=ratio)
    _S["resolutions"].append(
        (row["session_date"], row["alert_id"], oi_next, today, code, ratio))
    _S["followup_dirty"] = True


def _ratio(follow, key):
    v = _finite(follow.get(key))
    return flow_alerts._DEFAULTS["followup"][key] if v is None else v


def _resolve_pending(today, follow) -> None:
    """Read today's open interest for the previous session's flagged contracts.

    Re-read every minute: when Schwab's chain starts showing the new figure is
    not yet measured, so a first read may repeat yesterday's. A figure that
    MOVES is logged -- that log line is the measurement."""
    opened, closed = _ratio(follow, "opened_ratio"), _ratio(follow, "closed_ratio")
    for symbol, by_osi in _S["pending"].items():
        book = _S["books"].get(symbol) or {}
        for osi, rows in by_osi.items():
            entry = book.get(osi)
            if entry is None:
                # A watched contract gets an entry whenever it is in the chain,
                # traded or not. None after a regular-hours fetch made WITH the
                # watch list means it is not there: no reading, never a guess.
                # (A later fetch that does carry it replaces this below.)
                if symbol in _S["watched_fetch"]:
                    for row in rows:
                        if row.get("verdict") is None:
                            _queue_resolution(row, None, today, "none", None)
                continue
            oi = entry[flow_sides.OI]
            if oi is None:
                continue
            for row in rows:
                had = row.get("oi_next")
                if row.get("verdict") is not None and had == oi:
                    continue
                code, ratio = flow_sides.verdict(
                    row.get("oi_prev"), oi, row.get("volume"),
                    expiry=row.get("expiry"), session_date=row.get("session_date"),
                    opened_ratio=opened, closed_ratio=closed)
                if row.get("verdict") is not None:
                    log.info("flow follow-up: open interest moved for %s (%s): "
                             "%s -> %s, verdict %s -> %s", osi, row["session_date"],
                             had, oi, row.get("verdict"), code)
                _queue_resolution(row, oi, today, code, ratio)


def _write(gh, conn, today, figures) -> None:
    rows = []
    for aid, meta in _S["flagged"].items():
        f = figures[aid]
        stream = f["stream"] or {}
        sig = (f["oi_prev"], f["volume"], tuple(f["poll"].values()),
               tuple(stream.values()))
        if _S["written"].get(aid) == sig:
            continue
        rows.append((aid, sig, {
            "session_date": today, "alert_id": aid, **meta,
            "oi_prev": f["oi_prev"], "volume": f["volume"],
            "poll_bought": f["poll"]["bought"], "poll_sold": f["poll"]["sold"],
            "poll_unlabelled": f["poll"]["unlabelled"],
            "stream_bought": stream.get("bought"), "stream_sold": stream.get("sold"),
            "stream_unlabelled": stream.get("unlabelled")}))
    if rows:
        gh.upsert_flow_contract_days(conn, [r for _aid, _sig, r in rows])
        for aid, sig, _r in rows:
            _S["written"][aid] = sig
    if _S["resolutions"]:
        # ONE commit, and the queue is emptied only once it has succeeded: a
        # failed write (a locked store) is retried next minute, not forgotten.
        gh.resolve_flow_contract_days(conn, _S["resolutions"])
        _S["resolutions"] = []


def _retract(bus, today) -> None:
    """The estimate was switched off: empty both views, once per session date,
    so no screen keeps showing figures the operator has switched off."""
    if _RETRACTED["date"] == today:
        return
    bus.cache_set(CACHE_SIDES, {"date": today, "public": False, "contracts": {}},
                  event=EVENT_SIDES, skip_unchanged=True)
    bus.cache_set(CACHE_FOLLOWUP, dict(_NO_FOLLOWUP), event=EVENT_FOLLOWUP,
                  skip_unchanged=True)
    _RETRACTED["date"] = today


def after_alerts(bus, fresh, today, now_ts) -> None:
    """Register this minute's flagged contracts, persist, resolve, publish.

    ``fresh`` is ``run_flow_alerts``' list of alerts new this minute (any
    kind); ``today`` the CT session date; ``now_ts`` the detecting tick.
    Best-effort: never raises -- a failure here must not cost a flow alert."""
    global _SCHEMA_READY
    try:
        cfg = flow_alerts.load_thresholds()
        if not _on(cfg, "sides"):
            _retract(bus, today)
            return
        sides = flow_alerts.section(cfg, "sides")
        follow = flow_alerts.section(cfg, "followup")
        follow_on = follow.get("enabled") is True
        public = sides.get("public") is True
        _roll(today)
        if _RETRACTED["date"] is not None:
            # Switched back on after a retraction: the follow-up view is empty
            # in Redis though nothing in memory changed, so publish it again.
            _RETRACTED["date"] = None
            _S["followup_pub"] = None
        # A changed switch republishes the follow-up view even when no row
        # moved: turning ``public`` off must not wait for the next resolution.
        if _S["followup_pub"] != (public, follow_on):
            _S["followup_dirty"] = True

        import gex_history_db as gh
        conn = gh.connect()
        followup = None
        try:
            if not _SCHEMA_READY:
                gh.init_flow_day_schema(conn)
                _SCHEMA_READY = True
            if not _S["restored"]:
                _restore_today(gh, conn, today)
                _S["restored"] = True
            if follow_on and not _S["pending_loaded"]:
                _load_pending(gh, conn, today)
                _S["pending_loaded"] = True
            _register(fresh, now_ts)
            _want_flagged(sides)
            if follow_on:
                _resolve_pending(today, follow)
            streams = _stream_tallies()
            figures = {aid: _figures(aid, meta, streams)
                       for aid, meta in _S["flagged"].items()}
            _write(gh, conn, today, figures)
            if not _S["purged"]:
                keep = _finite(follow.get("keep_sessions"))
                gh.purge_flow_contract_days(
                    conn, int(keep) if keep is not None
                    else flow_alerts._DEFAULTS["followup"]["keep_sessions"])
                _S["purged"] = True
            if _S["followup_dirty"]:
                if not follow_on:
                    followup = dict(_NO_FOLLOWUP)
                elif _S["followup_date"]:
                    followup = {
                        "date": _S["followup_date"], "public": public,
                        "rows": gh.load_flow_contract_days(conn, _S["followup_date"])}
        finally:
            conn.close()

        # Only once a contract is flagged: an empty dated view says nothing.
        if figures:
            bus.cache_set(CACHE_SIDES, {
                "date": today, "public": public,
                # ``side`` and ``osi`` ride each entry so a reader that pools
                # the estimate (market_svc.market_read.flow) needs this view
                # alone, not a join against the capped alert list.
                "contracts": {aid: {"poll": f["poll"], "stream": f["stream"],
                                    "volume": f["volume"],
                                    "at_alert": f["at_alert"],
                                    "side": _S["flagged"][aid]["side"],
                                    "osi": _S["flagged"][aid]["osi"]}
                              for aid, f in figures.items()}},
                event=EVENT_SIDES, skip_unchanged=True)
        if followup is not None:
            bus.cache_set(CACHE_FOLLOWUP, followup, event=EVENT_FOLLOWUP,
                          skip_unchanged=True)
            _S["followup_dirty"] = False
            _S["followup_pub"] = (public, follow_on)
    except Exception:
        _degrade.degraded("options.flow_sides.after_alerts")
