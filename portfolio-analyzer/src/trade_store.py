"""Persistent, dedupe-merging trade store for the portfolio analyzer.

Trades (CSV-bootstrapped or proxy-synced) share an 8-key contract; the stable
``trade_id`` is the dedup key. This module persists the trade history to disk
and merges new trades in without duplicates, tracking the last sync date.

Store format (JSON)::

    {"last_sync": "YYYY-MM-DD" | null, "trades": [<trade row dicts>]}

Pure logic + filesystem only — no network.
"""
from __future__ import annotations

import json
import time
import os
import logging
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
from repo_paths import PORTFOLIO_ANALYZER

# Default location for the persisted trade store.
ENTRIES_PATH = PORTFOLIO_ANALYZER / "data" / "entries.json"

_EMPTY_STORE = {"last_sync": None, "trades": []}


def last_trade_date(trades: list[dict]) -> str | None:
    """Return the max ``trade_date`` across trades, or None if empty.

    ISO ``YYYY-MM-DD`` strings sort lexically the same as chronologically, so a
    plain string ``max`` is correct here.
    """
    dates = [t["trade_date"] for t in trades if t.get("trade_date")]
    return max(dates) if dates else None


def merge_trades(existing: list[dict], new: list[dict]) -> list[dict]:
    """Dedupe by ``trade_id``: existing wins, genuinely-new trades appended.

    Order is stable: existing trades keep their order, then new trades whose
    ``trade_id`` is not already present are appended in their given order. A new
    trade sharing a ``trade_id`` with one already seen does NOT overwrite or
    duplicate it (first occurrence wins).
    """
    merged: list[dict] = list(existing)
    seen = {t["trade_id"] for t in merged}
    for trade in new:
        tid = trade["trade_id"]
        if tid in seen:
            continue
        merged.append(trade)
        seen.add(tid)
    return merged


log = logging.getLogger(__name__)


def _write_whole(path, text: str) -> None:
    """Write ``text`` to ``path`` all-or-nothing: a temp file beside it, then one
    rename. A plain write that is interrupted leaves a truncated file, and this
    is the only copy (audit AR-09)."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _set_aside(path) -> None:
    """Move an unreadable file to ``<name>.corrupt-<time>`` so the next save
    cannot overwrite what is left of it. Never raises."""
    p = pathlib.Path(path)
    try:
        dest = p.with_name(f"{p.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
        os.replace(p, dest)
        log.error("%s could not be read; it was moved to %s and a new one will "
                  "be started", p.name, dest.name)
    except OSError:
        log.exception("could not set aside the unreadable %s", p.name)


def load_store(path) -> dict:
    """Read the JSON store at ``path``.

    Returns the empty store if the file does not exist or cannot be used. A
    CORRUPT file is moved aside (``<name>.corrupt-<time>``) before the empty
    store is returned, so it is never overwritten.
    """
    p = pathlib.Path(path)
    if not p.exists():
        return dict(_EMPTY_STORE)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError:
        # Unreadable right now (a lock, a permission): empty for this pass, and
        # the file is left exactly where it is.
        return dict(_EMPTY_STORE)
    except json.JSONDecodeError:
        # Corrupt. The app carries on with an empty store, but the file is SET
        # ASIDE first: the next sync saves whatever load returned, and saving an
        # empty store over this was how one bad write lost the whole history.
        _set_aside(p)
        return dict(_EMPTY_STORE)
    # Valid JSON but not a proper store shape — the same, for the same reason.
    if (
        not isinstance(data, dict)
        or "trades" not in data
        or not isinstance(data["trades"], list)
    ):
        _set_aside(p)
        return dict(_EMPTY_STORE)
    data.setdefault("last_sync", None)
    return data


def save_store(path, store: dict) -> None:
    """Write ``store`` to ``path`` as pretty JSON, creating parent dirs."""
    _write_whole(path, json.dumps(store, indent=2))


def update_store(store: dict, new_trades: list[dict]) -> dict:
    """Merge ``new_trades`` into ``store`` and recompute ``last_sync``.

    Dedupes via :func:`merge_trades`, then sets ``last_sync`` to the max
    ``trade_date`` of the merged trades. If the merge yields no trades, the
    existing ``last_sync`` is preserved.
    """
    merged = merge_trades(store.get("trades", []), new_trades)
    store["trades"] = merged
    recomputed = last_trade_date(merged)
    if recomputed is not None:
        store["last_sync"] = recomputed
    return store
