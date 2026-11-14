"""The per-expiry grid behind the Open Interest tab, over more expirations
than the nearest-expiry chain holds.

The gamma views fetch seven days of chain. The Open Interest tab shows EVERY
expiration a symbol has (``config/gamma_heat.toml [open_interest]``), except
for the symbols in ``limited_symbols`` (``$SPX``: its whole chain is too large
to ask for), which show the nearest ``expiries``.

Either way the rest of the chain is a wider request, which is large and slow,
and the page asks for a snapshot every two minutes. Open interest is published
once a day, so the wide grid is kept per symbol and fetched again only after
``refresh_min``. A limited symbol whose base chain already covers its count
fetches nothing and keeps nothing.

The grid is published inside the gamma snapshot, so it is cut to what the tab
reads: each cell's two counts, and only the strikes that hold any.

Imports nothing from ``compute``; the count-based widening fetch is passed in.
"""
import datetime as _dt
import threading
import time

from services import _degrade, _proxy
from shared import gamma_heat_config

EVERY = 10_000                  # "every expiration", as a count
_KEPT_SYMBOLS = 64              # the memo is dropped whole past this many
_LOCK = threading.Lock()
_KEPT = {}                      # symbol -> (monotonic seconds, grid)


def reset():
    """Forget every kept grid (tests, and nothing else)."""
    with _LOCK:
        _KEPT.clear()


def _expirations(chain):
    dates = set()
    for side in ("callExpDateMap", "putExpDateMap"):
        for key in ((chain or {}).get(side) or {}):
            dates.add(str(key).split(":", 1)[0])
    return len(dates)


def fetch_whole(symbol, days):
    """One request for every expiration of ``symbol`` within ``days``. Raises
    when Schwab does not answer 200."""
    today = _dt.date.today()
    resp = _proxy.schwab_py_client.get_option_chain(
        symbol, contract_type="ALL", from_date=today,
        to_date=today + _dt.timedelta(days=days))
    if getattr(resp, "status_code", None) != 200:
        raise RuntimeError(f"whole chain for {symbol}: "
                           f"status {getattr(resp, 'status_code', None)}")
    return resp.json()


def slim(grid):
    """The grid with each cell cut to its two counts and the strikes that hold
    none dropped. Anything that is not a grid passes through."""
    cells = grid.get("cells") if isinstance(grid, dict) else None
    if not isinstance(cells, dict):
        return grid
    kept = {}
    for date, strikes in cells.items():
        if not isinstance(strikes, dict):
            continue
        kept[date] = {
            strike: {"call_oi": cell.get("call_oi") or 0.0,
                     "put_oi": cell.get("put_oi") or 0.0}
            for strike, cell in strikes.items()
            if isinstance(cell, dict)
            and (cell.get("call_oi") or cell.get("put_oi"))}
    return {**grid, "cells": kept}


def grid(engine, symbol, base_chain, widen, clock=time.monotonic):
    """The grid for ``symbol``: every expiration, or the configured number for
    a limited symbol. ``widen(symbol, base_chain, n)`` returns a chain covering
    at least ``n`` expirations where the symbol has that many. A wide fetch
    that fails falls back to the chain in hand, so the tab narrows instead of
    emptying."""
    cfg = gamma_heat_config.open_interest()
    limited = str(symbol).upper() in cfg["limited_symbols"]
    count = cfg["expiries"] if limited else EVERY
    if limited and _expirations(base_chain) >= count:
        return slim(engine.compute_term_grid(base_chain, top_n=count))
    now = clock()
    with _LOCK:
        kept = _KEPT.get(symbol)
    if kept and now - kept[0] < cfg["refresh_min"] * 60.0:
        return kept[1]
    try:
        chain = (widen(symbol, base_chain, count) if limited
                 else fetch_whole(symbol, cfg["all_days"]))
    except Exception:
        _degrade.degraded("oi_expiries")
        return slim(engine.compute_term_grid(base_chain, top_n=count))
    if _expirations(chain) < _expirations(base_chain):
        # An answer holding less than the chain in hand is no answer: use the
        # chain in hand and ask again next time rather than keeping it.
        return slim(engine.compute_term_grid(base_chain, top_n=count))
    built = slim(engine.compute_term_grid(chain, top_n=count))
    with _LOCK:
        if len(_KEPT) >= _KEPT_SYMBOLS:
            _KEPT.clear()
        _KEPT[symbol] = (now, built)
    return built
