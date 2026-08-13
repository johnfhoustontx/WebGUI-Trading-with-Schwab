"""SQLite persistence for intraday GEX/Charm snapshots."""
from __future__ import annotations

import datetime as _dt
import json
import logging
import struct
import zlib
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Tuple

import numpy as np

try:  # orjson rides in with nicegui; ~2x faster on the LEGACY json grid path.
    import orjson as _fastjson

    def _json_loads(b):
        return _fastjson.loads(b)
except ImportError:  # pragma: no cover - stdlib fallback, same semantics
    def _json_loads(b):
        return json.loads(b)

log = logging.getLogger("scanner")

DB_PATH = Path(__file__).parent / "gex_history.db"

# Memory-map the DB rather than read()-ing page by page. A single
# (symbol, view, session) read touches ~437 SCATTERED pages — the collector
# writes every symbol each minute, so consecutive rows of one key land ~360
# rowids apart and essentially every row sits on its own page. mmap drops a
# syscall + buffer copy per page (measured 4.5 -> 3.0 ms median warm; the win
# is larger on cold random reads). SQLite silently caps this at its
# compile-time SQLITE_MAX_MMAP_SIZE, so treat it as a request, not a promise.
_MMAP_BYTES = 1 << 30  # 1 GiB

# Max free pages returned to the filesystem per purge (see purge_keep_sessions).
# 20k pages x 4 KiB ~= 80 MB per daily call: enough to walk the ~797 MB backlog
# down over a couple of weeks while keeping each lock short.
_VACUUM_MAX_PAGES = 20000


def _local_unix_range(d=None) -> tuple[int, int]:
    """``[start, end)`` unix seconds spanning the LOCAL calendar day ``d``
    (default: today).

    Lets day-filters use a sargable ``ts >= ? AND ts < ?`` range (the index on
    ``ts`` applies) instead of ``DATE(ts,'unixepoch','localtime') = DATE('now')``,
    which wraps ``ts`` in a function and so can't use the index. Passing an
    explicit ``d`` lets callers load a PRIOR session's rows (gamma persistence:
    show Friday's heatmap over the weekend).

    NOTE: uses the current fixed local UTC offset; on the two DST-transition days
    the [start,end) edge can differ by an hour from SQLite's DST-aware
    ``DATE(...,'localtime')``. Immaterial here — GEX snapshots only exist 08:30–
    15:20 CT, never near the 02:00/midnight boundary, so no row is ever classified
    differently."""
    now = _dt.datetime.now().astimezone()
    base = now if d is None else _dt.datetime(d.year, d.month, d.day, tzinfo=now.tzinfo)
    start = base.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = (start + _dt.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp()), int(tomorrow.timestamp())


def _today_local_unix_range() -> tuple[int, int]:
    """``[start, end)`` unix seconds for the current local calendar day."""
    return _local_unix_range(None)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    symbol          TEXT    NOT NULL,
    view            TEXT    NOT NULL,
    ts              INTEGER NOT NULL,
    spot            REAL,
    flip            REAL,
    top_pos_strike  REAL,
    top_neg_strike  REAL,
    net_total       REAL,
    dte             INTEGER,
    gex_json        TEXT,
    net_delta_0dte            REAL,
    projected_net_delta_close REAL,
    hedge_pressure            REAL,
    rr_25d                    REAL,
    call_vol                  INTEGER,
    put_vol                   INTEGER,
    call_prem                 REAL,
    put_prem                  REAL,
    atm_iv                    REAL,
    PRIMARY KEY (symbol, view, ts)
);
"""
# NOTE: the old ``idx_snap_today ON snapshots(symbol, view, ts)`` was DROPPED
# (2026-07-18) — it exactly duplicated the PRIMARY KEY autoindex, so every insert
# maintained two identical b-trees. init_schema drops it from pre-existing DBs.


TERM_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS gex_term_snapshots (
    timestamp_ct      TEXT    NOT NULL,
    symbol            TEXT    NOT NULL,
    expiration_date   TEXT    NOT NULL,
    strike            REAL    NOT NULL,
    call_gex_usd      REAL    NOT NULL,
    put_gex_usd       REAL    NOT NULL,
    net_gex_usd       REAL    NOT NULL,
    underlying_price  REAL    NOT NULL,
    PRIMARY KEY (timestamp_ct, symbol, expiration_date, strike)
);
-- PK already covers (timestamp_ct, ...) lookups; only the date-prefix
-- index is needed. The substr() expression must match the WHERE clause
-- in list_term_timestamps_for_date for SQLite to use this index.
CREATE INDEX IF NOT EXISTS idx_term_date ON gex_term_snapshots (substr(timestamp_ct, 1, 10));
"""


_GRID_SIG_FIGS = 6


def _round_sig(value, sig: int = _GRID_SIG_FIGS):
    """Round a float to ``sig`` significant figures; pass everything else through.

    Recurses into dicts/lists so nested grid cells are covered. bools are left
    alone (``isinstance(True, int)`` is True, and they are not measurements)."""
    if isinstance(value, float):
        if value == 0.0 or value != value or value in (float("inf"), float("-inf")):
            return value
        return float(f"%.{sig}g" % value)
    if isinstance(value, dict):
        return {k: _round_sig(v, sig) for k, v in value.items()}
    if isinstance(value, list):
        return [_round_sig(v, sig) for v in value]
    return value


_GRID_MAGIC = b"G1"          # columnar float32 payload (zlib streams start 0x78)
_GRID_FIELDS = ("call", "put", "net")


def _pack_columnar(grid) -> bytes | None:
    """Pack a production-shaped grid into a columnar float32 blob, else ``None``.

    Layout: ``b"G1"`` + zlib(``<I`` count + n float32 strikes + n*3 float32
    ``call, put, net``). Strikes are sorted, which also compresses better.

    SHAPE-GATED on purpose. Grids are documented to carry ints/strings/None/
    nested dicts (see ``test_encode_grid_handles_non_float_and_nested_values``),
    which this layout cannot express — anything but three plain numbers per cell
    returns ``None`` so the caller falls back to JSON. Measured across 1,500
    random live snapshots, 100% of real cells are exactly this shape, so the
    fallback costs nothing in production.

    ``bool`` is rejected explicitly: it is an ``int`` subclass and would
    otherwise be silently flattened to 1.0/0.0.
    """
    rows = []
    for key, cell in grid.items():
        if cell.__class__ is not dict or len(cell) != len(_GRID_FIELDS):
            return None
        values = []
        for field in _GRID_FIELDS:
            value = cell.get(field)
            if value.__class__ is not float and value.__class__ is not int:
                return None  # bool / str / None / nested / missing field
            values.append(value)
        try:
            rows.append((float(key), values))
        except (TypeError, ValueError):
            return None  # non-numeric strike key
    if not rows:
        return None
    rows.sort(key=lambda r: r[0])
    strikes = np.fromiter((r[0] for r in rows), np.float32, len(rows))
    exact = np.array([r[1] for r in rows], dtype=np.float64)
    with np.errstate(over="ignore"):
        values = exact.astype(np.float32)
    # A finite value that overflows to inf would be silent corruption in a
    # money-adjacent store, so degrade to the lossless JSON path instead. Real
    # GEX tops out ~4.6e13 against float32's 3.4e38, so this should never fire.
    # UNDERflow is deliberate and NOT guarded: sub-1.18e-38 denormals (deep-OTM
    # BS gamma/vanna noise) flush to 0.0, which is the physically correct value.
    if not np.array_equal(np.isfinite(exact), np.isfinite(values)):
        return None
    payload = struct.pack("<I", len(rows)) + strikes.tobytes() + values.tobytes()
    return _GRID_MAGIC + zlib.compress(payload, 1)


def _unpack_columnar(raw) -> dict:
    """Decode a ``_pack_columnar`` blob back to ``{strike: {call, put, net}}``.

    Returns plain Python floats (via ``.tolist()``), NOT numpy scalars — the
    decoded grid is JSON-serialized into ``cache:options:gamma``, and numpy
    scalars are not JSON-serializable.
    """
    buf = zlib.decompress(bytes(raw)[len(_GRID_MAGIC):])
    (count,) = struct.unpack_from("<I", buf, 0)
    offset = struct.calcsize("<I")
    strikes = np.frombuffer(buf, np.float32, count, offset).tolist()
    values = np.frombuffer(buf, np.float32, count * 3, offset + 4 * count)
    call, put, net = (values.reshape(count, 3).T).tolist()
    return {
        k: {"call": c, "put": p, "net": n}
        for k, c, p, n in zip(strikes, call, put, net)
    }


def _encode_grid(grid) -> bytes | None:
    """Serialize a per-strike grid dict → bytes for the ``gex_json`` column, or
    ``None`` for an empty grid. The full chain grid dominates on-disk size
    (×4 views × ~440 rows/day), so this is the hottest storage path in the app.

    TWO formats, tried in order:

    1. **Columnar float32** (``_pack_columnar``) — the production shape. Measured
       on the live DB (2026-08-08) against the JSON path below: decode 2.5×
       faster, encode 2.9×, blobs 1.38× smaller. ``json.loads`` had been **68% of
       the whole read path** while SQLite itself was only 4%.
    2. **zlib-compressed JSON** — the fallback for grids carrying
       ints/strings/None/nested cells, which the columnar layout cannot express.
       Floats are rounded to ``_GRID_SIG_FIGS`` significant figures FIRST:
       measured 2026-07-25, grid size is driven by float ENTROPY rather than
       strike count (values serialize as e.g. ``1.2345678901234e-05``), and
       rounding cut the payload ~52% (966 MB → 459 MB).

    FORWARD-ONLY: existing rows are untouched and still decode (see
    ``_decode_grid``, which reads all three historical formats). Flip/wall values
    are stored in their own columns, computed from the FULL chain, so they are
    unaffected by grid rounding OR float32 conversion.
    """
    if not grid:
        return None
    packed = _pack_columnar(grid)
    if packed is not None:
        return packed
    return zlib.compress(json.dumps(_round_sig(grid)).encode("utf-8"))


def _decode_grid(raw) -> dict:
    """Decode a stored ``gex_json`` value → per-strike dict with FLOAT keys.

    Format-agnostic for back-compat — three formats, newest first:
    ``b"G1"…`` = columnar float32 (current), other ``bytes`` = zlib-compressed
    JSON, ``str`` = plain JSON (pre-compression rows). None/empty → {}."""
    if not raw:
        return {}
    if isinstance(raw, (bytes, bytearray)):
        if bytes(raw[:len(_GRID_MAGIC)]) == _GRID_MAGIC:
            return _unpack_columnar(raw)
        raw = zlib.decompress(raw)
    return {float(k): v for k, v in _json_loads(raw).items()}


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation."""
    conn.executescript(_SCHEMA)
    # Drop the redundant duplicate-of-PK index from any pre-existing DB.
    conn.execute("DROP INDEX IF EXISTS idx_snap_today")
    # Backfill columns on pre-existing DBs (SQLite lacks IF NOT EXISTS for columns).
    existing = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
    for col, col_type in (
        ("net_delta_0dte", "REAL"),
        ("projected_net_delta_close", "REAL"),
        ("hedge_pressure", "REAL"),
        ("rr_25d", "REAL"),
        ("call_vol", "INTEGER"),
        ("put_vol", "INTEGER"),
        ("call_prem", "REAL"),
        ("put_prem", "REAL"),
        ("atm_iv", "REAL"),
        ("projected_flip", "REAL"),
    ):
        if col not in existing:
            conn.execute(f"ALTER TABLE snapshots ADD COLUMN {col} {col_type}")
    conn.commit()
    init_term_schema(conn)


def init_term_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation for the term-structure snapshots table."""
    conn.executescript(TERM_SCHEMA_SQL)
    conn.commit()


def insert_term_snapshot_rows(
    conn: sqlite3.Connection,
    rows: Iterable[Tuple[str, str, str, float, float, float, float, float]],
) -> None:
    """rows: iterable of 8-tuples
        (timestamp_ct, symbol, expiration_date, strike,
         call_gex_usd, put_gex_usd, net_gex_usd, underlying_price)."""
    conn.executemany(
        "INSERT OR REPLACE INTO gex_term_snapshots "
        "(timestamp_ct, symbol, expiration_date, strike, call_gex_usd, "
        " put_gex_usd, net_gex_usd, underlying_price) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def load_term_snapshot(
    conn: sqlite3.Connection,
    timestamp_ct: str,
    symbol: str,
) -> list[dict]:
    """Return rows for a given (timestamp_ct, symbol) as dicts ordered by
    expiration_date then strike."""
    cur = conn.execute(
        "SELECT expiration_date, strike, call_gex_usd, put_gex_usd, "
        "       net_gex_usd, underlying_price "
        "FROM gex_term_snapshots "
        "WHERE timestamp_ct = ? AND symbol = ? "
        "ORDER BY expiration_date, strike",
        (timestamp_ct, symbol),
    )
    return [
        {"expiration_date": e, "strike": s, "call_gex_usd": c,
         "put_gex_usd": p, "net_gex_usd": n, "underlying_price": u}
        for (e, s, c, p, n, u) in cur.fetchall()
    ]


def list_term_timestamps_for_date(
    conn: sqlite3.Connection,
    date_str: str,
    symbol: str,
) -> list[str]:
    """Return distinct timestamp_ct values for the given local date prefix
    (YYYY-MM-DD) and symbol, ordered ascending."""
    cur = conn.execute(
        "SELECT DISTINCT timestamp_ct FROM gex_term_snapshots "
        "WHERE substr(timestamp_ct, 1, 10) = ? AND symbol = ? "
        "ORDER BY timestamp_ct",
        (date_str, symbol),
    )
    return [r[0] for r in cur.fetchall()]


def connect(read_only: bool = False) -> sqlite3.Connection:
    """Open the snapshots DB. read_only=True uses SQLite URI mode=ro."""
    if read_only:
        uri = f"file:{DB_PATH.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        conn.execute(f"PRAGMA mmap_size={_MMAP_BYTES}")
        return conn
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA mmap_size={_MMAP_BYTES}")
    return conn


def insert_snapshot(
    conn: sqlite3.Connection,
    symbol: str,
    view: str,
    summary: dict,
    gex_grid: dict,
    dte: int,
) -> None:
    """Write one snapshot row. INSERT OR REPLACE keeps re-runs idempotent."""
    conn.execute(
        """
        INSERT OR REPLACE INTO snapshots
            (symbol, view, ts, spot, flip, top_pos_strike,
             top_neg_strike, net_total, dte, gex_json,
             net_delta_0dte, projected_net_delta_close, hedge_pressure,
             rr_25d, call_vol, put_vol, call_prem, put_prem, atm_iv,
             projected_flip)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            view,
            int(summary.get("ts") or time.time()),
            summary.get("spot"),
            summary.get("flip"),
            summary.get("top_pos_strike"),
            summary.get("top_neg_strike"),
            summary.get("net_total"),
            dte,
            _encode_grid(gex_grid),
            summary.get("net_delta_0dte"),
            summary.get("projected_net_delta_close"),
            summary.get("hedge_pressure"),
            summary.get("rr_25d"),
            summary.get("call_vol"),
            summary.get("put_vol"),
            summary.get("call_prem"),
            summary.get("put_prem"),
            summary.get("atm_iv"),
            summary.get("projected_flip"),
        ),
    )


def load_today(
    conn: sqlite3.Connection,
    symbol: str,
    view: str,
) -> list[tuple]:
    """Return today's snapshots for (symbol, view), ordered by ts ascending.

    Rows: (ts, spot, flip, top_pos_strike, top_neg_strike, net_total).
    """
    start, end = _today_local_unix_range()
    cur = conn.execute(
        """
        SELECT ts, spot, flip, top_pos_strike, top_neg_strike, net_total
          FROM snapshots
         WHERE symbol = ?
           AND view   = ?
           AND ts >= ? AND ts < ?
         ORDER BY ts ASC
        """,
        (symbol, view, start, end),
    )
    return cur.fetchall()


def load_date_with_grid(
    conn: sqlite3.Connection,
    symbol: str,
    view: str,
    date=None,
    since_ts: int | None = None,
) -> list[tuple]:
    """Like load_today_with_grid but for an explicit local calendar ``date``
    (default: today).

    Used by the Gamma persistence path to load the LAST trading session's rows
    (e.g. Friday's) so the heatmap stays populated after close / over a weekend,
    then clears once a new trading day (with no rows yet) becomes the active date.

    ``since_ts`` — when given, return only rows STRICTLY NEWER (``ts > since_ts``,
    still date-bounded, sargable on the PK). The gamma-snapshot memo uses this to
    append the last minute's rows instead of re-decoding the whole session's
    grids every refresh.

    Returns list of (ts, spot, flip, top_pos_strike, top_neg_strike,
                     net_total, gex_grid) tuples. gex_grid is the decoded
    JSON dict or {} if NULL.
    """
    start, end = _local_unix_range(date)
    if since_ts is not None:
        start = max(start, int(since_ts) + 1)
    cur = conn.execute(
        """
        SELECT ts, spot, flip, top_pos_strike, top_neg_strike, net_total, gex_json
          FROM snapshots
         WHERE symbol = ?
           AND view   = ?
           AND ts >= ? AND ts < ?
         ORDER BY ts ASC
        """,
        (symbol, view, start, end),
    )
    out = []
    for row in cur.fetchall():
        out.append((*row[:6], _decode_grid(row[6])))
    return out


def load_today_with_grid(
    conn: sqlite3.Connection,
    symbol: str,
    view: str,
) -> list[tuple]:
    """Today's per-strike rows + grid dict (thin wrapper over load_date_with_grid)."""
    return load_date_with_grid(conn, symbol, view, None)


def latest_flip(
    conn: sqlite3.Connection,
    symbol: str,
    view: str = "gex",
    date=None,
) -> float | None:
    """The most-recent gamma-flip strike for (symbol, view) on LOCAL ``date``
    (default today), or ``None`` when there is no row.

    Selects ONLY the ``flip`` column — never ``gex_json`` — so a caller that
    needs just the latest flip (e.g. the options matrix) avoids decoding the whole
    session's grids the way ``load_date_with_grid`` does. Uses the same sargable
    ``ts >= ? AND ts < ?`` date range as its siblings.
    """
    start, end = _local_unix_range(date)
    cur = conn.execute(
        """
        SELECT flip
          FROM snapshots
         WHERE symbol = ?
           AND view   = ?
           AND ts >= ? AND ts < ?
         ORDER BY ts DESC
         LIMIT 1
        """,
        (symbol, view, start, end),
    )
    row = cur.fetchone()
    return row[0] if row else None


def latest_spot_flip(
    conn: sqlite3.Connection,
    symbol: str,
    view: str = "gex",
    date=None,
) -> tuple | None:
    """The most-recent ``(ts, spot, flip)`` for (symbol, view) on LOCAL ``date``
    (default today), or ``None`` when there is no row.

    Reads spot + flip together so the gamma-flip alert can classify the dealer
    gamma regime (spot vs flip) from one query. Selects only the three summary
    columns — never ``gex_json`` — using the same sargable ts range as its
    siblings.
    """
    start, end = _local_unix_range(date)
    cur = conn.execute(
        """
        SELECT ts, spot, flip
          FROM snapshots
         WHERE symbol = ?
           AND view   = ?
           AND ts >= ? AND ts < ?
         ORDER BY ts DESC
         LIMIT 1
        """,
        (symbol, view, start, end),
    )
    row = cur.fetchone()
    return (row[0], row[1], row[2]) if row else None


def latest_skew_by_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    view: str = "gex",
) -> list[tuple]:
    """Return the two most-recent skew rows for (symbol, view), most-recent first.

    Rows: (ts, rr_25d, call_vol, put_vol). Used by the flow-skew publish step to
    compute the CHANGE in the 25-delta risk-reversal since the prior snapshot
    (LIMIT 2, ORDER BY ts DESC). Returns [] when there are no rows.
    """
    cur = conn.execute(
        """
        SELECT ts, rr_25d, call_vol, put_vol
          FROM snapshots
         WHERE symbol = ?
           AND view   = ?
         ORDER BY ts DESC
         LIMIT 2
        """,
        (symbol, view),
    )
    return cur.fetchall()


def load_hedge_series(
    conn: sqlite3.Connection,
    symbol: str,
    d=None,
) -> list[tuple]:
    """0-DTE hedge-pressure series for one symbol on LOCAL date ``d`` (default today).

    One row per snapshot from the ``dex`` view, chronological:
    ``(ts, hedge_pressure, net_delta_0dte, projected_flip)``.

    Rows with a NULL ``hedge_pressure`` are SKIPPED rather than returned as zero —
    the column is only populated while a symbol's nearest expiry is TODAY, so most
    symbols and most off-session rows have none, and a zero would read as "no drift"
    instead of "no 0-DTE book". ``projected_flip`` is forward-only (the column was
    added 2026-07-28), so older rows carry None there while still reporting pressure.

    Uses the sargable ``ts >= ? AND ts < ?`` range so the ``ts`` index applies.
    """
    start, end = _local_unix_range(d)
    cur = conn.execute(
        """
        SELECT ts, hedge_pressure, net_delta_0dte, projected_flip
          FROM snapshots
         WHERE symbol = ? AND view = 'dex'
           AND ts >= ? AND ts < ?
           AND hedge_pressure IS NOT NULL
         ORDER BY ts ASC
        """,
        (symbol, start, end),
    )
    return [tuple(r) for r in cur.fetchall()]


def load_flow_series(
    conn: sqlite3.Connection,
    symbol: str,
    d=None,
) -> list[tuple]:
    """Intraday options-flow series for one symbol on LOCAL date ``d`` (default today).

    One row per 2-min snapshot from the ``gex`` view, chronological:
    ``(ts, spot, call_vol, put_vol, call_prem, put_prem)`` — the underlying price plus
    the daily-cumulative call/put volume (contracts) + premium (dollars). Feeds the
    intraday premium-flow chart; the frontend derives per-window flow + net from the
    cumulative series. Uses the sargable ``ts >= ? AND ts < ?`` range so the ``ts``
    index applies. Passing an explicit ``d`` loads a prior session.
    """
    start, end = _local_unix_range(d)
    cur = conn.execute(
        """
        SELECT ts, spot, call_vol, put_vol, call_prem, put_prem
          FROM snapshots
         WHERE symbol = ?
           AND view   = 'gex'
           AND ts >= ? AND ts < ?
         ORDER BY ts
        """,
        (symbol, start, end),
    )
    return cur.fetchall()


def load_atm_iv_series(
    conn: sqlite3.Connection,
    symbol: str,
    d=None,
) -> list[tuple]:
    """Intraday ATM-IV LEVEL series for one symbol on LOCAL date ``d`` (default
    today), from the ``gex`` view, chronological: ``(ts, atm_iv)`` per snapshot.

    Feeds ``matrix.iv_regime`` (the IV-direction axis: collapsing vs spiking).
    ``atm_iv`` is forward-only — NULL on rows written before the column existed,
    so early/legacy rows come back as ``(ts, None)``. Uses the sargable
    ``ts >= ? AND ts < ?`` range so the ``ts`` index applies."""
    start, end = _local_unix_range(d)
    cur = conn.execute(
        """
        SELECT ts, atm_iv
          FROM snapshots
         WHERE symbol = ?
           AND view   = 'gex'
           AND ts >= ? AND ts < ?
         ORDER BY ts
        """,
        (symbol, start, end),
    )
    return cur.fetchall()


def load_flow_tail(
    conn: sqlite3.Connection,
    symbol: str,
    limit: int,
    d=None,
) -> list[tuple]:
    """The last ``limit`` flow rows for ``symbol`` on local date ``d``, ASC.

    Same shape as :func:`load_flow_series` but bounded — the flow-alert detectors
    need only the trailing ~22 rows (crossover: 2; spike: window+2), not the whole
    day (~440 rows/symbol by the close × the whole universe every minute). Uses
    ``ORDER BY ts DESC LIMIT n`` on the PK then reverses to chronological."""
    start, end = _local_unix_range(d)
    cur = conn.execute(
        """
        SELECT ts, spot, call_vol, put_vol, call_prem, put_prem
          FROM snapshots
         WHERE symbol = ?
           AND view   = 'gex'
           AND ts >= ? AND ts < ?
         ORDER BY ts DESC
         LIMIT ?
        """,
        (symbol, start, end, int(limit)),
    )
    return list(reversed(cur.fetchall()))


def purge_old(conn: sqlite3.Connection) -> int:
    """Delete snapshots older than today (local date). Returns rows deleted.

    Legacy standalone-collector retention (keep today only). The live
    options-service path uses :func:`purge_keep_sessions` instead so the
    off-hours-persistence feature (show the last session until the next trading
    day) keeps at least the last N distinct session-dates.
    """
    cur = conn.execute(
        """
        DELETE FROM snapshots
         WHERE DATE(ts, 'unixepoch', 'localtime') < DATE('now', 'localtime')
        """
    )
    conn.commit()
    return cur.rowcount


def _distinct_session_dates(conn: sqlite3.Connection) -> list[str]:
    """Distinct local session-dates (YYYY-MM-DD) present in ``snapshots``,
    newest first.

    Derived from the ``snapshots.ts`` epoch column via
    ``DATE(ts,'unixepoch','localtime')`` (only for the small DISTINCT scan — the
    per-row DELETE below uses a sargable ``ts <`` range so the index applies)."""
    cur = conn.execute(
        """
        SELECT DISTINCT DATE(ts, 'unixepoch', 'localtime') AS d
          FROM snapshots
         ORDER BY d DESC
        """
    )
    return [r[0] for r in cur.fetchall() if r[0] is not None]


def purge_keep_sessions(conn: sqlite3.Connection, keep_sessions: int = 5) -> int:
    """Keep the last ``keep_sessions`` distinct session-dates; delete everything
    older, across BOTH ``snapshots`` AND ``gex_term_snapshots``. Returns the total
    rows deleted (both tables).

    This is the retention used by the always-on options-service collection path.
    It preserves the Gamma page's off-hours persistence: the page shows the last
    session's candles/heatmap until the next trading day, so we must never drop
    the most recent session(s). Keeping the last N (default 5) distinct
    session-dates covers weekends/holidays comfortably.

    Retention alone (DELETE) hands pages to the freelist for reuse but does NOT
    shrink the file on disk. When the DB was created with
    ``auto_vacuum=INCREMENTAL`` we therefore follow the DELETE with a BOUNDED
    ``PRAGMA incremental_vacuum(_VACUUM_MAX_PAGES)``, which returns a capped
    number of free pages to the filesystem per call and releases its lock
    promptly — so the file shrinks gradually over successive daily purges instead
    of growing forever.

    This was added 2026-07-25: the live DB had reached 2.2 GB for 5 sessions of
    data, of which 194,623 pages (~797 MB, 36%) were free. ``auto_vacuum`` was
    already INCREMENTAL, but nothing in the repo had ever called
    ``incremental_vacuum``, so those pages were never returned.

    Never auto-run a full ``VACUUM`` here — on the multi-GB file mid-collection it
    would lock for minutes and risk collection failures. That remains a manual,
    offline operation (``tools/vacuum_gex.py``, which refuses during market hours).
    On a DB created without incremental auto-vacuum the pragma is a documented
    no-op, so this is safe either way.
    """
    if keep_sessions < 1:
        keep_sessions = 1
    dates = _distinct_session_dates(conn)
    if len(dates) <= keep_sessions:
        return 0  # nothing older than the retention window
    # The oldest date to KEEP is dates[keep_sessions - 1]; delete strictly before
    # its local-day start (sargable ts range → the ts index applies).
    oldest_keep = dates[keep_sessions - 1]
    y, m, d = (int(x) for x in oldest_keep.split("-"))
    cutoff, _ = _local_unix_range(_dt.date(y, m, d))  # [start, ...) of oldest kept day

    deleted = 0
    cur = conn.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
    deleted += cur.rowcount or 0

    # gex_term_snapshots keys its time as a 'YYYY-MM-DD ...' text prefix; keep the
    # SAME distinct session-dates. Term rows are SPX-only, but a lexicographic
    # date-prefix compare (substr 1..10) is exact for the ISO date format and
    # matches the idx_term_date index expression.
    cur = conn.execute(
        "DELETE FROM gex_term_snapshots WHERE substr(timestamp_ct, 1, 10) < ?",
        (oldest_keep,),
    )
    deleted += cur.rowcount or 0

    conn.commit()

    if deleted:
        # Bounded reclaim — returns at most _VACUUM_MAX_PAGES free pages to the
        # filesystem and releases the lock promptly. No-op when the DB was not
        # created with auto_vacuum=INCREMENTAL. Best-effort: a busy/locked DB must
        # never turn a successful purge into a failure.
        try:
            conn.execute(f"PRAGMA incremental_vacuum({_VACUUM_MAX_PAGES})")
            conn.commit()
        except sqlite3.Error:
            log.debug("purge_keep_sessions: incremental_vacuum skipped", exc_info=True)

    return deleted


def last_snapshot_age(
    conn: sqlite3.Connection,
    symbol: str,
    view: str,
) -> tuple[int | None, int | None]:
    """Return (age_seconds, last_ts) for today's latest snapshot, or (None, None).

    Sargable ``ts >= ? AND ts < ?`` day-range (mirrors ``load_today``) so the
    ``ts`` index applies, instead of wrapping ``ts`` in ``DATE(...)``."""
    start, end = _today_local_unix_range()
    cur = conn.execute(
        """
        SELECT MAX(ts) FROM snapshots
         WHERE symbol = ? AND view = ?
           AND ts >= ? AND ts < ?
        """,
        (symbol, view, start, end),
    )
    row = cur.fetchone()
    if not row or row[0] is None:
        return None, None
    last_ts = int(row[0])
    return int(time.time()) - last_ts, last_ts


def first_snapshot_today(
    conn: sqlite3.Connection,
    symbol: str,
    view: str,
) -> dict:
    """Return the gex_grid dict of today's earliest snapshot, or {} if none.

    Used as the "vs Open" baseline for ΔDEX ghost bars.

    Sargable ``ts >= ? AND ts < ?`` day-range (mirrors ``load_today``) so the
    ``ts`` index applies, instead of wrapping ``ts`` in ``DATE(...)``.
    """
    start, end = _today_local_unix_range()
    cur = conn.execute(
        """
        SELECT gex_json FROM snapshots
         WHERE symbol = ? AND view = ?
           AND ts >= ? AND ts < ?
         ORDER BY ts ASC
         LIMIT 1
        """,
        (symbol, view, start, end),
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return {}
    return _decode_grid(row[0])
