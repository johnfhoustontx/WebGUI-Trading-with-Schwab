"""The public Calculator -> Simulator hand-off, over NiceGUI TAB storage.

The design: the public Calculator WRITES its position (symbol + legs) here on
every edit and never reads it; the public Simulator READS it once, to seed
itself when a visitor moves across in the same browser tab. Nothing persists
across visits: tab storage lives in this process's memory, belongs to one
browser tab, and ``live_main`` caps its age at an hour
(``app.storage.max_tab_storage_age``), after which NiceGUI prunes it.

Why tab storage, and not what the private pages use: ``app_settings``,
``shared_position`` and ``page_state`` are SINGLE-USER module stores. They are
right for the owner's app, which has one user; on the public process they
would be one store shared by every visitor, so one visitor's legs would appear
on the next visitor's Simulator (and ``app_settings`` is frozen there anyway).
Tab storage is keyed by NiceGUI's per-tab id, so it is per visitor, per tab.

⚠ How ``app.storage.tab`` behaves in NiceGUI 3.13: it RAISES ``RuntimeError``
until the page's socket connection is up (``Storage.tab`` checks
``client.has_socket_connection``), and outside any page it raises too. So
:func:`read` returns None - and :func:`write` does nothing - during a page's
synchronous build; a page that wants the seed must ``await
ui.context.client.connected()`` first. Both swallow every failure, because a
lost hand-off costs a visitor re-typing a symbol and a traceback costs a
journal line per anonymous visit.

What is stored is re-validated on the way OUT as well as on the way in - the
same leg cleaner the public request builders use
(``shared.public_tools._clean_calc_leg``), with a missing price allowed because
the Calculator may not have priced a leg yet. :func:`read` returns
``seed_from(stored)``, never the stored value itself.

⚠ The legs are Calculator legs and may include a SHARE leg; the Simulator's
engines have no share concept, so the reader decides what to do with one.
"""
from shared import public_tools as _pt
from shared.symbols import clean_symbol

KEY = "calc_position"


# ── the pure half ───────────────────────────────────────────────────────────

def position_payload(symbol, legs, today=None) -> dict | None:
    """``{"symbol": <SYMBOL>, "legs": [...]}`` with every leg normalized to the
    Calculator's six keys, or None when the symbol is invalid, ``legs`` is not
    a list, it holds more than ``public_tools.MAX_LEGS`` legs, or ANY leg is
    unusable. An empty list is a symbol with no position yet."""
    sym = clean_symbol(symbol)
    if sym is None or not isinstance(legs, list) or len(legs) > _pt.MAX_LEGS:
        return None
    out = [_pt._clean_calc_leg(leg, today, allow_missing_premium=True)
           for leg in legs]
    if any(leg is None for leg in out):
        return None
    return {"symbol": sym, "legs": out}


def seed_from(payload, today=None):
    """``(symbol, legs)`` from a stored payload, re-validated, or None for
    anything malformed. Extra keys, at either level, are dropped."""
    if not isinstance(payload, dict):
        return None
    clean = position_payload(payload.get("symbol"), payload.get("legs"), today)
    return None if clean is None else (clean["symbol"], clean["legs"])


# ── the NiceGUI half ────────────────────────────────────────────────────────

def _tab():
    from nicegui import app
    return app.storage.tab


def write(payload) -> None:
    """Store a position for this browser tab. A malformed payload, no client,
    or tab storage not ready is a silent no-op."""
    clean = seed_from(payload)
    if clean is None:
        return
    symbol, legs = clean
    try:
        _tab()[KEY] = {"symbol": symbol, "legs": legs}
    except Exception:     # no client / not connected yet / tab storage not created
        return


def read():
    """``(symbol, legs)`` stored for this browser tab, re-validated, or None -
    including when there is no client or tab storage is not ready."""
    try:
        stored = _tab().get(KEY)
    except Exception:     # no client / not connected yet / tab storage not created
        return None
    return seed_from(stored)
