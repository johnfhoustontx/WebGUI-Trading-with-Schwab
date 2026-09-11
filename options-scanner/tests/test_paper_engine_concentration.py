"""``run_entry_cycle`` enforces the per-name / per-expiry caps.

The unit tests for the policy itself live in test_paper_concentration.py; these
pin the WIRING, which is the half that was missing -- a cap nothing calls is a
comment. See paper_concentration.py's header for the 2026-09-08 ORCL book.
"""
import paper_account_db as pdb
import paper_engine as pe


def _sig(**kw):
    base = {"signal_id": "s1", "symbol": "SPY", "strategy": "PCS",
            "short_strike": 500, "long_strike": 499, "width": 1.0,
            "expiration": "2026-06-30", "dte_at_entry": 27,
            "entry_credit": 0.50, "entry_score": 80, "recommendation": None}
    base.update(kw)
    return base


class _FakeBroker:
    """Fills every order at a fixed price."""
    def __init__(self, price=0.48):
        self.price = price
    def submit_order(self, order, client):
        return {"orderId": 1, "status": "FILLED", "orderType": "NET_CREDIT",
                "quantity": order["quantity"], "filledQuantity": order["quantity"],
                "price": self.price, "enteredTime": "t", "closeTime": "t",
                "orderStrategyType": "SINGLE",
                "complexOrderStrategyType": "VERTICAL",
                "orderLegCollection": [], "statusDescription": None}


def _fresh(tmp_path):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-03")
    return db


def _orcl(n, **kw):
    """n eligible ORCL signals, each with its own signal_id."""
    return [_sig(signal_id=f"orcl{i}", symbol="ORCL", width=1.0,
                 entry_credit=0.50, **kw) for i in range(n)]


def test_entry_cycle_stops_at_the_per_symbol_position_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_SYMBOL", 3)
    monkeypatch.setattr(pe.config_paper, "MAX_RISK_PER_SYMBOL", 100_000.0)
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_EXPIRY", 100)
    db = _fresh(tmp_path)
    pe.run_entry_cycle(None, "2026-06-03", _orcl(14), _FakeBroker(0.48), db)
    assert len(pdb.fetch_open_positions(db)) == 3


def test_a_capped_signal_is_not_recorded_as_a_rejected_order(tmp_path, monkeypatch):
    """The condition is transient. An order row would make
    ``has_order_for_signal`` blacklist the signal permanently, so a name that
    freed up later could never be entered."""
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_SYMBOL", 1)
    monkeypatch.setattr(pe.config_paper, "MAX_RISK_PER_SYMBOL", 100_000.0)
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_EXPIRY", 100)
    db = _fresh(tmp_path)
    pe.run_entry_cycle(None, "2026-06-03", _orcl(4), _FakeBroker(0.48), db)
    assert [o for o in pdb.fetch_orders(db) if o["status"] == "REJECTED"] == []


def test_a_capped_signal_is_admitted_once_the_name_frees_up(tmp_path, monkeypatch):
    """The behaviour the skip-without-recording exists to preserve."""
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_SYMBOL", 1)
    monkeypatch.setattr(pe.config_paper, "MAX_RISK_PER_SYMBOL", 100_000.0)
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_EXPIRY", 100)
    db = _fresh(tmp_path)
    sigs = _orcl(2)
    pe.run_entry_cycle(None, "2026-06-03", sigs, _FakeBroker(0.48), db)
    assert len(pdb.fetch_open_positions(db)) == 1

    opened = pdb.fetch_open_positions(db)[0]
    pdb.close_position(db, opened["position_id"], 0.10, None, 38.0,
                       "MANUAL_CLOSE", "2026-06-03T12:00")
    pe.run_entry_cycle(None, "2026-06-03", sigs, _FakeBroker(0.48), db)
    assert len(pdb.fetch_open_positions(db)) == 1   # the second signal got in


def test_entry_cycle_stops_at_the_per_symbol_risk_cap(tmp_path, monkeypatch):
    """Risk binds before the count does: at 208 per ticket, 750 admits three."""
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_SYMBOL", 100)
    monkeypatch.setattr(pe.config_paper, "MAX_RISK_PER_SYMBOL", 750.0)
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_EXPIRY", 100)
    db = _fresh(tmp_path)
    pe.run_entry_cycle(None, "2026-06-03", _orcl(14), _FakeBroker(0.48), db)
    positions = pdb.fetch_open_positions(db)
    assert sum(p["max_loss_total"] for p in positions) <= 750.0
    assert len(positions) == 3


def test_entry_cycle_stops_at_the_per_expiry_cap_across_names(tmp_path, monkeypatch):
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_SYMBOL", 100)
    monkeypatch.setattr(pe.config_paper, "MAX_RISK_PER_SYMBOL", 100_000.0)
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_EXPIRY", 5)
    db = _fresh(tmp_path)
    sigs = [_sig(signal_id=f"s{i}", symbol=s, width=1.0, entry_credit=0.50)
            for i, s in enumerate(
                ["ORCL", "MSFT", "AMD", "INTC", "NVDA", "AVGO", "PANW"])]
    pe.run_entry_cycle(None, "2026-06-03", sigs, _FakeBroker(0.48), db)
    assert len(pdb.fetch_open_positions(db)) == 5


def test_a_different_expiry_is_still_open_when_one_is_full(tmp_path, monkeypatch):
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_SYMBOL", 100)
    monkeypatch.setattr(pe.config_paper, "MAX_RISK_PER_SYMBOL", 100_000.0)
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_EXPIRY", 2)
    db = _fresh(tmp_path)
    sigs = [_sig(signal_id="a", symbol="MSFT", width=1.0, entry_credit=0.50,
                 expiration="2026-06-30"),
            _sig(signal_id="b", symbol="AMD", width=1.0, entry_credit=0.50,
                 expiration="2026-06-30"),
            _sig(signal_id="c", symbol="INTC", width=1.0, entry_credit=0.50,
                 expiration="2026-06-30"),      # third on 06-30 -> refused
            _sig(signal_id="d", symbol="NVDA", width=1.0, entry_credit=0.50,
                 expiration="2026-07-17")]      # a clear expiry -> admitted
    pe.run_entry_cycle(None, "2026-06-03", sigs, _FakeBroker(0.48), db)
    got = {p["symbol"] for p in pdb.fetch_open_positions(db)}
    assert got == {"MSFT", "AMD", "NVDA"}


def test_an_uncapped_book_is_unaffected(tmp_path, monkeypatch):
    """The caps must not disturb the ordinary path."""
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_SYMBOL", 100)
    monkeypatch.setattr(pe.config_paper, "MAX_RISK_PER_SYMBOL", 100_000.0)
    monkeypatch.setattr(pe.config_paper, "MAX_POSITIONS_PER_EXPIRY", 100)
    db = _fresh(tmp_path)
    pe.run_entry_cycle(None, "2026-06-03", [_sig()], _FakeBroker(0.48), db)
    assert len(pdb.fetch_open_positions(db)) == 1
