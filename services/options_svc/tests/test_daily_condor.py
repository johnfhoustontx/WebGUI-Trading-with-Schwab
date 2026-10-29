"""The daily 0-DTE iron condor: the strikes it picks, the row it books, the day it
skips, and the slot that fires it.

The chain is synthetic: SPY at 580 with $1 strikes, a put's delta rising 0.035 a
strike toward the money and a call's the same, so the 0.15 shorts are the 570 put
and the 590 call. Each contract is quoted 0.05 either side of 4 x its delta.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import daily_condor, idea_ledger
from services.options_svc.tests.test_idea_ledger_wiring import ledger  # noqa: F401

_CT = ZoneInfo("America/Chicago")
TODAY = "2026-09-17"
NOW = dt.datetime(2026, 9, 17, 8, 45, tzinfo=_CT)
CFG = {"enabled": True, "symbol": "SPY", "short_delta": 0.15, "width": 5.0,
       "stop_mult": 2.0, "quantity": 1, "max_delta_miss": 0.05}


def _contract(kind, strike, delta):
    price = round(abs(delta) * 4, 2)
    return {"putCall": kind.upper(), "strikePrice": strike, "delta": delta,
            "bid": round(max(price - 0.05, 0.0), 2), "ask": round(price + 0.05, 2)}


def _chain(spot=580.0, overrides=None):
    """``overrides`` maps ``(kind, strike)`` to the fields to replace on it."""
    overrides = overrides or {}
    puts, calls = {}, {}
    for strike in range(555, 606):
        k = float(strike)
        p = -round(max(0.02, 0.5 - (spot - k) * 0.035), 4) if k <= spot else -0.6
        c = round(max(0.02, 0.5 - (k - spot) * 0.035), 4) if k >= spot else 0.6
        put, call = _contract("put", k, p), _contract("call", k, c)
        put.update(overrides.get(("put", k), {}))
        call.update(overrides.get(("call", k), {}))
        puts[f"{k:.1f}"], calls[f"{k:.1f}"] = [put], [call]
    key = f"{TODAY}:0"
    return {"symbol": "SPY", "underlyingPrice": spot,
            "putExpDateMap": {key: puts}, "callExpDateMap": {key: calls}}


# ── pick ────────────────────────────────────────────────────────────────────
def test_pick_takes_the_15_delta_shorts_and_5_dollar_wings():
    got = daily_condor.pick(_chain(), TODAY, CFG)
    assert "skip" not in got
    assert (got["put_short"], got["put_long"]) == (570.0, 565.0)
    assert (got["call_short"], got["call_long"]) == (590.0, 595.0)
    # each vertical: natural 0.42 x 0.62, worked 40% in -> 0.50; the two summed
    assert got["credit"] == pytest.approx(1.00)
    assert got["spot"] == 580.0
    assert got["put_delta"] == pytest.approx(-0.15)
    assert got["call_delta"] == pytest.approx(0.15)
    legs = {(lg["kind"], lg["side"]): lg for lg in got["legs"]}
    assert legs[("put", "short")]["strike"] == 570.0
    assert legs[("put", "long")]["strike"] == 565.0
    assert legs[("call", "short")]["strike"] == 590.0
    assert legs[("call", "long")]["strike"] == 595.0
    assert all(lg["expiration"] == TODAY and lg["qty"] == 1 for lg in got["legs"])


def test_pick_reads_the_configured_delta_and_width():
    got = daily_condor.pick(_chain(), TODAY, {**CFG, "short_delta": 0.22, "width": 3.0})
    assert (got["put_short"], got["put_long"]) == (572.0, 569.0)
    assert (got["call_short"], got["call_long"]) == (588.0, 591.0)


def test_pick_tie_takes_the_farther_strike():
    """570 unusable leaves 571 (0.185) and 569 (0.115) equally far from 0.15."""
    chain = _chain(overrides={("put", 570.0): {"delta": -999.0},
                              ("call", 590.0): {"delta": -999.0}})
    got = daily_condor.pick(chain, TODAY, CFG)
    assert got["put_short"] == 569.0 and got["call_short"] == 591.0


def test_pick_skips_when_no_usable_delta():
    chain = _chain()
    for name in ("putExpDateMap", "callExpDateMap"):
        for contracts in chain[name][f"{TODAY}:0"].values():
            contracts[0]["delta"] = -999.0
    assert daily_condor.pick(chain, TODAY, CFG) == {"skip": "no usable put delta"}


def test_pick_skips_when_the_nearest_usable_delta_is_too_far():
    """Schwab's -999 on every near strike must not pass a far 0.02 put off as the
    15-delta short: past ``max_delta_miss`` the day is a skip."""
    near = {("put", float(k)): {"delta": -999.0} for k in range(563, 580)}
    got = daily_condor.pick(_chain(overrides=near), TODAY, CFG)
    assert got == {"skip": "no put delta within 0.05 of 0.15"}


def test_pick_skips_without_a_wing_at_width():
    chain = _chain()
    del chain["callExpDateMap"][f"{TODAY}:0"]["595.0"]
    assert daily_condor.pick(chain, TODAY, CFG) == {"skip": "no call strike at 595.0"}


def test_pick_skips_a_leg_with_no_market():
    chain = _chain(overrides={("put", 565.0): {"bid": 0.0, "ask": 0.0}})
    assert daily_condor.pick(chain, TODAY, CFG) == {"skip": "no market on the 565.0 put"}


def test_pick_skips_an_empty_chain():
    assert daily_condor.pick({}, TODAY, CFG) == {"skip": "no chain for today"}
    assert daily_condor.pick(None, TODAY, CFG) == {"skip": "no chain for today"}


def test_pick_skips_a_condor_taken_in_for_nothing():
    """Every leg quoted alike leaves no credit: a condor that takes in nothing has no
    stop to measure against, so it is not booked."""
    flat = {"bid": 0.10, "ask": 0.20}
    chain = _chain(overrides={(k, s): flat for k in ("put", "call")
                              for s in (565.0, 570.0, 590.0, 595.0)})
    assert daily_condor.pick(chain, TODAY, CFG) == {"skip": "no credit"}


# ── build_row ───────────────────────────────────────────────────────────────
def test_build_row_sets_the_price_stop():
    """$1.00 in, $5.20 round trip: the stop fires when buying it back costs $3.00."""
    row = daily_condor.build_row(daily_condor.pick(_chain(), TODAY, CFG), NOW, CFG, 5.20)
    assert row["source"] == idea_ledger.CONDOR_SOURCE == "daily_condor"
    assert row["strategy"] == "IC" and row["trade_type"] == "0-DTE"
    assert row["symbol"] == "SPY" and row["expiration"] == TODAY
    assert row["status"] == "OPEN" and row["mode"] == "PAPER"
    assert row["entry_credit"] == pytest.approx(0.948)
    assert row["entry_credit_total"] == pytest.approx(94.8)
    assert row["stop_pnl"] == pytest.approx(205.2)
    assert row["target_pnl"] is None and row["mae_pnl"] == 0.0
    assert (row["short_strike"], row["long_strike"]) == (570.0, 565.0)
    assert (row["call_short"], row["call_long"], row["width"]) == (590.0, 595.0, 5.0)
    assert row["max_loss_per"] == pytest.approx(4.052)
    assert row["max_loss_total"] == pytest.approx(405.2)
    assert row["max_profit_total"] == pytest.approx(94.8)
    assert row["unbounded"] is False and row["direction"] is None
    assert row["breakeven"] == "569.05 / 590.95"
    assert row["underlying_at_entry"] == 580.0 and row["dte_at_entry"] == 0
    assert row["entry_time"] == NOW.isoformat(timespec="seconds")
    assert row["notes"] == "Daily SPY condor (put delta -0.15, call delta 0.15)"
    # the stop line is exactly a $3.00 cost to close
    from structure_marks import pnl_of
    assert pnl_of(row, 3.00) == pytest.approx(-row["stop_pnl"])


def test_build_row_scales_the_totals_by_quantity():
    row = daily_condor.build_row(daily_condor.pick(_chain(), TODAY, CFG), NOW,
                                 {**CFG, "quantity": 3}, 15.60)
    assert row["quantity"] == 3
    assert row["entry_credit"] == pytest.approx(0.948)       # per share, as before
    assert row["stop_pnl"] == pytest.approx(205.2)           # per contract
    assert row["entry_credit_total"] == pytest.approx(284.4)
    assert row["max_loss_total"] == pytest.approx(1215.6)


# ── book ────────────────────────────────────────────────────────────────────
@pytest.fixture
def fetched(monkeypatch):
    """``signal_repricer._fetch_chain`` scripted; every call recorded."""
    import signal_repricer
    state = {"chain": _chain(), "calls": []}

    def fake(client, symbol, expiration, max_age=None):
        state["calls"].append((client, symbol, expiration, max_age))
        return state["chain"]
    monkeypatch.setattr(signal_repricer, "_fetch_chain", fake)
    monkeypatch.setattr(signal_repricer, "clear_chain_cache", lambda: None)
    return state


@pytest.fixture
def cfg(monkeypatch):
    from shared import paper_limits
    state = dict(CFG)
    monkeypatch.setattr(paper_limits, "daily_condor", lambda: dict(state))
    return state


def test_book_books_one_condor_from_a_fresh_chain(ledger, fetched, cfg):  # noqa: F811
    out = daily_condor.book(NOW, client="CLIENT")
    assert out["status"] == "booked" and out["reason"] is None
    (row,) = ledger.get_all_trades()
    assert row["trade_id"] == out["trade_id"] and row["source"] == "daily_condor"
    assert idea_ledger.is_self_managed(row)
    assert fetched["calls"] == [("CLIENT", "SPY", TODAY, 0)]      # a fill: max_age 0


def test_book_refuses_a_second_condor_the_same_day(ledger, fetched, cfg):  # noqa: F811
    assert daily_condor.book(NOW, client="C")["status"] == "booked"
    later = NOW + dt.timedelta(minutes=5)
    assert daily_condor.book(later, client="C") == {
        "status": "duplicate", "trade_id": None, "reason": "already booked today"}
    assert len(ledger.get_all_trades()) == 1
    assert len(fetched["calls"]) == 1                              # no second fetch


def test_book_again_the_next_day(ledger, fetched, cfg):  # noqa: F811
    daily_condor.book(NOW, client="C")
    nxt = NOW + dt.timedelta(days=1)
    fetched["chain"] = _chain()
    for name in ("putExpDateMap", "callExpDateMap"):
        fetched["chain"][name] = {"2026-09-18:0": fetched["chain"][name][f"{TODAY}:0"]}
    assert daily_condor.book(nxt, client="C")["status"] == "booked"
    assert len(ledger.get_all_trades()) == 2


def test_book_disabled(ledger, fetched, cfg):  # noqa: F811
    cfg["enabled"] = False
    assert daily_condor.book(NOW, client="C") == {
        "status": "disabled", "trade_id": None, "reason": None}
    assert fetched["calls"] == [] and ledger.get_all_trades() == []


def test_book_skips_a_holiday(ledger, fetched, cfg):  # noqa: F811
    christmas = dt.datetime(2026, 12, 25, 8, 45, tzinfo=_CT)
    out = daily_condor.book(christmas, client="C")
    assert out["status"] == "skipped" and out["reason"] == "not a trading day"
    assert fetched["calls"] == []


def test_book_skips_with_the_pick_reason(ledger, fetched, cfg):  # noqa: F811
    fetched["chain"] = None
    out = daily_condor.book(NOW, client="C")
    assert out == {"status": "skipped", "trade_id": None, "reason": "no chain for today"}
    assert ledger.get_all_trades() == []


def test_book_takes_the_book_lock():
    import paper_lock
    assert paper_lock.is_serialized(daily_condor.book)


# ── the slot ────────────────────────────────────────────────────────────────
def _at(day, hh, mm):
    return dt.datetime(2026, 9, day, hh, mm, tzinfo=_CT)


@pytest.mark.parametrize("hh, mm, due", [
    (8, 44, False), (8, 45, True), (8, 54, True), (8, 55, False), (8, 56, False)])
def test_slot_due_inside_its_grace_only(hh, mm, due):
    from services.options_svc import scheduler
    assert (scheduler.daily_condor_due(_at(17, hh, mm), set()) == "at") is due


def test_slot_fires_once_a_day_and_never_on_a_weekend_or_holiday():
    from services.options_svc import scheduler
    ran = {("2026-09-17", "at")}
    assert scheduler.daily_condor_due(_at(17, 8, 46), ran) is None
    assert scheduler.daily_condor_due(_at(18, 8, 46), ran) == "at"
    assert scheduler.daily_condor_due(_at(19, 8, 46), set()) is None        # Saturday
    christmas = dt.datetime(2026, 12, 25, 8, 46, tzinfo=_CT)
    assert scheduler.daily_condor_due(christmas, set()) is None


class _Bus:
    def __init__(self):
        self.sets = []


def test_run_daily_condor_publishes_the_ledger_only_when_it_booked(monkeypatch):
    from services.options_svc import handlers
    published = []
    monkeypatch.setattr(handlers, "refresh_paper_trades",
                        lambda bus, **kw: published.append(kw))
    monkeypatch.setattr(daily_condor, "book",
                        lambda now: {"status": "booked", "trade_id": "x", "reason": None})
    assert handlers.run_daily_condor(_Bus())["status"] == "booked"
    assert published == [{"reprice": False, "keep_live_pnl": True}]
    monkeypatch.setattr(daily_condor, "book",
                        lambda now: {"status": "skipped", "trade_id": None, "reason": "r"})
    assert handlers.run_daily_condor(_Bus())["status"] == "skipped"
    assert len(published) == 1


def test_run_daily_condor_never_raises(monkeypatch):
    from services import _degrade
    from services.options_svc import handlers

    def boom(now):
        raise RuntimeError("proxy down")
    monkeypatch.setattr(daily_condor, "book", boom)
    _degrade.reset()
    assert handlers.run_daily_condor(_Bus()) == {"status": "failed", "trade_id": None,
                                                 "reason": "proxy down"}
    assert _degrade.counts().get("options.run_daily_condor") == 1
