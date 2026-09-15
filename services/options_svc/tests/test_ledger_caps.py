"""The Paper Ledger's book, limits and equity basis for the risk caps."""
import pytest

from services.options_svc import compute


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A real trades.db under tmp_path (same shape as test_ledger_manage_end_to_end)."""
    import paper_trader
    import trade_tracker_client
    import trades_db

    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", tmp_path / "trades.db")
    monkeypatch.setattr(trades_db, "_initialised", set())
    monkeypatch.setattr(trade_tracker_client, "track", lambda t: True)
    monkeypatch.setattr(trade_tracker_client, "untrack", lambda tid: True)
    return paper_trader


def _pcs(symbol="ORCL", expiration="2026-10-17", credit=0.60, width=2.5):
    return {"symbol": symbol, "type": "PCS", "trade_type": "SWING",
            "expiration": expiration, "dte": 30, "short_strike": 100.0,
            "long_strike": 100.0 - width, "width": width, "credit": credit,
            "max_loss": round(width - credit, 2)}


def test_open_trades_carry_symbol_expiry_risk_and_sector(ledger):
    ledger.add_trade(ledger.create_paper_trade(_pcs(), 1))
    state = compute.ledger_book_state()
    assert len(state["open"]) == 1
    row = state["open"][0]
    assert row["symbol"] == "ORCL" and row["expiration"] == "2026-10-17"
    assert row["max_loss_total"] == pytest.approx(190.0)
    assert row["sector"] == "Information Technology"


def test_equity_is_starting_balance_plus_closed_realized_pnl(ledger):
    t = ledger.create_paper_trade(_pcs(), 1)
    ledger.add_trade(t)
    closed = ledger.close_paper_trade(t, 0.10)         # +$50
    ledger.update_trade(t["trade_id"], closed)
    state = compute.ledger_book_state()
    assert state["open"] == []
    assert state["realized_pnl"] == pytest.approx(50.0)
    assert state["equity"] == pytest.approx(25050.0)


def test_limits_are_the_accounts_plus_the_per_trade_cap(ledger):
    import config_paper
    import paper_concentration
    limits = compute.ledger_book_state()["limits"]
    assert limits == {**paper_concentration.default_limits(),
                      "max_risk_per_trade": config_paper.LEDGER_MAX_RISK_PER_TRADE}


def test_a_non_finite_realized_pnl_is_dropped_not_summed(ledger, monkeypatch):
    rows = [{"status": "CLOSED", "realized_pnl": float("nan")},
            {"status": "EXPIRED", "realized_pnl": 20.0}]
    state = compute.ledger_book_state(trades=rows)
    assert state["realized_pnl"] == pytest.approx(20.0)


def test_the_ledger_has_its_own_750_per_trade_limit_and_the_account_keeps_250():
    import config_paper
    assert config_paper.LEDGER_MAX_RISK_PER_TRADE == 750.0
    assert config_paper.MAX_RISK_PER_TRADE == 250.0


def test_limits_follow_the_ledger_constant_at_call_time(ledger, monkeypatch):
    import config_paper
    monkeypatch.setattr(config_paper, "LEDGER_MAX_RISK_PER_TRADE", 123.0)
    assert compute.ledger_book_state()["limits"]["max_risk_per_trade"] == 123.0


def test_a_realized_pnl_that_is_not_a_number_is_skipped():
    rows = [{"status": "CLOSED", "realized_pnl": None},
            {"status": "CLOSED", "realized_pnl": "junk"},
            {"status": "EXPIRED", "realized_pnl": -30.0}]
    state = compute.ledger_book_state(trades=rows)
    assert state["realized_pnl"] == pytest.approx(-30.0)
    assert state["equity"] == pytest.approx(24970.0)


def test_an_unmapped_symbol_gets_its_own_bucket():
    rows = [{"status": "OPEN", "symbol": "ZZZQ", "expiration": "2026-10-17",
             "max_loss_total": 50.0}]
    assert compute.ledger_book_state(trades=rows)["open"][0]["sector"] == "?ZZZQ"
