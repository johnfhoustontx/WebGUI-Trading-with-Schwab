"""Expiry settlement: WHEN a position settles and against WHICH price.

Audit AC-01 / AC-02 (2026-10-03). Every book decided this separately and each
got it wrong: the paper Account and Ledger ran 09:00-14:00 CT only, so a
position could never settle on its expiry day and settled the next trading
morning against THAT morning's live quote; captured signals closed as EXPIRED
on expiry morning.

One rule, in ``paper_engine``:

* on the expiry day, at or after the 15:00 CT close: the regular-session last
  from a direct quote;
* on any later day: the EXPIRATION DATE's daily close, never a live quote;
* no usable price: defer (None), never guess.

Expected dollar figures are worked by hand from the payoff, not read back off
the code under test.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import paper_account_db as pdb
import paper_engine as pe

_CT = ZoneInfo("America/Chicago")

# 2026-06-05 is a Friday; 2026-06-08 the Monday after it.
FRIDAY, MONDAY = "2026-06-05", "2026-06-08"


def _candle(day, close):
    """A Schwab daily candle: stamped at midnight Central of its session."""
    y, m, d = (int(p) for p in day.split("-"))
    ms = int(datetime(y, m, d, tzinfo=_CT).timestamp() * 1000)
    return {"datetime": ms, "open": close, "high": close, "low": close,
            "close": close, "volume": 1}


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


class _Client:
    """A quote that says one thing and a daily history that says another, so a
    test can tell which of the two a settlement read."""

    def __init__(self, *, last=None, regular_last=None, candles=None,
                 history_status=200):
        self.last, self.regular_last = last, regular_last
        self.candles = candles or []
        self.history_status = history_status
        self.quote_calls, self.history_calls = [], []

    def get_quotes(self, syms):
        self.quote_calls.append(list(syms))
        block = {"quote": {"lastPrice": self.last}}
        if self.regular_last is not None:
            block["regular"] = {"regularMarketLastPrice": self.regular_last}
        return _Resp({syms[0]: block})

    def get_price_history_every_day(self, symbol):
        self.history_calls.append(symbol)
        return _Resp({"candles": self.candles}, self.history_status)


# ── the pure candle read ─────────────────────────────────────────────────────

def test_close_on_date_reads_the_session_whose_central_date_matches():
    candles = [_candle("2026-06-04", 501.0), _candle(FRIDAY, 505.0),
               _candle(MONDAY, 480.0)]
    assert pe.close_on_date(candles, FRIDAY) == 505.0


def test_close_on_date_is_none_when_that_session_is_not_in_the_series():
    assert pe.close_on_date([_candle(MONDAY, 480.0)], FRIDAY) is None
    assert pe.close_on_date([], FRIDAY) is None
    assert pe.close_on_date(None, FRIDAY) is None


@pytest.mark.parametrize("bad", [None, 0, 0.0, -1.0, float("nan"), float("inf"), "x"])
def test_close_on_date_refuses_an_unusable_close(bad):
    c = _candle(FRIDAY, 505.0)
    c["close"] = bad
    assert pe.close_on_date([c], FRIDAY) is None


# ── which price ──────────────────────────────────────────────────────────────

def test_expiry_day_settles_against_the_quote():
    client = _Client(last=505.0, candles=[_candle(FRIDAY, 111.0)])
    assert pe.settlement_underlying(client, "SPY", FRIDAY, FRIDAY) == 505.0
    assert client.history_calls == []


def test_expiry_day_prefers_the_regular_session_last_over_an_after_hours_print():
    # At 15:05 CT an equity's lastPrice can already be an after-hours trade.
    client = _Client(last=507.4, regular_last=505.0)
    assert pe.settlement_underlying(client, "SPY", FRIDAY, FRIDAY) == 505.0


def test_a_past_expiry_settles_against_that_dates_close_not_the_live_quote():
    client = _Client(last=480.0, candles=[_candle(FRIDAY, 505.0),
                                          _candle(MONDAY, 481.0)])
    assert pe.settlement_underlying(client, "SPY", FRIDAY, MONDAY) == 505.0
    assert client.quote_calls == []


def test_a_past_expiry_with_no_close_for_that_date_defers():
    client = _Client(last=480.0, candles=[_candle(MONDAY, 481.0)])
    assert pe.settlement_underlying(client, "SPY", FRIDAY, MONDAY) is None
    assert client.quote_calls == []          # never falls back to the live quote


def test_a_past_expiry_defers_when_the_history_fetch_fails():
    client = _Client(last=480.0, history_status=502)
    assert pe.settlement_underlying(client, "SPY", FRIDAY, MONDAY) is None


def test_a_past_expiry_maps_spx_to_its_index_symbol():
    client = _Client(candles=[_candle(FRIDAY, 5050.0)])
    assert pe.settlement_underlying(client, "SPX", FRIDAY, MONDAY) == 5050.0
    assert client.history_calls == ["$SPX"]


def test_an_injected_close_source_is_used_for_a_past_expiry():
    seen = []

    def close_fn(symbol, day):
        seen.append((symbol, day))
        return 505.0

    client = _Client(last=480.0)
    assert pe.settlement_underlying(client, "SPY", FRIDAY, MONDAY,
                                    close_fn=close_fn) == 505.0
    assert seen == [("SPY", FRIDAY)]
    assert client.history_calls == []


def test_a_malformed_expiration_defers():
    assert pe.settlement_underlying(_Client(last=505.0), "SPY", None, FRIDAY) is None
    assert pe.settlement_underlying(_Client(last=505.0), "SPY", "nope", FRIDAY) is None


# ── the Account, end to end ──────────────────────────────────────────────────

def _open_pcs(db, expiration, *, short=500.0, long=495.0, credit=1.00, qty=1):
    risk = round((short - long - credit) * 100 * qty, 2)
    pdb.reserve_buying_power(db, risk)
    return pdb.insert_position(db, {
        "signal_id": None, "symbol": "SPY", "strategy": "PCS",
        "short_strike": short, "long_strike": long, "call_short": None,
        "call_long": None, "width": short - long, "expiration": expiration,
        "dte_at_entry": 3, "quantity": qty, "entry_credit": credit,
        "entry_order_id": None, "max_loss_per": risk / qty,
        "max_loss_total": risk, "entry_ts": "2026-06-02T09:00:00"})


def _no_reprice(monkeypatch):
    monkeypatch.setattr(
        pe.signal_repricer, "reprice_swing",
        lambda trade, client: {"current_value": None, "unrealized_pnl": None,
                               "pnl_pct_of_credit": None, "current_underlying": None,
                               "current_short_delta": None, "error": "expired"})


def test_monday_manage_cycle_settles_fridays_expiry_at_fridays_close(tmp_path, monkeypatch):
    """The audit's reproduction. A 500/495 put credit spread sold for 1.00 that
    expired Friday with SPY at 505 kept its whole credit: +$100, less the $1.30
    it cost to open = +$98.70. Monday morning SPY is 480; settling against that
    books the full loss instead: (1.00 - 5.00) x 100 - 1.30 = -$401.30."""
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-02")
    _open_pcs(db, FRIDAY)
    _no_reprice(monkeypatch)
    client = _Client(last=480.0, candles=[_candle(FRIDAY, 505.0)])
    pe.run_manage_cycle(client, MONDAY, db_path=db,
                        now_ct=datetime(2026, 6, 8, 9, 0, tzinfo=_CT))
    assert pdb.fetch_open_positions(db) == []
    assert pdb.get_account(db)["realized_pnl"] == pytest.approx(98.70)


def test_a_past_expiry_with_no_close_stays_open(tmp_path, monkeypatch):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-02")
    _open_pcs(db, FRIDAY)
    _no_reprice(monkeypatch)
    client = _Client(last=480.0, candles=[])
    pe.run_manage_cycle(client, MONDAY, db_path=db,
                        now_ct=datetime(2026, 6, 8, 9, 0, tzinfo=_CT))
    assert len(pdb.fetch_open_positions(db)) == 1
    assert pdb.get_account(db)["realized_pnl"] == 0.0


# ── the settle-only pass (the 15:05 CT slot) ─────────────────────────────────

def _reprice_must_not_run(monkeypatch):
    def boom(trade, client):
        raise AssertionError("the settle pass must not reprice or run exit rules")
    monkeypatch.setattr(pe.signal_repricer, "reprice_swing", boom)


def test_settle_cycle_settles_todays_expiry_after_the_close(tmp_path, monkeypatch):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-02")
    _open_pcs(db, FRIDAY)
    _reprice_must_not_run(monkeypatch)
    n = pe.run_settle_cycle(_Client(last=505.0), FRIDAY, db_path=db,
                            now_ct=datetime(2026, 6, 5, 15, 5, tzinfo=_CT))
    assert n == 1
    assert pdb.fetch_open_positions(db) == []
    acct = pdb.get_account(db)
    assert acct["realized_pnl"] == pytest.approx(98.70)
    assert acct["buying_power_reserved"] == 0.0


def test_settle_cycle_does_nothing_before_the_close(tmp_path, monkeypatch):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-02")
    _open_pcs(db, FRIDAY)
    _reprice_must_not_run(monkeypatch)
    n = pe.run_settle_cycle(_Client(last=505.0), FRIDAY, db_path=db,
                            now_ct=datetime(2026, 6, 5, 14, 59, tzinfo=_CT))
    assert n == 0
    assert len(pdb.fetch_open_positions(db)) == 1


def test_settle_cycle_leaves_a_position_that_has_not_expired(tmp_path, monkeypatch):
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-02")
    _open_pcs(db, "2026-06-19")
    _reprice_must_not_run(monkeypatch)
    n = pe.run_settle_cycle(_Client(last=400.0), FRIDAY, db_path=db,
                            now_ct=datetime(2026, 6, 5, 15, 5, tzinfo=_CT))
    assert n == 0
    assert len(pdb.fetch_open_positions(db)) == 1


def test_settle_cycle_books_an_in_the_money_spread_at_its_intrinsic(tmp_path, monkeypatch):
    # 500/495 for 1.00, SPY closes 497: short put worth 3.00, long worthless.
    # (1.00 - 3.00) x 100 = -$200, less the $1.30 opening commission.
    db = str(tmp_path / "acct.db")
    pdb.ensure_account(db, 25_000.0, "2026-06-02")
    _open_pcs(db, FRIDAY)
    _reprice_must_not_run(monkeypatch)
    pe.run_settle_cycle(_Client(last=497.0), FRIDAY, db_path=db,
                        now_ct=datetime(2026, 6, 5, 15, 5, tzinfo=_CT))
    assert pdb.get_account(db)["realized_pnl"] == pytest.approx(-201.30)


# ── what a CAPTURED signal is worth at expiry ────────────────────────────────
# ``intrinsic_value`` books a single-leg short at ZERO whatever the close, and
# for the Account that is deliberate: an in-the-money cash-secured put is
# ASSIGNED at the strike, so its loss lives in the share lot. A captured signal
# has no lot - it is an outcome row for calibration - so there the option's own
# intrinsic is the whole result, and a zero would book every assigned put as a
# full win.
import signal_repricer as sr


def _trade(strategy, **kw):
    base = {"strategy": strategy, "short_strike": 100.0, "long_strike": 95.0,
            "call_short": None, "call_long": None, "entry_credit": 1.0}
    base.update(kw)
    return base


@pytest.mark.parametrize("spot,expected", [(105.0, 0.0), (100.0, 0.0),
                                           (97.0, 3.0), (90.0, 5.0)])
def test_expiry_value_put_credit_spread(spot, expected):
    assert sr.expiry_value(_trade("PCS"), spot) == pytest.approx(expected)


@pytest.mark.parametrize("spot,expected", [(95.0, 0.0), (103.0, 3.0), (120.0, 5.0)])
def test_expiry_value_call_credit_spread(spot, expected):
    t = _trade("CCS", short_strike=100.0, long_strike=105.0)
    assert sr.expiry_value(t, spot) == pytest.approx(expected)


@pytest.mark.parametrize("spot,expected", [(100.0, 0.0), (93.0, 2.0), (80.0, 5.0),
                                           (108.0, 3.0), (130.0, 5.0)])
def test_expiry_value_iron_condor(spot, expected):
    t = _trade("IC", short_strike=95.0, long_strike=90.0,
               call_short=105.0, call_long=110.0)
    assert sr.expiry_value(t, spot) == pytest.approx(expected)


@pytest.mark.parametrize("strategy", ["SHORT_PUT", "NAKED_PUT"])
@pytest.mark.parametrize("spot,expected", [(104.0, 0.0), (100.0, 0.0), (92.5, 7.5)])
def test_expiry_value_single_short_put_is_its_own_intrinsic(strategy, spot, expected):
    t = _trade(strategy, long_strike=None)
    assert sr.expiry_value(t, spot) == pytest.approx(expected)


@pytest.mark.parametrize("spot,expected", [(96.0, 0.0), (100.0, 0.0), (106.0, 6.0)])
def test_expiry_value_covered_call_is_the_call_legs_intrinsic(spot, expected):
    t = _trade("COVERED_CALL", long_strike=None, call_short=100.0)
    assert sr.expiry_value(t, spot) == pytest.approx(expected)


def test_expiry_value_refuses_a_structure_it_cannot_value():
    # None, not 0.0: a zero books the full credit as a win.
    assert sr.expiry_value(_trade("LONG_CALL"), 100.0) is None
    assert sr.expiry_value(_trade(None), 100.0) is None


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), 0.0, -5.0, "x"])
def test_expiry_value_refuses_an_unusable_settlement(bad):
    assert sr.expiry_value(_trade("PCS"), bad) is None


def test_expiry_value_refuses_a_missing_strike():
    assert sr.expiry_value(_trade("PCS", long_strike=None), 97.0) is None
    assert sr.expiry_value(_trade("IC", call_short=None, call_long=None), 97.0) is None


# ── the outcome row records what it settled against ──────────────────────────

def test_a_settled_outcome_records_its_settlement_underlying(tmp_path):
    import signal_db
    db = str(tmp_path / "signals.db")
    signal_db.init_schema(db_path=db)
    conn = signal_db.connect(db)
    try:
        conn.execute(
            "INSERT INTO signals (signal_id, scanner_type, symbol, strategy, expiration, "
            "entry_credit, status) VALUES ('S1', 'SWING', 'SPY', 'PCS', ?, 1.0, 'OPEN')",
            (FRIDAY,))
        conn.commit()
    finally:
        conn.close()
    signal_db.close_signal_manually("S1", 3.0, "EXPIRED", db_path=db,
                                    settlement_underlying=497.0)
    conn = signal_db.connect(db)
    try:
        row = conn.execute("SELECT exit_value, realized_pnl, exit_reason, "
                           "settlement_underlying FROM signal_outcomes "
                           "WHERE signal_id='S1'").fetchone()
    finally:
        conn.close()
    assert tuple(row) == (3.0, -200.0, "EXPIRED", 497.0)
