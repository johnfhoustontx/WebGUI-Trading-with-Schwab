"""The per-expiry grid behind the Open Interest tab, over more expirations
than the nearest-expiry chain holds.

The gamma views fetch seven days of chain. For a symbol with daily expirations
that is five or six of them; the Open Interest tab shows up to
``[open_interest] expiries`` (``config/gamma_heat.toml``). Reaching the rest
means a wider chain request, which is large and slow, and the page asks for a
snapshot every two minutes. Open interest is published once a day, so the wide
grid is kept per symbol and fetched again only after
``[open_interest] refresh_min``.

When the base chain already covers the count, nothing is kept and nothing is
fetched: the grid is built from the chain in hand, as before.

Imports nothing from ``compute``; the widening fetch is passed in.
"""
import threading
import time

from services import _degrade
from shared import gamma_heat_config

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


def grid(engine, symbol, base_chain, widen, clock=time.monotonic):
    """The grid for ``symbol`` over up to the configured number of
    expirations. ``widen(symbol, base_chain, n)`` returns a chain covering at
    least ``n`` of them where the symbol has that many. A wide fetch that
    fails falls back to the chain in hand, so the tab narrows instead of
    emptying."""
    cfg = gamma_heat_config.open_interest()
    count = cfg["expiries"]
    if _expirations(base_chain) >= count:
        return engine.compute_term_grid(base_chain, top_n=count)
    now = clock()
    with _LOCK:
        kept = _KEPT.get(symbol)
    if kept and now - kept[0] < cfg["refresh_min"] * 60.0:
        return kept[1]
    try:
        chain = widen(symbol, base_chain, count)
    except Exception:
        _degrade.degraded("oi_expiries")
        return engine.compute_term_grid(base_chain, top_n=count)
    built = engine.compute_term_grid(chain, top_n=count)
    with _LOCK:
        if len(_KEPT) >= _KEPT_SYMBOLS:
            _KEPT.clear()
        _KEPT[symbol] = (now, built)
    return built
