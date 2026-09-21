"""The public Rescue form's worker: one visitor request -> one answer.

A visitor on ``live.neuralstrike.co/rescue`` loads a symbol's strikes and
describes a trade; the public process puts one validated command on
``cmd:rescue_public`` (``webgui/bus_client.request_public_ladder`` /
``request_public_rescue``). This module handles that stream, on its OWN
consumer loop (``make_app``'s ``extra_consumers``), so a visitor's request never
queues ahead of the owner's commands on ``cmd:options`` or the Finder's scans.

Two requests (``shared.public_rescue``):

* **ladder** -> ``cache:options:rescue_pub_ladder:<SYMBOL>``: expirations and
  each expiration's strikes. The chain is fetched by the Calculator's own lazy
  loader and every quote is thrown away before anything is written - the
  public form publishes no bid, ask or mark (decision D1).
* **compute** -> ``cache:options:rescue_pub:<hash of the trade>``: the private
  form's own ``compute_rescue_adhoc``, advisory-only, never an Apply.

Every request ends in one outcome from ``public_rescue.OUTCOMES``, written to
``cache:options:rescue_pub_answer:<request key>`` so the visitor's page reads
its own answer. The refusals run in this order, all before any Schwab call:

1. ``invalid``    the fields fail the shared validator (counted; with no valid
                  key there is nothing to answer, and the page ran the same
                  validator before sending, so it never waits on one);
2. ``expired``    waited longer than ``max_wait_sec`` or stamped in the future -
                  also the replay guard, since a consumer group created at id 0
                  re-delivers the stream's whole backlog on a fresh start;
3. ``cached``     a fresh result for exactly this request exists;
4. ``not_listed`` / ``no_options``  answered from a strikes list already held;
5. ``duplicate``  the same request ran under ``dedup_sec`` ago;
6. ``closed``     outside ``[windows.rescue_public]``;
7. ``budget``     the day's compute or strikes budget is spent.

⚠ **No LIST of visitors' requests is kept.** The status view holds counts only
(the Finder's status map lists every symbol anyone searched, and a list of
trades people hold is more sensitive than that), the dedup memory lives in this
process and forgets on restart, and nothing is logged with an address - this
process never sees one. That is not the same as keeping nothing: each result
holds its trade for ``result_keep_min`` under a hashed key, readable by anything
with a Redis read credential (see ``public_rescue.spec_key``).

Design: docs/plans/2026-09-21-public-rescue-adhoc-roadmap.md, Phase 1.
"""
from __future__ import annotations

import collections
import datetime as dt
import logging
import math
import threading
import time
from zoneinfo import ZoneInfo

from services import _degrade
from services.options_svc import compute
from shared import market_calendar
from shared import public_rescue as pr
from shared.symbols import clean_symbol

log = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")
WINDOW = "rescue_public"
FUTURE_SKEW_SEC = 30
# A ``busy`` marker older than this is a crash's leftover, not a running job.
BUSY_STALE_SEC = 300
# How many request keys the in-process dedup memory holds; oldest go first.
DEDUP_KEEP = 2000

_RECENT: "collections.OrderedDict[str, float]" = collections.OrderedDict()
# Runs per trade STRUCTURE (the trade less its prices and size). The cache and
# the dedup key on the whole trade, so without this a visitor could run one
# structure afresh for every price they type, and a handful of addresses could
# spend the day's shared budget that way.
_RUNS: "collections.OrderedDict[str, collections.deque]" = collections.OrderedDict()
_RECENT_LOCK = threading.Lock()


def _now() -> dt.datetime:
    """The current time in CT. A function so tests can pin the clock."""
    return dt.datetime.now(CT)


def _mono() -> float:
    return time.monotonic()


def reset_memory() -> None:
    """Forget the dedup memory (tests)."""
    with _RECENT_LOCK:
        _RECENT.clear()
        _RUNS.clear()


def _ran_recently(key, within_sec) -> bool:
    with _RECENT_LOCK:
        at = _RECENT.get(key)
    return at is not None and _mono() - at < within_sec


def _mark_ran(key) -> None:
    with _RECENT_LOCK:
        _RECENT.pop(key, None)
        _RECENT[key] = _mono()
        while len(_RECENT) > DEDUP_KEEP:
            _RECENT.popitem(last=False)


def _structure_busy(key, limit, window_sec) -> bool:
    """Whether this structure has already run ``limit`` times in the window."""
    now = _mono()
    with _RECENT_LOCK:
        runs = _RUNS.get(key)
        if runs is None:
            return False
        while runs and now - runs[0] >= window_sec:
            runs.popleft()
        return len(runs) >= limit


def _count_structure(key) -> None:
    with _RECENT_LOCK:
        runs = _RUNS.pop(key, None) or collections.deque()
        runs.append(_mono())
        _RUNS[key] = runs
        while len(_RUNS) > DEDUP_KEEP:
            _RUNS.popitem(last=False)


def _age_s(iso, now) -> float | None:
    if not iso:
        return None
    try:
        when = dt.datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return (now - when).total_seconds()


# ── the status view: counts only ─────────────────────────────────────────────

def _read_status(bus, now) -> dict:
    today = now.date().isoformat()
    env = bus.cache_get(pr.STATUS_KEY)
    status = dict(env.payload) if env and isinstance(env.payload, dict) else {}
    if status.get("date") != today:
        status = {"date": today, "computes_today": 0, "ladders_today": 0,
                  "invalid_today": 0, "busy": None}
    for k in ("computes_today", "ladders_today", "invalid_today"):
        status.setdefault(k, 0)
    busy = status.get("busy")
    since = _age_s(busy.get("since"), now) if isinstance(busy, dict) else None
    if since is None or since > BUSY_STALE_SEC:
        status["busy"] = None
    return status


def _write_status(bus, status) -> None:
    lim = pr.limits()
    status["daily_budget"] = lim["daily_budget"]
    status["computes_left"] = max(0, lim["daily_budget"] - status["computes_today"])
    status["ladder_budget"] = lim["ladder_budget"]
    status["ladders_left"] = max(0, lim["ladder_budget"] - status["ladders_today"])
    start, end = market_calendar.window_bounds(WINDOW)
    status["window"] = {"start": start.strftime("%H:%M"),
                        "end": end.strftime("%H:%M"), "tz": "CT"}
    version = bus.cache_set(pr.STATUS_KEY, status)
    bus.publish(pr.STATUS_EVENT, {"version": version})


def _answer(bus, key, outcome, now) -> None:
    view = pr.answer_view(key)
    version = bus.cache_set(pr.cache_key(view), {"outcome": outcome,
                                                 "at": now.isoformat()},
                            ttl=pr.limits()["answer_keep_min"] * 60)
    bus.publish(pr.event(view), {"version": version})


def _payload(bus, view):
    env = bus.cache_get(pr.cache_key(view))
    return env.payload if env is not None and isinstance(env.payload, dict) else None


# ── the strikes list ─────────────────────────────────────────────────────────

def strikes_from_chain(chain) -> dict:
    """``{expiry: {"call": [strikes], "put": [strikes]}}`` from a thinned chain.
    Strikes only: every quote field is left behind."""
    out: dict = {}
    for map_key, right in (("callExpDateMap", "call"), ("putExpDateMap", "put")):
        for exp_key, strikes in ((chain or {}).get(map_key) or {}).items():
            exp = str(exp_key).split(":")[0]
            ladder = out.setdefault(exp, {"call": [], "put": []})
            for s in (strikes or {}):
                try:
                    ladder[right].append(float(s))
                except (TypeError, ValueError):
                    continue
    for ladder in out.values():
        for right in ("call", "put"):
            ladder[right] = sorted(set(ladder[right]))
    return out


def _spot(price):
    try:
        f = float(price)
    except (TypeError, ValueError):
        return None
    return round(f, 2) if math.isfinite(f) and f > 0 else None


def _load_ladder(symbol, expiry, existing, now, *, fresh):
    """Fetch what the request needs; return ``(ladder or None, outcome)``.
    ``None`` means write nothing: a list already held stays as it was.

    * One more expiration on a FRESH list is merged into it and keeps the list's
      own ``loaded_at``: the EXPIRATIONS came from that first load, so a merge
      must not make them look newer than they are.
    * A STALE list is reloaded, asking again for every expiration it had
      strikes for, so a leg already sitting on one does not lose its strikes.
    * ⚠ ``no_options`` only when Schwab ANSWERED and listed nothing. A fetch
      that failed is an ``error`` and is remembered nowhere: stored as "no
      options", one proxy hiccup would tell every visitor for an hour that SPY
      has no options."""
    held = existing if isinstance(existing, dict) and not existing.get("no_options") else None
    if (fresh and held and expiry and held.get("api")
            and expiry in (held.get("expirations") or [])):
        extra = compute._fetch_thin_runs(held["api"], [[expiry]])
        if extra is None:
            return None, "error"
        strikes = dict(held.get("strikes") or {})
        strikes.update(strikes_from_chain(extra))
        return {**held, "strikes": strikes}, "done"
    stamp = now.isoformat()
    wanted = ([expiry] if expiry else []) + sorted((held or {}).get("strikes") or {})
    cc = compute.calc_load_symbol(symbol, lazy=True, expiries=wanted or None)
    chain = cc.get("chain")
    strikes = strikes_from_chain(chain)
    expirations = list(cc.get("expirations") or sorted(strikes))
    if not strikes:
        if not isinstance(chain, dict) or expirations:
            return None, "error"        # a fetch failed; say so, remember nothing
        return {"symbol": symbol, "no_options": True, "loaded_at": stamp}, "no_options"
    ladder = {"symbol": symbol, "api": cc.get("api") or symbol,
              "spot": _spot(cc.get("price")), "expirations": expirations,
              "strikes": strikes, "loaded_at": stamp}
    if expiry and expiry not in expirations:
        return ladder, "not_listed"
    return ladder, "done"


def _handle_ladder(bus, command, now, lim, status) -> None:
    args = getattr(command, "args", None) or {}
    symbol = clean_symbol(args.get("symbol"))
    raw_expiry = args.get("expiry")
    expiry = pr.clean_expiry(raw_expiry, now.date()) if raw_expiry is not None else None
    if symbol is None or (raw_expiry is not None and expiry is None):
        status["invalid_today"] += 1
        _write_status(bus, status)
        return
    key = pr.ladder_key(symbol, expiry)
    view = pr.ladder_view(symbol)
    existing = _payload(bus, view)
    age = _age_s(getattr(command, "ts", None), now)
    held = _age_s((existing or {}).get("loaded_at"), now)
    fresh = held is not None and 0 <= held < lim["ladder_ttl_min"] * 60
    if age is not None and (age > lim["max_wait_sec"] or age < -FUTURE_SKEW_SEC):
        outcome = "expired"
    elif fresh and existing.get("no_options"):
        outcome = "no_options"
    elif fresh and expiry and expiry not in (existing.get("expirations") or []):
        outcome = "not_listed"
    elif fresh and (expiry is None or expiry in (existing.get("strikes") or {})):
        outcome = "cached"
    elif _ran_recently(key, lim["dedup_sec"]):
        outcome = "duplicate"
    elif not market_calendar.in_window(WINDOW, now):
        outcome = "closed"
    elif status["ladders_today"] >= lim["ladder_budget"]:
        outcome = "budget"
    else:
        outcome = None
    if outcome is not None:
        _answer(bus, key, outcome, now)
        return

    status["ladders_today"] += 1
    status["busy"] = {"kind": "ladder", "since": now.isoformat()}
    _write_status(bus, status)
    _mark_ran(key)
    try:
        ladder, outcome = _load_ladder(symbol, expiry, existing, now, fresh=fresh)
        if ladder is not None:
            version = bus.cache_set(pr.cache_key(view), ladder,
                                    ttl=lim["ladder_keep_min"] * 60)
            bus.publish(pr.event(view), {"version": version})
    except Exception:  # noqa: BLE001 - one visitor's request must not kill the loop
        _degrade.degraded("options.rescue_public_ladder", detail=symbol)
        outcome = "error"
    _answer(bus, key, outcome, now)


# ── the rescue menu ──────────────────────────────────────────────────────────

def advisory_only(adv):
    """The advisory with every candidate forced to ``advisory``. The engine
    already does this for an ad-hoc trade (``force_advisory=True``); a public
    result carrying an ``execute`` candidate would be a button someone could
    try to wire, so it is enforced again where the result is written."""
    if not isinstance(adv, dict):
        return {"error": "no result"}
    cands = [{**c, "apply_kind": "advisory"} for c in (adv.get("candidates") or [])
             if isinstance(c, dict)]
    out = {k: v for k, v in adv.items() if k != "apply_result"}
    if "candidates" in adv:
        out["candidates"] = cands
    return out


def _handle_compute(bus, command, now, lim, status) -> None:
    args = getattr(command, "args", None) or {}
    spec = pr.clean_spec(args.get("spec"), now.date())
    if spec is None:
        status["invalid_today"] += 1
        _write_status(bus, status)
        return
    key = pr.spec_key(spec)
    view = pr.result_view(key)
    age = _age_s(getattr(command, "ts", None), now)
    held = _age_s((_payload(bus, view) or {}).get("computed_at"), now)
    ladder = _payload(bus, pr.ladder_view(spec["symbol"])) or {}
    # A strikes list answers for the trade only while it is fresh: an old one
    # could refuse an expiration listed since, or carry a stale verdict.
    lheld = _age_s(ladder.get("loaded_at"), now)
    lfresh = lheld is not None and 0 <= lheld < lim["ladder_ttl_min"] * 60
    listed = ladder.get("expirations") if lfresh else None
    on_ladder = (ladder.get("strikes") or {}).get(spec["expiration"]) if lfresh else None
    structure = pr.structure_key(spec)
    if age is not None and (age > lim["max_wait_sec"] or age < -FUTURE_SKEW_SEC):
        outcome = "expired"
    elif held is not None and 0 <= held < lim["result_ttl_min"] * 60:
        outcome = "cached"
    elif lfresh and ladder.get("no_options"):
        outcome = "no_options"
    elif isinstance(listed, list) and listed and spec["expiration"] not in listed:
        outcome = "not_listed"
    elif isinstance(on_ladder, dict) and not pr.strikes_on_ladder(spec, on_ladder):
        outcome = "off_ladder"
    elif _ran_recently(key, lim["dedup_sec"]):
        outcome = "duplicate"
    elif _structure_busy(structure, lim["structure_runs"],
                         lim["result_ttl_min"] * 60):
        outcome = "throttled"
    elif not market_calendar.in_window(WINDOW, now):
        outcome = "closed"
    elif status["computes_today"] >= lim["daily_budget"]:
        outcome = "budget"
    else:
        outcome = None
    if outcome is not None:
        _answer(bus, key, outcome, now)
        return

    status["computes_today"] += 1
    status["busy"] = {"kind": "compute", "since": now.isoformat()}
    _write_status(bus, status)
    _mark_ran(key)
    _count_structure(structure)
    try:
        adv = compute.compute_rescue_adhoc(spec)
        if not isinstance(adv, dict) or adv.get("error"):
            # The engine never raises: every failure comes back as
            # ``{"error": "<ExcType>: <message>"}``. That text is not for a
            # stranger's screen, and a failure must not be cached as a result
            # the next visitor with the same trade is served.
            log.info("public rescue for %s produced no menu: %s", spec["symbol"],
                     adv.get("error") if isinstance(adv, dict) else adv)
            outcome = "error"
        else:
            adv = {**advisory_only(adv), "public": True,
                   "computed_at": now.isoformat()}
            version = bus.cache_set(pr.cache_key(view), adv,
                                    ttl=lim["result_keep_min"] * 60)
            bus.publish(pr.event(view), {"version": version})
            outcome = "done"
    except Exception:  # noqa: BLE001 - one visitor's request must not kill the loop
        _degrade.degraded("options.rescue_public_compute", detail=spec["symbol"])
        outcome = "error"
    _answer(bus, key, outcome, now)


# ── the handler ──────────────────────────────────────────────────────────────

def handle(bus, command) -> None:
    """Answer one ``cmd:rescue_public`` request. Never raises: a Redis error
    anywhere in it, the status read included, is a degrade, not a dead loop."""
    now = _now()
    try:
        _dispatch(bus, command, now)
    except Exception:  # noqa: BLE001 - the loop outlives any one request
        _degrade.degraded("options.rescue_public")
    try:
        # Clear ``busy`` whatever happened, on a fresh read so a count written
        # mid-request is kept.
        latest = _read_status(bus, now)
        if latest.get("busy") is not None:
            latest["busy"] = None
            _write_status(bus, latest)
    except Exception:  # noqa: BLE001 - a Redis error here must not escape either
        _degrade.degraded("options.rescue_public_status")


def _dispatch(bus, command, now) -> None:
    lim = pr.limits()
    status = _read_status(bus, now)
    kind = getattr(command, "type", None)
    if kind == pr.LADDER_TYPE:
        _handle_ladder(bus, command, now, lim, status)
    elif kind == pr.COMPUTE_TYPE:
        _handle_compute(bus, command, now, lim, status)
    else:
        status["invalid_today"] += 1
        log.info("public rescue refused: unknown request type %r", kind)
        _write_status(bus, status)
