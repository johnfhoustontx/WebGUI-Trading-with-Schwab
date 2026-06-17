"""Driver page (Tier-3 reader) — morning-agent order-approval queue + performance.

This page holds **no engine call**. The morning pipeline (grade the day, select
trades), order execution, and performance aggregation all live in
``services/driver_svc``; the page reads the cached ``ApprovalState`` /
``PerfReport`` and enqueues commands:

* **Run morning agent** → ``{"type":"run"}`` on ``cmd:driver`` — grade today and
  propose trades (the 09:28-ET scheduler fires the same command unattended).
* **APPROVE** → ``{"type":"approve"}`` — execute the pending proposed trades
  (``order_executor``; ``PAPER_TRADE=True`` in config → simulated). Gated behind
  a confirm dialog since it is outward-facing.
* **SKIP** → ``{"type":"skip"}`` — decline today's trades.
* **Refresh performance** → ``{"type":"perf"}`` — recompute the perf report.

A version-poll on ``driver:approvals`` / ``driver:performance`` repaints from the
cache; the state persists across navigation (single-user). The pure display
builders (``grade_color``/``status_text``/``condition_rows``/
``proposed_trade_lines``/``perf_*``) are unit-tested.
"""
import bus_client
from nicegui import ui

from pages.ui_guard import guard

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


def render():
    """Driver page: approval queue (run/approve/skip) + performance view."""
    ui.label("Claude Driver").classes("text-h5")
    ui.label("Morning-agent order-approval queue. Orders execute via "
             "order_executor — PAPER_TRADE is enabled, so approvals are "
             "simulated, not sent to Schwab.").classes("text-xs opacity-60")

    state = {"appr": None, "appr_ver": None, "perf": None, "perf_ver": None}

    with ui.row().classes("items-center gap-3 flex-wrap"):
        run_btn = ui.button("Run morning agent", icon="play_arrow")
        perf_btn = ui.button("Refresh performance", icon="refresh").props("outline")
        status = ui.label("").classes("opacity-70 text-sm")

    approval = ui.column().classes("w-full gap-3")
    ui.separator()
    ui.label("Performance").classes("text-h6")
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

    run_btn.on_click(lambda: _do("run", "Running morning agent…"))
    perf_btn.on_click(lambda: _do("perf", "Refreshing performance…"))

    # ── version-poll repaint (fetch-free) ─────────────────────────────────────
    @guard
    def _poll():
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

    # Initial paint (graceful-empty when the service is cold / nothing cached).
    state["appr_ver"] = bus_client.read_version("driver:approvals")
    state["appr"] = bus_client.read("driver:approvals") or None
    state["perf_ver"] = bus_client.read_version("driver:performance")
    state["perf"] = bus_client.read("driver:performance") or None
    _render_approval()
    _render_perf()
    status.text = status_text(state["appr"])
    ui.timer(2.0, _poll)
