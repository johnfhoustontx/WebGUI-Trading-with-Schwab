"""Tests that paper_trader hooks the streaming tracker on add/update/delete."""
from unittest.mock import patch

import pytest

import trades_db
import paper_trader


def _trade(tid="t1", status="OPEN"):
    return {
        "trade_id": tid, "mode": "PAPER", "status": status, "symbol": "SPX",
        "strategy": "PCS", "expiration": "2026-05-30", "short_strike": 5200,
        "long_strike": 5150, "width": 50, "quantity": 1, "entry_credit": 1.5,
        "entry_time": "2026-05-30T10:00:00-05:00",
    }


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point every persistence path at an isolated tmp DB file."""
    db = tmp_path / "trades.db"
    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(trades_db, "LEGACY_TRADES_JSON", tmp_path / "paper_trades.json")
    monkeypatch.setattr(trades_db, "LEGACY_EVENTS_JSON", tmp_path / "trade_log.json")
    trades_db._initialised.clear()
    yield tmp_path
    trades_db._initialised.clear()


def test_add_trade_calls_track(sandbox):
    with patch("paper_trader.trade_tracker_client.track") as mtrack:
        paper_trader.add_trade(_trade())
    mtrack.assert_called_once()
    assert mtrack.call_args[0][0]["trade_id"] == "t1"


def test_update_to_closed_calls_untrack(sandbox):
    paper_trader.add_trade(_trade())
    with patch("paper_trader.trade_tracker_client.untrack") as muntrack:
        paper_trader.update_trade("t1", _trade(status="CLOSED"))
    muntrack.assert_called_once_with("t1")


def test_update_to_expired_calls_untrack(sandbox):
    paper_trader.add_trade(_trade())
    with patch("paper_trader.trade_tracker_client.untrack") as muntrack:
        paper_trader.update_trade("t1", _trade(status="EXPIRED"))
    muntrack.assert_called_once_with("t1")


def test_update_to_open_does_not_untrack(sandbox):
    paper_trader.add_trade(_trade())
    with patch("paper_trader.trade_tracker_client.untrack") as muntrack:
        paper_trader.update_trade("t1", _trade(status="OPEN"))
    muntrack.assert_not_called()


def test_delete_existing_trade_calls_untrack(sandbox):
    paper_trader.add_trade(_trade())
    with patch("paper_trader.trade_tracker_client.untrack") as muntrack:
        paper_trader.delete_trade("t1")
    muntrack.assert_called_once_with("t1")


def test_delete_missing_trade_does_not_untrack(sandbox):
    with patch("paper_trader.trade_tracker_client.untrack") as muntrack:
        paper_trader.delete_trade("nope")
    muntrack.assert_not_called()
