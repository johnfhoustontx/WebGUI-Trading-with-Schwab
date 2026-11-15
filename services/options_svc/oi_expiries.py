"""The per-expiry grid behind the Open Interest tab, over more expirations
than the nearest-expiry chain holds.

The gamma views fetch seven days of chain. The Open Interest tab shows EVERY
expiration a symbol has (``config/gamma_heat.toml [open_interest]``), except
for the symbols in ``limited_symbols`` (``$SPX``: its whole chain is too large
to ask for), which show the nearest ``expiries``.

⚠ **A whole chain is never ONE request.** The first version asked Schwab for
today to +1100 days in a single ``/chains`` call; on prod (2026-11-14, NVDA)
it ran into the proxy's 30-second limit every time and the tab fell back to
the chain in hand. The whole chain is fetched the way the Strategy Finder
fetches one: the expirations are listed and requested in runs
(``compute.fetch_scan_chain``, passed in as ``whole``).

Either way the rest of the chain is slow to fetch, and the page asks for a
snapshot every two minutes. Open interest is published once a day, so the wide
grid is kept per symbol and fetched again only after ``refresh_min``. A fetch
that FAILED is kept too, for ``retry_min``: without that a request that keeps
failing would be made, and waited for, on every refresh. A limited symbol
whose base chain already covers its count fetches nothing and keeps nothing.

The grid is published inside the gamma snapshot, so it is cut to what the tab
reads: each cell's two counts, and only the strikes that hold any.

Imports nothing from ``compute``; both fetches are passed in.
"""
import threading
import time

from services import _degrade
from shared import gamma_heat_config

EVERY = 10_000                  # "every expiration", as a count
_KEPT_SYMBOLS = 64              # the memo is dropped whole past this many
_LOCK = threading.Lock()
_KEPT = {}                      # symbol -> (good until, monotonic seconds; grid)


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


def _keep(symbol, until, built):
    with _LOCK:
        if len(_KEPT) >= _KEPT_SYMBOLS:
            _KEPT.clear()
        _KEPT[symbol] = (until, built)
    return built


def _wide_chain(symbol, base_chain, count, limited, widen, whole):
    """The wider chain, or ``None`` when it could not be had. Never raises."""
    try:
        if limited:
            chain = widen(symbol, base_chain, count)
        else:
            chain, failed = whole(symbol, None)
            if failed:
                _degrade.degraded("oi_expiries", exc_info=False,
                                  detail=f"{symbol}: {failed} expirations not fetched")
    except Exception:
        _degrade.degraded("oi_expiries", detail=str(symbol))
        return None
    # An answer holding less than the chain in hand is no answer.
    if _expirations(chain) < _expirations(base_chain):
        if not limited:
            _degrade.degraded("oi_expiries", exc_info=False,
                              detail=f"{symbol}: whole chain came back short")
        return None
    return chain


def grid(engine, symbol, base_chain, widen, whole, clock=time.monotonic):
    """The grid for ``symbol``: every expiration, or the configured number for
    a limited symbol.

    ``widen(symbol, base_chain, n)`` returns a chain covering at least ``n``
    expirations where the symbol has that many. ``whole(symbol, None)``
    returns ``(chain, expirations_not_fetched)`` for every listed expiration.
    A wide fetch that fails falls back to the chain in hand, so the tab
    narrows instead of emptying, and is not tried again for ``retry_min``."""
    cfg = gamma_heat_config.open_interest()
    limited = str(symbol).upper() in cfg["limited_symbols"]
    count = cfg["expiries"] if limited else EVERY
    if limited and _expirations(base_chain) >= count:
        return slim(engine.compute_term_grid(base_chain, top_n=count))
    now = clock()
    with _LOCK:
        kept = _KEPT.get(symbol)
    if kept and now < kept[0]:
        return kept[1]
    chain = _wide_chain(symbol, base_chain, count, limited, widen, whole)
    if chain is None:
        return _keep(symbol, now + cfg["retry_min"] * 60.0,
                     slim(engine.compute_term_grid(base_chain, top_n=count)))
    return _keep(symbol, now + cfg["refresh_min"] * 60.0,
                 slim(engine.compute_term_grid(chain, top_n=count)))
