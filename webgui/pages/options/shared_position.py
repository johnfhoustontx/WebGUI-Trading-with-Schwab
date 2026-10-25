"""The ONE position the Strategy Tools pages share (Tier-1, pure).

Before 2026-09-12 each page kept its own legs and a Copy-to-Simulator /
Copy-to-Calculator button moved them across. Now both pages publish their
symbol, strategy, legs and selected expiry here whenever they save their own
state, and both open with it — so whichever page was edited last is what the
other one shows. Page-only inputs (the Calculator's IV, rate and contracts; the
Simulator's sliders and tab) stay in each page's own snapshot.

The Expected Move page joined them on 2026-10-24, as the group's third tab. It
shows a position as strike lines on a cone and edits less of one — a symbol, an
expiry, at most one strike — so it goes through ``merge_view`` rather than
``publish``: an edit there changes what it names and keeps the rest (the
quantities and prices the Calculator holds, a calendar's far leg, the shares).

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


def _bare(symbol):
    """A symbol as the Expected Move page spells it: upper case, no ``$``."""
    return str(symbol or "").replace("$", "").strip().upper()


def _line(leg):
    """The part of a leg the Expected Move page draws: which right, which side,
    which strike."""
    strike = leg.get("strike")
    return {"strike": float(strike), "option_type": leg.get("option_type"),
            "side": leg.get("side")}


def view_legs(position):
    """The strike lines the Expected Move page draws for ``position``: its
    option legs that have a strike. Share legs have no strike to draw."""
    out = []
    for leg in (position or {}).get("legs") or []:
        if _S.is_stock_leg(leg) or not isinstance(leg.get("strike"), (int, float)):
            continue
        out.append(_line(leg))
    return out


def _same_lines(a, b):
    key = lambda l: (l["option_type"], l["side"], l["strike"])  # noqa: E731
    return sorted(map(key, a)) == sorted(map(key, b))


def merge_view(prior, symbol, expiry, legs=None):
    """The shared position after an edit on the Expected Move page, or ``None``
    when there is nothing to publish (a blank symbol).

    ``legs`` is the strike lines the page shows — ``[{strike, option_type,
    side}]`` — or ``None``/``[]`` when it names none (no strike picked), which
    is not a request to delete the position. The rules, in order:

    * **Lines that differ from the prior position's** — a strike the reader
      picked, or a hand-off from the Scanner — replace its legs. They carry no
      price (``premium`` ``None``), so the Calculator prices them off its
      chain, and the strategy is the template they form.
    * **A different symbol** keeps the strategy and drops the legs, whose
      strikes belong to the other symbol; each page lays its own template.
    * **The same symbol on a new expiry** moves the legs that sat on the prior
      expiry there, and drops their prices, which were that expiry's. Legs on
      another expiry (a calendar's far leg) and share legs stay as they were.
    * Otherwise nothing changed and the prior position comes back whole."""
    sym = _bare(symbol)
    if not sym:
        return None
    prior = prior if isinstance(prior, dict) and prior.get("symbol") else None
    expiry = expiry or None
    lines = [_line(l) for l in legs or []
             if isinstance(l, dict) and isinstance(l.get("strike"), (int, float))]
    same_symbol = prior is not None and _bare(prior["symbol"]) == sym

    if lines and not (same_symbol and _same_lines(lines, view_legs(prior))):
        from .sim_view import matches_template, template_for
        new_legs = [dict(l, expiry=expiry, qty=1, premium=None) for l in lines]
        # Strikes never count toward a template's shape, so a put credit spread
        # and a put debit spread look alike: keep the prior name while it still
        # fits, rather than let the first template in the menu rename it.
        kept = (prior or {}).get("strategy")
        strategy = (kept if kept and matches_template(kept, new_legs)
                    else template_for(new_legs))
        return {"symbol": prior["symbol"] if same_symbol else sym,
                "strategy": strategy, "legs": new_legs, "expiry": expiry}

    if not same_symbol:
        return {"symbol": sym, "strategy": (prior or {}).get("strategy"),
                "legs": [], "expiry": expiry}

    old = prior.get("expiry")
    if expiry is None or expiry == old:
        return copy.deepcopy(prior)
    moved = []
    for leg in prior.get("legs") or []:
        leg = dict(leg)
        if not _S.is_stock_leg(leg) and leg.get("expiry") in (old, None):
            leg.update(expiry=expiry, premium=None)
        moved.append(leg)
    return {"symbol": prior["symbol"], "strategy": prior.get("strategy"),
            "legs": moved, "expiry": expiry}


def publish_view(symbol, expiry, legs=None):
    """Record an edit made on the Expected Move page (see ``merge_view``)."""
    merged = merge_view(current(), symbol, expiry, legs)
    if merged is not None:
        publish(merged["symbol"], merged["strategy"], merged["legs"],
                merged["expiry"])


def split_stock_legs(legs):
    """``(option legs, share legs)``, each in its original order. The Simulator
    prices option legs only, so it carries the share legs through untouched and
    writes them back — it must never delete the Calculator's shares."""
    options, shares = [], []
    for leg in legs or []:
        (shares if _S.is_stock_leg(leg) else options).append(leg)
    return options, shares
