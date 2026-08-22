"""Recommendation journal — the forward-accruing record of what the model said.

Every analysis appends what the model read at that moment: composite, band,
percentile, both verdicts, which gates fired, and the artifact version that
produced it. Phase 6's nightly labeler fills in the realized forward excess
returns once each horizon matures, and the live-IC monitor reads the pair.

**This store cannot be backfilled.** A model's historical output is not
recoverable after the fact — the artifact, the universe cross-section and the
gates all move — so the only way to ever answer "is the live edge holding?" is
to have been writing this down since before the question was asked. That is why
it lands in Phase 1 and not in Phase 6 beside its readers: it costs almost
nothing to write and pays only in calendar time.

**One reading per (symbol, day), last write wins.** A symbol analyzed five times
in an afternoon must not cast five votes in the IC — that would silently
overweight whatever you happened to look at most, which is exactly the names you
were unsure about. The row is keyed on the pair and upserted.

Mirrors ``deepdive/iv_history``'s contract: idempotent schema, a ``sqlite3.Row``
factory so callers read by name, and a write path that NEVER raises into the
caller — ``analyze`` calls this for its side effect, and a journal failure must
never cost the user their analysis.
"""
import datetime as dt
import logging
import sqlite3
from pathlib import Path

from repo_paths import REC_JOURNAL_DB

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = REC_JOURNAL_DB

# ``fwd_*`` and ``labeled_at`` exist from the first row so Phase 6's labeler is
# an UPDATE rather than a migration of rows written without them.
SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    symbol            TEXT NOT NULL,
    reading_date      TEXT NOT NULL,
    recorded_at       TEXT,
    price             REAL,
    composite         REAL,
    band              INTEGER,
    percentile        INTEGER,
    swing_verdict     TEXT,
    position_verdict  TEXT,
    investor_verdict  TEXT,
    investor_score    INTEGER,
    gates             TEXT,
    model_version     TEXT,
    fwd_5d            REAL,
    fwd_10d           REAL,
    fwd_20d           REAL,
    labeled_at        TEXT,
    PRIMARY KEY (symbol, reading_date)
);
CREATE INDEX IF NOT EXISTS idx_readings_unlabeled
    ON readings (labeled_at, reading_date);
"""

_FIELDS = ("symbol", "reading_date", "recorded_at", "price", "composite",
           "band", "percentile", "swing_verdict", "position_verdict",
           "investor_verdict", "investor_score", "gates", "model_version")

# Phase 6. Phase 4 measured this model at cross-sectional IC +0.16 when the
# market rises and -0.11 when it falls — its edge IS beta. A live monitor
# scoring itself on the RAW forward excess would therefore report a healthy IC
# right through any rising market, reproducing exactly the illusion Phase 4
# dismantled. So each horizon also stores the BETA-ADJUSTED forward and the
# market's OWN forward, which is what lets the monitor split up-market from
# down-market instead of averaging them into a comfortable number.
#
# Added by migration rather than a new schema: this store cannot be backfilled
# (a model's historical output is not recoverable), so it must never be
# recreated to gain a column.
_LABEL_COLUMNS = (
    ("fwd_5d_ba", "REAL"), ("fwd_10d_ba", "REAL"), ("fwd_20d_ba", "REAL"),
    ("mkt_fwd_5d", "REAL"), ("mkt_fwd_10d", "REAL"), ("mkt_fwd_20d", "REAL"),
    ("beta", "REAL"),
)

_LABEL_FIELDS = ("fwd_5d", "fwd_10d", "fwd_20d",
                 "fwd_5d_ba", "fwd_10d_ba", "fwd_20d_ba",
                 "mkt_fwd_5d", "mkt_fwd_10d", "mkt_fwd_20d", "beta")


def _migrate(conn):
    """Add any label column this journal predates. Idempotent."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(readings)")}
    for name, decl in _LABEL_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE readings ADD COLUMN {name} {decl}")
    conn.commit()


def init_db(db_path=DEFAULT_DB_PATH):
    """Open the journal, creating the schema if needed. Idempotent."""
    db_path = Path(db_path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def close_db(conn):
    try:
        conn.close()
    except Exception:
        pass


def record(conn, reading):
    """Upsert one reading. Returns True on success, False on ANY failure.

    Never raises: the caller is ``analyze``, which owes the user an analysis
    whether or not the journal accepted the row."""
    try:
        row = {k: reading.get(k) for k in _FIELDS}
        if not row.get("symbol") or not row.get("reading_date"):
            return False
        row["recorded_at"] = row.get("recorded_at") or _now_iso()
        cols = ", ".join(_FIELDS)
        marks = ", ".join(f":{k}" for k in _FIELDS)
        # The label columns are deliberately NOT touched here: re-reading a
        # symbol later the same day must not wipe a label already applied.
        updates = ", ".join(f"{k}=excluded.{k}" for k in _FIELDS
                            if k not in ("symbol", "reading_date"))
        conn.execute(
            f"INSERT INTO readings ({cols}) VALUES ({marks}) "
            f"ON CONFLICT(symbol, reading_date) DO UPDATE SET {updates}", row)
        conn.commit()
        return True
    except Exception:
        logger.debug("rec_journal.record failed", exc_info=True)
        return False


def apply_label(conn, symbol, reading_date, **fields):
    """Attach realized forward returns to one reading (Phase 6).

    Accepts any of ``_LABEL_FIELDS``; anything omitted is written as NULL.
    ⚠ NULL and 0.0 are different answers — an unmatured horizon is UNKNOWN,
    while 0.0 is a measured flat outcome, and the monitor has to tell them
    apart. That is why absent fields are not defaulted to zero."""
    try:
        unknown = set(fields) - set(_LABEL_FIELDS)
        if unknown:
            raise ValueError(f"unknown label fields: {sorted(unknown)}")
        sets = ", ".join(f"{k}=?" for k in _LABEL_FIELDS)
        args = [fields.get(k) for k in _LABEL_FIELDS]
        conn.execute(
            f"UPDATE readings SET {sets}, labeled_at=? "
            f"WHERE symbol=? AND reading_date=?",
            (*args, _now_iso(), symbol, reading_date))
        conn.commit()
        return True
    except Exception:
        logger.debug("rec_journal.apply_label failed", exc_info=True)
        return False


def readings(conn, symbol=None, limit=None):
    """Readings newest-first; optionally for one symbol."""
    sql = "SELECT * FROM readings"
    args = []
    if symbol:
        sql += " WHERE symbol = ?"
        args.append(symbol)
    sql += " ORDER BY reading_date DESC, symbol ASC"
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    return conn.execute(sql, args).fetchall()


def unlabeled(conn, before_date=None):
    """Readings still awaiting a forward-return label, oldest first.

    ``before_date`` lets the labeler ask only for readings whose horizon has
    had time to mature."""
    sql = "SELECT * FROM readings WHERE labeled_at IS NULL"
    args = []
    if before_date:
        sql += " AND reading_date <= ?"
        args.append(before_date)
    sql += " ORDER BY reading_date ASC, symbol ASC"
    return conn.execute(sql, args).fetchall()


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()
