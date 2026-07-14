"""Per-day counter of Anthropic (Claude) API calls — cross-tier shared store.

Three separate processes call Claude (the driver decider, options_svc's Gamma
Analyze, market_svc's ticker summary), so the counter lives in ``shared/`` and
each caller records into one SQLite file (``shared/data/anthropic_call_counts.db``,
short WAL transactions — safe across processes). ``record()`` is hooked
immediately before each ``messages.create``, so no-key / stand-down paths that
never reach the API are (correctly) not counted, while every real attempt is.
The webgui Settings "API usage" card reads ``stats()`` directly (a tiny
stdlib-only read — ``shared/`` is the sanctioned cross-tier library, like
``shared.bus``).

Best-effort by design: ``record``/``stats`` never raise, so a counter failure
can never break a Claude call. Under pytest the default connection is
in-memory (the house isolation rule — tests must never write the real counts
file); an explicit ``path`` is always honored.
"""
from __future__ import annotations

import datetime as _dt
import os
import pathlib
import sqlite3
import threading

DB_PATH = pathlib.Path(__file__).resolve().parent / "data" / "anthropic_call_counts.db"
_SCHEMA = ("CREATE TABLE IF NOT EXISTS anthropic_calls ("
           "day TEXT PRIMARY KEY, n INTEGER NOT NULL)")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def connect(path=None) -> sqlite3.Connection:
    """Open (and initialize) the counts DB. ``path=None`` → the real file, or
    ``:memory:`` under pytest (PYTEST_CURRENT_TEST)."""
    if path is None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            path = ":memory:"
        else:
            path = DB_PATH
    if str(path) != ":memory:":
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:  # WAL + busy timeout: three service processes write concurrently.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
    except Exception:  # noqa: BLE001 — pragmas are best-effort (e.g. :memory:)
        pass
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = connect()
    return _conn


def record(n: int = 1, day: str | None = None) -> None:
    """Add ``n`` Claude calls to ``day`` (default: today, local). Never raises."""
    try:
        d = day or _dt.date.today().isoformat()
        with _lock:
            c = _get_conn()
            c.execute(
                "INSERT INTO anthropic_calls(day, n) VALUES(?, ?) "
                "ON CONFLICT(day) DO UPDATE SET n = n + excluded.n", (d, int(n)))
            c.commit()
    except Exception:  # noqa: BLE001 — counting must never break a Claude call.
        pass


def stats(today: _dt.date | None = None) -> dict:
    """Rollups: ``{"today", "last_7_days", "last_30_days", "since"}``.

    Rolling windows INCLUDE today (7 = today + prior 6). ``since`` is the
    earliest counted day (None before the first count). Never raises — zeros
    on any failure."""
    try:
        t = today or _dt.date.today()
        with _lock:
            rows = dict(_get_conn().execute(
                "SELECT day, n FROM anthropic_calls").fetchall())
        t_iso = t.isoformat()

        def _window(days: int) -> int:
            lo = (t - _dt.timedelta(days=days - 1)).isoformat()
            return sum(v for d, v in rows.items() if lo <= d <= t_iso)

        return {"today": int(rows.get(t_iso, 0)),
                "last_7_days": _window(7),
                "last_30_days": _window(30),
                "since": min(rows) if rows else None}
    except Exception:  # noqa: BLE001
        return {"today": 0, "last_7_days": 0, "last_30_days": 0, "since": None}
