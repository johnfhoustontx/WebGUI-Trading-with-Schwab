"""SQLite history of Gamma Analyze briefings.

Stores every generated briefing — the 4×/day Auto briefings (premarket / open /
midday / close) plus ad-hoc and manual runs — as its STRUCTURED analysis payload
(the source of truth). The report HTML is regenerated on demand from that payload
(see ``services/options_svc/gamma_briefing_report.py``) rather than being frozen at
generation time, so old briefings re-render in the current infographic design and the
data stays queryable.

One row per ``(date, slot)``:
* the four scheduled slots are unique per day → re-running a slot REPLACES its row;
* ad-hoc / manual runs use time-stamped slot labels (e.g. ``manual-1432``) so each is
  kept distinctly and never collides with the scheduled slots.

Idempotent ``connect`` (creates the schema); every function takes an explicit
connection so tests can drive a temp DB. Path defaults to
``repo_paths.GAMMA_BRIEFING_DB``.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

try:  # repo root is normally on sys.path (service / script bootstrap)
    from repo_paths import GAMMA_BRIEFING_DB as _DEFAULT_DB
except Exception:  # pragma: no cover - fallback when imported in isolation
    _DEFAULT_DB = Path(__file__).parent / "data" / "gamma_briefings.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS briefings (
    date          TEXT NOT NULL,   -- CT trading date YYYY-MM-DD
    slot          TEXT NOT NULL,   -- premarket|open|midday|close|adhoc|manual-HHMM
    generated_at  TEXT NOT NULL,   -- ISO timestamp (CT)
    symbol_scope  TEXT,            -- e.g. "$SPX/SPY/QQQ"
    model         TEXT,
    bias          REAL,            -- -100..100 (extracted for cheap trend queries)
    headline      TEXT,
    analysis_json TEXT NOT NULL,   -- the full structured analysis dict (source of truth)
    PRIMARY KEY (date, slot)
);
CREATE INDEX IF NOT EXISTS idx_briefings_date ON briefings(date);
"""

_COLS = ("date", "slot", "generated_at", "symbol_scope", "model",
         "bias", "headline", "analysis_json")


def connect(db_path=None) -> sqlite3.Connection:
    """Open (creating parent dir + schema) the briefings DB. Idempotent."""
    p = Path(db_path or _DEFAULT_DB)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.executescript(_SCHEMA)
    return conn


def _to_float(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f


def insert_briefing(conn, *, date, slot, generated_at, symbol_scope, model,
                    bias, headline, analysis) -> None:
    """Upsert a briefing row (REPLACE on (date, slot)). ``analysis`` is the structured
    dict; it is JSON-encoded. ``bias``/``headline`` are also pulled out as columns."""
    conn.execute(
        "INSERT OR REPLACE INTO briefings "
        "(date, slot, generated_at, symbol_scope, model, bias, headline, analysis_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(date), str(slot), str(generated_at),
         symbol_scope, model, _to_float(bias),
         headline or "", json.dumps(analysis)))
    conn.commit()


def _row_to_dict(row, with_analysis=True) -> dict:
    d = {"date": row[0], "slot": row[1], "generated_at": row[2],
         "symbol_scope": row[3], "model": row[4], "bias": row[5], "headline": row[6]}
    if with_analysis:
        try:
            d["analysis"] = json.loads(row[7]) if row[7] else None
        except Exception:  # noqa: BLE001 - a corrupt blob shouldn't kill a listing
            d["analysis"] = None
    return d


def get_briefing(conn, date, slot) -> dict | None:
    """One briefing (with parsed analysis), or None."""
    row = conn.execute(
        f"SELECT {', '.join(_COLS)} FROM briefings WHERE date=? AND slot=?",
        (str(date), str(slot))).fetchone()
    return _row_to_dict(row) if row else None


def briefings_for_date(conn, date, with_analysis=True) -> list[dict]:
    """All briefings for one date, chronological (premarket→close→manual)."""
    rows = conn.execute(
        f"SELECT {', '.join(_COLS)} FROM briefings WHERE date=? "
        "ORDER BY generated_at ASC", (str(date),)).fetchall()
    return [_row_to_dict(r, with_analysis) for r in rows]


def list_briefings(conn, start_date=None, end_date=None, limit=None,
                   with_analysis=False) -> list[dict]:
    """Briefings in ``[start_date, end_date]`` (inclusive; either may be None),
    newest date first then chronological within a day. ``with_analysis`` False by
    default (metadata only — cheap for listings)."""
    q = f"SELECT {', '.join(_COLS)} FROM briefings"
    conds, args = [], []
    if start_date:
        conds.append("date >= ?"); args.append(str(start_date))
    if end_date:
        conds.append("date <= ?"); args.append(str(end_date))
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY date DESC, generated_at ASC"
    if limit:
        q += " LIMIT ?"; args.append(int(limit))
    rows = conn.execute(q, args).fetchall()
    return [_row_to_dict(r, with_analysis) for r in rows]


def purge(conn, keep_days: int) -> int:
    """Delete rows older than the most recent ``keep_days`` DISTINCT dates. Returns
    the number of rows deleted. ``keep_days<=0`` is a no-op (keep everything)."""
    if not keep_days or keep_days <= 0:
        return 0
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM briefings ORDER BY date DESC").fetchall()]
    if len(dates) <= keep_days:
        return 0
    cutoff = dates[keep_days - 1]  # keep dates >= cutoff
    cur = conn.execute("DELETE FROM briefings WHERE date < ?", (cutoff,))
    conn.commit()
    return cur.rowcount
