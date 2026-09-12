"""D3: the Paper Ledger gets a pre-expiry exit pass for its DEBIT positions.

⚠ **The premise in the assessment was half wrong.** It asked to "move them into
the account, or give the ledger a manage cycle" — but the ledger already HAS a
manage tick: ``run_manage_and_refresh`` reprices it and settles its expiries
(``expire_ledger_trades``) every 5 minutes. What it had was **no rule that closes
a position before expiry**: the only pre-expiry exit was the page's Close button.

So a long call sent from the Strategy Finder or the Market Scanner's Directional
tab (both list the four debit structures in ``_PAPER_TYPES``) rode to expiry
whatever it did in between — up 300% or down to nothing.

**Scope is DEBIT rows only, deliberately.** The ledger's credit spreads are
equally ruleless, but giving them the credit rules would change how a second book
exits with its own measurement attached — the same reason the credit spreads have
no ``manage_dte``. D3 asked for long options and debit spreads.
"""
import datetime as _dt
import sys as _sys
import types as _types
from zoneinfo import ZoneInfo

from services.options_svc import compute


_NOW = _dt.datetime(2026, 6, 3, 11, 0, tzinfo=ZoneInfo("America/Chicago"))


def _debit_row(trade_id="d1", *, exp="2026-07-18", entry_dte=45, debit=200.0,
               max_profit=None, qty=1, strategy="LONG_CALL", status="OPEN"):
    return {"trade_id": trade_id, "status": status, "symbol": "SPY",
            "strategy": strategy, "direction": "DEBIT", "expiration": exp,
            "dte_at_entry": entry_dte, "quantity": qty,
            "entry_credit": -debit / 100.0, "entry_debit": debit,
            "max_profit_total": (max_profit * qty) if max_profit else None,
            "legs": [{"kind": "call", "side": "long", "strike": 500, "qty": 1}]}


def _credit_row(trade_id="c1", exp="2026-07-18"):
    return {"trade_id": trade_id, "status": "OPEN", "symbol": "QQQ",
            "strategy": "PCS", "expiration": exp, "short_strike": 400,
            "long_strike": 399, "entry_credit": 0.50, "quantity": 1}


def _install(monkeypatch, trades, marks):
    """Stub the ledger store and the repricer. ``marks`` maps trade_id ->
    the repricer's return dict."""
    closed = []
    monkeypatch.setitem(_sys.modules, "paper_trader", _types.SimpleNamespace(
        get_all_trades=lambda: trades,
        close_paper_trade=lambda t, exit_debit, reason: {
            **t, "status": "CLOSED", "exit_debit": exit_debit,
            "exit_reason": reason},
        update_trade=lambda tid, row: closed.append((tid, row))))
    monkeypatch.setitem(_sys.modules, "signal_repricer", _types.SimpleNamespace(
        clear_chain_cache=lambda: None,
        reprice_legs=lambda t, client, **kw: marks.get(t["trade_id"], {}),
        reprice_swing=lambda t, client, **kw: marks.get(t["trade_id"], {})))
    return closed


# ── the target closes a winner ─────────────────────────────────────────────

def test_a_long_call_at_its_TARGET_is_closed(monkeypatch):
    """$200 paid, now worth $3.10/share = +$110 -> past the +$100 target."""
    rows = [_debit_row()]
    closed = _install(monkeypatch, rows,
                      {"d1": {"unrealized_pnl": 110.0, "current_value": 3.10,
                              "error": None}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 1
    tid, row = closed[0]
    assert tid == "d1" and row["status"] == "CLOSED"
    assert row["exit_reason"] == "TARGET_HIT"


def test_it_closes_at_the_REPRICED_value_not_at_zero(monkeypatch):
    """⚠ ``close_paper_trade`` books P&L off this number, so passing the wrong one
    records a fictional result. It must be the position's live per-share value."""
    rows = [_debit_row()]
    closed = _install(monkeypatch, rows,
                      {"d1": {"unrealized_pnl": 110.0, "current_value": 3.10,
                              "error": None}})
    compute.manage_ledger_trades(now_ct=_NOW)
    assert closed[0][1]["exit_debit"] == 3.10


def test_a_debit_vertical_holds_at_half_its_DEBIT_and_closes_at_half_MAX_PROFIT(
        monkeypatch):
    """The denominator decision, driven end to end: $200 paid on a $5 width
    (max profit $300). +$110 is past half the debit but short of the +$150
    target, so it must HOLD."""
    rows = [_debit_row(strategy="BULL_CALL", max_profit=300.0)]
    closed = _install(monkeypatch, rows,
                      {"d1": {"unrealized_pnl": 110.0, "current_value": 3.10,
                              "error": None}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 0
    assert closed == []

    rows2 = [_debit_row(strategy="BULL_CALL", max_profit=300.0)]
    closed2 = _install(monkeypatch, rows2,
                       {"d1": {"unrealized_pnl": 155.0, "current_value": 3.55,
                               "error": None}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 1
    assert closed2[0][1]["exit_reason"] == "TARGET_HIT"


def test_max_profit_is_read_PER_CONTRACT_not_per_row(monkeypatch):
    """⚠ The row stores ``max_profit_total`` (already x quantity) while the
    repricer's P&L is PER CONTRACT. Comparing them directly would make a 3-lot's
    target three times too far away."""
    rows = [_debit_row(strategy="BULL_CALL", max_profit=300.0, qty=3)]
    assert rows[0]["max_profit_total"] == 900.0
    closed = _install(monkeypatch, rows,
                      {"d1": {"unrealized_pnl": 155.0, "current_value": 3.55,
                              "error": None}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 1


# ── the time exit and its at-entry guard ───────────────────────────────────

def test_a_long_horizon_debit_is_TIME_EXITED_inside_the_window(monkeypatch):
    """Opened at 45 DTE, now 21 days out (2026-06-24 from 2026-06-03)."""
    rows = [_debit_row(exp="2026-06-24", entry_dte=45)]
    closed = _install(monkeypatch, rows,
                      {"d1": {"unrealized_pnl": -30.0, "current_value": 1.70,
                              "error": None}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 1
    assert closed[0][1]["exit_reason"] == "TIME_EXIT"


def test_a_SHORT_DATED_debit_is_never_time_exited(monkeypatch):
    """⚠ The Directional tab's whole inventory. Opened at 3 DTE, 2 days left —
    an unguarded 21-DTE rule would close it on the tick after it opened."""
    rows = [_debit_row(exp="2026-06-05", entry_dte=3)]
    closed = _install(monkeypatch, rows,
                      {"d1": {"unrealized_pnl": -30.0, "current_value": 1.70,
                              "error": None}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 0
    assert closed == []


def test_the_DTE_is_computed_from_the_expiration_and_the_injected_clock(
        monkeypatch):
    """One day further out than the threshold must hold, which only works if the
    remaining DTE is real rather than the entry value."""
    rows = [_debit_row(exp="2026-06-25", entry_dte=45)]   # 22 days out
    closed = _install(monkeypatch, rows,
                      {"d1": {"unrealized_pnl": -30.0, "current_value": 1.70,
                              "error": None}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 0


# ── what it must LEAVE ALONE ───────────────────────────────────────────────

def test_a_losing_debit_is_HELD_with_no_stop_configured(monkeypatch):
    """Sourced: the practitioners close debit spreads before expiry rather than
    stopping them out. Down 90% of the debit and still open."""
    rows = [_debit_row()]
    closed = _install(monkeypatch, rows,
                      {"d1": {"unrealized_pnl": -180.0, "current_value": 0.20,
                              "error": None}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 0


def test_a_CREDIT_ledger_row_is_not_touched(monkeypatch):
    """⚠ Scope. The ledger's credit spreads stay ruleless here on purpose — the
    control that this pass cannot start closing a second book's positions."""
    rows = [_credit_row()]
    closed = _install(monkeypatch, rows,
                      {"c1": {"unrealized_pnl": 400.0, "current_value": 0.10,
                              "error": None}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 0
    assert closed == []


def test_a_CLOSED_row_is_skipped(monkeypatch):
    rows = [_debit_row(status="CLOSED")]
    closed = _install(monkeypatch, rows,
                      {"d1": {"unrealized_pnl": 500.0, "current_value": 7.0,
                              "error": None}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 0


def test_an_UNMARKED_row_is_skipped_rather_than_closed_at_zero(monkeypatch):
    """⚠ No mark means no decision. Treating it as a zero would satisfy any
    zero-threshold rule and close the position at a fabricated price."""
    rows = [_debit_row()]
    closed = _install(monkeypatch, rows,
                      {"d1": {"unrealized_pnl": None, "current_value": None,
                              "error": "repricing failed"}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 0
    assert closed == []


def test_a_row_with_a_pnl_but_NO_value_is_skipped(monkeypatch):
    """The close price is what gets written to the ledger; without it there is
    nothing honest to record."""
    rows = [_debit_row()]
    closed = _install(monkeypatch, rows,
                      {"d1": {"unrealized_pnl": 400.0, "current_value": None,
                              "error": None}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 0


def test_an_EXPIRED_position_is_left_to_the_settlement_pass(monkeypatch):
    """``expire_ledger_trades`` owns expiry. A past-expiry row here must not be
    closed at a stale mark instead."""
    rows = [_debit_row(exp="2026-06-01")]
    closed = _install(monkeypatch, rows,
                      {"d1": {"unrealized_pnl": None, "current_value": None,
                              "error": "expired"}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 0


# ── robustness: one bad row must not abort the pass ────────────────────────

def test_one_broken_row_does_not_stop_the_others(monkeypatch):
    rows = [{"trade_id": "junk", "status": "OPEN", "direction": "DEBIT"},
            _debit_row("d1")]
    closed = _install(monkeypatch, rows,
                      {"d1": {"unrealized_pnl": 110.0, "current_value": 3.10,
                              "error": None}})
    assert compute.manage_ledger_trades(now_ct=_NOW) == 1
    assert closed[0][0] == "d1"


def test_an_unreadable_ledger_degrades_to_zero(monkeypatch):
    def _boom():
        raise RuntimeError("no db")
    monkeypatch.setitem(_sys.modules, "paper_trader", _types.SimpleNamespace(
        get_all_trades=_boom))
    assert compute.manage_ledger_trades(now_ct=_NOW) == 0


def test_a_repricer_that_raises_leaves_the_position_open(monkeypatch):
    def _boom(t, client, **kw):
        raise RuntimeError("chain None")
    rows = [_debit_row()]
    monkeypatch.setitem(_sys.modules, "paper_trader", _types.SimpleNamespace(
        get_all_trades=lambda: rows,
        close_paper_trade=lambda t, d, r: t,
        update_trade=lambda tid, row: (_ for _ in ()).throw(
            AssertionError("must not close"))))
    monkeypatch.setitem(_sys.modules, "signal_repricer", _types.SimpleNamespace(
        clear_chain_cache=lambda: None, reprice_legs=_boom, reprice_swing=_boom))
    assert compute.manage_ledger_trades(now_ct=_NOW) == 0
