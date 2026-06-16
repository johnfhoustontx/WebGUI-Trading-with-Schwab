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

# (key, label, option_type, side) per strategy — mirrors dashboard leg inputs.
LEG_SPECS = {
    "PCS": [("short_put", "Short Put (write)", "put", "short"),
            ("long_put", "Long Put (buy)", "put", "long")],
    "CCS": [("short_call", "Short Call (write)", "call", "short"),
            ("long_call", "Long Call (buy)", "call", "long")],
    "IC": [("short_put", "Short Put (write)", "put", "short"),
           ("long_put", "Long Put (buy)", "put", "long"),
           ("short_call", "Short Call (write)", "call", "short"),
           ("long_call", "Long Call (buy)", "call", "long")],
    "LONG_PUT": [("long_put", "Long Put (buy)", "put", "long")],
    "NAKED_PUT": [("short_put", "Short Put (sell)", "put", "short")],
    "LONG_CALL": [("long_call", "Long Call (buy)", "call", "long")],
    "NAKED_CALL": [("short_call", "Short Call (sell)", "call", "short")],
}


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

    ui.label("Calculator").classes("text-h5")

    leg_inputs: dict = {}

    with ui.row().classes("gap-3 items-end flex-wrap"):
        strategy_sel = ui.select(list(LEG_SPECS.keys()), value="PCS", label="Strategy").classes("w-36")
        symbol_in = select_all_on_focus(ui.input("Symbol", value="SPY").classes("w-24"))
        price_in = ui.number("Price", value=100.0, format="%.2f").classes("w-28")
        ui.button("load", icon="download", on_click=lambda: load_symbol()) \
            .props("flat dense size=sm").tooltip("Load price + expiries/strikes from the chain")
        expiry_sel = ui.select([], label="Expiry").classes("w-44")
        contracts_in = ui.number("Contracts", value=1, min=1, max=100).classes("w-24")
        iv_in = ui.number("IV %", value=20.0, format="%.1f").classes("w-24")
        ui.button("IV", icon="download", on_click=lambda: fetch_iv()) \
            .props("flat dense size=sm").tooltip("Fetch ATM IV for the expiry")
        ivchg_in = ui.number("IV Δ %", value=0.0, format="%.1f").classes("w-24")
        rate_in = ui.number("Rate %", value=4.5, format="%.2f").classes("w-24")

    with ui.row().classes("gap-3 items-end flex-wrap"):
        rmin_in = ui.number("Range min", value=0.0, format="%.2f").classes("w-28")
        rmax_in = ui.number("Range max", value=0.0, format="%.2f").classes("w-28")
        rpct_in = ui.number("Range %", value=5.0, format="%.1f").classes("w-24")

    leg_box = ui.column().classes("gap-2")
    # Page state (local closure, not module globals — built per request).
    state = {
        "chain": None,        # last calc_load chain dict (pure-extracted locally)
        "result": None,       # last calc_result payload (summary/labels/grid)
        "chain_ver": None,    # last-seen calc_chain cache version
        "result_ver": None,   # last-seen calc_result cache version
        "calc_spot": None,    # spot used for the last enqueued compute (grid marker)
    }

    def _sync_strikes():
        chain = state.get("chain")
        if not chain or not expiry_sel.value:
            return
        spot = float(price_in.value or 0)
        for info in leg_inputs.values():
            strikes = chain_strikes(chain, expiry_sel.value, info["option_type"])
            sel = info["strike"]
            sel.options = strikes
            if strikes and sel.value not in strikes:
                sel.value = min(strikes, key=lambda s: abs(s - spot)) if spot else strikes[0]
            sel.update()

    def rebuild_legs():
        leg_box.clear()
        leg_inputs.clear()
        with leg_box:
            for key, label, otype, side in LEG_SPECS[strategy_sel.value]:
                with ui.row().classes("items-end gap-2"):
                    ui.label(label).classes("w-44 text-sm")
                    sin = ui.select([], label="Strike").classes("w-28")
                    pin = ui.number("Premium", value=0.0, format="%.2f").classes("w-28")
                    leg_inputs[key] = {"strike": sin, "premium": pin,
                                       "option_type": otype, "side": side}
        _sync_strikes()

    ui.button("Fetch premiums", icon="download", on_click=lambda: fetch_premiums()) \
        .props("flat dense size=sm").tooltip("Fill leg premiums from the chain (strikes required)")

    rebuild_legs()
    strategy_sel.on_value_change(lambda e: rebuild_legs())
    expiry_sel.on_value_change(lambda e: _sync_strikes())

    @guard
    def fetch_premiums():
        """Fill leg premiums from the CACHED chain (pure ``extract_premium``)."""
        if not expiry_sel.value:
            ui.notify("Load symbol + pick an Expiry first.", type="warning")
            return
        chain = state.get("chain")
        if chain is None:
            ui.notify("Load symbol first.", type="warning")
            return
        legs = []
        for info in leg_inputs.values():
            if not info["strike"].value:
                ui.notify("Pick all leg strikes first.", type="warning")
                return
            legs.append((info, float(info["strike"].value)))
        try:
            expiry = dt.date.fromisoformat(str(expiry_sel.value))
        except Exception as exc:
            ui.notify(f"Bad expiry: {exc}", type="negative")
            return
        filled, missing = 0, []
        for info, strike in legs:
            prem = extract_premium(chain, info["option_type"], strike, expiry=expiry)
            if prem is None:
                prem = extract_premium(chain, info["option_type"], strike)
            if prem is not None:
                info["premium"].value = round(prem, 2)
                filled += 1
            else:
                missing.append(f"{info['option_type']} @ {strike:g}")
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
        """Read ATM IV from the CACHED chain (pure ``extract_atm_iv``)."""
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

    ui.button("Calculate", icon="calculate", on_click=lambda: do_calc())
    summary_box = ui.row().classes("gap-3 flex-wrap")
    grid_box = ui.column().classes("w-full")

    @guard
    def do_calc():
        """Build the params dict and enqueue ``calc_compute``; the version-poll
        paints the summary tiles + P&L grid from the cached result."""
        try:
            spot = float(price_in.value)
            qty = int(contracts_in.value or 1)
            params = {
                "strategy": strategy_sel.value,
                "spot": spot,
                "iv": float(iv_in.value) / 100.0,
                "rate": float(rate_in.value) / 100.0,
                "ivadj": float(ivchg_in.value) / 100.0,
                "qty": qty,
                "expiry": str(expiry_sel.value),
                "legs": [{"strike": float(info["strike"].value),
                          "premium": float(info["premium"].value),
                          "option_type": info["option_type"],
                          "side": info["side"], "qty": qty}
                         for info in leg_inputs.values()],
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
        _sync_strikes()
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

    # Initial paint (graceful-empty when the service is cold). Track the current
    # versions WITHOUT applying stale cached chain/result so a fresh page doesn't
    # adopt a previous symbol's chain or grid; the user drives load/Calculate.
    state["chain_ver"] = bus_client.read_version("options:calc_chain")
    state["result_ver"] = bus_client.read_version("options:calc_result")

    ui.timer(1.0, _poll_chain)
    ui.timer(1.0, _poll_result)

    def _prefill(sig):
        """Populate inputs from a scanner/swing signal (Send to Calculator).

        Note: ``oc.generate_price_range`` is gone from the page, so the Range
        min/max are NOT pre-filled here (left at 0/0); ``calc_compute`` falls back
        to ``generate_price_range`` server-side at the Range %."""
        t = sig.get("type")
        if t in LEG_SPECS:
            strategy_sel.value = t
        rebuild_legs()
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

        def setleg(key, strike, mark):
            info = leg_inputs.get(key)
            if not info or strike in (None, 0):
                return
            info["strike"].options = [float(strike)]
            info["strike"].value = float(strike)
            info["strike"].update()
            if mark:
                info["premium"].value = round(mark, 2)

        if t == "PCS":
            setleg("short_put", sig.get("short_strike"), sig.get("short_mark"))
            setleg("long_put", sig.get("long_strike"), sig.get("long_mark"))
        elif t == "CCS":
            setleg("short_call", sig.get("short_strike"), sig.get("short_mark"))
            setleg("long_call", sig.get("long_strike"), sig.get("long_mark"))
        elif t == "IC":
            setleg("short_put", sig.get("short_strike"), sig.get("short_mark"))
            setleg("long_put", sig.get("long_strike"), sig.get("long_mark"))
            setleg("short_call", sig.get("call_short"), sig.get("call_short_mark"))
            setleg("long_call", sig.get("call_long"), sig.get("call_long_mark"))
        try:
            do_calc()
        except Exception:
            pass
        ui.notify(f"Loaded {sym} {t} from scanner.", type="positive")

    _pending = handoff.take_pending_calculator()
    if _pending:
        _prefill(_pending)
