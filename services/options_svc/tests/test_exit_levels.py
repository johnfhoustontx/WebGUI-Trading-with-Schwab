"""The Stop and Target each paper book publishes on its OPEN rows.

``services/options_svc/exit_levels.py`` puts ``stop_level`` / ``target_level``
(position dollars, the basis of the row's P&L column) and ``stop_kind`` on each
row; the rule engine's agreement with these levels is pinned in
options-scanner/tests/test_exit_levels.py.
"""
import sys
import types

import pytest

from services.options_svc import exit_levels, handlers
from shared import trade_mgmt as tm

#: The kinds a row can carry; the page's words for them are pinned to the same
#: set (webgui/tests/test_exit_columns.py).
STOP_KINDS = {"money", "break-even", "trail", "debit", "idea"}

CONF = {"trail_start": 0.25, "trail_giveback": 0.10, "quiet_windows": [],
        "captured_at_target": "close", "captured": True, "account": True,
        "ledger": True, "ledger_self_managed": True, "ledger_take_profit": True}


@pytest.fixture
def conf(monkeypatch):
    c = dict(CONF)
    monkeypatch.setattr(tm, "protect", lambda: dict(c))
    monkeypatch.setattr(exit_levels, "_be_level", lambda row: 2.6)
    return c


# ── the Paper Ledger ─────────────────────────────────────────────────────────

def test_a_ledger_credit_trade_shows_its_target_and_no_loss_stop(conf):
    t = {"trade_id": "c", "status": "OPEN", "strategy": "PCS", "entry_credit": 1.00,
         "quantity": 2}
    exit_levels.ledger([t])
    assert (t["stop_level"], t["stop_kind"], t["target_level"]) == (None, None, 100.0)


def test_its_trail_once_armed_is_the_stop_in_position_dollars(conf):
    t = {"trade_id": "c", "status": "OPEN", "strategy": "PCS", "entry_credit": 1.00,
         "quantity": 2, "mfe_pnl": 40.0}                 # per contract, as stored
    exit_levels.ledger([t])
    assert (t["stop_level"], t["stop_kind"]) == (60.0, "trail")     # (40 - 10) x 2


def test_take_profit_off_shows_no_target(conf):
    conf["ledger_take_profit"] = False
    t = {"trade_id": "c", "status": "OPEN", "strategy": "PCS", "entry_credit": 1.00}
    exit_levels.ledger([t])
    assert t.get("target_level") is None          # no level at all: no keys


def test_a_ledger_debit_trade(conf):
    t = {"trade_id": "d", "status": "OPEN", "strategy": "LONG_CALL",
         "direction": "DEBIT", "entry_credit": -2.00, "entry_debit": 200.0,
         "quantity": 1, "max_profit_total": None}
    exit_levels.ledger([t])
    assert t["target_level"] == pytest.approx(100.0)


def test_an_idea_row_shows_its_own_lines_until_the_trail_passes_its_stop(conf):
    t = {"trade_id": "i", "status": "OPEN", "source": "trade_idea", "strategy": "PCS",
         "direction": "CREDIT", "entry_credit": 1.00, "quantity": 1,
         "target_pnl": 50.0, "stop_pnl": 150.0}
    exit_levels.ledger([t])
    assert (t["stop_level"], t["stop_kind"], t["target_level"]) == (-150.0, "idea", 50.0)
    t["mfe_pnl"] = 40.0
    exit_levels.ledger([t])
    assert (t["stop_level"], t["stop_kind"]) == (30.0, "trail")


def test_a_closed_row_gets_no_levels(conf):
    t = {"trade_id": "x", "status": "CLOSED", "strategy": "PCS", "entry_credit": 1.0}
    exit_levels.ledger([t])
    assert not set(exit_levels.KEYS) & set(t)


# ── the paper Account ────────────────────────────────────────────────────────

def _pos(**kw):
    return {"position_id": 1, "status": "OPEN", "strategy": "PCS", "symbol": "SPY",
            "entry_credit": 1.00, "quantity": 3, "mfe": None, "be_armed": 0, **kw}


def test_the_account_closes_at_the_target_with_the_switch_off(conf):
    p = _pos()
    exit_levels.account([p], lifecycle=False)
    assert (p["stop_level"], p["stop_kind"], p["target_level"]) == (-600.0, "money", 150.0)


def test_the_accounts_best_is_position_level(conf):
    """``mfe`` of $120 on a 3-lot is $40 a contract: trail at $30, so $90."""
    p = _pos(mfe=120.0)
    exit_levels.account([p], lifecycle=False)
    assert (p["stop_level"], p["stop_kind"]) == (90.0, "trail")


def test_a_riding_account_position_shows_no_target(conf):
    p = _pos(be_armed=1)
    exit_levels.account([p], lifecycle=True)
    assert p["target_level"] is None
    assert (p["stop_level"], p["stop_kind"]) == (pytest.approx(7.8), "break-even")


# ── captured signals ─────────────────────────────────────────────────────────

def _signals(monkeypatch, peak):
    monkeypatch.setitem(sys.modules, "signal_db", types.SimpleNamespace(
        peak_unrealized=lambda sid: peak))


def test_a_captured_signal_closes_at_the_target_as_shipped(monkeypatch, conf):
    _signals(monkeypatch, None)
    s = {"signal_id": "s", "strategy": "PCS", "entry_credit": 1.00, "be_armed": 0,
         "unrealized_pnl": 10.0}
    exit_levels.captured([s])
    assert (s["stop_level"], s["stop_kind"], s["target_level"]) == (-200.0, "money", 50.0)


def test_a_captured_signals_trail_uses_the_stored_best(monkeypatch, conf):
    _signals(monkeypatch, 45.0)
    s = {"signal_id": "s", "strategy": "PCS", "entry_credit": 1.00, "unrealized_pnl": 20.0}
    exit_levels.captured([s])
    assert (s["stop_level"], s["stop_kind"]) == (35.0, "trail")


def test_a_row_that_cannot_be_read_is_left_blank_and_the_rest_are_not(monkeypatch, conf):
    def boom(sid):
        if sid == "bad":
            raise OSError("locked")
        return None
    monkeypatch.setitem(sys.modules, "signal_db", types.SimpleNamespace(peak_unrealized=boom))
    bad = {"signal_id": "bad", "strategy": "PCS", "entry_credit": 1.0}
    good = {"signal_id": "ok", "strategy": "PCS", "entry_credit": 1.0}
    exit_levels.captured([bad, good])
    assert "stop_level" not in bad
    assert good["target_level"] == 50.0


def test_every_publisher_attaches_the_levels():
    """The three views carry the levels: captured through ``_publish_captured``
    (every writer of that view), the Account and the Ledger at their refresh."""
    import inspect
    assert "exit_levels.captured(" in inspect.getsource(handlers._publish_captured)
    assert "exit_levels.account(" in inspect.getsource(handlers.refresh_paper_account)
    src = inspect.getsource(handlers)
    assert src.count("exit_levels.ledger(") == 1


def test_the_kinds_are_the_pages_kinds():
    import inspect
    import signal_recommender
    src = inspect.getsource(exit_levels) + inspect.getsource(signal_recommender.exit_levels)
    for kind in STOP_KINDS:
        assert f'"{kind}"' in src
