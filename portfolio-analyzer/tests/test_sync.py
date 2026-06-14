"""Tests for src/sync.py — pure sync-decision logic + offline orchestration.

All tests are pure/offline: ``sync_trades`` is exercised with a FAKE data
object so nothing here touches the proxy or the network.
"""
import datetime

import pytest

from src import sync


# -- should_sync ------------------------------------------------------------

def test_should_sync_none_last_sync_is_true():
    assert sync.should_sync(None, "2026-06-02") is True


def test_should_sync_stale_last_sync_is_true():
    assert sync.should_sync("2026-06-01", "2026-06-02") is True


def test_should_sync_already_today_is_false():
    assert sync.should_sync("2026-06-02", "2026-06-02") is False


def test_should_sync_future_last_sync_is_false():
    # Clock weirdness: last_sync ahead of today — don't sync.
    assert sync.should_sync("2026-06-03", "2026-06-02") is False


# -- sync_start_date --------------------------------------------------------

def test_sync_start_date_none_uses_lookback():
    today = "2026-06-02"
    expected = (
        datetime.date.fromisoformat(today) - datetime.timedelta(days=30)
    ).isoformat()
    assert sync.sync_start_date(None, today) == expected


def test_sync_start_date_none_respects_custom_lookback():
    today = "2026-06-02"
    expected = (
        datetime.date.fromisoformat(today) - datetime.timedelta(days=7)
    ).isoformat()
    assert sync.sync_start_date(None, today, default_lookback_days=7) == expected


def test_sync_start_date_returns_last_sync_when_set():
    # Re-pull from last_sync (overlap dedupes) to catch late-settled trades.
    assert sync.sync_start_date("2026-05-20", "2026-06-02") == "2026-05-20"


# -- sync_trades (offline, fake data) ---------------------------------------

def _trade(trade_id: str, trade_date: str) -> dict:
    return {
        "trade_id": trade_id,
        "symbol": "AAPL",
        "asset_type": "EQUITY",
        "underlying": "AAPL",
        "quantity": 1.0,
        "price": 100.0,
        "instruction": "BUY",
        "trade_date": trade_date,
    }


class _FakeData:
    """Records calls; returns canned account hash + transactions."""

    def __init__(self, account_hash="ABC", transactions=None):
        self._account_hash = account_hash
        self._transactions = transactions or []
        self.get_transactions_calls = []

    def first_account_hash(self):
        return self._account_hash

    def get_transactions(self, account_hash, start_date, end_date):
        self.get_transactions_calls.append((account_hash, start_date, end_date))
        return list(self._transactions)


class _ExplodingData:
    """get_transactions raises if ever called — proves it is NOT called."""

    def first_account_hash(self):
        return "ABC"

    def get_transactions(self, *a, **k):  # pragma: no cover - must not run
        raise AssertionError("get_transactions should not be called")


def test_sync_trades_merges_new_and_sets_last_sync_today():
    data = _FakeData(transactions=[_trade("a", "2026-06-01")])
    store = {"last_sync": "2026-05-31", "trades": []}
    result = sync.sync_trades(data, store, today="2026-06-02")
    assert [t["trade_id"] for t in result["trades"]] == ["a"]
    # last_sync reflects TODAY (sync ran successfully through today), not the
    # max trade_date.
    assert result["last_sync"] == "2026-06-02"
    assert data.get_transactions_calls == [("ABC", "2026-05-31", "2026-06-02")]


def test_sync_trades_skips_when_already_synced_today():
    data = _ExplodingData()
    store = {"last_sync": "2026-06-02", "trades": [_trade("a", "2026-06-01")]}
    result = sync.sync_trades(data, store, today="2026-06-02")
    assert result is store
    assert result["last_sync"] == "2026-06-02"
    assert [t["trade_id"] for t in result["trades"]] == ["a"]


def test_sync_trades_skips_when_no_account_hash():
    class _NoAccount:
        def first_account_hash(self):
            return None

        def get_transactions(self, *a, **k):  # pragma: no cover
            raise AssertionError("must not be called without an account")

    store = {"last_sync": "2026-05-31", "trades": []}
    result = sync.sync_trades(_NoAccount(), store, today="2026-06-02")
    assert result is store
    assert result["last_sync"] == "2026-05-31"
    assert result["trades"] == []


def test_sync_trades_zero_new_trades_still_bumps_last_sync():
    data = _FakeData(transactions=[])
    store = {"last_sync": "2026-05-31", "trades": []}
    result = sync.sync_trades(data, store, today="2026-06-02")
    assert result["last_sync"] == "2026-06-02"
    assert result["trades"] == []
    assert len(data.get_transactions_calls) == 1


def test_sync_trades_first_run_uses_lookback_start():
    data = _FakeData(transactions=[])
    store = {"last_sync": None, "trades": []}
    sync.sync_trades(data, store, today="2026-06-02", default_lookback_days=30)
    expected_start = (
        datetime.date.fromisoformat("2026-06-02") - datetime.timedelta(days=30)
    ).isoformat()
    assert data.get_transactions_calls == [("ABC", expected_start, "2026-06-02")]
