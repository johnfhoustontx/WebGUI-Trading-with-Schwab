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

from .inputs import select_all_on_focus, should_load
# Shared dark-navy "dashboard" theme (Calculator + Simulator inject the SAME CSS so
# the look never drifts). Kept under its historical name here.
from .theme import (QUASAR_INTERNAL_CSS, PAGE, CARD, BTN, BTN_PRIMARY, LABEL,
                    TXT_POS, TXT_NEG, TXT_NEUTRAL, TILE_3D)
from . import page_state as _ps

# Persisted (single-user) Calculator input snapshot — survives navigation + browser
# reload, resets on a webgui restart (same as the other persisting pages). The pure
# snapshot/merge/precedence helpers live in page_state.py.
_CALC_KEYS = ("symbol", "strategy", "legs", "iv", "rate", "ivadj", "contracts",
              "price", "num_strikes", "expiry")
_CALC_DEFAULTS = {"symbol": "SPY", "strategy": "PCS", "legs": [], "iv": 20.0,
                  "rate": 4.5, "ivadj": 0.0, "contracts": 1, "price": 100.0,
                  "num_strikes": 24, "expiry": None}
_LAST_CALC: dict = {}

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


def _find_contract(chain, option_type, strike, expiry=None):
    """The first chain contract matching one leg: call/put map, strike within
    0.51, and — when ``expiry`` is given — that expiry only. None if not found.

    The shared walk behind ``extract_premium`` and ``extract_delta``, so the
    premium and the delta on one leg row are always read off the SAME contract
    and can never come from different strikes.
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
                return contracts[0]
    return None


def extract_premium(chain, option_type, strike, expiry=None):
    """Premium for one leg (mark, else bid/ask mid) from the chain.

    Matches the strike within 0.51 in the call/put map for ``option_type``.
    When ``expiry`` is given, only that expiry is considered. None if not found.
    """
    c = _find_contract(chain, option_type, strike, expiry)
    if c is None:
        return None
    mark = c.get("mark")
    if mark and mark > 0:
        return mark
    bid = c.get("bid", 0) or 0
    ask = c.get("ask", 0) or 0
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return None


# ── the redesign's page-side readouts ────────────────────────────────────────
# Everything the rebuilt screen shows beyond today's page — the per-leg delta,
# the ③ LEGS strip, the matrix % column, the status pill — is derived HERE, from
# payloads options_svc already caches. No Tier-2 change; no second pricing model.

# Mirror of UNLIMITED (999999) in the scanner's options-calculator module — a
# magic placeholder the service returns VERBATIM for an uncapped max_profit
# (LONG_CALL) or max_loss (NAKED_CALL). It is NOT infinity: divide by it and
# every matrix cell reads +0.0%; format it and the tile reads "$999,999".
# Tier-1 may not import that module, so the value is restated here; keep the
# two in step. (The name is written hyphenated on purpose — the Tier-1 guard
# test bans the module's import token anywhere in this file, comments included.)
UNLIMITED = 999999

# Past this, a "delta" is the chain's missing-greek SENTINEL rather than a
# reading: Schwab sends -999.0 for a greek it does not have, and a real option
# delta never leaves [-1, 1]. ``options_svc.flow_alerts.detect_big_delta`` drops
# the same contracts for the same reason.
_DELTA_LIMIT = 1.0

# Contracts per option — the multiplier on every dollar figure below.
_SHARES_PER_CONTRACT = 100


def is_unlimited(v):
    """Whether a summary figure is the service's uncapped sentinel."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == UNLIMITED


def _finite(v):
    """``v`` as a float when it is a real finite number, else None.

    Bools are rejected: ``True`` is not a premium, and ``isinstance(True, int)``
    would otherwise let one through as 1.0."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _leg_qty(leg):
    """A leg's contract count as a float, or None when it is unusable.

    A missing or falsy qty means one contract (the page's long-standing
    ``int(leg.get("qty", 1) or 1)`` convention); junk means None, so the caller
    degrades to an em-dash rather than pricing an unknown size."""
    return _finite(leg.get("qty", 1) or 1)


def extract_delta(chain, option_type, strike, expiry=None):
    """Per-contract delta for one leg from the cached chain, or ``None``.

    Reads the chain's OWN ``delta`` (the field ``flow_alerts`` uses), so this is
    market delta rather than a second pricing model living in Tier 1.

    ⚠ Returns ``None`` — never ``0.0`` — when the contract carries no usable
    delta. Index option chains read hollow outside regular hours, and a ``0.00``
    on an otherwise live-looking row is a confident wrong number. A genuine
    ``0.0`` (a far out-of-the-money contract) IS kept; what is rejected is the
    missing-greek sentinel, i.e. anything outside ``[-1, 1]``.
    """
    c = _find_contract(chain, option_type, strike, expiry)
    d = _finite(c.get("delta")) if isinstance(c, dict) else None
    if d is None or abs(d) > _DELTA_LIMIT:
        return None
    return d


def position_delta(delta, side):
    """Contract delta signed for the POSITION: a short leg inverts it.

    PER CONTRACT — deliberately not multiplied by ``qty``. The leg row shows it
    beside its own QTY cell, and this is the number that compares directly with
    a broker's chain. An aggregate would be ``sum(position_delta(d, side) * qty)``
    and belongs in its own function, not folded in here."""
    if isinstance(delta, bool) or not isinstance(delta, (int, float)):
        return None
    return -delta if side == "short" else delta


def net_premium(legs):
    """Net cash at entry in dollars: positive = credit received, negative = paid.

    ``None`` when any leg is unpriced. Legs arrive with ``premium=None`` until
    Fetch premiums runs, and counting those as zero would print "NET $0" over a
    position whose cash is simply not known yet. No legs is a true ``0.0``."""
    total = 0.0
    for leg in legs or []:
        prem = _finite(leg.get("premium"))
        qty = _leg_qty(leg)
        if prem is None or qty is None or qty < 0:
            return None
        sign = 1 if leg.get("side") == "short" else -1
        total += sign * prem * qty * _SHARES_PER_CONTRACT
    return round(total, 2)          # a cash figure: cents are the meaningful unit


def _short_outlives_long(legs):
    """True when a SHORT leg expires LATER than a long one (a reverse calendar).

    The single-date payoff below would model that short as settled while it is
    still live, and report an uncovered credit as riskless."""
    shorts = [str(l.get("expiry")) for l in legs or []
              if l.get("side") == "short" and l.get("expiry")]
    longs = [str(l.get("expiry")) for l in legs or []
             if l.get("side") != "short" and l.get("expiry")]
    return bool(shorts and longs and max(shorts) > min(longs))


def max_loss_estimate(legs):
    """The ③ LEGS strip's max-loss figure, in dollars, or ``None``.

    The MINIMUM of the position's expiration payoff. That curve is piecewise
    linear with corners only at the strikes, so evaluating ``net_premium`` plus
    intrinsic value at ``{0} ∪ strikes`` finds the true minimum exactly — no
    pricing model, no width heuristic, and no per-structure special cases. It is
    therefore right for everything the templates build and for anything edited by
    hand: an iron condor risks ONE side rather than both, a 1-2-1 butterfly's
    qty-2 middle leg needs no handling of its own, and a lone short put reads its
    real ``strike × 100 − credit`` instead of a width that does not exist.

    ``None`` — an em-dash — rather than a wrong dollar figure when:

    * a leg is unpriced or has no strike (nothing to evaluate);
    * the net call quantity is SHORT, so loss grows without bound above the
      last strike (a naked call, a call ratio credit);
    * a short leg outlives a long one, which this single-date model cannot see.

    ⚠ Every leg is settled at ONE date. For the templates' calendars/diagonals
    (short front, long back) that is the standard convention and gives the right
    answer — the back leg's residual value only helps — but it is an assumption,
    not a valuation. The authoritative MAX RISK tile still comes from the
    service's summary; this is the header strip.
    """
    net = net_premium(legs)
    if net is None:
        return None
    rows = []
    for leg in legs or []:
        strike = _finite(leg.get("strike"))
        qty = _leg_qty(leg)
        if strike is None or qty is None or qty < 0:
            return None
        rows.append(("call" if leg.get("option_type") == "call" else "put",
                     1 if leg.get("side") != "short" else -1, qty, strike))
    if not rows:
        return 0.0
    if _short_outlives_long(legs):
        return None
    if sum(sign * qty for kind, sign, qty, _k in rows if kind == "call") < 0:
        return None

    def _payoff(spot):
        v = 0.0
        for kind, sign, qty, strike in rows:
            intrinsic = max(spot - strike, 0.0) if kind == "call" else max(strike - spot, 0.0)
            v += sign * qty * _SHARES_PER_CONTRACT * intrinsic
        return net + v

    worst = min(_payoff(s) for s in [0.0] + [r[3] for r in rows])
    return round(-worst, 2) if worst < 0 else 0.0


def matrix_pct_of_max(pnl, max_profit):
    """A matrix cell's P&L as a percentage of the structure's MAX RETURN.

    Replaces the service's ``pnl_pct`` (a share of premium received) so the
    column reads directly against the MAX RETURN tile above the matrix.

    ``None`` — an em-dash, never a 0.0% that would read like a measurement —
    when there is no max return to divide by, INCLUDING the uncapped sentinel:
    there is no percentage of an unlimited return."""
    p = _finite(pnl)
    m = _finite(max_profit)
    if p is None or m is None or m <= 0 or is_unlimited(m):
        return None
    return p / m * 100.0


def chain_status_facts(loading, symbol, chain):
    """The title-bar status pill + the ② SYMBOL frame's hint.

    ``{state, label, hint}`` where state is ``idle`` / ``loading`` / ``ready``.
    An EMPTY chain dict is ``idle``, not ``ready`` — a chain that arrived
    carrying nothing is not a loaded chain, and colouring the frame for it would
    announce data the page does not have."""
    if loading:
        return {"state": "loading", "label": "LOADING CHAIN", "hint": "···"}
    if _has_contracts(chain):
        sym = (symbol or "").strip().upper()
        return {"state": "ready",
                "label": f"CHAIN LOADED · {sym}" if sym else "CHAIN LOADED",
                "hint": "LIVE"}
    return {"state": "idle", "label": "AWAITING SYMBOL", "hint": "NOT LOADED"}


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

# Scroll the P&L grid so the current-spot row sits in the MIDDLE of the scroll
# viewport (the grid is spot-centered but opens scrolled to the top, hiding spot).
# Scrolls only the grid container, never the page.
_CENTER_SPOT_JS = """
(() => {
  const sc = document.getElementById('calc-grid-scroll');
  const row = document.getElementById('calc-spot-row');
  if (!sc || !row) return;
  const sr = sc.getBoundingClientRect(), rr = row.getBoundingClientRect();
  sc.scrollTop += (rr.top - sr.top) - (sc.clientHeight / 2) + (rr.height / 2);
})()
"""


# Summary-tile color palette → Tailwind text token. The hexes are a finite set
# (positive / negative / neutral) chosen in ``_render_summary``; map them to the
# shared theme tokens so the tiles carry no inline-style color.
_TILE_COLOR_CLASSES = {
    "#66bb6a": TXT_POS,
    "#ef5350": TXT_NEG,
    "#bdbdbd": TXT_NEUTRAL,
}


def tile_color_class(color):
    """Map a summary-tile hex color → its Tailwind text token (neutral fallback)."""
    return _TILE_COLOR_CLASSES.get(color, TXT_NEUTRAL)


def _render_summary(box, summary):
    from nicegui import ui

    def tile(label, value, color):
        with ui.card().classes(f"p-2 min-w-[110px] {TILE_3D}"):
            ui.label(label).classes("text-xs opacity-60")
            ui.label(value).classes(f"text-base font-bold {tile_color_class(color)}")

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

    th = ['<th style="position:sticky;left:0;top:0;background:#141a30;z-index:2;'
          'padding:4px 8px;">Price</th>']
    for lab in labels:
        th.append(f'<th style="position:sticky;top:0;background:#141a30;padding:4px 8px;">{lab} $</th>'
                  f'<th style="position:sticky;top:0;background:#141a30;padding:4px 8px;">%</th>')

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
        tr_open = '<tr id="calc-spot-row">' if i == cur_idx else "<tr>"
        trs.append(tr_open + "".join(cells) + "</tr>")

    html = ('<div id="calc-grid-scroll" style="max-height:480px;overflow:auto;border:1px solid #333;">'
            '<table style="border-collapse:collapse;font-size:12px;">'
            f'<thead><tr>{"".join(th)}</tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>')
    with box:
        ui.html(html).classes("w-full")
        # Centre the spot row in the viewport once the DOM has painted.
        ui.timer(0.12, lambda: ui.run_javascript(_CENTER_SPOT_JS), once=True)


def strikes_window(strikes, spot, n):
    """The P&L grid's price rows: the ``n`` strikes ≤ spot plus the ``n`` strikes
    > spot (strictly ±n around spot — far-OTM legs beyond it fall off the grid).

    Junk / non-numeric strikes are ignored and duplicates collapsed."""
    xs = sorted({float(s) for s in (strikes or []) if isinstance(s, (int, float))})
    if not xs or spot is None:
        return []
    below = [s for s in xs if s <= spot][-n:]
    above = [s for s in xs if s > spot][:n]
    return below + above


def render():
    """Build the Calculator page: inputs form + summary tiles + P&L heatmap.

    No engine call here — ``load`` enqueues ``calc_load`` and ``Calculate``
    enqueues ``calc_compute``; version-polls on the two cache views paint the
    chain selectors and the summary/grid. IV + Fetch premiums run the pure
    chain-extractors LOCALLY on the cached chain dict."""
    import datetime as dt

    from nicegui import ui, run

    import bus_client

    from pages.ui_guard import guard, guard_async

    from . import handoff
    from . import leg_editor
    from . import strategy_menu
    from . import overlay as _overlay

    ui.add_css(QUASAR_INTERNAL_CSS)

    # Full-screen wait overlay shown while a user-initiated Load is in flight.
    wait = _overlay.build_loading_overlay()

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
        "restoring": False,   # True while restoring a persisted snapshot (suppress enqueues)
        "last_loaded": None,   # last symbol a Load was triggered for (tab/Enter dedup)
        "loading": False,      # True while a user-initiated load is in flight (overlay up)
        "applying": False,     # True while _apply_chain/_prefill set Expiry programmatically
        "chain_fetching": False,  # in-flight guard for the off-loop big-chain read
    }

    # ── Dark "dashboard" layout (page-scoped, .calc-v2). The functional widgets
    # keep their names. LEFT = input section (inputs card + Load/IV/Calculate stack,
    # legs, bottom actions); RIGHT = the P&L matrix, which h-scrolls so it never
    # overlaps the inputs. ───────────────────────────────────────────────────────
    with ui.column().classes(f"calc-v2 {PAGE} w-full gap-4"):
        ui.label("Options Strategy Calculator").classes(f"text-h6 {LABEL}")
        with ui.row().classes("items-end gap-4 flex-wrap"):
            strategy_sel = strategy_menu.build_strategy_menu(value="PCS", classes="w-52", boxed=True)
            symbol_in = select_all_on_focus(ui.input("Symbol", value="SPY").classes("w-40"))
        with ui.row().classes("w-full items-start gap-4 no-wrap"):
            # LEFT: input section (fixed width so the legs table never collides with
            # the matrix beside it).
            with ui.column().classes("shrink-0 gap-4 w-[720px]"):
                with ui.row().classes("w-full items-start gap-4 no-wrap"):
                    with ui.column().classes(f"{CARD} flex-1 min-w-0 gap-3"):
                        with ui.row().classes("items-end gap-4 flex-wrap"):
                            expiry_sel = ui.select([], label="Expiry").classes("w-40")
                            contracts_in = ui.number("Contracts", value=1, min=1, max=100).classes("w-28")
                            iv_in = ui.number("IV %", value=20.0, format="%.1f").classes("w-24")
                        with ui.row().classes("items-end gap-4 flex-wrap"):
                            price_in = ui.number("Price", value=100.0, format="%.2f").classes("w-32")
                            rate_in = ui.number("Rate %", value=4.5, format="%.2f").classes("w-24")
                            ivchg_in = ui.number("IV Δ %", value=0.0, format="%.1f").classes("w-24")
                        with ui.row().classes("items-end gap-4 flex-wrap"):
                            # The P&L grid spans ±N real chain strikes around spot
                            # (replaces the old Range min/max/%). See ``strikes_window``.
                            nstrikes_in = ui.number("Number of strikes", value=24, min=1,
                                                    max=200, format="%.0f").classes("w-40") \
                                .tooltip("Strikes shown either side of spot in the P&L grid")
                    with ui.column().classes("shrink-0 gap-3 w-[170px]"):
                        ui.button("Load", icon="cloud_upload", color=None, on_click=lambda: load_symbol(show_wait=True)) \
                            .props("no-caps").classes(f"{BTN} w-full").tooltip("Load price + expiries/strikes")
                        ui.button("IV Update", icon="trending_up", color=None, on_click=lambda: fetch_iv()) \
                            .props("no-caps").classes(f"{BTN} w-full").tooltip("Fetch / imply IV for the expiry")
                        ui.button("Calculate", icon="calculate", color=None, on_click=lambda: do_calc()) \
                            .props("no-caps").classes(f"{BTN_PRIMARY} w-full")
                # Legs card (header-table editor)
                with ui.column().classes(f"{CARD} w-full gap-2"):
                    leg_box = ui.column().classes("gap-2 w-full")
                # Bottom action buttons
                with ui.row().classes("items-center gap-3 flex-wrap"):
                    ui.button("Fetch Premiums", icon="download", color=None, on_click=lambda: fetch_premiums()) \
                        .props("no-caps").classes(BTN).tooltip("Fill leg premiums from the chain")
                    ui.button("Expected Move", icon="show_chart", color=None, on_click=lambda: send_to_em()) \
                        .props("no-caps").classes(BTN).tooltip("Chart the expected move for these legs")
                    ui.button("Copy to Simulator", icon="science", color=None,
                              on_click=lambda: handoff.send_to_simulator(
                                  leg_editor.legs_to_payload(
                                      (symbol_in.value or "").replace("$", "").upper(),
                                      editor.get_legs(), keep_premium=False))) \
                        .props("no-caps").classes(BTN).tooltip("Open these legs in the Simulator")
            # RIGHT: P&L matrix. min-w-0 + the grid's own overflow lets it h-scroll
            # instead of pushing into the inputs.
            with ui.column().classes(f"{CARD} flex-1 min-w-0 gap-3 self-stretch"):
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
        show_premium=True, on_change=lambda: _capture(), header=True,
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
        if state.get("restoring"):
            return
        editor.apply_template(strategy_sel.value)
        _scale_leg_qty(max(1, int(contracts_in.value or 1)))

    # ── persist + restore full UI state across navigation (single-user) ───────
    def _capture():
        """Snapshot the current inputs into the module-level _LAST_CALC (cheap dict
        write; wired to every input change). No-op while restoring."""
        if state.get("restoring"):
            return
        _LAST_CALC.clear()
        _LAST_CALC.update(_ps.snapshot({
            "symbol": (symbol_in.value or "").strip().upper(),
            "strategy": strategy_sel.value, "legs": editor.get_legs(),
            "iv": iv_in.value, "rate": rate_in.value, "ivadj": ivchg_in.value,
            "contracts": int(contracts_in.value or 1), "price": price_in.value,
            "num_strikes": int(nstrikes_in.value or 24), "expiry": expiry_sel.value,
        }, _CALC_KEYS))

    def _restore(snap):
        """Apply a persisted snapshot to the widgets under the restoring guard (so
        wiring fires no stray commands). Legs ride ``pending_legs`` so the post-load
        ``_apply_chain`` applies them (keeping their premiums) and recomputes."""
        s = _ps.merge_restore(snap, _CALC_DEFAULTS)
        state["restoring"] = True
        try:
            symbol_in.value = s["symbol"]
            strategy_sel.value = s["strategy"]
            iv_in.value = s["iv"]
            rate_in.value = s["rate"]
            ivchg_in.value = s["ivadj"]
            contracts_in.value = s["contracts"]
            state["contracts"] = s["contracts"]
            price_in.value = s["price"]
            nstrikes_in.value = s["num_strikes"]
            if s["expiry"]:
                expiry_sel.options = [s["expiry"]]
                expiry_sel.value = s["expiry"]
                expiry_sel.update()
            state["pending_legs"] = s["legs"] or None
        finally:
            state["restoring"] = False

    # Seed the default template (PCS). Tolerates empty strikes/expiries pre-load.
    _seed_template()
    strategy_sel.on_value_change(
        lambda e: None if state.get("restoring") else (_seed_template(), _capture()))

    @guard
    def _on_contracts_change():
        """Contracts is the position-size multiplier: scale all legs by new/old so
        changing it from 1 → 10 takes every leg's qty up 10× (ratios preserved)."""
        if state.get("restoring"):
            return
        new = max(1, int(contracts_in.value or 1))
        old = state.get("contracts") or 1
        if new != old:
            _scale_leg_qty(new / old)
        state["contracts"] = new

    contracts_in.on_value_change(lambda e: (_on_contracts_change(), _capture()))
    # Persist the remaining inputs on change (cheap dict write; no command).
    for _w in (iv_in, rate_in, ivchg_in, price_in, nstrikes_in):
        _w.on_value_change(lambda e: _capture())
    # Symbol: tab-out / Enter simulate Load (deduped); value-change still persists.
    symbol_in.on_value_change(lambda e: _capture())
    symbol_in.on("keydown.enter", lambda e: _symbol_submit())
    symbol_in.on("focusout", lambda e: _symbol_submit())

    @guard
    def _on_expiry_change():
        """Top-level Expiry → propagate to ALL legs (re-syncs each leg's strikes).
        Suppressed while restoring or while _apply_chain/_prefill set it programmatically."""
        if state.get("restoring") or state.get("applying"):
            return
        editor.apply_expiry(expiry_sel.value)
        _capture()

    expiry_sel.on_value_change(lambda e: _on_expiry_change())

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
    def load_symbol(show_wait=False):
        """Enqueue a ``calc_load`` for the symbol; the version-poll applies it.

        ``show_wait`` (user-initiated: Load button / symbol tab-out / Enter) shows the
        centered wait overlay until the chain arrives (or a safety timeout).
        Mount-time auto-loads (restore / handoff) pass show_wait=False."""
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        if show_wait and state.get("loading"):
            # Collapses the focusout-then-button-click double fire while a load is in
            # flight. (The Load button still force-reloads once loading clears — it
            # bypasses should_load, unlike the Trade page's button-through-dedup.)
            return
        state["last_loaded"] = sym
        if show_wait:
            state["loading"] = True
            wait.show(f"Loading {sym}…")
            ui.timer(_overlay.LOAD_TIMEOUT_SEC, _load_timeout, once=True)
        bus_client.request("options", {"type": "calc_load", "args": {"symbol": sym}})
        ui.notify(f"Loading {sym}…", type="info")

    @guard
    def _load_timeout():
        """Safety net: if the chain never arrived, drop the overlay + reset the dedup
        so a retry re-triggers (e.g. the service is down)."""
        if state.get("loading"):
            state["loading"] = False
            wait.hide()
            state["last_loaded"] = None

    @guard
    def _symbol_submit():
        """Tab-out / Enter on the symbol field → Load (deduped: only when changed)."""
        if not should_load((symbol_in.value or "").strip().upper(), state.get("last_loaded")):
            return
        load_symbol(show_wait=True)

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
                "num_strikes": int(nstrikes_in.value or 24),
                # Grid rows = the ±N real chain strikes around spot (front-expiry
                # ladder, union of calls+puts). None when no chain yet → engine
                # falls back to its even-step ±N heuristic.
                "price_rows": strikes_window(
                    sorted(set(_strikes_for(expiry_sel.value, "call"))
                           | set(_strikes_for(expiry_sel.value, "put"))),
                    spot, int(nstrikes_in.value or 24)) or None,
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
        state["loading"] = False
        wait.hide()
        state["chain"] = cc.get("chain")
        if cc.get("price"):
            price_in.value = round(cc["price"], 2)
        exps = chain_expiries(state["chain"] or {})
        state["applying"] = True
        try:
            expiry_sel.options = exps
            if exps and expiry_sel.value not in exps:
                expiry_sel.value = exps[0]
            expiry_sel.update()
        finally:
            state["applying"] = False
        # Repopulate the per-leg expiry/strike selects from the freshly-loaded
        # chain. Pending legs copied in from the Simulator win; otherwise, when the
        # user hasn't touched the legs, re-seed the template so strikes snap to the
        # real ladder (preserves the old Load → strikes-ready behavior).
        pending = state.pop("pending_legs", None)
        if pending:
            editor.set_legs(pending)
            # Copy-from-Simulator legs carry no premium → fetch from the chain; a
            # restored snapshot keeps the user's own premiums (don't clobber them).
            if any(l.get("premium") in (None, 0) for l in pending):
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

    @guard_async
    async def _poll_chain():
        # The :ver probe stays ON the event loop (cheap). The chain payload
        # (cache:options:calc_chain is the full ~7000-contract chain, ~10 MB — a
        # blocking GET + JSON parse) is read OFF the loop via run.io_bound so it
        # never blocks other clients. The version-gate means the big read only
        # happens when a new chain was published (not every 1 s tick), and the
        # in-flight guard stops a slow read from stacking across ticks.
        version = bus_client.read_version("options:calc_chain")
        if version == state["chain_ver"] or state.get("chain_fetching"):
            return
        state["chain_ver"] = version
        state["chain_fetching"] = True
        try:
            chain = await run.io_bound(bus_client.read, "options:calc_chain")
        finally:
            state["chain_fetching"] = False
        _apply_chain(chain)

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

        Builds the legs from the signal's strike/mark fields and stashes them as
        ``pending_legs``, then **loads the symbol's chain** — the legs are applied
        (and the grid computed) once the chain arrives, in ``_apply_chain``. This
        mirrors the Copy-from-Simulator path: applying legs BEFORE the chain loads
        wiped every strike, because the leg-editor coerces each strike against the
        cached chain's strike ladder (empty pre-load → strike cleared to None) —
        which is exactly the "legs don't transfer" bug.

        Note: ``oc.generate_price_range`` is gone from the page, so the Range
        min/max are NOT pre-filled here; ``calc_compute`` derives the grid rows
        from the loaded chain's strike ladder."""
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
            state["applying"] = True
            try:
                expiry_sel.options = [exp]
                expiry_sel.value = exp
                expiry_sel.update()
            finally:
                state["applying"] = False
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
        # Apply once the chain lands (valid strikes); load_symbol enqueues calc_load.
        state["pending_legs"] = legs or None
        load_symbol()
        ui.notify(f"Loaded {sym} {t} from scanner — loading chain…", type="positive")

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

    # Restore the persisted snapshot — but only when no handoff consumed the seed
    # (an explicit Copy/Send-to-Calculator wins). Auto-refresh: reload the chain
    # (fresh price) → _apply_chain applies the restored legs + recomputes.
    if not _pending and not _legs_in and _LAST_CALC:
        _restore(_LAST_CALC)
        load_symbol()
