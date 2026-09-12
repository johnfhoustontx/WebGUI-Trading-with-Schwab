"""D3 end to end: a real ledger, a real repricer, a real rule engine.

⚠ **The unit tests beside this one stub ``paper_trader`` and
``signal_repricer``, so they cannot see a units or shape mismatch between the
layers — and that is the bug class this repo keeps paying for** (the `get_quotes`
envelope, the `_LEG_LAYOUT["CCS"]` fixture, the consumer-side guard the producer
never drove). Here only the Schwab client is fake: the row is written by the real
``paper_trader.create_paper_trade`` into a real SQLite ledger, marked by the real
``reprice_legs`` off a chain shaped like the proxy's, judged by the real
``recommend``, and closed by the real ``close_paper_trade``.

So this is the test that would catch a per-share/per-contract slip between
``entry_debit`` (per contract), ``current_value`` (per share) and
``max_profit_total`` (per contract x quantity).
"""
import datetime as _dt
import sys as _sys
import types as _types
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import compute

# ⚠ DERIVED from today, never written down. ``reprice_legs`` checks expiry
# against the REAL clock (its ``today`` argument defaults there) while
# ``manage_ledger_trades`` computes the remaining DTE from the clock injected
# here — so a hardcoded expiration drifts into the past and every reprice comes
# back ``error="expired"``, which this pass correctly SKIPS. The first draft of
# this file did exactly that and read as "the rules never fire".
_TODAY = _dt.date.today()
_ENTRY_DTE = 45
_EXP = (_TODAY + _dt.timedelta(days=_ENTRY_DTE)).isoformat()


def _ct(days_from_today=0):
    """11:00 CT, ``days_from_today`` from now."""
    d = _TODAY + _dt.timedelta(days=days_from_today)
    return _dt.datetime(d.year, d.month, d.day, 11, 0,
                        tzinfo=ZoneInfo("America/Chicago"))


_NOW = _ct()


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A real ``trades.db`` under tmp_path, with the real engine modules.

    ⚠ Patching ``DEFAULT_DB_PATH`` is enough here and would NOT be for
    ``signal_db``: ``trades_db`` resolves it at CALL time (``db_path=None`` ->
    look it up), the shape the repo's live-DB-leak post-mortem asks for.
    ``_initialised`` is cleared so ``connect`` re-runs ``init_db`` for the new
    path rather than trusting a previous test's latch.
    """
    import paper_trader
    import trade_tracker_client
    import trades_db

    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", tmp_path / "trades.db")
    monkeypatch.setattr(trades_db, "_initialised", set())
    # ⚠ ``add_trade`` POSTs the row to the proxy's stream tracker at
    # ``repo_paths.PROXY_URL``. Off the prod box that is a 1.5s timeout per row;
    # ON it, the suite would register fake trades with the LIVE proxy. The
    # repo-root conftest guards `sqlite3.connect` against exactly this class and
    # does not yet guard outbound HTTP.
    monkeypatch.setattr(trade_tracker_client, "track", lambda t: True)
    monkeypatch.setattr(trade_tracker_client, "untrack", lambda tid: True)
    return paper_trader


def _chain(mark_long, mark_short=None):
    """A proxy-shaped chain. ``_leg_mid`` reads bid/ask, so quote both sides
    tight around the mark."""
    def ctr(m):
        return [{"bid": m - 0.05, "ask": m + 0.05, "mark": m, "delta": 0.5,
                 "volatility": 25.0}]
    exp_key = f"{_EXP}:{_ENTRY_DTE}"
    cm = {exp_key: {"500.0": ctr(mark_long)}}
    if mark_short is not None:
        cm[exp_key]["505.0"] = ctr(mark_short)
    return {"underlyingPrice": 502.0, "underlying": {"last": 502.0},
            "callExpDateMap": cm, "putExpDateMap": {}}


def _fake_client(chain):
    class _R:
        status_code = 200

        @staticmethod
        def json():
            return chain

    class _Opts:
        class ContractType:
            ALL = "ALL"

    return _types.SimpleNamespace(
        get_option_chain=lambda *a, **k: _R(), Options=_Opts)


def _long_call_signal(net_debit=200.0):
    return {"symbol": "SPY", "type": "LONG_CALL", "expiration": _EXP,
            "dte": _ENTRY_DTE,
            "legs": [{"kind": "call", "side": "long", "strike": 500.0, "qty": 1,
                      "mark": 2.00}],
            "net_debit": net_debit, "max_loss": net_debit, "max_profit": None,
            "unbounded": True, "breakevens": [502.0], "underlying_price": 500.0}


def _bull_call_signal():
    """$2.00 paid for the 500/505 call spread: max profit $300/contract."""
    return {"symbol": "SPY", "type": "BULL_CALL", "expiration": _EXP,
            "dte": _ENTRY_DTE,
            "legs": [{"kind": "call", "side": "long", "strike": 500.0, "qty": 1,
                      "mark": 3.00},
                     {"kind": "call", "side": "short", "strike": 505.0, "qty": 1,
                      "mark": 1.00}],
            "net_debit": 200.0, "max_loss": 200.0, "max_profit": 300.0,
            "unbounded": False, "breakevens": [502.0], "underlying_price": 500.0}


def _run(monkeypatch, chain, now=_NOW):
    monkeypatch.setattr(compute._proxy, "schwab_py_client", _fake_client(chain))
    import signal_repricer
    signal_repricer.clear_chain_cache()
    return compute.manage_ledger_trades(now_ct=now)


def test_a_long_call_up_55pc_is_closed_and_the_P_and_L_is_right(
        ledger, monkeypatch):
    """Paid $2.00, now worth $3.10 -> +$110 on $200 = +55%, past the target.

    ⚠ The assertion that matters is ``realized_pnl``: every layer has to agree
    on units for it to come out $110 rather than $11,000 or -$510."""
    ledger.add_trade(ledger.create_paper_trade(_long_call_signal(), 1))
    assert _run(monkeypatch, _chain(3.10)) == 1

    row = ledger.get_all_trades()[0]
    assert row["status"] == "CLOSED"
    assert row["exit_reason"] == "TARGET_HIT"
    assert row["exit_debit"] == pytest.approx(3.10)
    assert row["realized_pnl"] == pytest.approx(110.0)


def test_the_same_position_up_45pc_stays_OPEN(ledger, monkeypatch):
    """$2.90 -> +$90, short of the +$100 target. The boundary, driven for real."""
    ledger.add_trade(ledger.create_paper_trade(_long_call_signal(), 1))
    assert _run(monkeypatch, _chain(2.90)) == 0
    assert ledger.get_all_trades()[0]["status"] == "OPEN"


def test_a_THREE_LOT_books_three_times_the_P_and_L(ledger, monkeypatch):
    """⚠ Quantity is the other units axis: ``entry_debit`` is per contract and
    ``max_profit_total`` is already x3, so a slip either way shows up here."""
    ledger.add_trade(ledger.create_paper_trade(_long_call_signal(), 3))
    assert _run(monkeypatch, _chain(3.10)) == 1
    assert ledger.get_all_trades()[0]["realized_pnl"] == pytest.approx(330.0)


def test_a_debit_VERTICAL_uses_its_max_profit_and_prices_BOTH_legs(
        ledger, monkeypatch):
    """The 500/505 spread paid $2.00, max profit $300, so the target is +$150.
    Marks of $4.00 and $0.50 give a net $3.50 -> +$150 exactly.

    This is also the only test that proves the short leg is SUBTRACTED: priced
    as a naked long it would read $4.00 and +$200."""
    ledger.add_trade(ledger.create_paper_trade(_bull_call_signal(), 1))
    assert _run(monkeypatch, _chain(4.00, mark_short=0.50)) == 1

    row = ledger.get_all_trades()[0]
    assert row["exit_debit"] == pytest.approx(3.50)
    assert row["realized_pnl"] == pytest.approx(150.0)
    assert row["exit_reason"] == "TARGET_HIT"


def test_a_debit_vertical_at_half_its_DEBIT_is_still_OPEN(ledger, monkeypatch):
    """+$110 clears half the debit but not half the max profit, so the
    denominator decision is visible end to end. Marks $3.60/$0.50 -> $3.10."""
    ledger.add_trade(ledger.create_paper_trade(_bull_call_signal(), 1))
    assert _run(monkeypatch, _chain(3.60, mark_short=0.50)) == 0
    assert ledger.get_all_trades()[0]["status"] == "OPEN"


def test_the_time_exit_closes_a_loser_at_the_real_mark(ledger, monkeypatch):
    """Opened at 45 DTE; the clock moves to 21 days before expiry. Worth $1.20
    against $2.00 paid -> -$80, and the row must record BOTH the loss and
    TIME_EXIT."""
    ledger.add_trade(ledger.create_paper_trade(_long_call_signal(), 1))
    later = _ct(_ENTRY_DTE - 21)
    assert _run(monkeypatch, _chain(1.20), now=later) == 1

    row = ledger.get_all_trades()[0]
    assert row["exit_reason"] == "TIME_EXIT"
    assert row["realized_pnl"] == pytest.approx(-80.0)


def test_a_CREDIT_row_in_the_same_ledger_is_untouched(ledger, monkeypatch):
    """Both books' rows live in one table, so the direction filter has to work
    against a real mixed ledger rather than a hand-built list."""
    ledger.add_trade(ledger.create_paper_trade(
        {"symbol": "SPY", "type": "PCS", "expiration": _EXP, "dte": _ENTRY_DTE,
         "short_strike": 490.0, "long_strike": 488.0, "width": 2.0,
         "credit": 0.60, "max_loss": 1.40, "trade_type": "SWING"}, 1))
    ledger.add_trade(ledger.create_paper_trade(_long_call_signal(), 1))

    assert _run(monkeypatch, _chain(3.10)) == 1
    by_strat = {t["strategy"]: t for t in ledger.get_all_trades()}
    assert by_strat["PCS"]["status"] == "OPEN"
    assert by_strat["LONG_CALL"]["status"] == "CLOSED"


def test_a_row_whose_legs_are_not_QUOTED_stays_open(ledger, monkeypatch):
    """The chain has no 500 strike, so ``reprice_legs`` reports a failure and
    the pass must skip rather than close at a fabricated price."""
    ledger.add_trade(ledger.create_paper_trade(_long_call_signal(), 1))
    empty = {"underlyingPrice": 502.0,
             "callExpDateMap": {f"{_EXP}:{_ENTRY_DTE}": {}},
             "putExpDateMap": {}}
    assert _run(monkeypatch, empty) == 0
    assert ledger.get_all_trades()[0]["status"] == "OPEN"


def test_a_closed_row_is_not_closed_AGAIN_on_the_next_pass(ledger, monkeypatch):
    """Idempotence on the real store: the second pass must find nothing, or a
    row's exit price and P&L would be rewritten every hour."""
    ledger.add_trade(ledger.create_paper_trade(_long_call_signal(), 1))
    assert _run(monkeypatch, _chain(3.10)) == 1
    first = dict(ledger.get_all_trades()[0])

    assert _run(monkeypatch, _chain(5.00)) == 0
    assert ledger.get_all_trades()[0]["realized_pnl"] == first["realized_pnl"]
    assert ledger.get_all_trades()[0]["exit_debit"] == first["exit_debit"]
