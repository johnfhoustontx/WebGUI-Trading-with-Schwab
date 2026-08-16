"""Driver page (Tier-3 reader) — autonomous monitor + STOP + realized performance.

This page holds **no engine call**. The autonomous Claude decision layer, order
execution, and performance aggregation all live in ``services/driver_svc`` /
``services/options_svc``; the page reads cached views and enqueues commands.

**Autonomous monitor (autonomy level B).** Reads ``cache:driver:autonomous``
(``AutonomousState`` — day P&L vs the $500 target, open driver positions, and the
newest-first per-checkpoint decision log) and ``cache:driver:control``
(``DriverControl`` — the enabled/halted master switch). It is a MONITOR + OVERRIDE:
* **Enable / Disable** toggle → ``{"type":"enable"|"disable"}`` on ``cmd:driver`` —
  the master switch (Enable also re-arms a prior day's halt).
* **STOP** (kill-switch, confirm-gated) → ``{"type":"stop"}`` — latches ``halted``
  so no further checkpoints run until the next-day re-arm.
* **Run now** → ``{"type":"cycle"}`` — fire one decision checkpoint immediately.

**Performance.** The driver's realized track record from its isolated paper account
(``cache:options:driver_paper_account['closed_positions']`` — closed credit spreads
with real realized P&L, updated every 5-min manage cycle); **Refresh** forces an
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
from pages import busy as _busy
from nicegui import run, ui

from pages.ui_guard import guard, guard_async
from pages.options.theme import BTN, BTN_DANGER, BTN_PRIMARY

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
# read by COLOR, not by hunting for a +/- sign. These hexes equal the theme
# TXT_POS/TXT_NEG/TXT_NEUTRAL tokens, but are kept LOCAL because driver.py has no
# theme.py dependency (it's not an options-section page).
PNL_GREEN, PNL_RED, PNL_NEUTRAL = "#66bb6a", "#ef5350", "#bdbdbd"


def pnl_color(v):
    """Hex color for a numeric P&L: green > 0, red < 0, grey for 0 / None / junk."""
    if not isinstance(v, (int, float)) or v == 0:
        return PNL_NEUTRAL
    return PNL_GREEN if v > 0 else PNL_RED


def pnl_class(v):
    """Tailwind text arbitrary-value class for a numeric P&L (mirrors :func:`pnl_color`)."""
    return f"text-[{pnl_color(v)}]"


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
# ``cache:options:driver_paper_account['closed_positions']`` — updated every 5-min
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


def closed_summary_text(closed):
    """One-line realized-performance summary from the driver account's closed trades."""
    priced = [c for c in (closed or [])
              if isinstance(c, dict) and isinstance(c.get("realized_pnl"), (int, float))]
    if not priced:
        return ("No closed trades yet — the driver's realized P&L appears here as its "
                "positions close (target / stop / expiry).")
    wins = [c for c in priced if c["realized_pnl"] > 0]
    losses = [c for c in priced if c["realized_pnl"] < 0]
    realized = round(sum(c["realized_pnl"] for c in priced), 2)
    wr = round(100 * len(wins) / len(priced))
    return (f"Closed: {len(priced)} · {len(wins)}W–{len(losses)}L ({wr}% win) · "
            f"Realized: {_money(realized)}")


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
    {"name": "qty", "label": "Qty", "field": "qty"},
    {"name": "reason", "label": "Exit reason", "field": "reason", "align": "left"},
    {"name": "pnl", "label": "Realized P&L", "field": "pnl"},
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


def control_bg_class(control):
    """Tailwind bg arbitrary-value class for the control state (mirrors :func:`control_state_color`)."""
    return f"bg-[{control_state_color(control)}]"


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
            "_pnl_color": pnl_color(p.get("unrealized_pnl")),
            "_pnl_class": pnl_class(p.get("unrealized_pnl")),
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
            "_pnl_color": pnl_color(r.get("pnl")),
            "_pnl_class": pnl_class(r.get("pnl")),
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
    {"name": "strikes", "label": "Strikes", "field": "strikes", "align": "left"},
    {"name": "expiration", "label": "Expiration", "field": "expiration", "align": "left"},
    {"name": "opened", "label": "Opened", "field": "opened", "align": "left"},
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


# Driver-table styling: fixed (sticky) header over a scrolling body, so the column
# headers stay visible as the trade list scrolls; colored P&L is via body-cell slots.
DRIVER_CSS = """
.driver-table .q-table__middle { max-height: 52vh; }
.driver-table thead tr th {
  position: sticky; top: 0; z-index: 2; background: #141a30;
}
"""

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
    """Driver page: autonomous monitor + STOP + realized performance."""
    ui.add_css(DRIVER_CSS)
    ui.label("Claude Driver").classes("text-h5")
    ui.label("Autonomous PAPER options trader (Claude decides, code-enforced "
             "guardrails). This page MONITORS what it does and lets you STOP it. "
             "Paper only — nothing is sent to Schwab.").classes("text-xs opacity-60")

    state = {
        "auto": None, "auto_ver": None, "ctrl": None, "ctrl_ver": None,
        "paper": None, "paper_ver": None,
        "dperf": None, "dperf_ver": None,        # driver-account performance scorecard
        "analytics": None, "analytics_ver": None,  # equity curve + posture post-mortem + MAE/MFE
        "pending_enabled": None, "pending_ticks": 0,
    }

    # ── Autonomous monitor + override ─────────────────────────────────────────
    monitor = ui.column().classes("w-full gap-3")
    # A cycle runs the decider (a Claude call) and a manage reprices the book;
    # both take seconds during which the monitor shows the previous state.
    monitor_busy = _busy.build_busy(monitor, "Running…")
    # Busy-message line for the monitor's autonomous actions (enable/disable/stop/
    # cycle) + the performance Refresh below.
    status = ui.label("").classes("opacity-70 text-sm")

    ui.separator()
    with ui.row().classes("items-center gap-3 flex-wrap"):
        ui.label("Performance").classes("text-h6")
        perf_btn = ui.button("Refresh", icon="refresh", color=None) \
            .props("no-caps dense").classes(BTN)
    ui.label("The driver's closed trades and realized P&L from its isolated paper "
             "account — updates every 5-min manage cycle as positions close.") \
        .classes("text-xs opacity-50")
    perf_summary = ui.label("").classes("text-sm opacity-80")
    perf_table = ui.table(columns=_CLOSED_COLS, rows=[], row_key="cid") \
        .classes("w-full driver-table").props("dense")
    perf_table.add_slot("body-cell-pnl", _PNL_CELL_SLOT)

    # ── Performance analytics: equity curve + posture post-mortem + MAE/MFE ────
    # Persistent elements (the Highcharts element must exist at first render — the
    # ESM import-map gotcha — and is updated in place, never rebuilt).
    ui.separator()
    ui.label("Analytics").classes("text-h6")
    ui.label("Realized equity curve, whether trading WITH or AGAINST the tape paid "
             "(posture at entry vs outcome), and how far trades ran for/against before "
             "closing (MAE/MFE) — the driver book's self-diagnostics.") \
        .classes("text-xs opacity-50")
    equity_chart = ui.highchart(equity_curve_figure([])).classes("w-full")
    analytics_headline = ui.label("").classes("text-sm opacity-80")
    postmortem_table = ui.table(columns=_POSTMORTEM_COLS, rows=[], row_key="stance") \
        .classes("w-full driver-table").props("dense")
    postmortem_table.add_slot("body-cell-pnl", _PNL_CELL_SLOT)
    excursion_label = ui.label("").classes("text-xs opacity-60")
    analytics_empty = ui.label("No closed driver trades yet — analytics populate as "
                               "positions close.").classes("text-xs opacity-50")

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

    # ── confirm dialog for STOP (latch the kill-switch) ───────────────────────
    with ui.dialog() as stop_dialog, ui.card():
        ui.label("STOP the autonomous driver?").classes("text-subtitle1")
        ui.label("Latches the kill-switch for the rest of today — no new trades "
                 "will be opened. Open positions keep auto-managing. Enable "
                 "re-arms it (clears the halt).").classes("text-xs opacity-70")
        with ui.row().classes("justify-end gap-2 w-full"):
            ui.button("Cancel", on_click=stop_dialog.close).props("flat")
            ui.button("STOP", color=None,
                      on_click=lambda: (_do("stop", "Stopping…"),
                                        stop_dialog.close())).props("no-caps").classes(BTN_DANGER)

    # ── autonomous monitor render (rebuilt in place from cache:driver:*) ───────
    def _render_monitor():
        monitor_busy.hide()
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
                        .classes("text-weight-bold text-white px-3 py-1 rounded "
                                 + control_bg_class(ctrl_view))
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
                    ui.button("Run now", icon="bolt", color=None,
                              on_click=lambda: _do("cycle", "Running a checkpoint…")) \
                        .props("no-caps").classes(BTN_PRIMARY)
                    ui.button("STOP", icon="stop", color=None,
                              on_click=stop_dialog.open) \
                        .props("no-caps").classes(f"{BTN_DANGER} text-weight-bold")

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
                    pos_tbl = ui.table(columns=_POSITION_COLS, rows=position_rows(positions),
                                       row_key="position_id").classes("w-full driver-table").props("dense")
                    pos_tbl.add_slot("body-cell-pnl", _PNL_CELL_SLOT)
                else:
                    ui.label("No open positions.").classes("text-xs opacity-50")

            # Decision log (per-checkpoint thesis + executed/rejected + halt) —
            # TODAY's checkpoints only (the full history isn't useful day-to-day).
            log = decision_log_rows(current_day_decisions(auto.get("decisions")))
            with ui.card().classes("w-full gap-2"):
                ui.label(f"Decision log — today ({len(log)})") \
                    .classes("text-subtitle2 opacity-70")
                if not log:
                    ui.label("No checkpoints today — enable autonomy or click "
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
                    # R7: an OPS-INCIDENT stand-down (no_key/api_error/parse_error)
                    # gets a distinct red chip so a broken key isn't mistaken for
                    # weeks of "cautious model behavior". A model stand-down / legacy
                    # row (no reason) shows nothing extra — renders as before.
                    incident = stand_down_reason_label(row.get("reason"))
                    if incident:
                        ui.label(incident).classes(
                            "text-xs text-weight-bold text-white px-2 rounded "
                            "bg-[#E24B4A]").tooltip(
                            "Stand-down was caused by an operational failure, not a "
                            "model decision — check the driver service / API key.")
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
            shadow = shadow_gate_line(row)
            if shadow:
                ui.label(f"👁 {shadow}").classes(
                    "text-xs text-weight-medium text-amber-9").tooltip(
                    "Directional gate is in log-only shadow mode — this trade fired but a "
                    "LIVE gate would have blocked it as wrong-side for the tape. Evidence "
                    "for enabling settings.DIRECTIONAL_GATE_ENABLED.")
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
                        st = ui.table(columns=_SCORE_SYMBOL_COLS, rows=sym_rows,
                                      row_key="symbol").classes("w-full driver-table").props("dense")
                        st.add_slot("body-cell-pnl", _PNL_CELL_SLOT)
                strat_rows = scorecard_strategy_rows(perf)
                if strat_rows:
                    with ui.column().classes("gap-1 flex-1 min-w-[260px]"):
                        ui.label("P&L by strategy").classes("text-xs opacity-60")
                        st = ui.table(columns=_SCORE_STRATEGY_COLS, rows=strat_rows,
                                      row_key="strategy").classes("w-full driver-table").props("dense")
                        st.add_slot("body-cell-pnl", _PNL_CELL_SLOT)

    def _render_perf():
        # The driver's realized track record = the isolated paper account's CLOSED
        # trades (cache:options:driver_paper_account['closed_positions']), NOT the dead
        # legacy trade_log ledger. Rides the same 2s version-poll as the monitor.
        closed = (state["paper"] or {}).get("closed_positions") or []
        perf_summary.text = closed_summary_text(closed)
        perf_table.rows = closed_trade_rows(closed)
        perf_table.update()

    # ── command enqueue ───────────────────────────────────────────────────────
    @guard
    def _do(cmd, busy_msg):
        bus_client.request("driver", {"type": cmd})
        monitor_busy.show()
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

    @guard
    def _refresh_perf():
        # Force an immediate driver-account reprice + republish (options_svc) so the
        # closed-trade table refreshes now, not at the next 5-min manage tick.
        bus_client.request("options", {"type": "driver_paper_manage"})
        monitor_busy.show("Repricing the driver book…")
        status.text = "Refreshing performance…"

    perf_btn.on_click(_refresh_perf)

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
                ui.notify("Enable/Disable didn't take — is driver_svc running?",
                          type="warning")
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
