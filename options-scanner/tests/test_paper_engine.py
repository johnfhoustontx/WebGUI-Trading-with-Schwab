from datetime import datetime
from zoneinfo import ZoneInfo

import paper_engine as pe
import paper_account_db as pdb

_CT = ZoneInfo("America/Chicago")


# 2026-06-04 is a Thursday (a normal trading day, not a holiday).
def test_in_trading_window_excludes_premarket():
    # 08:15 CT is before the 08:30 RTH open -> not tradeable
    assert pe.in_trading_window(datetime(2026, 6, 4, 8, 15, tzinfo=_CT)) is False


def test_in_trading_window_includes_rth():
    assert pe.in_trading_window(datetime(2026, 6, 4, 9, 0, tzinfo=_CT)) is True


def test_in_trading_window_excludes_after_close():
    # 15:30 CT is after the 15:00 RTH close -> not tradeable
    assert pe.in_trading_window(datetime(2026, 6, 4, 15, 30, tzinfo=_CT)) is False


def test_in_trading_window_excludes_weekend():
    # 2026-06-06 is a Saturday
    assert pe.in_trading_window(datetime(2026, 6, 6, 10, 0, tzinfo=_CT)) is False


def test_in_entry_window_excludes_opening_buffer():
    # entries are blocked for the first OPEN_BUFFER_MIN (5) minutes after 08:30
    assert pe.in_entry_window(datetime(2026, 6, 4, 8, 31, tzinfo=_CT)) is False
    assert pe.in_entry_window(datetime(2026, 6, 4, 8, 34, tzinfo=_CT)) is False


def test_in_entry_window_open_after_buffer():
    assert pe.in_entry_window(datetime(2026, 6, 4, 8, 36, tzinfo=_CT)) is True
    assert pe.in_entry_window(datetime(2026, 6, 4, 12, 0, tzinfo=_CT)) is True


def test_in_entry_window_still_respects_rth_close():
    assert pe.in_entry_window(datetime(2026, 6, 4, 15, 30, tzinfo=_CT)) is False


def _sig(**kw):
    base = {"signal_id": "s1", "symbol": "SPY", "strategy": "PCS",
            "short_strike": 500, "long_strike": 499, "width": 1.0,
            "expiration": "2026-06-30", "dte_at_entry": 27,
            "entry_credit": 0.50, "entry_score": 80, "recommendation": None}
    base.update(kw)
    return base


def test_eligible_high_score_no_mark():
    assert pe.is_eligible(_sig(entry_score=80, recommendation=None), min_score=70) is True


def test_ineligible_low_score():
    assert pe.is_eligible(_sig(entry_score=60), min_score=70) is False


def test_ineligible_when_cut():
    assert pe.is_eligible(_sig(recommendation="CUT"), min_score=70) is False


def test_ineligible_when_already_at_target():
    assert pe.is_eligible(_sig(recommendation="TAKE_PROFIT"), min_score=70) is False


def test_eligible_when_hold():
    assert pe.is_eligible(_sig(recommendation="HOLD"), min_score=70) is True


class _FakeBroker:
    """Returns FILLED at a fixed price; records the orders it received."""
    def __init__(self, price=0.48):
        self.price = price
        self.calls = []
    def submit_order(self, order, client):
        self.calls.append(order)
        return {"orderId": 1, "status": "FILLED", "orderType": "NET_CREDIT",
                "quantity": order["quantity"], "filledQuantity": order["quantity"],
                "price": self.price, "enteredTime": "t", "closeTime": "t",
                "orderStrategyType": "SINGLE", "complexOrderStrategyType": "VERTICAL",
                "orderLegCollection": [], "statusDescription": None}


def test_entry_cycle_opens_sized_position(tmp_path):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    sig = _sig(entry_score=80, width=1.0, entry_credit=0.50)
    broker = _FakeBroker(price=0.48)
    pe.run_entry_cycle(client=None, now_date="2026-06-03",
                       signals=[sig], broker=broker, db_path=db)
    pos = pdb.fetch_open_positions(db)
    # qty is sized off the ACTUAL fill (0.48), not the signal credit (0.50):
    # max loss/contract = (1.0-0.48)*100 = 52 -> floor(250/52) = 4 -> BP 208 (<=250)
    assert len(pos) == 1 and pos[0]["quantity"] == 4
    acct = pdb.get_account(db)
    assert acct["buying_power_reserved"] == 208.0
    assert acct["buying_power_reserved"] <= 250.0   # never exceeds the per-trade cap
    assert broker.calls[0]["side"] == "SELL_TO_OPEN"


def test_entry_cycle_rejects_low_credit_fill(tmp_path):
    """A near-zero / garbage opening-auction fill must be rejected, not opened."""
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    sig = _sig(width=1.0, entry_credit=0.50)   # passes the pre-fill estimate
    pe.run_entry_cycle(None, "2026-06-03", [sig], _FakeBroker(0.04), db)  # fills at 0.04
    assert pdb.fetch_open_positions(db) == []
    assert pdb.fetch_orders(db)[0]["reject_reason"] == "LOW_CREDIT"


def test_entry_cycle_does_not_retry_a_rejected_signal(tmp_path):
    """A signal rejected (e.g. RISK_TOO_HIGH) must not be re-attempted every
    cycle — that was the reject-churn flood."""
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    sig = _sig(width=5.0, entry_credit=1.0)  # max loss/contract 400 -> qty 0 -> reject
    pe.run_entry_cycle(None, "2026-06-03", [sig], _FakeBroker(), db)
    pe.run_entry_cycle(None, "2026-06-03", [sig], _FakeBroker(), db)  # 2nd pass
    rejects = [o for o in pdb.fetch_orders(db) if o["status"] == "REJECTED"]
    assert len(rejects) == 1  # one record, not two


def test_entry_cycle_does_not_reenter_a_traded_signal(tmp_path):
    """Once a signal has been opened (even after it closes), it must not be
    re-opened — stops the stop-out/re-entry churn."""
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    sig = _sig(width=1.0, entry_credit=0.50)
    pe.run_entry_cycle(None, "2026-06-03", [sig], _FakeBroker(0.48), db)
    p = pdb.fetch_open_positions(db)[0]
    pdb.close_position(db, p["position_id"], exit_debit=0.20, exit_order_id=999,
                       realized_pnl=140.0, exit_reason="TARGET_HIT",
                       exit_ts="t", status="CLOSED")
    # same signal comes around again -> must NOT re-open
    pe.run_entry_cycle(None, "2026-06-03", [sig], _FakeBroker(0.48), db)
    assert pdb.fetch_open_positions(db) == []
    same = [p for p in pdb.fetch_all_positions(db) if p["signal_id"] == sig["signal_id"]]
    assert len(same) == 1


def test_entry_cycle_resizes_down_on_poor_fill_to_respect_risk_cap(tmp_path):
    """Regression: an IC sized off the scanner's optimistic mid (0.93 -> 35
    contracts) must NOT open 35 lots when it actually fills at 0.44. It must
    re-size off the fill so total max loss stays <= MAX_RISK_PER_TRADE."""
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    sig = _sig(strategy="IC", width=1.0, entry_credit=0.93,
               call_short=760, call_long=761, entry_score=80)
    pe.run_entry_cycle(None, "2026-06-03", [sig], _FakeBroker(0.44), db)
    pos = pdb.fetch_open_positions(db)
    assert len(pos) == 1
    p = pos[0]
    # (1.0-0.44)*100 = 56/contract -> floor(250/56) = 4 contracts -> $224
    assert p["quantity"] == 4
    assert p["max_loss_total"] <= 250.0          # the rule the bug violated
    assert p["max_loss_total"] == 224.0
    assert pdb.get_account(db)["buying_power_reserved"] == 224.0


def test_entry_cycle_skips_when_position_exists(tmp_path):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    sig = _sig()
    broker = _FakeBroker()
    pe.run_entry_cycle(None, "2026-06-03", [sig], broker, db)
    pe.run_entry_cycle(None, "2026-06-03", [sig], broker, db)  # dedup
    assert len(pdb.fetch_open_positions(db)) == 1


def test_entry_cycle_rejects_risk_too_high(tmp_path):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    sig = _sig(width=5.0, entry_credit=1.0)   # max loss/contract 400 -> qty 0
    broker = _FakeBroker()
    pe.run_entry_cycle(None, "2026-06-03", [sig], broker, db)
    assert pdb.fetch_open_positions(db) == []
    orders = pdb.fetch_orders(db)
    assert orders and orders[0]["status"] == "REJECTED"
    assert orders[0]["reject_reason"] == "RISK_TOO_HIGH"
    assert broker.calls == []   # rejected before the broker is called


def test_entry_cycle_halted_blocks_entries(tmp_path):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pdb.set_halted(db, True)
    pe.run_entry_cycle(None, "2026-06-03", [_sig()], _FakeBroker(), db)
    assert pdb.fetch_open_positions(db) == []


def test_entry_cycle_rejects_insufficient_bp(tmp_path):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 100.0, "2026-06-03")   # tiny cash
    sig = _sig(width=1.0, entry_credit=0.50)      # needs 260 BP -> reject
    pe.run_entry_cycle(None, "2026-06-03", [sig], _FakeBroker(0.48), db)
    assert pdb.fetch_open_positions(db) == []
    assert pdb.fetch_orders(db)[0]["reject_reason"] == "INSUFFICIENT_BUYING_POWER"


def test_manage_cycle_closes_on_take_profit(tmp_path, monkeypatch):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pe.run_entry_cycle(None, "2026-06-03", [_sig()], _FakeBroker(0.50), db)
    monkeypatch.setattr(pe.signal_repricer, "reprice_swing",
        lambda trade, client: {"current_value": 0.20, "unrealized_pnl": 150.0,
            "pnl_pct_of_credit": 60.0, "current_underlying": 500.0,
            "current_short_delta": -0.1, "error": None})
    monkeypatch.setattr(pe.signal_recommender, "recommend",
        lambda ctx: {"action": "TAKE_PROFIT", "reason": "x", "code": "TARGET_HIT"})
    pe.run_manage_cycle(client=None, now_date="2026-06-03",
                        broker=_FakeBroker(0.20), db_path=db)
    assert pdb.fetch_open_positions(db) == []
    acct = pdb.get_account(db)
    assert acct["buying_power_reserved"] == 0.0
    assert acct["realized_pnl"] > 0


def test_manage_cycle_sets_halt_on_drawdown(tmp_path, monkeypatch):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pe.run_entry_cycle(None, "2026-06-03", [_sig()], _FakeBroker(0.50), db)
    monkeypatch.setattr(pe.signal_repricer, "reprice_swing",
        lambda trade, client: {"current_value": 6.0, "unrealized_pnl": -2_600.0,
            "pnl_pct_of_credit": -1000.0, "current_underlying": 480.0,
            "current_short_delta": -0.5, "error": None})
    monkeypatch.setattr(pe.signal_recommender, "recommend",
        lambda ctx: {"action": "HOLD", "reason": "x", "code": "HOLD"})
    pe.run_manage_cycle(None, "2026-06-03", _FakeBroker(), db)
    assert pdb.get_account(db)["halted"] == 1


def test_manage_cycle_defers_expiry_without_underlying(tmp_path, monkeypatch):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    # expiration == now_date -> dte 0
    sig = _sig(expiration="2026-06-03")
    pe.run_entry_cycle(None, "2026-06-03", [sig], _FakeBroker(0.50), db)
    monkeypatch.setattr(pe.signal_repricer, "reprice_swing",
        lambda trade, client: {"current_value": None, "unrealized_pnl": None,
            "pnl_pct_of_credit": None, "current_underlying": None,
            "current_short_delta": None, "error": "chain None"})
    pe.run_manage_cycle(None, "2026-06-03", _FakeBroker(), db)
    # position still open, BP still reserved (not leaked, not force-settled)
    assert len(pdb.fetch_open_positions(db)) == 1
    assert pdb.get_account(db)["buying_power_reserved"] > 0


# ── Expiration settlement gate (should_settle / underlying_last) ────────────
def _ct(h, m=0, d=3):
    return datetime(2026, 6, d, h, m, tzinfo=_CT)


def test_should_settle_past_expiration_true():
    assert pe.should_settle("2026-06-02", "2026-06-03", _ct(10)) is True


def test_should_settle_expiry_day_before_close_false():
    # 0-DTE at 10:00 CT must be held to the close, not settled at the open.
    assert pe.should_settle("2026-06-03", "2026-06-03", _ct(10)) is False


def test_should_settle_expiry_day_at_close_true():
    assert pe.should_settle("2026-06-03", "2026-06-03", _ct(15)) is True
    assert pe.should_settle("2026-06-03", "2026-06-03", _ct(15, 30)) is True


def test_should_settle_future_false():
    assert pe.should_settle("2026-06-10", "2026-06-03", _ct(16)) is False


def test_should_settle_malformed_false():
    assert pe.should_settle(None, "2026-06-03", _ct(16)) is False
    assert pe.should_settle("not-a-date", "2026-06-03", _ct(16)) is False


class _QuoteClient:
    """Minimal client whose get_quotes([sym]) yields a lastPrice for settlement."""
    def __init__(self, price):
        self._price = price

    def get_quotes(self, syms):
        sym = syms[0]
        price = self._price
        class _R:
            status_code = 200
            def json(self):
                return {sym: {"quote": {"lastPrice": price}}}
        return _R()


def test_underlying_last_reads_last_price():
    assert pe.underlying_last(_QuoteClient(497.25), "SPY") == 497.25


def test_underlying_last_none_client_returns_none():
    assert pe.underlying_last(None, "SPY") is None


def test_manage_cycle_settles_at_close_with_fetched_underlying(tmp_path, monkeypatch):
    """A 0-DTE position at/after 15:00 CT settles at intrinsic vs a FETCHED
    underlying, even though the repricer supplies no current_underlying."""
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pe.run_entry_cycle(None, "2026-06-03", [_sig(expiration="2026-06-03")],
                       _FakeBroker(0.50), db)
    # Repricer can't price the expired chain (underlying None) — settlement must
    # fall back to a direct quote. Underlying 505 is above both PCS put strikes
    # (500/499) -> both worthless -> full credit kept.
    monkeypatch.setattr(pe.signal_repricer, "reprice_swing",
        lambda trade, client: {"current_value": None, "unrealized_pnl": None,
            "pnl_pct_of_credit": None, "current_underlying": None,
            "current_short_delta": None, "error": "expired"})
    pe.run_manage_cycle(_QuoteClient(505.0), "2026-06-03", _FakeBroker(), db,
                        now_ct=_ct(15, 5))
    assert pdb.fetch_open_positions(db) == []          # settled + closed
    acct = pdb.get_account(db)
    assert acct["buying_power_reserved"] == 0.0
    assert acct["realized_pnl"] > 0                    # OTM PCS -> kept credit (net fees)


def test_manage_cycle_holds_0dte_before_close(tmp_path, monkeypatch):
    """Before 15:00 CT on expiry day, a 0-DTE position is NOT force-settled."""
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    pe.run_entry_cycle(None, "2026-06-03", [_sig(expiration="2026-06-03")],
                       _FakeBroker(0.50), db)
    monkeypatch.setattr(pe.signal_repricer, "reprice_swing",
        lambda trade, client: {"current_value": 0.20, "unrealized_pnl": 30.0,
            "pnl_pct_of_credit": 40.0, "current_underlying": 500.0,
            "current_short_delta": -0.1, "error": None})
    monkeypatch.setattr(pe.signal_recommender, "recommend",
        lambda ctx: {"action": "HOLD", "reason": "x", "code": "HOLD"})
    pe.run_manage_cycle(_QuoteClient(500.0), "2026-06-03", _FakeBroker(), db,
                        now_ct=_ct(10))
    assert len(pdb.fetch_open_positions(db)) == 1      # held, not settled
    assert pdb.get_account(db)["buying_power_reserved"] > 0


def test_manage_cycle_passes_per_contract_pnl_to_recommender(tmp_path, monkeypatch):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    # width 1.0, credit 0.50 -> qty 5
    pe.run_entry_cycle(None, "2026-06-03", [_sig(width=1.0, entry_credit=0.50)],
                       _FakeBroker(0.50), db)
    monkeypatch.setattr(pe.signal_repricer, "reprice_swing",
        lambda trade, client: {"current_value": 0.30, "unrealized_pnl": 20.0,
            "pnl_pct_of_credit": 40.0, "current_underlying": 500.0,
            "current_short_delta": -0.1, "error": None})
    captured = {}
    def _fake_recommend(ctx):
        captured.update(ctx)
        return {"action": "HOLD", "reason": "x", "code": "HOLD"}
    monkeypatch.setattr(pe.signal_recommender, "recommend", _fake_recommend)
    pe.run_manage_cycle(None, "2026-06-03", _FakeBroker(), db)
    # per-contract value (20.0), NOT 20.0*5=100
    assert captured["unrealized_pnl"] == 20.0
    assert captured["entry_credit"] == 0.50


def test_account_snapshot_reports_equity(tmp_path):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    snap = pe.account_snapshot(db)
    assert snap["equity"] == 25_000.0
    assert snap["open_count"] == 0
    assert snap["session_pnl"] == 0.0
    assert snap["halted"] is False


def test_entry_cycle_rejects_degenerate_fill(tmp_path):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    # width 1.0, credit 0.50 -> qty 5; broker "fills" at 1.50 (credit >= width)
    sig = _sig(width=1.0, entry_credit=0.50)
    pe.run_entry_cycle(None, "2026-06-03", [sig], _FakeBroker(1.50), db)
    assert pdb.fetch_open_positions(db) == []
    acct = pdb.get_account(db)
    assert acct["cash"] == 25_000.0                 # not corrupted
    assert acct["buying_power_reserved"] == 0.0
    assert pdb.fetch_orders(db)[0]["reject_reason"] == "DEGENERATE_FILL"
