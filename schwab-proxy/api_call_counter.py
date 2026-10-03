"""Per-day counter of outbound Schwab API calls (SQLite, one row per local day).

Every actual HTTP request the proxy sends to Schwab is counted — the marketdata
path hooks ``TokenManager._rate_limit`` (called immediately before every
request, including retries) and the trader path counts each attempt in
``trader_request``. The Settings page shows the rollups via the proxy's
``GET /stats/api_calls`` endpoint (today / last 7 days / last 30 days —
rolling windows including today).

A second table, ``api_calls_detail``, breaks each day down by endpoint, caller
and outcome (``record_detail`` / ``detail_summary``). It counts REQUESTS to the
proxy, including the ones its local market-data store answered without calling
Schwab, so it is how hits and misses are attributed; the per-day total above
stays "calls sent to Schwab".

Counting is strictly best-effort: none of these functions raise, so a
counter failure can never break an API call. Under pytest the default
connection is in-memory (mirrors the Bus fakeredis / intraday-DB convention —
tests must never write the real counts file); an explicit ``path`` is always
honored.
"""
from __future__ import annotations

import datetime as _dt
import os
import pathlib
import sqlite3
import threading

DB_PATH = pathlib.Path(__file__).resolve().parent / "data" / "api_call_counts.db"
_SCHEMA = ("CREATE TABLE IF NOT EXISTS api_calls ("
           "day TEXT PRIMARY KEY, n INTEGER NOT NULL)")
_DETAIL_SCHEMA = ("CREATE TABLE IF NOT EXISTS api_calls_detail ("
                  "day TEXT NOT NULL, endpoint TEXT NOT NULL, caller TEXT NOT NULL, "
                  "outcome TEXT NOT NULL, n INTEGER NOT NULL, "
                  "PRIMARY KEY (day, endpoint, caller, outcome))")
# Outcomes that answered a request WITHOUT a call to Schwab.
LOCAL_OUTCOMES = ("hit", "subset", "coalesced", "composed")
# The most rows one day's breakdown lists. The totals always cover every row.
MAX_DETAIL_ROWS = 500

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
    # record() runs on the Schwab hot path (~60-70 calls/min RTH), each doing an
    # INSERT+commit. WAL + synchronous=NORMAL drops the per-commit fsync (the
    # dominant cost) so the lock is held only briefly — counts stay durable across
    # the process, only an OS crash could lose the last few. WAL needs a file.
    if str(path) != ":memory:":
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except Exception:  # noqa: BLE001 — pragmas are best-effort tuning.
            pass
    conn.execute(_SCHEMA)
    conn.execute(_DETAIL_SCHEMA)
    conn.commit()
    return conn


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = connect()
    return _conn


def record(n: int = 1, day: str | None = None) -> None:
    """Add ``n`` calls to ``day`` (default: today, local date). Never raises."""
    try:
        d = day or _dt.date.today().isoformat()
        with _lock:
            c = _get_conn()
            c.execute(
                "INSERT INTO api_calls(day, n) VALUES(?, ?) "
                "ON CONFLICT(day) DO UPDATE SET n = n + excluded.n", (d, int(n)))
            c.commit()
    except Exception:  # noqa: BLE001 — counting must never break an API call.
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
                "SELECT day, n FROM api_calls").fetchall())
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


def record_detail(endpoint: str, caller: str, outcome: str, n: int = 1,
                  day: str | None = None) -> None:
    """Add ``n`` to one (day, endpoint, caller, outcome) row. Never raises.

    ``outcome`` is ``upstream`` for a call sent to Schwab, one of
    ``LOCAL_OUTCOMES`` for a request answered locally, ``partial`` for a quote
    request that fetched only its stale symbols, or a ``shadow_*`` name."""
    try:
        d = day or _dt.date.today().isoformat()
        with _lock:
            c = _get_conn()
            c.execute(
                "INSERT INTO api_calls_detail(day, endpoint, caller, outcome, n) "
                "VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(day, endpoint, caller, outcome) "
                "DO UPDATE SET n = n + excluded.n",
                (d, str(endpoint)[:40], str(caller)[:40], str(outcome)[:40], int(n)))
            c.commit()
    except Exception:  # noqa: BLE001 — counting must never break a request.
        pass


def detail_summary(day: str | None = None) -> dict:
    """One day's breakdown: ``{"served_locally", "by_outcome", "rows"}``.

    ``by_outcome`` and ``served_locally`` are totals over EVERY row of the day.
    ``rows`` lists the ``MAX_DETAIL_ROWS`` largest: the caller name comes from a
    request header, so the number of rows is not ours to bound.
    Never raises — an empty summary on any failure."""
    try:
        d = day or _dt.date.today().isoformat()
        with _lock:
            conn = _get_conn()
            totals = conn.execute(
                "SELECT outcome, SUM(n) FROM api_calls_detail WHERE day = ? "
                "GROUP BY outcome ORDER BY SUM(n) DESC, outcome", (d,)).fetchall()
            rows = conn.execute(
                "SELECT endpoint, caller, outcome, n FROM api_calls_detail "
                "WHERE day = ? ORDER BY n DESC, endpoint, caller, outcome "
                "LIMIT ?", (d, max(0, int(MAX_DETAIL_ROWS)))).fetchall()
        by_outcome = {outcome: int(n) for outcome, n in totals}
        return {
            "served_locally": sum(by_outcome.get(o, 0) for o in LOCAL_OUTCOMES),
            "by_outcome": by_outcome,
            "rows": [{"endpoint": e, "caller": c, "outcome": o, "n": int(n)}
                     for e, c, o, n in rows],
        }
    except Exception:  # noqa: BLE001
        return {"served_locally": 0, "by_outcome": {}, "rows": []}
