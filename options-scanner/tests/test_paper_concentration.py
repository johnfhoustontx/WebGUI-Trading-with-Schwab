"""Concentration caps for the auto paper engine.

These exist because on 2026-09-08 the paper book held FOURTEEN open positions
and every one of them was ORCL: $2,829 of max loss (11.6% of a $24,490 account)
in one name, one direction and one expiry, over an earnings report scheduled the
day before that expiry. The engine had no per-name limit of any kind -- only
``MAX_RISK_PER_TRADE`` (per trade) and ``MAX_SESSION_DRAWDOWN`` (whole account),
with nothing in between. Fourteen tickets read as a diversified book and were
one bet sliced fourteen ways.

The correlated-loss failure mode had already fired once: on 2026-09-01 ORCL fell
149.12 -> 141.32 in a single session and eight ORCL spreads hit DELTA_STOP /
MONEY_STOP the same day. The stops worked correctly -- that is the point.
"""
import math

import pytest

import paper_concentration as pc


def _pos(symbol, expiration="2026-09-11", max_loss_total=100.0):
    return {"symbol": symbol, "expiration": expiration,
            "max_loss_total": max_loss_total}


LIMITS = {"max_positions_per_symbol": 3,
          "max_risk_per_symbol": 750.0,
          "max_positions_per_expiry": 5}


def test_empty_book_admits_the_first_position():
    assert pc.concentration_reject([], "ORCL", "2026-09-11", 150.0,
                                   limits=LIMITS) is None


def test_a_book_under_every_cap_admits_the_candidate():
    book = [_pos("ORCL"), _pos("MSFT", "2026-09-18")]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 150.0,
                                   limits=LIMITS) is None


def test_the_fourth_position_in_one_name_is_refused():
    book = [_pos("ORCL"), _pos("ORCL"), _pos("ORCL")]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 100.0,
                                   limits=LIMITS) == pc.SYMBOL_POSITION_CAP


def test_positions_in_other_names_do_not_count_against_a_symbol():
    book = [_pos("MSFT"), _pos("AMD"), _pos("INTC")]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 100.0,
                                   limits=LIMITS) is None


def test_risk_that_would_breach_the_per_symbol_budget_is_refused():
    book = [_pos("ORCL", max_loss_total=600.0)]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 200.0,
                                   limits=LIMITS) == pc.SYMBOL_RISK_CAP


def test_risk_landing_exactly_on_the_budget_is_admitted():
    """The cap is a ceiling, not a strict bound -- 750 of 750 is within it."""
    book = [_pos("ORCL", max_loss_total=600.0)]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 150.0,
                                   limits=LIMITS) is None


def test_one_expiry_is_capped_across_the_whole_book_not_per_name():
    """Five names expiring the same Friday is date concentration even though no
    single name is over its own cap."""
    book = [_pos("MSFT"), _pos("AMD"), _pos("INTC"), _pos("NVDA"), _pos("AVGO")]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 100.0,
                                   limits=LIMITS) == pc.EXPIRY_POSITION_CAP


def test_a_different_expiry_is_not_blocked_by_a_full_one():
    book = [_pos("MSFT"), _pos("AMD"), _pos("INTC"), _pos("NVDA"), _pos("AVGO")]
    assert pc.concentration_reject(book, "ORCL", "2026-09-18", 100.0,
                                   limits=LIMITS) is None


def test_a_non_finite_row_cannot_silently_switch_the_risk_cap_off():
    """``nan > 750`` is False, so a NaN in the sum disables the ceiling while
    looking like a working guard -- the repo's documented pins-the-bound trap.
    The bad row contributes nothing; the finite ones still bind."""
    book = [_pos("ORCL", max_loss_total=600.0),
            _pos("ORCL", max_loss_total=float("nan"))]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 200.0,
                                   limits=LIMITS) == pc.SYMBOL_RISK_CAP


def test_a_missing_risk_field_does_not_crash_the_gate():
    book = [{"symbol": "ORCL", "expiration": "2026-09-11"}]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 100.0,
                                   limits=LIMITS) is None


def test_symbols_are_compared_case_and_whitespace_insensitively():
    book = [_pos(" orcl "), _pos("ORCL"), _pos("Orcl")]
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 100.0,
                                   limits=LIMITS) == pc.SYMBOL_POSITION_CAP


def test_the_position_cap_is_reported_before_the_risk_cap():
    """Both are breached; the count is the more legible reason to log."""
    book = [_pos("ORCL", max_loss_total=300.0)] * 3
    assert pc.concentration_reject(book, "ORCL", "2026-09-11", 400.0,
                                   limits=LIMITS) == pc.SYMBOL_POSITION_CAP


def test_the_defaults_come_from_config_paper():
    """No ``limits`` argument means the shipped policy, so a config edit moves
    the engine without a code change."""
    import config_paper
    book = [_pos("ORCL")] * config_paper.MAX_POSITIONS_PER_SYMBOL
    assert pc.concentration_reject(book, "ORCL", "2026-09-11",
                                   1.0) == pc.SYMBOL_POSITION_CAP


def test_the_september_orcl_book_would_have_been_refused_at_the_fourth_ticket():
    """The regression this module exists for, in its own numbers."""
    book = []
    admitted = 0
    for _ in range(14):
        if pc.concentration_reject(book, "ORCL", "2026-09-11", 200.0,
                                   limits=LIMITS) is not None:
            break
        book.append(_pos("ORCL", max_loss_total=200.0))
        admitted += 1
    assert admitted == 3


# ---- B3: a book-wide deployment cap ----------------------------------------
# The three caps above are per SYMBOL and per EXPIRY. Nothing capped the book as
# a whole, so a diversified-looking book could still commit most of the account:
# three symbols at the $750 symbol cap is $2,250 and clears every gate above.
# Measured on the live book 2026-09-11: 11 positions, $1,933 of open risk, 8.0%
# of a $24,184 equity - comfortable, and with no ceiling above it.
#
# The fraction is deliberately the TIGHT end of the published range
# (theoptionpremium 20-25%; Option Alpha keeps 40-50% in cash, i.e. 50-60%
# deployed). At 20% of the live book that is $4,837 against $1,933 committed, so
# it is a real ceiling with ~11 more $250 spreads of headroom rather than
# something that bites on day one.

DEPLOY = {**LIMITS, "max_deployed_risk_pct": 0.20}


def test_the_deployment_cap_refuses_once_the_book_is_full():
    book = [_pos("AAA", max_loss_total=1000.0), _pos("BBB", max_loss_total=1000.0)]
    # 2000 committed + 100 incoming = 2100 > 20% of 10000
    assert pc.concentration_reject(book, "CCC", "2026-10-16", 100.0,
                                  limits=DEPLOY, equity=10_000.0) == pc.DEPLOYMENT_CAP


def test_the_deployment_cap_allows_while_there_is_room():
    """Non-vacuity: the cap must not refuse everything."""
    book = [_pos("AAA", max_loss_total=500.0)]
    assert pc.concentration_reject(book, "CCC", "2026-10-16", 100.0,
                                   limits=DEPLOY, equity=10_000.0) is None


def test_the_cap_counts_the_INCOMING_risk_too():
    """A candidate that fits only if you ignore it is not a candidate. 1900 + 200
    crosses 2000 where 1900 + 50 does not."""
    book = [_pos("AAA", max_loss_total=1900.0)]
    assert pc.concentration_reject(book, "CCC", "2026-10-16", 50.0,
                                   limits=DEPLOY, equity=10_000.0) is None
    assert pc.concentration_reject(book, "CCC", "2026-10-16", 200.0,
                                   limits=DEPLOY, equity=10_000.0) == pc.DEPLOYMENT_CAP


def test_the_cap_shrinks_with_the_account():
    """The whole point of a FRACTION: the same book that is fine at $25k is over
    the line after a drawdown, with no config edit."""
    book = [_pos("AAA", max_loss_total=2000.0)]
    assert pc.concentration_reject(book, "CCC", "2026-10-16", 100.0,
                                   limits=DEPLOY, equity=25_000.0) is None
    assert pc.concentration_reject(book, "CCC", "2026-10-16", 100.0,
                                   limits=DEPLOY, equity=8_000.0) == pc.DEPLOYMENT_CAP


def test_no_equity_means_the_cap_is_SKIPPED_not_zero():
    """A fraction of an unknown cannot be enforced, and treating a missing equity
    as 0 would refuse every trade forever - the failure mode that looks like a
    broken engine rather than a cap."""
    book = [_pos("AAA", max_loss_total=9999.0)]
    for equity in (None, 0.0, float("nan")):
        assert pc.concentration_reject(book, "CCC", "2026-10-16", 100.0,
                                       limits=DEPLOY, equity=equity) is None


def test_a_limits_dict_without_the_key_keeps_the_old_behaviour():
    """Back-compat by DATA: the cap is opt-in, so every existing caller and the
    three older caps are untouched."""
    book = [_pos("AAA", max_loss_total=9999.0)]
    assert pc.concentration_reject(book, "CCC", "2026-10-16", 100.0,
                                   limits=LIMITS, equity=10_000.0) is None


def test_the_deployment_cap_is_reported_BEFORE_the_per_symbol_caps():
    """If the book as a whole is full, which symbol you asked for is irrelevant -
    and the broader reason is the more useful log line."""
    book = [_pos("AAA", max_loss_total=3000.0)] * 3       # also breaches the symbol cap
    assert pc.concentration_reject(book, "AAA", "2026-10-16", 100.0,
                                   limits=DEPLOY, equity=10_000.0) == pc.DEPLOYMENT_CAP


def test_the_shipped_fraction_is_the_tight_end_of_the_published_range():
    import config_paper
    assert config_paper.MAX_DEPLOYED_RISK_PCT == 0.20
    assert pc.default_limits()["max_deployed_risk_pct"] == 0.20


def test_a_non_finite_position_cannot_switch_the_ceiling_off():
    """The documented pins-the-bound trap: a NaN total makes every ``>`` False.
    ``open_risk_dollars`` drops the row instead, so the rest still counts."""
    book = [_pos("AAA", max_loss_total=float("nan")),
            _pos("BBB", max_loss_total=2500.0)]
    assert pc.concentration_reject(book, "CCC", "2026-10-16", 100.0,
                                   limits=DEPLOY, equity=10_000.0) == pc.DEPLOYMENT_CAP


# ---- B3 end to end: the entry cycle supplies the denominator ----------------


def test_the_entry_cycle_refuses_a_signal_that_would_overcommit_the_book(tmp_path):
    """The cap is dead code unless the cycle passes an equity. Seeded so ONE more
    $250-risk spread crosses 20% of session-start equity."""
    import paper_account_db as pdb
    import paper_engine as pe
    from datetime import datetime
    from zoneinfo import ZoneInfo

    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 5_000.0, "2026-05-20")      # 20% = $1,000
    # 900 already committed, across DIFFERENT symbols and expiries so neither
    # per-symbol nor per-expiry cap can be what refuses.
    for i, sym in enumerate(("AAA", "BBB", "CCC")):
        pdb.reserve_buying_power(db, 300.0)
        pdb.insert_position(db, {
            "signal_id": f"s{i}", "symbol": sym, "strategy": "PCS",
            "short_strike": 100.0, "long_strike": 99.0, "call_short": None,
            "call_long": None, "width": 1.0,
            "expiration": f"2026-06-{10 + i:02d}", "dte_at_entry": 20,
            "quantity": 1, "entry_credit": 0.40, "entry_order_id": None,
            "max_loss_per": 300.0, "max_loss_total": 300.0, "entry_ts": "t"})

    class _Broker:
        def submit_order(self, order, client):
            return {"orderId": 1, "status": "FILLED", "orderType": "NET_CREDIT",
                    "quantity": order["quantity"], "filledQuantity": order["quantity"],
                    "price": 0.40, "enteredTime": "t", "closeTime": "t",
                    "orderStrategyType": "SINGLE",
                    "complexOrderStrategyType": "VERTICAL",
                    "orderLegCollection": [], "statusDescription": None}

    sig = {"signal_id": "new", "symbol": "DDD", "strategy": "PCS",
           "short_strike": 100.0, "long_strike": 98.0, "width": 2.0,
           "expiration": "2026-06-25", "dte_at_entry": 20,
           "entry_credit": 0.40, "entry_score": 80, "recommendation": None,
           "scanner_type": "SWING"}

    pe.run_entry_cycle(None, "2026-05-20", [sig], _Broker(), db)

    opened = [p["symbol"] for p in pdb.fetch_open_positions(db)]
    assert "DDD" not in opened, "the deployment cap did not bind"
    assert len(opened) == 3


def test_the_same_signal_opens_in_a_bigger_account(tmp_path):
    """The control. Identical book and signal against 4x the equity: the cap is
    about the FRACTION, not about this candidate being unopenable."""
    import paper_account_db as pdb
    import paper_engine as pe

    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 20_000.0, "2026-05-20")     # 20% = $4,000
    for i, sym in enumerate(("AAA", "BBB", "CCC")):
        pdb.reserve_buying_power(db, 300.0)
        pdb.insert_position(db, {
            "signal_id": f"s{i}", "symbol": sym, "strategy": "PCS",
            "short_strike": 100.0, "long_strike": 99.0, "call_short": None,
            "call_long": None, "width": 1.0,
            "expiration": f"2026-06-{10 + i:02d}", "dte_at_entry": 20,
            "quantity": 1, "entry_credit": 0.40, "entry_order_id": None,
            "max_loss_per": 300.0, "max_loss_total": 300.0, "entry_ts": "t"})

    class _Broker:
        def submit_order(self, order, client):
            return {"orderId": 1, "status": "FILLED", "orderType": "NET_CREDIT",
                    "quantity": order["quantity"], "filledQuantity": order["quantity"],
                    "price": 0.40, "enteredTime": "t", "closeTime": "t",
                    "orderStrategyType": "SINGLE",
                    "complexOrderStrategyType": "VERTICAL",
                    "orderLegCollection": [], "statusDescription": None}

    sig = {"signal_id": "new", "symbol": "DDD", "strategy": "PCS",
           "short_strike": 100.0, "long_strike": 98.0, "width": 2.0,
           "expiration": "2026-06-25", "dte_at_entry": 20,
           "entry_credit": 0.40, "entry_score": 80, "recommendation": None,
           "scanner_type": "SWING"}

    pe.run_entry_cycle(None, "2026-05-20", [sig], _Broker(), db)

    assert "DDD" in [p["symbol"] for p in pdb.fetch_open_positions(db)]
