from fastapi.testclient import TestClient
import approval_server


def test_performance_route_renders(monkeypatch):
    monkeypatch.setattr(approval_server, "build_report", lambda: {
        "summary": {"total_trades": 2, "wins": 1, "losses": 1, "win_rate": 50.0,
                    "realized_pnl": 35.0, "pnl_by_bucket": {"A": 75.0, "B": -40.0}},
        "trades": [{"trade_id": "d-A-1", "date": "2026-05-30", "bucket": "A",
                    "instrument": "SPX", "side": "SELL_TO_OPEN", "pnl": 75.0,
                    "status": "closed", "source": "streamed",
                    "exit_reason": "target_hit", "order_id": "1"}],
    })
    client = TestClient(approval_server.app)
    r = client.get("/performance")
    assert r.status_code == 200
    assert "Win rate" in r.text
    assert "d-A-1" in r.text
    assert "streamed" in r.text


def test_approval_page_links_to_performance():
    payload = {"grade": "A", "date": "2026-05-30", "pnl_today": 0, "pnl_week": 0,
               "grade_reasons": [], "conditions": {}, "proposed_trades": []}
    html = approval_server._format_trade_sheet_html(payload)
    assert "/performance" in html
