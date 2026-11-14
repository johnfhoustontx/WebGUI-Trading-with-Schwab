"""The exit chance, scored out of sample (ML roadmap Phase 3b).

At each mark of a closed captured credit signal (PCS, CCS, IC), before the
first mark at its take-profit fraction (``[stops] tp_frac``), the chance it
still reaches that fraction before its own exits ended it. Judged against the
rate by how far it had already travelled (its P&L as a share of the credit,
in buckets), learned on earlier trades only.

``evaluate`` returns raw losses, day-clustered differences and the inputs;
``model_health`` turns those into verdicts and drift, so this module imports
nothing from it. Imports nothing from ``compute``. Nothing reads it to decide
anything. Design: docs/plans/2026-11-14-exit-chance-design.md
"""
from __future__ import annotations

import math
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from repo_paths import OPTIONS_SCANNER
from shared import model_health_config as cfg
from shared import structures
from shared import trade_mgmt
from shared.ml import logistic, metrics

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

CENTRAL = ZoneInfo("America/Chicago")
STRATEGIES = ("PCS", "CCS", "IC")
#: Closes that say nothing about the rules: the operator's own.
MANUAL = ("MANUAL_CLOSE", "MANUAL")
MINUTES_PER_YEAR = 365 * 24 * 60
#: The model's inputs, in column order.
INPUTS = ("pnl_frac", "peak_frac", "worst_frac", "life_used", "log_mins_left",
          "short_delta", "delta_change", "cushion", "entry_iv_rank", "is_ic", "is_ccs")
#: The inputs whose drift is read: what the market hands the scanner. The P&L
#: path and the time inputs follow from the trade itself.
DRIFT_INPUTS = ("short_delta", "cushion", "entry_iv_rank")
#: Prior precision on the intercept: next to nothing, so the base rate is free.
INTERCEPT_PREC = 1e-6


@dataclass
class Trade:
    signal_id: str
    kind: str
    strategy: str
    entry_day: str
    first_seen: datetime
    closed: datetime
    expiration: str
    short_strike: float | None
    call_short: float | None
    entry_short_delta: float | None
    entry_iv_rank: float | None
    exit_reason: str
    close_frac: float | None
    #: ``(time, pnl as a share of the credit, underlying, short delta)``.
    marks: list = field(default_factory=list)


@dataclass
class Row:
    day: str
    trade: str
    x: dict
    y: int
    #: When the trade closed: a test day trains only on trades closed before it.
    closed: datetime | None = None


# ── reading ─────────────────────────────────────────────────────────────────
def _when(text) -> datetime | None:
    """An ISO time as an aware datetime; naive means Central. None if unreadable."""
    if not text:
        return None
    try:
        t = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=CENTRAL)


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _default_db():
    return OPTIONS_SCANNER / "data" / "signals.db"


def load(db_path=None) -> list:
    """Closed credit captures with at least two usable marks, oldest first.

    The marks' own ``pnl_pct_of_credit`` gives each fraction, and the close's
    is ``realized_pnl`` over the scale the marks imply (``unrealized_pnl`` per
    percent): an Income capture is stored per share, so ``entry_credit * 100``
    would be the wrong scale for it. That is the fallback only."""
    path = db_path or _default_db()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" * len(STRATEGIES))
        sigs = conn.execute(
            "SELECT s.signal_id, s.scanner_type, s.strategy, s.short_strike, "
            "s.call_short, s.expiration, s.entry_credit, s.entry_short_delta, "
            "s.entry_iv_rank, s.first_seen_ts, s.first_seen_date, s.legs_json, "
            "o.close_ts, o.realized_pnl, o.exit_reason "
            "FROM signals s JOIN signal_outcomes o ON o.signal_id = s.signal_id "
            f"WHERE s.strategy IN ({placeholders}) AND {structures.not_tracked_sql('s.')} "
            "ORDER BY s.first_seen_ts", STRATEGIES).fetchall()
        marks = {}
        for r in conn.execute(
                "SELECT signal_id, mark_ts, unrealized_pnl, pnl_pct_of_credit, "
                "current_underlying, current_short_delta FROM signal_marks "
                "ORDER BY signal_id, mark_ts"):
            marks.setdefault(r["signal_id"], []).append(r)
    finally:
        conn.close()
    out = []
    for s in sigs:
        if (s["legs_json"] or "").strip() or str(s["exit_reason"] or "") in MANUAL:
            continue
        seen, closed = _when(s["first_seen_ts"]), _when(s["close_ts"])
        if seen is None or closed is None or not s["first_seen_date"]:
            continue
        path, scales = [], []
        for m in marks.get(s["signal_id"], ()):
            t, pct = _when(m["mark_ts"]), _num(m["pnl_pct_of_credit"])
            if t is None or pct is None:
                continue
            pnl = _num(m["unrealized_pnl"])
            if pnl is not None and abs(pct) > 1e-9:
                scales.append(pnl / (pct / 100.0))
            path.append((t, pct / 100.0, _num(m["current_underlying"]),
                         _num(m["current_short_delta"])))
        if len(path) < 2:
            continue
        scale = statistics.median(scales) if scales else None
        if scale is None or scale <= 0:
            credit = _num(s["entry_credit"])
            scale = credit * 100.0 if credit and credit > 0 else None
        realized = _num(s["realized_pnl"])
        close_frac = realized / scale if realized is not None and scale else None
        out.append(Trade(
            signal_id=s["signal_id"], kind=str(s["scanner_type"] or ""),
            strategy=structures.normalise(s["strategy"]),
            entry_day=str(s["first_seen_date"]), first_seen=seen, closed=closed,
            expiration=str(s["expiration"] or ""),
            short_strike=_num(s["short_strike"]), call_short=_num(s["call_short"]),
            entry_short_delta=_num(s["entry_short_delta"]),
            entry_iv_rank=_num(s["entry_iv_rank"]),
            exit_reason=str(s["exit_reason"] or ""), close_frac=close_frac, marks=path))
    return out


# ── rows ────────────────────────────────────────────────────────────────────
def _years_left(t, expiration) -> float | None:
    """Years from ``t`` to the expiry's settlement, through the one helper."""
    import options_calculator as oc
    try:
        exp = date.fromisoformat(expiration)
    except (TypeError, ValueError):
        return None
    return float(oc.expiry_time_to_years(t, exp))


def _cushion(strategy, spot, short_strike, call_short) -> float | None:
    """Spot's distance from the short strike, in percent of spot, positive on
    the safe side. An IC takes its nearer side."""
    if spot is None or spot <= 0:
        return None
    put = (spot - short_strike) / spot * 100.0 if short_strike is not None else None
    if strategy == "PCS":
        return put
    if strategy == "CCS":
        return (short_strike - spot) / spot * 100.0 if short_strike is not None else None
    call = (call_short - spot) / spot * 100.0 if call_short is not None else None
    sides = [v for v in (put, call) if v is not None]
    return min(sides) if sides else None


def _early_time_stop(trade, min_hold) -> bool:
    if trade.exit_reason != "TIME_STOP" or not min_hold:
        return False
    return (trade.closed - trade.first_seen) < timedelta(minutes=min_hold)


def rows(trades, *, tp_frac=None, thin_min=None, min_hold=None) -> list:
    """One row per kept mark before the trade's first mark at ``tp_frac``.

    ``y`` is 1 when a later mark or the close reaches ``tp_frac``. Marks are
    thinned to one per ``thin_min`` minutes, the first kept. A ``TIME_STOP``
    close within ``min_hold`` minutes of capture is left out: that stop fired
    on the first mark and the trade never had a path."""
    stops = trade_mgmt.stops()
    tp = stops["tp_frac"] if tp_frac is None else tp_frac
    thin = cfg.exit_thin_min() if thin_min is None else thin_min
    hold = stops.get("time_stop_min_hold_min", 0) if min_hold is None else min_hold
    out = []
    for tr in trades:
        if _early_time_stop(tr, hold):
            continue
        fracs = [m[1] for m in tr.marks]
        first_hit = next((i for i, f in enumerate(fracs) if f >= tp), None)
        end = len(fracs) if first_hit is None else first_hit
        if end == 0:
            continue
        y = 1 if first_hit is not None or (
            tr.close_frac is not None and tr.close_frac >= tp) else 0
        life = _years_left(tr.first_seen, tr.expiration)
        last_kept = None
        peak = worst = None
        for i in range(end):
            t, f, spot, delta = tr.marks[i]
            peak = f if peak is None else max(peak, f)
            worst = f if worst is None else min(worst, f)
            if last_kept is not None and t < last_kept + timedelta(minutes=thin):
                continue
            last_kept = t
            left = _years_left(t, tr.expiration)
            entry_d = tr.entry_short_delta
            out.append(Row(
                day=tr.entry_day, trade=tr.signal_id, y=y, closed=tr.closed,
                x={"pnl_frac": f, "peak_frac": peak, "worst_frac": worst,
                   "life_used": (min(max(1.0 - left / life, 0.0), 1.0)
                                 if left is not None and life else None),
                   "log_mins_left": (math.log(max(left * MINUTES_PER_YEAR, 1.0))
                                     if left is not None else None),
                   "short_delta": abs(delta) if delta is not None else None,
                   "delta_change": (abs(delta) - abs(entry_d)
                                    if delta is not None and entry_d is not None else None),
                   "cushion": _cushion(tr.strategy, spot, tr.short_strike, tr.call_short),
                   "entry_iv_rank": tr.entry_iv_rank,
                   "is_ic": 1.0 if tr.strategy == "IC" else 0.0,
                   "is_ccs": 1.0 if tr.strategy == "CCS" else 0.0}))
    return out
