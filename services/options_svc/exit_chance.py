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
#: The closes the app's own rules make. Anything else is the operator's: the
#: Close dialog's reason is free text, so a hand close is not always spelled
#: MANUAL_CLOSE, and its exit value says nothing about the rules.
AUTOMATIC = ("MONEY_STOP", "DELTA_STOP", "TIME_STOP", "BREAKEVEN_STOP", "MANAGE_DTE",
             "TARGET_HIT", "TIME_EXIT", "EXPIRED")
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
        if (s["legs_json"] or "").strip() or str(s["exit_reason"] or "") not in AUTOMATIC:
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


# ── the baseline, the model and the walk-forward ────────────────────────────
def bucket_rate(train_rows, w, edges):
    """The weighted rate of ``y`` in each ``pnl_frac`` bucket of the training
    rows (``edges`` sorted; a value equal to an edge falls in the bucket above
    it), as a function of ``pnl_frac``. A bucket the training rows left empty
    takes their overall rate."""
    edges = list(edges)
    sums = [[0.0, 0.0] for _ in range(len(edges) + 1)]
    total = [0.0, 0.0]
    for r, wi in zip(train_rows, w):
        b = sums[_bucket(r.x["pnl_frac"], edges)]
        b[0] += wi * r.y
        b[1] += wi
        total[0] += wi * r.y
        total[1] += wi
    overall = total[0] / total[1] if total[1] > 0 else 0.5
    rates = [s[0] / s[1] if s[1] > 0 else overall for s in sums]
    return lambda frac: rates[_bucket(frac, edges)]


def _bucket(frac, edges) -> int:
    return sum(1 for e in edges if frac >= e)


def _weights(rows_) -> list:
    """Each entry day weighs one, split equally over its trades, and each
    trade over its rows."""
    trades_of, rows_of = {}, {}
    for r in rows_:
        trades_of.setdefault(r.day, set()).add(r.trade)
        rows_of[r.trade] = rows_of.get(r.trade, 0) + 1
    return [1.0 / len(trades_of[r.day]) / rows_of[r.trade] for r in rows_]


def _columns(train_rows):
    """``(means, stds, with_missing)`` for the inputs, from the training rows.
    An input never present in training is centred at 0 with spread 1, so its
    standardised value is 0 and only its missing indicator carries anything."""
    means, stds, missing = {}, {}, []
    for name in INPUTS:
        vals = [r.x.get(name) for r in train_rows]
        present = [v for v in vals if v is not None]
        if len(present) < len(vals):
            missing.append(name)
        if present:
            m = float(np.mean(present))
            s = float(np.std(present))
        else:
            m, s = 0.0, 1.0
        means[name], stds[name] = m, (s if s > 1e-12 else 1.0)
    return means, stds, missing


def _matrix(rows_, means, stds, missing) -> np.ndarray:
    out = np.empty((len(rows_), 1 + len(INPUTS) + len(missing)))
    for i, r in enumerate(rows_):
        vals = [1.0]
        for name in INPUTS:
            v = r.x.get(name)
            vals.append(0.0 if v is None else (v - means[name]) / stds[name])
        vals.extend(1.0 if r.x.get(name) is None else 0.0 for name in missing)
        out[i] = vals
    return out


def _fit(train_rows, w, penalty, *, test_day=None):
    """The fitted model as a function of a list of rows. ``test_day`` names the
    day it is for (only for tests that look at what each fit trained on)."""
    means, stds, missing = _columns(train_rows)
    x = _matrix(train_rows, means, stds, missing)
    y = np.array([r.y for r in train_rows], dtype=float)
    prec = np.full(x.shape[1], float(penalty))
    prec[0] = INTERCEPT_PREC
    theta = logistic.map_fit(x, y, np.asarray(w, dtype=float), np.zeros(x.shape[1]), prec)
    return lambda rows_: logistic.sigmoid(_matrix(rows_, means, stds, missing) @ theta)


def _day_start(day) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=CENTRAL)


def walk_forward(rows_, *, min_train_days=None, penalty=None, edges=None) -> list:
    """``(row, p_model, p_distance, weight)`` for every scored row.

    A test day is predicted by a fit on the rows of trades that had CLOSED
    before that day began, so a trade still open then cannot lend its ending
    to the fit. Scoring starts once those trades span ``min_train_days`` entry
    days."""
    min_train = cfg.exit_min_train_days() if min_train_days is None else min_train_days
    lam = cfg.exit_penalty() if penalty is None else penalty
    edges = cfg.exit_buckets() if edges is None else edges
    weights = _weights(rows_)
    by_day = {}
    for r, w in zip(rows_, weights):
        by_day.setdefault(r.day, []).append((r, w))
    out = []
    for day in sorted(by_day):
        start = _day_start(day)
        train = [(r, w) for r, w in zip(rows_, weights)
                 if r.closed is not None and r.closed < start and r.day < day]
        if len({r.day for r, _w in train}) < min_train:
            continue
        t_rows, t_w = [r for r, _w in train], [w for _r, w in train]
        if len({r.y for r in t_rows}) < 2:
            continue
        model = _fit(t_rows, t_w, lam, test_day=day)
        distance = bucket_rate(t_rows, t_w, edges)
        test = by_day[day]
        p = model([r for r, _w in test])
        out.extend((r, float(pi), float(distance(r.x["pnl_frac"])), w)
                   for (r, w), pi in zip(test, p))
    return out


def score(scored, *, n_boot=None) -> dict:
    """The raw figures for one population: rows, days and trades scored, both
    Brier scores, the model's minus the baseline's per day (negative is the
    model better), the reliability bands and the last day scored."""
    n_boot = cfg.bootstrap_draws() if n_boot is None else n_boot
    y = [r.y for r, _p, _q, _w in scored]
    p = [pi for _r, pi, _q, _w in scored]
    q = [qi for _r, _p, qi, _w in scored]
    w = [wi for _r, _p, _q, wi in scored]
    days = [r.day for r, _p, _q, _w in scored]
    diff = None
    if scored:
        diff = metrics.day_bootstrap_diff([(a - b) ** 2 for a, b in zip(p, y)],
                                          [(a - b) ** 2 for a, b in zip(q, y)],
                                          days, w, n_boot=n_boot)
    return {"days": len(set(days)), "rows": len(scored),
            "trades": len({r.trade for r, _p, _q, _w in scored}),
            "brier": metrics.brier(p, y, w), "brier_distance": metrics.brier(q, y, w),
            "diff": diff, "reliability": metrics.reliability(p, y, w),
            "last_day": max(days, default=None)}


def evaluate(db_path=None) -> list:
    """One entry per configured scanner type the store holds: its score plus
    ``inputs``, the drift inputs at each row as ``(day, {name: value})``."""
    trades = load(db_path)
    out = []
    for kind in cfg.exit_types():
        mine = [t for t in trades if t.kind == kind]
        if not mine:
            continue
        rows_ = rows(mine)
        entry = {"type": kind, **score(walk_forward(rows_))}
        entry["inputs"] = [(r.day, {k: r.x.get(k) for k in DRIFT_INPUTS}) for r in rows_]
        out.append(entry)
    return out
