"""Tests for src/trade_store.py — persist + dedupe-merge trade store."""
from src import trade_store


def _trade(trade_id: str, trade_date: str = "2026-05-01") -> dict:
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


def test_merge_dedupes_by_trade_id():
    existing = [_trade("a"), _trade("b")]
    new = [_trade("b"), _trade("c")]
    merged = trade_store.merge_trades(existing, new)
    assert [t["trade_id"] for t in merged] == ["a", "b", "c"]
    assert len(merged) == 3


def test_last_trade_date_returns_max():
    trades = [
        _trade("a", "2026-05-01"),
        _trade("b", "2026-05-30"),
        _trade("c", "2026-05-20"),
    ]
    assert trade_store.last_trade_date(trades) == "2026-05-30"
    assert trade_store.last_trade_date([]) is None


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "data" / "entries.json"
    store = {"last_sync": "2026-05-30", "trades": [_trade("a"), _trade("b")]}
    trade_store.save_store(path, store)
    assert trade_store.load_store(path) == store

    missing = tmp_path / "nope" / "missing.json"
    assert trade_store.load_store(missing) == {"last_sync": None, "trades": []}


def test_update_store_recomputes_last_sync():
    store = {"last_sync": None, "trades": []}
    store = trade_store.update_store(
        store, [_trade("a", "2026-05-01"), _trade("b", "2026-05-30")]
    )
    assert store["last_sync"] == "2026-05-30"
    assert len(store["trades"]) == 2

    # Updating again with a duplicate trade_id does not grow the list.
    store = trade_store.update_store(store, [_trade("b", "2026-05-30")])
    assert len(store["trades"]) == 2
    assert store["last_sync"] == "2026-05-30"


def test_update_store_empty_new_trades_preserves_last_sync():
    store = {
        "last_sync": "2026-05-30",
        "trades": [_trade("a", "2026-05-01"), _trade("b", "2026-05-30")],
    }
    store = trade_store.update_store(store, [])
    assert store["last_sync"] == "2026-05-30"
    assert [t["trade_id"] for t in store["trades"]] == ["a", "b"]


def test_load_store_treats_malformed_shape_as_empty(tmp_path):
    import json

    path = tmp_path / "entries.json"
    path.write_text(json.dumps([]), encoding="utf-8")
    assert trade_store.load_store(path) == {"last_sync": None, "trades": []}

    path.write_text(json.dumps({"trades": "oops"}), encoding="utf-8")
    assert trade_store.load_store(path) == {"last_sync": None, "trades": []}
