import approval_server


def test_card_shows_explicit_order_line():
    payload = {
        "grade": "A", "date": "2026-05-30", "pnl_today": 0, "pnl_week": 0,
        "grade_reasons": [], "conditions": {},
        "proposed_trades": [{
            "bucket": "B", "instrument": "QQQ", "notes": "QQQ via NQ",
            "max_risk": 150.0,
            "preview": {"side": "BUY", "quantity": 15, "quantity_unit": "shares",
                        "est_entry": 500.0, "stop": 490.0, "target": 520.0,
                        "max_risk": 150.0},
        }],
    }
    html = approval_server._format_trade_sheet_html(payload)
    assert "BUY 15 QQQ" in html
    assert "stop $490.0" in html
    assert "target $520.0" in html
    assert "Estimate" in html


def test_bucket_a_card_shows_profit_target():
    payload = {
        "grade": "A", "date": "2026-05-30", "pnl_today": 0, "pnl_week": 0,
        "grade_reasons": [], "conditions": {},
        "proposed_trades": [{
            "bucket": "A", "instrument": "SPX", "structure": "iron_condor",
            "notes": "SPX 0-DTE", "strikes": {"put_short": 4950},
            "preview": {"side": "SELL_TO_OPEN", "quantity": 1,
                        "quantity_unit": "contracts", "est_entry": None,
                        "stop": None, "target": None, "max_risk": 300.0,
                        "profit_target_dollars": 150.0},
        }],
    }
    html = approval_server._format_trade_sheet_html(payload)
    assert "SELL_TO_OPEN 1 SPX" in html
    assert "profit target $150" in html
    assert "max risk $300" in html
