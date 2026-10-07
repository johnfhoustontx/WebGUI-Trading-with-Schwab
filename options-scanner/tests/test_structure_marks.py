"""``structure_marks`` - a mark series and an outcome for a TRACKED structure.

Tracked rows are measured, never traded. What these tests pin is that the
measurement is honest: one sign convention for every structure, no mark from a
partial quote, no intrinsic settlement for a position whose back month still
has time value, and an exit that never reads a missing mark as a zero.
"""
import datetime as dt
import json
import math
from zoneinfo import ZoneInfo

import pytest

import signal_repricer as sr
import structure_marks as sm

CT = ZoneInfo("America/Chicago")
TODAY = dt.date(2026, 10, 7)
FRONT, BACK = "2026-10-16", "2026-11-13"


def _leg(kind, side, strike, exp=FRONT, qty=1):
    return {"kind": kind, "side": side, "strike": strike, "expiration": exp, "qty": qty}


def _row(legs, entry_credit, strategy="LONG_STRADDLE", **over):
    row = {"signal_id": "t1", "symbol": "SPY", "strategy": strategy,
           "scanner_type": "SWING_STRUCT", "expiration": FRONT,
           "entry_credit": entry_credit, "entry_max_loss": 5.4,
           "entry_max_profit": None, "unbounded": 0,
           "legs_json": json.dumps(legs)}
    row.update(over)
    return row


STRADDLE = [_leg("call", "long", 500.0), _leg("put", "long", 500.0)]
STRANGLE_SHORT = [_leg("call", "short", 510.0), _leg("put", "short", 490.0)]
FLY = [_leg("call", "long", 495.0), _leg("call", "short", 500.0, qty=2),
       _leg("call", "long", 505.0)]
VERTICAL = [_leg("call", "long", 500.0), _leg("call", "short", 505.0)]
CALENDAR = [_leg("put", "short", 500.0, FRONT), _leg("put", "long", 500.0, BACK)]
BACKSPREAD = [_leg("call", "short", 500.0), _leg("call", "long", 503.0, qty=2)]


# ── the legs ────────────────────────────────────────────────────────────────

def test_legs_are_read_from_the_stored_blob():
    assert sm.legs_of(_row(FLY, -1.2)) == FLY
    assert sm.legs_of({"legs_json": FLY}) == FLY            # already parsed


@pytest.mark.parametrize("blob", [
    None, "", "not json", "[]", "{}", "[1, 2]",
    json.dumps([{"kind": "stock", "side": "long", "strike": 1, "expiration": FRONT}]),
    json.dumps([{"kind": "call", "side": "long", "strike": None, "expiration": FRONT}]),
    json.dumps([{"kind": "call", "side": "long", "strike": float("nan"),
                 "expiration": FRONT}]).replace("NaN", "null"),
    json.dumps([{"kind": "call", "side": "flat", "strike": 5.0, "expiration": FRONT}]),
    json.dumps([{"kind": "call", "side": "long", "strike": 5.0, "expiration": ""}]),
])
def test_a_blob_that_cannot_be_used_is_no_legs_and_never_a_raise(blob):
    assert sm.legs_of({"legs_json": blob}) is None
    assert sm.legs_of(None) is None


def test_a_missing_or_silly_quantity_is_one_contract():
    legs = [dict(_leg("call", "long", 500.0), qty=q) for q in (None, 0, -2, "x", True)]
    assert [l["qty"] for l in sm.legs_of({"legs_json": legs})] == [1, 1, 1, 1, 1]


def test_two_expiries_are_told_apart_from_one():
    assert sm.is_two_expiry(CALENDAR) and not sm.is_two_expiry(FLY)
    assert sm.expirations(CALENDAR) == [FRONT, BACK]


# ── the one convention ──────────────────────────────────────────────────────

def test_cost_to_close_is_positive_for_a_short_and_negative_for_a_long():
    price = lambda leg: 2.0
    assert sm.cost_to_close(STRANGLE_SHORT, price) == 4.0     # buy both back
    assert sm.cost_to_close(STRADDLE, price) == -4.0          # sell both: worth 4
    assert sm.cost_to_close(FLY, price) == 0.0                # 1 - 2x1 + 1 at one price
    assert sm.cost_to_close(BACKSPREAD, price) == -2.0        # +2 - 2x2


@pytest.mark.parametrize("bad", [None, float("nan"), -0.5, "2.0"])
def test_one_leg_without_a_price_means_no_value_at_all(bad):
    prices = iter([2.0, bad])
    assert sm.cost_to_close(STRADDLE, lambda leg: next(prices)) is None


def test_pnl_is_the_stores_formula_for_a_debit_and_a_credit():
    # A straddle bought for 5.40, now worth 8.10: +$270.
    assert sm.pnl_of(_row(STRADDLE, -5.40), -8.10) == pytest.approx(270.0)
    # ... and worth 3.00: -$240.
    assert sm.pnl_of(_row(STRADDLE, -5.40), -3.00) == pytest.approx(-240.0)
    # A strangle sold for 1.35, costing 0.60 to buy back: +$75.
    assert sm.pnl_of(_row(STRANGLE_SHORT, 1.35), 0.60) == pytest.approx(75.0)
    assert sm.pnl_of(_row(STRADDLE, None), -8.10) is None
    assert sm.pnl_of(_row(STRADDLE, -5.40), None) is None


# ── a live mark ─────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


class _Client:
    """One chain per expiration; records what was asked for."""
    class Options:
        class ContractType:
            ALL = "ALL"

    def __init__(self, chains):
        self.chains, self.calls = chains, []

    def get_option_chain(self, symbol, from_date=None, to_date=None, **kw):
        self.calls.append((symbol, from_date.isoformat()))
        chain = self.chains.get(from_date.isoformat())
        return _Resp(chain) if chain is not None else _Resp(None, status=500)


def _c(bid, ask):
    return [{"bid": bid, "ask": ask, "delta": 0.5}]


def _chain(exp, calls=None, puts=None, spot=501.0):
    return {"underlyingPrice": spot,
            "callExpDateMap": {f"{exp}:9": calls or {}},
            "putExpDateMap": {f"{exp}:9": puts or {}}}


@pytest.fixture(autouse=True)
def _fresh_chain_cache():
    sr.clear_chain_cache()
    yield
    sr.clear_chain_cache()


def test_a_straddle_is_marked_from_both_legs():
    client = _Client({FRONT: _chain(FRONT, calls={"500.0": _c(4.0, 4.2)},
                                    puts={"500.0": _c(3.9, 4.1)})})
    rep = sm.reprice(_row(STRADDLE, -5.40), client, today=TODAY)
    assert rep["error"] is None
    assert rep["current_value"] == pytest.approx(-8.10)       # worth 4.10 + 4.00
    assert rep["unrealized_pnl"] == pytest.approx(270.0)
    assert rep["pnl_pct_of_credit"] == pytest.approx(50.0)    # of the 540 paid
    assert rep["current_underlying"] == 501.0
    assert rep["current_short_delta"] is None


def test_a_two_contract_body_counts_twice():
    client = _Client({FRONT: _chain(FRONT, calls={
        "495.0": _c(6.9, 7.1), "500.0": _c(3.9, 4.1), "505.0": _c(1.9, 2.1)})})
    rep = sm.reprice(_row(FLY, -1.20, "BUTTERFLY_CALL"), client, today=TODAY)
    # long 7.0, short 2 x 4.0, long 2.0 -> worth 1.0; paid 1.20 -> -$20.
    assert rep["current_value"] == pytest.approx(-1.0)
    assert rep["unrealized_pnl"] == pytest.approx(-20.0)


def test_a_calendar_reads_each_leg_on_its_own_expiration():
    client = _Client({FRONT: _chain(FRONT, puts={"500.0": _c(3.0, 3.2)}),
                      BACK: _chain(BACK, puts={"500.0": _c(7.4, 7.6)}, spot=999.0)})
    rep = sm.reprice(_row(CALENDAR, -3.80, "CALENDAR_PUT"), client, today=TODAY)
    assert rep["error"] is None
    assert sorted(c[1] for c in client.calls) == [FRONT, BACK]
    # short front 3.10 to buy back, long back 7.50 to sell: worth 4.40.
    assert rep["current_value"] == pytest.approx(-4.40)
    assert rep["unrealized_pnl"] == pytest.approx(60.0)
    assert rep["current_underlying"] == 501.0                 # the FRONT chain's


def test_a_quarter_point_strike_is_found():
    """``signal_repricer._leg_mid`` formats the strike to one decimal."""
    legs = [_leg("call", "long", 99.75), _leg("call", "short", 100.25)]
    client = _Client({FRONT: _chain(FRONT, calls={"99.75": _c(1.0, 1.2),
                                                  "100.25": _c(0.7, 0.9)})})
    rep = sm.reprice(_row(legs, -0.25, "BULL_CALL"), client, today=TODAY)
    assert rep["error"] is None and rep["current_value"] == pytest.approx(-0.30)


def test_a_short_structure_is_marked_as_a_cost():
    client = _Client({FRONT: _chain(FRONT, calls={"510.0": _c(0.2, 0.4)},
                                    puts={"490.0": _c(0.2, 0.4)})})
    rep = sm.reprice(_row(STRANGLE_SHORT, 1.35, "SHORT_STRANGLE"), client, today=TODAY)
    assert rep["current_value"] == pytest.approx(0.60)
    assert rep["unrealized_pnl"] == pytest.approx(75.0)


@pytest.mark.parametrize("calls,puts,error", [
    ({"500.0": _c(4.0, 4.2)}, {}, "missing leg quote"),                 # leg not listed
    ({"500.0": _c(4.0, 4.2)}, {"500.0": _c(0, 0)}, "missing leg quote"),  # no market
    ({"500.0": _c(4.0, 4.2)}, {"500.0": _c(0.0, 3.0)}, "missing leg quote"),  # broken
])
def test_a_leg_with_no_market_means_no_mark_never_a_partial_one(calls, puts, error):
    client = _Client({FRONT: _chain(FRONT, calls=calls, puts=puts)})
    rep = sm.reprice(_row(STRADDLE, -5.40), client, today=TODAY)
    assert rep["error"] == error
    assert rep["current_value"] is None and rep["unrealized_pnl"] is None


def test_a_zero_bid_under_a_small_offer_is_still_a_market():
    """A far out-of-the-money long near expiry: 0.00 x 0.05."""
    client = _Client({FRONT: _chain(FRONT, calls={"500.0": _c(2.0, 2.2),
                                                  "505.0": _c(0.0, 0.05)})})
    rep = sm.reprice(_row(VERTICAL, -1.00, "BULL_CALL"), client, today=TODAY)
    assert rep["error"] is None and rep["current_value"] == pytest.approx(-2.075)


def test_a_chain_that_does_not_come_back_is_no_mark():
    rep = sm.reprice(_row(CALENDAR, -3.8), _Client({FRONT: _chain(FRONT)}), today=TODAY)
    assert rep["error"] in ("no chain", "missing leg quote")
    assert rep["unrealized_pnl"] is None


def test_an_expired_front_leg_is_not_fetched():
    client = _Client({})
    rep = sm.reprice(_row(STRADDLE, -5.4), client, today=dt.date(2026, 10, 17))
    assert rep["error"] == "expired" and client.calls == []


def test_a_row_with_no_legs_is_no_mark_and_no_fetch():
    client = _Client({})
    assert sm.reprice({"signal_id": "x", "symbol": "SPY"}, client)["error"] == "no legs"
    assert client.calls == []


def test_a_client_that_raises_is_a_failed_mark_not_a_raise():
    class _Boom(_Client):
        def get_option_chain(self, *a, **k):
            raise RuntimeError("proxy down")
    rep = sm.reprice(_row(STRADDLE, -5.4), _Boom({}), today=TODAY)
    assert rep["error"] == "repricing failed" and rep["unrealized_pnl"] is None


# ── settlement ──────────────────────────────────────────────────────────────

def test_a_single_expiry_structure_settles_at_intrinsic():
    # Straddle at 500, underlying 512: the call is worth 12, the put nothing.
    assert sm.expiry_value(_row(STRADDLE, -5.4), 512.0) == -12.0
    assert sm.pnl_of(_row(STRADDLE, -5.4), -12.0) == pytest.approx(660.0)
    # Short strangle 490/510 at 500: both expire worthless, nothing to buy back.
    assert sm.expiry_value(_row(STRANGLE_SHORT, 1.35), 500.0) == 0.0
    # ... and at 520 the short call costs 10.
    assert sm.expiry_value(_row(STRANGLE_SHORT, 1.35), 520.0) == 10.0
    # Butterfly 495/500/505 pinned at the body: worth its wing.
    assert sm.expiry_value(_row(FLY, -1.2), 500.0) == -5.0
    assert sm.expiry_value(_row(FLY, -1.2), 520.0) == 0.0


def test_a_backspreads_worst_case_is_at_the_long_strike():
    row = _row(BACKSPREAD, 0.41, "CALL_BACKSPREAD")
    at_long = sm.pnl_of(row, sm.expiry_value(row, 503.0))
    assert at_long == pytest.approx(-259.0)                   # -(3.00 - 0.41) x 100
    for spot in (480.0, 500.0, 501.0, 506.0, 540.0):
        assert sm.pnl_of(row, sm.expiry_value(row, spot)) >= at_long, spot
    assert sm.pnl_of(row, sm.expiry_value(row, 480.0)) == pytest.approx(41.0)
    assert sm.pnl_of(row, sm.expiry_value(row, 540.0)) > 3000


def test_a_calendar_is_never_settled_at_intrinsic():
    """Its back month still has time value on the front leg's expiry day."""
    assert sm.expiry_value(_row(CALENDAR, -3.8, "CALENDAR_PUT"), 500.0) is None


@pytest.mark.parametrize("spot", [None, 0, -5.0, float("nan"), "500"])
def test_no_usable_price_defers_the_settlement(spot):
    assert sm.expiry_value(_row(STRADDLE, -5.4), spot) is None


def test_the_front_expiry_close_is_for_two_expiry_rows_on_that_day_from_that_hour():
    close = dt.time(14, 0)
    cal = _row(CALENDAR, -3.8, "CALENDAR_PUT")
    day = lambda h, m=0, d=16: dt.datetime(2026, 10, d, h, m, tzinfo=CT)
    assert sm.front_expiry_due(cal, day(14), close) is True
    assert sm.front_expiry_due(cal, day(14, 55), close) is True
    assert sm.front_expiry_due(cal, day(13, 59), close) is False     # too early
    assert sm.front_expiry_due(cal, day(14, d=15), close) is False   # the day before
    assert sm.front_expiry_due(cal, day(14, d=19), close) is False   # after it
    assert sm.front_expiry_due(_row(STRADDLE, -5.4), day(14), close) is False
    assert sm.front_expiry_due({"legs_json": "junk"}, day(14), close) is False


# ── the exit rules ──────────────────────────────────────────────────────────

def _rec(row, pnl, **kw):
    return sm.recommend(row, pnl, **kw)["code"]


@pytest.mark.parametrize("pnl", [None, float("nan"), "100", True])
def test_no_mark_holds_and_is_never_read_as_zero(pnl):
    row = _row(STRANGLE_SHORT, 1.35, "SHORT_STRANGLE", entry_max_profit=1.32)
    out = sm.recommend(row, pnl, front_expiry_day=True)
    assert out["code"] == "HOLD" and out["reason"] == "no mark yet"


def test_a_bounded_structure_targets_half_its_max_profit():
    fly = _row(FLY, -1.20, "BUTTERFLY_CALL", entry_max_profit=3.80)
    assert _rec(fly, 189.9) == "HOLD"
    assert _rec(fly, 190.0) == "TARGET_HIT"
    assert sm.recommend(fly, 190.0)["action"] == "TAKE_PROFIT"
    assert "max profit" in sm.recommend(fly, 190.0)["reason"]


def test_an_uncapped_debit_targets_half_of_what_was_paid():
    straddle = _row(STRADDLE, -5.40)
    assert _rec(straddle, 269.9) == "HOLD"
    assert _rec(straddle, 270.0) == "TARGET_HIT"
    assert "debit paid" in sm.recommend(straddle, 270.0)["reason"]


def test_an_uncapped_credit_has_no_target_and_is_held_to_expiry():
    """A call backspread entered for a credit. Half of its small credit is not
    what it was opened to measure."""
    back = _row(BACKSPREAD, 0.41, "CALL_BACKSPREAD")
    for pnl in (20.5, 41.0, 5000.0):
        assert _rec(back, pnl) == "HOLD", pnl


def test_a_debit_has_no_loss_stop():
    assert _rec(_row(STRADDLE, -5.40), -539.0) == "HOLD"
    assert _rec(_row(FLY, -1.2, "BUTTERFLY_CALL", entry_max_profit=3.8), -120.0) == "HOLD"


def test_a_short_strangle_is_stopped_at_twice_its_credit():
    strangle = _row(STRANGLE_SHORT, 1.35, "SHORT_STRANGLE", entry_max_profit=1.32,
                    unbounded=1)
    assert _rec(strangle, -269.9) == "HOLD"
    assert _rec(strangle, -270.0) == "MONEY_STOP"
    assert sm.recommend(strangle, -270.0)["action"] == "CUT"
    assert _rec(strangle, 66.0) == "TARGET_HIT"               # half of 1.32


def test_a_backspread_is_not_stopped_in_its_valley():
    """Its loss is small, defined, and where it sits until the move it is for."""
    back = _row(BACKSPREAD, 0.41, "CALL_BACKSPREAD")
    assert _rec(back, -259.0) == "HOLD"
    put = _row([_leg("put", "short", 500.0), _leg("put", "long", 497.0, qty=2)],
               0.24, "PUT_BACKSPREAD", entry_max_profit=95.2)
    assert _rec(put, -276.0) == "HOLD"


def test_the_stop_and_the_target_are_read_from_the_rules(monkeypatch):
    strangle = _row(STRANGLE_SHORT, 1.00, "SHORT_STRANGLE", entry_max_profit=1.00)
    monkeypatch.setattr(sm._trade_mgmt, "structure_rules",
                        lambda s: {"tp_frac": 0.25, "stop_mult": 1.5, "loss_rules": True})
    assert _rec(strangle, 25.0) == "TARGET_HIT" and _rec(strangle, 24.9) == "HOLD"
    assert _rec(strangle, -150.0) == "MONEY_STOP" and _rec(strangle, -149.9) == "HOLD"
    monkeypatch.setattr(sm._trade_mgmt, "structure_rules",
                        lambda s: {"tp_frac": 0.25, "stop_mult": 1.5, "loss_rules": False})
    assert _rec(strangle, -5000.0) == "HOLD"


@pytest.mark.parametrize("rules", [
    {"tp_frac": None, "stop_mult": None, "loss_rules": True},
    {"tp_frac": float("nan"), "stop_mult": float("nan"), "loss_rules": True},
    {"tp_frac": 0, "stop_mult": 2.0, "loss_rules": True},
    {},
])
def test_an_unusable_rule_fires_nothing(monkeypatch, rules):
    """A zero or NaN target would fire at break-even or never compare."""
    monkeypatch.setattr(sm._trade_mgmt, "structure_rules", lambda s: rules)
    strangle = _row(STRANGLE_SHORT, 1.00, "SHORT_STRANGLE", entry_max_profit=1.00)
    assert _rec(strangle, 0.0) == "HOLD"
    assert _rec(strangle, 99.0) == "HOLD"


def test_the_front_expiry_close_fires_win_or_lose_and_ahead_of_the_target():
    cal = _row(CALENDAR, -3.80, "CALENDAR_PUT", entry_max_profit=1.50)
    up = sm.recommend(cal, 500.0, front_expiry_day=True)
    down = sm.recommend(cal, -200.0, front_expiry_day=True)
    assert (up["code"], up["action"]) == ("FRONT_EXPIRY", "TAKE_PROFIT")
    assert (down["code"], down["action"]) == ("FRONT_EXPIRY", "CUT")
    assert _rec(cal, 74.9) == "HOLD" and _rec(cal, 75.0) == "TARGET_HIT"


def test_every_closing_code_is_named():
    assert set(sm.CLOSE_CODES) == {"TARGET_HIT", "MONEY_STOP", "FRONT_EXPIRY"}


# ── the mark row ────────────────────────────────────────────────────────────

NOW = dt.datetime(2026, 10, 7, 10, 5, tzinfo=CT)


def test_a_mark_row_carries_the_recommendation_and_no_score():
    rep = {"current_value": -8.10, "unrealized_pnl": 270.0, "pnl_pct_of_credit": 50.0,
           "current_underlying": 501.0, "current_short_delta": None, "error": None}
    mark = sm.build_mark(_row(STRADDLE, -5.40), rep, NOW)
    assert mark["signal_id"] == "t1" and mark["mark_date"] == "2026-10-07"
    assert mark["mark_ts"] == NOW.isoformat()
    assert mark["current_value"] == -8.10 and mark["unrealized_pnl"] == 270.0
    assert mark["recommendation"] == "TAKE_PROFIT"
    assert mark["recommendation_code"] == "TARGET_HIT"
    assert mark["current_score"] is None and mark["score_drift"] is None
    assert mark["current_short_delta"] is None


def test_a_failed_reprice_writes_no_mark():
    assert sm.build_mark(_row(STRADDLE, -5.4), {"error": "missing leg quote"}, NOW) is None
    assert sm.build_mark(_row(STRADDLE, -5.4), None, NOW) is None


def test_a_mark_row_is_accepted_by_the_store(tmp_path):
    import signal_db
    db = tmp_path / "s.db"
    signal_db.insert_signal({
        "signal_id": "t1", "scanner_type": "SWING_STRUCT", "symbol": "SPY",
        "strategy": "LONG_STRADDLE", "short_strike": None, "long_strike": None,
        "call_short": None, "call_long": None, "width": None, "expiration": FRONT,
        "dte_at_entry": 9, "entry_credit": -5.4, "entry_max_loss": 5.43,
        "entry_score": 55, "entry_grade": "Marginal", "entry_short_delta": None,
        "entry_net_theta": None, "entry_iv_rank": 40, "entry_underlying": 500,
        "first_seen_ts": NOW.isoformat(), "first_seen_date": "2026-10-07",
        "dedup_key": "k", "status": "OPEN"}, db_path=db)
    rep = {"current_value": -6.0, "unrealized_pnl": 60.0, "pnl_pct_of_credit": 11.1,
           "current_underlying": 501.0, "current_short_delta": None, "error": None}
    assert signal_db.insert_marks([sm.build_mark(_row(STRADDLE, -5.4), rep, NOW)],
                                  db_path=db) == 1
    row = signal_db.get_open_signals_with_latest_mark(db_path=db, tracked=True)[0]
    assert row["unrealized_pnl"] == 60.0 and row["recommendation"] == "HOLD"
    assert not math.isnan(row["current_value"])
