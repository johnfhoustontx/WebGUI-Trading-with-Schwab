"""The ONE position the Calculator and the Simulator share (Tier-1, pure).

Before 2026-09-12 each page kept its own legs and a Copy-to-Simulator /
Copy-to-Calculator button moved them across. Now both pages publish their
symbol, strategy, legs and selected expiry here whenever they save their own
state, and both open with it — so whichever page was edited last is what the
other one shows. Page-only inputs (the Calculator's IV, rate and contracts; the
Simulator's sliders and tab) stay in each page's own snapshot.

Single-user and module-level, the same lifetime as ``_LAST_CALC`` / ``_LAST_SIM``:
it survives navigation and resets when the webgui restarts. No nicegui import.
"""
import copy

from . import strategies as _S

_KEYS = ("option_type", "side", "strike", "expiry", "qty", "premium")
_POSITION: dict = {}


def _leg(leg):
    src = leg if isinstance(leg, dict) else {}
    out = {k: src.get(k) for k in _KEYS}
    out["qty"] = int(src.get("qty", 1) or 1)
    return out


def publish(symbol, strategy, legs, expiry):
    """Record the position a page is showing. A blank symbol clears it — there
    is no position without one."""
    sym = str(symbol or "").strip().upper()
    _POSITION.clear()
    if not sym:
        return
    _POSITION.update({"symbol": sym, "strategy": strategy,
                      "legs": [_leg(l) for l in legs or []],
                      "expiry": expiry or None})


def current():
    """A COPY of the shared position, or None when no page has published one."""
    return copy.deepcopy(_POSITION) if _POSITION.get("symbol") else None


def reset():
    """Forget the shared position (test helper)."""
    _POSITION.clear()


def split_stock_legs(legs):
    """``(option legs, share legs)``, each in its original order. The Simulator
    prices option legs only, so it carries the share legs through untouched and
    writes them back — it must never delete the Calculator's shares."""
    options, shares = [], []
    for leg in legs or []:
        (shares if _S.is_stock_leg(leg) else options).append(leg)
    return options, shares
