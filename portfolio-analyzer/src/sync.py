"""Daily incremental transaction sync for the portfolio analyzer.

The CSV bootstrap (``src.trades_import``) is the one-time historical backfill —
the user imports a CSV once to seed the store. *This* module is the incremental
daily sync: on the first launch each day it pulls NEW trades since ``last_sync``
from the proxy and merges them into the trade store (dedupe by ``trade_id``),
then bumps ``last_sync`` to today.

The sync-decision helpers (:func:`should_sync`, :func:`sync_start_date`) are
pure (no network) so they are unit-tested directly. :func:`sync_trades` takes
the data client by dependency injection, so it can be exercised offline with a
fake.
"""
from __future__ import annotations

import datetime

from src.trade_store import update_store


def should_sync(last_sync: str | None, today: str) -> bool:
    """Whether a sync should run now.

    Returns True if we have never synced (``last_sync is None``) or the last
    sync predates ``today``. Returns False if we already synced today, or if
    ``last_sync`` is somehow ahead of ``today`` (clock weirdness) — in either
    case there is nothing new to pull.

    ISO ``YYYY-MM-DD`` strings sort lexically the same as chronologically, so a
    plain string comparison is correct.
    """
    if last_sync is None:
        return True
    return last_sync < today


def sync_start_date(
    last_sync: str | None,
    today: str,
    default_lookback_days: int = 30,
) -> str:
    """ISO start date to request transactions from.

    On the first sync (``last_sync is None``) there is no anchor, so look back
    ``default_lookback_days`` from ``today``. Otherwise start from ``last_sync``
    itself — re-pulling the last synced day catches trades that settled late;
    the store's dedupe-by-``trade_id`` merge absorbs the overlap.

    Returns an ISO ``YYYY-MM-DD`` string.
    """
    if last_sync is None:
        start = datetime.date.fromisoformat(today) - datetime.timedelta(
            days=default_lookback_days
        )
        return start.isoformat()
    return last_sync


def sync_trades(
    data,
    store: dict,
    today: str,
    *,
    default_lookback_days: int = 30,
) -> dict:
    """Pull new trades since ``last_sync`` and merge them into ``store``.

    ``data`` is injected (a ``PortfolioData`` or a fake) so this is testable
    offline. Returns the (mutated) store.

    Behavior:

    - If we already synced today (``not should_sync``), returns the store
      unchanged.
    - If no linked account hash is available, returns the store unchanged
      (nothing to sync against).
    - Otherwise requests transactions from :func:`sync_start_date` through
      ``today`` **for every linked account**, merges them via
      :func:`~src.trade_store.update_store` (dedupe by ``trade_id`` — Schwab
      activity ids are unique per account, so concatenating across accounts is
      safe), then explicitly sets ``last_sync = today``.

    Why force ``last_sync = today`` after the merge: ``update_store`` only bumps
    ``last_sync`` to the max *trade_date* of the merged trades. If today brought
    zero new trades (or only older late-settled ones), that would leave
    ``last_sync`` stale and we would re-sync repeatedly the same day. The sync
    *did* run successfully through today, so today is the correct watermark.
    """
    if not should_sync(store.get("last_sync"), today):
        return store

    hashes = data.account_hashes()
    if not hashes:
        return store

    start = sync_start_date(
        store.get("last_sync"), today, default_lookback_days=default_lookback_days
    )
    new: list = []
    for account_hash in hashes:
        new.extend(data.get_transactions(account_hash, start, today))
    store = update_store(store, new)
    # Watermark to today: the sync ran successfully through today even if no new
    # trades (or only older ones) came back. See docstring.
    store["last_sync"] = today
    return store
