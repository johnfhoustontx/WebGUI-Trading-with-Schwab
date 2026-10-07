"""``signal_recorder.record_tracked`` - recording structures to MEASURE them.

The Market Scanner's structures that are not credit spreads are written to
``signals.db`` under ``0DTE_STRUCT`` / ``SWING_STRUCT`` so each one gets a mark
series and an outcome. Nothing here is a trade: the paper Account refuses both
types and every structure but its three.

The risk in this function is UNITS and SIGN. A normalized candidate carries
per-CONTRACT dollars (``net_debit`` 540.0); the store carries per-SHARE values
(``entry_credit`` -5.40), and the realized-P&L formula, the R-multiple and every
display read the store's convention. A row 100x off would look like an
implausibly good strategy rather than a bug.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import signal_db
import signal_recorder as rec

TZ = ZoneInfo("America/Chicago")
RTH = datetime(2026, 10, 7, 10, 0, tzinfo=TZ)            # Wednesday, mid-session
PRE = datetime(2026, 10, 7, 8, 15, tzinfo=TZ)


def _leg(kind, side, strike, exp="2026-10-16", qty=1, mark=2.7):
    return {"kind": kind, "side": side, "strike": strike, "expiration": exp,
            "qty": qty, "mark": mark, "delta": 0.5, "theta": -0.1, "vega": 0.2,
            "gamma": 0.02, "iv": 28.0}


def _sig(structure="LONG_STRADDLE", symbol="SPY", score=55.0, **over):
    s = {"id": f"{symbol}_{structure}", "symbol": symbol, "type": structure,
         "family": "VOLATILITY", "group": "STRADDLE", "strategy_label": "Long Straddle",
         "bias": "neutral", "expiration": "2026-10-16", "dte": 9,
         "legs": [_leg("call", "long", 500.0), _leg("put", "long", 500.0)],
         "net_debit": 540.0, "net_credit": None, "max_profit": None,
         "max_loss": 542.6, "capital": 542.6, "breakevens": [494.6, 505.4],
         "unbounded": True, "unbounded_profit": True, "unbounded_loss": False,
         "rr": None, "pop_pct": 41.0, "net_delta": 0.01, "net_theta": -0.2,
         "net_vega": 0.4, "underlying_price": 500.0, "composite_score": score,
         "grade": "Marginal", "iv_rank": 44.0}
    s.update(over)
    return s


def _stored(db):
    return signal_db.get_open_signals(db_path=db, tracked=True)


def _one(db, sig, scanner_type="SWING_STRUCT"):
    assert rec.record_tracked([sig], scanner_type, db_path=db, now=RTH) == 1
    return _stored(db)[0]


# ── units and sign ──────────────────────────────────────────────────────────

def test_a_debit_is_stored_per_share_and_negative(tmp_path):
    row = _one(tmp_path / "s.db", _sig())
    assert row["entry_credit"] == pytest.approx(-5.40)
    assert row["entry_max_loss"] == pytest.approx(5.426)
    assert row["entry_capital"] == pytest.approx(5.426)
    assert row["entry_max_profit"] is None                 # unbounded profit
    assert row["unbounded"] == 0                           # the LOSS is bounded


def test_a_credit_is_stored_per_share_and_positive(tmp_path):
    sig = _sig("IRON_BUTTERFLY", net_debit=None, net_credit=310.0,
               max_profit=304.8, max_loss=195.2, capital=195.2,
               unbounded=False, unbounded_profit=False, group="BUTTERFLY")
    row = _one(tmp_path / "s.db", sig)
    assert row["entry_credit"] == pytest.approx(3.10)
    assert row["entry_max_profit"] == pytest.approx(3.048)
    assert row["entry_max_loss"] == pytest.approx(1.952)


def test_an_unbounded_loss_is_flagged_and_carries_its_capital(tmp_path):
    sig = _sig("SHORT_STRANGLE", net_debit=None, net_credit=135.0, max_profit=132.4,
               max_loss=2002.6, capital=2002.6, unbounded=True,
               unbounded_profit=False, unbounded_loss=True)
    row = _one(tmp_path / "s.db", sig)
    assert row["unbounded"] == 1
    # A margin estimate, not a real maximum - which is what the flag says.
    assert row["entry_max_loss"] == pytest.approx(20.026)
    assert row["entry_capital"] == pytest.approx(20.026)


def test_even_money_is_zero_not_missing(tmp_path):
    sig = _sig("CALL_BACKSPREAD", net_debit=None, net_credit=None, group="RATIO")
    assert _one(tmp_path / "s.db", sig)["entry_credit"] == 0.0


# ── the rest of the row ─────────────────────────────────────────────────────

def test_the_row_is_a_tracked_row_with_no_credit_spread_fields(tmp_path):
    row = _one(tmp_path / "s.db", _sig(spans_earnings=True))
    assert row["scanner_type"] == "SWING_STRUCT" and row["strategy"] == "LONG_STRADDLE"
    assert row["symbol"] == "SPY" and row["status"] == "OPEN"
    assert row["family"] == "STRADDLE" and row["mode"] == "TRACKED"
    assert row["expiration"] == "2026-10-16" and row["dte_at_entry"] == 9
    assert row["entry_score"] == 55.0 and row["entry_grade"] == "Marginal"
    assert row["entry_iv_rank"] == 44.0 and row["entry_underlying"] == 500.0
    assert row["entry_spans_earnings"] == 1
    assert row["first_seen_ts"] == RTH.isoformat()
    # Nothing a credit-spread reader would mistake for a strike or a delta.
    for key in ("short_strike", "long_strike", "call_short", "call_long", "width"):
        assert row[key] is None, key
    # None is "not recorded". A 0.0 here would arm a delta stop at entry.
    assert row["entry_short_delta"] is None
    assert row["entry_net_delta_position"] == 0.01
    assert row["entry_net_theta_position"] == -0.2


def test_the_legs_are_stored_and_nothing_but_what_a_mark_needs(tmp_path):
    cal = _sig("CALENDAR_PUT", group="CALENDAR", legs=[
        _leg("put", "short", 500.0, "2026-10-16", mark=4.1),
        _leg("put", "long", 500.0, "2026-11-13", mark=7.9)])
    legs = json.loads(_one(tmp_path / "s.db", cal)["legs_json"])
    assert legs == [
        {"kind": "put", "side": "short", "strike": 500.0, "expiration": "2026-10-16",
         "qty": 1, "entry_mark": 4.1},
        {"kind": "put", "side": "long", "strike": 500.0, "expiration": "2026-11-13",
         "qty": 1, "entry_mark": 7.9}]


def test_a_two_contract_leg_keeps_its_quantity(tmp_path):
    back = _sig("CALL_BACKSPREAD", group="RATIO", net_debit=None, net_credit=41.0,
                legs=[_leg("call", "short", 500.0), _leg("call", "long", 503.0, qty=2)])
    legs = json.loads(_one(tmp_path / "s.db", back)["legs_json"])
    assert [l["qty"] for l in legs] == [1, 2]


def test_a_directional_row_with_no_group_is_filed_as_directional(tmp_path):
    single = _sig("LONG_CALL", legs=[_leg("call", "long", 505.0)], family="DIRECTIONAL")
    del single["group"]
    assert _one(tmp_path / "s.db", single)["family"] == "DIRECTIONAL"


# ── what is refused rather than guessed ─────────────────────────────────────

@pytest.mark.parametrize("damage", [
    {"legs": []}, {"legs": None}, {"symbol": ""}, {"type": None},
    {"max_loss": None}, {"max_loss": 0}, {"max_loss": float("nan")},
    {"max_loss": -5.0}, {"expiration": None},
    {"legs": [{"kind": "call", "side": "long", "strike": None,
               "expiration": "2026-10-16", "qty": 1}]},
    {"legs": [{"kind": "stock", "side": "long", "strike": None,
               "expiration": None, "qty": 1}]},
    {"net_debit": float("nan")},
])
def test_a_row_that_cannot_be_measured_is_not_recorded(tmp_path, damage):
    """No risk figure means no R-multiple, and the dedup key is unique forever:
    a bad row would hold its slot and block the real one."""
    db = tmp_path / "s.db"
    assert rec.record_tracked([_sig(**damage)], "SWING_STRUCT", db_path=db, now=RTH) == 0
    assert _stored(db) == []


def test_only_the_two_tracked_types_are_accepted(tmp_path):
    db = tmp_path / "s.db"
    for bad in ("SWING", "0DTE", "INCOME", "", None, "swing_structx"):
        with pytest.raises(ValueError):
            rec.record_tracked([_sig()], bad, db_path=db, now=RTH)
    assert signal_db.get_open_signals(db_path=db) == []
    assert _stored(db) == []


# ── the same gates the credit recorder has ──────────────────────────────────

def test_nothing_is_recorded_outside_the_regular_session(tmp_path):
    db = tmp_path / "s.db"
    assert rec.record_tracked([_sig()], "SWING_STRUCT", db_path=db, now=PRE) == 0
    assert _stored(db) == []
    # ... which leaves the slot free for the real post-open sighting.
    assert rec.record_tracked([_sig()], "SWING_STRUCT", db_path=db, now=RTH) == 1


def test_the_same_structure_is_recorded_once(tmp_path):
    db = tmp_path / "s.db"
    assert rec.record_tracked([_sig()], "SWING_STRUCT", db_path=db, now=RTH) == 1
    assert rec.record_tracked([_sig(score=70.0)], "SWING_STRUCT", db_path=db, now=RTH) == 0
    assert len(_stored(db)) == 1


def test_a_different_strike_or_window_is_a_different_row(tmp_path, monkeypatch):
    monkeypatch.setattr(rec._scfg, "capture_max_open_per_symbol_tracked", lambda: 0)
    db = tmp_path / "s.db"
    other = _sig(legs=[_leg("call", "long", 505.0), _leg("put", "long", 505.0)])
    assert rec.record_tracked([_sig(), other], "SWING_STRUCT", db_path=db, now=RTH) == 2
    assert rec.record_tracked([_sig()], "0DTE_STRUCT", db_path=db, now=RTH) == 1


def test_the_key_does_not_depend_on_leg_order():
    a = _sig()
    b = _sig(legs=list(reversed(a["legs"])))
    assert rec.tracked_dedup_key(a, "SWING_STRUCT") == rec.tracked_dedup_key(b, "SWING_STRUCT")
    assert rec.tracked_dedup_key(a, "SWING_STRUCT") != rec.tracked_dedup_key(a, "0DTE_STRUCT")


def test_the_capture_floor_is_its_own_setting(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    monkeypatch.setattr(rec._scfg, "scores",
                        lambda: {"capture_min": 58, "capture_min_tracked": 60})
    assert rec.record_tracked([_sig(score=59.9)], "SWING_STRUCT", db_path=db, now=RTH) == 0
    assert rec.record_tracked([_sig(score=60.0)], "SWING_STRUCT", db_path=db, now=RTH) == 1


def test_the_shipped_floor_records_whatever_the_scan_showed(tmp_path):
    """The scan's own quality cut is the filter; a second number would drift."""
    assert rec._scfg.scores()["capture_min_tracked"] == 0
    assert _one(tmp_path / "s.db", _sig(score=50.1))["entry_score"] == 50.1


# ── the cap: its own pool ───────────────────────────────────────────────────

def _credit(symbol="SPY", short=690, score=70):
    return {"symbol": symbol, "type": "PCS", "short_strike": short,
            "long_strike": short - 2, "width": 2, "expiration": "2026-10-16", "dte": 9,
            "credit": 0.6, "max_loss": 1.4, "composite_score": score, "grade": "Good",
            "short_delta": -0.15, "net_theta": 5.0, "iv_rank": 30.0,
            "underlying_price": 700.0}


def test_the_tracked_cap_keeps_the_best_and_holds_across_scans(tmp_path, monkeypatch):
    monkeypatch.setattr(rec._scfg, "capture_max_open_per_symbol_tracked", lambda: 2)
    db = tmp_path / "s.db"
    sigs = [_sig("LONG_STRADDLE", score=52), _sig("BUTTERFLY_CALL", score=74),
            _sig("BULL_CALL", score=77), _sig("LONG_STRADDLE", symbol="QQQ", score=51)]
    assert rec.record_tracked(sigs, "SWING_STRUCT", db_path=db, now=RTH) == 3
    by = {(r["symbol"], r["strategy"]) for r in _stored(db)}
    assert by == {("SPY", "BULL_CALL"), ("SPY", "BUTTERFLY_CALL"), ("QQQ", "LONG_STRADDLE")}
    # A later scan cannot add a third SPY row, in either window.
    assert rec.record_tracked([_sig("CONDOR_PUT", score=99)], "0DTE_STRUCT",
                              db_path=db, now=RTH) == 0


def test_a_full_tracked_pool_never_blocks_a_credit_spread(tmp_path, monkeypatch):
    """The paper Account enters from the credit captures. A tracked straddle
    taking one of their slots would keep a tradeable signal out of the Account."""
    monkeypatch.setattr(rec._scfg, "capture_max_open_per_symbol_tracked", lambda: 2)
    monkeypatch.setattr(rec._scfg, "capture_max_open_per_symbol", lambda: 2)
    db = tmp_path / "s.db"
    assert rec.record_tracked([_sig("LONG_STRADDLE"), _sig("BULL_CALL")],
                              "SWING_STRUCT", db_path=db, now=RTH) == 2
    assert rec.record_signals([_credit(short=690), _credit(short=685)], "SWING",
                              db_path=db, now=RTH) == 2
    assert len(signal_db.get_open_signals(db_path=db)) == 2


def test_a_full_credit_pool_never_blocks_a_tracked_structure(tmp_path, monkeypatch):
    monkeypatch.setattr(rec._scfg, "capture_max_open_per_symbol_tracked", lambda: 2)
    monkeypatch.setattr(rec._scfg, "capture_max_open_per_symbol", lambda: 2)
    db = tmp_path / "s.db"
    assert rec.record_signals([_credit(short=690), _credit(short=685)], "SWING",
                              db_path=db, now=RTH) == 2
    assert rec.record_tracked([_sig()], "SWING_STRUCT", db_path=db, now=RTH) == 1


def test_a_cap_of_zero_is_no_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(rec._scfg, "capture_max_open_per_symbol_tracked", lambda: 0)
    db = tmp_path / "s.db"
    sigs = [_sig(t) for t in ("LONG_STRADDLE", "BULL_CALL", "BEAR_PUT", "CONDOR_CALL")]
    assert rec.record_tracked(sigs, "SWING_STRUCT", db_path=db, now=RTH) == 4


def test_an_unreadable_count_records_nothing(tmp_path, monkeypatch):
    """Fail closed, as the credit recorder does."""
    monkeypatch.setattr(signal_db, "count_open_by_symbol",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("locked")))
    db = tmp_path / "s.db"
    assert rec.record_tracked([_sig()], "SWING_STRUCT", db_path=db, now=RTH) == 0


def test_the_switch_turns_recording_off(tmp_path, monkeypatch):
    monkeypatch.setattr(rec._scfg, "capture_tracked_enabled", lambda: False)
    db = tmp_path / "s.db"
    assert rec.record_tracked([_sig()], "SWING_STRUCT", db_path=db, now=RTH) == 0
    assert _stored(db) == []


def test_a_failed_insert_is_counted_as_nothing_and_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "_insert", lambda row, db_path: (_ for _ in ()).throw(
        RuntimeError("disk")))
    assert rec.record_tracked([_sig()], "SWING_STRUCT", db_path=tmp_path / "s.db",
                              now=RTH) == 0


def test_the_default_path_is_resolved_when_called(monkeypatch, tmp_path):
    """A default bound at ``def`` time cannot be redirected by a test or a tool.

    The suite's conftest wraps ``record_tracked`` to hand it a per-test store,
    so the real function is reached through ``__wrapped__``."""
    real = rec.record_tracked.__wrapped__
    seen = {}
    monkeypatch.setattr(signal_db, "DEFAULT_DB_PATH", tmp_path / "elsewhere.db")
    monkeypatch.setattr(signal_db, "count_open_by_symbol", lambda **kw: {})
    monkeypatch.setattr(rec, "_insert",
                        lambda row, db_path: seen.setdefault("path", db_path) or True)
    real([_sig()], "SWING_STRUCT", now=RTH)
    assert seen["path"] == tmp_path / "elsewhere.db"
