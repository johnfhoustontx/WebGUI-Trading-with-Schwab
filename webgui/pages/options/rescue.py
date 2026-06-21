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
                              id_field="position_id", strategy_field="strategy",
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
        "short_delta": _num(src.get("current_short_delta")),
        "pnl": _num(src.get("unrealized_pnl")),
        "heat": _num(src.get("heat"), 0.0) or 0.0,
        "state": src.get("rescue_state") or "ok",
    }


def cash_text(value):
    """Credit/debit display dict: {'text': '+$120'|'-$45'|'$0', 'color': ...}.

    Positive = credit (green), negative = debit (red), zero/missing = neutral."""
    v = _num(value)
    if v is None or round(v) == 0:
        return {"text": "$0", "color": CASH_NEUTRAL}
    mag = abs(round(v))
    if v > 0:
        return {"text": f"+${mag}", "color": CASH_GREEN}
    return {"text": f"-${mag}", "color": CASH_RED}


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


def render():  # pragma: no cover - wired in Task 7.2
    """Render the Rescue page (NiceGUI). Implemented in Task 7.2."""
    from nicegui import ui  # noqa: F401  (lazy import — keeps builders app-free)
    ui.label("Rescue page — coming in Task 7.2")
