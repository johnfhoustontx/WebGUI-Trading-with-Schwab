# options-scanner/tests/test_simulator_engine_ratio.py
"""Ratio-leg aggregation: a 2x body must contribute 2x its Greeks."""
from datetime import date

import pandas as pd

from options_simulator.engine import Leg, Position, aggregate_position


def _row(name, val):
    return pd.DataFrame({"theo_price": [val], "delta": [val], "gamma": [val],
                         "theta": [val], "vega": [val], "rho": [val]})


def test_ratio_scales_greeks():
    # Two legs sharing a dummy contract: long ratio-2 minus short ratio-1.
    c = object()
    pos = Position(legs=[Leg(contract=c, sign=+1, ratio=2),
                         Leg(contract=c, sign=-1, ratio=1)],
                   label="ratio test")
    out = aggregate_position(pos, lambda _c: _row("x", 10.0))
    # 10*(+1*2) + 10*(-1*1) = 10
    assert out["theo_price"].iloc[0] == 10.0
    assert out["delta"].iloc[0] == 10.0


def test_ratio_defaults_to_one():
    c = object()
    pos = Position.from_legs([(c, +1, 1), (c, +1, 1)], label="two longs")
    out = aggregate_position(pos, lambda _c: _row("x", 3.0))
    assert out["theo_price"].iloc[0] == 6.0
