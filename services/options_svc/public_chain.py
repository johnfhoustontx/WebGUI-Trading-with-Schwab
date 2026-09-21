"""Public chains: the QUOTED chain held in this worker's memory, a STRIPPED copy
published to Redis.

The public Calculator's "Rate My Trade" and its implied-volatility estimate
need the quoted chain, and the public Rescue form's dropdowns need its strikes.
All three share ONE key per symbol, ``cache:options:pub_chain:<SYMBOL>``
(``shared.public_rescue.ladder_view``), so visitors on any of them share one
Schwab fetch.

⚠ **Redis is readable by the public process, so a quote written there is a
quote published** - and Schwab's terms for republishing quotes are unsettled
(decision D1). So the full thin chain stays HERE, in ``_HELD``, and what is
written carries expirations and strikes only. Only while
``public_scan.show_leg_quotes()`` - the ONE quotes switch, off by default - is
on does the published copy also carry a small ``quotes`` block, four fields per
contract (bid, ask, mark, delta), each a finite float or None. The switch is
read at WRITE time, so flipping it needs no restart and no fresh load.

⚠ **Memory, not Redis, means a restart forgets every held chain** while the
published strikes list may still be fresh. A merge needs a held chain to merge
INTO, so with none held the merge becomes a full load (``load`` below) - never
a merge into nothing, which would publish quotes for one expiration and hold a
chain missing all the others.

The held chains are bounded (``public_tools.limits()["chain_hold_limit"]``,
oldest evicted) because visitors choose the symbols, and each entry is a whole
chain; and they expire on the same clock as the published list
(``public_rescue.limits()["ladder_ttl_min"]``).

Moved here from ``rescue_public`` (2026-09-21) with every rule its review
forced: a failed fetch is an ``error`` that writes nothing and keeps what is
held; ``no_options`` only when Schwab answered with an empty chain; a merge onto
a fresh list keeps the list's ``loaded_at``; a stale reload asks again for every
expiration it had strikes for.
"""
from __future__ import annotations

import collections
import math
import threading
import time

from services.options_svc import compute
from shared import public_rescue as pr
from shared import public_scan
from shared import public_tools

QUOTE_FIELDS = ("bid", "ask", "mark", "delta")

# symbol -> (monotonic time the chain was LOADED, calc_load_symbol-shaped payload)
_HELD: "collections.OrderedDict[str, tuple[float, dict]]" = collections.OrderedDict()
_HELD_LOCK = threading.Lock()


def _mono() -> float:
    """The clock the held chains age on. A function so tests can move it."""
    return time.monotonic()


def reset() -> None:
    """Forget every held chain (tests)."""
    with _HELD_LOCK:
        _HELD.clear()


def hold(symbol, cc, *, since=None) -> None:
    """Hold ``cc`` - a ``calc_load_symbol``-shaped payload, quotes and all - for
    ``symbol``. ``since`` keeps an earlier load time: a merge adds one
    expiration's fresh quotes, but every other expiration's are as old as the
    first load, so the merge must not make the chain look newer than it is."""
    key = str(symbol).strip().upper()
    limit = public_tools.limits()["chain_hold_limit"]
    with _HELD_LOCK:
        _HELD.pop(key, None)
        _HELD[key] = (_mono() if since is None else since, cc)
        while len(_HELD) > limit:
            _HELD.popitem(last=False)


def _held_entry(symbol):
    key = str(symbol).strip().upper()
    ttl = pr.limits()["ladder_ttl_min"] * 60
    with _HELD_LOCK:
        entry = _HELD.get(key)
    if entry is None or not 0 <= _mono() - entry[0] < ttl:
        return None
    return entry


def held(symbol):
    """The quoted chain held for ``symbol``, or None when absent or older than
    ``ladder_ttl_min``. ⚠ Never write what this returns to Redis: it carries
    every quote field."""
    entry = _held_entry(symbol)
    return entry[1] if entry is not None else None


# ── reading a thinned chain ─────────────────────────────────────────────────

def _maps(chain):
    for map_key, right in (("callExpDateMap", "call"), ("putExpDateMap", "put")):
        yield right, ((chain or {}).get(map_key) or {})


def strikes_from_chain(chain) -> dict:
    """``{expiry: {"call": [strikes], "put": [strikes]}}`` from a thinned chain.
    Strikes only: every quote field is left behind."""
    out: dict = {}
    for right, exps in _maps(chain):
        for exp_key, strikes in exps.items():
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


def quotes_from_chain(chain) -> dict:
    """``{expiry: {"call"|"put": {"500.0": {bid, ask, mark, delta}}}}``.

    Exactly those four fields and nothing else - no greek past delta, no open
    interest, no volume - each a finite float or None. ⚠ A NaN or inf is None,
    never passed on: a NaN reaching a comparison on the page is the repo's
    most-repeated bug class. Strike keys are normalized through ``float`` so
    ``"500"`` and ``"500.0"`` are one key, the spelling the strikes list uses."""
    out: dict = {}
    for right, exps in _maps(chain):
        for exp_key, strikes in exps.items():
            exp = str(exp_key).split(":")[0]
            side: dict = {}
            for s, rows in (strikes or {}).items():
                try:
                    strike = str(float(s))
                except (TypeError, ValueError):
                    continue
                row = rows[0] if isinstance(rows, list) and rows else None
                if not isinstance(row, dict):
                    continue
                side[strike] = {f: pr._finite(row.get(f)) for f in QUOTE_FIELDS}
            if side:
                out.setdefault(exp, {})[right] = side
    return out


def _spot(price):
    try:
        f = float(price)
    except (TypeError, ValueError):
        return None
    return round(f, 2) if math.isfinite(f) and f > 0 else None


def _with_quotes(payload, chain):
    """``payload`` plus a quotes block, only while the one quotes switch is on -
    read now, at write time."""
    if public_scan.show_leg_quotes():
        return {**payload, "quotes": quotes_from_chain(chain)}
    return payload


# ── loading ─────────────────────────────────────────────────────────────────

def load(symbol, expiry, existing, now, *, fresh):
    """Fetch what the request needs; return ``(published payload or None,
    outcome)``. ``None`` means write nothing: a list already published, and a
    chain already held, stay as they were.

    * One more expiration on a FRESH list is merged into it and keeps the list's
      own ``loaded_at``: the EXPIRATIONS came from that first load, so a merge
      must not make them look newer than they are. The held chain gains the
      expiration too. ⚠ With no held chain (a restart) it is a full load.
    * A STALE list is reloaded, asking again for every expiration it had
      strikes for, so a leg already sitting on one does not lose its strikes.
    * ⚠ ``no_options`` only when Schwab ANSWERED and listed nothing. A fetch
      that failed is an ``error`` and is remembered nowhere: stored as "no
      options", one proxy hiccup would tell every visitor for an hour that SPY
      has no options."""
    listed = (existing if isinstance(existing, dict)
              and not existing.get("no_options") else None)
    entry = _held_entry(symbol)
    if (fresh and listed and expiry and listed.get("api") and entry is not None
            and expiry in (listed.get("expirations") or [])):
        since, cc = entry
        extra = compute._fetch_thin_runs(listed["api"], [[expiry]])
        if extra is None:
            return None, "error"
        merged = {**cc, "chain": compute.merge_chains(cc.get("chain"), extra)}
        hold(symbol, merged, since=since)
        strikes = dict(listed.get("strikes") or {})
        strikes.update(strikes_from_chain(extra))
        return _with_quotes({**_strip(listed), "strikes": strikes},
                            merged["chain"]), "done"
    stamp = now.isoformat()
    wanted = ([expiry] if expiry else []) + sorted((listed or {}).get("strikes") or {})
    cc = compute.calc_load_symbol(symbol, lazy=True, expiries=wanted or None)
    chain = cc.get("chain")
    strikes = strikes_from_chain(chain)
    expirations = list(cc.get("expirations") or sorted(strikes))
    if not strikes:
        if not isinstance(chain, dict) or expirations:
            return None, "error"        # a fetch failed; say so, remember nothing
        return {"symbol": symbol, "no_options": True, "loaded_at": stamp}, "no_options"
    hold(symbol, cc)
    ladder = _with_quotes({"symbol": symbol, "api": cc.get("api") or symbol,
                           "spot": _spot(cc.get("price")),
                           "expirations": expirations, "strikes": strikes,
                           "loaded_at": stamp}, chain)
    if expiry and expiry not in expirations:
        return ladder, "not_listed"
    return ladder, "done"


def _strip(payload):
    """A published payload less its quotes block, so a merge rebuilds it from
    the held chain (or leaves it out, if the switch is now off)."""
    return {k: v for k, v in payload.items() if k != "quotes"}


def publish(bus, symbol, payload, keep_min):
    """Write ``payload`` to the shared public chain key with a TTL - visitors
    choose the symbols, so every key must expire - and announce it."""
    view = pr.ladder_view(symbol)
    version = bus.cache_set(pr.cache_key(view), payload, ttl=keep_min * 60)
    bus.publish(pr.event(view), {"version": version})
    return version
