"""The Opening Scalp's morning call: measured inputs + stored rates -> one view.

At 08:36 CT (``[slots.scalp]``) and on the page's refresh (``scalp_refresh``)
``run`` publishes ``cache:options:scalp``, the ONLY view the Opening Scalp page
reads. For each scalp symbol it carries the morning (direction, inputs, which
agreed, the alignment bucket), each role's win rate over every stored morning
(``scalp_study.current_rates``) with the study's walk-forward-tested verdict on
whether the bucket beats its base rate, and that morning's strikes, entry
premium and the underlying points to the target and the stop.

Rules that keep the number honest, each from the design or a review ruling:

* **A published call stays as published.** ``run`` saves a symbol's call in
  ``scalp_store``'s ``calls`` table only once it has passed ``ScalpView`` and
  been written to the bus, stamped ``made_at``. A later build of the same day
  reuses that entry WHOLE: nothing re-priced, its chain not fetched again (so
  its saved slice is never replaced). Grading reads ``calls``.
* **No late first call.** A symbol with no published call by entry +
  ``time_stop_min`` (09:35 CT at the defaults) gets no roles: its trade is over,
  and after the 16:20 rebuild the study's rates hold the day's own outcome. A
  morning that had a direction is refused (``"missed the call window"``); one
  with no opening bar or a flat open still says so, with its inputs (the morning
  reads nothing after 08:35, so it leaks no outcome).
* **A call keeps its rules.** Each called symbol's entry carries ``rules``
  (target, stop, time stop, minimum premium, ``iv_scale``) as they were when it
  was made; the evening's grading walks the trade under those.
* **Every fresh call carries its chance of target**, frozen with it. After the
  verdict ``attach_chance`` (``scalp_prob.chance``, the newest stored weights read
  once per build) writes ``entry["chance"]`` and each role's ``chance``; the
  sources are the ones ``run`` read once and the 08:36 record saves. It changes no
  rate, price or highlight. An entry with no roles has ``chance`` ``None``;
  ``[prob] show`` off writes no ``chance`` key at all (the page's "off", against
  ``version`` ``None``, "not fitted yet"). A refit never touches a published call.
* **One run at a time.** ``run`` and ``republish_scorecard`` hold one lock: the
  08:36 slot and a page refresh never build (and fetch) side by side.
* **No edge is said plainly.** The stored verdict is two-sided. ``highlight`` is
  the role with an edge AND a rate above its base rate (the highest rate of
  those); a role with an edge BELOW its base rate is listed in ``avoid``. With
  no highlight the note reads ``"no measured edge"`` and the page shows base
  rates, never a bucket rate dressed as a call.
* **Absent stays absent.** A symbol with no opening bar or a flat one gets no
  direction, a note saying why, its inputs where there are any, and no roles;
  the others are still called. A failed chain prices from the model
  (``$VIX``/``$VXN`` x ``iv_scale``) with ``chain: None``; with no volatility at
  all a symbol keeps its rates but gets no strikes or points.
* **Entry is the 08:35 boundary** (``Morning.entry_ts``), never the wall clock:
  the study enters there, so the strikes and points must too.
* **Live premium where the chain has it**: a role's ``entry_value`` is the slice's
  mid ((bid + ask) / 2, else the mark) at its strikes - short minus long for a
  spread - and ``entry_source`` says ``"chain"``; otherwise it is the model's and
  says ``"model"``. Both legs of a spread come from ONE root (never an SPX leg
  against an SPXW leg), and only an expiry dated today is read. The point
  distances are always the model's, at the chain's ATM volatility when it has
  one.

``scorecard`` (every payload, whatever its state) is the graded record
``scalp_score`` builds from ``grades``: how the highlighted calls and the cautions
actually did over the last ``scorecard_days`` graded days, beside the base rates
they were published with. ``scalp_grade`` writes it each night, and
``republish_scorecard`` then puts the fresh record into the current view (the
symbols untouched), so a day's grades show that evening.

Before the call time on a trading day (the later of the 08:35 entry and the
``[slots.scalp]`` time: 08:36 at the defaults) the view is ``"waiting"``, on a
closed day ``"closed"``; both keep the last published call under ``symbols``
(``call_day`` says which day) and fetch NOTHING. An unexpected failure publishes ``"error"`` the
same way, counted as a degrade. Every payload, whatever its state, carries the
current view's ``intraday`` section forward unchanged (``scalp_intraday_call``
builds it; this module never does). Every payload is checked against
``shared.contracts.options.ScalpView`` first; one that fails is not published.

``context`` (the reports link, index headlines from ``cache:news:feed``, today's
flow alerts on the three symbols since 08:30) is carried for the page to show as
NOT in the probability. Both feeds are private views; this view is read only by
the private app. Imports nothing from ``compute``.
"""
import datetime as _dt
import logging
import re
import threading
from zoneinfo import ZoneInfo

from services import _degrade
from shared import market_calendar as _mc
from shared import scalp_config
from shared.contracts.options import ScalpView
from shared.numeric import finite as _finite

from services.options_svc import (scalp_chain, scalp_confirm, scalp_fetch, scalp_inputs,
                                  scalp_model, scalp_prob, scalp_record, scalp_score,
                                  scalp_sim, scalp_store, scalp_study)

log = logging.getLogger("options_svc.scalp_call")

VIEW = "cache:options:scalp"
EVENT = "events:options:scalp"
NEWS_VIEW = "cache:news:feed"
FLOW_VIEW = "cache:options:flow_alerts"

NO_EDGE = "no measured edge"
NO_OPEN_BAR = "no opening bar yet"
FLAT_OPEN = "opened flat — no direction"
NO_VOL = "no usable volatility: no strikes or points"
FAILED = "the call could not be built"
MISSED = "missed the call window"

#: A headline counts as about the index when it carries one of these tickers.
_INDEX_TICKERS = frozenset({"SPY", "QQQ", "SPX", "$SPX", "ES", "/ES"})
#: Minutes after the open the call waits for: the first five-minute bar.
_ENTRY_AFTER_MIN = 5

_CT = ZoneInfo("America/Chicago")
_CALLS = ("long_call", "ccs")       # structures priced off the call map
#: The 0DTE weekly root (scalp_chain.preferred_contract reads it the same way).
_WEEKLY_ROOT = "SPXW"
_ROOT_RE = re.compile(r"[A-Z]+")

#: ONE run (and one scorecard republish) at a time. The 08:36 slot runs on the
#: scheduler's executor and a ``scalp_refresh`` on the slow lane's thread; two
#: builds at once would both see no frozen call, both fetch (~58 requests in one
#: minute) and could publish one run's entry beside the other's saved slice. The
#: second caller waits, then finds the calls frozen and fetches nothing.
_RUN_LOCK = threading.Lock()


# ── small helpers ────────────────────────────────────────────────────────────
def _ct_now(now):
    if now is None:
        return _dt.datetime.now(_CT)
    return now.replace(tzinfo=_CT) if now.tzinfo is None else now.astimezone(_CT)


def _entry_time(day) -> _dt.datetime:
    return _mc.regular_open_on(day) + _dt.timedelta(minutes=_ENTRY_AFTER_MIN)


def _call_time(day) -> _dt.datetime:
    """The earliest a call is BUILT: the later of the 08:35 entry and the first
    ``[slots.scalp]`` time (08:36 at the defaults). Before it the view is
    ``"waiting"`` - a refresh at 08:35:30 must not fire the call a minute early,
    before the 08:30 bar has settled and while the slot is about to run."""
    entry = _entry_time(day)
    slots = sorted(_mc.slot_times("scalp").values())
    if not slots:
        return entry
    return max(entry, _dt.datetime.combine(day, slots[0], tzinfo=_CT))


def _window_end(day) -> _dt.datetime:
    """After this a NEW call is refused: the trade it would describe has ended,
    and by the evening the study's rates hold the day's own outcome."""
    return _entry_time(day) + _dt.timedelta(minutes=scalp_config.time_stop_min())


def _is_call(entry) -> bool:
    """A stored entry that is a real call (it has roles), and so is frozen."""
    return (isinstance(entry, dict) and isinstance(entry.get("roles"), dict)
            and bool(entry["roles"]))


def _int(v):
    f = _finite(v)
    return None if f is None else int(f)


def _rules() -> dict:
    """The trade rules the numbers were made under, shown with the call."""
    return {"target_pct": scalp_config.target_pct(), "stop_pct": scalp_config.stop_pct(),
            "time_stop_min": scalp_config.time_stop_min(),
            "otm_steps": scalp_config.otm_steps(), "iv_scale": scalp_config.iv_scale(),
            "min_samples": scalp_config.min_samples()}


def _call_rules() -> dict:
    """The rules a GRADE depends on, frozen into each called symbol's entry (so
    saved with it): the evening's grading walks the trade under these, never
    under a config changed after the call (``scalp_grade.call_rules``)."""
    return {"target_pct": scalp_config.target_pct(), "stop_pct": scalp_config.stop_pct(),
            "time_stop_min": scalp_config.time_stop_min(),
            "min_premium": scalp_config.min_premium(), "iv_scale": scalp_config.iv_scale()}


def _next_call(day, state):
    """When the next scheduled call fires (ISO, CT): today's slot while waiting,
    else the next trading day's."""
    slots = sorted(_mc.slot_times("scalp").values())
    if not slots:
        return None
    on = day if state == "waiting" else _mc.next_trading_day(day)
    return _dt.datetime.combine(on, slots[0], tzinfo=_CT).isoformat()


# ── the chain side ───────────────────────────────────────────────────────────
def _expiry_rows(slice_, name, day) -> dict:
    """One map's ``{strike_key: [contract, ...]}`` for ``day``'s expiry; ``{}``
    when the slice has no expiry dated ``day`` (a later expiry is not 0DTE and
    must never be priced as one)."""
    maps = slice_.get(name) if isinstance(slice_, dict) else None
    if not isinstance(maps, dict):
        return {}
    for key, rows in maps.items():
        if str(key).startswith(day.isoformat()) and isinstance(rows, dict):
            return rows
    return {}


def _quote_mid(contract):
    """(bid + ask) / 2 for a two-sided market (a zero bid is still one), else a
    positive mark, else None."""
    bid, ask = _finite(contract.get("bid")), _finite(contract.get("ask"))
    if bid is not None and ask is not None and 0 <= bid <= ask and ask > 0:
        return (bid + ask) / 2.0
    mark = _finite(contract.get("mark"))
    return mark if mark is not None and mark > 0 else None


def _root(contract) -> str:
    """An option symbol's root (``SPXW`` of ``"SPXW  261019C05000000"``)."""
    m = _ROOT_RE.match(str(contract.get("symbol") or "").strip().upper())
    return m.group(0) if m else ""


def _quoted(rows, strike) -> list:
    """The contracts at ``strike`` with a usable mid, SPXW first."""
    for key, contracts in rows.items():
        try:
            k = float(key)
        except (TypeError, ValueError):
            continue
        if abs(k - strike) < 1e-6 and isinstance(contracts, list):
            usable = [c for c in contracts
                      if isinstance(c, dict) and _quote_mid(c) is not None]
            return sorted(usable, key=lambda c: _root(c) != _WEEKLY_ROOT)
    return []


def chain_legs(slice_, day, structure, strikes):
    """``(short, long_leg)`` contracts the slice quotes for the structure, or
    None when it lacks a strike. ``long_leg`` is None for a long option (the
    contract bought is ``short``, the first strike). A spread's two legs are on
    ONE root: on a third Friday a $SPX strike can list SPX and SPXW, and a pair
    across them is two different contracts' prices, not a spread. Grading reads
    the same legs to imply a volatility."""
    if slice_ is None:
        return None
    rows = _expiry_rows(slice_, "callExpDateMap" if structure in _CALLS
                        else "putExpDateMap", day)
    first, second = strikes
    for short in _quoted(rows, first):
        if second is None:
            return short, None
        long_leg = next((c for c in _quoted(rows, second)
                         if _root(c) == _root(short)), None)
        if long_leg is not None:
            return short, long_leg
    return None


def _chain_value(slice_, day, structure, strikes):
    """The slice's mid for the structure, or None when it lacks a strike or the
    value is not positive (a spread is short mid - long mid; see ``chain_legs``)."""
    legs = chain_legs(slice_, day, structure, strikes)
    if legs is None:
        return None
    short, long_leg = legs
    v = _quote_mid(short)
    if long_leg is not None:
        v -= _quote_mid(long_leg)
    return v if v > 0 else None


def _chain_summary(slice_, iv, day):
    if slice_ is None:
        return None
    maps = slice_.get("callExpDateMap") or {}
    expiry = next((k for k in maps if str(k).startswith(day.isoformat())), None)
    return {"spot": _finite(slice_.get("underlyingPrice")), "atm_iv": iv,
            "expiry": expiry}


# ── one symbol ───────────────────────────────────────────────────────────────
def _reading(m, align, bucket) -> dict:
    return {"direction": m.direction, "bucket": bucket, "alignment": _finite(align),
            "entry_spot": _finite(m.entry_spot), "entry_ts": _int(m.entry_ts),
            "inputs": {k: _finite(v) for k, v in m.inputs.items()},
            "agree": {k: _int(v) for k, v in m.agree.items()},
            "vol_at_entry": _finite(m.vol_at_entry), "vol_source": m.vol_source}


def _role(rate, structure, priced) -> dict:
    out = {"structure": structure,
           "p": _finite(rate.get("p")), "lo": _finite(rate.get("lo")),
           "hi": _finite(rate.get("hi")), "n": _int(rate.get("n")) or 0,
           "p_base": _finite(rate.get("p_base")), "n_base": _int(rate.get("n_base")) or 0,
           "edge": bool(rate.get("edge")),
           "strikes": None, "entry_value": None, "entry_source": None,
           "win_points": None, "loss_points": None}
    out.update(priced)
    return out


def _price(symbol, structure, spot, entry, day, sigma, slice_) -> dict:
    """The role's strikes, entry premium (chain mid, else model) and points."""
    first, second = scalp_sim.strikes_for(
        structure, spot, scalp_config.strike_step(symbol), scalp_config.width(symbol),
        scalp_config.otm_steps())
    live = _chain_value(slice_, day, structure, (first, second))
    if live is not None:
        value, source = live, "chain"
    else:
        value = _finite(scalp_sim.value(structure, spot, (first, second), entry, day,
                                        sigma))
        source = "model" if value is not None else None
    win, loss = scalp_sim.level_points(structure, spot, (first, second), entry, day,
                                       sigma)
    return {"strikes": [float(s) for s in (first, second) if s is not None],
            "entry_value": value, "entry_source": source,
            "win_points": _finite(win), "loss_points": _finite(loss)}


def _symbol_call(symbol, day, slice_, db_path, late=False) -> dict:
    """A fresh call for ``symbol`` from the store, the study and ``slice_``.

    ``late`` (past the call window) still reads the morning - ``morning`` is pure
    and reads nothing after 08:35, so it holds no later outcome - and keeps a no
    opening bar or a flat open as what it is, with its inputs. Only a morning that
    HAS a direction is ``MISSED``. No roles are ever made late."""
    m = scalp_inputs.morning(symbol, day, 5, db_path=db_path)
    align = scalp_inputs.alignment(m)
    reading = _reading(m, align, scalp_model.bucket_of(align, scalp_config.buckets()))
    if slice_ is not None and not _expiry_rows(slice_, "callExpDateMap", day):
        slice_ = None           # no expiry dated today: nothing 0DTE to read
    spot = reading["entry_spot"]
    iv = None
    if slice_ is not None:
        ref = spot or _finite(slice_.get("underlyingPrice"))
        iv = scalp_chain.atm_iv(slice_, ref) if ref else None
    entry = dict(reading, chain=_chain_summary(slice_, iv, day), sigma=None,
                 sigma_source=None, roles={}, highlight=None, avoid=[], note=None,
                 points_note=None)
    if m.open_bar is None:
        entry["note"] = NO_OPEN_BAR
        return entry
    if reading["direction"] not in (1, -1):
        entry["note"] = FLAT_OPEN
        return entry
    if late:
        entry["note"] = MISSED
        return entry

    entry["rules"] = _call_rules()
    vol = reading["vol_at_entry"]
    if iv is not None:
        sigma, entry["sigma_source"] = iv, "chain"
    elif vol is not None and vol > 0:
        sigma, entry["sigma_source"] = vol * scalp_config.iv_scale(), "model"
    else:
        sigma = None
        entry["points_note"] = NO_VOL
    entry["sigma"] = _finite(sigma)

    rates = scalp_study.current_rates(symbol, reading["bucket"], db_path=db_path)
    when = scalp_fetch.ct(reading["entry_ts"])
    for role in scalp_sim.ROLES:
        structure = scalp_sim.structure_for(role, reading["direction"])
        priced = ({} if sigma is None else
                  _price(symbol, structure, spot, when, day, sigma, slice_))
        entry["roles"][role] = _role(rates.get(role) or {}, structure, priced)
    _verdict(entry)
    return entry


def _verdict(entry) -> None:
    """``highlight``, ``avoid`` and ``note`` from the roles. The stored edge is
    two-sided: a bucket that reliably LOSES more often than usual has one too.
    Only an edge ABOVE the base rate is a call (the highest such rate wins); an
    edge below it names a role to avoid. No call -> ``"no measured edge"``."""
    roles = entry["roles"]

    def side(r):
        p, base = roles[r]["p"], roles[r]["p_base"]
        if not roles[r]["edge"] or p is None or base is None:
            return 0
        return 1 if p > base else -1 if p < base else 0

    better = [r for r in scalp_sim.ROLES if side(r) > 0]
    entry["avoid"] = [r for r in scalp_sim.ROLES if side(r) < 0]
    entry["highlight"] = max(better, key=lambda r: roles[r]["p"]) if better else None
    entry["note"] = None if better else NO_EDGE


# ── the fitted chance of target ──────────────────────────────────────────────
def chance_context(db_path):
    """What one build shares across its symbols' chances: the newest stored
    weights, read ONCE, and a trend-bar cache. ``None`` when the chance is
    switched off (``[prob] show``): nothing is then computed and no ``chance``
    key is written, which is how the page tells "off" from "not fitted yet"
    (``chance`` with ``version`` None)."""
    if not scalp_config.prob_show():
        return None
    try:
        models = scalp_store.latest_prob_models(db_path=db_path)
    except Exception:
        _degrade.degraded("options.scalp_prob", detail="models")
        models = {}
    return {"models": models, "trend_cache": {}}


def attach_chance(kind, symbol, entry, day, src, ctx, db_path) -> None:
    """Give a FRESH ``entry`` its ``chance`` (``scalp_prob.chance``) and each role
    its own, AFTER the verdict: nothing else in the entry is read back or
    changed. An entry with no roles (flat, missed, failed, early close) gets
    ``None``. ``src`` is ``scalp_confirm.sources``' answer, or ``None`` (every
    source factor is then a gap); the snapshot is the one ``scalp_record.record``
    builds for the same call. A stored entry is never passed here: it is reused
    whole, its chance included. ``ctx`` ``None`` is "switched off". Never raises."""
    if ctx is None:
        return
    try:
        if not _is_call(entry):
            entry["chance"] = None
            return
        snapshot = (scalp_confirm.snapshot(src, scalp_config.symbols(),
                                           entry_ts=entry.get("entry_ts"), symbol=symbol)
                    if src else None)
        got = scalp_prob.chance(kind, symbol, entry, snapshot, day, db_path=db_path,
                                models=ctx["models"], trend_cache=ctx["trend_cache"])
        entry["chance"] = got
        for role, body in entry["roles"].items():
            body["chance"] = (got.get("by_role") or {}).get(role)
    except Exception:
        _degrade.degraded("options.scalp_prob", detail=f"attach {symbol}")
        entry["chance"] = None


def _scorecard(db_path) -> dict:
    """The graded record (``scalp_score``) every payload carries, or ``{}``: the
    calls stay on screen when the record cannot be read."""
    try:
        return scalp_score.scorecard(db_path=db_path)
    except Exception:
        _degrade.degraded("options.scalp_scorecard")
        return {}


# ── build and publish ────────────────────────────────────────────────────────
def build(day: _dt.date, now: _dt.datetime, db_path=None, client=None,
          src=None) -> dict:
    """The view's payload for ``day`` (``context`` is ``run``'s: it reads the bus).

    A symbol whose call was already PUBLISHED today is reused whole from
    ``calls``: nothing re-priced, its chain not fetched again. A symbol with no
    published call gets a fresh one, unless the call window (entry +
    ``time_stop_min``) has passed: then a morning with a direction is refused
    (``MISSED``), because the trade has ended and the study may already hold its
    outcome, while a morning with no opening bar or a flat one still says so,
    with its inputs (no roles either way). Bars are topped up and chains
    captured only when some symbol gets a fresh call inside the window, and only
    that symbol's chain. Nothing is saved here: ``run`` saves what it actually
    published. One symbol failing never stops the others.

    ``src`` is ``scalp_confirm.sources``' answer, read by ``run`` once (the same
    one the 08:36 record saves). Each FRESH call's chance of target is computed
    from it after its verdict (``attach_chance``); with ``src`` ``None`` (a test,
    an old caller) every source factor is a gap, and this never reads the bus."""
    iso = day.isoformat()
    frozen = {s: e for s, e in scalp_store.calls_on(iso, db_path=db_path).items()
              if _is_call(e)}
    late = now >= _window_end(day)
    todo = [s for s in scalp_config.symbols() if s not in frozen and not late]
    slices = {}
    if todo:
        try:
            failed = [k for k, n in scalp_fetch.top_up(db_path=db_path, client=client,
                                                        today=day).items()
                      if n == scalp_fetch.FAILED]
            if failed:
                log.warning("scalp call: bar top-up failed for %s", ", ".join(failed))
        except Exception:
            _degrade.degraded("options.scalp_topup")
        for symbol in todo:
            # capture() guards itself: a failure is None (the model prices it).
            slices[symbol] = scalp_chain.capture(symbol, iso, client=client,
                                                 db_path=db_path)

    symbols = {}
    ctx = chance_context(db_path) if len(frozen) < len(scalp_config.symbols()) else None
    for symbol in scalp_config.symbols():
        if symbol in frozen:
            symbols[symbol] = frozen[symbol]
            continue
        try:
            entry = _symbol_call(symbol, day, slices.get(symbol), db_path, late=late)
        except Exception:
            _degrade.degraded("options.scalp_symbol", detail=symbol)
            entry = {"direction": None, "roles": {}, "chain": None, "highlight": None,
                     "avoid": [], "note": FAILED}
        attach_chance("morning", symbol, entry, day, src, ctx, db_path)
        if not late:
            entry["made_at"] = now.isoformat()
        symbols[symbol] = entry
    return {"day": iso, "call_day": iso, "generated_at": now.isoformat(),
            "state": "call", "symbols": symbols, "rules": _rules(), "context": {},
            "scorecard": _scorecard(db_path), "next_call": None}


def _save_published(payload, db_path) -> None:
    """Freeze every call just published that is not stored yet (a stored one was
    reused whole, so it is the same entry)."""
    day = payload.get("day")
    try:
        stored = scalp_store.calls_on(day, db_path=db_path)
        for symbol, entry in (payload.get("symbols") or {}).items():
            if _is_call(entry) and symbol not in stored:
                scalp_store.save_call(day, symbol, entry, db_path=db_path)
    except Exception:
        _degrade.degraded("options.scalp_save")


def _last_call(bus, db_path):
    """``(day, symbols)`` of the most recent published call (``calls`` holds only
    those), else the current view's."""
    try:
        last = scalp_store.latest_call_day(db_path=db_path)
        if last is not None:
            return last, scalp_store.calls_on(last, db_path=db_path)
    except Exception:
        log.warning("scalp call: the saved calls could not be read", exc_info=True)
    view = _payload(bus, VIEW) or {}
    symbols = view.get("symbols")
    return (view.get("call_day") or view.get("day"),
            symbols if isinstance(symbols, dict) else {})


def _held(state, bus, day, now, db_path) -> dict:
    """A payload that publishes no new call: the last one, under a state line."""
    call_day, symbols = _last_call(bus, db_path)
    try:
        nxt = _next_call(day, state)
    except Exception:
        nxt = None
    return {"day": day.isoformat(), "call_day": call_day,
            "generated_at": now.isoformat(), "state": state, "symbols": symbols,
            "context": context(bus, day), "scorecard": _scorecard(db_path),
            "next_call": nxt}


def _valid(payload) -> bool:
    try:
        ScalpView(**payload)
    except Exception:  # noqa: BLE001 - a bad payload is reported, never raised.
        _degrade.degraded("options.scalp_view")
        return False
    return True


def run(bus, now=None, db_path=None, client=None) -> None:
    """Publish the view for ``now`` (default: now, CT): ``"closed"`` on a
    non-trading day, ``"waiting"`` before the call time (``_call_time``: 08:36 at
    the defaults), else a freshly built ``"call"``. One run at a time
    (``_RUN_LOCK``): a second caller waits for the first and then reuses its
    frozen calls. Never raises."""
    with _RUN_LOCK:
        _run(bus, now, db_path, client)


def _run(bus, now, db_path, client) -> None:
    now = _ct_now(now)
    day = now.date()
    built = False
    src = None
    try:
        if not _mc.is_trading_day(day):
            payload = _held("closed", bus, day, now, db_path)
        elif now < _call_time(day):
            payload = _held("waiting", bus, day, now, db_path)
        else:
            src = scalp_confirm.sources(bus, now)      # read ONCE: the call and its record
            payload = build(day, now, db_path=db_path, client=client, src=src)
            payload["context"] = context(bus, day)
            payload["next_call"] = _next_call(day, "call")
            built = True
    except Exception:
        _degrade.degraded("options.scalp_call")
        payload = _held("error", bus, day, now, db_path)
    _keep_intraday(bus, payload)
    if not _valid(payload):
        return
    try:
        bus.cache_set(VIEW, payload, event=EVENT)
    except Exception:
        _degrade.degraded("options.scalp_publish")
        return
    if built:
        # Only now is it what the page was shown: freeze it.
        _save_published(payload, db_path)
        _record_call_slot(bus, day, now, payload, db_path, src=src)


def _keep_intraday(bus, payload) -> None:
    """Carry the CURRENT view's ``intraday`` section (``scalp_intraday_call``'s)
    into ``payload`` unchanged, whatever its state: this module never builds that
    section, and a republish here must not drop it until the next slot.

    Only a section that validates ON ITS OWN is carried: one that no longer
    passes the contract (a promote that tightened it, say) would otherwise make
    every payload here fail, and the 08:36 call would never publish. Such a
    section is dropped and counted (``options.scalp_intraday``); the next slot
    publishes a fresh one. A section of an earlier day is still carried: it
    names its ``day``, and the page compares."""
    current = _payload(bus, VIEW)
    if current is None or "intraday" not in current:
        return
    section = current["intraday"]
    try:
        ScalpView(intraday=section)
    except Exception:  # noqa: BLE001 - a bad section is dropped, never raised.
        _degrade.degraded("options.scalp_intraday", detail="carried section")
        return
    payload["intraday"] = section


def _record_call_slot(bus, day, now, payload, db_path, src=None) -> None:
    """Record the confirmation inputs at the call's own moment (``"08:36"`` at the
    defaults), with each called symbol's direction and the long-with-the-move
    role's points to target, so the 08:36 row is scored like an intraday slot.

    Only a run inside the slot's grace records: a page refresh at 10:00 builds
    too (and reuses the frozen calls), but what the sources say then is not what
    the call saw, and it must not replace the 08:36 row. A failure here never
    touches the call: it is already published and saved, and this is counted as
    ``options.scalp_record``. ``src`` is the sources the call (and its chance) was
    built from, so the recorded row is exactly what they saw."""
    try:
        at = _call_time(day)
        if now >= at + _dt.timedelta(minutes=_mc.slot_grace_min("scalp")):
            return
        directions, win_points, spots = {}, {}, {}
        for symbol, entry in (payload.get("symbols") or {}).items():
            if _is_call(entry) and entry.get("direction") in (1, -1):
                directions[symbol] = entry["direction"]
                # A distance: a down morning's long put has negative points,
                # and the dealer read measures room against the magnitude.
                win = _finite((entry["roles"].get("long_with") or {}).get("win_points"))
                win_points[symbol] = None if win is None else abs(win)
                # ... from the call's own entry price, not the matrix's later spot
                spots[symbol] = _finite(entry.get("entry_spot"))
        scalp_record.record(bus, day, at.strftime("%H:%M"), now=now, db_path=db_path,
                            directions=directions, win_points=win_points,
                            entry_ts=int(_entry_time(day).timestamp()), spots=spots,
                            src=src)
    except Exception:
        _degrade.degraded("options.scalp_record", detail="call")


def republish_scorecard(bus, db_path=None) -> bool:
    """After the evening's grading: republish the CURRENT view with a fresh
    ``scalp_score.scorecard()`` in place of its ``scorecard`` and nothing else
    changed - no build, no fetch, the frozen symbols byte for byte - so the day's
    grades show that evening, not at the next morning's publish. Holds the run
    lock. No view yet, an unreadable record or an invalid payload publishes
    nothing (counted as a degrade where it is a failure). Returns True when it
    published. Never raises."""
    with _RUN_LOCK:
        view = _payload(bus, VIEW)
        if view is None:
            return False
        try:
            card = scalp_score.scorecard(db_path=db_path)
        except Exception:
            _degrade.degraded("options.scalp_scorecard")
            return False
        payload = {k: v for k, v in view.items() if k != "intraday"}
        payload["scorecard"] = card
        _keep_intraday(bus, payload)     # a stale section never blocks the record
        if not _valid(payload):
            return False
        try:
            bus.cache_set(VIEW, payload, event=EVENT)
        except Exception:
            _degrade.degraded("options.scalp_publish")
            return False
        return True


# ── context: shown beside the call, NOT in the probability ──────────────────
def _payload(bus, key):
    try:
        env = bus.cache_get(key)
    except Exception:
        log.warning("scalp call: %s could not be read", key, exc_info=True)
        return None
    payload = getattr(env, "payload", None)
    return payload if isinstance(payload, dict) else None


def _tickers(item) -> list:
    raw = item.get("tickers")
    return [t.strip().upper() for t in raw if isinstance(t, str)] \
        if isinstance(raw, list) else []


def _news(bus) -> list:
    n = scalp_config.news_items()
    items = (_payload(bus, NEWS_VIEW) or {}).get("items")
    if n <= 0 or not isinstance(items, list):
        return []
    rows = [i for i in items if isinstance(i, dict)]
    tagged = [i for i in rows if _INDEX_TICKERS & set(_tickers(i))]
    return [{"title": str(i.get("title") or ""), "url": str(i.get("url") or ""),
             "source": str(i.get("source") or ""),
             "published_at": str(i.get("published_at") or ""),
             "tickers": _tickers(i)}
            for i in (tagged or rows)[:n]]


def _flow(bus, day) -> list:
    payload = _payload(bus, FLOW_VIEW) or {}
    alerts = payload.get("alerts")
    if payload.get("date") != day.isoformat() or not isinstance(alerts, list):
        return []
    start = int(_mc.regular_open_on(day).timestamp())
    wanted = set(scalp_config.symbols())
    out = []
    for a in alerts:
        if not isinstance(a, dict) or a.get("symbol") not in wanted:
            continue
        stamp = _int(a.get("ts"))
        if stamp is None or stamp < start:
            continue
        out.append({"ts": stamp, "symbol": a["symbol"], "type": str(a.get("type") or ""),
                    "side": str(a.get("side") or ""), "text": str(a.get("text") or "")})
    return sorted(out, key=lambda a: a["ts"])


def context(bus, day) -> dict:
    """The reports link, index headlines and today's flow alerts on the scalp
    symbols since the open. A missing or malformed source is an empty list."""
    return {"report_url": scalp_config.report_url(), "news": _news(bus),
            "flow": _flow(bus, day)}


__all__ = ["VIEW", "EVENT", "NO_EDGE", "build", "run", "republish_scorecard", "context"]
