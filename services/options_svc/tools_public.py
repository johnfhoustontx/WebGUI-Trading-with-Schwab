"""The public Calculator and Simulator's workers: one visitor request -> one answer.

A visitor on ``live.neuralstrike.co/calculator`` or ``/simulator`` loads a
symbol, builds a position and prices it; the public process puts one validated
command on one of two streams (``shared.public_tools``), each read by its own
consumer loop here (``make_app``'s ``extra_consumers``):

* ``handle_tools`` on ``cmd:tools_public`` - requests that SPEND Schwab calls:
  ``chain`` / ``expiry`` (the shared public chain, exactly Rescue's strikes
  list - ``public_chain.ladder_request``), ``rate`` (Rate My Trade against the
  held chain), ``sim_snapshot`` / ``sim_expiry`` (a Simulator snapshot in
  ``PUBLIC_SIM``, never the owner's ``compute._SIM_SNAPSHOTS``).
* ``handle_math`` on ``cmd:tools_public_math`` - pure pricing over data already
  held: ``price`` (``compute.calc_compute``), ``iv`` (``compute.calc_iv`` off
  the held chain's mark), ``sweep`` (``compute.sim_run``'s what-if half).

⚠ Two loops so a snapshot fetch (seconds of Schwab round trips) never stalls
the reprice a visitor's every edit triggers.

Every request ends in one outcome from ``public_tools.OUTCOMES``, written to
``cache:options:tools_pub_answer:<request key>``; a result goes to
``cache:options:tools_pub:<request key>``. The key is ``public_tools.request_key``
of the CLEAN command, so two visitors making the same request share one run.

Tools refusals, in order, all before any Schwab call:

1. ``invalid``   fails the shared builder (counted; nothing to answer);
2. ``expired``   older than ``max_wait_sec`` or stamped in the future - also the
                 replay guard for a consumer group created at id 0;
3. ``cached``    a fresh result for this request, a held chain, a held snapshot;
4. ``not_listed`` / ``no_options`` / ``load_first``  from what is already held;
5. ``duplicate`` the same request ran under ``dedup_sec`` ago;
6. ``throttled`` (rate only) one trade structure over ``structure_runs`` per
                 ``rate_ttl_min``, whatever prices were typed;
7. ``closed``    outside ``[windows.tools_public]``;
8. ``budget``    the ONE public budget (shared with Rescue) is spent. Checked
                 LAST by ``public_budget.spend``, where the Schwab work starts.

Math refusals: ``invalid`` -> ``expired`` -> ``load_first`` -> ``cached``. No
budget and no window: math spends no Schwab call, and runs on what is held.

⚠ Nothing raw reaches a visitor. An engine's ``{"error": ...}`` (its text is
often an exception message) becomes the outcome ``error`` and is NEVER cached,
so the next visitor with the same request is not served a failure.

⚠ The status view holds COUNTS only - no symbol, strike, expiration or key. The
dedup and structure memories live in this process and forget on restart.

Design: docs/plans/2026-09-21-public-calculator-simulator-design.md.
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
from services.options_svc import handlers
from services.options_svc import public_budget
from services.options_svc import public_chain
from services.options_svc import rate_trade
from shared import market_calendar
from shared import public_rescue as pr
from shared import public_tools as pt

log = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")
WINDOW = "tools_public"
FUTURE_SKEW_SEC = 30
# A ``busy`` marker older than this is a crash's leftover, not a running job.
BUSY_STALE_SEC = 300
# How many keys each in-process memory holds; oldest go first.
DEDUP_KEEP = 2000

# The public Simulator's snapshots: their own store, bounded and expiring, so a
# visitor's load can never replace the owner's snapshot for a symbol. Both the
# cap and the life are read from ``tools_public.toml`` on use, so a value saved
# in Settings applies with no restart.
PUBLIC_SIM = compute.SimStore(
    limit=lambda: pt.limits()["snapshot_limit"],
    ttl_sec=lambda: pt.limits()["snapshot_ttl_min"] * 60)

# The checklist's Paper book line is built on the page from these stamps and the
# OWNER's ledger caps (``checks._book`` -> ``book_fit.preview``). A public rating
# carries neither, so the line cannot be drawn from what this worker publishes.
PAPER_BOOK_STAMPS = ("ledger_risk_basis", "ledger_risk_per_contract")

_RECENT: "collections.OrderedDict[str, float]" = collections.OrderedDict()
_RUNS: "collections.OrderedDict[str, collections.deque]" = collections.OrderedDict()
_RECENT_LOCK = threading.Lock()
# The two consumer threads both count into one status key.
_STATUS_LOCK = threading.Lock()


def _now() -> dt.datetime:
    """The current time in CT. A function so tests can pin the clock."""
    return dt.datetime.now(CT)


def _mono() -> float:
    return time.monotonic()


def reset_memory(*, keep_chains=False, keep_snapshots=False) -> None:
    """Forget the dedup and structure memories and, unless kept, the held chains
    and the public snapshots (tests)."""
    with _RECENT_LOCK:
        _RECENT.clear()
        _RUNS.clear()
    if not keep_chains:
        public_chain.reset()
    if not keep_snapshots:
        PUBLIC_SIM.clear()


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


def _expired(command, now, lim) -> bool:
    age = public_chain.age_s(getattr(command, "ts", None), now)
    return age is not None and (age > lim["max_wait_sec"] or age < -FUTURE_SKEW_SEC)


# ── the status view: counts only ─────────────────────────────────────────────

_COUNTS = ("tools_today", "math_today", "invalid_today")


def _read_status(bus, now) -> dict:
    today = now.date().isoformat()
    env = bus.cache_get(pt.STATUS_KEY)
    status = dict(env.payload) if env and isinstance(env.payload, dict) else {}
    if status.get("date") != today:
        status = {"date": today, **{k: 0 for k in _COUNTS}, "busy": None}
    for k in _COUNTS:
        if not isinstance(status.get(k), int):
            status[k] = 0
    busy = status.get("busy")
    since = public_chain.age_s(busy.get("since"), now) if isinstance(busy, dict) else None
    if since is None or since > BUSY_STALE_SEC:
        status["busy"] = None
    return status


def _write_status(bus, status, now) -> None:
    spent = public_budget.status(bus, now)["spent"]
    status["budget_left"] = max(0, pr.budget() - spent)
    start, end = market_calendar.window_bounds(WINDOW)
    status["window"] = {"start": start.strftime("%H:%M"),
                        "end": end.strftime("%H:%M"), "tz": "CT"}
    version = bus.cache_set(pt.STATUS_KEY, status)
    bus.publish(pt.event(pt.STATUS_VIEW), {"version": version})


_KEEP = object()


def _bump(bus, now, count=None, busy=_KEEP) -> None:
    """Add one to ``count`` and/or set ``busy``, on a fresh read under a lock:
    the tools and math loops write the same key from two threads."""
    with _STATUS_LOCK:
        status = _read_status(bus, now)
        if count is not None:
            status[count] += 1
        if busy is not _KEEP:
            status["busy"] = busy
        _write_status(bus, status, now)


def _clear_busy(bus, now) -> None:
    with _STATUS_LOCK:
        status = _read_status(bus, now)
        if status.get("busy") is not None:
            status["busy"] = None
            _write_status(bus, status, now)


# ── answers and results ──────────────────────────────────────────────────────

def _answer(bus, key, outcome, now) -> None:
    view = pt.answer_view(key)
    version = bus.cache_set(pt.cache_key(view), {"outcome": outcome,
                                                 "at": now.isoformat()},
                            ttl=pt.limits()["answer_keep_min"] * 60)
    bus.publish(pt.event(view), {"version": version})


def _publish(bus, key, payload, ttl_sec) -> None:
    view = pt.result_view(key)
    version = bus.cache_set(pt.cache_key(view), payload, ttl=int(ttl_sec))
    bus.publish(pt.event(view), {"version": version})


def _stamped(bus, key, payload, now, lim) -> None:
    """A result carrying ``computed_at``, kept ``result_keep_min``."""
    _publish(bus, key, {**payload, "public": True, "computed_at": now.isoformat()},
             lim["result_keep_min"] * 60)


def _fresh_result(bus, key, now, ttl_min) -> bool:
    env = bus.cache_get(pt.cache_key(pt.result_view(key)))
    if env is None or not isinstance(env.payload, dict):
        return False
    age = public_chain.age_s(env.payload.get("computed_at"), now)
    return age is not None and 0 <= age < ttl_min * 60


def _is_engine_error(out) -> bool:
    return not isinstance(out, dict) or bool(out.get("error"))


# ── pure helpers ─────────────────────────────────────────────────────────────

def _finite(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def strikes_window(strikes, spot, n):
    """The P&L matrix's price rows: the ``n`` strikes at or below spot plus the
    ``n`` above - ``webgui/pages/options/calculator.strikes_window``'s rule,
    derived HERE so a visitor cannot ask for an arbitrarily large grid. Junk and
    non-finite strikes are ignored and duplicates collapsed."""
    xs = sorted({f for f in (_finite(s) for s in (strikes or [])) if f is not None})
    if not xs or _finite(spot) is None:
        return []
    below = [s for s in xs if s <= spot][-n:]
    above = [s for s in xs if s > spot][:n]
    return below + above


def mark_from_chain(chain, option_type, expiry, strike):
    """One contract's mark from a thinned chain - the mark, else the bid/ask
    midpoint - as a finite positive float, or None. Read here and never
    published: a quote written to Redis is a quote published (decision D1)."""
    map_key = {"call": "callExpDateMap", "put": "putExpDateMap"}.get(option_type)
    exps = (chain or {}).get(map_key) if isinstance(chain, dict) and map_key else None
    if not isinstance(exps, dict):
        return None
    target = _finite(strike)
    for exp_key, strikes in exps.items():
        if str(exp_key).split(":")[0] != expiry or not isinstance(strikes, dict):
            continue
        for s, rows in strikes.items():
            if public_chain._strike_key(s) != target:
                continue
            row = rows[0] if isinstance(rows, list) and rows else None
            if not isinstance(row, dict):
                return None
            mark = _finite(row.get("mark"))
            if mark is not None and mark > 0:
                return mark
            bid, ask = _finite(row.get("bid")), _finite(row.get("ask"))
            if bid is None or ask is None or ask < bid:
                return None
            mid = (bid + ask) / 2.0
            return mid if mid > 0 else None
    return None


def _on_ladder(strikes, expiry, right, strike) -> bool:
    ladder = (strikes.get(expiry) or {}).get(right) or []
    return any(abs(s - strike) < 1e-6 for s in ladder)


def _option_legs(legs):
    return [leg for leg in legs if leg.get("option_type") in ("call", "put")]


def public_row(row):
    """A rated row less the Paper book stamps."""
    return {k: v for k, v in row.items() if k not in PAPER_BOOK_STAMPS}


def _sim_meta(snap):
    """A snapshot's page metadata - expirations, strikes, spot - never a chain."""
    meta = compute._sim_meta(snap)
    meta.pop("chain", None)
    return meta


# ── tools requests ───────────────────────────────────────────────────────────

def _start(bus, now, key, kind) -> None:
    _bump(bus, now, "tools_today", {"kind": kind, "since": now.isoformat()})
    _mark_ran(key)


def _spend(bus, kind, now) -> bool:
    return public_budget.spend(bus, kind, pr.budget(), now)


def _gate(bus, key, now, lim, spend_kind):
    """Refusals 5, 7 and 8 - duplicate, closed, budget - or None when due."""
    if _ran_recently(key, lim["dedup_sec"]):
        return "duplicate"
    if not market_calendar.in_window(WINDOW, now):
        return "closed"
    if not _spend(bus, spend_kind, now):
        return "budget"
    return None


def _tools_chain(bus, command, now, lim, key, args) -> str:
    return public_chain.ladder_request(
        bus, args["symbol"], args.get("expiry"),
        age=public_chain.age_s(getattr(command, "ts", None), now), now=now,
        max_wait_sec=lim["max_wait_sec"], window=WINDOW,
        recently=lambda: _ran_recently(key, lim["dedup_sec"]),
        spend=lambda: _spend(bus, "chain", now),
        start=lambda: _start(bus, now, key, "chain"), area="options.tools_public")


def _tools_rate(bus, command, now, lim, key, args) -> str:
    symbol = args["symbol"]
    held = public_chain.held(symbol)
    structure = pt.structure_key(args)
    listed = (held or {}).get("expirations") or []
    if _expired(command, now, lim):
        return "expired"
    if _fresh_result(bus, key, now, lim["rate_ttl_min"]):
        return "cached"
    if held is None:
        return "load_first"
    if listed and any(leg["expiry"] not in listed for leg in _option_legs(args["legs"])):
        return "not_listed"
    if _ran_recently(key, lim["dedup_sec"]):
        return "duplicate"
    if _structure_busy(structure, lim["structure_runs"], lim["rate_ttl_min"] * 60):
        return "throttled"
    outcome = _gate(bus, key, now, lim, "rate")
    if outcome is not None:
        return outcome
    _start(bus, now, key, "rate")
    _count_structure(structure)
    try:
        out = rate_trade.rate(symbol, args["structure"], args["legs"], held,
                              market_state=handlers._market_state(bus))
        if _is_engine_error(out) or not isinstance(out.get("row"), dict):
            log.info("public rating for %s produced no row: %s", symbol,
                     out.get("error") if isinstance(out, dict) else out)
            return "error"
        _stamped(bus, key, {"row": public_row(out["row"])}, now, lim)
        return "done"
    except Exception:  # noqa: BLE001 - one visitor's request must not kill the loop
        _degrade.degraded("options.tools_public_rate", detail=symbol)
        return "error"


def _tools_sim_snapshot(bus, command, now, lim, key, args) -> str:
    symbol = args["symbol"]
    if _expired(command, now, lim):
        return "expired"
    held = PUBLIC_SIM.get(symbol)
    if held is not None:
        # ⚠ Never re-fetch a held symbol: that would replace another visitor's
        # snapshot and drop the expirations they added. The ttl retires it.
        _stamped(bus, key, dict(_sim_meta(held),
                                expirations=PUBLIC_SIM.expirations_of(symbol)),
                 now, lim)
        return "cached"
    chain = public_chain.published(bus, symbol) or {}
    age = public_chain.age_s(chain.get("loaded_at"), now)
    if chain.get("no_options") and age is not None \
            and 0 <= age < pr.limits()["ladder_ttl_min"] * 60:
        return "no_options"
    outcome = _gate(bus, key, now, lim, "sim_snapshot")
    if outcome is not None:
        return outcome
    _start(bus, now, key, "sim_snapshot")
    try:
        meta = compute.sim_fetch(symbol, lazy=True, expiries=args.get("expiries"),
                                 store=PUBLIC_SIM)
        if isinstance(meta, dict):
            meta.pop("chain", None)        # discarded: never published
        if not isinstance(meta, dict) or not meta.get("n_contracts"):
            # An empty snapshot held would answer every later request
            # ``cached`` with nothing to price; drop exactly the one we put.
            PUBLIC_SIM.discard_if(symbol, PUBLIC_SIM.get(symbol))
            log.info("public snapshot for %s came back empty", symbol)
            return "error"
        _stamped(bus, key, meta, now, lim)
        return "done"
    except Exception:  # noqa: BLE001 - one visitor's request must not kill the loop
        _degrade.degraded("options.tools_public_snapshot", detail=symbol)
        return "error"


def _tools_sim_expiry(bus, command, now, lim, key, args) -> str:
    symbol, expiry = args["symbol"], args["expiry"]
    if _expired(command, now, lim):
        return "expired"
    snap = PUBLIC_SIM.get(symbol)
    if snap is None:
        return "load_first"
    if expiry in compute.expiries_of(snap):
        _stamped(bus, key, dict(_sim_meta(snap),
                                expirations=PUBLIC_SIM.expirations_of(symbol)),
                 now, lim)
        return "cached"
    # With no expiration list (the eager fallback) nothing can be added.
    if expiry not in (PUBLIC_SIM.expirations_of(symbol) or []):
        return "not_listed"
    outcome = _gate(bus, key, now, lim, "sim_expiry")
    if outcome is not None:
        return outcome
    _start(bus, now, key, "sim_expiry")
    try:
        meta = compute.sim_fetch_expiry(symbol, expiry, store=PUBLIC_SIM)
        if meta is None:
            return "load_first"            # evicted or replaced mid-fetch
        meta.pop("chain", None)
        _stamped(bus, key, meta, now, lim)
        return "done"
    except Exception:  # noqa: BLE001 - one visitor's request must not kill the loop
        _degrade.degraded("options.tools_public_sim_expiry", detail=symbol)
        return "error"


_TOOLS = {"chain": _tools_chain, "expiry": _tools_chain, "rate": _tools_rate,
          "sim_snapshot": _tools_sim_snapshot, "sim_expiry": _tools_sim_expiry}


def _dispatch_tools(bus, command, now) -> None:
    lim = pt.limits()
    today = now.date()
    clean = (pt.tools_command(getattr(command, "args", None), today)
             if getattr(command, "type", None) == pt.TOOLS_TYPE else None)
    key = pt.request_key(clean, today) if clean is not None else None
    if clean is None or key is None:
        _bump(bus, now, "invalid_today")
        log.info("public tools request refused: invalid %r",
                 getattr(command, "type", None))
        return
    args = clean["args"]
    outcome = _TOOLS[args["kind"]](bus, command, now, lim, key, args)
    if outcome == "budget":
        # The budget is shared: Rescue may have spent it, so this view's
        # ``budget_left`` is refreshed here rather than left reading stale.
        _bump(bus, now)
    _answer(bus, key, outcome, now)


def handle_tools(bus, command) -> None:
    """Answer one ``cmd:tools_public`` request. Never raises: a Redis error
    anywhere in it, the status read included, is a degrade, not a dead loop."""
    now = _now()
    try:
        _dispatch_tools(bus, command, now)
    except Exception:  # noqa: BLE001 - the loop outlives any one request
        _degrade.degraded("options.tools_public")
    try:
        # Clear ``busy`` whatever happened, on a fresh read so a count written
        # mid-request is kept.
        _clear_busy(bus, now)
    except Exception:  # noqa: BLE001 - a Redis error here must not escape either
        _degrade.degraded("options.tools_public_status")


# ── math requests ────────────────────────────────────────────────────────────

def _math_price(bus, now, lim, key, args) -> str:
    held = public_chain.held(args["symbol"])
    if held is None:
        return "load_first"
    if _fresh_result(bus, key, now, lim["result_ttl_min"]):
        return "cached"
    strikes = public_chain.strikes_from_chain(held.get("chain"))
    legs = args["legs"]
    options = _option_legs(legs)
    if not all(_on_ladder(strikes, leg["expiry"], leg["option_type"], leg["strike"])
               for leg in options):
        return "off_ladder"
    # The matrix's price axis, derived here: the ±N strikes of the FRONT leg's
    # expiration (the horizon calc_compute prices to), calls and puts together.
    front = min((leg["expiry"] for leg in options), default=args["expiry"])
    ladder = strikes.get(front) or {}
    rows = strikes_window(sorted(set(ladder.get("call") or [])
                                 | set(ladder.get("put") or [])),
                          args["spot"], args["num_strikes"])
    out = compute.calc_compute(
        strategy=args["strategy"], spot=args["spot"], iv=args["iv"],
        rate=args["rate"], ivadj=args["ivadj"], qty=args["qty"],
        expiry=args["expiry"], legs=[dict(leg) for leg in legs],
        num_strikes=args["num_strikes"], price_rows=rows or None)
    if _is_engine_error(out):
        log.info("public price for %s produced no result", args["symbol"])
        return "error"
    _stamped(bus, key, out, now, lim)
    return "done"


def _math_iv(bus, now, lim, key, args) -> str:
    held = public_chain.held(args["symbol"])
    if held is None:
        return "load_first"
    # No ``computed_at`` here: the result is exactly ``{"iv": x}``, so it lives
    # only ``result_ttl_min`` and its presence is what "fresh" means.
    if bus.cache_get(pt.cache_key(pt.result_view(key))) is not None:
        return "cached"
    spot = _finite(held.get("price"))
    mark = mark_from_chain(held.get("chain"), args["option_type"], args["expiry"],
                           args["strike"])
    if spot is None or spot <= 0 or mark is None:
        return "error"
    out = compute.calc_iv(spot, args["strike"], args["option_type"], mark,
                          args["expiry"])
    iv = _finite(out.get("iv")) if isinstance(out, dict) else None
    if iv is None:
        return "error"
    # ⚠ The IV and nothing else: the mark it came from is a quote.
    _publish(bus, key, {"iv": iv}, lim["result_ttl_min"] * 60)
    return "done"


def _math_sweep(bus, now, lim, key, args) -> str:
    symbol = args["symbol"]
    snap = PUBLIC_SIM.get(symbol)
    if snap is None:
        return "load_first"
    if _fresh_result(bus, key, now, lim["result_ttl_min"]):
        return "cached"
    if any(compute.find_contract(snap, leg["expiry"], leg["kind"], leg["strike"])
           is None for leg in args["legs"]):
        return "off_ladder"
    out = compute.sim_run(symbol, legs=[dict(leg) for leg in args["legs"]],
                          dt=args["dt"], store=PUBLIC_SIM)
    if not out:
        return "load_first"                # the snapshot went since the check
    if _is_engine_error(out):
        return "error"
    # The what-if half only: the Volatility tab (``ivshock``) is not published.
    _stamped(bus, key, {k: out.get(k) for k in
                        ("whatif_rows", "whatif_baseline", "spot", "legs", "dt")},
             now, lim)
    return "done"


_MATH = {"price": _math_price, "iv": _math_iv, "sweep": _math_sweep}


def _dispatch_math(bus, command, now) -> None:
    lim = pt.limits()
    today = now.date()
    clean = (pt.math_command(getattr(command, "args", None), today)
             if getattr(command, "type", None) == pt.MATH_TYPE else None)
    key = pt.request_key(clean, today) if clean is not None else None
    if clean is None or key is None:
        _bump(bus, now, "invalid_today")
        return
    args = clean["args"]
    if _expired(command, now, lim):
        _answer(bus, key, "expired", now)
        return
    try:
        outcome = _MATH[args["kind"]](bus, now, lim, key, args)
    except Exception:  # noqa: BLE001 - one visitor's request must not kill the loop
        _degrade.degraded(f"options.tools_public_{args['kind']}", detail=args["symbol"])
        outcome = "error"
    if outcome in ("done", "error"):
        _bump(bus, now, "math_today")
    _answer(bus, key, outcome, now)


def handle_math(bus, command) -> None:
    """Answer one ``cmd:tools_public_math`` request. Never raises.

    Math never sets ``busy`` (it is not a long job, and clearing it here would
    wipe a tools request's marker from the other loop), so there is no status
    clear to guard - only the one ``try``."""
    try:
        _dispatch_math(bus, command, _now())
    except Exception:  # noqa: BLE001 - the loop outlives any one request
        _degrade.degraded("options.tools_public_math")
