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
        with container:
            for i, leg in enumerate(state["legs"]):
                with ui.row().classes("items-end gap-2 no-wrap"):
                    ui.select(["call", "put"], value=leg.get("option_type"), label="Type") \
                        .classes("w-24").on_value_change(lambda e, i=i: _set_field(i, "option_type", e.value))
                    ui.select(["long", "short"], value=leg.get("side"), label="Side") \
                        .classes("w-24").on_value_change(lambda e, i=i: _set_field(i, "side", e.value))
                    ui.select(expiries_for() or [], value=leg.get("expiry"), label="Expiry") \
                        .classes("w-40").on_value_change(lambda e, i=i: _set_field(i, "expiry", e.value))
                    sw = ui.select(strikes_for(leg.get("expiry"), leg.get("option_type")) or [],
                                   value=leg.get("strike"), label="Strike").classes("w-28")
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
        legs = S.build_default_legs(name, spot_getter() or 0,
                                    strikes_for(None, "call") or [], expiries_for() or [])
        set_legs(legs)

    def refresh_options():
        """Re-pull expiries/strikes after the page's data source loads."""
        _render()

    return SimpleNamespace(get_legs=get_legs, set_legs=set_legs,
                           apply_template=apply_template, refresh_options=refresh_options,
                           is_dirty=lambda: state["dirty"])
