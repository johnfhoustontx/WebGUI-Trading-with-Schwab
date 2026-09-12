"""The shared trade-entry panel mounted by the Calculator and the Simulator.

Top to bottom: a symbol bar (ticker — Enter loads — spot, strategy, a page slot,
refresh, status), an expiry strip, then the chain grid BESIDE the leg list.
Clicking a Bid adds a SELL leg and clicking an Ask a BUY leg; the page decides
what a pick means (``on_pick``) and owns the leg editor mounted into
``legs_box``. Everything this module decides is in the pure ``chain_grid`` /
``entry`` modules; this one is widgets and wiring.

Colours enter as ``tokens`` (see ``panel_tokens``) so the Calculator can paint
the panel in its near-black ``[calc]`` language while the Simulator keeps the
app-wide dark navy — the same split ``leg_editor.card_tokens`` makes.
"""
import datetime as dt
from types import SimpleNamespace

from nicegui import ui

import app_settings

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
    "btn": "text-[10px] tracking-[.12em] text-[#cdd8ee] border border-[#243353] rounded-[2px] bg-[#15213b]",
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

#: Rows each side of spot on first paint; "more" adds this many again.
GRID_WINDOW = 10
_WINDOW_STEP = 10

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
    """(call columns, put columns): each side reads OUTWARD from the strike, so
    the calls list ends with Bid, Ask and the puts list starts with them."""
    cols = cg.parse_columns(columns)
    rest = [c for c in cols if c not in _PICKS]
    return rest[::-1] + list(_PICKS), list(_PICKS) + rest


def saved_columns():
    return cg.parse_columns(app_settings.get(SETTINGS_KEY))


def build_entry_panel(*, tokens=None, strategy_value="PCS", strategy_exclude=None,
                      strategy_btn_class=None, strategy_menu_class=None,
                      today=None):
    """Mount the panel at the current slot and return its handle.

    Handle: ``symbol_in``, ``spot_lbl``, ``status_lbl``, ``refresh_btn``,
    ``strategy_sel``, ``bar_extra`` (a row the page fills), ``legs_box``,
    ``legs_footer``; ``set_chain(chain, spot, expiry=None)`` → the selected
    expiry; ``selected_expiry()``; ``set_expiry(expiry)`` (programmatic — fires
    nothing); ``on_expiry(cb)`` with ``cb(expiry)``; ``on_pick(cb)`` with
    ``cb(column, option_type, strike, expiry)``."""
    tk = panel_tokens(tokens)
    state = {"chain": None, "spot": None, "expiry": None, "window": GRID_WINDOW,
             "columns": saved_columns(), "today": today}
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
            refresh_btn = ui.button("REFRESH", color=None) \
                .props("flat dense no-caps").classes(f"entry-refresh {tk['btn']} px-2") \
                .tooltip("Re-pull the chain and quotes")
            status_lbl = ui.label("").classes(f"entry-status {tk['muted']} truncate")

        # ── expiry strip ──────────────────────────────────────────────────
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.label("EXPIRY").classes(f"{tk['eyebrow']} shrink-0")
            strip = ui.row().classes("entry-strip flex-1 min-w-0 gap-1 no-wrap overflow-x-auto")
            cols_btn = ui.button("COLUMNS", color=None) \
                .props("flat dense no-caps").classes(f"entry-columns {tk['btn']} px-2 shrink-0")
            with cols_btn:
                col_menu = ui.menu().props("auto-close=false")

        # ── the chain grid beside the legs ────────────────────────────────
        with ui.row().classes("w-full items-start gap-3 no-wrap"):
            with ui.column().classes("entry-grid flex-[1.4_1_0%] min-w-0 gap-0.5"):
                grid_title = ui.label("CHAIN").classes(tk["eyebrow"])
                grid_box = ui.column().classes("w-full min-w-0 gap-0")
            with ui.column().classes("entry-legs flex-1 min-w-[430px] gap-2"):
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
        state["window"] = GRID_WINDOW
        _paint_strip()
        _paint_grid()
        for cb in list(listeners["expiry"]):
            cb(expiry)

    def _more(step):
        state["window"] += step
        _paint_grid()

    def _paint_strip():
        strip.clear()
        pills = cg.expiry_pills(cg.chain_expiries(state["chain"] or {}), _today())
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
        col_menu.clear()
        with col_menu, ui.column().classes("gap-0 p-2"):
            for key, label in cg.GRID_COLUMNS.items():
                box = ui.checkbox(label, value=key in state["columns"]) \
                    .props("dense").classes(f"entry-col-{key}")
                if key in _PICKS:
                    box.set_enabled(False)
                box.on_value_change(lambda e, k=key: _toggle_column(k, e.value))

    def _toggle_column(key, on):
        cols = [c for c in state["columns"] if c != key] + ([key] if on else [])
        state["columns"] = cg.parse_columns(cols)
        app_settings.set(SETTINGS_KEY, list(state["columns"]))
        _paint_grid()

    def _cell(row, side, field, itm):
        c = row[side]
        text = cg.cell_text(field, c.get(field))
        tone = f" {tk[field]}" if field in _PICKS else ""
        wash = f" {tk['itm']}" if itm else ""
        if field in _PICKS:
            lbl = ui.label(text).classes(
                f"entry-pick entry-{side}-{field} {tk['cell']}{tone}{wash} "
                f"{tk['pick']} text-center px-1 py-0.5")
            lbl.on("click", lambda e, f=field, s=side, k=row["strike"]: _pick(f, s, k))
        else:
            ui.label(text).classes(f"{tk['cell']}{wash} text-center px-1 py-0.5")

    def _paint_grid():
        grid_box.clear()
        chain, expiry = state["chain"], state["expiry"]
        call_cols, put_cols = side_columns(state["columns"])
        n = len(put_cols)
        track = _GRID_TRACKS[n]
        grid_title.text = f"CHAIN · {expiry}" if expiry else "CHAIN"
        g = cg.chain_grid_rows(chain, expiry, state["spot"],
                               above=state["window"], below=state["window"])
        with grid_box:
            if not g["rows"]:
                ui.label("Load a symbol to see its chain." if not chain
                         else "No strikes listed for this expiry.") \
                    .classes(f"entry-empty {tk['muted']} py-4")
                return
            with ui.element("div").classes(f"entry-ghead {track} {tk['rule']} pb-0.5"):
                for f in call_cols:
                    ui.label(cg.GRID_COLUMNS[f]).classes(f"{tk['eyebrow']} text-center")
                ui.label("STRIKE").classes(f"{tk['eyebrow']} text-center")
                for f in put_cols:
                    ui.label(cg.GRID_COLUMNS[f]).classes(f"{tk['eyebrow']} text-center")
            ui.label("CALLS · click Bid to sell, Ask to buy · PUTS") \
                .classes(f"{tk['muted']} text-[9px] text-center w-full")
            if g["more_below"]:
                ui.button("more strikes below", color=None,
                          on_click=lambda e: _more(_WINDOW_STEP)) \
                    .props("flat dense no-caps") \
                    .classes(f"entry-more-below {tk['muted']} w-full min-h-0 text-[10px]")
            for row in g["rows"]:
                with ui.element("div").classes(f"entry-grow {track}"):
                    for f in call_cols:
                        _cell(row, "call", f, row["call_itm"])
                    ui.label(f"{row['strike']:g}").classes(
                        f"entry-strike {'entry-atm ' + tk['strike_atm'] if row['atm'] else tk['strike']} "
                        "text-center rounded-[2px] py-0.5")
                    for f in put_cols:
                        _cell(row, "put", f, row["put_itm"])
            if g["more_above"]:
                ui.button("more strikes above", color=None,
                          on_click=lambda e: _more(_WINDOW_STEP)) \
                    .props("flat dense no-caps") \
                    .classes(f"entry-more-above {tk['muted']} w-full min-h-0 text-[10px]")

    # ── the handle ────────────────────────────────────────────────────────
    def set_chain(chain, spot, expiry=None):
        """Show ``chain``. The expiry kept is ``expiry`` if listed, else the one
        already selected if still listed, else the nearest. Fires nothing."""
        state["chain"] = chain if isinstance(chain, dict) else None
        state["spot"] = spot
        exps = cg.chain_expiries(state["chain"] or {})
        if expiry in exps:
            state["expiry"] = expiry
        elif state["expiry"] not in exps:
            state["expiry"] = exps[0] if exps else None
        state["window"] = GRID_WINDOW
        _paint_strip()
        _paint_grid()
        return state["expiry"]

    def set_expiry(expiry):
        if expiry in cg.chain_expiries(state["chain"] or {}) and expiry != state["expiry"]:
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
        selected_expiry=lambda: state["expiry"],
        on_expiry=listeners["expiry"].append, on_pick=listeners["pick"].append)
