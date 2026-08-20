"""Shared editable leg-editor for the Simulator + Calculator (Tier-1).

Pure helpers (normalize/payload) are unit-tested. ``build_leg_editor`` renders
one leg per row (kind/side/strike/expiry/qty[/premium] + remove) plus an Add-leg
button; ``state['legs']`` is the single source of truth and each widget writes
its own row by index on change, so re-rendering (add/remove/template apply) never
loses in-progress edits. Each page injects ``strikes_for(expiry, otype)`` /
``expiries_for()`` (its own data source) and ``show_premium``.

Two layouts over that one model:

``layout="row"``   the original single-line table (the Simulator today).
``layout="card"``  the redesign's two-line card — eyebrow captions over
                   TYPE/SIDE/EXPIRY then STRIKE/QTY/PREMIUM/DELTA, a left accent
                   bar coloured by side, and a remove button that locks at a
                   ``min_legs`` floor. PREMIUM and DELTA each COLLAPSE their
                   track when the page supplies no source for them, so no
                   caption ever sits over a cell that cannot hold a value.

The card's GEOMETRY is shared; its COLOURS are not. Both pages will mount the
card, but the Calculator paints it in the near-black ``CALC_*`` language while
the Simulator keeps the app-wide dark navy — so the palette enters as the
``tokens`` argument and this module imports no page's theme constants.
"""
import math
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


def set_legs_expiry(legs, expiry):
    """Return normalized legs with EVERY leg's expiry set to ``expiry`` (used by the
    Calculator's top-level Expiry → propagate to all legs). Other fields preserved."""
    out = normalize_legs(legs)
    for l in out:
        l["expiry"] = expiry
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


# ── the card layout ──────────────────────────────────────────────────────────
# Card-layout palette. Enters as an argument so the Calculator can pass its own
# near-black CALC_* tokens while the Simulator keeps the app-wide dark navy —
# the two pages share the GEOMETRY, not the colours. The defaults below are that
# dark navy, so a page that passes nothing still looks like the rest of the app.
DEFAULT_CARD_TOKENS = {
    "frame": "border border-[#213152] rounded-[2px] bg-[rgba(9,14,20,.55)]",
    "eyebrow": "text-[8px] tracking-[.14em] text-[#7f8db0] whitespace-nowrap truncate",
    "accent_long": "border-l-2 border-l-[#22d3ee]",
    "accent_short": "border-l-2 border-l-[#2dd4a7]",
    "num": "text-[10px] text-[#7189a0]",
    "delta": "text-[11px] text-[#cdd8ee] whitespace-nowrap",
    "remove": "text-[10px] text-[#9db0c2] border border-[#3a4a5b] rounded-[2px]",
    "remove_off": "text-[10px] text-[#4e5f70] border border-[#26313d] rounded-[2px] cursor-not-allowed",
    "add": "text-[9px] tracking-[.18em] text-[#a7dceb] border border-dashed border-[#3a6070] rounded-[2px]",
    "reset": "text-[9px] tracking-[.18em] text-[#8aa0b4] border border-[#2c3b4b] rounded-[2px]",
}

# The grid templates ARE the card's alignment contract: captions and cells
# share one track list, so a caption can never drift off the cell under it.
_CARD_ROW1_COLS = "grid grid-cols-[72px_78px_minmax(0,1fr)] gap-x-2 gap-y-0.5 items-center w-full"
# An omitted cell COLLAPSES its track rather than leaving a hole: a 4-track grid
# fed 3 cells would slide the next value under the wrong caption, which is worse
# than a narrower card. PREMIUM goes when the page prices legs itself; DELTA goes
# when the page passes no ``delta_for`` — a captioned column that can NEVER hold
# a value reads as broken rather than as not-applicable, and on the Simulator
# (``sim_meta`` carries no greeks) it never can. Four combinations, four STATIC
# class strings — a finite set, never a runtime-built arbitrary value.
_CARD_ROW2_TAIL = "gap-x-2 gap-y-0.5 items-center w-full"
_CARD_ROW2_COLS = f"grid grid-cols-[minmax(0,1.25fr)_46px_minmax(0,1fr)_44px] {_CARD_ROW2_TAIL}"
_CARD_ROW2_COLS_NO_PREMIUM = f"grid grid-cols-[minmax(0,1.25fr)_46px_44px] {_CARD_ROW2_TAIL}"
_CARD_ROW2_COLS_NO_DELTA = f"grid grid-cols-[minmax(0,1.25fr)_46px_minmax(0,1fr)] {_CARD_ROW2_TAIL}"
_CARD_ROW2_COLS_MINIMAL = f"grid grid-cols-[minmax(0,1.25fr)_46px] {_CARD_ROW2_TAIL}"
_CARD_ROW2_GRIDS = {
    # (show_premium, show_delta) -> the row-2 track list
    (True, True): _CARD_ROW2_COLS,
    (False, True): _CARD_ROW2_COLS_NO_PREMIUM,
    (True, False): _CARD_ROW2_COLS_NO_DELTA,
    (False, False): _CARD_ROW2_COLS_MINIMAL,
}

# The track list above is drawn for a ~424px column (the Calculator's). Left to
# stretch across the Simulator's ``flex-grow min-w-[340px]`` column — ~800px on
# a desktop — the two ``fr`` tracks absorb ~700px each and the card renders a
# 700px-wide select showing "450.0". The cap rides the CARD rather than a page's
# column, because "this geometry wants ≤440px" is a fact about the card, not
# about any one host; in a narrower column it is simply inert.
_CARD_MAX_W = "max-w-[440px]"


_LAYOUTS = ("row", "card")


def card_tokens(overrides=None):
    """``DEFAULT_CARD_TOKENS`` with known keys overridden. Unknown keys are
    ignored, so a typo cannot silently introduce a token nothing reads; blank and
    non-string values are ignored too, so a page whose own token computed to ""
    degrades to the default look rather than to an unstyled element. A
    non-mapping ``overrides`` is ignored outright rather than raising."""
    out = dict(DEFAULT_CARD_TOKENS)
    if not isinstance(overrides, dict):
        overrides = {}
    for k, v in overrides.items():
        if k in out and isinstance(v, str) and v.strip():
            out[k] = v
    return out


def can_remove(leg_count, min_legs):
    """Whether the remove button is live at this leg count.

    Plain ``>``: a floor of 2 locks at 2 legs, a floor of 1 locks at 1, and a
    floor of 0 locks only when there is nothing left to remove. ``None`` reads as
    no floor at all."""
    return int(leg_count or 0) > max(int(min_legs or 0), 0)


def delta_text(delta):
    """Signed 2-dp delta, or an em-dash when there is no reading.

    Never renders 0.00 for a missing delta — see ``calculator.extract_delta``:
    index chains read hollow outside regular hours, and a confident ``0.00`` on
    an otherwise live-looking leg is a wrong number rather than a blank one.

    ⚠ NaN and the infinities are readings too, and format as ``+nan`` / ``+inf``
    rather than raising. That is the same class of trap as the CLAUDE.md
    ``_clamp(nan)`` section — a missing input rendering as a confident value —
    so a non-finite float is an em-dash, not a number. Unreachable from the
    Calculator (its ``extract_delta`` already filters non-finite), but this is a
    public helper and the Simulator will supply its own ``delta_for``."""
    if not isinstance(delta, (int, float)) or isinstance(delta, bool):
        return "—"
    if not math.isfinite(delta):
        return "—"
    return f"{delta:+.2f}"


def build_leg_editor(container, *, strikes_for, expiries_for, show_premium,
                     on_change=lambda: None, spot_getter=lambda: 0.0, header=False,
                     layout="row", tokens=None, delta_for=None, min_legs=1,
                     on_reset=None):
    """Mount the editor into ``container``. Returns a handle with
    get_legs() / set_legs(legs) / apply_template(name) / is_dirty().

    ``layout="card"`` swaps the single-line row for the two-line card; ``tokens``
    overrides its palette (see ``card_tokens``), ``delta_for(leg)`` supplies the
    per-leg delta the card shows — omit it and the DELTA cell collapses, exactly
    as ``show_premium=False`` collapses PREMIUM — ``min_legs`` floors the remove
    button and
    ``on_reset`` adds a RESET TO TEMPLATE button beside ADD LEG. All five are
    inert in row mode."""
    # A typo here would silently render the WRONG screen with nothing to see it:
    # both layouts are valid renders of the same state, so neither the page nor
    # any test would report a failure.
    if layout not in _LAYOUTS:
        raise ValueError(f"unknown leg-editor layout {layout!r}; "
                         f"expected one of {sorted(_LAYOUTS)}")
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
        # ``_strike_widget`` is registered unconditionally by _render for every
        # leg, so it is present here by construction — this deliberately does NOT
        # guard on its absence. The old ``if w is not None`` made a forgotten
        # registration a SILENT no-op, i.e. exactly the stale-ladder →
        # ``ValueError: Invalid value`` failure this widget exists to prevent.
        leg = state["legs"][i]
        opts = strikes_for(leg.get("expiry"), leg.get("option_type")) or []
        w = leg["_strike_widget"]
        w.options = opts
        if opts and w.value not in opts:
            spot = spot_getter() or 0
            w.value = min(opts, key=lambda s: abs(s - spot)) if spot else opts[0]
            state["legs"][i]["strike"] = w.value
        w.update()

    card = layout == "card"
    tk = card_tokens(tokens)

    def _row_body(i, leg, exps, e_val, s_opts, s_val, lab):
        with ui.row().classes("items-end gap-2 no-wrap leg-row"):
            # Compact widths so the whole row (incl. the remove ✕) fits the
            # Calculator's fixed-width left column and never spills onto the
            # P&L matrix beside it.
            ui.select(["call", "put"], value=leg.get("option_type"), label=lab("Type")) \
                .classes("w-24").on_value_change(lambda e, i=i: _set_field(i, "option_type", e.value))
            ui.select(["long", "short"], value=leg.get("side"), label=lab("Side")) \
                .classes("w-24").on_value_change(lambda e, i=i: _set_field(i, "side", e.value))
            ui.select(exps, value=e_val, label=lab("Expiry")) \
                .classes("w-40").on_value_change(lambda e, i=i: _set_field(i, "expiry", e.value))
            sw = ui.select(s_opts, value=s_val, label=lab("Strike")).classes("w-24 leg-strike")
            sw.on_value_change(lambda e, i=i: _set_field(i, "strike", e.value))
            ui.number(lab("Qty"), value=leg.get("qty", 1), min=1, max=100, format="%.0f") \
                .classes("w-16").on_value_change(lambda e, i=i: _set_field(i, "qty", int(e.value or 1)))
            if show_premium:
                ui.number(lab("Premium"), value=leg.get("premium") or 0.0, format="%.2f") \
                    .classes("w-20").on_value_change(lambda e, i=i: _set_field(i, "premium", e.value))
            ui.button(icon="delete", on_click=lambda e, i=i: _remove(i)) \
                .props("flat dense round").classes("w-10").tooltip("Remove leg")
        return sw

    def _card_body(i, leg, exps, e_val, s_opts, s_val, _lab):
        # Side → accent, mapped from the finite {long, short} set to a fixed
        # class (never a runtime-built colour). An unrecognised side reads as
        # long, the same default the rest of the module uses.
        accent = tk["accent_short"] if leg.get("side") == "short" else tk["accent_long"]
        with ui.element("div").classes(
                f"leg-card {tk['frame']} {accent} w-full {_CARD_MAX_W} "
                f"flex items-stretch gap-2 px-2 py-1.5"):
            ui.label(f"{i + 1:02d}").classes(f"{tk['num']} shrink-0 w-5 pt-1")
            with ui.element("div").classes("flex-1 min-w-0 flex flex-col gap-1"):
                with ui.element("div").classes(_CARD_ROW1_COLS):
                    ui.label("TYPE").classes(tk["eyebrow"])
                    ui.label("SIDE").classes(tk["eyebrow"])
                    ui.label("EXPIRY").classes(tk["eyebrow"])
                    ui.select(["call", "put"], value=leg.get("option_type")) \
                        .props("dense options-dense").classes("w-full") \
                        .on_value_change(lambda e, i=i: _set_field(i, "option_type", e.value))
                    ui.select(["long", "short"], value=leg.get("side")) \
                        .props("dense options-dense").classes("w-full") \
                        .on_value_change(lambda e, i=i: _set_field(i, "side", e.value))
                    ui.select(exps, value=e_val).props("dense options-dense").classes("w-full") \
                        .on_value_change(lambda e, i=i: _set_field(i, "expiry", e.value))
                show_delta = delta_for is not None
                with ui.element("div").classes(
                        _CARD_ROW2_GRIDS[(bool(show_premium), show_delta)]):
                    ui.label("STRIKE").classes(tk["eyebrow"])
                    ui.label("QTY").classes(tk["eyebrow"])
                    if show_premium:
                        ui.label("PREMIUM").classes(tk["eyebrow"])
                    if show_delta:
                        ui.label("DELTA").classes(f"{tk['eyebrow']} text-right")
                    sw = ui.select(s_opts, value=s_val).props("dense options-dense") \
                        .classes("w-full leg-strike")
                    sw.on_value_change(lambda e, i=i: _set_field(i, "strike", e.value))
                    ui.number(value=leg.get("qty", 1), min=1, max=100, format="%.0f") \
                        .props("dense").classes("w-full") \
                        .on_value_change(lambda e, i=i: _set_field(i, "qty", int(e.value or 1)))
                    if show_premium:
                        ui.number(value=leg.get("premium") or 0.0, format="%.2f") \
                            .props("dense").classes("w-full") \
                            .on_value_change(lambda e, i=i: _set_field(i, "premium", e.value))
                    if show_delta:
                        # A source that returns None for THIS leg still gets a
                        # cell — an em-dash is "no reading now", which is not the
                        # same claim as having no source at all.
                        (ui.label(delta_text(delta_for(leg)))
                         .classes(f"{tk['delta']} text-right"))
            _card_remove(i)
        return sw

    def _card_remove(i):
        live = can_remove(len(state["legs"]), min_legs)
        floor = max(int(min_legs or 0), 0)
        # The tooltip hangs off a WRAPPER, not the button: Quasar kills pointer
        # events on a disabled q-btn, and the locked state is precisely when the
        # explanation is worth reading.
        with ui.element("div").classes("shrink-0 self-end flex items-center"):
            btn = ui.button("✕", on_click=lambda e, i=i: _remove(i), color=None) \
                .props("flat dense no-caps") \
                .classes(f"leg-remove px-1 min-h-0 {tk['remove'] if live else tk['remove_off']}")
            btn.set_enabled(live)
            ui.tooltip("Remove leg" if live
                       else f"At least {floor} leg{'' if floor == 1 else 's'} required")

    def _card_footer():
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.button("ADD LEG", on_click=lambda e: _add(), color=None) \
                .props("flat dense no-caps").classes(f"{tk['add']} px-2")
            if on_reset is not None:
                ui.button("RESET TO TEMPLATE", on_click=lambda e: on_reset(), color=None) \
                    .props("flat dense no-caps").classes(f"{tk['reset']} px-2")

    def _render():
        container.clear()
        exps = expiries_for() or []
        # ``header`` mode (row layout): the field labels move to a single header
        # row and each leg renders label-less inputs — a clean table. Default off,
        # so the Simulator keeps a label on every field. The card layout carries
        # its own per-leg eyebrow captions, so ``header`` is inert there.
        lab = (lambda _t: "") if header else (lambda t: t)
        body = _card_body if card else _row_body
        with container:
            if header and not card:
                with ui.row().classes("items-center gap-2 no-wrap leg-head"):
                    ui.label("Type").classes("w-24")
                    ui.label("Side").classes("w-24")
                    ui.label("Expiry").classes("w-40")
                    ui.label("Strike").classes("w-24")
                    ui.label("Qty").classes("w-16")
                    if show_premium:
                        ui.label("Premium").classes("w-20")
                    ui.label("").classes("w-10")
            for i, leg in enumerate(state["legs"]):
                # Coerce the leg's expiry + strike into the AVAILABLE options FIRST —
                # ui.select raises ValueError on a value not in its options (a default
                # leg placed off the cross-expiry strike union, or a leg copied in from
                # the Simulator, can carry a strike/expiry absent from this expiry's
                # chain). Write the coerced values back to state so get_legs() matches
                # the display. (Not an edit → no dirty flag.)
                #
                # ⚠ This pass lives HERE, above the layout dispatch, and not in
                # either body — the two layouts physically cannot drift on it,
                # and a third layout inherits it for free. Same for the
                # ``_strike_widget`` registration below.
                e_val = coerce_choice(leg.get("expiry"), exps)
                leg["expiry"] = e_val
                s_opts = strikes_for(e_val, leg.get("option_type")) or []
                s_val = coerce_strike(leg.get("strike"), s_opts)
                leg["strike"] = s_val
                sw = body(i, leg, exps, e_val, s_opts, s_val, lab)
                # Registered HERE, not in either body, for the same reason the
                # coercion above lives here: two copies of one assignment is a
                # drift surface, and this one degrades SILENTLY — the strike
                # select just keeps a stale ladder after a type/expiry flip,
                # which is the ValueError this widget exists to prevent.
                if sw is None:
                    raise RuntimeError("leg body returned no strike widget - "
                                       "_strike_widget could not be registered")
                leg["_strike_widget"] = sw
            if card:
                _card_footer()
            else:
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

    def apply_expiry(expiry):
        """Set every leg's expiry to ``expiry`` and re-render (strike selects re-sync
        to that expiry's strikes via _render's coercion). Fires on_change. The dirty
        flag is preserved — an untouched single-expiry template still routes analytic."""
        state["legs"] = set_legs_expiry(state["legs"], expiry)
        _render()
        on_change()

    return SimpleNamespace(get_legs=get_legs, set_legs=set_legs,
                           apply_template=apply_template, apply_expiry=apply_expiry,
                           refresh_options=refresh_options,
                           is_dirty=lambda: state["dirty"])
