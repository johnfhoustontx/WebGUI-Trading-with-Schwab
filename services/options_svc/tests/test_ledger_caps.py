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


def test_a_trade_within_every_cap_opens_and_reports_it(ledger):
    out = compute.create_paper_trade(_pcs(), 1)
    assert out["status"] == "opened"
    assert out["trade_id"] and out["trade"]["trade_id"] == out["trade_id"]
    assert [t["trade_id"] for t in ledger.get_open_trades()] == [out["trade_id"]]


def test_over_the_per_trade_cap_is_refused_and_nothing_is_written(ledger):
    out = compute.create_paper_trade(_pcs(credit=1.00, width=10.0), 1)  # $900
    assert out["status"] == "refused"
    assert out["code"] == "TRADE_RISK_CAP"
    assert out["max_quantity"] == 0
    assert "over the $750 per-trade limit" in out["message"]
    assert ledger.get_open_trades() == []


def test_quantity_counts_toward_the_per_trade_cap(ledger):
    out = compute.create_paper_trade(_pcs(), 4)          # 4 x $190 = $760
    assert out["status"] == "refused" and out["code"] == "TRADE_RISK_CAP"
    assert out["max_quantity"] == 3


def test_the_account_limit_does_not_bind_the_ledger(ledger):
    """$400 is over the Account's $250 but inside the Ledger's own $750."""
    out = compute.create_paper_trade(_pcs(credit=1.00, width=5.0), 1)
    assert out["status"] == "opened"


def test_a_fourth_position_in_one_symbol_is_refused(ledger):
    for _ in range(3):
        assert compute.create_paper_trade(_pcs(), 1)["status"] == "opened"
    out = compute.create_paper_trade(_pcs(expiration="2026-10-24"), 1)
    assert out["status"] == "refused" and out["code"] == "SYMBOL_POSITION_CAP"
    assert len(ledger.get_open_trades()) == 3


def test_the_limit_really_binds_when_config_moves(ledger, monkeypatch):
    """Discriminating: the cap is read at call time, not copied from a literal."""
    import config_paper
    monkeypatch.setattr(config_paper, "LEDGER_MAX_RISK_PER_TRADE", 100.0)
    out = compute.create_paper_trade(_pcs(), 1)          # $190
    assert out["status"] == "refused" and out["code"] == "TRADE_RISK_CAP"


def test_every_outcome_carries_the_rungs_in_display_order(ledger):
    from shared import book_caps
    out = compute.create_paper_trade(_pcs(), 1)
    assert [r["code"] for r in out["rungs"]] == list(book_caps.DISPLAY_ORDER)


def test_an_untradeable_signal_is_an_error_outcome_not_a_raise(ledger):
    out = compute.create_paper_trade({"symbol": "SPY", "type": "LONG_STRADDLE"}, 1)
    assert out["status"] == "error" and "not paper-tradeable" in out["message"]


def test_a_trade_whose_max_loss_cannot_be_read_is_refused_not_opened(ledger):
    """book_caps counts an unusable risk as ZERO (the Account sized its trades
    first). The Ledger did not, so it must refuse here - otherwise a debit signal
    with no max_loss books max_loss_total 0.0 and clears every risk rung, while
    the preview (which needs a positive risk) says it cannot check."""
    sig = {"symbol": "SPY", "type": "LONG_CALL", "expiration": "2026-10-17",
           "net_debit": 300.0, "legs": [{"kind": "call", "side": "long", "strike": 500}]}
    out = compute.create_paper_trade(sig, 1)
    assert out["status"] == "error"
    assert out["message"] == "The trade's max loss could not be read, so the risk caps cannot be checked."
    assert ledger.get_open_trades() == []
