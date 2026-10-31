"""The Gamma Archive: a thinned copy of each closed session, kept forever.

``gex_history.db`` keeps 5 sessions (``purge_keep_sessions``), so a study that
needs months of strike-level history could never run and a deleted session could
not be recovered. This copies each closed session into ``gex_archive.db`` before
the purge reaches it. The tables have the same shape as the live store, so
``gex_history_db``'s readers and ``_decode_grid`` work on it unchanged.

Reads the source and writes the archive; no proxy, no Redis, no Claude. Run by
``tools/archive_gex.py``. Design: docs/plans/2026-10-31-gamma-archive-design.md
"""
from __future__ import annotations

import datetime as _dt
import math
import sqlite3
from pathlib import Path

import gex_history_db as gh
from repo_paths import GEX_ARCHIVE_TOML
from shared import market_calendar as mc
from shared.config_toml import toml_loader

DB_PATH = Path(__file__).parent / "gex_archive.db"

DEFAULTS = {
    "enabled": True,
    "grid_interval_min": 5,
    "strike_band": 0.15,
    "term_interval_min": 15,
}

load, reset_cache = toml_loader(GEX_ARCHIVE_TOML, DEFAULTS, label="gex_archive.toml")

# Every column of ``snapshots`` but the grid, which is cropped on the way across.
_SUMMARY_COLUMNS = (
    "symbol", "view", "ts", "spot", "flip", "top_pos_strike", "top_neg_strike",
    "net_total", "dte", "net_delta_0dte", "projected_net_delta_close",
    "hedge_pressure", "rr_25d", "call_vol", "put_vol", "call_prem", "put_prem",
    "atm_iv", "projected_flip", "carried_age_sec",
)

_TERM_COLUMNS = ("timestamp_ct", "symbol", "expiration_date", "strike",
                 "call_gex_usd", "put_gex_usd", "net_gex_usd", "underlying_price")

_ARCHIVED_SESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS archived_sessions (
    session_date      TEXT PRIMARY KEY,
    archived_at       TEXT NOT NULL,
    snapshot_rows     INTEGER NOT NULL,
    term_rows         INTEGER NOT NULL,
    grid_interval_min INTEGER NOT NULL,
    strike_band       REAL NOT NULL,
    term_interval_min INTEGER NOT NULL,
    code_version      TEXT
);
"""


def _setting(key, *, minimum, maximum):
    """A config number of the default's own type inside its range, or the
    default. A bool is refused: ``True`` is an int and would read as 1."""
    default = DEFAULTS[key]
    raw = load().get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    value = type(default)(raw)
    return value if minimum <= value <= maximum else default


def settings() -> dict:
    """The archive's settings, read at call time. ``enabled`` must be a real bool."""
    enabled = load().get("enabled")
    return {
        "enabled": enabled if isinstance(enabled, bool) else DEFAULTS["enabled"],
        "grid_interval_min": _setting("grid_interval_min", minimum=1, maximum=60),
        "strike_band": _setting("strike_band", minimum=0.01, maximum=1.0),
        "term_interval_min": _setting("term_interval_min", minimum=5, maximum=60),
    }


def connect_archive(path=None) -> sqlite3.Connection:
    """Open the archive for writing, creating its tables. ``path=None`` is the
    app's own store, resolved at call time."""
    conn = sqlite3.connect(str(DB_PATH if path is None else path))
    conn.executescript(gh._SCHEMA + gh.TERM_SCHEMA_SQL + _ARCHIVED_SESSIONS_SQL)
    return conn


def archived_dates(archive) -> set[str]:
    return {r[0] for r in archive.execute("SELECT session_date FROM archived_sessions")}


def closed_sessions(source, now) -> list[str]:
    """The source's session dates that can no longer change, oldest first: any day
    before ``now``'s, and ``now``'s own once collection has stopped."""
    _start, stop = mc.window_bounds("collection")
    today = now.date().isoformat()
    return sorted(d for d in gh._distinct_session_dates(source)
                  if d < today or (d == today and now.time() >= stop))


def pick_rows(rows, interval_sec) -> list[int]:
    """The ``ts`` to keep of a key's ``(ts, carried_age_sec)`` rows: each bucket's
    latest fetched row, or its latest carried row when it has no fetched one.

    A carried row holds modelled Greeks and repeated volume (see
    ``gex_history_db``). Half of a session's rows are carried, so sampling on the
    clock minute would archive mostly those."""
    best = {}
    for ts, carried in sorted(rows):
        bucket = ts // interval_sec
        held = best.get(bucket)
        if held is None or carried is None or held[1] is not None:
            best[bucket] = (ts, carried)
    return sorted(ts for ts, _carried in best.values())


def _usable_spot(spot) -> bool:
    return isinstance(spot, (int, float)) and math.isfinite(spot) and spot > 0


def crop_grid(grid: dict, spot, band: float) -> dict:
    """The strikes within ``band`` of ``spot``. No usable spot keeps the whole grid:
    wider beats wrong."""
    if not _usable_spot(spot):
        return grid
    low, high = spot * (1 - band), spot * (1 + band)
    return {strike: cell for strike, cell in grid.items() if low <= strike <= high}


def _source_columns(source) -> tuple:
    """``_SUMMARY_COLUMNS`` the source holds: an old backup lacks the newest."""
    held = {r[1] for r in source.execute("PRAGMA table_info(snapshots)")}
    return tuple(c for c in _SUMMARY_COLUMNS if c in held)


def _copy_snapshots(source, archive, day, cfg) -> int:
    start, end = gh._local_unix_range(_dt.date.fromisoformat(day))
    interval_sec = cfg["grid_interval_min"] * 60
    columns = _source_columns(source)
    carried = "carried_age_sec" if "carried_age_sec" in columns else "NULL"
    select =(f"SELECT {', '.join(columns)}, gex_json FROM snapshots "
              "WHERE symbol = ? AND view = ? AND ts = ?")
    insert = (f"INSERT OR REPLACE INTO snapshots ({', '.join(columns)}, gex_json) "
              f"VALUES ({', '.join('?' * (len(columns) + 1))})")
    spot_at = columns.index("spot")
    written = 0
    keys = source.execute("SELECT DISTINCT symbol, view FROM snapshots").fetchall()
    for symbol, view in keys:
        rows = source.execute(
            f"SELECT ts, {carried} FROM snapshots "
            "WHERE symbol = ? AND view = ? AND ts >= ? AND ts < ?",
            (symbol, view, start, end)).fetchall()
        for ts in pick_rows(rows, interval_sec):
            # One read per kept row: the PK reaches each directly, and the grids of
            # the rows left behind are never loaded.
            row = source.execute(select, (symbol, view, ts)).fetchone()
            grid = crop_grid(gh._decode_grid(row[-1]), row[spot_at], cfg["strike_band"])
            archive.execute(insert, (*row[:-1], gh._encode_grid(grid)))
            written += 1
    return written


def _copy_term(source, archive, day, cfg) -> int:
    stamps = [r[0] for r in source.execute(
        "SELECT DISTINCT timestamp_ct FROM gex_term_snapshots "
        "WHERE substr(timestamp_ct, 1, 10) = ? ORDER BY timestamp_ct", (day,))]
    insert = (f"INSERT OR REPLACE INTO gex_term_snapshots ({', '.join(_TERM_COLUMNS)}) "
              f"VALUES ({', '.join('?' * len(_TERM_COLUMNS))})")
    strike_at, spot_at = _TERM_COLUMNS.index("strike"), _TERM_COLUMNS.index("underlying_price")
    band = cfg["strike_band"]
    seen, written = set(), 0
    for stamp in stamps:
        at = _dt.datetime.fromisoformat(stamp)
        bucket = (at.hour * 60 + at.minute) // cfg["term_interval_min"]
        if bucket in seen:
            continue
        seen.add(bucket)
        for row in source.execute(
                f"SELECT {', '.join(_TERM_COLUMNS)} FROM gex_term_snapshots "
                "WHERE timestamp_ct = ?", (stamp,)):
            spot = row[spot_at]
            if _usable_spot(spot) and not spot * (1 - band) <= row[strike_at] <= spot * (1 + band):
                continue
            archive.execute(insert, row)
            written += 1
    return written


def archive_session(source, archive, day, cfg, code_version=None) -> tuple[int, int]:
    """Copy one session in ONE transaction with its marker, so a killed run leaves
    nothing half archived and the next run redoes it. Returns the rows written."""
    with archive:
        snapshot_rows = _copy_snapshots(source, archive, day, cfg)
        term_rows = _copy_term(source, archive, day, cfg)
        archive.execute(
            "INSERT INTO archived_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (day, _dt.datetime.now().isoformat(timespec="seconds"), snapshot_rows,
             term_rows, cfg["grid_interval_min"], cfg["strike_band"],
             cfg["term_interval_min"], code_version))
    return snapshot_rows, term_rows


def archive_new_sessions(source, archive, now, code_version=None) -> list[tuple]:
    """Archive every closed session the archive does not hold, oldest first.
    Returns ``(date, snapshot_rows, term_rows)`` for each. A failure raises: the
    tool exits non-zero, the unit ends FAILED, and that sends the push."""
    cfg = settings()
    if not cfg["enabled"]:
        return []
    held = archived_dates(archive)
    done = []
    for day in closed_sessions(source, now):
        if day not in held:
            done.append((day, *archive_session(source, archive, day, cfg, code_version)))
    return done
