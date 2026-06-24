"""Options strategy Calculator page (Tier-3 reader).

Faithful port of the Tk calculator's two visuals: a colored summary-tile panel
(``calc_summary``) and a colored P&L heatmap grid (``calc_spread_pnl``: price ×
eval-date pairs of $ and %).

This page holds **no engine call**: the symbol quote + option-chain fetch and the
options-calculator math (summary tiles + P&L grid) live in
``services/options_svc/compute`` (``calc_load_symbol``/``calc_compute``). The
option chain is a plain JSON dict, so it round-trips through the bus cache; the
page keeps its PURE chain-extractors (``extract_atm_iv``/``extract_premium``/
``chain_expiries``/``chain_strikes``) and runs them LOCALLY on the cached chain.

Interaction model:

* **load** → enqueue ``calc_load``; a version-poll on ``options:calc_chain``
  populates price/range/expiries/strikes from the cached chain dict.
* **IV** / **Fetch premiums** operate LOCALLY on the cached chain (no command).
* **Calculate** → enqueue ``calc_compute`` with the form params; a version-poll on
  ``options:calc_result`` repaints the summary tiles + P&L grid from the cached
  ``{summary, eval_labels, pnl_data}``.

Pure transforms (banding, grid mapping, formatting, chain extractors) are
unit-tested; ``render()`` wires the form + visuals.
"""
import math

from .inputs import select_all_on_focus

# 3D / beveled button styling for the Calculator action buttons (Load / IV /
# Fetch Premiums and Calculate / Expected Move). Scoped to ``.calc-btn-3d`` and
# injected via ui.add_css (ui.html strips <style>). ``.calc-go`` is the green
# accent for the primary Calculate action. The raised look = a hard bottom shadow
# (the "lip"); pressing translates down and shrinks the lip.
CALC_CSS = """
.calc-btn-3d.q-btn{
  background:linear-gradient(180deg,#5aa0e6 0%,#3a7bc0 55%,#316eac 100%)!important;
  color:#fff!important;border-radius:7px;font-weight:600;min-height:36px;
  box-shadow:0 4px 0 0 #244e78,0 6px 10px rgba(0,0,0,.45);
  transition:transform .06s ease,box-shadow .06s ease,filter .12s ease;
}
.calc-btn-3d.q-btn:hover{filter:brightness(1.08);}
.calc-btn-3d.q-btn:active{
  transform:translateY(4px);
  box-shadow:0 1px 0 0 #244e78,0 2px 4px rgba(0,0,0,.45);
}
.calc-btn-3d.calc-go.q-btn{
  background:linear-gradient(180deg,#5cc46a 0%,#3da64f 55%,#338a43 100%)!important;
  box-shadow:0 4px 0 0 #1f5e2a,0 6px 10px rgba(0,0,0,.45);
}
.calc-btn-3d.calc-go.q-btn:active{box-shadow:0 1px 0 0 #1f5e2a,0 2px 4px rgba(0,0,0,.45);}
"""

# Dark-navy "dashboard" restyle, page-scoped under .calc-v2 (Calculator only for
# now; promotable app-wide later). Converts NiceGUI's standard underline fields to
# filled navy boxes, draws bordered cards, and styles the outline / primary buttons.
CALC_V2_CSS = """
.calc-v2{
  background:radial-gradient(130% 90% at 50% -20%,#16243f 0%,#0c1424 55%,#0a0f1c 100%);
  border:1px solid #1d2942;border-radius:14px;padding:18px 20px 22px;
}
.calc-v2 .calc-card{
  background:#101a30;border:1px solid #213152;border-radius:12px;padding:14px 16px;
}
.calc-v2 .calc-eyebrow{color:#8794b4;font-size:12px;letter-spacing:.02em;}
/* Boxed dark inputs — restyle the standard q-field control into a filled box. */
.calc-v2 .q-field__control{
  background:#0c1426;border:1px solid #243353;border-radius:8px;padding:0 10px;min-height:40px;
}
.calc-v2 .q-field__control:before,.calc-v2 .q-field__control:after{border:0!important;}
.calc-v2 .q-field--focused .q-field__control{
  border-color:#3b82f6;box-shadow:0 0 0 2px rgba(59,130,246,.28);
}
.calc-v2 .q-field__label{color:#7f8db0;}
.calc-v2 .q-field__native,.calc-v2 .q-field__native input,
.calc-v2 .q-field__native textarea,.calc-v2 .q-field__native span{color:#e7edf8!important;}
.calc-v2 .q-field__append .q-icon,.calc-v2 .q-field__prepend .q-icon{color:#8794b4;}
/* Leg table header row */
.calc-v2 .leg-head{color:#7f8db0;font-size:12px;padding:0 2px 4px;}
/* Buttons */
.calc-v2 .cv2-btn.q-btn{
  background:#15213b!important;color:#cdd8ee!important;border:1px solid #2a3a5c;
  border-radius:9px;min-height:40px;font-weight:500;
}
.calc-v2 .cv2-btn.q-btn:hover{background:#1b2950!important;}
.calc-v2 .cv2-btn-primary.q-btn{
  background:#2563eb!important;color:#fff!important;border-radius:9px;min-height:40px;font-weight:600;
}
.calc-v2 .cv2-btn-primary.q-btn:hover{background:#1d4fd1!important;}
"""

def strategy_options():
    """Flat list of strategy codes for the dropdown, in ``STRATEGY_GROUPS`` order.

    The editable leg-editor (``pages.options.strategies``) is the single source of
    truth for the strategy table, so the Calculator + Simulator never drift. Codes
    span singles / verticals / condors / butterflies / calendars; the analytic
    summary path is auto-selected per code in the Tier-2 ``calc_compute``.
    """
    from . import strategies as S

    return [code for _label, codes in S.STRATEGY_GROUPS for code in codes]


def _summary_strategy(strategy_code, legs, dirty):
    """The strategy code to send to ``calc_compute`` for summary routing.

    The analytic summary (PCS/CCS/IC/singles) is used only when the legs are
    untouched AND still MATCH the selected template (shape + single expiry); an
    edited structure, or one copied in while the dropdown reads a different code,
    falls to the generic numeric summary (``"CUSTOM"``). ``summary_code`` (shared
    with the Simulator) is the single source of truth for the match check.

    NB: imports ``strategies`` itself — the page's only ``strategies`` alias is a
    local inside ``strategy_options``, so referencing it inline in ``do_calc``
    raised ``NameError: name 'S' is not defined`` on Calculate."""
    if dirty:
        return "CUSTOM"
    from . import strategies as S
    return S.summary_code(strategy_code, legs)


def _band(frac):
    """Map a 0..1 magnitude fraction to a 1..5 shade band."""
    frac = max(0.0, min(1.0, frac))
    return max(1, min(5, math.ceil(frac / 0.2)))


def pnl_cell_class(value, g_max, g_min):
    """CSS class for a P&L cell: p1..p5 (profit), l1..l5 (loss), or neutral.

    Shade intensity is relative to the grid's global max-profit / max-loss.
    """
    if not isinstance(value, (int, float)) or value == 0:
        return "neutral"
    if value > 0:
        frac = value / g_max if isinstance(g_max, (int, float)) and g_max > 0 else 0.0
        return f"p{_band(frac)}"
    frac = value / g_min if isinstance(g_min, (int, float)) and g_min < 0 else 0.0
    return f"l{_band(frac)}"


def grid_extremes(pnl_data):
    """(global_max, global_min) over every P&L value in the grid."""
    vals = [p for r in (pnl_data or []) for p in (r.get("pnl") or [])
            if isinstance(p, (int, float))]
    if not vals:
        return 0.0, 0.0
    return max(vals), min(vals)


def grid_rows(pnl_data):
    """[{price, cells:[{pnl, pnl_pct}, ...]}, ...] from calc_spread_pnl output."""
    rows = []
    for r in pnl_data or []:
        pnls = r.get("pnl") or []
        pcts = r.get("pnl_pct") or []
        cells = [{"pnl": pnls[i] if i < len(pnls) else None,
                  "pnl_pct": pcts[i] if i < len(pcts) else None}
                 for i in range(len(pnls))]
        rows.append({"price": r.get("price"), "cells": cells})
    return rows


def eval_date_labels(dates):
    return [d.strftime("%m/%d") if hasattr(d, "strftime") else str(d) for d in dates or []]


def fmt_dollar(v):
    return f"{v:+,.0f}" if isinstance(v, (int, float)) else "—"


def fmt_pct(v):
    return f"{v:+.1f}%" if isinstance(v, (int, float)) else "—"


def api_symbol(symbol):
    """Map a user symbol to the Schwab API form ($SPX for SPX index)."""
    s = (symbol or "").strip().upper()
    return "$SPX" if s == "SPX" else s


def extract_atm_iv(chain, spot, expiry=None):
    """ATM implied vol (as a percentage) from an option-chain payload.

    Picks the contract whose strike is closest to ``spot`` and reads its
    ``volatility`` (Schwab returns it as a percentage or a decimal — normalize).
    When ``expiry`` is given, only contracts under that expiry are considered
    (chain exp keys look like ``"2026-06-19:5"``). Returns None if no usable
    volatility is found.
    """
    if not isinstance(chain, dict) or not isinstance(spot, (int, float)):
        return None
    exp_iso = None
    if expiry is not None:
        exp_iso = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
    best_diff = float("inf")
    best = None
    for map_key in ("callExpDateMap", "putExpDateMap"):
        for exp_key, strikes in (chain.get(map_key) or {}).items():
            if exp_iso and exp_key.split(":")[0] != exp_iso:
                continue
            for strike_str, contracts in (strikes or {}).items():
                try:
                    strike = float(strike_str)
                except (ValueError, TypeError):
                    continue
                if not (isinstance(contracts, list) and contracts):
                    continue
                vol = contracts[0].get("volatility")
                if vol is None:
                    continue
                diff = abs(strike - spot)
                if diff < best_diff:
                    best_diff = diff
                    best = vol if vol < 5.0 else vol / 100.0
    return None if best is None else best * 100.0


def extract_premium(chain, option_type, strike, expiry=None):
    """Premium for one leg (mark, else bid/ask mid) from the chain.

    Matches the strike within 0.51 in the call/put map for ``option_type``.
    When ``expiry`` is given, only that expiry is considered. None if not found.
    """
    if not isinstance(chain, dict) or not isinstance(strike, (int, float)):
        return None
    exp_iso = None
    if expiry is not None:
        exp_iso = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
    map_key = "callExpDateMap" if option_type == "call" else "putExpDateMap"
    for exp_key, strikes in (chain.get(map_key) or {}).items():
        if exp_iso and exp_key.split(":")[0] != exp_iso:
            continue
        for strike_str, contracts in (strikes or {}).items():
            try:
                sk = float(strike_str)
            except (ValueError, TypeError):
                continue
            if abs(sk - strike) < 0.51 and isinstance(contracts, list) and contracts:
                c = contracts[0]
                mark = c.get("mark")
                if mark and mark > 0:
                    return mark
                bid = c.get("bid", 0) or 0
                ask = c.get("ask", 0) or 0
                if bid > 0 and ask > 0:
                    return (bid + ask) / 2.0
                return None
    return None


def chain_expiries(chain):
    """Sorted unique expiry strings (YYYY-MM-DD) from an option-chain payload."""
    out = set()
    for map_key in ("callExpDateMap", "putExpDateMap"):
        for exp_key in (chain or {}).get(map_key) or {}:
            out.add(exp_key.split(":")[0])
    return sorted(out)


def chain_strikes(chain, expiry, option_type):
    """Sorted strikes for one expiry + option_type (call/put)."""
    map_key = "callExpDateMap" if option_type == "call" else "putExpDateMap"
    out = set()
    for exp_key, strikes in ((chain or {}).get(map_key) or {}).items():
        if exp_key.split(":")[0] != str(expiry):
            continue
        for s in strikes or {}:
            try:
                out.add(float(s))
            except (ValueError, TypeError):
                continue
    return sorted(out)


def _has_contracts(chain):
    return bool(chain and (chain.get("callExpDateMap") or chain.get("putExpDateMap")))


# class -> (background, foreground) for the heatmap cells (dark palette).
_CELL_COLORS = {
    "p1": ("#0d2814", "#81c784"), "p2": ("#12381b", "#a5d6a7"),
    "p3": ("#184d23", "#c8e6c9"), "p4": ("#1f5f2a", "#e8f5e9"),
    "p5": ("#2e7d32", "#ffffff"),
    "l1": ("#2a1414", "#ef9a9a"), "l2": ("#3a1a1a", "#ef5350"),
    "l3": ("#4d1f1f", "#ff8a80"), "l4": ("#6b1f1f", "#ffcdd2"),
    "l5": ("#8b0000", "#ffffff"),
    "neutral": ("#2a2a2a", "#bdbdbd"),
}


def _render_summary(box, summary):
    from nicegui import ui

    def tile(label, value, color):
        with ui.card().classes("p-2 min-w-[110px]"):
            ui.label(label).classes("text-xs opacity-60")
            ui.label(value).classes("text-base font-bold").style(f"color:{color}")

    box.clear()
    with box:
        credit = summary.get("entry_credit", 0) or 0
        if credit >= 0:
            tile("Entry Credit", f"${credit:,.2f}", "#66bb6a")
        else:
            tile("Entry Debit", f"${abs(credit):,.2f}", "#ef5350")
        tile("Max Risk", f"${summary.get('max_loss', 0):,.2f}", "#ef5350")
        tile("Max Return", f"${summary.get('max_profit', 0):,.2f}", "#66bb6a")
        tile("Return on Risk", f"{summary.get('return_on_risk', 0):.1f}%", "#bdbdbd")
        bes = summary.get("breakevens") or []
        tile("Breakeven(s)", " / ".join(f"{b:,.2f}" for b in bes) or "—", "#bdbdbd")
        tile("Prob of Profit", f"{summary.get('pop', 0):.1f}%", "#bdbdbd")


def _render_grid(box, eval_labels, pnl_data, spot):
    from nicegui import ui

    box.clear()
    rows = grid_rows(pnl_data)
    if not rows:
        with box:
            ui.label("No P&L data.").classes("opacity-60")
        return
    g_max, g_min = grid_extremes(pnl_data)
    # ``eval_labels`` arrive pre-formatted (MM/DD strings) from the service;
    # ``eval_date_labels`` is harmless here (str()'s strings) and keeps the page
    # robust if date objects are ever passed.
    labels = eval_date_labels(eval_labels)
    cur_idx = min(range(len(rows)), key=lambda i: abs((rows[i]["price"] or 0) - spot))

    th = ['<th style="position:sticky;left:0;top:0;background:#1d1d1d;z-index:2;'
          'padding:4px 8px;">Price</th>']
    for lab in labels:
        th.append(f'<th style="position:sticky;top:0;background:#1d1d1d;padding:4px 8px;">{lab} $</th>'
                  f'<th style="position:sticky;top:0;background:#1d1d1d;padding:4px 8px;">%</th>')

    trs = []
    for i, r in enumerate(rows):
        pbg = "#ffd54f" if i == cur_idx else "#2a2a2a"
        pfg = "#000000" if i == cur_idx else "#e0e0e0"
        price = r["price"] if isinstance(r["price"], (int, float)) else 0.0
        cells = [f'<td style="position:sticky;left:0;background:{pbg};color:{pfg};'
                 f'text-align:right;padding:2px 8px;font-family:monospace;font-weight:bold;">'
                 f'{price:,.2f}</td>']
        for c in r["cells"]:
            bg, fg = _CELL_COLORS[pnl_cell_class(c["pnl"], g_max, g_min)]
            style = (f'background:{bg};color:{fg};text-align:right;padding:2px 8px;'
                     f'font-family:monospace;')
            cells.append(f'<td style="{style}">{fmt_dollar(c["pnl"])}</td>')
            cells.append(f'<td style="{style}">{fmt_pct(c["pnl_pct"])}</td>')
        trs.append("<tr>" + "".join(cells) + "</tr>")

    html = ('<div style="max-height:480px;overflow:auto;border:1px solid #333;">'
            '<table style="border-collapse:collapse;font-size:12px;">'
            f'<thead><tr>{"".join(th)}</tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>')
    with box:
        ui.html(html).classes("w-full")


def render():
    """Build the Calculator page: inputs form + summary tiles + P&L heatmap.

    No engine call here — ``load`` enqueues ``calc_load`` and ``Calculate``
    enqueues ``calc_compute``; version-polls on the two cache views paint the
    chain selectors and the summary/grid. IV + Fetch premiums run the pure
    chain-extractors LOCALLY on the cached chain dict."""
    import datetime as dt

    from nicegui import ui

    import bus_client

    from pages.ui_guard import guard

    from . import handoff
    from . import leg_editor
    from . import strategy_menu

    ui.add_css(CALC_CSS)
    ui.add_css(CALC_V2_CSS)

    # Page state (local closure, not module globals — built per request).
    state = {
        "chain": None,        # last calc_load chain dict (pure-extracted locally)
        "result": None,       # last calc_result payload (summary/labels/grid)
        "chain_ver": None,    # last-seen calc_chain cache version
        "result_ver": None,   # last-seen calc_result cache version
        "iv_ver": None,       # last-seen calc_iv cache version
        "calc_spot": None,    # spot used for the last enqueued compute (grid marker)
        "pending_legs": None,  # legs copied in from the Simulator, applied on chain load
        "contracts": 1,       # last-applied Contracts count (drives per-leg qty scaling)
    }

    # ── Dark "dashboard" layout (page-scoped, .calc-v2). The functional widgets
    # keep their names; only arrangement + styling change. Right stack = Load / IV /
    # Calculate; bottom row = Fetch Premiums / Expected Move / Copy to Simulator. ──
    with ui.column().classes("calc-v2 w-full gap-4"):
        ui.label("Options Strategy Calculator").classes("text-h6").style("color:#eaf0fb")
        with ui.row().classes("items-end gap-4 flex-wrap"):
            strategy_sel = strategy_menu.build_strategy_menu(value="PCS", classes="w-52")
            symbol_in = select_all_on_focus(ui.input("Symbol", value="SPY").classes("w-40"))
        with ui.row().classes("w-full items-start gap-4 no-wrap"):
            with ui.column().classes("flex-1 min-w-0 gap-4"):
                # Inputs card
                with ui.column().classes("calc-card w-full gap-3"):
                    with ui.row().classes("items-end gap-4 flex-wrap"):
                        expiry_sel = ui.select([], label="Expiry").classes("w-40")
                        contracts_in = ui.number("Contracts", value=1, min=1, max=100).classes("w-28")
                        iv_in = ui.number("IV %", value=20.0, format="%.1f").classes("w-24")
                    with ui.row().classes("items-end gap-4 flex-wrap"):
                        price_in = ui.number("Price", value=100.0, format="%.2f").classes("w-32")
                        rate_in = ui.number("Rate %", value=4.5, format="%.2f").classes("w-24")
                        ivchg_in = ui.number("IV Δ %", value=0.0, format="%.1f").classes("w-24")
                    with ui.row().classes("items-end gap-4 flex-wrap"):
                        rmin_in = ui.number("Range min", value=0.0, format="%.2f").classes("w-28")
                        rmax_in = ui.number("Range max", value=0.0, format="%.2f").classes("w-28")
                        with ui.column().classes("gap-0"):
                            ui.label("Range %").classes("calc-eyebrow")
                            rpct_in = ui.slider(min=0, max=50, step=0.5, value=5) \
                                .props("label-always").classes("w-44")
                # Legs card (header-table editor)
                with ui.column().classes("calc-card w-full gap-2"):
                    leg_box = ui.column().classes("gap-2 w-full")
                # Bottom action buttons
                with ui.row().classes("items-center gap-3 flex-wrap"):
                    ui.button("Fetch Premiums", icon="download", color=None, on_click=lambda: fetch_premiums()) \
                        .props("no-caps").classes("cv2-btn").tooltip("Fill leg premiums from the chain")
                    ui.button("Expected Move", icon="show_chart", color=None, on_click=lambda: send_to_em()) \
                        .props("no-caps").classes("cv2-btn").tooltip("Chart the expected move for these legs")
                    ui.button("Copy to Simulator", icon="science", color=None,
                              on_click=lambda: handoff.send_to_simulator(
                                  leg_editor.legs_to_payload(
                                      (symbol_in.value or "").replace("$", "").upper(),
                                      editor.get_legs(), keep_premium=False))) \
                        .props("no-caps").classes("cv2-btn").tooltip("Open these legs in the Simulator")
            # Right action stack
            with ui.column().classes("shrink-0 gap-3").style("width:190px"):
                ui.button("Load", icon="cloud_upload", color=None, on_click=lambda: load_symbol()) \
                    .props("no-caps").classes("cv2-btn w-full").tooltip("Load price + expiries/strikes")
                ui.button("IV Update", icon="trending_up", color=None, on_click=lambda: fetch_iv()) \
                    .props("no-caps").classes("cv2-btn w-full").tooltip("Fetch / imply IV for the expiry")
                ui.button("Calculate", icon="calculate", color=None, on_click=lambda: do_calc()) \
                    .props("no-caps").classes("cv2-btn-primary w-full")
        # Results card (summary tiles + P&L grid), full width
        with ui.column().classes("calc-card w-full gap-3"):
            summary_box = ui.row().classes("gap-3 flex-wrap")
            grid_box = ui.column().classes("w-full")

    # ── editable multi-leg editor (shared with the Simulator) ────────────────
    # Strike/expiry options come from the cached chain; the editor owns the legs
    # (add/remove/edit) and tracks a ``dirty`` flag (any manual edit ⇒ the summary
    # routes through the generic numeric path with strategy="CUSTOM").
    def _strikes_for(expiry, otype):
        chain = state.get("chain") or {}
        if expiry:
            return chain_strikes(chain, expiry, otype)
        # Union across expiries — used by apply_template before a per-leg expiry
        # is set, and by the editor's pre-load empty state.
        out = set()
        for e in chain_expiries(chain):
            out.update(chain_strikes(chain, e, otype))
        return sorted(out)

    def _expiries_for():
        return chain_expiries(state.get("chain") or {})

    editor = leg_editor.build_leg_editor(
        leg_box, strikes_for=_strikes_for, expiries_for=_expiries_for,
        show_premium=True, on_change=lambda: None, header=True,
        spot_getter=lambda: float(price_in.value or 0))

    def _scale_leg_qty(factor):
        """Multiply every leg's qty by ``factor`` (RATIO-preserving) and re-render —
        how the page-level Contracts count flows onto the legs (a 1-2-1 butterfly
        scales to 10-20-10, not flattened)."""
        if factor == 1:
            return
        legs = editor.get_legs()
        if not legs:
            return
        for leg in legs:
            leg["qty"] = max(1, round(int(leg.get("qty", 1) or 1) * factor))
        editor.set_legs(legs)

    def _seed_template():
        """Apply the selected template (legs = its ratios) then scale by the current
        Contracts so the legs reflect the position size from the start."""
        editor.apply_template(strategy_sel.value)
        _scale_leg_qty(max(1, int(contracts_in.value or 1)))

    # Seed the default template (PCS). Tolerates empty strikes/expiries pre-load.
    _seed_template()
    strategy_sel.on_value_change(lambda e: _seed_template())

    @guard
    def _on_contracts_change():
        """Contracts is the position-size multiplier: scale all legs by new/old so
        changing it from 1 → 10 takes every leg's qty up 10× (ratios preserved)."""
        new = max(1, int(contracts_in.value or 1))
        old = state.get("contracts") or 1
        if new != old:
            _scale_leg_qty(new / old)
        state["contracts"] = new

    contracts_in.on_value_change(lambda e: _on_contracts_change())

    @guard
    def fetch_premiums():
        """Fill leg premiums from the CACHED chain (pure ``extract_premium``).

        Each leg is priced at its OWN expiry (falling back to the primary Expiry
        select, then to a no-expiry strike match). The filled legs are written back
        via ``editor.set_legs`` so the premium fields repaint."""
        chain = state.get("chain")
        if chain is None:
            ui.notify("Load symbol first.", type="warning")
            return
        legs = editor.get_legs()
        if any(l.get("strike") is None for l in legs):
            ui.notify("Pick all leg strikes first.", type="warning")
            return
        filled, missing = 0, []
        for leg in legs:
            strike = float(leg["strike"])
            leg_exp = leg.get("expiry") or expiry_sel.value
            prem = extract_premium(chain, leg["option_type"], strike, expiry=leg_exp)
            if prem is None:
                prem = extract_premium(chain, leg["option_type"], strike)
            if prem is not None:
                leg["premium"] = round(prem, 2)
                filled += 1
            else:
                missing.append(f"{leg['option_type']} @ {strike:g}")
        editor.set_legs(legs)
        if filled:
            ui.notify(f"Filled {filled} premium(s).", type="positive")
        if missing:
            ui.notify("No premium for: " + ", ".join(missing), type="warning")

    @guard
    def load_symbol():
        """Enqueue a ``calc_load`` for the symbol; the version-poll applies it."""
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        bus_client.request("options", {"type": "calc_load", "args": {"symbol": sym}})
        ui.notify(f"Loading {sym}…", type="info")

    @guard
    def fetch_iv():
        """Imply IV (ThinkorSwim-style) from the traded contract's live mark at the
        intraday time-to-expiry — the service solves Black-Scholes for sigma (async;
        the ``calc_iv`` poll fills the field). Falls back to the cached chain's ATM
        ``volatility`` when no leg strike/mark is available yet (pre-selection)."""
        sym = (symbol_in.value or "").strip().upper()
        if not sym or not expiry_sel.value or not price_in.value:
            ui.notify("Load symbol + pick Expiry (Price required).", type="warning")
            return
        chain = state.get("chain")
        if chain is None:
            ui.notify("Load symbol first.", type="warning")
            return
        try:
            expiry = dt.date.fromisoformat(str(expiry_sel.value))
            spot = float(price_in.value)
        except Exception as exc:
            ui.notify(f"Bad expiry/price: {exc}", type="negative")
            return

        # Prefer implying IV from the traded contract's mark (matches ToS). Pick the
        # leg whose strike is nearest spot (the most liquid mark) among legs that
        # have a chosen strike + a mark in the cached chain.
        primary = None  # (strike, option_type, mark)
        best = float("inf")
        for leg in editor.get_legs():
            sv = leg.get("strike")
            if not sv:
                continue
            strike = float(sv)
            mark = (extract_premium(chain, leg["option_type"], strike, expiry=expiry)
                    or extract_premium(chain, leg["option_type"], strike))
            if mark is None:
                continue
            d = abs(strike - spot)
            if d < best:
                best, primary = d, (strike, leg["option_type"], mark)
        if primary is not None:
            strike, otype, mark = primary
            bus_client.request("options", {"type": "calc_iv", "args": {
                "spot": spot, "strike": strike, "option_type": otype, "mark": mark,
                "expiry": str(expiry), "rate": float(rate_in.value or 4.5) / 100.0}})
            ui.notify(f"Implying IV from {otype} {strike:g} mark {mark:.2f}…",
                      type="info")
            return

        # Fallback: ATM volatility straight from the cached chain (pre-strike pick).
        iv = extract_atm_iv(chain, spot, expiry=expiry)
        approx = False
        if iv is None:
            iv = extract_atm_iv(chain, spot)  # nearest listed expiry
            approx = iv is not None
        if iv is None:
            ui.notify(f"No ATM IV for {sym} {expiry}.", type="warning")
            return
        iv_in.value = round(iv, 1)
        suffix = " (nearest listed expiry)" if approx else ""
        ui.notify(f"ATM IV {iv:.1f}%{suffix}", type="positive")

    @guard
    def do_calc():
        """Build the params dict and enqueue ``calc_compute``; the version-poll
        paints the summary tiles + P&L grid from the cached result.

        Each leg carries its OWN expiry + qty (so calendars/diagonals price each
        leg correctly and ``calc_compute`` derives the grid horizon from the front
        leg). When the user has edited the legs (``editor.is_dirty()``) the summary
        routes through the generic numeric path (``strategy="CUSTOM"``); otherwise
        the selected strategy code drives the analytic path where supported."""
        legs = editor.get_legs()
        if not legs:
            ui.notify("Add at least one leg first.", type="warning")
            return
        if any(l.get("strike") is None for l in legs):
            ui.notify("Pick all leg strikes first.", type="warning")
            return
        try:
            spot = float(price_in.value)
            page_qty = int(contracts_in.value or 1)
            page_exp = str(expiry_sel.value)
            # Route analytic vs generic summary (see _summary_strategy): a copied
            # or edited structure falls to the generic numeric summary.
            strat = _summary_strategy(strategy_sel.value, legs, editor.is_dirty())
            params = {
                "strategy": strat,
                "spot": spot,
                "iv": float(iv_in.value) / 100.0,
                "rate": float(rate_in.value) / 100.0,
                "ivadj": float(ivchg_in.value) / 100.0,
                "qty": page_qty,
                "expiry": page_exp,
                "legs": [{"strike": float(l["strike"]),
                          "premium": float(l["premium"] or 0),
                          "option_type": l["option_type"],
                          "side": l["side"],
                          "qty": int(l.get("qty", 1) or 1),
                          "expiry": l.get("expiry") or page_exp}
                         for l in legs],
                "range_min": float(rmin_in.value or 0),
                "range_max": float(rmax_in.value or 0),
                "range_pct": float(rpct_in.value or 5) / 100.0,
            }
            dt.date.fromisoformat(params["expiry"])  # validate before enqueue
        except Exception as exc:
            ui.notify(f"Calc failed: {exc}", type="negative")
            return
        state["calc_spot"] = spot
        bus_client.request("options", {"type": "calc_compute", "args": params})
        ui.notify("Calculating…", type="info")

    @guard
    def send_to_em():
        """Chart the expected move for the current legs (opens a new tab)."""
        legs = [{"strike": float(l["strike"]), "option_type": l["option_type"],
                 "side": l["side"]}
                for l in editor.get_legs() if l.get("strike") is not None]
        handoff.send_to_expected_move({
            "symbol": (symbol_in.value or "").replace("$", "").upper(),
            "expiry": str(expiry_sel.value or ""), "legs": legs})

    # ── version-poll repaint (fetch-free) ────────────────────────────────────
    def _apply_chain(cc):
        cc = cc or {}
        state["chain"] = cc.get("chain")
        if cc.get("price"):
            price_in.value = round(cc["price"], 2)
        if cc.get("range_lo") or cc.get("range_hi"):
            rmin_in.value = round(cc.get("range_lo") or 0, 2)
            rmax_in.value = round(cc.get("range_hi") or 0, 2)
        exps = chain_expiries(state["chain"] or {})
        expiry_sel.options = exps
        if exps and expiry_sel.value not in exps:
            expiry_sel.value = exps[0]
        expiry_sel.update()
        # Repopulate the per-leg expiry/strike selects from the freshly-loaded
        # chain. Pending legs copied in from the Simulator win; otherwise, when the
        # user hasn't touched the legs, re-seed the template so strikes snap to the
        # real ladder (preserves the old Load → strikes-ready behavior).
        pending = state.pop("pending_legs", None)
        if pending:
            editor.set_legs(pending)
            fetch_premiums()
            do_calc()
        elif not editor.is_dirty():
            _seed_template()   # re-seed (template ratios × Contracts), snap strikes
        else:
            editor.refresh_options()
        if cc.get("symbol") is not None:
            price = cc.get("price")
            msg = f"{cc['symbol']}: {len(exps)} expiries" + (f", {price:.2f}" if price else "")
            ui.notify(msg, type="positive" if exps else "warning")

    def _apply_result(result):
        state["result"] = result or None
        if not result:
            return
        spot = state.get("calc_spot")
        if spot is None:
            spot = float(price_in.value or 0)
        _render_summary(summary_box, result.get("summary") or {})
        _render_grid(grid_box, result.get("eval_labels") or [],
                     result.get("pnl_data") or [], spot)

    def _apply_iv(res):
        """Fill the IV field from a ``calc_iv`` result (implied from the mark)."""
        res = res or {}
        iv = res.get("iv")
        if iv is not None:
            iv_in.value = round(iv, 1)
            sk = res.get("strike")
            sk_txt = f"{sk:g}" if isinstance(sk, (int, float)) else sk
            ui.notify(f"Implied IV {iv:.1f}% ({res.get('option_type')} {sk_txt})",
                      type="positive")
        elif res.get("error"):
            ui.notify(f"Couldn't imply IV ({res['error']}). Enter it manually.",
                      type="warning")

    @guard
    def _poll_chain():
        version = bus_client.read_version("options:calc_chain")
        if version == state["chain_ver"]:
            return
        state["chain_ver"] = version
        _apply_chain(bus_client.read("options:calc_chain"))

    @guard
    def _poll_result():
        version = bus_client.read_version("options:calc_result")
        if version == state["result_ver"]:
            return
        state["result_ver"] = version
        _apply_result(bus_client.read("options:calc_result"))

    @guard
    def _poll_iv():
        version = bus_client.read_version("options:calc_iv")
        if version == state["iv_ver"]:
            return
        state["iv_ver"] = version
        _apply_iv(bus_client.read("options:calc_iv"))

    # Initial paint (graceful-empty when the service is cold). Track the current
    # versions WITHOUT applying stale cached chain/result so a fresh page doesn't
    # adopt a previous symbol's chain or grid; the user drives load/Calculate.
    state["chain_ver"] = bus_client.read_version("options:calc_chain")
    state["result_ver"] = bus_client.read_version("options:calc_result")
    state["iv_ver"] = bus_client.read_version("options:calc_iv")

    ui.timer(1.0, _poll_chain)
    ui.timer(1.0, _poll_result)
    ui.timer(1.0, _poll_iv)

    # Per signal-type: leg specs as (option_type, side, strike_field, mark_field).
    # Mirrors the legacy ``setleg`` wiring (PCS/CCS/IC); the strikes/marks come
    # straight off the scanner signal dict.
    _PREFILL_LEGS = {
        "PCS": [("put", "short", "short_strike", "short_mark"),
                ("put", "long", "long_strike", "long_mark")],
        "CCS": [("call", "short", "short_strike", "short_mark"),
                ("call", "long", "long_strike", "long_mark")],
        "IC": [("put", "short", "short_strike", "short_mark"),
               ("put", "long", "long_strike", "long_mark"),
               ("call", "short", "call_short", "call_short_mark"),
               ("call", "long", "call_long", "call_long_mark")],
    }

    def _prefill(sig):
        """Populate inputs from a scanner/swing signal (Send to Calculator).

        Builds the legs from the signal's strike/mark fields and pushes them onto
        the shared leg-editor (each leg at the signal's expiry, qty 1).

        Note: ``oc.generate_price_range`` is gone from the page, so the Range
        min/max are NOT pre-filled here (left at 0/0); ``calc_compute`` falls back
        to ``generate_price_range`` server-side at the Range %."""
        t = sig.get("type")
        if t in strategy_options():
            strategy_sel.value = t        # also re-seeds the template via on_change
        sym = (sig.get("symbol") or "").replace("$", "")
        if sym:
            symbol_in.value = sym
        price = sig.get("underlying_price")
        if price:
            price_in.value = round(price, 2)
        exp = sig.get("expiration")
        if exp:
            expiry_sel.options = [exp]
            expiry_sel.value = exp
            expiry_sel.update()
        iv = sig.get("short_iv")
        if iv:
            iv_in.value = round(iv, 1)

        legs = []
        for otype, side, strike_field, mark_field in _PREFILL_LEGS.get(t, []):
            strike = sig.get(strike_field)
            if strike in (None, 0, ""):
                continue
            mark = sig.get(mark_field)
            legs.append({"option_type": otype, "side": side,
                         "strike": float(strike), "expiry": exp, "qty": 1,
                         "premium": round(mark, 2) if mark else None})
        if legs:
            editor.set_legs(legs)
        try:
            do_calc()
        except Exception:
            pass
        ui.notify(f"Loaded {sym} {t} from scanner.", type="positive")

    _pending = handoff.take_pending_calculator()
    if _pending:
        _prefill(_pending)

    # Legs copied in from the Simulator: stash them and load the symbol; the legs
    # are applied once the chain arrives (see ``_apply_chain``'s pending path).
    _legs_in = handoff.take_pending_calculator_legs()
    if _legs_in:
        symbol_in.value = _legs_in.get("symbol") or symbol_in.value
        state["pending_legs"] = _legs_in.get("legs") or []
        load_symbol()   # enqueue calc_load; legs applied when the chain arrives
