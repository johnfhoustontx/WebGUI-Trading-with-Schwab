"""Driver page (Tier-3 reader) — autonomous monitor + Stop + realized performance.

This page holds **no engine call**. The autonomous Claude decision layer, order
execution, and performance aggregation all live in ``services/driver_svc`` /
``services/options_svc``; the page reads cached views and enqueues commands.

**Autonomous monitor (autonomy level B).** Reads ``cache:driver:autonomous``
(``AutonomousState`` — day P&L vs the $500 target, open driver positions, and the
newest-first per-checkpoint decision log) and ``cache:driver:control``
(``DriverControl`` — the enabled/halted master switch). It is a MONITOR + OVERRIDE:
* **Enable / Disable** toggle → ``{"type":"enable"|"disable"}`` on ``cmd:driver`` —
  the master switch. Enable clears a MANUAL stop and a stale (prior-day) halt,
  but leaves a same-day RISK halt latched; **Resume today**
  (``args={"clear_halt": true}``) is the deliberate override for that.
* **Stop** (kill-switch, confirm-gated) → ``{"type":"stop"}`` — latches ``halted``
  so no further checkpoints run until the next-day re-arm.
* **Run now** → ``{"type":"cycle"}`` — fire one decision checkpoint immediately.

**Performance.** The driver's realized track record from its isolated paper account
(``cache:options:driver_paper_account['closed_positions']`` — closed credit spreads
with real realized P&L, updated every 1-min manage cycle); **Refresh** forces an
immediate reprice/republish via ``{"type":"driver_paper_manage"}`` on ``cmd:options``.

A version-poll on ``driver:autonomous`` / ``driver:control`` /
``options:driver_paper_account`` / ``options:driver_paper_perf`` repaints from the
cache; state persists across navigation (single-user). The pure display builders
(``target_progress``/``control_state_label``/``decision_log_rows``/``position_rows``
for the monitor; ``closed_summary_text``/``closed_trade_rows`` + the ``scorecard_*``
builders for performance) are unit-tested.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import bus_client
from nicegui import run, ui

from pages import ui_kit as kit
from pages.ui_guard import guard, guard_async
# The scorecard's PURE render vocabulary, shared with the Paper Account page since
# 2026-09-12 (gap assessment C5). Imported by NAME so ``driver.<fn>`` still
# resolves for the page body and its tests - the page USES the vocabulary, it does
# not own it. See pages/scorecard.py.
from pages.scorecard import (  # noqa: F401
    PNL_GREEN, PNL_NEUTRAL, PNL_RED, best_worst_text, money as _pnl,
    percent as _pct, pnl_class, pnl_color, scorecard_exit_reason_rows,
    scorecard_headline_chips, scorecard_quality_chips, scorecard_strategy_rows,
    scorecard_symbol_rows)
from pages.options import theme as _t

# The bus view the header's Updated stamp reads. ⚠ NOT ``driver:autonomous``:
# that one is published only WHILE a cycle runs, so its age would freeze between
# cycles and the stamp would report a stalled page. The driver's paper account is
# the widest-reach view this page draws from — the day P&L, the open positions,
# the closed trades and the summary all come out of it.
STAMP_VIEW = "options:driver_paper_account"

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


def _money(v):
    """Signed dollar string, or '—' for None."""
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.2f}"


# P&L cell colors (green profit / red loss / grey flat-or-unknown) — so a value is
# read by COLOR, not by hunting for a +/- sign. They live in pages/scorecard.py,
# shared with the Paper Account page, and Phase 1 of the UI-consistency work
# deliberately did NOT fold them into the theme's TXT_POS/TXT_NEG: the P&L
# palette is one decision across four pages, not this page's to take.


def current_day_decisions(decisions, today_ct=None):
    """Filter the checkpoint decision log to TODAY only (Central trading date).

    Each decision's ``ts`` is a UTC ISO string; it's converted to Central and kept
    only if its CT date matches ``today_ct`` (defaults to now in CT). Rows with a
    missing/unparseable ts are dropped — they can't be confidently placed in today.
    """
    if today_ct is None:
        today_ct = datetime.now(_CENTRAL).date()
    out = []
    for d in decisions or []:
        ts = (d or {}).get("ts")
        try:
            dt = datetime.fromisoformat(str(ts))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            if dt.astimezone(_CENTRAL).date() == today_ct:
                out.append(d)
        except Exception:  # noqa: BLE001 — undateable row can't be "today"; skip it.
            continue
    return out


# ── driver realized-performance table (from the isolated paper account's CLOSED
# trades) ────────────────────────────────────────────────────────────────────────
# The driver's ACTUAL closed options credit spreads with real realized P&L, read from
# ``cache:options:driver_paper_account['closed_positions']`` — updated every 1-min
# manage cycle (timely), so realized results appear as positions close.
_EXIT_REASON_LABELS = {
    "TARGET_HIT": "Target hit", "MONEY_STOP": "Money stop", "DELTA_STOP": "Delta stop",
    "TIME_STOP": "Time stop", "EXPIRED": "Expired", "MANUAL": "Manual close",
}


def _humanize_reason(r):
    """A snake_case exit code → a reader-friendly label (keeps unknown codes readable)."""
    if not r:
        return "—"
    return _EXIT_REASON_LABELS.get(str(r).upper(), str(r).replace("_", " ").title())


def _when_text(ts):
    """Compact 'YYYY-MM-DD HH:MM' from a stored ISO ts (already CT), else the date / '—'.

    Used for BOTH the entry (``entry_ts``) and exit (``exit_ts``) stamps.

    ⚠ It SLICES the string — no conversion, no zone label — so "already CT" is an
    assumption this function makes and never checks. It feeds the Opened/Closed
    columns of two tables. Flagged rather than changed: the format only becomes
    safe to touch after reading ``paper_account_db``'s writer.
    """
    s = str(ts or "")
    if len(s) >= 16 and s[10:11] == "T":
        return s[:10] + " " + s[11:16]
    return s[:10] or "—"


def _strike_num(x):
    """A strike as a compact string — 7650.0 → '7650', 22.5 → '22.5' (None → '')."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return ""
    return str(int(f)) if f == int(f) else str(f)


def _strikes_text(p):
    """The position's strikes as 'short/long SIDE' — e.g. '7650/7660 C' (CCS),
    '165/160 P' (PCS), or both wings for an iron condor ('165/160 P · 185/190 C').

    Both PCS and CCS store their pair in ``short_strike``/``long_strike``; only an IC
    additionally fills ``call_short``/``call_long`` (its short/long pair is the PUT
    side). '—' when the strikes aren't known. Never raises.
    """
    p = p or {}
    strat = str(p.get("strategy") or "").upper()
    parts = []
    short_k, long_k = _strike_num(p.get("short_strike")), _strike_num(p.get("long_strike"))
    if short_k and long_k:
        side = "C" if strat == "CCS" else "P"     # an IC's short/long pair is the put side
        parts.append(f"{short_k}/{long_k} {side}")
    cs, cl = _strike_num(p.get("call_short")), _strike_num(p.get("call_long"))
    if cs and cl:
        parts.append(f"{cs}/{cl} C")
    return " · ".join(parts) or "—"


def closed_summary_text(closed, totals=None):
    """One-line realized-performance summary for the driver's closed trades.

    These are LIFETIME figures. options_svc publishes only the newest
    ``DRIVER_CLOSED_LIMIT`` rows but computes ``closed_totals`` over every closed
    trade, so the aggregate is preferred and the rows are only a fallback for a
    snapshot published before it existed — counting the truncated rows would
    understate the whole track record. When the table cannot show everything the
    line says how many it IS showing, rather than letting the reader assume the
    table is complete.
    """
    if isinstance(totals, dict) and totals.get("count"):
        count, wins, losses = totals["count"], totals.get("wins", 0), totals.get("losses", 0)
        realized = totals.get("realized", 0.0)
    else:
        priced = [c for c in (closed or [])
                  if isinstance(c, dict) and isinstance(c.get("realized_pnl"), (int, float))]
        if not priced:
            return ("No closed trades yet — the driver's realized P&L appears here as its "
                    "positions close (target / stop / expiry).")
        count = len(priced)
        wins = len([c for c in priced if c["realized_pnl"] > 0])
        losses = len([c for c in priced if c["realized_pnl"] < 0])
        realized = round(sum(c["realized_pnl"] for c in priced), 2)
    wr = round(100 * wins / count) if count else 0
    text = (f"Closed: {count} · {wins}W–{losses}L ({wr}% win) · "
            f"Realized: {_money(realized)}")
    if isinstance(totals, dict) and totals.get("truncated"):
        text += f" · showing the most recent {len(closed or [])}"
    return text


def closed_trade_rows(closed):
    """Reader-friendly, newest-first rows for the driver's closed-trade table."""
    items = [c for c in (closed or []) if isinstance(c, dict)]
    items.sort(key=lambda c: str(c.get("exit_ts") or ""), reverse=True)
    rows = []
    for c in items:
        pnl = c.get("realized_pnl")
        rows.append({
            "cid": str(c.get("position_id", c.get("signal_id", ""))),
            "opened": _when_text(c.get("entry_ts")),
            "closed": _when_text(c.get("exit_ts")),
            "symbol": c.get("symbol", ""),
            "strategy": c.get("strategy", ""),
            "qty": c.get("quantity", ""),
            "reason": _humanize_reason(c.get("exit_reason")),
            "pnl": _money(pnl),
            "_pnl_class": pnl_class(pnl),
        })
    return rows


_CLOSED_COLS = [
    {"name": "opened", "label": "Opened", "field": "opened", "align": "left"},
    {"name": "closed", "label": "Closed", "field": "closed", "align": "left"},
    {"name": "symbol", "label": "Symbol", "field": "symbol", "align": "left"},
    {"name": "strategy", "label": "Strategy", "field": "strategy"},
    {"name": "qty", "label": "Contracts", "field": "qty"},
    {"name": "reason", "label": "Exit reason", "field": "reason", "align": "left"},
    {"name": "pnl", "label": "Realized P&L", "field": "pnl"},
]
# ⚠ Quasar's own column default is RIGHT and the kit's is LEFT, so every column
# NOT named here moves left on the kit migration. That is the point for
# ``strategy``: it is a text column that has been right-aligned by accident, and
# nobody writes ``align`` for a text column expecting right. ``pnl`` must stay
# named — ``_PNL_CELL_SLOT`` hardcodes ``text-right`` on the cell, so dropping it
# here would leave the header and the body disagreeing.
_CLOSED_NUMERIC = ("qty", "pnl")


# ── autonomous monitor: pure builders (Phase 7) ──────────────────────────────
# The repurposed page reads ``cache:driver:autonomous`` (AutonomousState) +
# ``cache:driver:control`` (DriverControl) and surfaces: day-P&L-vs-target
# progress, the control state, the open driver positions, and the per-checkpoint
# decision log. The master-switch state renders as a filled pill, and its three
# fills are the app's own semantic badge tokens: the old ``#888888`` was a pure
# neutral, and "running" / "latched halt" are a STATE reading, which is exactly
# what BADGE_POS / BADGE_WARN encode everywhere else in the app.
CONTROL_OFF_BADGE = _t.BADGE_MUTED      # disabled — autonomous off
CONTROL_ACTIVE_BADGE = _t.BADGE_POS     # enabled, running
CONTROL_HALTED_BADGE = _t.BADGE_WARN    # latched halt (banked / loss cap / VIX / Stop)

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


MANUAL_STOP_REASON = "manual STOP"   # mirrors driver_svc.handlers (Tier 1 may not import it)


def is_risk_halt(control) -> bool:
    """True when the driver halted ITSELF and the latch still stands.

    A manual STOP is the user's own switch and the Enable toggle clears it; a
    risk halt (daily loss cap / banked target / VIX ceiling) is NOT cleared by a
    re-arm any more, so the page offers a deliberate override instead. PURE, so
    the button's visibility is unit-testable without a browser.
    """
    if not isinstance(control, dict) or not control.get("halted"):
        return False
    return str(control.get("reason") or "") != MANUAL_STOP_REASON


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


def control_badge_class(control):
    """The badge token matching :func:`control_state_label` (off/active/halted).

    One function rather than the old colour-plus-class pair: ``control_state_color``
    existed only to be interpolated into ``bg-[…]``, and a theme badge already
    carries its fill, its foreground and its radius together.
    """
    control = control or {}
    if not control.get("enabled"):
        return CONTROL_OFF_BADGE
    if control.get("halted"):
        return CONTROL_HALTED_BADGE
    return CONTROL_ACTIVE_BADGE


# R7 — stand-down reason observability. The decider tags each decision with WHY it
# stood down: ``model`` (a real choice) vs an OPS INCIDENT (``no_key`` = broken /
# rotated Anthropic key, ``api_error`` = network/SDK failure, ``parse_error`` =
# garbled tool reply). Only the incident reasons get a visible tag — a genuine
# model stand-down (or a legacy row with no reason) renders exactly as before, so
# "weeks of cautious model behavior" can't hide a dead key. Unknown reason → no tag.
_INCIDENT_REASON_LABELS = {
    "no_key": "NO API KEY",
    "api_error": "API ERROR",
    "parse_error": "BAD REPLY",
}


def stand_down_reason_label(reason):
    """Short human tag for an OPS-INCIDENT stand-down reason, else ``None``.

    ``no_key`` → 'NO API KEY' · ``api_error`` → 'API ERROR' · ``parse_error`` →
    'BAD REPLY'. A genuine model decision (``model``), a missing/empty reason
    (legacy row — back-compat), or any unrecognized code → ``None`` (no tag; the
    entry renders exactly as it did before this field existed)."""
    return _INCIDENT_REASON_LABELS.get(reason or "")


def decision_log_rows(decisions):
    """Normalize the newest-first checkpoint audit log into render-ready rows.

    Each source row (from ``AutonomousState.decisions``) is sparse:
    ``{ts, thesis, stand_down, reason, executed:[{id,symbol,qty,rationale}],
    rejected:[{id,reason}], halted, halt_reason}``. Missing fields default
    safely so a stand-down / halt row renders cleanly. ``reason`` (R7) is threaded
    through so the page can flag an ops-incident stand-down (no_key/api_error)
    distinctly from a genuine model stand-down; a row lacking it → ``None``
    (back-compat — renders exactly as before).
    """
    out = []
    for d in decisions or []:
        d = d or {}
        out.append({
            "ts": d.get("ts", ""),
            "thesis": d.get("thesis", ""),
            "stand_down": bool(d.get("stand_down", False)),
            # Why the decider stood down, if it did (model | no_key | api_error |
            # parse_error). Absent on legacy rows → None → renders as today.
            "reason": d.get("reason"),
            # Filter nested sub-lists to dicts: the AutonomousState contract gates
            # `decisions` as list[dict] but NOT these nested lists, so a malformed
            # executed/rejected (None, a str, or a list of non-dicts) must not reach
            # the card loops and blank the monitor — this page is the audit-log
            # resilience boundary.
            "executed": [t for t in (d.get("executed") or []) if isinstance(t, dict)],
            "rejected": [r for r in (d.get("rejected") or []) if isinstance(r, dict)],
            "halted": bool(d.get("halted", False)),
            "halt_reason": d.get("halt_reason"),
            # Shadow directional gate (log-only evidence). A dict {posture, would_block,
            # n, enabled} or None on legacy rows → renders nothing.
            "shadow_gate": d.get("shadow_gate") if isinstance(d.get("shadow_gate"), dict)
            else None,
        })
    return out


def shadow_gate_line(row):
    """One-line summary of the log-only directional-gate shadow, or '' when there is
    nothing to show.

    Surfaced only while the gate is INERT (``enabled`` False) AND it would have blocked
    at least one trade that fired — the actionable evidence for flipping the gate on. A
    live gate (``enabled`` True) already rejects wrong-side trades, so nothing is shown.
    Never raises on a sparse/None shadow dict.
    """
    sg = row.get("shadow_gate") if isinstance(row, dict) else None
    if not isinstance(sg, dict) or sg.get("enabled") or not sg.get("would_block"):
        return ""
    wb = [w for w in sg.get("would_block") or [] if isinstance(w, dict)]
    if not wb:
        return ""
    legs = ", ".join(f"{w.get('structure', '?')} {w.get('symbol', '?')}" for w in wb)
    posture = sg.get("posture", "?")
    return f"Gate shadow: would block {len(wb)} ({legs}) — {posture} tape"


def decision_summary(row):
    """A compact one-line summary of a single decision-log row's outcome.

    A stand-down caused by an OPS INCIDENT (no_key/api_error/parse_error) is tagged
    (``Stood down — no trades [API ERROR]``) so a broken key surfaces in the summary
    line, not just as a normal-looking stand-down. A genuine model stand-down (or a
    legacy row without a reason) reads exactly as before."""
    row = row or {}
    if row.get("halted"):
        return f"HALTED — {row.get('halt_reason') or 'stopped'}"
    executed = row.get("executed") or []
    rejected = row.get("rejected") or []
    if not executed:
        base = "Stood down — no trades" if row.get("stand_down") else "No trades executed"
        tag = stand_down_reason_label(row.get("reason"))
        if tag:
            base += f" [{tag}]"
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
            "strikes": _strikes_text(p),
            "expiration": p.get("expiration", "") or "—",
            "opened": _when_text(p.get("entry_ts")),
            "pnl": _money(p.get("unrealized_pnl")),
            # ⚠ No ``_pnl_color``: it was written into every row and read by no
            # renderer — ``_PNL_CELL_SLOT`` binds ``_pnl_class``.
            "_pnl_class": pnl_class(p.get("unrealized_pnl")),
            "status": p.get("status", ""),
        })
    return rows


def paper_summary(paper_view):
    """Live driver paper-account P&L from ``cache:options:driver_paper_account``.

    The autonomous loop executes into the DRIVER's own isolated paper account (via
    the ``driver_paper_create`` command), so THIS is the real, live P&L of its book
    — it moves as the options service reprices the driver account (every minute) and
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
# ``cache:options:driver_paper_perf`` (published every 1-min driver manage tick —
# more live than ``AutonomousState.perf``, which only updates per 30-min cycle).
# All builders are defensive: an unpublished view → ``{}`` → an empty/placeholder
# card, never a raise. ``profit_factor`` ``None`` (no losses yet) renders as "—".
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
    # "Strategy", matching this page's OWN closed-trades table above - one
    # screen was printing the concept two ways.
    {"name": "strategy", "label": "Strategy", "field": "strategy"},
    {"name": "quantity", "label": "Contracts", "field": "quantity"},
    {"name": "strikes", "label": "Strikes", "field": "strikes", "align": "left"},
    {"name": "expiration", "label": "Expiry", "field": "expiration", "align": "left"},
    {"name": "opened", "label": "Opened", "field": "opened", "align": "left"},
    # ``position_rows`` is documented as the OPEN positions panel.
    {"name": "pnl", "label": "Open P&L", "field": "pnl"},
    {"name": "status", "label": "Status", "field": "status"},
]
# ⚠ ``strategy`` AND ``status`` are text and are deliberately absent — both were
# right-aligned only because Quasar defaults that way for a column with no
# ``align``. Under the kit they take the app's left, like every other text cell.
_POSITION_NUMERIC = ("quantity", "pnl")

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
# Both breakdowns carry the same three numbers; the first column (Symbol /
# Strategy) is the name they are grouped by.
_SCORE_NUMERIC = ("trades", "pnl", "win_rate")


# ── performance analytics: equity curve + posture post-mortem + MAE/MFE ───────
# Reads cache:options:driver_paper_analytics (perf_analytics.build_analytics) —
# the time-series / regime-attribution view the forensic driver review needed. The
# equity-curve + excursion builders are SHARED with the Paper Portfolio page (the
# scanner-baseline book) via pages.options.perf_charts so both render identically.
from pages.options.perf_charts import (  # noqa: E402
    equity_curve_figure,
    excursion_text,
    signed_dollar as _signed_dollar,
)


_POSTMORTEM_COLS = [
    {"name": "stance", "label": "Stance", "field": "stance", "align": "left"},
    {"name": "trades", "label": "Trades", "field": "trades"},
    {"name": "win_rate", "label": "Win %", "field": "win_rate"},
    {"name": "pnl", "label": "Realized", "field": "pnl"},
    {"name": "avg", "label": "Avg/trade", "field": "avg"},
]
_POSTMORTEM_NUMERIC = ("trades", "win_rate", "pnl", "avg")


def postmortem_rows(pm):
    """Table rows for the WITH/AGAINST/neutral posture post-mortem (stances with ≥1
    trade only). The Realized cell colors via its row ``_pnl_class`` (``_PNL_CELL_SLOT``)."""
    by = (pm or {}).get("by_stance") or {}
    rows = []
    for key, label in (("with", "With tape"), ("against", "Against tape"),
                       ("neutral", "Neutral / IC")):
        b = by.get(key) or {}
        if not b.get("trades"):
            continue
        realized = b.get("realized") or 0
        rows.append({
            "stance": label, "trades": b.get("trades", 0),
            "win_rate": f"{(b.get('win_rate') or 0) * 100:.0f}%",
            "pnl": _signed_dollar(realized), "avg": _signed_dollar(b.get("avg")),
            "_pnl_class": pnl_class(realized),
        })
    return rows


def postmortem_headline(pm):
    """One-line WITH-vs-AGAINST edge, or '' when no posture-attributed trades exist yet."""
    edge = (pm or {}).get("edge") or {}
    n_w, n_a = edge.get("n_with") or 0, edge.get("n_against") or 0
    if n_w == 0 and n_a == 0:
        return ""
    return (f"With the tape: {_signed_dollar(edge.get('with_avg'))}/trade ({n_w}) · "
            f"Against: {_signed_dollar(edge.get('against_avg'))}/trade ({n_a}) · "
            f"edge {_signed_dollar(edge.get('avg_delta'))}/trade")


# A SHORTER scroll box than the app default, and nothing else. The sticky header
# this block used to declare is redundant with ``shell.TABLE_CSS`` — which is
# app-wide and already does sticky thead, row dividers and the 11px/600 eyebrow
# head — and it FOUGHT it, pinning a hardcoded ``#141a30`` over the theme's own
# inset tone. All that is left is the one rule the app does not have an opinion
# about: five tables stacked on one page, each capped so the page still scrolls.
DRIVER_CSS = """
.driver-table .q-table__middle { max-height: 52vh; }
"""

# ⚠ The STOP body used to end "Enable re-arms it (clears the halt)". That is true
# of THIS halt — a manual stop — and false of a halt the driver sets itself, and
# the difference is the entire reason the Resume today button exists (see
# ``is_risk_halt``). It now says which is which.
STOP_BODY = (
    "Latches the kill-switch for the rest of today — no new trades will be "
    "opened. Open positions keep auto-managing. This is a manual stop, so "
    "Enable clears it; a halt the driver sets itself (loss cap, banked target, "
    "VIX ceiling) stays latched until Resume today clears it."
)
RESUME_BODY = (
    "The driver halted ITSELF today (loss cap, banked target, or the VIX "
    "ceiling). Enable re-arms it for the next session but leaves the halt in "
    "place; this clears it and lets it open trades again TODAY."
)

# A body-cell slot that paints the P&L value in its row's _pnl_class (the JIT
# generates the runtime ``text-[#hex]`` utility from the stamped class string).
_PNL_CELL_SLOT = r'''
  <q-td :props="props" class="text-right">
    <span :class="(props.row._pnl_class || 'text-[#bdbdbd]') + ' font-semibold'">
      {{ props.value }}
    </span>
  </q-td>
'''


def render():
    """Driver page: autonomous monitor + Stop + realized performance."""
    ui.add_css(DRIVER_CSS)

    state = {
        "auto": None, "auto_ver": None, "ctrl": None, "ctrl_ver": None,
        "paper": None, "paper_ver": None,
        "dperf": None, "dperf_ver": None,        # driver-account performance scorecard
        "analytics": None, "analytics_ver": None,  # equity curve + posture post-mortem + MAE/MFE
        "pending_enabled": None, "pending_ticks": 0,
    }

    # The kit page column. Held in a name and RE-ENTERED (``with page_col:``)
    # rather than wrapped around the whole of ``render``: the closures below sit
    # at render's own indent and three source-grep tests read them there - the
    # poll's pipelined ``read_versions`` and its ``run.io_bound``, and the
    # monitor's ``read("options:driver_paper_account")``. The gamma precedent.
    page_col = kit.page()
    with page_col:
        # ⚠ ``stale=False`` is a DECISION, not a default. This view's publisher
        # is ``manage_due``, gated on a trading day AND the 08:00-15:15 CT
        # window, and it is in neither ``alerts.STALE_OVERRIDES`` nor
        # ``alerts.RTH_ONLY_VIEWS`` - so ``stale=True`` would paint the stamp
        # amber every evening and all weekend: a true reading of the key's age
        # and a false one about the page.
        head = kit.header("Claude Trades", view=STAMP_VIEW, stale=False)
        with head.actions:
            # ⚠ ALL FOUR ACTIONS LIVE HERE, not in the monitor card where three
            # of them used to. ``kit.set_busy`` creates a button's backstop
            # timer with ``with btn.parent_slot:``, and ``_render_monitor``
            # clears ``monitor`` on every version move - so a held button inside
            # it would be deleted by the very repaint its own command causes.
            # They are actions on the whole page, which is where the standard
            # puts them anyway; danger first, primary last.
            stop_btn = kit.button("Stop", kind="danger", icon="stop",
                                  tooltip="Latch the kill-switch for the rest "
                                          "of today. Open positions keep "
                                          "auto-managing.")
            # Built ONCE and shown by state, rather than built only when the
            # condition holds: out of the cleared container there is no repaint
            # to rebuild it on.
            resume_btn = kit.button("Resume today", kind="secondary",
                                    icon="lock_open",
                                    tooltip="Clear a halt the driver set itself "
                                            "and let it trade again today.")
            resume_btn.set_visibility(False)
            perf_btn = kit.button("Refresh", kind="secondary", icon="refresh",
                                  tooltip="Reprice the driver book now, rather "
                                          "than at the next 1-min manage tick.")
            run_btn = kit.button("Run now", kind="primary", icon="bolt",
                                 tooltip="Fire one decision checkpoint "
                                         "immediately.")
        _actions = (stop_btn, resume_btn, perf_btn, run_btn)

        # ── Autonomous monitor + override ─────────────────────────────────────
        # A cycle runs the decider (a Claude call) and a manage reprices the
        # book; both take seconds during which the monitor shows the previous
        # state. ⚠ THE SCRIM LIVES ON THE REGION'S OUTER ELEMENT. It used to be
        # ``build_busy(monitor, ...)`` - mounted INSIDE the container
        # ``_render_monitor`` opens by clearing - so the build-time paint
        # deleted it and every ``show()`` since reached a deleted element.
        mon = kit.region("Running…")
        monitor = mon.content

        @guard
        def _release_actions():
            for b in _actions:
                kit.set_busy(b, False)

        # ── Performance ───────────────────────────────────────────────────────
        with ui.card().classes(f"{_t.CARD} w-full gap-2"):
            kit.section_title("Performance")
            ui.label("The driver's closed trades and realized P&L from its "
                     "isolated paper account — updates every 1-min manage cycle "
                     "as positions close.").classes(f"text-xs {_t.MUTED}")
            perf_summary = kit.status_line()
            perf_table = kit.table(_CLOSED_COLS, row_key="cid",
                                   numeric=_CLOSED_NUMERIC,
                                   classes="w-full driver-table")
            perf_table.add_slot("body-cell-pnl", _PNL_CELL_SLOT)

        # ── Performance analytics: equity curve + posture post-mortem + MAE/MFE
        # Persistent elements (the Highcharts element must exist at first render
        # — the ESM import-map gotcha — and is updated in place, never rebuilt,
        # so it also sits OUTSIDE the region above).
        with ui.card().classes(f"{_t.CARD} w-full gap-2"):
            kit.section_title("Analytics")
            ui.label("Realized equity curve, whether trading WITH or AGAINST the "
                     "tape paid (posture at entry vs outcome), and how far trades "
                     "ran for/against before closing (MAE/MFE) — the driver "
                     "book's self-diagnostics.").classes(f"text-xs {_t.MUTED}")
            equity_chart = ui.highchart(equity_curve_figure([])).classes("w-full")
            analytics_headline = kit.status_line()
            postmortem_table = kit.table(_POSTMORTEM_COLS, row_key="stance",
                                         numeric=_POSTMORTEM_NUMERIC,
                                         classes="w-full driver-table")
            postmortem_table.add_slot("body-cell-pnl", _PNL_CELL_SLOT)
            excursion_label = ui.label("").classes(f"text-xs {_t.MUTED}")
            analytics_empty = kit.empty("No closed driver trades yet — analytics "
                                        "populate as positions close.")

    @guard
    def _render_analytics():
        a = state["analytics"] or {}
        curve = a.get("equity_curve") or []
        equity_chart.options = equity_curve_figure(curve)
        equity_chart.update()
        pm = a.get("postmortem") or {}
        analytics_headline.text = postmortem_headline(pm)
        postmortem_table.rows = postmortem_rows(pm)
        postmortem_table.update()
        excursion_label.text = excursion_text(a.get("excursions"))
        has_data = bool(curve) or bool(postmortem_table.rows) or bool(excursion_label.text)
        analytics_empty.set_visibility(not has_data)

    # ── the two confirm dialogs ───────────────────────────────────────────────
    # Both are built at the page's own level, so neither sits in a container a
    # repaint clears; both are ``danger`` - Resume today re-arms an autonomous
    # trader that halted ITSELF, which is as consequential as the stop it undoes.
    stop_confirm = kit.confirm(
        "Stop the autonomous driver?", STOP_BODY, confirm_text="Stop",
        danger=True, on_confirm=lambda: _do("stop", "Stopping the driver…",
                                            btn=stop_btn))
    resume_confirm = kit.confirm(
        "Resume trading after a risk halt?", RESUME_BODY,
        confirm_text="Resume today", danger=True,
        on_confirm=lambda: _do("enable", "Clearing the halt…", btn=resume_btn,
                               clear_halt=True))
    stop_btn.on_click(stop_confirm.open)
    resume_btn.on_click(resume_confirm.open)

    # ── autonomous monitor render (rebuilt in place from cache:driver:*) ───────
    def _render_monitor():
        mon.busy.hide()
        _release_actions()
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
        # Only for a halt the DRIVER set itself: Enable no longer clears one of
        # those, so Resume today is the way back in today. The button lives in
        # the header now, so its CONDITION is its visibility.
        resume_btn.set_visibility(is_risk_halt(ctrl_view))
        # Day P&L = the LIVE paper-account session P&L (the truthful source — the
        # driver trades into the paper account, so it moves as the options service
        # reprices, whether or not the autonomous loop is enabled). Fall back to the
        # autonomous snapshot only when the paper account isn't cached yet.
        day_pnl = psum["session_pnl"] if psum["has_account"] else auto.get("day_pnl")
        target = auto.get("target", 500.0)

        with monitor:
            with ui.card().classes(f"{_t.CARD} w-full gap-3"):
                # State banner + the master switch (the page's four ACTIONS are
                # in the header line; this one control stays here because it is
                # rebuilt per repaint on purpose - writing ``sw.value`` from the
                # repaint would re-enter ``_on_toggle``, which is exactly what
                # ``resolve_switch_state`` exists to avoid).
                with ui.row().classes("items-center gap-3 flex-wrap w-full"):
                    ui.label(control_state_label(ctrl_view)) \
                        .classes("text-weight-bold px-3 py-1 "
                                 + control_badge_class(ctrl_view))
                    if auto.get("date"):
                        ui.label(auto["date"]).classes(f"text-sm {_t.MUTED}")
                    if auto.get("last_cycle_ts"):
                        ui.label(f"last cycle {to_central(auto['last_cycle_ts'])}") \
                            .classes(f"text-xs {_t.MUTED}")
                    ui.space()
                    # Enable/Disable master toggle (re-arms a prior halt on enable).
                    sw = ui.switch("Autonomous", value=enabled,
                                   on_change=_on_toggle)
                    sw.props("color=positive")

                # Day-P&L-vs-target progress.
                with ui.row().classes("items-center gap-3 w-full"):
                    ui.label("Day P&L").classes(_t.EYEBROW)
                    ui.linear_progress(value=target_progress(day_pnl, target),
                                       show_value=False, size="18px") \
                        .classes("flex-1").props("rounded")
                    ui.label(target_text(day_pnl, target)) \
                        .classes(f"text-sm text-weight-medium {_t.LABEL}")
                # Live paper-account P&L summary (the truthful, always-current numbers).
                if psum["has_account"]:
                    with ui.row().classes("items-center gap-4 flex-wrap"):
                        for lbl, val in (("Session P&L", _money(psum["session_pnl"])),
                                         ("Realized", _money(psum["realized_pnl"])),
                                         ("Open P&L", _money(psum["open_unrealized"])),
                                         ("Equity", _money(psum["equity"])),
                                         ("Open", str(psum["open_count"]))):
                            _chip(lbl, val)
                if halted and ctrl_view.get("reason"):
                    ui.label(f"Halt: {ctrl_view['reason']}") \
                        .classes(f"text-xs {_t.TXT_WARN}")

            # Open positions — the LIVE paper account (where the driver trades),
            # falling back to the autonomous snapshot only if the paper account
            # isn't cached yet.
            positions = (paper.get("positions") if psum["has_account"]
                         else auto.get("positions")) or []
            with ui.card().classes(f"{_t.CARD} w-full gap-2"):
                kit.section_title(f"Open positions ({len(positions)})")
                if positions:
                    pos_tbl = kit.table(_POSITION_COLS, position_rows(positions),
                                        row_key="position_id",
                                        numeric=_POSITION_NUMERIC,
                                        classes="w-full driver-table")
                    pos_tbl.add_slot("body-cell-pnl", _PNL_CELL_SLOT)
                else:
                    kit.empty("No open positions.")

            # Decision log (per-checkpoint thesis + executed/rejected + halt) —
            # TODAY's checkpoints only (the full history isn't useful day-to-day).
            log = decision_log_rows(current_day_decisions(auto.get("decisions")))
            with ui.card().classes(f"{_t.CARD} w-full gap-2"):
                kit.section_title(f"Decision log — today ({len(log)})")
                if not log:
                    kit.empty("No checkpoints today — enable autonomy or click "
                              "“Run now”.")
                for row in log:
                    _decision_card(row)

            # Performance scorecard — the driver account's standalone track record
            # (cache:options:driver_paper_perf, refreshed every 1-min manage tick).
            _scorecard_card(state["dperf"] or {})

    def _decision_card(row):
        halted = row.get("halted")
        with ui.card().classes(f"{_t.CARD} w-full gap-1"):
            with ui.row().classes("items-center gap-2 flex-wrap"):
                ui.label(to_central(row.get("ts", ""))).classes(f"text-xs {_t.MUTED}")
                if row.get("stand_down"):
                    ui.label("STAND DOWN").classes("text-xs text-weight-bold "
                                                   + _t.TXT_WARN)
                    # R7: an OPS-INCIDENT stand-down (no_key/api_error/parse_error)
                    # gets a distinct red chip so a broken key isn't mistaken for
                    # weeks of "cautious model behavior". A model stand-down / legacy
                    # row (no reason) shows nothing extra — renders as before.
                    incident = stand_down_reason_label(row.get("reason"))
                    if incident:
                        ui.label(incident).classes(
                            f"text-xs text-weight-bold px-2 {_t.BADGE_NEG}").tooltip(
                            "Stand-down was caused by an operational failure, not a "
                            "model decision — check the driver service / API key.")
                if halted:
                    ui.label("HALTED").classes("text-xs text-weight-bold "
                                               + _t.TXT_NEG)
            if row.get("thesis"):
                ui.label(row["thesis"]).classes(f"text-sm {_t.LABEL}")
            ui.label(decision_summary(row)).classes(f"text-xs {_t.LABEL}")
            for ex in row.get("executed") or []:
                rat = ex.get("rationale")
                line = (f"✓ {ex.get('symbol', '?')} ×{ex.get('qty', '?')}"
                        + (f" — {rat}" if rat else ""))
                ui.label(line).classes(f"text-xs {_t.TXT_POS}")
            for rj in row.get("rejected") or []:
                ui.label(f"✗ {rj.get('id', '?')} — {rj.get('reason', '')}") \
                    .classes(f"text-xs {_t.TXT_NEG}")
            shadow = shadow_gate_line(row)
            if shadow:
                ui.label(f"👁 {shadow}").classes(
                    f"text-xs text-weight-medium {_t.TXT_WARN}").tooltip(
                    "Directional gate is in log-only shadow mode — this trade fired but a "
                    "LIVE gate would have blocked it as wrong-side for the tape. Evidence "
                    "for enabling settings.DIRECTIONAL_GATE_ENABLED.")
            if halted and row.get("halt_reason"):
                ui.label(row["halt_reason"]).classes(f"text-xs {_t.TXT_WARN}")

    def _chip(label, value):
        with ui.column().classes("gap-0"):
            ui.label(label).classes(_t.EYEBROW)
            ui.label(value).classes(f"text-sm text-weight-medium {_t.LABEL}")

    def _scorecard_card(perf):
        # Plain-widget card (no Highcharts) → safe to rebuild in place each repaint.
        with ui.card().classes(f"{_t.CARD} w-full gap-2"):
            kit.section_title("Performance scorecard")
            ui.label("The driver account's standalone track record (isolated paper "
                     "book; updates every minute as it reprices).") \
                .classes(f"text-xs {_t.MUTED}")
            if not perf or not perf.get("total_trades"):
                kit.empty("No driver trades recorded yet.")
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
                ui.label(bw).classes(f"text-xs {_t.MUTED}")
            # Breakdown tables (P&L by symbol / by strategy).
            with ui.row().classes("w-full gap-4 items-start flex-wrap"):
                sym_rows = scorecard_symbol_rows(perf)
                if sym_rows:
                    with ui.column().classes("gap-1 flex-1 min-w-[260px]"):
                        ui.label("P&L by symbol").classes(_t.EYEBROW)
                        st = kit.table(_SCORE_SYMBOL_COLS, sym_rows,
                                       row_key="symbol", numeric=_SCORE_NUMERIC,
                                       classes="w-full driver-table")
                        st.add_slot("body-cell-pnl", _PNL_CELL_SLOT)
                strat_rows = scorecard_strategy_rows(perf)
                if strat_rows:
                    with ui.column().classes("gap-1 flex-1 min-w-[260px]"):
                        ui.label("P&L by strategy").classes(_t.EYEBROW)
                        st = kit.table(_SCORE_STRATEGY_COLS, strat_rows,
                                       row_key="strategy", numeric=_SCORE_NUMERIC,
                                       classes="w-full driver-table")
                        st.add_slot("body-cell-pnl", _PNL_CELL_SLOT)

    def _render_perf():
        # The driver's realized track record = the isolated paper account's CLOSED
        # trades (cache:options:driver_paper_account['closed_positions']), NOT the dead
        # legacy trade_log ledger. Rides the same 2s version-poll as the monitor.
        closed = (state["paper"] or {}).get("closed_positions") or []
        perf_summary.text = closed_summary_text(
            closed, (state["paper"] or {}).get("closed_totals"))
        perf_table.rows = closed_trade_rows(closed)
        perf_table.update()

    # ── command enqueue ───────────────────────────────────────────────────────
    @guard
    def _do(cmd, busy_msg, btn=None, **args):
        # The wait is the region scrim plus the button's own spinner, both
        # released by the repaint the command causes. The old ``status`` label
        # was written here and at the Refresh below, and NEVER RESET - those two
        # were its only writes, so "Stopping…" stayed on screen for the rest of
        # the session.
        bus_client.request("driver", {"type": cmd, "args": args})
        mon.busy.show(busy_msg)
        if btn is not None:
            kit.set_busy(btn)

    @guard
    def _on_toggle(e):
        # Master switch: enable re-arms a prior halt (per the service); disable
        # stands the loop down without latching. Record the intent so a repaint
        # can't flip the switch before the command lands (resolve_switch_state),
        # and reset the confirmation timeout.
        state["pending_enabled"] = bool(e.value)
        state["pending_ticks"] = 0
        if e.value:
            _do("enable", "Enabling autonomous trading…")
        else:
            _do("disable", "Disabling autonomous trading…")

    @guard
    def _refresh_perf():
        # Force an immediate driver-account reprice + republish (options_svc) so the
        # closed-trade table refreshes now, not at the next 1-min manage tick.
        bus_client.request("options", {"type": "driver_paper_manage"})
        mon.busy.show("Repricing the driver book…")
        kit.set_busy(perf_btn)

    perf_btn.on_click(_refresh_perf)
    run_btn.on_click(lambda: _do("cycle", "Running a checkpoint…", btn=run_btn))

    # ── version-poll repaint (fetch-free) ─────────────────────────────────────
    _POLL_VIEWS = ["driver:autonomous", "driver:control",
                   "options:driver_paper_account", "options:driver_paper_perf",
                   "options:driver_paper_analytics"]

    def _read_monitor_payloads():
        return (bus_client.read("driver:autonomous") or None,
                bus_client.read("driver:control") or None,
                bus_client.read("options:driver_paper_account") or None,
                bus_client.read("options:driver_paper_perf") or None)

    @guard_async
    async def _poll():
        # Monitor: repaint when the autonomous view, the control key, the live DRIVER
        # paper account, OR the driver performance scorecard advances. Batch the 5
        # version probes into ONE pipelined read_versions (was 5 round-trips/tick)
        # and read the changed payloads OFF the event loop.
        vers = await run.io_bound(bus_client.read_versions, _POLL_VIEWS)
        avv = vers.get("driver:autonomous")
        cvv = vers.get("driver:control")
        ppv = vers.get("options:driver_paper_account")
        dpv = vers.get("options:driver_paper_perf")
        dav = vers.get("options:driver_paper_analytics")
        if (avv != state["auto_ver"] or cvv != state["ctrl_ver"]
                or ppv != state["paper_ver"] or dpv != state["dperf_ver"]):
            state["auto_ver"] = avv
            state["ctrl_ver"] = cvv
            state["paper_ver"] = ppv
            state["dperf_ver"] = dpv
            (state["auto"], state["ctrl"], state["paper"],
             state["dperf"]) = await run.io_bound(_read_monitor_payloads)
            # The version moving IS the answer landing: the scrim comes down and
            # every held action button is handed back (_render_monitor).
            _render_monitor()
            _render_perf()          # the closed-trade table lives in the driver account
        if dav != state["analytics_ver"]:
            state["analytics_ver"] = dav
            state["analytics"] = await run.io_bound(
                bus_client.read, "options:driver_paper_analytics") or None
            _render_analytics()
        # Optimistic-toggle timeout: if the control state never catches up to the
        # user's pending toggle (command never consumed — e.g. driver_svc down),
        # give up after a few ticks: revert the switch to reality and warn. This is
        # the feedback that surfaces a dead service instead of a silently stuck toggle.
        if state["pending_enabled"] is not None:
            state["pending_ticks"] += 1
            if state["pending_ticks"] >= _PENDING_TIMEOUT_TICKS:
                state["pending_enabled"] = None
                state["pending_ticks"] = 0
                kit.toast("warn",
                          "Enable/Disable didn't take — is driver_svc running?")
                _render_monitor()

    # Initial paint (graceful-empty when the service is cold / nothing cached).
    # One pipelined version probe, mirroring the poll.
    _seed_vers = bus_client.read_versions(_POLL_VIEWS)
    state["auto_ver"] = _seed_vers.get("driver:autonomous")
    state["ctrl_ver"] = _seed_vers.get("driver:control")
    state["paper_ver"] = _seed_vers.get("options:driver_paper_account")
    state["dperf_ver"] = _seed_vers.get("options:driver_paper_perf")
    state["analytics_ver"] = _seed_vers.get("options:driver_paper_analytics")
    (state["auto"], state["ctrl"], state["paper"],
     state["dperf"]) = _read_monitor_payloads()
    state["analytics"] = bus_client.read("options:driver_paper_analytics") or None
    _render_monitor()
    _render_perf()
    _render_analytics()
    ui.timer(2.0, _poll)
