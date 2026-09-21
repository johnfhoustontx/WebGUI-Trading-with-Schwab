"""Shared editable leg-editor for the Simulator + Calculator (Tier-1).

Pure helpers (normalize/payload) are unit-tested. ``build_leg_editor`` renders
one leg per row (kind/side/strike/expiry/qty[/premium] + remove) plus an Add-leg
button; ``state['legs']`` is the single source of truth and each widget writes
its own row by index on change, so re-rendering (add/remove/template apply) never
loses in-progress edits. Each page injects ``strikes_for(expiry, otype)`` /
``expiries_for()`` (its own data source) and ``show_premium``.

Two layouts over that one model:

``layout="table"`` the entry panel's one-row-per-leg list, mounted by the
                   Calculator and the Simulator (2026-09-12): SIDE/TYPE one-click
                   toggles, a strike DROPDOWN on the real ladder (‹ › step it),
                   and a price that re-fills from the chain (``price_for``) at the
                   Bid, Mark or Ask the row's own dropdown chooses, unless the
                   user typed it. PRICE and DELTA collapse their tracks when the
                   page has no source for them. A chain-grid click MOVES the
                   matching leg (``place_pick``) rather than adding a row.
``layout="row"``   the original single-line table — mounted by Rescue.

(The 2026-09 two-line ``card`` layout was removed on 2026-09-12, once both of its
pages had moved to ``table``.)

The GEOMETRY is shared; the COLOURS enter as the ``tokens`` argument, so this
module imports no page's theme constants. ⚠ Every mount takes the defaults
today: the Calculator's near-black ``CALC_*`` repaint was the one override and
went on 2026-09-20 with the ``[calc]`` surface language. The seam stays because
the DEFAULTS below are what it makes replaceable — and because every key left
in it is a READING, not chrome.

**Six of the ten buttons here are ACTIONS and go through ``pages/ui_kit.py``**
(2026-09-20): both layouts' remove, both layouts' Add leg, Reset to template and
the typed-price reset. **Four are not buttons at all and stay raw** — the
SELL/BUY side toggle, the two ‹ › strike steppers and the cycling CALL/PUT/STOCK
picker. Each of those is a segmented or stepping control living in a ~40px table
track, and each carries a per-leg READING (long-cyan / short-green) that the
kit's four kinds cannot express; the reason is recorded in the guard's
``ALLOWED``.

⚠ **The ``row`` layout is rescue.py's screen.** It references ``tk`` nowhere, so
repainting the palette cannot reach it — the only two places this module can
change Rescue are its remove icon and its Add leg, and both are the kit. ``layout``
must keep defaulting to ``"row"``: Rescue passes none, and a changed default
would hand it the table layout with ``delta_for=None`` silently.
"""
import math
from types import SimpleNamespace

from nicegui import ui

from pages import ui_kit as kit

from . import entry as _entry
from . import strategies as S

_KEYS = ("option_type", "side", "strike", "expiry", "qty", "premium")

#: Every leg type the TYPE select can offer. ``stock`` last — nearly every leg
#: is an option and shares are the exception (D4).
TYPE_OPTIONS = ["call", "put", S.STOCK]

#: Option-only, which is what every mount gets unless it opts in.
OPTION_TYPE_OPTIONS = ["call", "put"]


def type_options(allow_stock=False) -> list:
    """The TYPE select's options for one MOUNT.

    ⚠ **Stock is opt-in per mount, and the default is off.** Gating the strategy
    MENU is not enough on its own: the Simulator mounts this same leg table, so
    an always-on ``stock`` entry would let a user hand-build a share leg on a page
    whose Replay and IV-shock engines price a ``ContractRow`` off the option chain
    and cannot value one. The Rescue ad-hoc form is worse — it BOOKS, into an
    account that holds shares in ``equity_lots`` and not in ``paper_positions``.

    Only the Calculator passes ``allow_stock=True``, which is D4's analysis
    surface.
    """
    return list(TYPE_OPTIONS) if allow_stock else list(OPTION_TYPE_OPTIONS)


#: The share-leg predicate lives in the PURE model (``strategies``), which
#: ``calculator`` can import without dragging in nicegui. Re-exported here
#: because this module is where the leg-editing helpers live.
is_stock_leg = S.is_stock_leg
_is_stock = S.is_stock_leg


def leg_strike_options(leg, options) -> list:
    """Strikes this leg may take. A share leg has none, so the control goes
    inert rather than offering a stale option strike."""
    if _is_stock(leg):
        return []
    return list(options or [])


def leg_expiry_options(leg, options) -> list:
    """Expiries this leg may take — none for a share leg, which never expires."""
    if _is_stock(leg):
        return []
    return list(options or [])


def retype_leg(leg, new_type) -> dict:
    """A COPY of ``leg`` with its type changed to ``new_type``.

    ⚠ **Crossing the stock/option boundary clears the strike and expiry, and
    that is load-bearing rather than tidy.** A share leg carrying a stale expiry
    joins the front-expiry computation in
    ``options_calculator.calc_summary_generic``; if that expiry were EARLIER than
    the real option leg's, the option would be priced with time remaining at the
    wrong horizon. Going the other way there is no remembered strike to restore,
    and inventing one would put a number the user never chose into a priced leg.

    Call↔put keeps both, which is the pre-D4 behaviour: they are option legs on
    the same ladder.
    """
    src = leg if isinstance(leg, dict) else {}
    out = {k: src.get(k) for k in _KEYS}
    out["option_type"] = new_type
    was_stock = _is_stock(src)
    now_stock = (isinstance(new_type, str)
                 and new_type.strip().lower() == S.STOCK)
    if was_stock != now_stock:
        out["strike"] = None
        out["expiry"] = None
    return out


def leg_ready(leg) -> bool:
    """Is this leg complete enough to price?

    ⚠ **A SHARE leg needs no strike, and five places on the Calculator read
    "no strike" as incomplete** — four blocked or asked the user to pick a strike
    that does not exist, and one silently DROPPED the leg, which made the page
    price a covered call as a naked short call with no warning. One predicate, so
    a sixth site added later cannot reintroduce it.

    An option leg needs a REAL strike: ``strike is None`` alone would pass a NaN
    straight through to ``bs_price`` and poison every cell of the grid.
    """
    if not isinstance(leg, dict):
        return False
    if _is_stock(leg):
        return True
    k = leg.get("strike")
    if isinstance(k, bool) or not isinstance(k, (int, float)):
        return False
    return k == k          # not NaN


def legs_ready(legs) -> bool:
    """Every leg complete, and at least one of them — "nothing" is no position."""
    legs = list(legs or [])
    return bool(legs) and all(leg_ready(l) for l in legs)


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


# ── the leg palette ──────────────────────────────────────────────────────────
# Enters as an argument so a page CAN repaint the legs — the two pages share the
# GEOMETRY, not the colours. The defaults below are the app-wide dark navy, and
# since 2026-09-20 every mount takes them: the Calculator's near-black CALC_*
# repaint went with the [calc] surface language.
# ⚠ ``side_long`` / ``side_short`` (long-cyan, short-green) and ``manual`` (the
# typed-price amber) are READINGS, not chrome — they say what a leg IS. The four
# button skins that used to sit here (``remove`` / ``remove_off`` / ``add`` /
# ``reset``) went on 2026-09-20 with the buttons they painted: the kit paints
# those now, and a token nothing reads is worse than no token.
DEFAULT_LEG_TOKENS = {
    "frame": "border border-[#213152] rounded-[2px] bg-[rgba(9,14,20,.55)]",
    "eyebrow": "text-[8px] tracking-[.14em] text-[#7f8db0] whitespace-nowrap truncate",
    "num": "text-[10px] text-[#7189a0]",
    "delta": "text-[11px] text-[#cdd8ee] whitespace-nowrap",
    # The one-click toggles (SIDE, TYPE, the strike steppers) and the
    # typed-price reset.
    "toggle": "text-[10px] tracking-[.12em] border rounded-[2px]",
    "side_long": "text-[#22d3ee] border-[#22d3ee]",
    "side_short": "text-[#2dd4a7] border-[#2dd4a7]",
    "step": "text-[12px] text-[#9db0c2]",
    "manual": "text-[11px] text-[#fbbf24]",
}


_LAYOUTS = ("row", "table")

# -- the table layout ---------------------------------------------------------
# One row per leg, and the header shares the row's track list so a caption can
# never drift off its column: # . SIDE . QTY . EXPIRY . STRIKE . TYPE .
# [PRICE FROM . PRICE] . [DELTA] . remove. The two price tracks and DELTA
# collapse when the page has no source for them - four combinations, four STATIC
# class strings.
# Drawn for a ~480px column (the entry panel's half at a 1280px window): the
# fixed tracks are as narrow as their contents allow, so the three that hold
# dropdowns - expiry, strike, price - keep the room.
_TABLE_TAIL = "gap-x-1 items-center w-full min-w-0"
_TABLE_HEAD = "grid-cols-[16px_40px_36px_minmax(0,1.15fr)_minmax(0,1.25fr)_40px"
_TABLE_GRIDS = {
    # (show_premium, show_delta) -> the track list
    (True, True): f"grid {_TABLE_HEAD}_50px_minmax(0,0.9fr)_38px_24px] {_TABLE_TAIL}",
    (True, False): f"grid {_TABLE_HEAD}_50px_minmax(0,0.9fr)_24px] {_TABLE_TAIL}",
    (False, True): f"grid {_TABLE_HEAD}_38px_24px] {_TABLE_TAIL}",
    (False, False): f"grid {_TABLE_HEAD}_24px] {_TABLE_TAIL}",
}


def _strike_text(strike):
    """A strike as the dropdown shows it: ``570``, ``567.5`` - never ``570.0``."""
    return "" if strike is None else f"{strike:g}"


def strike_choices(strikes):
    """``{strike: label}`` for the strike dropdown - the VALUE stays the float
    every consumer keys on, only the text shown changes."""
    return {k: _strike_text(k) for k in (strikes or [])}


def _usable_price(p):
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        return None
    return float(p) if math.isfinite(p) else None


def leg_tokens(overrides=None):
    """``DEFAULT_LEG_TOKENS`` with known keys overridden. Unknown keys are
    ignored, so a typo cannot silently introduce a token nothing reads; blank and
    non-string values are ignored too, so a page whose own token computed to ""
    degrades to the default look rather than to an unstyled element. A
    non-mapping ``overrides`` is ignored outright rather than raising."""
    out = dict(DEFAULT_LEG_TOKENS)
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
                     on_reset=None, allow_stock=False, price_for=None,
                     listed_expiries_for=None, on_expiry_needed=None):
    """Mount the editor into ``container``. Returns a handle with
    get_legs() / set_legs(legs) / apply_template(name) / is_dirty().

    ``layout="table"`` is the entry panel's compact one-row-per-leg list: SIDE and
    TYPE are one-click toggles, the strike is a dropdown of the real ladder (‹ ›
    step it), and ``price_for(leg, source)`` re-fills a leg's price
    from the chain at the row's chosen Bid / Mark / Ask whenever the leg becomes a
    different contract - never over a price the user typed (see
    ``entry.should_refill``). ``tokens`` overrides its palette (see
    ``leg_tokens``), ``delta_for(leg)`` supplies the per-leg delta — omit it and
    the DELTA cell collapses, exactly as ``show_premium=False`` collapses PRICE —
    ``min_legs`` floors the remove button and ``on_reset`` adds a "Reset to
    template" button beside "Add leg". The handle gains ``place_pick`` and
    ``refill_prices`` for the page's grid clicks and chain loads. All of these
    are inert in row mode.

    ``listed_expiries_for()`` widens every leg's Expiry dropdown to the symbol's
    WHOLE expiration list, where ``expiries_for()`` is only the expirations whose
    strikes are loaded. A leg picked onto an unloaded one keeps its strike while
    the ladder is fetched - ``on_expiry_needed(expiry)`` is the page's cue to
    fetch it - and snaps onto the ladder when the page calls
    ``refresh_options``. Omit both and the dropdown lists the loaded set, as
    before."""
    # A typo here would silently render the WRONG screen with nothing to see it:
    # both layouts are valid renders of the same state, so neither the page nor
    # any test would report a failure.
    if layout not in _LAYOUTS:
        raise ValueError(f"unknown leg-editor layout {layout!r}; "
                         f"expected one of {sorted(_LAYOUTS)}")
    state = {"legs": [], "dirty": False}

    table = layout == "table"

    def _set_field(i, field, value):
        if not (0 <= i < len(state["legs"])):
            return
        leg = state["legs"][i]
        leg[field] = value
        state["dirty"] = True
        pending = field == "expiry" and _awaiting_ladder(value)
        if pending and on_expiry_needed is not None:
            on_expiry_needed(value)
        if table:
            if field in ("option_type", "expiry"):
                _snap_strike(leg)
            if _entry.should_refill(field, leg.get("_manual_premium")):
                _refill(leg)
            if field in ("side", "strike", "expiry", "option_type"):
                _render()
        elif pending:
            # No ladder to sync against yet: _render keeps the strike on screen
            # until the page's refresh_options brings one.
            _render()
        elif field in ("option_type", "expiry"):
            _sync_row_strikes(i)
        on_change()

    def _awaiting_ladder(expiry):
        """A listed expiry whose strikes have not arrived yet."""
        return (listed_expiries_for is not None and expiry is not None
                and expiry not in (expiries_for() or []))

    def _refill(leg):
        """Price ``leg`` off the chain at its chosen source. A share leg, a typed
        price, or no reading leaves the price exactly as it was - "no mark" is
        never a $0.00 leg."""
        if price_for is None:
            # A page with no price column and no price source (the Simulator)
            # cannot re-price a moved leg, and the price it carried belongs to
            # the OLD contract. Drop it, so the page that does price (the
            # Calculator, via the shared position) prices the new one.
            if not show_premium and not _is_stock(leg):
                leg["premium"] = None
            return
        if leg.get("_manual_premium"):
            return
        # A share leg's price is what the shares cost: filled only while unset
        # (0.0 is what an untouched number box reports), never over a basis.
        if _is_stock(leg) and _usable_price(leg.get("premium")):
            return
        p = _usable_price(price_for(normalize_legs([leg])[0],
                                    _entry.price_source(leg.get("_price_source"))))
        if p is not None:
            leg["premium"] = round(p, 2)

    def _snap_strike(leg):
        """A leg that moved to another ladder (type or expiry) takes a strike ON
        it - the one nearest spot, the rule ``_sync_row_strikes`` applies."""
        if _is_stock(leg):
            return
        opts = strikes_for(leg.get("expiry"), leg.get("option_type")) or []
        if not opts or leg.get("strike") in opts:
            return
        spot = spot_getter() or 0
        leg["strike"] = (min(opts, key=lambda k: abs(k - spot)) if spot
                         else coerce_strike(leg.get("strike"), opts))

    def _set_type(i, value):
        """A leg's TYPE change re-SHAPES its row, so it re-renders rather than
        patching one field: crossing the stock/option boundary clears the strike
        and expiry (``retype_leg``), the column labels swap, and both of those
        controls go inert. ``_render`` re-registers ``_strike_widget``, which
        ``retype_leg`` drops along with every other non-normalized key."""
        if not (0 <= i < len(state["legs"])):
            return
        source = state["legs"][i].get("_price_source")
        state["legs"][i] = retype_leg(state["legs"][i], value)
        state["dirty"] = True
        if table:
            # retype_leg drops _manual_premium with every other private key: a
            # new contract's typed price no longer describes anything. The
            # row's price SOURCE is a choice about the row, so it stays.
            if source is not None:
                state["legs"][i]["_price_source"] = source
            _snap_strike(state["legs"][i])
            _refill(state["legs"][i])
        _render()
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

    # -- table-layout handlers ----------------------------------------------
    def _toggle_side(i):
        if 0 <= i < len(state["legs"]):
            _set_field(i, "side", "long" if state["legs"][i].get("side") == "short"
                       else "short")

    def _cycle_type(i):
        if not (0 <= i < len(state["legs"])):
            return
        opts = type_options(allow_stock)
        cur = state["legs"][i].get("option_type")
        nxt = opts[(opts.index(cur) + 1) % len(opts)] if cur in opts else opts[0]
        _set_type(i, nxt)

    def _ladder_for(leg):
        return strikes_for(leg.get("expiry"), leg.get("option_type")) or []

    def _step(i, step):
        if not (0 <= i < len(state["legs"])):
            return
        leg = state["legs"][i]
        new = _entry.step_strike(_ladder_for(leg), leg.get("strike"), step)
        if new is not None and new != leg.get("strike"):
            _set_field(i, "strike", new)

    def _pick_strike(i, value):
        """A strike chosen from the dropdown. ``None`` is the filter box being
        cleared mid-typing, not a request to un-strike the leg."""
        if not (0 <= i < len(state["legs"])) or value is None:
            return
        if value != state["legs"][i].get("strike"):
            _set_field(i, "strike", value)

    def _set_source(i, value):
        """The row's Bid / Mark / Ask choice: re-price from that side now, over a
        typed price too - choosing a source IS asking for the chain's price."""
        if not (0 <= i < len(state["legs"])):
            return
        leg = state["legs"][i]
        source = _entry.price_source(value)
        if source == _entry.price_source(leg.get("_price_source")) \
                and not leg.get("_manual_premium"):
            return          # a repaint writing the value it already holds
        leg["_price_source"] = source
        leg["_manual_premium"] = False
        state["dirty"] = True
        _refill(leg)
        _render()
        on_change()

    def _set_price(i, value, reset_btn):
        if not (0 <= i < len(state["legs"])):
            return
        leg = state["legs"][i]
        if leg.get("premium") == value:
            return          # a repaint writing the value it already holds
        leg["premium"] = value
        leg["_manual_premium"] = True
        state["dirty"] = True
        reset_btn.set_visibility(not _is_stock(leg))
        on_change()

    def _reset_price(i):
        if not (0 <= i < len(state["legs"])):
            return
        leg = state["legs"][i]
        leg["_manual_premium"] = False
        _refill(leg)
        _render()
        on_change()
    tk = leg_tokens(tokens)

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
            # One of the TWO places this module can change rescue.py's screen,
            # and a genuine action: the kit owns what a remove looks like.
            # ``w-10`` stays — the row header's trailing spacer is that wide.
            kit.icon_button("delete", tooltip="Remove leg",
                            on_click=lambda e, i=i: _remove(i)).classes("w-10")
        return sw

    def _remove_button(i):
        live = can_remove(len(state["legs"]), min_legs)
        floor = max(int(min_legs or 0), 0)
        locked_msg = f"At least {floor} leg{'' if floor == 1 else 's'} required"
        # The WRAPPER survives the kit migration, and it is load-bearing: Quasar
        # kills pointer events on a disabled q-btn, so the tooltip the kit mounts
        # INSIDE the button cannot be read at exactly the moment the explanation
        # is worth reading. The locked state therefore repeats it on the wrapper,
        # where the pointer still lands.
        with ui.element("div").classes("shrink-0 self-end flex items-center"):
            btn = kit.icon_button("close",
                                  tooltip="Remove leg" if live else locked_msg,
                                  on_click=lambda e, i=i: _remove(i))
            btn.classes("leg-remove")
            btn.set_enabled(live)
            if not live:
                ui.tooltip(locked_msg).props("delay=350").classes("max-w-[340px]")

    def _table_footer():
        with ui.row().classes("items-center gap-2 no-wrap"):
            kit.button("Add leg", kind="secondary", icon="add",
                       on_click=lambda: _add())
            if on_reset is not None:
                # QUIET: re-seeding the template is a way back, not the page's
                # action — beside Add leg it must not read as one.
                kit.button("Reset to template", kind="quiet",
                           on_click=lambda: on_reset())

    def _table_head():
        grid = _TABLE_GRIDS[(bool(show_premium), delta_for is not None)]
        caps = ["#", "SIDE", "QTY", "EXPIRY", "STRIKE", "TYPE"]
        caps += ["PRICE", ""] if show_premium else []
        caps += ["DELTA"] if delta_for is not None else []
        with ui.element("div").classes(f"leg-thead {grid} px-1.5"):
            for cap in caps + [""]:
                ui.label(cap).classes(tk["eyebrow"])

    def _table_body(i, leg, exps, e_val, s_opts, s_val, _lab):
        stock = _is_stock(leg)
        short = leg.get("side") == "short"
        grid = _TABLE_GRIDS[(bool(show_premium), delta_for is not None)]
        with ui.element("div").classes(f"leg-trow {grid} {tk['frame']} px-1.5 py-1"):
            ui.label(f"{i + 1}").classes(f"leg-num {tk['num']}")
            ui.button("SELL" if short else "BUY", color=None,
                      on_click=lambda e, i=i: _toggle_side(i)) \
                .props("flat dense no-caps") \
                .classes(f"leg-side {tk['toggle']} "
                         f"{tk['side_short'] if short else tk['side_long']} "
                         "w-full min-h-0 px-0")
            qty = ui.number(value=leg.get("qty", 1), min=1, max=100, format="%.0f") \
                .props("dense").classes("leg-qty w-full")
            qty.on_value_change(lambda e, i=i: _set_field(i, "qty", int(e.value or 1)))
            if stock:
                qty.tooltip("Lots of 100 shares")
            e_opts = leg_expiry_options(leg, exps)
            ew = ui.select(_entry.expiry_options(e_opts),
                           value=(e_val if e_opts else None)) \
                .props("dense options-dense").classes("leg-expiry w-full min-w-0")
            ew.on_value_change(lambda e, i=i: _set_field(i, "expiry", e.value))
            ew.set_enabled(bool(e_opts))
            ladder = leg_strike_options(leg, s_opts)
            with ui.element("div").classes("flex items-center gap-0.5 min-w-0 no-wrap"):
                dn = ui.button("‹", color=None, on_click=lambda e, i=i: _step(i, -1)) \
                    .props("flat dense no-caps") \
                    .classes(f"leg-strike-dn {tk['step']} px-0.5 min-h-0 min-w-0")
                # A dropdown of the real ladder, opening on the leg's strike. The
                # value is always one of its options (``_render`` coerced it), so
                # the select can never raise on an off-ladder strike.
                # ⚠ Not ``with_input``: Quasar gives that filter box a 50px
                # min-width in its own !important layer, which a page's CSS cannot
                # override, and in this track it slid under the ‹ button. A plain
                # q-select still jumps to a strike typed while it has focus.
                sw = ui.select(strike_choices(ladder), value=(s_val if ladder else None)) \
                    .props("dense options-dense") \
                    .classes("leg-strike flex-1 min-w-0")
                up = ui.button("›", color=None, on_click=lambda e, i=i: _step(i, +1)) \
                    .props("flat dense no-caps") \
                    .classes(f"leg-strike-up {tk['step']} px-0.5 min-h-0 min-w-0")
            for w in (dn, sw, up):
                w.set_enabled(bool(ladder))
            sw.on_value_change(lambda e, i=i: _pick_strike(i, e.value))
            ui.button(str(leg.get("option_type") or "call").upper(), color=None,
                      on_click=lambda e, i=i: _cycle_type(i)) \
                .props("flat dense no-caps") \
                .classes(f"leg-type {tk['toggle']} {tk['step']} w-full min-h-0 px-0")
            if show_premium:
                src = ui.select(dict(_entry.PRICE_SOURCES),
                                value=_entry.price_source(leg.get("_price_source"))) \
                    .props("dense options-dense").classes("leg-price-source w-full min-w-0")
                # No tooltip here: a tooltip child inside a q-select stops its
                # menu opening (seen in the browser, 2026-09-12).
                src.on_value_change(lambda e, i=i: _set_source(i, e.value))
                # Shares cost spot: there is no bid/ask side to choose.
                src.set_visibility(not stock)
                with ui.element("div").classes("flex items-center gap-0.5 min-w-0 no-wrap"):
                    pw = ui.number(value=leg.get("premium"), format="%.2f") \
                        .props("dense").classes("leg-price flex-1 min-w-0")
                    rb = kit.icon_button(
                        "restart_alt",
                        tooltip="Typed price - click to use the chain's price",
                        on_click=lambda e, i=i: _reset_price(i))
                    # REPLACE, never add: the kit paints its icon buttons muted
                    # and the amber is this row's "the price was typed" reading.
                    # Both are one-class ``text-[#hex]`` arbitraries, so they tie
                    # on specificity and stylesheet order alone would pick the
                    # winner — the DESK_NEON_CSS trap, one property over.
                    rb.classes(replace=f"leg-price-reset {tk['manual']} min-w-0")
                    rb.set_visibility(bool(leg.get("_manual_premium")) and not stock)
                    pw.on_value_change(lambda e, i=i, rb=rb: _set_price(i, e.value, rb))
                    if stock:
                        pw.tooltip("Price paid per share")
            if delta_for is not None:
                ui.label(delta_text(delta_for(leg))) \
                    .classes(f"leg-delta {tk['delta']} text-right")
            _remove_button(i)
        return sw

    def _render():
        container.clear()
        exps = ((listed_expiries_for() if listed_expiries_for is not None
                 else None) or expiries_for() or [])
        # ``header`` mode (row layout): the field labels move to a single header
        # row and each leg renders label-less inputs — a clean table. Default off.
        # The table layout carries its own header, so ``header`` is inert there.
        lab = (lambda _t: "") if header else (lambda t: t)
        body = _table_body if table else _row_body
        with container:
            if table:
                _table_head()
            elif header:
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
                # either body — the two layouts physically cannot drift on it.
                # Same for the ``_strike_widget`` registration below.
                # ⚠ A SHARE leg is skipped: ``coerce_choice(None, exps)`` answers
                # the first expiry, so rendering a covered call used to stamp a
                # date onto its shares — the stale-expiry hazard retype_leg and
                # apply_expiry already guard, from a third direction.
                e_val = None if _is_stock(leg) else coerce_choice(leg.get("expiry"), exps)
                leg["expiry"] = e_val
                s_opts = strikes_for(e_val, leg.get("option_type")) or []
                if (not s_opts and _awaiting_ladder(e_val)
                        and leg.get("strike") is not None):
                    # Its ladder is still loading: keep the strike the user
                    # had, as the select's one option, rather than clear it.
                    s_opts = [leg["strike"]]
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
            if table:
                _table_footer()
            else:
                # The SECOND of the two places this module can change
                # rescue.py's screen, and the other genuine action.
                kit.button("Add leg", kind="secondary", icon="add",
                           on_click=lambda: _add())

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

    def apply_template(name, near=None):
        # Place default strikes off the NEAR expiry's real strikes (not the
        # cross-expiry union), so condor/butterfly wings land on strikes that
        # actually exist for that expiry — distinct, and valid at render time (the
        # union can include strikes absent from the chosen expiry, e.g. a 737.5 from
        # another expiry that isn't in a 0DTE integer chain).
        # ``near``: lay the legs on a CHOSEN expiry (the entry panel's strip)
        # rather than the nearest; a far leg takes the next one after it. An
        # unlisted ``near`` falls back to the nearest.
        exps = expiries_for() or []
        if near in exps:
            exps = exps[exps.index(near):]
        near = exps[0] if exps else None
        placement = (strikes_for(near, "call") if near else strikes_for(None, "call")) or []
        legs = S.build_default_legs(name, spot_getter() or 0, placement, exps)
        set_legs(legs)

    def refresh_options():
        """Re-pull expiries/strikes after the page's data source loads."""
        _render()

    def apply_expiry(expiry):
        """Set every OPTION leg's expiry to ``expiry`` and re-render (strikes
        re-snap to that expiry's ladder via _render's coercion). Fires on_change.
        In place, so each row keeps its price source and typed-price flag, and a
        share leg is skipped (it has no expiry to set). The dirty
        flag is preserved — an untouched single-expiry template still routes
        analytic."""
        for leg in state["legs"]:
            if not _is_stock(leg):
                leg["expiry"] = expiry
        _render()
        on_change()

    def add_leg(leg):
        """Append one leg WITHOUT round-tripping the others through ``set_legs``,
        which would drop their typed-price flags. An edit, so it marks the legs
        dirty and fires ``on_change``."""
        src = leg if isinstance(leg, dict) else {}
        new = {k: src.get(k) for k in _KEYS}
        new["qty"] = int(src.get("qty", 1) or 1)
        state["legs"].append(new)
        state["dirty"] = True
        _render()
        on_change()

    def place_pick(leg):
        """A chain-grid click. It MOVES the leg on the same side and type (a Bid
        click on a put moves the short put; see ``entry.pick_target``) to the
        clicked contract — keeping that row's quantity and price source, dropping
        a typed price, which described the old contract — and adds a new leg
        only when nothing matches. The moved or added leg is priced here, at its
        row's source. Returns the row index it landed on."""
        src = leg if isinstance(leg, dict) else {}
        new = {k: src.get(k) for k in _KEYS}
        new["qty"] = int(src.get("qty", 1) or 1)
        i = _entry.pick_target(state["legs"], new)
        if i is None:
            state["legs"].append(new)
            i = len(state["legs"]) - 1
        else:
            cur = state["legs"][i]
            for k in ("option_type", "side", "strike", "expiry", "premium"):
                cur[k] = new[k]
            cur["_manual_premium"] = False
        _refill(state["legs"][i])
        state["dirty"] = True
        _render()
        on_change()
        return i

    def refill_prices(only_missing=False):
        """Price every leg off the chain (a fresh chain load) - skipping typed
        prices, and share legs that already have one. ``only_missing`` prices
        just the legs with no price yet: legs arriving from the other page carry
        the prices set there, and a leg's typed flag does not travel with it.
        Repaints; fires nothing, like ``set_legs``."""
        for leg in state["legs"]:
            if only_missing and _usable_price(leg.get("premium")):
                continue
            _refill(leg)
        _render()

    return SimpleNamespace(add_leg=add_leg, place_pick=place_pick,
                           refill_prices=refill_prices,
                           get_legs=get_legs, set_legs=set_legs,
                           apply_template=apply_template, apply_expiry=apply_expiry,
                           refresh_options=refresh_options,
                           is_dirty=lambda: state["dirty"])
