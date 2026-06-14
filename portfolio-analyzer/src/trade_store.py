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


def load_store(path) -> dict:
    """Read the JSON store at ``path``.

    Returns the empty store if the file does not exist or is corrupt/unreadable
    (a corrupt file is treated as empty rather than crashing the app).
    """
    p = pathlib.Path(path)
    if not p.exists():
        return dict(_EMPTY_STORE)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt/unreadable store — treat as empty so the app can recover.
        return dict(_EMPTY_STORE)
    # Valid JSON but not a proper store shape — treat as empty.
    if (
        not isinstance(data, dict)
        or "trades" not in data
        or not isinstance(data["trades"], list)
    ):
        return dict(_EMPTY_STORE)
    data.setdefault("last_sync", None)
    return data


def save_store(path, store: dict) -> None:
    """Write ``store`` to ``path`` as pretty JSON, creating parent dirs."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(store, indent=2), encoding="utf-8")


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
