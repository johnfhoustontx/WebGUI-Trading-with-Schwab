"""A CCS conversion destroyed its own call legs — found 2026-09-12 measuring D2.

``_apply_convert`` adds the opposite side and relabels the position ``IC``. For a
PCS that is right: the put strikes stay in ``short_strike``/``long_strike`` and
the new call legs go to ``call_short``/``call_long``.

⚠ **For a CCS it was data loss.** A standalone CCS keeps its strikes in
``short_strike``/``long_strike`` (read off the CALL map — only an IC uses
``call_short``), and the CCS branch wrote the NEW PUT strikes straight over them
without moving the calls anywhere. The original call legs simply vanished.

The consequence is worse than a wrong number: ``reprice_swing``'s IC branch needs
all four strikes, so ``_leg_bid_ask(cm, None)`` fails and the position becomes
**unmarkable** — no mark, no exit rule, no P&L, until it expires.

One real occurrence on prod: manual position **403** (SPY), a
``convert_butterfly`` applied 2026-06-29, left as ``puts 747.0/746.0, calls
None/None`` and ultimately status ``EXPIRED``.
"""
import pytest

import paper_account_db
import paper_adjust


@pytest.fixture
def book(tmp_path):
    db = tmp_path / "paper.db"
    paper_account_db.init_db(db)
    paper_account_db.ensure_account(db, starting_balance=25000.0)
    return db


def _open(db, strategy, short, long_, credit=1.00, qty=1):
    pid = paper_account_db.insert_position(db, {
        "signal_id": "sig1", "symbol": "SPY", "strategy": strategy,
        "short_strike": short, "long_strike": long_, "width": abs(short - long_),
        "expiration": "2026-12-18", "dte_at_entry": 30, "quantity": qty,
        "entry_credit": credit, "max_loss_per": abs(short - long_) - credit,
        "max_loss_total": (abs(short - long_) - credit) * 100 * qty,
        "status": "OPEN"})
    return _row(db, pid)


def _row(db, pid):
    """One position row by id. ``paper_account_db`` exposes no single-row getter,
    so this reads through the all-positions fetch the engine itself uses."""
    for r in paper_account_db.fetch_all_positions(db):
        if r["position_id"] == pid:
            return dict(r)
    raise AssertionError("position %s not found" % pid)


def _candidate(action, legs, new_ml=300.0):
    return {"action": action, "gross_cash": 100.0, "commission": 2.6,
            "net_cash": 97.4, "new_max_loss": new_ml, "est_fill_legs": legs}


def _leg(side, right, strike, price):
    return {"side": side, "right": right, "strike": strike,
            "expiry": "2026-12-18", "qty": 1, "price": price}


def test_a_CCS_conversion_KEEPS_its_original_call_legs(book):
    """⚠ The regression. Before the fix, ``short_strike``/``long_strike`` were
    overwritten with the new PUT strikes and the calls were never written
    anywhere, so all four-strike readers saw two NULLs."""
    pos = _open(book, "CCS", 747.0, 748.0)
    cand = _candidate("convert_butterfly",
                      [_leg("SELL", "PUT", 747.0, 9.02),
                       _leg("BUY", "PUT", 746.0, 8.43)])
    res = paper_adjust.apply_convert_butterfly(book, pos, cand)
    assert res["ok"], res

    row = _row(book, pos["position_id"])
    assert row["strategy"] == "IC"
    # The ADDED put legs.
    assert row["short_strike"] == pytest.approx(747.0)
    assert row["long_strike"] == pytest.approx(746.0)
    # The ORIGINAL call legs, preserved rather than destroyed.
    assert row["call_short"] == pytest.approx(747.0)
    assert row["call_long"] == pytest.approx(748.0)


def test_the_converted_CCS_is_a_real_iron_butterfly(book):
    """Coincident shorts at 747 with wings either side — which is what the
    "Convert to Iron Butterfly" button says it builds."""
    pos = _open(book, "CCS", 747.0, 748.0)
    paper_adjust.apply_convert_butterfly(
        book, pos, _candidate("convert_butterfly",
                              [_leg("SELL", "PUT", 747.0, 9.02),
                               _leg("BUY", "PUT", 746.0, 8.43)]))
    row = _row(book, pos["position_id"])
    assert row["short_strike"] == row["call_short"]


def test_a_converted_CCS_can_still_be_PRICED(book):
    """The consequence that mattered: the IC branch needs all four strikes, so a
    NULL call leg made the position unmarkable — no mark, no exits, until expiry.
    Driven through ``position_greeks``, which reads the same four fields."""
    import signal_repricer as sr
    pos = _open(book, "CCS", 747.0, 748.0)
    paper_adjust.apply_convert_butterfly(
        book, pos, _candidate("convert_butterfly",
                              [_leg("SELL", "PUT", 747.0, 9.02),
                               _leg("BUY", "PUT", 746.0, 8.43)]))
    row = _row(book, pos["position_id"])
    g = {"delta": -0.50, "gamma": 0.02, "theta": -0.30, "vega": 0.20}
    w = {"delta": -0.45, "gamma": 0.02, "theta": -0.28, "vega": 0.19}
    chain = {"underlyingPrice": 747.0,
             "putExpDateMap": {"2026-12-18:90": {"747.0": [g], "746.0": [w]}},
             "callExpDateMap": {"2026-12-18:90": {
                 "747.0": [{"delta": 0.50, "gamma": 0.02, "theta": -0.30,
                            "vega": 0.20}],
                 "748.0": [{"delta": 0.45, "gamma": 0.02, "theta": -0.28,
                            "vega": 0.19}]}}}
    greeks = sr.position_greeks(row, chain)
    assert greeks["net_theta"] is not None
    assert greeks["net_theta"] > 0          # a credit body earns time


def test_a_PCS_conversion_was_already_correct_and_stays_so(book):
    """The PCS path kept its put strikes and added the calls — unchanged."""
    pos = _open(book, "PCS", 740.0, 739.0)
    res = paper_adjust.apply_convert_ic(
        book, pos, _candidate("convert_ic",
                              [_leg("SELL", "CALL", 760.0, 1.10),
                               _leg("BUY", "CALL", 761.0, 0.70)]))
    assert res["ok"], res
    row = _row(book, pos["position_id"])
    assert row["short_strike"] == pytest.approx(740.0)
    assert row["long_strike"] == pytest.approx(739.0)
    assert row["call_short"] == pytest.approx(760.0)
    assert row["call_long"] == pytest.approx(761.0)


def test_a_CCS_convert_to_IC_preserves_the_calls_too(book):
    """Same branch, same bug — ``convert_ic`` and ``convert_butterfly`` share
    ``_apply_convert``, so both were affected on the CCS side."""
    pos = _open(book, "CCS", 747.0, 748.0)
    paper_adjust.apply_convert_ic(
        book, pos, _candidate("convert_ic",
                              [_leg("SELL", "PUT", 730.0, 1.20),
                               _leg("BUY", "PUT", 729.0, 0.80)]))
    row = _row(book, pos["position_id"])
    assert row["call_short"] == pytest.approx(747.0)
    assert row["call_long"] == pytest.approx(748.0)
    assert row["short_strike"] == pytest.approx(730.0)


def test_a_conversion_that_adds_no_legs_changes_no_strikes(book):
    """Defensive: a candidate with no parseable legs must not blank anything."""
    pos = _open(book, "CCS", 747.0, 748.0)
    paper_adjust.apply_convert_butterfly(book, pos,
                                         _candidate("convert_butterfly", []))
    row = _row(book, pos["position_id"])
    assert row["short_strike"] == pytest.approx(747.0)
    assert row["long_strike"] == pytest.approx(748.0)
