"""The Opening Scalp's morning call: measured inputs + stored rates -> one view.

At 08:36 CT (``[slots.scalp]``) and on the page's refresh (``scalp_refresh``)
``run`` publishes ``cache:options:scalp``, the ONLY view the Opening Scalp page
reads. For each scalp symbol it carries the morning (direction, inputs, which
agreed, the alignment bucket), each role's walk-forward-tested win rate over the
stored study (``scalp_study.current_rates``), and that morning's strikes, entry
premium and the underlying points to the target and the stop.

Rules that keep the number honest, each from the design or a review ruling:

* **The probability is frozen when it is shown.** ``build`` saves every symbol's
  call in ``scalp_store``'s ``calls`` table, and a later build of the same day
  (a refresh after 16:20, when the nightly rebuild has added today's outcome to
  the study) reuses the stored morning and rates and only re-prices the chain
  side. Grading reads ``calls``; nothing recomputes "what was shown".
* **No edge is said plainly.** ``highlight`` is the role whose stored verdict has
  an edge, highest rate first; with none it is ``None`` and the note reads
  ``"no measured edge"``. The page then shows base rates, never a bucket rate
  dressed as a call.
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
  says ``"model"``. The point distances are always the model's, at the chain's
  ATM volatility when it has one.

Before 08:35 on a trading day the view is ``"waiting"``, on a closed day
``"closed"``; both keep the last saved call under ``symbols`` (``call_day`` says
which day) and fetch NOTHING. An unexpected failure publishes ``"error"`` the
same way, counted as a degrade. Every payload is checked against
``shared.contracts.options.ScalpView`` first; one that fails is not published.

``context`` (the reports link, index headlines from ``cache:news:feed``, today's
flow alerts on the three symbols since 08:30) is carried for the page to show as
NOT in the probability. Both feeds are private views; this view is read only by
the private app. Imports nothing from ``compute``.
"""
import datetime as _dt
import logging
from zoneinfo import ZoneInfo

from services import _degrade
from shared import market_calendar as _mc
from shared import scalp_config
from shared.contracts.options import ScalpView
from shared.numeric import finite as _finite

from services.options_svc import (scalp_chain, scalp_fetch, scalp_inputs, scalp_model,
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

#: A headline counts as about the index when it carries one of these tickers.
_INDEX_TICKERS = frozenset({"SPY", "QQQ", "SPX", "$SPX", "ES", "/ES"})
#: The study's answer per role, frozen with the call.
_RATE_KEYS = ("p", "lo", "hi", "n", "p_base", "n_base", "edge")
#: The morning's reading, frozen with the call (the rates were looked up for it).
_MORNING_KEYS = ("direction", "bucket", "alignment", "entry_spot", "entry_ts",
                 "inputs", "agree", "vol_at_entry", "vol_source")
#: Minutes after the open the call waits for: the first five-minute bar.
_ENTRY_AFTER_MIN = 5

_CT = ZoneInfo("America/Chicago")
_CALLS = ("long_call", "ccs")       # structures priced off the call map


# ── small helpers ────────────────────────────────────────────────────────────
def _ct_now(now):
    if now is None:
        return _dt.datetime.now(_CT)
    return now.replace(tzinfo=_CT) if now.tzinfo is None else now.astimezone(_CT)


def _entry_time(day) -> _dt.datetime:
    return _mc.regular_open_on(day) + _dt.timedelta(minutes=_ENTRY_AFTER_MIN)


def _int(v):
    f = _finite(v)
    return None if f is None else int(f)


def _rules() -> dict:
    """The trade rules the numbers were made under, frozen with the call."""
    return {"target_pct": scalp_config.target_pct(), "stop_pct": scalp_config.stop_pct(),
            "time_stop_min": scalp_config.time_stop_min(),
            "otm_steps": scalp_config.otm_steps(), "iv_scale": scalp_config.iv_scale(),
            "min_samples": scalp_config.min_samples()}


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
    """One map's ``{strike_key: [contract, ...]}`` for ``day``'s expiry."""
    maps = slice_.get(name) if isinstance(slice_, dict) else None
    if not isinstance(maps, dict):
        return {}
    for key, rows in maps.items():
        if str(key).startswith(day.isoformat()) and isinstance(rows, dict):
            return rows
    only = [v for v in maps.values() if isinstance(v, dict)]
    return only[0] if len(only) == 1 else {}


def _quote_mid(contract):
    """(bid + ask) / 2 for a two-sided market (a zero bid is still one), else a
    positive mark, else None."""
    bid, ask = _finite(contract.get("bid")), _finite(contract.get("ask"))
    if bid is not None and ask is not None and 0 <= bid <= ask and ask > 0:
        return (bid + ask) / 2.0
    mark = _finite(contract.get("mark"))
    return mark if mark is not None and mark > 0 else None


def _leg_mid(rows, strike):
    for key, contracts in rows.items():
        try:
            k = float(key)
        except (TypeError, ValueError):
            continue
        if abs(k - strike) < 1e-6:
            c = scalp_chain.preferred_contract(
                contracts, ok=lambda c: _quote_mid(c) is not None)
            return None if c is None else _quote_mid(c)
    return None


def _chain_value(slice_, day, structure, strikes):
    """The slice's mid for the structure (a spread: short mid - long mid), or
    None when it lacks a strike or the value is not positive."""
    if slice_ is None:
        return None
    rows = _expiry_rows(slice_, "callExpDateMap" if structure in _CALLS
                        else "putExpDateMap", day)
    first, second = strikes
    v = _leg_mid(rows, first)
    if v is not None and second is not None:
        long_leg = _leg_mid(rows, second)
        v = None if long_leg is None else v - long_leg
    return v if v is not None and v > 0 else None


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


def _symbol_call(symbol, day, slice_, stored, db_path) -> dict:
    m = scalp_inputs.morning(symbol, day, 5, db_path=db_path)
    align = scalp_inputs.alignment(m)
    reading = _reading(m, align, scalp_model.bucket_of(align, scalp_config.buckets()))
    rates = None
    if isinstance(stored, dict) and isinstance(stored.get("roles"), dict) and stored["roles"]:
        # Already shown today: the morning and its rates stay as they were.
        reading.update({k: stored.get(k) for k in _MORNING_KEYS})
        rates = {r: stored["roles"].get(r) or {} for r in scalp_sim.ROLES}

    spot = reading["entry_spot"]
    iv = None
    if slice_ is not None:
        ref = spot or _finite(slice_.get("underlyingPrice"))
        iv = scalp_chain.atm_iv(slice_, ref) if ref else None
    entry = dict(reading, chain=_chain_summary(slice_, iv, day), sigma=None,
                 sigma_source=None, roles={}, highlight=None, note=None, points_note=None)
    if m.open_bar is None and rates is None:
        entry["note"] = NO_OPEN_BAR
        return entry
    if reading["direction"] not in (1, -1):
        entry["note"] = FLAT_OPEN
        return entry

    vol = reading["vol_at_entry"]
    if iv is not None:
        sigma, entry["sigma_source"] = iv, "chain"
    elif vol is not None and vol > 0:
        sigma, entry["sigma_source"] = vol * scalp_config.iv_scale(), "model"
    else:
        sigma = None
        entry["points_note"] = NO_VOL
    entry["sigma"] = _finite(sigma)

    if rates is None:
        rates = scalp_study.current_rates(symbol, reading["bucket"], db_path=db_path)
    when = scalp_fetch.ct(reading["entry_ts"])
    for role in scalp_sim.ROLES:
        structure = scalp_sim.structure_for(role, reading["direction"])
        priced = ({} if sigma is None else
                  _price(symbol, structure, spot, when, day, sigma, slice_))
        entry["roles"][role] = _role(rates.get(role) or {}, structure, priced)

    edged = [r for r in scalp_sim.ROLES
             if entry["roles"][r]["edge"] and entry["roles"][r]["p"] is not None]
    if edged:
        entry["highlight"] = max(edged, key=lambda r: entry["roles"][r]["p"])
    else:
        entry["note"] = NO_EDGE
    return entry


# ── build and publish ────────────────────────────────────────────────────────
def build(day: _dt.date, now: _dt.datetime, db_path=None, client=None) -> dict:
    """Fetch today's bars and chains, call every scalp symbol, save each call in
    ``calls`` and return the view's payload (``context`` is ``run``'s, it reads
    the bus). One symbol failing never stops the others."""
    iso = day.isoformat()
    try:
        failed = [k for k, n in scalp_fetch.top_up(db_path=db_path, client=client,
                                                    today=day).items()
                  if n == scalp_fetch.FAILED]
        if failed:
            log.warning("scalp call: bar top-up failed for %s", ", ".join(failed))
    except Exception:
        _degrade.degraded("options.scalp_topup")
    try:
        slices = scalp_chain.capture_all(day=iso, client=client, db_path=db_path)
    except Exception:
        _degrade.degraded("options.scalp_capture")
        slices = {}
    stored = scalp_store.calls_on(iso, db_path=db_path)

    symbols = {}
    for symbol in scalp_config.symbols():
        try:
            entry = _symbol_call(symbol, day, slices.get(symbol), stored.get(symbol),
                                 db_path)
        except Exception:
            _degrade.degraded("options.scalp_symbol", detail=symbol)
            entry = {"direction": None, "roles": {}, "chain": None, "highlight": None,
                     "note": FAILED}
        symbols[symbol] = entry
        scalp_store.save_call(iso, symbol, entry, db_path=db_path)
    return {"day": iso, "call_day": iso, "generated_at": now.isoformat(),
            "state": "call", "symbols": symbols, "rules": _rules(), "context": {},
            "scorecard": {}, "next_call": None}


def _last_call(bus, db_path):
    """``(day, symbols)`` of the most recent saved call, else the current view's."""
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
            "context": context(bus, day), "scorecard": {}, "next_call": nxt}


def _valid(payload) -> bool:
    try:
        ScalpView(**payload)
    except Exception:  # noqa: BLE001 - a bad payload is reported, never raised.
        _degrade.degraded("options.scalp_view")
        return False
    return True


def run(bus, now=None, db_path=None, client=None) -> None:
    """Publish the view for ``now`` (default: now, CT): ``"closed"`` on a
    non-trading day, ``"waiting"`` before 08:35, else a freshly built
    ``"call"``. Never raises."""
    now = _ct_now(now)
    day = now.date()
    try:
        if not _mc.is_trading_day(day):
            payload = _held("closed", bus, day, now, db_path)
        elif now < _entry_time(day):
            payload = _held("waiting", bus, day, now, db_path)
        else:
            payload = build(day, now, db_path=db_path, client=client)
            payload["context"] = context(bus, day)
            payload["next_call"] = _next_call(day, "call")
    except Exception:
        _degrade.degraded("options.scalp_call")
        payload = _held("error", bus, day, now, db_path)
    if not _valid(payload):
        return
    bus.cache_set(VIEW, payload, event=EVENT)


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


__all__ = ["VIEW", "EVENT", "NO_EDGE", "build", "run", "context"]
