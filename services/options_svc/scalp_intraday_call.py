"""The Opening Scalp's intraday calls: a new, frozen 0DTE call every 15 minutes.

Every slot from 08:51 to 13:51 CT (``scalp_config.intraday_slots``) ``run_slot``
makes a brand-new call for $SPX, SPY and QQQ - a scalp entered AT that slot - and
publishes it into the ``intraday`` section of ``cache:options:scalp``, beside the
08:36 call, which it never touches (design 2026-10-20-scalp-intraday-calls,
sections 3, 5, 8 and 10). Per symbol the call carries:

* the slot's price reading (``scalp_intraday_inputs.reading``: the last 15
  minutes' direction, the inputs and which agree, the alignment bucket), its
  fields at the top level of the entry as in the 08:36 entry;
* each role's rates from the intraday study for the slot's time block and the
  bucket (``scalp_intraday_study.current_rates``) - IN-SAMPLE over every stored
  day (``rates_basis``), with the walk-forward verdict as ``edge``. The sample
  unit is the day: ``n_days`` / ``n_base_days``. Usually there is no edge, and
  ``note`` then says ``"no measured edge"``;
* the role's strikes, entry premium and points to the target and the stop,
  priced exactly as the 08:36 call prices them (``scalp_call._price``), and the
  walls in the path to each (``wall_context``);
* the confirmation reading (``scalp_confirm.components``: flow, net premium, the
  dealer read, sentiment) and its ``dealer_line``. It is SHOWN and recorded and
  NEVER changes a rate, a highlight or a price: it is built after them, from
  sources the rates never read. The dealer read measures the symbol's room
  from the call's own ``entry_spot``;
* ``made_at`` (the wall clock when the entry was built; the slot's window is
  judged by the scheduler's tick) and the frozen ``rules`` its grade will be
  walked under.

Rules, each from the design or a review ruling:

* **One run at a time, with the 08:36 call.** ``run_slot`` holds
  ``scalp_call._RUN_LOCK`` (never re-entered: nothing here calls ``scalp_call.run``
  or ``republish_scorecard``). The second caller waits, then finds the slot frozen.
* **A slot is made only in its own window**: from the slot's time to its time +
  ``[intraday] grace_min``. A refresh never makes an off-slot call (no entry ever
  falls between slots); outside a window ``run_slot`` republishes what today
  already published, or nothing. On a holiday or a weekend it does nothing.
* **A published call stays as published.** A (day, slot, symbol) already in
  ``intraday_calls`` is reused WHOLE - not re-read, not re-priced, its chain not
  fetched. A new call is saved (with its chain slice, in ``intraday_chain_slices``
  - never the 08:36 ``chain_slices``) only after the WHOLE view, with the new
  ``intraday`` section, has validated against ``ScalpView`` and been written to
  the bus. What is frozen: a call (it has roles) and a flat tape (a settled
  answer). A symbol with bars missing, or whose build failed, is not frozen: a
  refresh inside the window may still fill it.
* **No call after its trade is over**: after the slot's entry + ``time_stop_min``
  a symbol gets no roles and the note ``MISSED``, its reading kept; nothing is
  fetched for it. On an early close (``[intraday] early_close_days``) a slot
  whose entry + ``time_stop_min`` would pass the close is the same, with the
  note ``EARLY_CLOSE``, and ``next_slot`` skips it.
* **Fetch only what a new call needs, on the normal lane**: today's 5-minute bars
  for every bar symbol (``scalp_fetch.top_up_today``: one request each), then the
  0DTE chain of each symbol being built that HAS a direction.
* **Publishing merges.** The current view is republished with only ``intraday``
  replaced: the 08:36 ``symbols`` and every other key stay byte for byte.
* **Recording.** After a slot is published and saved, ``scalp_record.record``
  saves the slot's confirmation inputs with each call's direction, entry spot
  and the long-with-the-move role's points to target (a distance: a long put's
  points are negative). A slot that could not be published is still recorded, with no
  direction. A record failure never touches the call.

The section: ``{"day", "slot", "latest": {symbol: entry}, "timeline": [{"slot",
"symbols": {symbol: {"direction", "highlight", "word", "edge_p", "edge_p_base",
"n_days", "note"}}}], "next_slot", "time_stop_min"}`` - one timeline row per slot
published today, oldest first; ``edge_p`` / ``edge_p_base`` / ``n_days`` are the
highlighted role's rate, base rate and distinct-day count (``None`` with no
highlight); ``next_slot`` is when the next call is made (ISO, CT);
``time_stop_min`` is the configured time stop, which the page uses to end a slot
that made no trade. Imports nothing from ``compute``. Never raises.
"""
import datetime as _dt
import logging
import time
from zoneinfo import ZoneInfo

from services import _degrade
from shared import market_calendar as _mc
from shared import scalp_config
from shared import symbols as _symbols
from shared.contracts.options import ScalpView
from shared.numeric import finite as _finite

from services.options_svc import (scalp_call, scalp_chain, scalp_confirm, scalp_fetch,
                                  scalp_intraday_inputs, scalp_intraday_study,
                                  scalp_model, scalp_record, scalp_sim, scalp_store)

log = logging.getLogger("options_svc.scalp_intraday_call")

VIEW = scalp_call.VIEW
EVENT = scalp_call.EVENT

NO_EDGE = scalp_call.NO_EDGE
NO_VOL = scalp_call.NO_VOL
FAILED = scalp_call.FAILED
MISSED = scalp_call.MISSED
FLAT = "flat over the last 15 minutes — no direction"
NO_BARS = "no bars for the last 15 minutes yet"
EARLY_CLOSE = "the market closes early today"
#: What the shown rates are computed over (labelled on every entry).
RATES_BASIS = "all stored days"
#: The degrade area every guard here counts under.
_AREA = "options.scalp_intraday"

_CT = ZoneInfo("America/Chicago")


# ── the clock ────────────────────────────────────────────────────────────────
def _ct_now(now):
    if now is None:
        return _dt.datetime.now(_CT)
    return now.replace(tzinfo=_CT) if now.tzinfo is None else now.astimezone(_CT)


def _wall_now():
    """The wall clock, Central: when an entry was actually built (``made_at``).
    The slot's window is judged by the scheduler's tick, which can be earlier
    by the wait for ``_RUN_LOCK``."""
    return _dt.datetime.now(_CT)


def current_slot(now=None):
    """The slot (``"HH:MM"``) whose window holds ``now`` (default: now, CT) -
    from its time up to, not including, its time + ``[intraday] grace_min``, as
    the scheduler fires it - or ``None`` (between windows, or not a trading day)."""
    now = _ct_now(now)
    day = now.date()
    if not _mc.is_trading_day(day):
        return None
    return next((s for s in scalp_config.intraday_slots(day) if _in_window(day, s, now)),
                None)


def _in_window(day, slot, now) -> bool:
    """``slot`` is one of ``day``'s slots and ``now`` is inside its window."""
    if slot not in scalp_config.intraday_slots(day):
        return False
    at = scalp_config.intraday_slot_time(day, slot)
    return at <= now < at + _dt.timedelta(minutes=scalp_config.intraday_grace_min())


def _callable_slots(day) -> list:
    """``day``'s slots a call can be made at: on an early close, not those whose
    trade would run past it (``scalp_config.intraday_slot_cut``)."""
    return [s for s in scalp_config.intraday_slots(day)
            if not scalp_config.intraday_slot_cut(day, s)]


def _next_slot(day, now):
    """When the next call is made (ISO, CT): today's next slot a call can be
    made at, else the next trading day's first."""
    for slot in _callable_slots(day):
        at = scalp_config.intraday_slot_time(day, slot)
        if at > now:
            return at.isoformat()
    nxt = _mc.next_trading_day(day)
    slots = _callable_slots(nxt)
    return scalp_config.intraday_slot_time(nxt, slots[0]).isoformat() if slots else None


def _late(day, slot, now) -> bool:
    """True once the slot's trade is over: entry + ``time_stop_min``."""
    entry = scalp_config.intraday_entry_ts(day, slot)
    return now.timestamp() >= entry + scalp_config.time_stop_min() * 60


# ── one symbol ───────────────────────────────────────────────────────────────
def _why_no_direction(symbol, day, entry_ts, db_path) -> str:
    """``FLAT`` when the symbol's last 15 minutes are all there and stayed inside
    the dead zone, else ``NO_BARS`` (no 08:30 bar or one of the three missing)."""
    sii = scalp_intraday_inputs
    bars = sii._session(symbol, day, entry_ts, db_path, None)
    open_ts = sii._open_ts(day)
    has_open = sii._bar_ok(bars.get(open_ts))
    if has_open and sii._move15(bars, entry_ts, open_ts) is not None:
        return FLAT
    return NO_BARS


def _role(rate, structure, priced) -> dict:
    """The 08:36 role shape (``scalp_call._role``) with the intraday study's day
    counts under their own names."""
    out = scalp_call._role(dict(rate, n=rate.get("n_days"), n_base=rate.get("n_base_days")),
                           structure, priced)
    out["n_days"] = out.pop("n")
    out["n_base_days"] = out.pop("n_base")
    return out


def _sigma(entry, slice_, vol):
    """The chain's ATM volatility at the slice's own ``underlyingPrice``, else the
    vol index x ``iv_scale``; ``None`` (and a ``points_note``) with neither."""
    iv = entry["chain"]["atm_iv"] if entry["chain"] else None
    if iv is not None:
        entry["sigma_source"] = "chain"
        return iv
    if vol is not None and vol > 0:
        entry["sigma_source"] = "model"
        return vol * scalp_config.iv_scale()
    entry["points_note"] = NO_VOL
    return None


def _symbol_call(symbol, day, slot, r, slice_, src, db_path, late=False,
                 cut=False) -> dict:
    """A fresh entry for ``symbol`` at ``slot`` from its reading ``r``, the study
    and ``slice_`` (None: the model prices it). ``late``: the reading is kept and
    no roles are made (``MISSED``). ``cut``: the session closes early before the
    trade would end; the reading is kept, no roles, ``EARLY_CLOSE``."""
    align = scalp_intraday_inputs.alignment(r)
    bucket = scalp_model.bucket_of(align, scalp_config.buckets())
    block = scalp_config.intraday_block(slot)
    if slice_ is not None and not scalp_call._expiry_rows(slice_, "callExpDateMap", day):
        slice_ = None           # no expiry dated today: nothing 0DTE to read
    iv = None
    if slice_ is not None:
        ref = _finite(slice_.get("underlyingPrice"))
        iv = scalp_chain.atm_iv(slice_, ref) if ref else None
    entry = dict(scalp_call._reading(r, align, bucket), slot=slot, block=block,
                 rates_basis=RATES_BASIS, chain=scalp_call._chain_summary(slice_, iv, day),
                 sigma=None, sigma_source=None, roles={}, highlight=None, avoid=[],
                 note=None, points_note=None, confirmation=None, dealer_line=None)
    if cut:
        entry["note"] = EARLY_CLOSE
        return entry
    if r.direction not in (1, -1):
        entry["note"] = _why_no_direction(symbol, day, r.entry_ts, db_path)
        return entry
    if late:
        entry["note"] = MISSED
        return entry

    entry["rules"] = scalp_call._call_rules()
    sigma = _sigma(entry, slice_, entry["vol_at_entry"])
    entry["sigma"] = _finite(sigma)
    rates = (scalp_intraday_study.current_rates(symbol, block, bucket, db_path=db_path)
             if block is not None else {})
    spot = entry["entry_spot"]
    when = scalp_fetch.ct(r.entry_ts)
    for role in scalp_sim.ROLES:
        structure = scalp_sim.structure_for(role, r.direction)
        priced = ({} if sigma is None or spot is None else
                  scalp_call._price(symbol, structure, spot, when, day, sigma, slice_))
        out = _role(rates.get(role) or {}, structure, priced)
        win, loss = out["win_points"], out["loss_points"]
        out["wall_context"] = scalp_confirm.wall_context(
            src, symbol, spot, None if win is None else spot + win,
            None if loss is None else spot + loss) if spot is not None else []
        entry["roles"][role] = out
    scalp_call._verdict(entry)

    # The confirmation is read AFTER every rate and price is fixed, and changes
    # none of them: it is shown and recorded, never in the probability.
    win = _finite(entry["roles"]["long_with"].get("win_points"))
    conf = scalp_confirm.components(src, symbol, r.direction,
                                    None if win is None else abs(win), r.entry_ts,
                                    spot=spot)
    entry["confirmation"] = conf
    entry["dealer_line"] = conf.get("dealer_line")
    return entry


def _failed_entry(slot) -> dict:
    return {"slot": slot, "block": scalp_config.intraday_block(slot), "direction": None,
            "roles": {}, "chain": None, "highlight": None, "avoid": [], "note": FAILED,
            "confirmation": None, "dealer_line": None}


def _freezes(entry) -> bool:
    """A call (it has roles) or a flat tape: what the slot settled. A symbol
    whose bars were missing, whose build failed or that was missed is not."""
    return scalp_call._is_call(entry) or (isinstance(entry, dict)
                                          and entry.get("note") == FLAT)


# ── build ────────────────────────────────────────────────────────────────────
def _fetch(day, symbols_needing_chains, db_path, client) -> dict:
    """``{symbol: (ts, slice | None)}``: each symbol's 0DTE chain slice (saved
    nowhere here)."""
    out = {}
    for symbol in symbols_needing_chains:
        # fetch_slice guards itself: a failure is None (the model prices it).
        out[symbol] = (int(time.time()),
                       scalp_chain.fetch_slice(symbol, day.isoformat(), client=client))
    return out


def _top_up(day, db_path, client):
    """Today's bars for every bar symbol. Returns the symbols whose fetch failed
    (a set), or ``None`` when the top-up itself failed (no symbol is known
    fresh)."""
    try:
        failed = {k.rsplit("/", 1)[0] for k, n in scalp_fetch.top_up_today(
            scalp_config.bar_symbols(), 5, db_path=db_path, client=client,
            today=day).items() if n == scalp_fetch.FAILED}
        if failed:
            log.warning("scalp intraday: bar top-up failed for %s",
                        ", ".join(sorted(failed)))
        return failed
    except Exception:
        _degrade.degraded(_AREA, detail="top-up")
        return None


def _inputs_of(symbol) -> list:
    """Every bar symbol ``symbol``'s reading reads: itself, its futures, sector
    funds and vol index, $VIX, its VWAP stand-in and the big ten."""
    wiring = scalp_config.inputs_for(symbol)
    names = [symbol, wiring["futures"], *wiring["etfs"], wiring["vol"],
             scalp_intraday_inputs._VIX,
             scalp_intraday_inputs._VWAP_PROXY.get(symbol), *_symbols.big10()]
    return [n for n in dict.fromkeys(names) if n]


def _mark_inputs(entry, symbol, failed) -> None:
    """Name the inputs this entry was built without fresh bars for
    (``inputs_failed``, sorted; all of them when the top-up itself failed) and
    say so in ``inputs_note``: a call made on fewer inputs says it was."""
    wanted = _inputs_of(symbol)
    missing = sorted(wanted if failed is None else [n for n in wanted if n in failed])
    entry["inputs_failed"] = missing
    entry["inputs_note"] = (f"built on fewer inputs: no fresh bars for {', '.join(missing)}"
                            if missing else None)


def _build(day, slot, now, db_path, client, stored, src):
    """``(entries, slices, fresh)``: every symbol's entry for the slot, the chain
    slices fetched, and the symbols built now (not reused). ``now`` is the
    scheduler's tick (it judged the window); ``made_at`` is the wall clock."""
    late = _late(day, slot, now)
    cut = scalp_config.intraday_slot_cut(day, slot)
    entries, readings, fresh = {}, {}, []
    todo = [s for s in scalp_config.symbols() if (slot, s) not in stored]
    ctx = scalp_call.chance_context(db_path) if todo else None   # one load per slot
    failed = set()
    if todo and not (late or cut):
        failed = _top_up(day, db_path, client)
    for symbol in todo:
        try:
            readings[symbol] = scalp_intraday_inputs.reading(symbol, day, slot,
                                                             db_path=db_path)
        except Exception:
            _degrade.degraded(_AREA, detail=f"reading {symbol}")
    chained = [] if late or cut else [s for s, r in readings.items()
                                      if r.direction in (1, -1)]
    slices = _fetch(day, chained, db_path, client) if chained else {}

    for symbol in scalp_config.symbols():
        if (slot, symbol) in stored:
            entries[symbol] = stored[(slot, symbol)]
            continue
        fresh.append(symbol)
        try:
            if symbol not in readings:
                raise RuntimeError("no reading")
            entry = _symbol_call(symbol, day, slot, readings[symbol],
                                 slices.get(symbol, (None, None))[1], src, db_path,
                                 late=late, cut=cut)
        except Exception:
            _degrade.degraded(_AREA, detail=f"symbol {symbol}")
            entry = _failed_entry(slot)
        scalp_call.attach_chance("intraday", symbol, entry, day, src, ctx, db_path)
        if not (late or cut):
            _mark_inputs(entry, symbol, failed)
            entry["made_at"] = _wall_now().isoformat()
        entries[symbol] = entry
    return entries, slices, fresh


def build_slot(day, slot, now, db_path=None, client=None, stored=None, src=None) -> dict:
    """``{symbol: entry}`` for ``day``'s ``slot`` at ``now``. A symbol in
    ``stored`` (default: the saved ``intraday_calls``) is reused whole; the others
    are built fresh, fetching bars and chains only inside the trade's window
    (``MISSED`` after it). ``src`` is ``scalp_confirm.sources``'s answer (default:
    none, every confirmation component absent). Each fresh call also carries its
    fitted chance of target (``scalp_call.attach_chance``), computed from the same
    ``src`` after its verdict. Saves nothing."""
    now = _ct_now(now)
    if stored is None:
        stored = scalp_store.intraday_calls_on(day.isoformat(), db_path=db_path)
    return _build(day, slot, now, db_path, client, stored, src or {})[0]


# ── the view ─────────────────────────────────────────────────────────────────
def _cell_chance(entry, named):
    """The named trade's fitted chance from the entry's ``chance`` (``None`` when
    it has none)."""
    chance = entry.get("chance")
    by_role = chance.get("by_role") if isinstance(chance, dict) else None
    return _finite(by_role.get(named)) if isinstance(by_role, dict) else None


def _cell(entry) -> dict:
    """One symbol's compact timeline cell. ``chance`` is the named trade's: the
    highlighted role's when there is one, else the long-with-the-move trade's."""
    entry = entry if isinstance(entry, dict) else {}
    hl = entry.get("highlight")
    role = (entry.get("roles") or {}).get(hl) if hl else None
    conf = entry.get("confirmation")
    return {"direction": entry.get("direction"), "highlight": hl,
            "word": conf.get("word") if isinstance(conf, dict) else None,
            "edge_p": _finite(role.get("p")) if isinstance(role, dict) else None,
            "edge_p_base": _finite(role.get("p_base")) if isinstance(role, dict) else None,
            "n_days": _finite(role.get("n_days")) if isinstance(role, dict) else None,
            "chance": _cell_chance(entry, hl or "long_with"),
            "note": entry.get("note")}


def _section(day, now, slot, latest, stored) -> dict:
    """The ``intraday`` section: ``latest`` for ``slot`` and one timeline row per
    slot published today (the stored ones and this one), oldest first."""
    merged = dict(stored)
    merged.update({(slot, sym): e for sym, e in latest.items()})
    order = {s: i for i, s in enumerate(scalp_config.symbols())}
    timeline = []
    for s in sorted({k[0] for k in merged}):
        syms = sorted((sym for (sl, sym) in merged if sl == s),
                      key=lambda x: (order.get(x, len(order)), x))
        timeline.append({"slot": s, "symbols": {sym: _cell(merged[(s, sym)])
                                                for sym in syms}})
    # time_stop_min: the page reads a slot of no trades (all flat or missing
    # bars) as over at its entry + this, as a call's own frozen rules do.
    return {"day": day.isoformat(), "slot": slot, "latest": latest,
            "timeline": timeline, "next_slot": _next_slot(day, now),
            "time_stop_min": scalp_config.time_stop_min()}


def _valid(payload) -> bool:
    try:
        ScalpView(**payload)
    except Exception:  # noqa: BLE001 - a bad payload is reported, never raised.
        _degrade.degraded(_AREA, detail="view")
        return False
    return True


def _publish(bus, day, now, section, db_path) -> bool:
    """Merge ``section`` into the CURRENT view (every other key untouched) and
    publish it; with no view yet, under the last saved 08:36 call. False when it
    did not validate or the bus refused it."""
    view = scalp_call._payload(bus, VIEW)
    base = view if view is not None else scalp_call._held("call", bus, day, now, db_path)
    payload = dict(base, intraday=section)
    if not _valid(payload):
        return False
    try:
        bus.cache_set(VIEW, payload, event=EVENT)
    except Exception:
        _degrade.degraded(_AREA, detail="publish")
        return False
    return True


def _save(day, slot, entries, slices, fresh, db_path) -> None:
    """Freeze what was just published and not stored yet: the entry, and its
    chain slice beside it."""
    iso = day.isoformat()
    for symbol in fresh:
        entry = entries.get(symbol)
        if not _freezes(entry):
            continue
        try:
            stamp, sliced = slices.get(symbol, (None, None))
            if sliced is not None:
                scalp_store.save_intraday_slice(iso, slot, symbol, stamp, sliced,
                                                db_path=db_path)
            scalp_store.save_intraday_call(iso, slot, symbol, entry, db_path=db_path)
        except Exception:
            _degrade.degraded(_AREA, detail=f"save {symbol}")


def _record(bus, day, slot, now, entries, db_path, src=None) -> None:
    """The slot's confirmation inputs (``scalp_record.record``, write-once save
    the upgrade to a direction), read from ``src`` - the SAME sources the
    confirmation was built from, so the row is what the page showed - with each
    published call's direction and its long-with role's points to target as a
    distance. ``entries`` empty: recorded with no direction."""
    try:
        directions, win_points, spots = {}, {}, {}
        for symbol, entry in (entries or {}).items():
            if scalp_call._is_call(entry) and entry.get("direction") in (1, -1):
                directions[symbol] = entry["direction"]
                win = _finite((entry["roles"].get("long_with") or {}).get("win_points"))
                win_points[symbol] = None if win is None else abs(win)
                spots[symbol] = _finite(entry.get("entry_spot"))
        scalp_record.record(bus, day, slot, now=now, db_path=db_path,
                            directions=directions, win_points=win_points,
                            entry_ts=scalp_config.intraday_entry_ts(day, slot), src=src,
                            spots=spots)
    except Exception:
        _degrade.degraded(_AREA, detail="record")


# ── run ──────────────────────────────────────────────────────────────────────
def run_slot(bus, slot, now=None, db_path=None, client=None, scheduled=False) -> bool:
    """Make and publish ``slot``'s call when ``now`` (default: now, CT, read
    BEFORE the lock is taken, so a wait for the 08:36 call cannot push a due slot
    out of its window) is inside that slot's window; otherwise republish the
    latest slot today already published (``slot=None`` asks for just that).
    ``scheduled``: the scheduler fired this slot, so finding it outside its
    window is a fault, counted as a degrade. Holds ``scalp_call._RUN_LOCK``.
    Returns True when it published. Never raises."""
    now = _ct_now(now)
    with scalp_call._RUN_LOCK:
        try:
            return _run_slot(bus, slot, now, db_path, client, scheduled)
        except Exception:
            _degrade.degraded(_AREA, detail="run")
            return False


def run_scheduled(bus, slot, now, db_path=None, client=None) -> bool:
    """The scheduler's entry: ``run_slot`` at the scheduler's own tick time
    ``now`` (the moment it found the slot due), flagged as scheduled."""
    return run_slot(bus, slot, now=now, db_path=db_path, client=client, scheduled=True)


def _run_slot(bus, slot, now, db_path, client, scheduled=False) -> bool:
    day = now.date()
    if not _mc.is_trading_day(day):
        return False
    try:
        stored = scalp_store.intraday_calls_on(day.isoformat(), db_path=db_path)
    except Exception:
        _degrade.degraded(_AREA, detail="store")
        return False

    if slot is None or not _in_window(day, slot, now):
        if scheduled:
            _degrade.degraded(_AREA, detail=f"scheduled slot {slot} outside its window")
        # Off a slot's window: never a new call, only what today published.
        if not stored:
            return False
        last = max(s for s, _sym in stored)
        latest = {sym: e for (s, sym), e in stored.items() if s == last}
        return _publish(bus, day, now, _section(day, now, last, latest, stored), db_path)

    src = scalp_confirm.sources(bus, now)
    published = False
    entries = {}
    try:
        entries, slices, fresh = _build(day, slot, now, db_path, client, stored, src)
        published = _publish(bus, day, now, _section(day, now, slot, entries, stored),
                             db_path)
        if published:
            # Only now is it what the page was shown: freeze it.
            _save(day, slot, entries, slices, fresh, db_path)
    except Exception:
        _degrade.degraded(_AREA, detail=f"slot {slot}")
    _record(bus, day, slot, now, entries if published else {}, db_path, src=src)
    return published


__all__ = ["VIEW", "EVENT", "FLAT", "NO_BARS", "MISSED", "EARLY_CLOSE", "RATES_BASIS",
           "current_slot", "build_slot", "run_slot", "run_scheduled"]
