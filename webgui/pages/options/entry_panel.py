"""The shared trade-entry panel mounted by the Calculator and the Simulator.

Top to bottom: a symbol bar (ticker — Enter loads — spot, strategy, a page slot,
refresh, status), an expiry strip, then the chain grid BESIDE the leg list.
Clicking a Bid adds a SELL leg and clicking an Ask a BUY leg; the page decides
what a pick means (``on_pick``) and owns the leg editor mounted into
``legs_box``. Everything this module decides is in the pure ``chain_grid`` /
``entry`` modules; this one is widgets and wiring.

Colours enter as ``tokens`` (see ``panel_tokens``) so a page CAN repaint the
panel — the same split ``leg_editor.leg_tokens`` makes. ⚠ Every mount takes the
app-wide dark-navy defaults today: the Calculator's near-black ``[calc]``
repaint was the one override and went on 2026-09-20 with that language.

**The panel's two ACTIONS go through ``pages/ui_kit.py``** (2026-09-20): Load
(the page's refresh — a screen with a Symbol control bar has no header Refresh,
because its Load button IS the refresh) and Columns. **The expiry pill stays
raw**: it is a segmented picker built once per listed expiration on every
repaint, and its selected state is a class SWAP the kit's four button kinds
cannot express. That reason is recorded in the guard's ``ALLOWED``.
"""
import datetime as dt
from types import SimpleNamespace

from nicegui import ui

import app_settings
from pages import ui_kit as kit

from . import chain_grid as cg
from . import strategy_menu
from .inputs import select_all_on_focus

#: The app-wide dark navy. Every key is a class string the panel renders.
DEFAULT_PANEL_TOKENS = {
    "frame": "border border-[#213152] rounded-[3px] bg-[#101a30]",
    "eyebrow": "text-[9px] tracking-[.14em] text-[#7f8db0] whitespace-nowrap",
    "text": "text-[11px] text-[#cdd8ee]",
    "muted": "text-[11px] text-[#7f8db0]",
    "spot": "text-[15px] font-semibold text-[#eaf0fb]",
    "ticker": "w-[110px]",
    # ⚠ ``bid`` / ``ask`` / ``strike_atm`` / ``itm`` / ``pill_on`` / ``pill_off``
    # are READINGS — bid-green, ask-red, the at-the-money amber, the in-the-money
    # wash and the picker's selected state. The ``btn`` skin that used to sit here
    # went on 2026-09-20 with the two buttons it painted; the kit paints those.
    "pill_on": "text-[#eaf0fb] border-[#3b82f6] bg-[#1b2950]",
    "pill_off": "text-[#8794b4] border-[#243353] bg-transparent",
    "strike": "text-[11px] font-semibold text-[#cdd8ee] bg-[#0c1426]",
    "strike_atm": "text-[11px] font-bold text-[#fbbf24] bg-[#1b2950]",
    "itm": "bg-[rgba(59,130,246,.10)]",
    "cell": "text-[11px] text-[#cdd8ee] tabular-nums",
    "pick": "cursor-pointer rounded-[2px] hover:bg-[rgba(59,130,246,.25)]",
    "bid": "text-[#34d399]",
    "ask": "text-[#f87171]",
    "rule": "border-b border-b-[#1d2942]",
}

#: The pill's two states, as ONE swap set so repaints never stack them.
_PILL_KEYS = ("pill_on", "pill_off")

#: The grid body's height: the COMPLETE chain scrolls inside it, and every
#: paint scrolls the at-the-money row to its middle.
GRID_BODY_H = "max-h-[460px]"

#: Delegated click: one listener on the body reads the cell's data-* attributes
#: (see ``chain_grid.grid_body_html``). Non-pick clicks emit nothing.
_PICK_JS = ("(e) => { const c = e.target.closest('[data-pick]'); "
            "if (c) emit({pick: c.dataset.pick, side: c.dataset.side, "
            "strike: c.dataset.strike}); }")

#: Bid and Ask sit NEXT TO the strike on both sides (the thinkorswim layout), so
#: the click targets are the columns nearest the middle.
_PICKS = ("bid", "ask")

#: One static track list per per-side column count — a finite set (1..10).
_GRID_TRACKS = {
    n: (f"grid grid-cols-[repeat({n},minmax(0,1fr))_58px_repeat({n},minmax(0,1fr))] "
        "gap-x-1 items-center w-full")
    for n in range(1, len(cg.GRID_COLUMNS) + 1)
}

SETTINGS_KEY = "chain_grid_columns"


def panel_tokens(overrides=None):
    """``DEFAULT_PANEL_TOKENS`` with known, non-blank string keys overridden."""
    out = dict(DEFAULT_PANEL_TOKENS)
    if isinstance(overrides, dict):
        for k, v in overrides.items():
            if k in out and isinstance(v, str) and v.strip():
                out[k] = v
    return out


def side_columns(columns):
    """(call columns, put columns). The calls read left to right in registry
    order — Delta · OI · Volume · Bid · Ask by default — and the puts MIRROR them,
    so Bid and Ask sit against the strike on both sides."""
    cols = cg.parse_columns(columns)
    return list(cols), list(reversed(cols))


def center_js(element_id):
    """Scroll a grid body so its at-the-money row sits in the middle. Deferred a
    frame so it runs after the new content is in the DOM, and it moves only the
    body's own scrollTop — never the page."""
    return ("setTimeout(() => { const b = getHtmlElement(%d); "
            "const r = b && b.querySelector('[data-atm]'); "
            "if (r) { b.scrollTop = r.offsetTop - b.clientHeight / 2 "
            "+ r.offsetHeight / 2; } }, 60);" % int(element_id))


def pick_from_args(args):
    """A delegated click's payload → ``(column, option_type, strike)``, or None
    for anything that is not a well-formed Bid/Ask cell."""
    if not isinstance(args, dict):
        return None
    column, side = args.get("pick"), args.get("side")
    if column not in _PICKS or side not in ("call", "put"):
        return None
    try:
        strike = float(args.get("strike"))
    except (TypeError, ValueError):
        return None
    if strike != strike or strike in (float("inf"), float("-inf")):
        return None
    return column, side, strike


def saved_columns():
    return cg.parse_columns(app_settings.get(SETTINGS_KEY))


def build_entry_panel(*, tokens=None, strategy_value="PCS", strategy_exclude=None,
                      strategy_btn_class=None, strategy_menu_class=None,
                      today=None, grid=True, stack=False, columns=None):
    """Mount the panel at the current slot and return its handle.

    Handle: ``symbol_in``, ``spot_lbl``, ``status_lbl``, ``refresh_btn``,
    ``strategy_sel``, ``bar_extra`` (a row the page fills), ``legs_box``,
    ``legs_footer``; ``set_chain(chain, spot, expiry=None)`` → the selected
    expiry; ``selected_expiry()``; ``set_expiry(expiry)`` (programmatic — fires
    nothing); ``on_expiry(cb)`` with ``cb(expiry)``; ``on_pick(cb)`` with
    ``cb(column, option_type, strike, expiry)``.

    ``grid=False`` builds NO chain grid and no Columns picker: the public
    Calculator while its quotes switch is off, whose published chain carries
    strikes and no quotes. ``stack=True`` stacks the grid above the legs below
    ``xl`` and lets the legs column shrink, for a page read on a phone.
    ``columns`` FIXES the grid's columns and builds no Columns picker - for a
    chain that carries only some fields, where the operator's saved choice
    would draw columns of dashes. All three default to the private pages'
    layout."""
    tk = panel_tokens(tokens)
    state = {"chain": None, "spot": None, "expiry": None, "expirations": None,
             "columns": (saved_columns() if columns is None
                         else cg.parse_columns(columns)), "today": today}
    listeners = {"expiry": [], "pick": []}

    with ui.column().classes(f"entry-panel {tk['frame']} w-full gap-2 p-3"):
        # ── symbol bar ────────────────────────────────────────────────────
        with ui.row().classes("w-full items-end gap-3 flex-wrap"):
            with ui.column().classes("gap-0.5"):
                ui.label("SYMBOL").classes(tk["eyebrow"])
                symbol_in = select_all_on_focus(
                    ui.input(value="SPY")
                    .props('dense spellcheck=false input-class="uppercase font-bold"')
                    .classes(f"entry-ticker {tk['ticker']}"))
            with ui.column().classes("gap-0.5"):
                ui.label("SPOT").classes(tk["eyebrow"])
                spot_lbl = ui.label("—").classes(f"entry-spot {tk['spot']}")
            strategy_sel = strategy_menu.build_strategy_menu(
                value=strategy_value, classes="w-56", boxed=True, caption=False,
                exclude=strategy_exclude, btn_class=strategy_btn_class,
                menu_class=strategy_menu_class)
            bar_extra = ui.row().classes("items-end gap-2 flex-wrap")
            ui.element("div").classes("flex-1")
            # "Load", not "Refresh": this panel IS the page's control bar, and
            # the standard gives a screen with a Symbol field no header Refresh
            # — its Load button is the refresh (rescue.py ships the same shape).
            refresh_btn = kit.button("Load", kind="primary", icon="refresh",
                                     tooltip="Re-pull the chain and quotes") \
                .classes("entry-refresh")
            status_lbl = ui.label("").classes(f"entry-status {tk['muted']} truncate")

        # ── expiry strip ──────────────────────────────────────────────────
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.label("EXPIRY").classes(f"{tk['eyebrow']} shrink-0")
            strip = ui.row().classes("entry-strip flex-1 min-w-0 gap-1 no-wrap overflow-x-auto")
            col_menu = None
            if grid and columns is None:
                cols_btn = kit.button("Columns", kind="secondary", icon="view_column") \
                    .classes("entry-columns shrink-0")
                with cols_btn:
                    col_menu = ui.menu().props("auto-close=false")

        # ── the chain grid beside the legs ────────────────────────────────
        # ``stack``: the grid above the legs below ``xl``, and a legs column
        # free to shrink - the private pages' 430px floor would push a phone's
        # page sideways.
        row_cls = ("w-full items-start gap-3 flex flex-col xl:flex-row"
                   if stack else "w-full items-start gap-3 no-wrap")
        grid_cls = ("entry-grid w-full xl:flex-[1.4_1_0%] min-w-0 gap-0.5" if stack
                    else "entry-grid flex-[1.4_1_0%] min-w-0 gap-0.5")
        legs_cls = ("entry-legs w-full xl:flex-1 min-w-0 gap-2" if stack
                    else "entry-legs flex-1 min-w-[430px] gap-2")
        grid_title = grid_head = grid_hint = grid_empty = grid_body = None
        # A plain div when stacked: ``ui.row``'s own flex-direction would win
        # over a Tailwind ``flex-col`` (the rescue_live layout does the same).
        with (ui.element("div") if stack else ui.row()).classes(row_cls):
            if grid:
                with ui.column().classes(grid_cls):
                    grid_title = ui.label("CHAIN").classes(tk["eyebrow"])
                    # The header reserves the same scrollbar gutter as the body,
                    # so its columns line up with the scrolling rows under it.
                    grid_head = ui.element("div").classes(
                        "w-full overflow-y-hidden [scrollbar-gutter:stable]")
                    grid_hint = ui.label("CALLS · click Bid to sell, Ask to buy · PUTS") \
                        .classes(f"{tk['muted']} text-[9px] text-center w-full")
                    grid_empty = ui.label("Load a symbol to see its chain.") \
                        .classes(f"entry-empty {tk['muted']} py-4")
                    grid_body = ui.html("").classes(
                        f"entry-gridbody relative w-full {GRID_BODY_H} overflow-y-auto "
                        "[scrollbar-gutter:stable]")
                    grid_body.on("click", lambda e: _on_grid_click(e),
                                 js_handler=_PICK_JS)
            with ui.column().classes(legs_cls):
                ui.label("LEGS").classes(tk["eyebrow"])
                legs_box = ui.column().classes("w-full min-w-0 gap-1")
                legs_footer = ui.row().classes("w-full items-center gap-2 flex-wrap")

    # ── painting ──────────────────────────────────────────────────────────
    def _today():
        return state["today"] or dt.date.today()

    def _pick(column, option_type, strike):
        for cb in list(listeners["pick"]):
            cb(column, option_type, strike, state["expiry"])

    def _select(expiry):
        if expiry == state["expiry"]:
            return
        state["expiry"] = expiry
        _paint_strip()
        _paint_grid()
        for cb in list(listeners["expiry"]):
            cb(expiry)

    def _on_grid_click(e):
        pick = pick_from_args(getattr(e, "args", None))
        if pick is not None:
            _pick(*pick)

    def _loaded():
        return cg.chain_expiries(state["chain"] or {})

    def _listed():
        """Every expiration the page may pick: the service's full listing when it
        sent one, else just what the chain carries (an eager load)."""
        return list(state["expirations"] or _loaded())

    def _paint_strip():
        strip.clear()
        pills = cg.expiry_pills(_listed(), _today())
        swap = " ".join(tk[k] for k in _PILL_KEYS)
        with strip:
            for p in pills:
                on = p["value"] == state["expiry"]
                ui.button(f"{p['label']} · {p['dte']}d", color=None,
                          on_click=lambda e, v=p["value"]: _select(v)) \
                    .props("flat dense no-caps") \
                    .classes(f"entry-expiry text-[10px] border rounded-[2px] px-2 "
                             f"shrink-0 min-h-0") \
                    .classes(remove=swap, add=tk["pill_on" if on else "pill_off"])

    def _paint_columns_menu():
        if col_menu is None:
            return
        col_menu.clear()
        with col_menu, ui.column().classes("gap-0 p-2"):
            for key, label in cg.GRID_COLUMNS.items():
                box = ui.checkbox(label, value=key in state["columns"]) \
                    .props("dense").classes(f"entry-col-{key}")
                if key in _PICKS:
                    box.set_enabled(False)
                box.on_value_change(lambda e, k=key: _toggle_column(k, e.value))
        # Bid and Ask stay ticked and locked: they are the grid's click targets.

    def _toggle_column(key, on):
        cols = [c for c in state["columns"] if c != key] + ([key] if on else [])
        state["columns"] = cg.parse_columns(cols)
        app_settings.set(SETTINGS_KEY, list(state["columns"]))
        _paint_grid()

    def _paint_grid():
        if not grid:
            return
        chain, expiry = state["chain"], state["expiry"]
        call_cols, put_cols = side_columns(state["columns"])
        track = _GRID_TRACKS[len(put_cols)]
        grid_title.text = f"CHAIN · {expiry}" if expiry else "CHAIN"
        g = cg.chain_grid_rows(chain, expiry, state["spot"])
        has = bool(g["rows"])
        if not chain:
            grid_empty.text = "Load a symbol to see its chain."
        elif expiry and expiry not in _loaded():
            label = cg.expiry_pills([expiry], _today())
            grid_empty.text = f"Loading strikes for {label[0]['label'] if label else expiry}…"
        else:
            grid_empty.text = "No strikes listed for this expiry."
        for el, show in ((grid_empty, not has), (grid_head, has),
                         (grid_hint, has), (grid_body, has)):
            el.set_visibility(show)
        grid_head.clear()
        if has:
            with grid_head, ui.element("div").classes(
                    f"entry-ghead {track} {tk['rule']} pb-0.5"):
                for f in call_cols:
                    ui.label(cg.GRID_COLUMNS[f]).classes(f"{tk['eyebrow']} text-center")
                ui.label("STRIKE").classes(f"{tk['eyebrow']} text-center")
                for f in put_cols:
                    ui.label(cg.GRID_COLUMNS[f]).classes(f"{tk['eyebrow']} text-center")
        grid_body.content = cg.grid_body_html(g["rows"], call_cols, put_cols, track, tk)
        if has:
            _center_on_spot()

    def _center_on_spot():
        # Only a connected browser has a body to scroll. A chain always lands
        # after the page is up (a poll timer applies it), so nothing real is
        # skipped — this just keeps an unconnected build from queueing JS.
        client = grid_body.client
        if getattr(client, "has_socket_connection", False):
            client.run_javascript(center_js(grid_body.id))

    # ── the handle ────────────────────────────────────────────────────────
    def set_chain(chain, spot, expiry=None, expirations=None):
        """Show ``chain``. ``expirations`` is the service's full listing (a lazy
        load): every one gets a pill, and the ones whose strikes have not arrived
        say so when picked. The expiry kept is ``expiry`` if listed, else the one
        already selected if still listed, else the nearest. Fires nothing."""
        state["chain"] = chain if isinstance(chain, dict) else None
        state["spot"] = spot
        state["expirations"] = list(expirations) if expirations else None
        exps = _listed()
        if expiry in exps:
            state["expiry"] = expiry
        elif state["expiry"] not in exps:
            state["expiry"] = exps[0] if exps else None
        _paint_strip()
        _paint_grid()
        return state["expiry"]

    def set_expiry(expiry):
        if expiry in _listed() and expiry != state["expiry"]:
            state["expiry"] = expiry
            _paint_strip()
            _paint_grid()

    _paint_columns_menu()
    _paint_grid()

    return SimpleNamespace(
        symbol_in=symbol_in, spot_lbl=spot_lbl, status_lbl=status_lbl,
        refresh_btn=refresh_btn, strategy_sel=strategy_sel, bar_extra=bar_extra,
        legs_box=legs_box, legs_footer=legs_footer,
        set_chain=set_chain, set_expiry=set_expiry,
        is_loaded=lambda expiry: expiry in _loaded(),
        selected_expiry=lambda: state["expiry"],
        on_expiry=listeners["expiry"].append, on_pick=listeners["pick"].append)
