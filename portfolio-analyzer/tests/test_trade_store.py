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


# --- AR-09: a corrupt store is set aside, and a save is all-or-nothing --------
# The store is a single copy of every trade synced from Schwab. A corrupt file
# used to read as EMPTY, and the next sync then saved that over it: one bad
# write became the permanent loss of the history. A save was also a plain
# write, so a crash part-way through left the truncated file.

def test_a_corrupt_store_is_kept_beside_the_file_not_overwritten(tmp_path):
    path = tmp_path / "entries.json"
    path.write_text('{"trades": [{"trade_id": "a"', encoding="utf-8")   # cut short
    store = trade_store.load_store(path)
    assert store["trades"] == []
    kept = [p for p in tmp_path.iterdir() if p.name.startswith("entries.json.corrupt-")]
    assert len(kept) == 1
    assert kept[0].read_text(encoding="utf-8") == '{"trades": [{"trade_id": "a"'
    assert not path.exists()            # the next save starts a new file


def test_a_store_of_the_wrong_shape_is_set_aside_too(tmp_path):
    path = tmp_path / "entries.json"
    path.write_text('["not", "a", "store"]', encoding="utf-8")
    assert trade_store.load_store(path)["trades"] == []
    assert any(p.name.startswith("entries.json.corrupt-") for p in tmp_path.iterdir())


def test_a_missing_store_is_simply_empty(tmp_path):
    path = tmp_path / "entries.json"
    assert trade_store.load_store(path)["trades"] == []
    assert list(tmp_path.iterdir()) == []


def test_a_good_store_is_left_alone(tmp_path):
    path = tmp_path / "entries.json"
    trade_store.save_store(path, {"trades": [_trade("a")], "last_sync": "2026-05-01"})
    assert trade_store.load_store(path)["trades"][0]["trade_id"] == "a"
    assert [p.name for p in tmp_path.iterdir()] == ["entries.json"]


def test_a_save_that_fails_part_way_leaves_the_old_store(tmp_path, monkeypatch):
    path = tmp_path / "entries.json"
    trade_store.save_store(path, {"trades": [_trade("a")], "last_sync": None})

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(trade_store.os, "replace", boom)
    import pytest
    with pytest.raises(OSError):
        trade_store.save_store(path, {"trades": [], "last_sync": None})
    assert trade_store.load_store(path)["trades"][0]["trade_id"] == "a"
    assert [p.name for p in tmp_path.iterdir()] == ["entries.json"]   # no temp left
