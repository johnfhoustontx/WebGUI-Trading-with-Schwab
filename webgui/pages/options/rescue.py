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

from pages.options import theme
from .theme import BTN_3D

# Heat zone colors (higher heat = closer to trouble): green → amber → orange →
# red. Reuses the shared palette idiom from scanner.py / svg.py (#ef5350 red,
# #ffa726 amber, #66bb6a green) so the UI stays consistent; orange bridges the
# amber→red gap for the 50-75 zone.
HEAT_GREEN = theme.hex_of("green")
HEAT_AMBER = theme.hex_of("amber")
HEAT_ORANGE = "#ff7043"
HEAT_RED = theme.hex_of("red")

# Cash (credit/debit) text colors — same green/red as pnl_color in captured.py.
CASH_GREEN = theme.hex_of("green")
CASH_RED = theme.hex_of("red")
CASH_NEUTRAL = theme.hex_of("flat")

# rescue_state values that put a position on the at-risk board.
_AT_RISK_STATES = ("tested", "critical")


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
    """Tailwind ``bg-[<hex>]`` class for a 0-100 heat value (same zones as
    ``heat_color``; None / non-numeric -> green). Used by the heat-cell slot."""
    return f"bg-[{heat_color(heat)}]"


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
  background-color: #1d1d1d;
}
"""


def render():
    """Render the Rescue page (NiceGUI).

    Tier-3 reader: version-polls ``options:paper_account`` + ``options:captured``
    for the at-risk board, enqueues a ``rescue`` command on row-click, and
    version-polls the per-id ``options:rescue:<id>`` advisory view to paint the
    ranked candidate cards. Mirrors ``simulator.py`` / ``paper.py`` idioms
    (``bus_client.request`` / ``read`` / ``read_version`` + ``@guard``)."""
    import bus_client
    from nicegui import ui

    from pages.ui_guard import guard

    ui.add_css(_RESCUE_CSS)

    # Page state (local closure, not module globals — built per request).
    state: dict = {
        "paper": None, "paper_ver": None,
        "captured": None, "captured_ver": None,
        "selected_id": None, "selected_source": None,
        "advisory": None, "advisory_ver": None,
        "rows_by_id": {},          # id -> raw display row (for source/symbol)
    }

    ui.label("Rescue").classes("text-h5")

    # ── waiting-for-service placeholder ──────────────────────────────────────
    waiting = ui.label("Waiting for options service…").classes("opacity-70")

    with ui.row().classes("w-full gap-4 no-wrap items-start") as body:
        # Left: the at-risk board (shrunk so the rescue menu sits to its right).
        with ui.column().classes("min-w-0 grow-[3] shrink basis-0"):
            with ui.card().classes("w-full"):
                ui.label("At-risk positions").classes("text-subtitle1")
                at_risk_tbl = ui.table(columns=at_risk_columns(), rows=[],
                                       row_key="id").classes("w-full rescue-table")
                # Color the heat cell by zone (scanner's composite_score idiom).
                at_risk_tbl.add_slot("body-cell-heat", r"""
                  <q-td :props="props">
                    <q-badge :class="props.row._heat_class + ' text-[#111]'"
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

        # Right: the ranked rescue menu for the selected position.
        with ui.column().classes("min-w-0 grow-[2] shrink basis-0"):
            with ui.card().classes("w-full"):
                with ui.row().classes("items-center gap-3 w-full"):
                    advisory_head = ui.label(
                        "Select an at-risk position to see rescue options.") \
                        .classes("text-subtitle1")
                    advisory_spinner = ui.spinner(size="sm")
                    advisory_spinner.set_visibility(False)
                # Persistent chart at first render (ESM import-map gotcha): a minimal
                # payoff placeholder so any later in-place update resolves.
                payoff_chart = ui.highchart(_payoff_figure(None)).classes("w-full")
                payoff_chart.set_visibility(False)
                cards_col = ui.column().classes("w-full gap-3")

    # ── at-risk board ────────────────────────────────────────────────────────
    def _render_at_risk():
        rows = at_risk_rows(state["paper"], state["captured"])
        state["rows_by_id"] = {r["id"]: r for r in rows}
        at_risk_tbl.rows = _table_rows(rows)
        at_risk_tbl.update()
        at_risk_empty.set_visibility(not rows)

    @guard
    def _select(event):
        row = (event.args[1] if isinstance(event.args, list) and len(event.args) > 1
               else event.args)
        if not isinstance(row, dict):
            return
        rid = row.get("id")
        src = state["rows_by_id"].get(rid, {})
        state["selected_id"] = rid
        state["selected_source"] = src.get("source")
        advisory_head.text = f"Computing rescue options for {src.get('symbol') or rid}…"
        advisory_spinner.set_visibility(True)
        cards_col.clear()
        payoff_chart.set_visibility(False)
        # Enqueue the rescue command — args shape matches handlers'
        # command.args["position_id"]. ``source`` routes paper vs captured
        # (captured → advisory-only menu, no Apply).
        bus_client.request("options", {"type": "rescue",
                                       "args": {"position_id": rid,
                                                "source": state["selected_source"] or "paper"}})

    at_risk_tbl.on("rowClick", _select)

    # ── advisory + candidate cards ───────────────────────────────────────────
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
                        adv = state["advisory"] or {}
                        cand = candidate.get("_raw") or {}
                        bus_client.request("options", {"type": "rescue_apply", "args": {
                            "position_id": adv.get("position_id") or state["selected_id"],
                            "candidate": cand}})
                        ui.notify("Applying rescue…")
                        advisory_spinner.set_visibility(True)

                    ui.button("Apply", color=None, on_click=_go).props("no-caps").classes(BTN_3D)
            dlg.open()
        return _do

    def _render_cards():
        adv = state["advisory"]
        cards_col.clear()
        if not adv:
            advisory_head.text = "Select an at-risk position to see rescue options."
            payoff_chart.set_visibility(False)
            return
        advisory_head.text = summary_line(adv)
        cards = candidate_card_rows(adv)
        # candidate_card_rows strips the raw candidate; re-pair so Apply can send it.
        raw_cands = adv.get("candidates") or []
        if adv.get("error") or not cards:
            payoff_chart.set_visibility(False)
            with cards_col:
                ui.label(adv.get("error") or "No rescue candidates available.") \
                    .classes("opacity-70")
            return
        for i, card in enumerate(cards):
            raw = raw_cands[i] if i < len(raw_cands) else {}
            card = {**card, "_raw": raw}
            _render_one_card(card)

    def _render_one_card(card):
        with cards_col:
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
                                  on_click=_confirm_apply(card)).props("no-caps").classes(BTN_3D)
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

    # ── version-poll repaint (fetch-free) ────────────────────────────────────
    @guard
    def _poll_boards():
        pv = bus_client.read_version("options:paper_account")
        cv = bus_client.read_version("options:captured")
        absent = pv is None and cv is None
        waiting.set_visibility(absent)
        body.set_visibility(not absent)
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
    def _poll_advisory():
        rid = state["selected_id"]
        if rid is None:
            return
        ver = bus_client.read_version(f"options:rescue:{rid}")
        if ver == state["advisory_ver"]:
            return
        state["advisory_ver"] = ver
        adv = bus_client.read(f"options:rescue:{rid}")
        state["advisory"] = adv or None
        advisory_spinner.set_visibility(False)
        _render_cards()
        _notify_apply_result(adv)

    # Initial paint (graceful-empty when the service is cold).
    state["paper_ver"] = bus_client.read_version("options:paper_account")
    state["captured_ver"] = bus_client.read_version("options:captured")
    state["paper"] = bus_client.read("options:paper_account")
    state["captured"] = bus_client.read("options:captured")
    _absent = state["paper_ver"] is None and state["captured_ver"] is None
    waiting.set_visibility(_absent)
    body.set_visibility(not _absent)
    _render_at_risk()

    # Opening the page with an empty board → recompute captured marks ("Refresh
    # Marks") so freshly-CUT signals surface. The version-poll repaints when the
    # repriced captured view lands. Fires once on load, not on every poll.
    if not _absent and not at_risk_tbl.rows:
        bus_client.request("options", {"type": "captured_reprice"})
        at_risk_empty.text = "No at-risk positions yet — refreshing captured marks…"

    ui.timer(2.0, _poll_boards)
    ui.timer(2.0, _poll_advisory)


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
