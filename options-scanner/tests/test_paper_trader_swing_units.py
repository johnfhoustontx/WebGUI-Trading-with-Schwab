"""Ledger unit-correctness for SWING-originated credit spreads.

A raw ``scanner_engine`` signal carries ``credit`` and ``max_loss`` in PER-SHARE
dollars. A swing signal that has been through ``strategy_scanner._normalize_credit``
carries ``credit`` per-SHARE but ``max_loss`` in PER-CONTRACT dollars (x100, with
round-trip commission folded in) — a deliberate asymmetry there, because the
Strategy Finder table renders ``max_loss`` as dollars.

``paper_trader.create_paper_trade`` assumed per-SHARE for both, so a swing-sent
credit spread was recorded with 100x its real risk. These tests pin the corrected
scale AND pin the raw-scanner path as byte-identical, since the fix must not move it.
"""
import datetime

import paper_trader
import strategy_scanner

_EXP = (datetime.date.today() + datetime.timedelta(days=12)).isoformat()

# The ledger fields whose VALUE the economics of a credit spread determine. The
# characterization test pins these; volatile fields (trade_id, entry_time) are excluded.
_ECONOMIC_FIELDS = (
    "symbol", "strategy", "trade_type", "expiration", "dte_at_entry",
    "short_strike", "long_strike", "width", "quantity",
    "entry_credit", "entry_credit_total", "max_loss_per", "max_loss_total",
    "breakeven", "short_delta", "net_theta", "underlying_at_entry",
)


def _raw_pcs():
    """A PCS exactly as ``scanner_engine.screen_spreads`` emits it: PER-SHARE economics.

    $5-wide SPY put credit spread, $1.55 credit -> $3.45 per-share max loss
    ($345 per contract).
    """
    return {
        "id": "SPY_PCS_%s_600.0_595.0" % _EXP,
        "symbol": "SPY", "type": "PCS", "trade_type": "SWING",
        "expiration": _EXP, "dte": 12,
        "short_strike": 600.0, "long_strike": 595.0, "width": 5.0,
        "short_mark": 1.90, "long_mark": 0.35,
        "credit": 1.55, "max_loss": 3.45, "rr_pct": 44.9,
        "pop_pct": 70.0, "short_delta": -0.30,
        "net_theta": 0.12, "net_vega": -0.08,
        "breakeven": 598.45, "volume": 1200,
        "underlying_price": 610.0, "bid": 1.88, "ask": 1.92,
    }


def _raw_ic():
    """An IC exactly as ``scanner_engine.build_iron_condors`` emits it: PER-SHARE."""
    return {
        "id": "SPY_IC_%s" % _EXP,
        "symbol": "SPY", "type": "IC", "trade_type": "SWING",
        "expiration": _EXP, "dte": 12,
        "short_strike": 590.0, "long_strike": 585.0,
        "call_short": 630.0, "call_long": 635.0, "width": 5.0,
        "short_mark": 1.10, "long_mark": 0.40,
        "call_short_mark": 1.05, "call_long_mark": 0.35,
        "credit": 1.40, "max_loss": 3.60,
        "pop_pct": 72.0, "short_delta": -0.20,
        "net_theta": 0.15, "net_vega": -0.09,
        "breakeven": 588.60, "underlying_price": 610.0,
        "bid": 1.08, "ask": 1.12,
    }


def _adapted_pcs():
    """The SAME spread as ``_raw_pcs`` after the swing normalization."""
    return strategy_scanner.adapt_credit_spread(_raw_pcs())


def _adapted_ic():
    return strategy_scanner.adapt_iron_condor(_raw_ic())


#############################################
# THE ADAPTED SHAPE IS WHAT WE THINK IT IS
#############################################

def test_adapted_signal_really_carries_per_contract_max_loss():
    """Guard the premise: if the adapter ever moves to per-share, these tests must fail."""
    adapted = _adapted_pcs()
    assert adapted["legs"], "adapted signal must carry reconstructed legs"
    assert adapted["credit"] == 1.55, "source per-SHARE credit is preserved"
    # per-contract dollars, commission folded in -> far larger than the per-share 3.45
    assert adapted["max_loss"] > 300.0
    assert adapted["commission"] > 0


def test_raw_signal_carries_no_legs():
    """The discriminator's other half: a raw scanner signal has no ``legs``."""
    assert "legs" not in _raw_pcs()
    assert "legs" not in _raw_ic()


#############################################
# CHARACTERIZATION: THE RAW PATH MUST NOT MOVE
#############################################

def test_raw_scanner_pcs_recorded_exactly_as_today():
    t = paper_trader.create_paper_trade(_raw_pcs(), quantity=3)
    assert {k: t[k] for k in _ECONOMIC_FIELDS} == {
        "symbol": "SPY", "strategy": "PCS", "trade_type": "SWING",
        "expiration": _EXP, "dte_at_entry": 12,
        "short_strike": 600.0, "long_strike": 595.0, "width": 5.0, "quantity": 3,
        "entry_credit": 1.55, "entry_credit_total": 465.0,
        "max_loss_per": 3.45, "max_loss_total": 1035.0,
        "breakeven": 598.45, "short_delta": -0.30, "net_theta": 0.12,
        "underlying_at_entry": 610.0,
    }


def test_raw_scanner_ic_recorded_exactly_as_today():
    t = paper_trader.create_paper_trade(_raw_ic(), quantity=2)
    assert t["max_loss_per"] == 3.60
    assert t["max_loss_total"] == 720.0          # 3.60 x 2 x 100
    assert t["entry_credit"] == 1.40
    assert t["entry_credit_total"] == 280.0
    assert t["call_short"] == 630.0 and t["call_long"] == 635.0


#############################################
# THE BUG: SWING-ORIGINATED SPREADS
#############################################

def test_swing_pcs_records_per_share_max_loss():
    """A $345 spread must book $345 of risk per contract, not $34,500."""
    t = paper_trader.create_paper_trade(_adapted_pcs(), quantity=1)
    assert t["max_loss_per"] == 3.45
    assert t["max_loss_total"] == 345.0


def test_swing_pcs_max_loss_reconciles_with_credit_and_width():
    """Defined risk: per-share credit + per-share max loss == the spread width."""
    t = paper_trader.create_paper_trade(_adapted_pcs(), quantity=1)
    assert round(t["entry_credit"] + t["max_loss_per"], 4) == t["width"]
    # ...and the same identity in per-contract dollars.
    assert round(t["entry_credit_total"] + t["max_loss_total"], 2) == t["width"] * 100


def test_swing_ic_records_per_share_max_loss():
    t = paper_trader.create_paper_trade(_adapted_ic(), quantity=1)
    assert t["max_loss_per"] == 3.60
    assert t["max_loss_total"] == 360.0
    assert round(t["entry_credit"] + t["max_loss_per"], 4) == t["width"]


def test_swing_and_raw_record_identical_economics():
    """Same spread, either scanner -> the SAME ledger row. The ledger is source-agnostic."""
    raw = paper_trader.create_paper_trade(_raw_pcs(), quantity=4)
    swing = paper_trader.create_paper_trade(_adapted_pcs(), quantity=4)
    assert {k: swing[k] for k in _ECONOMIC_FIELDS} == {k: raw[k] for k in _ECONOMIC_FIELDS}


def test_swing_max_loss_total_scales_with_quantity():
    t = paper_trader.create_paper_trade(_adapted_pcs(), quantity=20)
    assert t["max_loss_total"] == 6900.0          # 3.45 x 20 x 100, not 690,000


def test_swing_pcs_entry_credit_stays_per_share():
    """``credit`` is preserved per-SHARE by the adapter and must not be rescaled."""
    t = paper_trader.create_paper_trade(_adapted_pcs(), quantity=2)
    assert t["entry_credit"] == 1.55
    assert t["entry_credit_total"] == 310.0
