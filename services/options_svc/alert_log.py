"""The alert record: every flow alert that fires, written once and kept forever.

``services/options_svc/data/alert_log.db``, beside ``scalp.db``. It is gitignored
like every ``*.db`` and the nightly backup sweeps it; never add it to
``DB_EXCLUDE``. The Redis alert list is day-scoped and ``flow_contract_days`` is
purged after 20 sessions, so this is the only history that lets Model Health
score an alert type over more than a month (``alert_quality``).

Written by ``handlers.run_flow_alerts`` (``source = 'live'``) and once by
``tools/seed_alert_log.py`` (``'seed'``). ``db_path=None`` resolves the live path
when called, so a test redirects it.
Design: docs/plans/2026-11-14-alert-quality-design.md
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import repo_paths

DB_PATH = Path(repo_paths.SCALP_DB).parent / "alert_log.db"
_CT = ZoneInfo("America/Chicago")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    alert_id      TEXT PRIMARY KEY,
    session_date  TEXT    NOT NULL,
    ts            INTEGER NOT NULL,
    symbol        TEXT    NOT NULL,
    type          TEXT    NOT NULL,
    side          TEXT,
    detail_json   TEXT,
    source        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_type_day ON alerts (type, session_date);
"""

# Stored in their own columns, or not at all (``text`` is the screen's sentence).
_OWN = ("id", "type", "side", "symbol", "ts", "text")
_COLS = ("alert_id", "session_date", "ts", "symbol", "type", "side", "source")


def _path(db_path):
    return DB_PATH if db_path is None else db_path


def connect(db_path=None) -> sqlite3.Connection:
    """Open the store for writing, creating its folder and table."""
    path = Path(_path(db_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    return conn


def logged_enabled() -> bool:
    """``config/flow_alerts.toml [log] enabled``: a real bool, or on."""
    from services.options_svc import flow_alerts
    log_cfg = flow_alerts.section(flow_alerts.load_thresholds(), "log")
    raw = log_cfg.get("enabled", True)
    return raw if isinstance(raw, bool) else True


def _row(a, source):
    """The insert tuple for one alert, or ``None`` when it has no usable id,
    time, symbol or type."""
    if not isinstance(a, dict):
        return None
    aid, ts, sym, kind = a.get("id"), a.get("ts"), a.get("symbol"), a.get("type")
    if not (isinstance(aid, str) and aid and isinstance(sym, str) and sym
            and isinstance(kind, str) and kind):
        return None
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
        return None
    ts = int(ts)
    day = _dt.datetime.fromtimestamp(ts, _CT).date().isoformat()
    detail = {k: v for k, v in a.items() if k not in _OWN}
    side = a.get("side")
    return (aid, day, ts, sym, kind, side if isinstance(side, str) else None,
            json.dumps(detail, default=str, sort_keys=True), source)


def record(alerts, *, source="live", db_path=None) -> int:
    """Write each alert once (``INSERT OR IGNORE`` on its id). Returns the rows
    added. A malformed alert is skipped; a store failure raises (the caller
    counts it)."""
    rows = [r for r in (_row(a, source) for a in alerts or ()) if r is not None]
    if not rows:
        return 0
    conn = connect(db_path)
    try:
        with conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO alerts (alert_id, session_date, ts, symbol, "
                "type, side, detail_json, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
            return conn.total_changes - before
    finally:
        conn.close()


def load(db_path=None, types=None) -> list[dict]:
    """The record, oldest first, read-only. ``types`` narrows it; a missing
    store is an empty list."""
    path = Path(_path(db_path))
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        sql = f"SELECT {', '.join(_COLS)} FROM alerts"
        args = ()
        if types is not None:
            kinds = list(types)
            if not kinds:
                return []
            sql += f" WHERE type IN ({', '.join('?' * len(kinds))})"
            args = tuple(kinds)
        cur = conn.execute(sql + " ORDER BY ts, alert_id", args)
        return [dict(zip(_COLS, r)) for r in cur]
    finally:
        conn.close()
