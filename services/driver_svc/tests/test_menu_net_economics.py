"""The model menu must show NET-of-commission economics (driver-facing edge).

The flat scanner attaches ``net_credit``/``net_max_loss``/``commission`` to each
signal; ``_menu_item`` projects those so the model reasons on net edge, with a
gross fallback for back-compat with any pre-fix cached signal that lacks them.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # driver_svc

import compute


def test_menu_item_prefers_net_economics():
    sig = {
        "symbol": "SPY", "type": "PCS", "expiration": "2026-07-10",
        "credit": 2.50, "max_loss": 2.50, "pop_pct": 70.0, "composite_score": 80,
        "commission": 2.60, "net_credit": 2.47, "net_max_loss": 2.53, "net_rr_pct": 97.6,
    }
    item = compute._menu_item(sig, "m0")
    assert item["credit"] == 2.47          # net, not the gross 2.50
    assert item["max_loss"] == 2.53        # net, not the gross 2.50
    assert item["commission"] == 2.60
    assert item["pop"] == 70.0


def test_menu_item_falls_back_to_gross_when_net_absent():
    # A pre-fix cached signal with no net_* fields must still project (gross).
    sig = {
        "symbol": "SPY", "type": "PCS", "expiration": "2026-07-10",
        "credit": 2.50, "max_loss": 2.50, "pop_pct": 70.0, "composite_score": 80,
    }
    item = compute._menu_item(sig, "m0")
    assert item["credit"] == 2.50
    assert item["max_loss"] == 2.50
    assert item["commission"] is None


def test_build_packet_menu_carries_net_economics():
    scan = {"signals_swing": [{
        "id": "SPY_PCS_x", "symbol": "SPY", "type": "PCS", "trade_type": "SWING",
        "expiration": "2026-07-10", "credit": 1.00, "max_loss": 4.00, "pop_pct": 65.0,
        "composite_score": 75, "commission": 2.60,
        "net_credit": 0.974, "net_max_loss": 4.026, "net_rr_pct": 24.2,
    }]}
    packet = compute.build_packet(scan, {}, target=500, limits={}, market={"vix": 15})
    assert packet["menu"], "expected the allowlisted PCS in the menu"
    item = packet["menu"][0]
    assert item["credit"] == 0.974          # net edge the model reasons on
    assert item["max_loss"] == 4.026
    assert item["commission"] == 2.60
