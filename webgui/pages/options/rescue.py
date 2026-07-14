"""Rescue page (Tier-3 reader) — at-risk positions + rescue advisories.

Engine-free renderer: the at-risk filtering, rescue-state/heat scoring, and the
ranked candidate rescue actions are all computed in ``services/options_svc`` and
read from Redis (``cache:options:paper_account`` / ``cache:options:captured`` /
``cache:options:rescue:<id>``). This module holds only the PURE display builders
(unit-tested) plus a thin ``render()`` (added in Task 7.2).

The pure builders below MUST import without a NiceGUI app context, so nicegui is
imported lazily inside ``render()`` only (mirrors ``expected_move.py`` /
``simulator.py``).
"""

from .theme import BADGE_NEG, BADGE_POS, BADGE_WARN, BTN_3D

# Heat zone colors (higher heat = closer to trouble): green → amber → orange →
# red. Reuses the shared palette idiom from scanner.py / svg.py (#ef5350 red,
# #ffa726 amber, #66bb6a green) so the UI stays consistent; orange bridges the
# amber→red gap for the 50-75 zone.
HEAT_GREEN = "#66bb6a"
HEAT_AMBER = "#ffa726"
HEAT_ORANGE = "#ff7043"
HEAT_RED = "#ef5350"

# Cash (credit/debit) text colors — same green/red as pnl_color in captured.py.
CASH_GREEN = "#66bb6a"
CASH_RED = "#ef5350"
CASH_NEUTRAL = "#9e9e9e"

# rescue_state values that put a position on the at-risk board.
_AT_RISK_STATES = ("tested", "critical")

# Strategy codes the ad-hoc rescue engine can advise on today. Selecting anything
# else (debit spreads, singles, all-call/put condors/butterflies, calendars,
# diagonals) pops a "not available yet" message. Extended as coverage grows — see
# docs/plans/2026-06-23-rescue-adhoc-calculator-tab-design.md.
RESCUE_ADHOC_SUPPORTED = ("PCS", "CCS", "IC", "IRON_BUTTERFLY",
                          "LONG_CALL", "LONG_PUT", "NAKED_CALL", "NAKED_PUT")


def heat_color(heat):
    """CSS color for a 0-100 heat value by zone (None / non-numeric -> green).

    <25 green · 25-50 amber · 50-75 orange · >=75 red."""
    try:
        h = float(heat)
    except (TypeError, ValueError):
        return HEAT_GREEN
    if h < 25:
        return HEAT_GREEN
    if h < 50:
        return HEAT_AMBER
    if h < 75:
        return HEAT_ORANGE
    return HEAT_RED


def heat_bg_class(heat):
    """Deep Slate badge token for a 0-100 heat value (tinted bg + colored fg).

    <25 green · 25-50 amber · >=50 red (the mock's "red if >=50" with an amber
    mid-band). None / non-numeric -> green. Used by the heat-cell slot."""
    try:
        h = float(heat)
    except (TypeError, ValueError):
        return BADGE_POS
    if h < 25:
        return BADGE_POS
    if h < 50:
        return BADGE_WARN
    return BADGE_NEG


def heat_border_class(heat):
    """Left-border + faint fill Tailwind classes
    (``border-l-4 border-[<hex>] bg-[<hex>]/[.13]``) tinting an at-risk row's
    symbol cell, or '' when heat is missing/non-numeric.

    The faint fill (``/[.13]`` ≈ the old inline ``${color}22`` alpha) is the
    primary at-a-glance at-risk signal — a 4px border alone is easy to miss on a
    dense scrolling table. Distinct from ``heat_color`` (which defaults missing
    heat to green): a row with no heat value gets NO tint. ``rescue_highlight``
    still gates on the rescue STATE, so a non-at-risk row never gets a tint even
    with valid heat."""
    if _num(heat) is None:
        return ""
    c = heat_color(heat)
    return f"border-l-4 border-[{c}] bg-[{c}]/[.13]"


def cash_class(value):
    """Tailwind ``text-[<hex>]`` class for a credit/debit value by sign:
    positive green · negative red · zero/missing neutral. Mirrors ``cash_text``."""
    v = _num(value)
    if v is None or round(v) == 0:
        return f"text-[{CASH_NEUTRAL}]"
    if v > 0:
        return f"text-[{CASH_GREEN}]"
    return f"text-[{CASH_RED}]"


def _num(value, default=None):
    """Coerce to float, else ``default`` (handles None / strings safely)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _strikes_text(row):
    """'short/long' strike pair, dropping missing sides ('500/495', '500', '')."""
    short = row.get("short_strike")
    long = row.get("long_strike")
    parts = [f"{s:g}" if isinstance(s, (int, float)) else None for s in (short, long)]
    parts = [p for p in parts if p is not None]
    return "/".join(parts)


def _underlying_vs_short(row):
    """Short string like '498 vs 500' (underlying vs short strike), else ''."""
    under = _num(row.get("underlying"))
    short = _num(row.get("short_strike"))
    if under is None and short is None:
        return ""
    u = f"{under:g}" if under is not None else "—"
    s = f"{short:g}" if short is not None else "—"
    return f"{u} vs {s}"


def at_risk_rows(paper_view, captured_view):
    """Display rows for positions/signals on the rescue board.

    Includes paper positions whose ``rescue_state`` is "tested"/"critical", plus
    captured signals flagged the same way (captured is advisory-only and usually
    carries no rescue_state, so none are included unless explicitly flagged).
    Sorted by heat desc. Defensive: missing keys → safe defaults, empty/None
    views → []."""
    rows = []

    for pos in (paper_view or {}).get("positions") or []:
        state = pos.get("rescue_state") or "ok"
        if state not in _AT_RISK_STATES:
            continue
        rows.append(_row_from(pos, source="paper",
                              id_field="position_id", strategy_field="strategy"))

    for sig in (captured_view or {}).get("signals") or []:
        state = sig.get("rescue_state") or "ok"
        if state not in _AT_RISK_STATES:
            continue
        rows.append(_row_from(sig, source="captured",
                              id_field="signal_id", strategy_field="strategy",
                              alt_strategy_field="type"))

    rows.sort(key=lambda r: r["heat"], reverse=True)
    return rows


def _row_from(src, source, id_field, strategy_field, alt_strategy_field=None):
    strategy = src.get(strategy_field)
    if not strategy and alt_strategy_field:
        strategy = src.get(alt_strategy_field)
    return {
        "id": src.get(id_field) or src.get("id") or src.get("symbol") or "",
        "source": source,
        "symbol": src.get("symbol") or "",
        "strategy": strategy or "",
        "strikes": _strikes_text(src),
        "dte": src.get("dte"),
        "expiration": src.get("expiration"),
        "underlying_vs_short": _underlying_vs_short(src),
        "short_delta": _round2(_num(src.get("current_short_delta"))),
        "pnl": _round2(_num(src.get("unrealized_pnl"))),
        "heat": _num(src.get("heat"), 0.0) or 0.0,
        "state": src.get("rescue_state") or "ok",
    }


def _round2(v):
    """Round a float to 2dp (kills binary-float tails like -41.0000000000014);
    leaves None as None."""
    return round(v, 2) if isinstance(v, (int, float)) else v


def cash_text(value):
    """Credit/debit display dict: {'text': '+$120'|'-$45'|'$0', 'color': ...,
    'class': 'text-[<hex>]'}.

    Positive = credit (green), negative = debit (red), zero/missing = neutral.
    ``class`` is the Tailwind equivalent of ``color`` (for the .classes() render
    path); ``color`` is retained for back-compat with existing callers/tests."""
    v = _num(value)
    cls = cash_class(value)
    if v is None or round(v) == 0:
        return {"text": "$0", "color": CASH_NEUTRAL, "class": cls}
    mag = abs(round(v))
    if v > 0:
        return {"text": f"+${mag}", "color": CASH_GREEN, "class": cls}
    return {"text": f"-${mag}", "color": CASH_RED, "class": cls}


# Metric label/key/formatter map for a candidate card (only non-None shown).
def _fmt_cash(v):
    return f"${v:,.0f}"


def _fmt_delta(v):
    return f"{v:.2f}"


def _fmt_plain(v):
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


_CANDIDATE_METRICS = (
    ("new_max_loss", "Max loss", _fmt_cash),
    ("new_breakeven", "Breakeven", _fmt_plain),
    ("new_short_delta", "Short delta", _fmt_delta),
    ("new_width", "Width", _fmt_plain),
    ("new_expiry", "Expiry", _fmt_plain),
    ("dte_after", "DTE after", _fmt_plain),
)


def _leg_text(leg):
    """'SELL PUT 500 @1.20' from an est_fill_legs entry (defensive)."""
    side = (leg.get("side") or "").upper()
    right = (leg.get("right") or "").upper()
    strike = leg.get("strike")
    strike_s = f"{strike:g}" if isinstance(strike, (int, float)) else str(strike or "")
    price = leg.get("price")
    parts = [p for p in (side, right, strike_s) if p]
    text = " ".join(parts)
    if isinstance(price, (int, float)):
        text = f"{text} @{price:.2f}"
    return text


def candidate_card_rows(advisory):
    """One display dict per ranked candidate in the advisory (already ordered).

    Each: {title, apply_kind, gross_text, commission_text, net_text (cash_text),
    metrics [list of 'Label: value' for non-None new_* fields, $ for cash, 2dp
    for delta], legs [list of 'SELL PUT 500 @1.20'], rationale, context,
    warnings, score}. Defensive: advisory with error / no candidates → []."""
    adv = advisory or {}
    if adv.get("error"):
        return []
    cards = []
    for cand in adv.get("candidates") or []:
        metrics = []
        for key, label, fmt in _CANDIDATE_METRICS:
            val = cand.get(key)
            if val is None:
                continue
            try:
                rendered = fmt(val)
            except (TypeError, ValueError):
                rendered = str(val)
            metrics.append(f"{label}: {rendered}")
        cards.append({
            "title": cand.get("label") or cand.get("action") or "Rescue",
            "apply_kind": cand.get("apply_kind") or "advisory",
            "gross_text": cash_text(cand.get("gross_cash")),
            "commission_text": cash_text(
                -abs(c) if (c := _num(cand.get("commission"))) is not None else None),
            "net_text": cash_text(cand.get("net_cash")),
            "metrics": metrics,
            "legs": [_leg_text(l) for l in cand.get("est_fill_legs") or []],
            "rationale": list(cand.get("rationale") or []),
            "context": list(cand.get("context") or []),
            "warnings": list(cand.get("warnings") or []),
            "score": cand.get("score"),
        })
    return cards


def summary_line(advisory):
    """One-line headline for the advisory.

    Normal: 'SPY PCS — TESTED · heat 72 · 6 rescue options'. Error → the error.
    With apply_result: prepend 'Applied <action> ✓' / 'Prices moved — re-review'."""
    adv = advisory or {}
    if adv.get("error"):
        return str(adv["error"])

    prefix = ""
    res = adv.get("apply_result")
    if res:
        action = res.get("action") or "rescue"
        if res.get("ok"):
            prefix = f"Applied {action} ✓ · "
        elif res.get("stale"):
            prefix = "Prices moved — re-review · "
        elif res.get("error"):
            prefix = f"Apply failed: {res['error']} · "
        else:
            prefix = "Apply failed · "

    symbol = adv.get("symbol") or "?"
    strategy = adv.get("strategy") or ""
    state = (adv.get("state") or "ok").upper()
    heat = adv.get("heat")
    heat_s = f"{heat:g}" if isinstance(heat, (int, float)) else "—"
    n = len(adv.get("candidates") or [])
    opt_word = "option" if n == 1 else "options"

    head = f"{symbol} {strategy}".strip()
    return f"{prefix}{head} — {state} · heat {heat_s} · {n} rescue {opt_word}"


# ── ad-hoc trade rescue (pure spec mapping from leg-editor legs) ──────────────
# Error surfaced when the legs aren't a recognized supported structure.
_ADHOC_STRUCT_ERR = ("Rescue supports single options and credit spreads / iron "
                     "condors/flies — this structure isn't recognized.")

# (option_type, side) → single-option strategy code (Phase 1).
_SINGLE_STRAT = {
    ("call", "long"): "LONG_CALL", ("put", "long"): "LONG_PUT",
    ("call", "short"): "NAKED_CALL", ("put", "short"): "NAKED_PUT",
}


def adhoc_spec_from_legs(symbol, legs):
    """Map leg-editor legs → the ad-hoc rescue spec ``compute_rescue_adhoc``
    consumes (``{symbol, strategy, short_strike, long_strike, call_short?,
    call_long?, expiration, quantity, entry_credit}``), or ``{"error": "..."}``.

    Credit structures ONLY (PCS / CCS / IC — an iron fly folds into IC). Each leg
    is ``{option_type: "call"/"put", side: "long"/"short", strike, expiry, qty,
    premium}`` (premium per share). Rules:

    * every leg needs a numeric strike (else error);
    * a SINGLE expiration across all legs (no calendars);
    * exactly one short + one long put (short > long) and no calls → PCS;
    * exactly one short + one long call (short < long) and no puts → CCS;
    * a valid put credit spread AND a valid call credit spread → IC (iron fly =
      the put/call shorts may share a strike);
    * anything else (single leg, only shorts, a debit spread, a ratio, wrong
      counts) → error;
    * ``entry_credit`` (per share) = Σ short premiums − Σ long premiums, must be
      > 0 (missing premium treated as 0);
    * ``quantity`` = the short leg's qty (default 1).
    Pure (no nicegui); never raises."""
    parsed = []
    for leg in legs or []:
        strike = _num((leg or {}).get("strike"))
        if strike is None:
            return {"error": "Every leg needs a strike."}
        parsed.append({
            "option_type": str(leg.get("option_type") or "").strip().lower(),
            "side": str(leg.get("side") or "").strip().lower(),
            "strike": strike,
            "expiry": str(leg.get("expiry") or "").strip(),
            "qty": int(_num(leg.get("qty"), 1) or 1),
            "premium": _num(leg.get("premium"), 0.0) or 0.0,
        })
    if not parsed:
        return {"error": "Add at least one leg."}

    expiries = {leg["expiry"] for leg in parsed if leg["expiry"]}
    if len(expiries) > 1:
        return {"error": "Rescue needs a single expiration (no calendars)."}
    expiration = next(iter(expiries), "")

    # Single-leg structures (Phase 1): long/naked call & put. entry_credit is
    # SIGNED — a long pays a debit (negative), a naked short receives a credit (+).
    if len(parsed) == 1:
        leg = parsed[0]
        strat = _SINGLE_STRAT.get((leg["option_type"], leg["side"]))
        if strat is None:
            return {"error": _ADHOC_STRUCT_ERR}
        signed = leg["premium"] if leg["side"] == "short" else -leg["premium"]
        return {
            "symbol": str(symbol or "").strip().upper(),
            "strategy": strat,
            "short_strike": leg["strike"],
            "expiration": expiration,
            "quantity": leg["qty"] or 1,
            "entry_credit": signed,
        }

    put_short = [leg for leg in parsed if leg["option_type"] == "put" and leg["side"] == "short"]
    put_long = [leg for leg in parsed if leg["option_type"] == "put" and leg["side"] == "long"]
    call_short = [leg for leg in parsed if leg["option_type"] == "call" and leg["side"] == "short"]
    call_long = [leg for leg in parsed if leg["option_type"] == "call" and leg["side"] == "long"]
    n_puts = len(put_short) + len(put_long)
    n_calls = len(call_short) + len(call_long)

    valid_pcs = (len(put_short) == 1 and len(put_long) == 1
                 and put_short[0]["strike"] > put_long[0]["strike"])
    valid_ccs = (len(call_short) == 1 and len(call_long) == 1
                 and call_short[0]["strike"] < call_long[0]["strike"])

    spec: dict = {}
    if n_calls == 0 and valid_pcs:
        spec["strategy"] = "PCS"
        spec["short_strike"] = put_short[0]["strike"]
        spec["long_strike"] = put_long[0]["strike"]
        short_qty = put_short[0]["qty"]
    elif n_puts == 0 and valid_ccs:
        spec["strategy"] = "CCS"
        spec["short_strike"] = call_short[0]["strike"]
        spec["long_strike"] = call_long[0]["strike"]
        short_qty = call_short[0]["qty"]
    elif valid_pcs and valid_ccs:
        spec["strategy"] = "IC"
        spec["short_strike"] = put_short[0]["strike"]
        spec["long_strike"] = put_long[0]["strike"]
        spec["call_short"] = call_short[0]["strike"]
        spec["call_long"] = call_long[0]["strike"]
        short_qty = put_short[0]["qty"]
    else:
        return {"error": _ADHOC_STRUCT_ERR}

    entry_credit = (sum(leg["premium"] for leg in parsed if leg["side"] == "short")
                    - sum(leg["premium"] for leg in parsed if leg["side"] == "long"))
    if entry_credit <= 0:
        return {"error": "Not a net-credit structure (entry credit must be positive)."}

    spec.update({
        "symbol": str(symbol or "").strip().upper(),
        "expiration": expiration,
        "quantity": short_qty or 1,
        "entry_credit": entry_credit,
    })
    return spec


# ── render-only helpers ──────────────────────────────────────────────────────
def at_risk_columns():
    """Column defs for the at-risk ui.table."""
    return [
        {"name": "symbol", "label": "Symbol", "field": "symbol", "align": "left"},
        {"name": "strategy", "label": "Strat", "field": "strategy", "align": "left"},
        {"name": "strikes", "label": "Strikes", "field": "strikes", "align": "left"},
        {"name": "strike_date", "label": "Strike Date", "field": "expiration",
         "align": "left"},
        {"name": "short_delta", "label": "Δ short", "field": "short_delta", "align": "right"},
        {"name": "pnl", "label": "P&L", "field": "pnl", "align": "right"},
        {"name": "heat", "label": "Heat", "field": "heat", "align": "right"},
        {"name": "state", "label": "State", "field": "state", "align": "left"},
    ]


def _table_rows(rows):
    """Add an ``_heat_class`` field (consumed by the body-cell-heat slot) + an
    ``id``-keyed row each ui.table row needs."""
    out = []
    for r in rows:
        out.append({**r, "_heat_class": heat_bg_class(r.get("heat"))})
    return out


# Sticky table header: the at-risk board's own body scrolls (bounded height) while
# the column headers stay pinned. Dark bg matches the Quasar dark card.
_RESCUE_CSS = """
.rescue-table .q-table__middle { max-height: 72vh; }
.rescue-table thead tr th {
  position: sticky; top: 0; z-index: 1;
  background-color: #141a30;
}
"""


def render():
    """Render the Rescue page (NiceGUI) — two tabs.

    Tier-3 reader. **At-Risk Board** version-polls ``options:paper_account`` +
    ``options:captured`` for the board, enqueues a ``rescue`` command on row-click,
    and version-polls the per-id ``options:rescue:<id>`` advisory to paint the
    ranked candidate cards. **Ad-hoc Trade** is a Calculator-style leg editor
    (strategy picker + Symbol/Load + Expiry + Contracts + the full leg table) that
    maps its legs to a credit-structure spec (``adhoc_spec_from_legs``) and enqueues
    ``rescue_adhoc``; its own version-poll on ``options:rescue:adhoc`` paints the
    (advisory-only) cards. The two tabs track their selection/advisory SEPARATELY so
    they never cross-wire. Mirrors ``calculator.py`` / ``simulator.py`` idioms
    (``bus_client.request`` / ``read`` / ``read_version`` + ``@guard``)."""
    import bus_client
    from nicegui import run, ui

    from pages.ui_guard import guard, guard_async

    from .calculator import chain_expiries, chain_strikes
    from . import leg_editor
    from .strategy_menu import build_strategy_menu
    from .strategies import strategy_label

    def _adhoc_unsupported(code):
        """True + pops a 'not available yet' message when a strategy the rescue
        engine can't advise on yet is selected/computed."""
        if code in RESCUE_ADHOC_SUPPORTED:
            return False
        ui.notify(f"Rescue for '{strategy_label(code)}' is not available yet.",
                  type="warning", timeout=4000)
        return True

    ui.add_css(_RESCUE_CSS)

    # Page state (local closure, not module globals — built per request). Board and
    # ad-hoc keep independent selection + advisory tracking.
    state: dict = {
        "paper": None, "paper_ver": None,
        "captured": None, "captured_ver": None,
        "board_id": None, "board_source": None,
        "board_advisory": None, "board_advisory_ver": None,
        "rows_by_id": {},          # id -> raw display row (for source/symbol)
        "adhoc_selected": False,
        "adhoc_advisory": None, "adhoc_advisory_ver": None,
    }
    # Ad-hoc chain sub-state (shares the Calculator's calc_chain cache).
    adhoc: dict = {"chain": None, "spot": 0.0, "chain_ver": None,
                   "chain_fetching": False, "contracts": 1}

    # No page title — the tab strip names the page (2026-07-11 cleanup).

    # ── waiting-for-service placeholder ──────────────────────────────────────
    waiting = ui.label("Waiting for options service…").classes("opacity-70")

    # ── shared candidate-card rendering (per-container so each tab owns its own
    # cards column + advisory head; the board passes the confirm/apply factory,
    # the ad-hoc tab passes a no-op since its cards are all advisory-only) ──────
    def _render_one_card(container, card, apply_factory):
        with container:
            with ui.card().classes("w-full"):
                with ui.row().classes("items-center gap-3 w-full"):
                    ui.label(card["title"]).classes("text-subtitle1")
                    if card.get("score") is not None:
                        ui.badge(f"score {card['score']:g}"
                                 if isinstance(card["score"], (int, float))
                                 else f"score {card['score']}").props("color=primary")
                    ui.space()
                    if card["apply_kind"] == "execute":
                        ui.button("Apply", icon="play_arrow", color=None,
                                  on_click=apply_factory(card)).props("no-caps").classes(BTN_3D)
                    else:
                        ui.label("manual — place yourself").classes("opacity-70 text-sm")
                # Gross / commission / net cash line (cash_text colors).
                with ui.row().classes("items-center gap-4"):
                    for lbl, key in (("Gross", "gross_text"), ("Comm", "commission_text"),
                                     ("Net", "net_text")):
                        cell = card[key]
                        with ui.row().classes("items-center gap-1"):
                            ui.label(f"{lbl}:").classes("opacity-70 text-sm")
                            ui.label(cell["text"]).classes(cell["class"])
                if card["metrics"]:
                    with ui.row().classes("gap-4 flex-wrap"):
                        for m in card["metrics"]:
                            ui.label(m).classes("text-sm")
                if card["legs"]:
                    with ui.column().classes("gap-0"):
                        for leg in card["legs"]:
                            ui.label(leg).classes("text-sm font-mono")
                for r in card["rationale"]:
                    ui.label(f"• {r}").classes("text-sm opacity-80")
                if card["context"]:
                    with ui.row().classes("gap-1 flex-wrap"):
                        for c in card["context"]:
                            ui.badge(str(c)).props("outline color=grey")
                for w in card["warnings"]:
                    ui.badge(str(w)).props("color=red")

    def _render_cards_into(container, head_label, advisory, apply_factory, empty_text):
        container.clear()
        if not advisory:
            head_label.text = empty_text
            return
        head_label.text = summary_line(advisory)
        cards = candidate_card_rows(advisory)
        # candidate_card_rows strips the raw candidate; re-pair so Apply can send it.
        raw_cands = advisory.get("candidates") or []
        if advisory.get("error") or not cards:
            with container:
                ui.label(advisory.get("error") or "No rescue candidates available.") \
                    .classes("opacity-70")
            return
        for i, card in enumerate(cards):
            raw = raw_cands[i] if i < len(raw_cands) else {}
            _render_one_card(container, {**card, "_raw": raw}, apply_factory)

    def _notify_apply_result(adv):
        res = (adv or {}).get("apply_result")
        if not res:
            return
        if res.get("ok"):
            ui.notify("Rescue applied ✓", type="positive")
        elif res.get("stale"):
            ui.notify("Prices moved — re-review", type="warning")
        else:
            ui.notify(f"Apply failed: {res.get('error') or 'unknown'}", type="negative")

    def _noop_apply(_card):
        return lambda: None

    # ── tabs: folder-style subtabs mounted under the main strip (app standard,
    # like Gamma/Scanner/Simulator) via main.subtab_slot(); falls back inline
    # when the slot is absent (standalone render / tests). ────────────────────
    import main as _shell
    _slot = _shell.subtab_slot()

    def _build_tabs():
        with ui.tabs().classes("compact-subtabs").props(
                "dense no-caps inline-label align=left") as t:
            tb = ui.tab("At-Risk Board")
            ta = ui.tab("Ad-hoc Trade")
        return t, tb, ta

    if _slot is not None:
        with _slot:
            tabs, tab_board, tab_adhoc = _build_tabs()
    else:
        tabs, tab_board, tab_adhoc = _build_tabs()

    with ui.tab_panels(tabs, value=tab_board).classes("w-full flush-panels"):
        # ── BOARD PANEL: at-risk table (left) + advisory cards (right) ─────────
        with ui.tab_panel(tab_board):
            with ui.row().classes("w-full gap-4 no-wrap items-start") as board_body:
                with ui.column().classes("min-w-0 grow-[3] shrink basis-0"):
                    at_risk_tbl = ui.table(columns=at_risk_columns(), rows=[],
                                           row_key="id").classes("w-full rescue-table").props("dense")
                    # Color the heat cell by zone (scanner's composite_score idiom).
                    at_risk_tbl.add_slot("body-cell-heat", r"""
                      <q-td :props="props">
                        <q-badge :class="props.row._heat_class"
                                 :label="props.value ?? '—'"/>
                      </q-td>
                    """)
                    # P&L + Δ short shown with exactly 2 decimals (kill float tails).
                    at_risk_tbl.add_slot("body-cell-pnl", r"""
                      <q-td :props="props" class="text-right">
                        {{ props.value == null ? '—' : Number(props.value).toFixed(2) }}
                      </q-td>
                    """)
                    at_risk_tbl.add_slot("body-cell-short_delta", r"""
                      <q-td :props="props" class="text-right">
                        {{ props.value == null ? '—' : Number(props.value).toFixed(2) }}
                      </q-td>
                    """)
                    at_risk_empty = ui.label("No tested or critical positions right now.") \
                        .classes("opacity-70")
                # Right: the ranked rescue menu for the selected board position.
                with ui.column().classes("min-w-0 grow-[2] shrink basis-0"):
                    with ui.row().classes("items-center gap-3 w-full"):
                        advisory_head = ui.label(
                            "Select an at-risk position to see rescue options.") \
                            .classes("text-subtitle1")
                        advisory_spinner = ui.spinner(size="sm")
                        advisory_spinner.set_visibility(False)
                    # Persistent chart at first render (ESM import-map gotcha): a
                    # minimal payoff placeholder on the DEFAULT-active tab so any
                    # later dynamically-added chart resolves the ESM import map.
                    payoff_chart = ui.highchart(_payoff_figure(None)).classes("w-full")
                    payoff_chart.set_visibility(False)
                    cards_col = ui.column().classes("w-full gap-3")

        # ── AD-HOC PANEL: Calculator-style leg editor (left) + advisory (right) ─
        with ui.tab_panel(tab_adhoc):
            with ui.row().classes("w-full gap-4 no-wrap items-start"):
                with ui.column().classes("min-w-0 grow-[3] shrink basis-0 gap-3"):
                    with ui.row().classes("items-end gap-3 flex-wrap"):
                        adhoc_strat = build_strategy_menu(value="PCS", classes="w-52", boxed=True)
                        adhoc_sym = ui.input("Symbol").props("dense").classes("w-40")
                        adhoc_load_btn = ui.button("Load", icon="cloud_upload").props("no-caps")
                    adhoc_status = ui.label(
                        "Pick a strategy, load a symbol, then set the legs.") \
                        .classes("text-sm opacity-70")
                    with ui.row().classes("items-end gap-3 flex-wrap"):
                        adhoc_exp_sel = ui.select([], label="Expiry").props("dense").classes("w-44")
                        adhoc_contracts = ui.number("Contracts", value=1, min=1, max=100) \
                            .props("dense").classes("w-28")
                    # Legs card (header-table editor, shared with the Calculator).
                    adhoc_leg_box = ui.column().classes("gap-2 w-full")
                    adhoc_compute_btn = ui.button("Compute rescue options",
                                                  color=None).props("no-caps").classes(BTN_3D)
                # Right: the ranked (advisory-only) rescue menu for the ad-hoc trade.
                with ui.column().classes("min-w-0 grow-[2] shrink basis-0"):
                    with ui.row().classes("items-center gap-3 w-full"):
                        adhoc_head = ui.label(
                            "Define a trade and compute rescue options.") \
                            .classes("text-subtitle1")
                        adhoc_spinner = ui.spinner(size="sm")
                        adhoc_spinner.set_visibility(False)
                    adhoc_cards_col = ui.column().classes("w-full gap-3")

    # ── at-risk board ────────────────────────────────────────────────────────
    def _render_at_risk():
        rows = at_risk_rows(state["paper"], state["captured"])
        state["rows_by_id"] = {r["id"]: r for r in rows}
        at_risk_tbl.rows = _table_rows(rows)
        at_risk_tbl.update()
        at_risk_empty.set_visibility(not rows)

    def _confirm_apply(candidate):
        @guard
        def _do():
            with ui.dialog() as dlg, ui.card():
                ui.label(f"Apply rescue: {candidate.get('title') or 'this action'}?")
                ui.label("This dispatches a (simulated) paper adjustment.") \
                    .classes("opacity-70 text-sm")
                with ui.row().classes("justify-end gap-2 w-full"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")

                    @guard
                    def _go():
                        dlg.close()
                        adv = state["board_advisory"] or {}
                        cand = candidate.get("_raw") or {}
                        bus_client.request("options", {"type": "rescue_apply", "args": {
                            "position_id": adv.get("position_id") or state["board_id"],
                            "candidate": cand}})
                        ui.notify("Applying rescue…")
                        advisory_spinner.set_visibility(True)

                    ui.button("Apply", color=None, on_click=_go).props("no-caps").classes(BTN_3D)
            dlg.open()
        return _do

    def _render_board_cards():
        _render_cards_into(cards_col, advisory_head, state["board_advisory"],
                           _confirm_apply,
                           "Select an at-risk position to see rescue options.")

    @guard
    def _select(event):
        row = (event.args[1] if isinstance(event.args, list) and len(event.args) > 1
               else event.args)
        if not isinstance(row, dict):
            return
        rid = row.get("id")
        src = state["rows_by_id"].get(rid, {})
        state["board_id"] = rid
        state["board_source"] = src.get("source")
        advisory_head.text = f"Computing rescue options for {src.get('symbol') or rid}…"
        advisory_spinner.set_visibility(True)
        cards_col.clear()
        # Enqueue the rescue command — args shape matches handlers'
        # command.args["position_id"]. ``source`` routes paper vs captured
        # (captured → advisory-only menu, no Apply).
        bus_client.request("options", {"type": "rescue",
                                       "args": {"position_id": rid,
                                                "source": state["board_source"] or "paper"}})

    at_risk_tbl.on("rowClick", _select)

    # ── ad-hoc trade rescue: Calculator-style leg editor ──────────────────────
    def _adhoc_strikes_for(expiry, otype):
        chain = adhoc.get("chain") or {}
        if expiry:
            return chain_strikes(chain, expiry, otype)
        # Union across expiries (pre-load / before a per-leg expiry is set).
        out = set()
        for e in chain_expiries(chain):
            out.update(chain_strikes(chain, e, otype))
        return sorted(out)

    def _adhoc_expiries_for():
        return chain_expiries(adhoc.get("chain") or {})

    adhoc_editor = leg_editor.build_leg_editor(
        adhoc_leg_box, strikes_for=_adhoc_strikes_for, expiries_for=_adhoc_expiries_for,
        show_premium=True, header=True,
        spot_getter=lambda: float(adhoc.get("spot") or 0.0))

    def _adhoc_scale_qty(factor):
        """Multiply every leg's qty by ``factor`` (ratio-preserving), like the
        Calculator's Contracts multiplier."""
        if factor == 1:
            return
        legs = adhoc_editor.get_legs()
        if not legs:
            return
        for leg in legs:
            leg["qty"] = max(1, round(int(leg.get("qty", 1) or 1) * factor))
        adhoc_editor.set_legs(legs)

    def _adhoc_seed_template():
        """Apply the selected strategy template, scale it by the current Contracts,
        and propagate the chosen expiry to every leg (tolerates an empty chain)."""
        adhoc_editor.apply_template(adhoc_strat.value)
        _adhoc_scale_qty(max(1, int(adhoc_contracts.value or 1)))
        if adhoc_exp_sel.value:
            adhoc_editor.apply_expiry(adhoc_exp_sel.value)

    @guard
    def _adhoc_on_strategy(_e=None):
        _adhoc_seed_template()
        _adhoc_unsupported(adhoc_strat.value)   # gentle heads-up on select

    @guard
    def _adhoc_on_expiry():
        adhoc_editor.apply_expiry(adhoc_exp_sel.value)

    @guard
    def _adhoc_on_contracts():
        new = max(1, int(adhoc_contracts.value or 1))
        old = adhoc.get("contracts") or 1
        if new != old:
            _adhoc_scale_qty(new / old)
        adhoc["contracts"] = new

    def _adhoc_apply_chain(cc):
        cc = cc or {}
        adhoc["chain"] = cc.get("chain")
        if cc.get("price"):
            adhoc["spot"] = round(cc["price"], 2)
        exps = chain_expiries(adhoc.get("chain") or {})
        adhoc_exp_sel.options = exps
        if exps and adhoc_exp_sel.value not in exps:
            adhoc_exp_sel.value = exps[0]
        adhoc_exp_sel.update()
        # Re-seed the legs against the real chain so strikes snap to the ladder —
        # unless the user has manually edited them (then just refresh dropdowns).
        if not adhoc_editor.is_dirty():
            _adhoc_seed_template()
        else:
            adhoc_editor.refresh_options()
        adhoc_status.text = (f"Chain loaded — {len(exps)} expirations."
                             if exps else "No chain data for that symbol.")

    @guard
    def _adhoc_load():
        sym = (adhoc_sym.value or "").strip().upper()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        adhoc_status.text = f"Loading {sym} chain…"
        # Shares the Calculator's calc_chain cache (single-user, one page at a time).
        bus_client.request("options", {"type": "calc_load", "args": {"symbol": sym}})

    @guard_async
    async def _adhoc_poll_chain():
        # Cheap :ver probe on the loop; the ~10 MB chain payload is read OFF the loop
        # via run.io_bound (mirrors calculator.py). Version-gated + in-flight-guarded
        # so the big read happens only on a fresh publish, never stacking.
        version = bus_client.read_version("options:calc_chain")
        if version == adhoc["chain_ver"] or adhoc.get("chain_fetching"):
            return
        adhoc["chain_ver"] = version
        adhoc["chain_fetching"] = True
        try:
            cc = await run.io_bound(bus_client.read, "options:calc_chain")
        finally:
            adhoc["chain_fetching"] = False
        _adhoc_apply_chain(cc)

    @guard
    def _adhoc_compute():
        # Gate on the selected strategy first: an unsupported one pops the
        # "not available yet" message instead of a confusing structure error.
        if _adhoc_unsupported(adhoc_strat.value):
            return
        spec = adhoc_spec_from_legs(adhoc_sym.value, adhoc_editor.get_legs())
        if spec.get("error"):
            ui.notify(spec["error"], type="warning")
            return
        state["adhoc_selected"] = True
        state["adhoc_advisory"] = None
        # Baseline to the CURRENT adhoc version so a stale prior result doesn't
        # flash; the poll renders only when the fresh publish bumps it.
        state["adhoc_advisory_ver"] = bus_client.read_version("options:rescue:adhoc")
        adhoc_head.text = f"Computing rescue options for {spec['symbol']}…"
        adhoc_spinner.set_visibility(True)
        adhoc_cards_col.clear()
        bus_client.request("options", {"type": "rescue_adhoc", "args": {"spec": spec}})

    adhoc_load_btn.on_click(_adhoc_load)
    adhoc_sym.on("keydown.enter", lambda e: _adhoc_load())
    adhoc_compute_btn.on_click(_adhoc_compute)
    adhoc_strat.on_value_change(_adhoc_on_strategy)
    adhoc_exp_sel.on_value_change(lambda e: _adhoc_on_expiry())
    adhoc_contracts.on_value_change(lambda e: _adhoc_on_contracts())
    _adhoc_seed_template()   # seed the default PCS template (tolerates empty chain)
    # Track the current calc_chain version WITHOUT applying a possibly-stale cached
    # chain (a prior symbol's); the poll applies only a fresh publish after Load.
    adhoc["chain_ver"] = bus_client.read_version("options:calc_chain")

    # ── version-poll repaint (fetch-free) ────────────────────────────────────
    @guard
    def _poll_boards():
        pv = bus_client.read_version("options:paper_account")
        cv = bus_client.read_version("options:captured")
        absent = pv is None and cv is None
        waiting.set_visibility(absent)
        board_body.set_visibility(not absent)
        changed = False
        if pv != state["paper_ver"]:
            state["paper_ver"] = pv
            state["paper"] = bus_client.read("options:paper_account")
            changed = True
        if cv != state["captured_ver"]:
            state["captured_ver"] = cv
            state["captured"] = bus_client.read("options:captured")
            changed = True
        if changed:
            _render_at_risk()

    @guard
    def _poll_board_advisory():
        rid = state["board_id"]
        if rid is None:
            return
        ver = bus_client.read_version(f"options:rescue:{rid}")
        if ver == state["board_advisory_ver"]:
            return
        state["board_advisory_ver"] = ver
        adv = bus_client.read(f"options:rescue:{rid}")
        state["board_advisory"] = adv or None
        advisory_spinner.set_visibility(False)
        _render_board_cards()
        _notify_apply_result(adv)

    @guard
    def _poll_adhoc_advisory():
        if not state["adhoc_selected"]:
            return
        ver = bus_client.read_version("options:rescue:adhoc")
        if ver == state["adhoc_advisory_ver"]:
            return
        state["adhoc_advisory_ver"] = ver
        adv = bus_client.read("options:rescue:adhoc")
        state["adhoc_advisory"] = adv or None
        adhoc_spinner.set_visibility(False)
        _render_cards_into(adhoc_cards_col, adhoc_head, state["adhoc_advisory"],
                           _noop_apply, "Define a trade and compute rescue options.")
        _notify_apply_result(adv)

    # Initial paint (graceful-empty when the service is cold).
    state["paper_ver"] = bus_client.read_version("options:paper_account")
    state["captured_ver"] = bus_client.read_version("options:captured")
    state["paper"] = bus_client.read("options:paper_account")
    state["captured"] = bus_client.read("options:captured")
    _absent = state["paper_ver"] is None and state["captured_ver"] is None
    waiting.set_visibility(_absent)
    board_body.set_visibility(not _absent)
    _render_at_risk()

    # Opening the page with an empty board → recompute captured marks ("Refresh
    # Marks") so freshly-CUT signals surface. The version-poll repaints when the
    # repriced captured view lands. Fires once on load, not on every poll.
    if not _absent and not at_risk_tbl.rows:
        bus_client.request("options", {"type": "captured_reprice"})
        at_risk_empty.text = "No at-risk positions yet — refreshing captured marks…"

    ui.timer(2.0, _poll_boards)
    ui.timer(2.0, _poll_board_advisory)
    ui.timer(1.0, _adhoc_poll_chain)
    ui.timer(2.0, _poll_adhoc_advisory)


def _payoff_figure(_unused):
    """Minimal placeholder payoff chart.

    A persistent ``ui.highchart`` must exist at first render so any dynamically
    added/updated chart on the page resolves the ESM import map (documented
    gotcha). We don't draw a real payoff curve yet — kept intentionally minimal."""
    return {
        "chart": {"type": "line", "backgroundColor": "transparent", "height": 240},
        "title": {"text": "", "style": {"color": "#e6e6e6"}},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": False},
        "xAxis": {"labels": {"style": {"color": "#bdbdbd"}}},
        "yAxis": {"title": {"text": ""}, "labels": {"style": {"color": "#bdbdbd"}}},
        "series": [],
    }
