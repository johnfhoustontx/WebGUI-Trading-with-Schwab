"""Driver page (Tier-3 reader) — autonomous monitor + STOP, then the legacy
morning-agent order-approval queue + performance.

This page holds **no engine call**. The morning pipeline, the autonomous Claude
decision layer, order execution, and performance aggregation all live in
``services/driver_svc``; the page reads cached views and enqueues commands.

**Autonomous monitor (top, autonomy level B).** Reads ``cache:driver:autonomous``
(``AutonomousState`` — day P&L vs the $500 target, open driver positions, and the
newest-first per-checkpoint decision log) and ``cache:driver:control``
(``DriverControl`` — the enabled/halted master switch). It is a MONITOR + OVERRIDE:
* **Enable / Disable** toggle → ``{"type":"enable"|"disable"}`` on ``cmd:driver`` —
  the master switch (Enable also re-arms a prior day's halt).
* **STOP** (kill-switch, confirm-gated) → ``{"type":"stop"}`` — latches ``halted``
  so no further checkpoints run until the next-day re-arm.
* **Run now** → ``{"type":"cycle"}`` — fire one decision checkpoint immediately.

**Legacy approval queue + performance (below the separator).** Still available
(it's gated off whenever autonomy is enabled):
* **Run morning agent** → ``{"type":"run"}`` — grade today + propose trades
  (the 09:28-ET scheduler fires the same command unattended).
* **APPROVE** → ``{"type":"approve"}`` (confirm-gated, outward-facing) /
  **SKIP** → ``{"type":"skip"}`` / **Refresh performance** → ``{"type":"perf"}``.

A version-poll on ``driver:autonomous`` / ``driver:control`` /
``driver:approvals`` / ``driver:performance`` repaints from the cache; state
persists across navigation (single-user). The pure display builders
(``target_progress``/``control_state_label``/``decision_log_rows``/
``position_rows`` for the monitor; ``grade_color``/``status_text``/
``condition_rows``/``proposed_trade_lines``/``perf_*`` for the legacy queue) are
unit-tested.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import bus_client
from nicegui import ui

from pages.ui_guard import guard

# Decision-log / cycle timestamps are stored in UTC; show the user's Central time.
_CENTRAL = ZoneInfo("America/Chicago")


def to_central(iso_ts):
    """Format a stored UTC ISO timestamp as Central (CT) wall-clock for display.

    The service stamps decision-log + last-cycle timestamps in UTC; the page shows
    them in the user's Central time (CDT/CST — ``America/Chicago`` handles DST). A
    naive (tz-less) timestamp is assumed UTC. Returns the input unchanged if it
    can't be parsed (never raises)."""
    if not iso_ts:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(_CENTRAL).strftime("%Y-%m-%d %H:%M:%S") + " CT"
    except Exception:  # noqa: BLE001 — an unparseable ts displays as-is, never breaks.
        return str(iso_ts)


# Grade chip colors (mirror the legacy approval_server trade sheet).
GRADE_COLORS = {"A": "#1D9E75", "B": "#185FA5", "C": "#BA7517", "X": "#E24B4A"}
GRADE_NEUTRAL = "#888888"
APPROVE_COLOR = "#2e7d32"
SKIP_COLOR = "#c62828"


def grade_color(grade):
    """Color a day grade A/B/C/X; unknown -> neutral grey."""
    return GRADE_COLORS.get((grade or "").upper(), GRADE_NEUTRAL)


def is_pending(payload):
    """True when a cached approval is still awaiting a decision."""
    return bool(payload) and payload.get("status") == "pending"


def _money(v):
    """Signed dollar string, or '—' for None."""
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.2f}"


def status_text(payload):
    """One-line summary of the current approval state for the status label."""
    if not payload or not payload.get("status"):
        return "Run the morning agent to grade today and propose trades."
    st = payload["status"]
    n = len(payload.get("proposed_trades") or [])
    if st == "pending":
        return f"Grade {payload.get('grade', '?')} · {n} proposed — awaiting approval."
    if st == "approved":
        sent = len(payload.get("results") or [])
        return f"Approved — {sent} order(s) sent (paper)."
    if st == "skipped":
        return "Skipped for today."
    if st == "no_trade":
        reasons = payload.get("reasons") or []
        tail = f" — {reasons[0]}" if reasons else ""
        return f"No trade today{tail}"
    if st == "error":
        return f"Pipeline error: {payload.get('error', 'unknown')}"
    return st


def _fmt(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}"


def condition_rows(conditions, pnl_today, pnl_week):
    """(label, value) pairs for the market-conditions strip."""
    conditions = conditions or {}
    return [
        ("VIX", _fmt(conditions.get("vix"))),
        ("SPX", "—" if conditions.get("spx_spot") is None
         else f"{conditions['spx_spot']:,.2f}"),
        ("VIX1D", _fmt(conditions.get("vix1d"))),
        ("P&L today", _money(pnl_today)),
        ("P&L week", _money(pnl_week)),
    ]


def proposed_trade_lines(trade):
    """Human-readable lines describing one proposed trade (any bucket)."""
    trade = trade or {}
    bucket = trade.get("bucket", "?")
    structure = trade.get("structure", "")
    instrument = trade.get("instrument", "")
    head = structure.replace("_", " ").title() if structure else instrument
    lines = [f"Bucket {bucket} · {head}".rstrip(" ·")]

    side = trade.get("side")
    if side:
        lines.append(f"{instrument} {side}".strip())

    strikes = trade.get("strikes") or {}
    detail = " · ".join(f"{k}: {v}" for k, v in strikes.items() if k != "structure")
    if detail:
        lines.append(detail)

    notes = trade.get("notes")
    if notes:
        lines.append(notes)

    bits = []
    if trade.get("contracts") is not None:
        bits.append(f"{trade['contracts']} contract(s)")
    if trade.get("max_risk") is not None:
        bits.append(f"max risk ${trade['max_risk']:.0f}")
    if bits:
        lines.append(" · ".join(bits))

    ml = trade.get("ml_signal")
    if ml:
        conf = trade.get("ml_confidence")
        lines.append(f"ML {ml}" + (f" {conf:.0f}%" if conf is not None else ""))
    return lines


def perf_summary_text(summary):
    """One-line performance summary, or a friendly empty note."""
    if not summary or not summary.get("total_trades"):
        return "No trades recorded yet."
    return (f"Trades: {summary.get('total_trades', 0)} · "
            f"Win rate: {summary.get('win_rate', 0)}% "
            f"({summary.get('wins', 0)}-{summary.get('losses', 0)}) · "
            f"Realized: ${summary.get('realized_pnl', 0):.2f}")


def perf_rows(trades):
    """Table rows for the performance trade list (P&L pre-formatted, signed)."""
    rows = []
    for t in trades or []:
        rows.append({
            "trade_id": t.get("trade_id", ""),
            "date": t.get("date", ""),
            "bucket": t.get("bucket", ""),
            "instrument": t.get("instrument", ""),
            "side": t.get("side", ""),
            "status": t.get("status", ""),
            "source": t.get("source", ""),
            "pnl": _money(t.get("pnl")),
        })
    return rows


_PERF_COLS = [
    {"name": "date", "label": "Date", "field": "date", "align": "left"},
    {"name": "trade_id", "label": "Trade", "field": "trade_id", "align": "left"},
    {"name": "bucket", "label": "Bkt", "field": "bucket"},
    {"name": "instrument", "label": "Inst", "field": "instrument"},
    {"name": "side", "label": "Side", "field": "side"},
    {"name": "status", "label": "Status", "field": "status"},
    {"name": "source", "label": "Source", "field": "source"},
    {"name": "pnl", "label": "P&L", "field": "pnl"},
]


# ── autonomous monitor: pure builders (Phase 7) ──────────────────────────────
# The repurposed page reads ``cache:driver:autonomous`` (AutonomousState) +
# ``cache:driver:control`` (DriverControl) and surfaces: day-P&L-vs-target
# progress, the control state, the open driver positions, and the per-checkpoint
# decision log. Control colors for the master-switch state.
CONTROL_OFF_COLOR = "#888888"       # disabled — autonomous off
CONTROL_ACTIVE_COLOR = "#1D9E75"    # enabled, running
CONTROL_HALTED_COLOR = "#BA7517"    # latched halt (banked / loss cap / VIX / STOP)

# How many ~2s version-poll ticks to hold the optimistic toggle before giving up:
# if the enable/disable command never lands (e.g. driver_svc down), revert + warn.
_PENDING_TIMEOUT_TICKS = 3


def target_progress(day_pnl, target):
    """Fraction of the daily target banked, clamped to [0, 1].

    ``None`` day P&L (no fills yet) or a non-positive target → 0.0 (never /0).
    A red day clamps to 0.0; banking past the target clamps to 1.0.
    """
    try:
        if day_pnl is None or not target or float(target) <= 0:
            return 0.0
        return max(0.0, min(1.0, float(day_pnl) / float(target)))
    except (TypeError, ValueError):
        return 0.0


def target_text(day_pnl, target):
    """``'+$250.00 / $500.00'`` — banked day P&L over the target."""
    tgt = "—" if target is None else f"${float(target):,.2f}"
    return f"{_money(day_pnl)} / {tgt}"


def control_state_label(control):
    """One-line human label for the autonomous master-switch state.

    ``DISABLED — autonomous off`` (default) · ``ACTIVE — autonomous running`` ·
    ``HALTED — <reason>`` (latched within the day; Enable re-arms it).
    """
    control = control or {}
    if not control.get("enabled"):
        return "DISABLED — autonomous off"
    if control.get("halted"):
        return f"HALTED — {control.get('reason') or 'stopped'}"
    return "ACTIVE — autonomous running"


def control_state_color(control):
    """Hex color matching :func:`control_state_label` (off/active/halted)."""
    control = control or {}
    if not control.get("enabled"):
        return CONTROL_OFF_COLOR
    if control.get("halted"):
        return CONTROL_HALTED_COLOR
    return CONTROL_ACTIVE_COLOR


def decision_log_rows(decisions):
    """Normalize the newest-first checkpoint audit log into render-ready rows.

    Each source row (from ``AutonomousState.decisions``) is sparse:
    ``{ts, thesis, stand_down, executed:[{id,symbol,qty,rationale}],
    rejected:[{id,reason}], halted, halt_reason}``. Missing fields default
    safely so a stand-down / halt row renders cleanly.
    """
    out = []
    for d in decisions or []:
        d = d or {}
        out.append({
            "ts": d.get("ts", ""),
            "thesis": d.get("thesis", ""),
            "stand_down": bool(d.get("stand_down", False)),
            # Filter nested sub-lists to dicts: the AutonomousState contract gates
            # `decisions` as list[dict] but NOT these nested lists, so a malformed
            # executed/rejected (None, a str, or a list of non-dicts) must not reach
            # the card loops and blank the monitor — this page is the audit-log
            # resilience boundary.
            "executed": [t for t in (d.get("executed") or []) if isinstance(t, dict)],
            "rejected": [r for r in (d.get("rejected") or []) if isinstance(r, dict)],
            "halted": bool(d.get("halted", False)),
            "halt_reason": d.get("halt_reason"),
        })
    return out


def decision_summary(row):
    """A compact one-line summary of a single decision-log row's outcome."""
    row = row or {}
    if row.get("halted"):
        return f"HALTED — {row.get('halt_reason') or 'stopped'}"
    executed = row.get("executed") or []
    rejected = row.get("rejected") or []
    if not executed:
        base = "Stood down — no trades" if row.get("stand_down") else "No trades executed"
    else:
        legs = ", ".join(
            f"{t.get('symbol', '?')}×{t.get('qty', '?')}" for t in executed)
        base = f"Executed {len(executed)}: {legs}"
    if rejected:
        base += f" · {len(rejected)} rejected"
    return base


def position_rows(positions):
    """Table rows for the open driver-positions panel (P&L pre-formatted, signed)."""
    rows = []
    for p in positions or []:
        p = p or {}
        rows.append({
            "position_id": p.get("position_id", ""),
            "symbol": p.get("symbol", ""),
            "strategy": p.get("strategy", ""),
            "quantity": p.get("quantity", ""),
            "pnl": _money(p.get("unrealized_pnl")),
            "status": p.get("status", ""),
        })
    return rows


def paper_summary(paper_view):
    """Live driver paper-account P&L from ``cache:options:driver_paper_account``.

    The autonomous loop executes into the DRIVER's own isolated paper account (via
    the ``driver_paper_create`` command), so THIS is the real, live P&L of its book
    — it moves as the options service reprices the driver account (every ~5 min) and
    is correct whether or not the autonomous decision loop is enabled. The monitor
    reads it directly rather than the autonomy-gated ``cache:driver:autonomous``
    snapshot (which is only published while a cycle runs).
    Defensive: a missing snapshot / no account → ``has_account`` False, None values.
    """
    pv = paper_view or {}
    snap = pv.get("snapshot")
    has_account = bool(pv.get("has_account")) and snap is not None
    snap = snap or {}
    return {
        "has_account": has_account,
        "session_pnl": snap.get("session_pnl"),
        "realized_pnl": snap.get("realized_pnl"),
        "open_unrealized": snap.get("open_unrealized"),
        "equity": snap.get("equity"),
        "open_count": snap.get("open_count", 0),
    }


# ── performance scorecard: pure builders (cache:options:driver_paper_perf) ───
# The scorecard renders the driver paper account's standalone performance from
# ``cache:options:driver_paper_perf`` (published every 5-min driver manage tick —
# more live than ``AutonomousState.perf``, which only updates per 30-min cycle).
# All builders are defensive: an unpublished view → ``{}`` → an empty/placeholder
# card, never a raise. ``profit_factor`` ``None`` (no losses yet) renders as "—".
def _pnl(v):
    """Signed dollar string for a P&L scorecard cell; exactly-zero is unsigned
    (``$0.00``), None → ``$0.00`` (a fresh-account scorecard reads cleanly)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    if v == 0:
        return "$0.00"
    return f"{'+' if v > 0 else '-'}${abs(v):,.2f}"


def _pct(frac):
    """A 0..1 fraction as a 1-dp percent (``0.6667 → '66.7%'``); None/garbage → '0.0%'."""
    try:
        return f"{float(frac) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def scorecard_headline_chips(perf):
    """Headline (label, value) chips: trades, open/closed, win rate, realized,
    open unrealized, total P&L — the at-a-glance row of the scorecard."""
    p = perf or {}
    return [
        ("Trades", str(int(p.get("total_trades") or 0))),
        ("Open", str(int(p.get("open") or 0))),
        ("Closed", str(int(p.get("closed") or 0))),
        ("Win rate", _pct(p.get("win_rate"))),
        ("Realized", _pnl(p.get("realized_pnl"))),
        ("Open P&L", _pnl(p.get("open_unrealized"))),
        ("Total P&L", _pnl(p.get("total_pnl"))),
    ]


def scorecard_quality_chips(perf):
    """Quality (label, value) chips: avg win, avg loss, profit factor.

    ``profit_factor`` is ``None`` until there is at least one loss (gross-win /
    gross-loss is undefined with no losses) — render it as the em-dash "—"."""
    p = perf or {}
    pf = p.get("profit_factor")
    pf_text = "—" if pf is None else f"{float(pf):.2f}"
    return [
        ("Avg win", _pnl(p.get("avg_win"))),
        ("Avg loss", _pnl(p.get("avg_loss"))),
        ("Profit factor", pf_text),
    ]


def _breakdown_rows(rows, key):
    """Format a P&L-by-{symbol|strategy} list (signed pnl, percent win-rate)."""
    out = []
    for r in rows or []:
        r = r or {}
        out.append({
            key: r.get(key, "?"),
            "trades": r.get("trades", 0),
            "pnl": _pnl(r.get("pnl")),
            "win_rate": _pct(r.get("win_rate")),
        })
    return out


def scorecard_symbol_rows(perf):
    """Render-ready P&L-by-symbol table rows (from ``perf['by_symbol']``)."""
    return _breakdown_rows((perf or {}).get("by_symbol"), "symbol")


def scorecard_strategy_rows(perf):
    """Render-ready P&L-by-strategy table rows (from ``perf['by_strategy']``)."""
    return _breakdown_rows((perf or {}).get("by_strategy"), "strategy")


def best_worst_text(perf):
    """``'Best MU +$120.00 · Worst MU -$60.00'`` — the extreme closed trades.

    Empty (nothing closed) → ``''`` so the card can hide the line. Defensive over a
    missing symbol / non-numeric realized_pnl."""
    p = perf or {}
    bits = []
    for label, pos in (("Best", p.get("best")), ("Worst", p.get("worst"))):
        if isinstance(pos, dict):
            sym = pos.get("symbol") or "?"
            bits.append(f"{label} {sym} {_pnl(pos.get('realized_pnl'))}")
    return " · ".join(bits)


def resolve_switch_state(pending, actual_enabled):
    """Optimistic Autonomous-switch state — the anti-flicker guard.

    The switch is bound to ``cache:driver:control.enabled`` and rebuilt on every
    monitor repaint (now frequent — the live paper account drives repaints). Without
    this, a repaint during the ~1s it takes the enable/disable command to reach the
    service would yank the switch back to the stale backend value, fighting the click.

    ``pending`` is the user's last toggled value awaiting confirmation (or None when
    nothing is pending). Returns ``(shown_enabled, still_pending)``:

    * no pending → show the actual control state;
    * pending matches the actual state → confirmed, clear it (return None);
    * pending differs → keep SHOWING THE INTENT (don't flip) and keep waiting.
    """
    if pending is None:
        return bool(actual_enabled), None
    if bool(actual_enabled) == bool(pending):
        return bool(actual_enabled), None      # confirmed — clear the pending intent
    return bool(pending), pending              # still in flight — hold the user's intent


_POSITION_COLS = [
    {"name": "position_id", "label": "ID", "field": "position_id", "align": "left"},
    {"name": "symbol", "label": "Symbol", "field": "symbol", "align": "left"},
    {"name": "strategy", "label": "Strat", "field": "strategy"},
    {"name": "quantity", "label": "Qty", "field": "quantity"},
    {"name": "pnl", "label": "P&L", "field": "pnl"},
    {"name": "status", "label": "Status", "field": "status"},
]

# Performance-scorecard breakdown tables (P&L by symbol / by strategy).
_SCORE_SYMBOL_COLS = [
    {"name": "symbol", "label": "Symbol", "field": "symbol", "align": "left"},
    {"name": "trades", "label": "Trades", "field": "trades"},
    {"name": "pnl", "label": "P&L", "field": "pnl"},
    {"name": "win_rate", "label": "Win %", "field": "win_rate"},
]
_SCORE_STRATEGY_COLS = [
    {"name": "strategy", "label": "Strategy", "field": "strategy", "align": "left"},
    {"name": "trades", "label": "Trades", "field": "trades"},
    {"name": "pnl", "label": "P&L", "field": "pnl"},
    {"name": "win_rate", "label": "Win %", "field": "win_rate"},
]


def render():
    """Driver page: autonomous monitor + STOP, then legacy approval queue + perf."""
    ui.label("Claude Driver").classes("text-h5")
    ui.label("Autonomous PAPER options trader (Claude decides, code-enforced "
             "guardrails). This page MONITORS what it does and lets you STOP it. "
             "Paper only — nothing is sent to Schwab.").classes("text-xs opacity-60")

    state = {
        "appr": None, "appr_ver": None, "perf": None, "perf_ver": None,
        "auto": None, "auto_ver": None, "ctrl": None, "ctrl_ver": None,
        "paper": None, "paper_ver": None,
        "dperf": None, "dperf_ver": None,        # driver-account performance scorecard
        "pending_enabled": None, "pending_ticks": 0,
    }

    # ── Autonomous monitor + override (Phase 7) ───────────────────────────────
    monitor = ui.column().classes("w-full gap-3")

    ui.separator()
    with ui.row().classes("items-center gap-3 flex-wrap"):
        ui.label("Legacy approval queue").classes("text-h6")
        ui.label("Active only when autonomy is disabled; the morning agent "
                 "still grades/proposes.").classes("text-xs opacity-50")

    with ui.row().classes("items-center gap-3 flex-wrap"):
        run_btn = ui.button("Run morning agent", icon="play_arrow")
        perf_btn = ui.button("Refresh performance", icon="refresh").props("outline")
        status = ui.label("").classes("opacity-70 text-sm")

    approval = ui.column().classes("w-full gap-3")
    ui.separator()
    ui.label("Performance").classes("text-h6")
    ui.label("Morning-agent / order-executor ledger (trade_log.json) — a separate "
             "record from the live paper-account P&L shown in the monitor above.") \
        .classes("text-xs opacity-50")
    perf_summary = ui.label("").classes("text-sm opacity-80")
    perf_table = ui.table(columns=_PERF_COLS, rows=[], row_key="trade_id") \
        .classes("w-full").props("dense")

    # ── confirm dialog for APPROVE (outward-facing action) ────────────────────
    with ui.dialog() as confirm_dialog, ui.card():
        ui.label("Approve and send the proposed orders?").classes("text-subtitle1")
        ui.label("Submitted via order_executor — PAPER_TRADE=True simulates the "
                 "orders (nothing is sent to Schwab).").classes("text-xs opacity-70")
        with ui.row().classes("justify-end gap-2 w-full"):
            ui.button("Cancel", on_click=confirm_dialog.close).props("flat")
            ui.button("Approve", color="positive",
                      on_click=lambda: (_do("approve", "Approving…"),
                                        confirm_dialog.close()))

    # ── confirm dialog for STOP (latch the kill-switch) ───────────────────────
    with ui.dialog() as stop_dialog, ui.card():
        ui.label("STOP the autonomous driver?").classes("text-subtitle1")
        ui.label("Latches the kill-switch for the rest of today — no new trades "
                 "will be opened. Open positions keep auto-managing. Enable "
                 "re-arms it (clears the halt).").classes("text-xs opacity-70")
        with ui.row().classes("justify-end gap-2 w-full"):
            ui.button("Cancel", on_click=stop_dialog.close).props("flat")
            ui.button("STOP", color="negative",
                      on_click=lambda: (_do("stop", "Stopping…"),
                                        stop_dialog.close()))

    # ── card builders ─────────────────────────────────────────────────────────
    def _conditions_strip(appr):
        with ui.row().classes("items-center gap-4 flex-wrap"):
            for label, value in condition_rows(appr.get("conditions"),
                                               appr.get("pnl_today"),
                                               appr.get("pnl_week")):
                with ui.row().classes("items-baseline gap-1"):
                    ui.label(label).classes("text-xs opacity-60")
                    ui.label(value).classes("text-sm text-weight-medium")

    def _trade_card(trade):
        lines = proposed_trade_lines(trade)
        with ui.card().classes("w-full"):
            if lines:
                ui.label(lines[0]).classes("text-subtitle2 text-weight-bold")
                for ln in lines[1:]:
                    ui.label(ln).classes("text-sm opacity-80")

    # ── autonomous monitor render (rebuilt in place from cache:driver:*) ───────
    def _render_monitor():
        monitor.clear()
        auto = state["auto"] or {}
        ctrl = state["ctrl"] or {}
        paper = state["paper"] or {}
        psum = paper_summary(paper)
        # Control derives from the dedicated control key when present, else falls
        # back to the autonomous view's mirrored flags (both are published by the
        # service; control is the authoritative switch).
        ctrl_view = ctrl or {"enabled": auto.get("enabled", False),
                             "halted": auto.get("halted", False),
                             "reason": auto.get("halt_reason")}
        # Optimistic toggle: show the user's pending intent until the control state
        # confirms it, so a (now-frequent) repaint can't flip the switch mid-command.
        enabled, state["pending_enabled"] = resolve_switch_state(
            state["pending_enabled"], bool(ctrl_view.get("enabled")))
        halted = bool(ctrl_view.get("halted"))
        # Day P&L = the LIVE paper-account session P&L (the truthful source — the
        # driver trades into the paper account, so it moves as the options service
        # reprices, whether or not the autonomous loop is enabled). Fall back to the
        # autonomous snapshot only when the paper account isn't cached yet.
        day_pnl = psum["session_pnl"] if psum["has_account"] else auto.get("day_pnl")
        target = auto.get("target", 500.0)

        with monitor:
            with ui.card().classes("w-full gap-3"):
                # State banner + master controls.
                with ui.row().classes("items-center gap-3 flex-wrap w-full"):
                    ui.label(control_state_label(ctrl_view)) \
                        .classes("text-weight-bold text-white px-3 py-1 rounded") \
                        .style(f"background:{control_state_color(ctrl_view)}")
                    if auto.get("date"):
                        ui.label(auto["date"]).classes("opacity-60 text-sm")
                    if auto.get("last_cycle_ts"):
                        ui.label(f"last cycle {to_central(auto['last_cycle_ts'])}") \
                            .classes("opacity-50 text-xs")
                    ui.space()
                    # Enable/Disable master toggle (re-arms a prior halt on enable).
                    sw = ui.switch("Autonomous", value=enabled,
                                   on_change=_on_toggle)
                    sw.props("color=positive")
                    ui.button("Run now", icon="bolt",
                              on_click=lambda: _do("cycle", "Running a checkpoint…")) \
                        .props("outline")
                    ui.button("STOP", icon="stop", color="negative",
                              on_click=stop_dialog.open) \
                        .props("unelevated").classes("text-weight-bold")

                # Day-P&L-vs-target progress.
                with ui.row().classes("items-center gap-3 w-full"):
                    ui.label("Day P&L").classes("text-xs opacity-60")
                    ui.linear_progress(value=target_progress(day_pnl, target),
                                       show_value=False, size="18px") \
                        .classes("flex-1").props("rounded")
                    ui.label(target_text(day_pnl, target)) \
                        .classes("text-sm text-weight-medium")
                # Live paper-account P&L summary (the truthful, always-current numbers).
                if psum["has_account"]:
                    with ui.row().classes("items-center gap-4 flex-wrap"):
                        for lbl, val in (("Session P&L", _money(psum["session_pnl"])),
                                         ("Realized", _money(psum["realized_pnl"])),
                                         ("Open P&L", _money(psum["open_unrealized"])),
                                         ("Equity", _money(psum["equity"])),
                                         ("Open", str(psum["open_count"]))):
                            with ui.column().classes("gap-0"):
                                ui.label(lbl).classes("text-xs opacity-60")
                                ui.label(val).classes("text-sm text-weight-medium")
                if halted and ctrl_view.get("reason"):
                    ui.label(f"Halt: {ctrl_view['reason']}") \
                        .classes("text-xs text-amber-9")

            # Open positions — the LIVE paper account (where the driver trades),
            # falling back to the autonomous snapshot only if the paper account
            # isn't cached yet.
            positions = (paper.get("positions") if psum["has_account"]
                         else auto.get("positions")) or []
            with ui.card().classes("w-full gap-2"):
                ui.label(f"Open positions ({len(positions)})") \
                    .classes("text-subtitle2 opacity-70")
                if positions:
                    ui.table(columns=_POSITION_COLS, rows=position_rows(positions),
                             row_key="position_id").classes("w-full").props("dense")
                else:
                    ui.label("No open positions.").classes("text-xs opacity-50")

            # Decision log (per-checkpoint thesis + executed/rejected + halt).
            log = decision_log_rows(auto.get("decisions"))
            with ui.card().classes("w-full gap-2"):
                ui.label(f"Decision log ({len(log)})") \
                    .classes("text-subtitle2 opacity-70")
                if not log:
                    ui.label("No checkpoints yet — enable autonomy or click "
                             "“Run now”.").classes("text-xs opacity-50")
                for row in log:
                    _decision_card(row)

            # Performance scorecard — the driver account's standalone track record
            # (cache:options:driver_paper_perf, refreshed every 5-min manage tick).
            _scorecard_card(state["dperf"] or {})

    def _decision_card(row):
        halted = row.get("halted")
        cls = "w-full gap-1"
        with ui.card().classes(cls):
            with ui.row().classes("items-center gap-2 flex-wrap"):
                ui.label(to_central(row.get("ts", ""))).classes("text-xs opacity-50")
                if row.get("stand_down"):
                    ui.label("STAND DOWN").classes("text-xs text-weight-bold "
                                                   "text-amber-9")
                if halted:
                    ui.label("HALTED").classes("text-xs text-weight-bold text-red-9")
            if row.get("thesis"):
                ui.label(row["thesis"]).classes("text-sm")
            ui.label(decision_summary(row)).classes("text-xs opacity-80")
            for ex in row.get("executed") or []:
                rat = ex.get("rationale")
                line = (f"✓ {ex.get('symbol', '?')} ×{ex.get('qty', '?')}"
                        + (f" — {rat}" if rat else ""))
                ui.label(line).classes("text-xs text-green-9")
            for rj in row.get("rejected") or []:
                ui.label(f"✗ {rj.get('id', '?')} — {rj.get('reason', '')}") \
                    .classes("text-xs text-red-8 opacity-80")
            if halted and row.get("halt_reason"):
                ui.label(row["halt_reason"]).classes("text-xs text-amber-9")

    def _chip(label, value):
        with ui.column().classes("gap-0"):
            ui.label(label).classes("text-xs opacity-60")
            ui.label(value).classes("text-sm text-weight-medium")

    def _scorecard_card(perf):
        # Plain-widget card (no Highcharts) → safe to rebuild in place each repaint.
        with ui.card().classes("w-full gap-2"):
            ui.label("Performance scorecard").classes("text-subtitle2 opacity-70")
            ui.label("The driver account's standalone track record (isolated paper "
                     "book; updates every ~5 min as it reprices).") \
                .classes("text-xs opacity-50")
            if not perf or not perf.get("total_trades"):
                ui.label("No driver trades recorded yet.").classes("text-xs opacity-50")
                return
            # Headline metrics.
            with ui.row().classes("items-center gap-5 flex-wrap"):
                for lbl, val in scorecard_headline_chips(perf):
                    _chip(lbl, val)
            # Quality metrics (avg win / avg loss / profit factor).
            with ui.row().classes("items-center gap-5 flex-wrap"):
                for lbl, val in scorecard_quality_chips(perf):
                    _chip(lbl, val)
            bw = best_worst_text(perf)
            if bw:
                ui.label(bw).classes("text-xs opacity-70")
            # Breakdown tables (P&L by symbol / by strategy).
            with ui.row().classes("w-full gap-4 items-start flex-wrap"):
                sym_rows = scorecard_symbol_rows(perf)
                if sym_rows:
                    with ui.column().classes("gap-1 flex-1 min-w-[260px]"):
                        ui.label("P&L by symbol").classes("text-xs opacity-60")
                        ui.table(columns=_SCORE_SYMBOL_COLS, rows=sym_rows,
                                 row_key="symbol").classes("w-full").props("dense")
                strat_rows = scorecard_strategy_rows(perf)
                if strat_rows:
                    with ui.column().classes("gap-1 flex-1 min-w-[260px]"):
                        ui.label("P&L by strategy").classes("text-xs opacity-60")
                        ui.table(columns=_SCORE_STRATEGY_COLS, rows=strat_rows,
                                 row_key="strategy").classes("w-full").props("dense")

    def _render_approval():
        approval.clear()
        appr = state["appr"]
        with approval:
            if not appr or not appr.get("status"):
                ui.label("No approval yet — click “Run morning agent”.") \
                    .classes("opacity-70")
                return
            with ui.card().classes("w-full"):
                with ui.row().classes("items-center gap-3 flex-wrap"):
                    g = appr.get("grade") or "?"
                    ui.label(g).classes("text-weight-bold text-white px-3 py-1 rounded") \
                        .style(f"background:{grade_color(g)}")
                    if appr.get("date"):
                        ui.label(appr["date"]).classes("opacity-70")
                    ui.label(status_text(appr)).classes("text-sm opacity-80")
                _conditions_strip(appr)
                for r in appr.get("grade_reasons") or []:
                    ui.label(f"• {r}").classes("text-xs opacity-70")

            trades = appr.get("proposed_trades") or []
            if trades:
                ui.label(f"Proposed trades ({len(trades)})") \
                    .classes("text-subtitle2 opacity-70")
                for t in trades:
                    _trade_card(t)

            if is_pending(appr):
                with ui.row().classes("gap-3"):
                    ui.button("APPROVE", icon="check", color="positive",
                              on_click=confirm_dialog.open)
                    ui.button("SKIP", icon="close", color="negative",
                              on_click=lambda: _do("skip", "Skipping…")).props("outline")
            elif appr.get("status") == "approved":
                results = appr.get("results") or []
                ok = sum(1 for r in results if r.get("success"))
                with ui.row().classes("items-center gap-2 bg-green-2 text-green-10 "
                                      "rounded p-3"):
                    ui.icon("check_circle")
                    ui.label(f"Approved — {ok}/{len(results)} order(s) succeeded "
                             "(paper).")
            elif appr.get("status") == "skipped":
                with ui.row().classes("items-center gap-2 opacity-70 rounded p-3"):
                    ui.icon("block")
                    ui.label("Skipped for today.")
            elif appr.get("status") == "no_trade":
                with ui.row().classes("items-center gap-2 bg-amber-2 text-amber-10 "
                                      "rounded p-3"):
                    ui.icon("info")
                    ui.label("; ".join(appr.get("reasons") or ["No trade today."]))
            elif appr.get("status") == "error":
                with ui.row().classes("items-center gap-2 bg-red-2 text-red-10 "
                                      "rounded p-3"):
                    ui.icon("warning")
                    ui.label(f"Pipeline error: {appr.get('error', 'unknown')}")

    def _render_perf():
        perf = state["perf"] or {}
        perf_summary.text = perf_summary_text(perf.get("summary"))
        perf_table.rows = perf_rows(perf.get("trades"))
        perf_table.update()

    # ── command enqueue ───────────────────────────────────────────────────────
    @guard
    def _do(cmd, busy_msg):
        bus_client.request("driver", {"type": cmd})
        status.text = busy_msg

    @guard
    def _on_toggle(e):
        # Master switch: enable re-arms a prior halt (per the service); disable
        # stands the loop down without latching. Record the intent so a repaint
        # can't flip the switch before the command lands (resolve_switch_state),
        # and reset the confirmation timeout.
        state["pending_enabled"] = bool(e.value)
        state["pending_ticks"] = 0
        if e.value:
            _do("enable", "Enabling autonomous driver…")
        else:
            _do("disable", "Disabling autonomous driver…")

    run_btn.on_click(lambda: _do("run", "Running morning agent…"))
    perf_btn.on_click(lambda: _do("perf", "Refreshing performance…"))

    # ── version-poll repaint (fetch-free) ─────────────────────────────────────
    @guard
    def _poll():
        # Monitor: repaint when the autonomous view, the control key, the live DRIVER
        # paper account, OR the driver performance scorecard advances. The driver
        # paper account drives the live P&L + positions (it updates even with the
        # autonomous loop disabled, as the options service reprices the driver book);
        # the perf view (cache:options:driver_paper_perf) drives the scorecard card.
        avv = bus_client.read_version("driver:autonomous")
        cvv = bus_client.read_version("driver:control")
        ppv = bus_client.read_version("options:driver_paper_account")
        dpv = bus_client.read_version("options:driver_paper_perf")
        if (avv != state["auto_ver"] or cvv != state["ctrl_ver"]
                or ppv != state["paper_ver"] or dpv != state["dperf_ver"]):
            state["auto_ver"] = avv
            state["ctrl_ver"] = cvv
            state["paper_ver"] = ppv
            state["dperf_ver"] = dpv
            state["auto"] = bus_client.read("driver:autonomous") or None
            state["ctrl"] = bus_client.read("driver:control") or None
            state["paper"] = bus_client.read("options:driver_paper_account") or None
            state["dperf"] = bus_client.read("options:driver_paper_perf") or None
            _render_monitor()
        av = bus_client.read_version("driver:approvals")
        if av != state["appr_ver"]:
            state["appr_ver"] = av
            state["appr"] = bus_client.read("driver:approvals") or None
            _render_approval()
            status.text = status_text(state["appr"])
        pv = bus_client.read_version("driver:performance")
        if pv != state["perf_ver"]:
            state["perf_ver"] = pv
            state["perf"] = bus_client.read("driver:performance") or None
            _render_perf()
        # Optimistic-toggle timeout: if the control state never catches up to the
        # user's pending toggle (command never consumed — e.g. driver_svc down),
        # give up after a few ticks: revert the switch to reality and warn. This is
        # the feedback that surfaces a dead service instead of a silently stuck toggle.
        if state["pending_enabled"] is not None:
            state["pending_ticks"] += 1
            if state["pending_ticks"] >= _PENDING_TIMEOUT_TICKS:
                state["pending_enabled"] = None
                state["pending_ticks"] = 0
                ui.notify("Enable/Disable didn't take — is driver_svc running?",
                          type="warning")
                _render_monitor()

    # Initial paint (graceful-empty when the service is cold / nothing cached).
    state["auto_ver"] = bus_client.read_version("driver:autonomous")
    state["auto"] = bus_client.read("driver:autonomous") or None
    state["ctrl_ver"] = bus_client.read_version("driver:control")
    state["ctrl"] = bus_client.read("driver:control") or None
    state["paper_ver"] = bus_client.read_version("options:driver_paper_account")
    state["paper"] = bus_client.read("options:driver_paper_account") or None
    state["dperf_ver"] = bus_client.read_version("options:driver_paper_perf")
    state["dperf"] = bus_client.read("options:driver_paper_perf") or None
    state["appr_ver"] = bus_client.read_version("driver:approvals")
    state["appr"] = bus_client.read("driver:approvals") or None
    state["perf_ver"] = bus_client.read_version("driver:performance")
    state["perf"] = bus_client.read("driver:performance") or None
    _render_monitor()
    _render_approval()
    _render_perf()
    status.text = status_text(state["appr"])
    ui.timer(2.0, _poll)
