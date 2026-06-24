"""Shared editable leg-editor for the Simulator + Calculator (Tier-1).

Pure helpers (normalize/payload) are unit-tested. ``build_leg_editor`` renders
one row per leg (kind/side/strike/expiry/qty[/premium] + remove) plus an Add-leg
button; ``state['legs']`` is the single source of truth and each widget writes
its own row by index on change, so re-rendering (add/remove/template apply) never
loses in-progress edits. Each page injects ``strikes_for(expiry, otype)`` /
``expiries_for()`` (its own data source) and ``show_premium``.
"""
from types import SimpleNamespace

from nicegui import ui

from . import strategies as S

_KEYS = ("option_type", "side", "strike", "expiry", "qty", "premium")


def normalize_legs(legs, keep_premium=True):
    """Return legs reduced to exactly the normalized keys (strips widget refs /
    junk), qty coerced to int, premium defaulted to None (or dropped)."""
    out = []
    for l in legs or []:
        out.append({
            "option_type": l.get("option_type"),
            "side": l.get("side"),
            "strike": l.get("strike"),
            "expiry": l.get("expiry"),
            "qty": int(l.get("qty", 1) or 1),
            "premium": (l.get("premium") if keep_premium else None),
        })
    return out


def legs_to_payload(symbol, legs, keep_premium=True):
    """Normalized cross-page copy payload: {symbol (upper, no $), legs:[...]}"""
    return {"symbol": (symbol or "").replace("$", "").upper(),
            "legs": normalize_legs(legs, keep_premium=keep_premium)}


def coerce_strike(value, options):
    """Snap ``value`` to a member of ``options`` (nearest numeric), or None.

    NiceGUI's ``ui.select`` raises ``ValueError: Invalid value`` for a value not in
    its options, so every strike handed to a strike select MUST be one of its
    options. A strike from a different expiry's chain (the cross-expiry default-leg
    ladder) or a leg copied in from the Simulator is snapped to the nearest
    available strike; with no options it clears to None."""
    if not options:
        return None
    if value in options:
        return value
    if value is None:
        return None
    try:
        return min(options, key=lambda o: abs(o - value))
    except TypeError:
        return options[0]


def coerce_choice(value, options):
    """Return ``value`` if it's in ``options``, else the first option (or None).

    For non-numeric selects (expiry) where 'nearest' isn't meaningful — keeps the
    leg functional rather than crashing the select on an absent value."""
    options = options or []
    if value in options:
        return value
    return options[0] if options else None


def build_leg_editor(container, *, strikes_for, expiries_for, show_premium,
                     on_change=lambda: None, spot_getter=lambda: 0.0):
    """Mount the editor into ``container``. Returns a handle with
    get_legs() / set_legs(legs) / apply_template(name) / is_dirty()."""
    state = {"legs": [], "dirty": False}

    def _set_field(i, field, value):
        if not (0 <= i < len(state["legs"])):
            return
        state["legs"][i][field] = value
        state["dirty"] = True
        if field in ("option_type", "expiry"):
            _sync_row_strikes(i)
        on_change()

    def _sync_row_strikes(i):
        leg = state["legs"][i]
        opts = strikes_for(leg.get("expiry"), leg.get("option_type")) or []
        w = leg.get("_strike_widget")
        if w is not None:
            w.options = opts
            if opts and w.value not in opts:
                spot = spot_getter() or 0
                w.value = min(opts, key=lambda s: abs(s - spot)) if spot else opts[0]
                state["legs"][i]["strike"] = w.value
            w.update()

    def _render():
        container.clear()
        exps = expiries_for() or []
        with container:
            for i, leg in enumerate(state["legs"]):
                # Coerce the leg's expiry + strike into the AVAILABLE options FIRST —
                # ui.select raises ValueError on a value not in its options (a default
                # leg placed off the cross-expiry strike union, or a leg copied in from
                # the Simulator, can carry a strike/expiry absent from this expiry's
                # chain). Write the coerced values back to state so get_legs() matches
                # the display. (Not an edit → no dirty flag.)
                e_val = coerce_choice(leg.get("expiry"), exps)
                leg["expiry"] = e_val
                s_opts = strikes_for(e_val, leg.get("option_type")) or []
                s_val = coerce_strike(leg.get("strike"), s_opts)
                leg["strike"] = s_val
                with ui.row().classes("items-end gap-2 no-wrap"):
                    ui.select(["call", "put"], value=leg.get("option_type"), label="Type") \
                        .classes("w-24").on_value_change(lambda e, i=i: _set_field(i, "option_type", e.value))
                    ui.select(["long", "short"], value=leg.get("side"), label="Side") \
                        .classes("w-24").on_value_change(lambda e, i=i: _set_field(i, "side", e.value))
                    ui.select(exps, value=e_val, label="Expiry") \
                        .classes("w-40").on_value_change(lambda e, i=i: _set_field(i, "expiry", e.value))
                    sw = ui.select(s_opts, value=s_val, label="Strike").classes("w-28")
                    sw.on_value_change(lambda e, i=i: _set_field(i, "strike", e.value))
                    leg["_strike_widget"] = sw
                    ui.number("Qty", value=leg.get("qty", 1), min=1, max=100, format="%.0f") \
                        .classes("w-20").on_value_change(lambda e, i=i: _set_field(i, "qty", int(e.value or 1)))
                    if show_premium:
                        ui.number("Premium", value=leg.get("premium") or 0.0, format="%.2f") \
                            .classes("w-28").on_value_change(lambda e, i=i: _set_field(i, "premium", e.value))
                    ui.button(icon="close", on_click=lambda e, i=i: _remove(i)) \
                        .props("flat dense round").tooltip("Remove leg")
            ui.button("Add leg", icon="add", on_click=lambda e: _add()).props("flat dense")

    def _add():
        state["legs"].append({"option_type": "call", "side": "long", "strike": None,
                              "expiry": (expiries_for() or [None])[0], "qty": 1, "premium": None})
        state["dirty"] = True
        _render(); on_change()

    def _remove(i):
        if 0 <= i < len(state["legs"]):
            state["legs"].pop(i)
            state["dirty"] = True
            _render(); on_change()

    def set_legs(legs):
        state["legs"] = normalize_legs(legs)
        state["dirty"] = False
        _render()

    def get_legs():
        return normalize_legs(state["legs"])    # strips _strike_widget

    def apply_template(name):
        # Place default strikes off the NEAR expiry's real strikes (not the
        # cross-expiry union), so condor/butterfly wings land on strikes that
        # actually exist for that expiry — distinct, and valid at render time (the
        # union can include strikes absent from the chosen expiry, e.g. a 737.5 from
        # another expiry that isn't in a 0DTE integer chain).
        exps = expiries_for() or []
        near = exps[0] if exps else None
        placement = (strikes_for(near, "call") if near else strikes_for(None, "call")) or []
        legs = S.build_default_legs(name, spot_getter() or 0, placement, exps)
        set_legs(legs)

    def refresh_options():
        """Re-pull expiries/strikes after the page's data source loads."""
        _render()

    return SimpleNamespace(get_legs=get_legs, set_legs=set_legs,
                           apply_template=apply_template, refresh_options=refresh_options,
                           is_dirty=lambda: state["dirty"])
