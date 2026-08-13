"""A DEBIT paper trade must survive the SQLite round trip well enough to SETTLE.

``expire_paper_trade`` routes on ``trade["direction"] == "DEBIT"`` and
``_expire_debit_trade`` values the position from ``trade["legs"]`` against
``trade["entry_debit"]``. None of those three had a column in ``trades.db``, and
``insert_trade`` silently drops unknown keys — so a trade loaded back from disk
fell through to the CREDIT settlement path, where a long option's strategy hits
the ``else: net_val = 0`` branch and the P&L is computed from ``entry_credit``
(a NEGATIVE per-share debit by display convention) instead of intrinsic value.

Every test here persists through a REAL temp database and reloads before
settling. Passing the in-memory dict straight to ``expire_paper_trade`` would
pass even against the broken code — the round trip IS the bug.
"""
import datetime
import json

import pytest

import paper_trader
import trades_db

_EXP = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point every persistence path at an isolated tmp DB file."""
    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", tmp_path / "trades.db")
    monkeypatch.setattr(trades_db, "LEGACY_TRADES_JSON", tmp_path / "paper_trades.json")
    monkeypatch.setattr(trades_db, "LEGACY_EVENTS_JSON", tmp_path / "trade_log.json")
    monkeypatch.setattr(paper_trader.trade_tracker_client, "track", lambda *a, **k: None)
    monkeypatch.setattr(paper_trader.trade_tracker_client, "untrack", lambda *a, **k: None)
    trades_db._initialised.clear()
    yield tmp_path
    trades_db._initialised.clear()


def _long_put(strike=240.0, net_debit=960.0, max_loss=973.0):
    """A long put: $9.60/share debit = $960 per contract."""
    return {"symbol": "AMZN", "type": "LONG_PUT", "expiration": _EXP, "dte": 30,
            "legs": [{"kind": "put", "side": "long", "strike": strike,
                      "mark": net_debit / 100.0, "qty": 1}],
            "net_debit": net_debit, "net_credit": None, "max_loss": max_loss,
            "max_profit": None, "unbounded": False, "breakevens": [strike - 9.60],
            "underlying_price": 245.0}


def _bull_call():
    """Debit vertical 100/105, $2.00/share debit = $200 per contract."""
    return {"symbol": "SPY", "type": "BULL_CALL", "expiration": _EXP, "dte": 30,
            "legs": [{"kind": "call", "side": "long", "strike": 100.0, "mark": 3.0, "qty": 1},
                     {"kind": "call", "side": "short", "strike": 105.0, "mark": 1.0, "qty": 1}],
            "net_debit": 200.0, "net_credit": None, "max_loss": 205.0,
            "max_profit": 295.0, "unbounded": False, "breakevens": [102.0],
            "underlying_price": 100.0}


def _persist_and_reload(signal, quantity):
    """Create -> write to the real DB -> read the row BACK. Returns the loaded dict."""
    created = paper_trader.create_paper_trade(signal, quantity=quantity)
    paper_trader.add_trade(created)
    loaded = next(t for t in paper_trader.load_trades()
                  if t["trade_id"] == created["trade_id"])
    assert loaded is not created                      # genuinely came off disk
    return created, loaded


#############################################
# THE FIELDS SURVIVE THE ROUND TRIP
#############################################

def test_direction_survives_the_round_trip(sandbox):
    _, loaded = _persist_and_reload(_long_put(), 10)
    assert loaded["direction"] == "DEBIT"


def test_legs_survive_the_round_trip(sandbox):
    created, loaded = _persist_and_reload(_bull_call(), 1)
    assert loaded["legs"] == created["legs"]
    assert len(loaded["legs"]) == 2
    assert loaded["legs"][0]["strike"] == 100.0
    assert loaded["legs"][1]["side"] == "short"


def test_entry_debit_survives_the_round_trip(sandbox):
    """``_expire_debit_trade`` subtracts this; without it P&L ignores the premium paid."""
    created, loaded = _persist_and_reload(_long_put(), 10)
    assert loaded["entry_debit"] == created["entry_debit"] == 960.0
    assert loaded["entry_debit_total"] == created["entry_debit_total"] == 9600.0


def test_legs_are_stored_as_json_text(sandbox):
    """Persisted shape, so an external reader (or a future migration) knows the encoding."""
    import sqlite3
    _persist_and_reload(_bull_call(), 1)
    conn = sqlite3.connect(sandbox / "trades.db")
    try:
        raw = conn.execute("SELECT legs, direction FROM trades").fetchone()
    finally:
        conn.close()
    assert isinstance(raw[0], str) and raw[1] == "DEBIT"
    assert json.loads(raw[0])[0]["kind"] == "call"


#############################################
# THE MONEY: SETTLEMENT AFTER A ROUND TRIP
#############################################

def test_reloaded_itm_long_put_settles_at_intrinsic(sandbox):
    """240 put settling at 231.50 is worth $8.50/share = $850; paid $960 -> -$110/contract."""
    _, loaded = _persist_and_reload(_long_put(strike=240.0, net_debit=960.0), 10)
    paper_trader.expire_paper_trade(loaded, settlement_price=231.50)
    assert loaded["status"] == "EXPIRED"
    assert loaded["exit_debit"] == 8.50                 # per-share intrinsic, NOT 0
    assert loaded["realized_pnl"] == -1100.0            # (850 - 960) x 10


def test_reloaded_deep_itm_long_put_books_a_profit(sandbox):
    """The clearest proof the credit path is gone: a winner used to book a loss."""
    _, loaded = _persist_and_reload(_long_put(strike=240.0, net_debit=960.0), 10)
    paper_trader.expire_paper_trade(loaded, settlement_price=200.0)
    assert loaded["exit_debit"] == 40.0                 # $40/share intrinsic
    assert loaded["realized_pnl"] == 30400.0            # (4000 - 960) x 10, a PROFIT


def test_reloaded_itm_bull_call_settles_at_width(sandbox):
    """Both legs ITM -> spread worth its $5 width = $500; paid $200 -> +$300/contract."""
    _, loaded = _persist_and_reload(_bull_call(), 2)
    paper_trader.expire_paper_trade(loaded, settlement_price=110.0)
    assert loaded["exit_debit"] == 5.0
    assert loaded["realized_pnl"] == 600.0              # (500 - 200) x 2


def test_reloaded_otm_debit_loses_exactly_the_premium_paid(sandbox):
    """OTM: worth nothing, lose the debit. Scales with quantity."""
    _, loaded = _persist_and_reload(_long_put(strike=240.0, net_debit=960.0), 10)
    paper_trader.expire_paper_trade(loaded, settlement_price=300.0)
    assert loaded["exit_debit"] == 0.0
    assert loaded["realized_pnl"] == -9600.0            # 960 x 10


#############################################
# CREDIT SPREADS ARE UNAFFECTED
#############################################

def test_reloaded_credit_spread_still_settles_on_the_credit_path(sandbox):
    pcs = {"symbol": "SPY", "type": "PCS", "trade_type": "SWING", "expiration": _EXP,
           "dte": 3, "short_strike": 600.0, "long_strike": 595.0, "width": 5.0,
           "credit": 1.55, "max_loss": 3.45, "breakeven": 598.45,
           "short_delta": -0.30, "net_theta": 0.12, "underlying_price": 610.0}
    _, loaded = _persist_and_reload(pcs, 2)
    assert loaded["direction"] is None                  # NULL, not "DEBIT"
    assert loaded["legs"] is None
    paper_trader.expire_paper_trade(loaded, settlement_price=598.0)
    # short ITM by 2.00, long OTM -> net 2.00; (1.55 - 2.00) x 2 x 100
    assert loaded["exit_debit"] == 2.0
    assert loaded["realized_pnl"] == -90.0


def test_legacy_row_without_the_new_columns_still_loads(sandbox):
    """A pre-migration DB must survive the additive ALTER with NULLs, not fail to open."""
    import sqlite3
    db = sandbox / "trades.db"
    conn = sqlite3.connect(db)
    try:
        # Build the table WITHOUT the new columns, as an old DB has it.
        conn.execute("CREATE TABLE trades (trade_id TEXT PRIMARY KEY, status TEXT NOT NULL,"
                     " symbol TEXT NOT NULL, strategy TEXT NOT NULL, entry_time TEXT NOT NULL,"
                     " entry_credit REAL, quantity INTEGER)")
        conn.execute("INSERT INTO trades VALUES ('old01','OPEN','SPY','PCS','2026-01-02',0.5,1)")
        conn.commit()
    finally:
        conn.close()
    trades_db._initialised.clear()
    rows = paper_trader.load_trades()
    assert len(rows) == 1
    assert rows[0]["trade_id"] == "old01"
    assert rows[0]["direction"] is None and rows[0]["legs"] is None
