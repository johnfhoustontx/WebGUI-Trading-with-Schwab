import json
import intraday_monitor


def test_update_trade_log_matches_on_trade_id(tmp_path, monkeypatch):
    log = tmp_path / "trade_log.json"
    json.dump([{"trade_id": "2026-05-30-A-1", "bucket": "A", "status": "open", "pnl": 0.0}],
              log.open("w"))
    monkeypatch.setattr(intraday_monitor, "TRADE_LOG", str(log))
    intraday_monitor._update_trade_log("2026-05-30-A-1", 87.5, "profit_target")
    data = json.load(log.open())
    assert data[0]["pnl"] == 87.5
    assert data[0]["status"] == "closed"
    assert data[0]["result"] == "Win"


def test_log_trade_writes_open_status(tmp_path, monkeypatch):
    import order_executor, json
    log = tmp_path / "trade_log.json"
    monkeypatch.setattr(order_executor, "TRADE_LOG", str(log))
    tid = order_executor._log_trade({"bucket": "A", "instrument": "SPX", "side": "SELL_TO_OPEN"},
                                    {"success": True, "order_id": "1"})
    rec = json.load(log.open())[0]
    assert rec["status"] == "open"
    assert rec["trade_id"] == tid
