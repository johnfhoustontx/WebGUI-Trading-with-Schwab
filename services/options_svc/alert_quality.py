"""Alert quality, scored on its own day (ML roadmap Phase 4, flow half).

For each flow alert type: how often the ``horizon_min`` minutes after an alert
ran wider (high minus low, over the starting price) than that symbol's usual
range at that time of day, against how often that symbol's OTHER windows the
same day did. The usual range is the median over the ``history_sessions``
archived sessions BEFORE the day, so nothing on or after a scored day goes into
its own yardstick. A busy day lifts both sides of the comparison.

Reads the alert record (``alert_log``) and the Gamma Archive's ``gex`` view:
one fetched spot per 5-minute grid point per collected symbol. Read-only; no
Schwab, proxy or Redis. ``model_health`` turns the entries into verdicts.
Nothing reads it to decide anything.
Design: docs/plans/2026-11-14-alert-quality-design.md
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import statistics
import sys
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from repo_paths import OPTIONS_SCANNER
from shared import model_health_config as cfg
from shared.ml import metrics
from services.options_svc import alert_log

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import gex_history_db as gh  # noqa: E402

CENTRAL = ZoneInfo("America/Chicago")
GRID_SEC = 300          # the archive's grid (config/gex_archive.toml grid_interval_min)
DROP_REASONS = ("price", "horizon", "history", "base")


def _day(ts) -> str:
    return _dt.datetime.fromtimestamp(ts, CENTRAL).date().isoformat()


def slot(ts) -> int:
    """The Central five-minute of the day ``ts`` falls in."""
    t = _dt.datetime.fromtimestamp(ts, CENTRAL)
    return (t.hour * 60 + t.minute) // 5


def _archive_path(archive_path):
    if archive_path is not None:
        return Path(archive_path)
    import gex_archive
    return Path(gex_archive.DB_PATH)


def load_paths(archive_path=None, symbols=None) -> dict:
    """``{(symbol, session date): [(ts, spot), ...]}`` from the archive's
    ``gex`` view: fetched rows with a price only, the futures feed left out.
    ``symbols`` narrows it; a missing archive is empty."""
    path = _archive_path(archive_path)
    if not path.exists():
        return {}
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        sql = (f"SELECT symbol, ts, spot FROM snapshots WHERE view = 'gex' "
               f"AND {gh.fetched_only_clause(conn)} AND spot > 0 "
               f"AND symbol NOT LIKE ?")
        args = [gh.FEED_SYMBOL_LIKE]
        if symbols is not None:
            names = sorted(symbols)
            if not names:
                return {}
            sql += f" AND symbol IN ({', '.join('?' * len(names))})"
            args += names
        out = defaultdict(list)
        for sym, ts, spot in conn.execute(sql + " ORDER BY symbol, ts", args):
            out[(sym, _day(ts))].append((int(ts), float(spot)))
        return dict(out)
    finally:
        conn.close()


def window_range(path, i, horizon_sec):
    """(highest - lowest) / starting price over ``horizon_sec`` from point
    ``i``, or ``None`` when the path does not reach a full horizon."""
    t0, s0 = path[i]
    if not s0 > 0:
        return None
    hi = lo = s0
    last = t0
    for ts, s in path[i + 1:]:
        if ts > t0 + horizon_sec:
            break
        hi, lo, last = max(hi, s), min(lo, s), ts
    if last < t0 + horizon_sec - GRID_SEC // 2:
        return None
    return (hi - lo) / s0


def _slot_ranges(path, horizon_sec) -> dict:
    """``{slot: range}`` of every point with a full window (the first of a slot)."""
    out = {}
    for i, (ts, _s) in enumerate(path):
        s = slot(ts)
        if s in out:
            continue
        r = window_range(path, i, horizon_sec)
        if r is not None:
            out[s] = r
    return out


def usual_ranges(paths, horizon_sec, history, min_history) -> dict:
    """``{(symbol, session, slot): median range}`` over that symbol's
    ``history`` sessions strictly before ``session`` that have the slot;
    absent when fewer than ``min_history`` do."""
    by_symbol = defaultdict(list)
    for (sym, day), path in paths.items():
        by_symbol[sym].append((day, _slot_ranges(path, horizon_sec)))
    out = {}
    for sym, days in by_symbol.items():
        days.sort()
        for k, (day, ranges) in enumerate(days):
            earlier = days[max(0, k - history):k]
            for s in ranges:
                seen = [r[s] for _d, r in earlier if s in r]
                if len(seen) >= min_history:
                    out[(sym, day, s)] = statistics.median(seen)
    return out


def _start(path, ts):
    """Index of the first point at or after ``ts``, within one grid step. Never
    a point before: an alert fired BY a move would be scored on that move."""
    i = bisect_left([p[0] for p in path], ts)
    if i >= len(path) or path[i][0] - ts > GRID_SEC:
        return None
    return i


def _base_rate(sym, day, path, usual, near, horizon_sec):
    """Share of the symbol-day's windows wider than usual, leaving out every
    window that overlaps an alert's own (any type); ``None`` with none left."""
    labels = []
    for i, (ts, _s) in enumerate(path):
        if any(abs(ts - a) <= horizon_sec for a in near):
            continue
        u = usual.get((sym, day, slot(ts)))
        if u is None:
            continue
        r = window_range(path, i, horizon_sec)
        if r is None:
            continue
        labels.append(1.0 if r > u else 0.0)
    return sum(labels) / len(labels) if labels else None


def score_type(kind, alerts, paths, usual, horizon_sec, n_boot) -> dict:
    """The entry for one alert type. ``alerts`` is the whole record (every
    type), since any alert's window is no 'other time' of its day."""
    near = defaultdict(list)
    for a in alerts:
        near[(a["symbol"], a["session_date"])].append(a["ts"])
    mine = [a for a in alerts if a["type"] == kind]
    dropped = dict.fromkeys(DROP_REASONS, 0)
    base_memo, scored = {}, []
    for a in mine:
        key = (a["symbol"], a["session_date"])
        path = paths.get(key)
        i = _start(path, a["ts"]) if path else None
        if i is None:
            dropped["price"] += 1
            continue
        r = window_range(path, i, horizon_sec)
        if r is None:
            dropped["horizon"] += 1
            continue
        u = usual.get((*key, slot(path[i][0])))
        if u is None:
            dropped["history"] += 1
            continue
        if key not in base_memo:
            base_memo[key] = _base_rate(*key, path, usual, near[key], horizon_sec)
        base = base_memo[key]
        if base is None:
            dropped["base"] += 1
            continue
        scored.append((a["session_date"], 1.0 if r > u else 0.0, base))
    entry = {"type": kind, "alerts": len(scored), "logged": len(mine), "days": 0,
             "hit_rate": None, "base_rate": None, "diff": None, "dropped": dropped,
             "last_day": None}
    if not scored:
        return entry
    per_day = defaultdict(int)
    for d, _l, _b in scored:
        per_day[d] += 1
    days = [d for d, _l, _b in scored]
    w = [1.0 / per_day[d] for d in days]
    n_days = len(per_day)
    entry.update(
        days=n_days, last_day=max(per_day),
        hit_rate=sum(wi * l for wi, (_d, l, _b) in zip(w, scored)) / n_days,
        base_rate=sum(wi * b for wi, (_d, _l, b) in zip(w, scored)) / n_days,
        diff=metrics.day_bootstrap_diff([-l for _d, l, _b in scored],
                                        [-b for _d, _l, b in scored], days, w,
                                        n_boot=n_boot))
    return entry


def evaluate(log_path=None, archive_path=None) -> list:
    """One entry per configured alert type, in the configured order."""
    horizon = cfg.alerts_horizon_min() * 60
    alerts = alert_log.load(db_path=log_path)
    symbols = {a["symbol"] for a in alerts}
    paths = load_paths(archive_path, symbols=symbols) if symbols else {}
    usual = usual_ranges(paths, horizon, cfg.alerts_history_sessions(),
                         cfg.alerts_min_history())
    n_boot = cfg.bootstrap_draws()
    return [score_type(kind, alerts, paths, usual, horizon, n_boot)
            for kind in cfg.alerts_types()]
