"""Tests for commission-net economics attached to flat-scanner signals.

The flat scanner's gross ``credit``/``max_loss``/``rr_pct`` feed the tuned
composite score, the sort, paper BP sizing, and the webgui display, so they are
left UNTOUCHED. ``_attach_net_economics`` adds ADDITIVE net-of-commission fields
(``commission``/``net_credit``/``net_max_loss``/``net_rr_pct``) that the autonomous
driver's model menu consumes, so the driver's perceived edge is net-of-fees.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # options-scanner

from scanner_engine import _attach_net_economics


def _pcs(**over):
    sig = {"type": "PCS", "symbol": "SPY", "credit": 2.50, "max_loss": 2.50, "rr_pct": 100.0}
    sig.update(over)
    return sig


def test_vertical_two_legs_round_trip_commission():
    # 2 legs x $0.65 x 2 (open+close) = $2.60 per contract -> $0.026/share.
    sig = _pcs()
    _attach_net_economics(sig)
    assert sig["commission"] == 2.60
    assert sig["net_credit"] == 2.47          # 2.50 - 0.026 -> round 2dp
    assert sig["net_max_loss"] == 2.53        # 2.50 + 0.026
    # net R:R is strictly worse than the gross 100%.
    assert sig["net_rr_pct"] < sig["rr_pct"]
    assert sig["net_rr_pct"] == round(2.47 / 2.53 * 100, 1)


def test_gross_fields_are_left_untouched():
    sig = _pcs()
    _attach_net_economics(sig)
    assert sig["credit"] == 2.50 and sig["max_loss"] == 2.50 and sig["rr_pct"] == 100.0


def test_iron_condor_four_legs():
    # 4 legs x $0.65 x 2 = $5.20 per contract -> $0.052/share.
    sig = {"type": "IC", "symbol": "SPY", "credit": 1.00, "max_loss": 4.00, "rr_pct": 25.0}
    _attach_net_economics(sig)
    assert sig["commission"] == 5.20
    assert sig["net_credit"] == 0.95
    assert sig["net_max_loss"] == 4.05


def test_index_symbol_uses_index_rate():
    sig = _pcs(symbol="$SPX")
    _attach_net_economics(sig)
    # index rate mirrors equity 0.65 in the shipped toml -> same $2.60, but routed
    # through the index path (regression guard that the index root is recognized).
    assert sig["commission"] == 2.60


def test_defensive_missing_credit_does_not_raise():
    sig = {"type": "PCS", "symbol": "SPY"}  # no credit/max_loss
    _attach_net_economics(sig)              # must not raise
    # net fields degrade to gross (None) rather than crashing.
    assert "commission" in sig


def test_unknown_type_defaults_to_two_legs():
    sig = _pcs(type="XYZ")
    _attach_net_economics(sig)
    assert sig["commission"] == 2.60
