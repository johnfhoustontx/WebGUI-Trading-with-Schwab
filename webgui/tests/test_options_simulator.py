"""Tests for the Simulator page pure figure/transform builders."""
from pages.options import simulator as sim


class _Row:
    def __init__(self, expiry, kind, strike):
        self.expiry, self.kind, self.strike = expiry, kind, strike


class _Snap:
    def __init__(self, contracts):
        self.contracts = contracts


def test_whatif_figure_is_plotly_dict():
    df = [{"S": 440, "theo_price": -50}, {"S": 450, "theo_price": 0}, {"S": 460, "theo_price": 80}]
    fig = sim.whatif_figure(df, spot=450.0, target_s=455.0)
    assert "data" in fig and "layout" in fig
    xs = fig["data"][0]["x"]
    assert xs[0] == 440 and xs[-1] == 460


def test_ivshock_figure_two_series():
    base = {"theo_price": 1.0, "delta": 0.5, "gamma": 0.02, "theta": -0.1, "vega": 0.3}
    shock = {"theo_price": 1.6, "delta": 0.55, "gamma": 0.018, "theta": -0.12, "vega": 0.45}
    fig = sim.ivshock_figure(base, shock, mult=1.5)
    assert "data" in fig and len(fig["data"]) == 2


def test_expiries_of_dedupes_sorted():
    snap = _Snap([_Row("2026-06-19", "call", 450), _Row("2026-06-18", "call", 455),
                  _Row("2026-06-19", "put", 445)])
    assert sim.expiries_of(snap) == ["2026-06-18", "2026-06-19"]


def test_strikes_of_filters_by_expiry_and_kind():
    snap = _Snap([_Row("2026-06-19", "call", 450), _Row("2026-06-19", "put", 445),
                  _Row("2026-06-18", "call", 460)])
    assert sim.strikes_of(snap, "2026-06-19", "call") == [450]
    assert sim.strikes_of(snap, "2026-06-19", "put") == [445]
