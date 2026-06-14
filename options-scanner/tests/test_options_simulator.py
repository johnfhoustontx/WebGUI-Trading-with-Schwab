from datetime import date, datetime
import pandas as pd
import pytest

from options_simulator.engine import ChainSnapshot, ContractRow, Leg, Position, aggregate_position


def test_contract_row_constructs():
    c = ContractRow(strike=580.0, kind="call", bid=2.10, ask=2.20, mid=2.15,
                    iv=0.18, expiry=date(2026, 5, 23))
    assert c.strike == 580.0
    assert c.kind == "call"


def test_chain_snapshot_constructs_with_empty_history():
    snap = ChainSnapshot(
        spot=580.0,
        as_of=datetime(2026, 5, 23, 14, 30),
        r=0.04,
        contracts=[],
        price_history=pd.Series(dtype=float),
    )
    assert snap.spot == 580.0
    assert snap.price_history.empty


from options_simulator.pnl import decompose


def test_decompose_returns_all_greek_components():
    greeks_t0 = {"delta": 0.5, "gamma": 0.02, "theta": -0.04, "vega": 0.11, "rho": 0.01}
    out = decompose(greeks_t0, dS=1.0, dt_years=1/365, dsigma=0.01)
    assert set(out.keys()) == {"delta", "gamma", "theta", "vega", "rho", "total"}


def test_decompose_delta_contribution():
    greeks_t0 = {"delta": 0.5, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    out = decompose(greeks_t0, dS=2.0, dt_years=0.0, dsigma=0.0)
    assert out["delta"] == pytest.approx(1.0)
    assert out["total"] == pytest.approx(1.0)


def test_decompose_gamma_quadratic():
    greeks_t0 = {"delta": 0.0, "gamma": 0.04, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    out = decompose(greeks_t0, dS=2.0, dt_years=0.0, dsigma=0.0)
    assert out["gamma"] == pytest.approx(0.08)


def test_decompose_theta_per_step_convention():
    greeks_t0 = {"delta": 0.0, "gamma": 0.0, "theta": -0.05, "vega": 0.0, "rho": 0.0}
    out = decompose(greeks_t0, dS=0.0, dt_years=1/365, dsigma=0.0)
    assert out["theta"] == pytest.approx(-0.05)


def test_decompose_vega_per_one_vol_point():
    greeks_t0 = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.20, "rho": 0.0}
    out = decompose(greeks_t0, dS=0.0, dt_years=0.0, dsigma=0.01)
    assert out["vega"] == pytest.approx(0.20)


import numpy as np
from options_simulator.engine import ReplayEngine


def _make_snapshot_with_history(prices, as_of=None):
    as_of = as_of or datetime(2026, 5, 23, 14, 30)
    ts = pd.date_range(end=as_of, periods=len(prices), freq="1min")
    return ChainSnapshot(
        spot=float(prices[-1]),
        as_of=as_of,
        r=0.04,
        contracts=[],
        price_history=pd.Series(prices, index=ts),
    )


def test_replay_engine_full_trace_returns_one_row_per_bar():
    snap = _make_snapshot_with_history(np.linspace(100, 105, 50))
    contract = ContractRow(strike=100.0, kind="call", bid=2.0, ask=2.1, mid=2.05,
                           iv=0.20, expiry=date(2026, 5, 24))
    eng = ReplayEngine(snap)
    trace = eng.full_trace(contract)
    assert len(trace) == 50
    assert set(trace.columns) >= {"theo_price", "delta", "gamma", "theta", "vega", "rho"}


def test_replay_engine_theta_more_negative_as_T_shrinks():
    snap = _make_snapshot_with_history(np.full(20, 100.0))
    contract = ContractRow(strike=100.0, kind="call", bid=2.0, ask=2.1, mid=2.05,
                           iv=0.20, expiry=date(2026, 5, 24))
    eng = ReplayEngine(snap)
    trace = eng.full_trace(contract)
    assert trace["theta"].iloc[-1] < trace["theta"].iloc[0]


def test_replay_engine_empty_history_returns_empty_frame():
    snap = ChainSnapshot(spot=100.0, as_of=datetime(2026, 5, 23, 14, 30),
                         r=0.04, contracts=[], price_history=pd.Series(dtype=float))
    contract = ContractRow(strike=100.0, kind="call", bid=2.0, ask=2.1, mid=2.05,
                           iv=0.20, expiry=date(2026, 5, 24))
    eng = ReplayEngine(snap)
    trace = eng.full_trace(contract)
    assert trace.empty


from options_simulator.engine import WhatIfEngine


def test_whatif_sweep_returns_dataframe_with_greeks():
    snap = _make_snapshot_with_history(np.full(10, 100.0))
    contract = ContractRow(strike=100.0, kind="call", bid=2.0, ask=2.1, mid=2.05,
                           iv=0.20, expiry=date(2026, 6, 23))
    eng = WhatIfEngine(snap)
    df = eng.sweep(contract, s_range=np.linspace(80, 120, 9), t_days=30.0)
    assert len(df) == 9
    assert set(df.columns) >= {"S", "theo_price", "delta", "gamma", "theta", "vega", "rho"}


def test_whatif_call_delta_monotonic_in_S():
    snap = _make_snapshot_with_history(np.full(10, 100.0))
    contract = ContractRow(strike=100.0, kind="call", bid=2.0, ask=2.1, mid=2.05,
                           iv=0.20, expiry=date(2026, 6, 23))
    eng = WhatIfEngine(snap)
    df = eng.sweep(contract, s_range=np.linspace(80, 120, 9), t_days=30.0)
    deltas = df["delta"].values
    assert np.all(np.diff(deltas) >= -1e-9)


def test_whatif_gamma_peaks_near_ATM():
    snap = _make_snapshot_with_history(np.full(10, 100.0))
    contract = ContractRow(strike=100.0, kind="call", bid=2.0, ask=2.1, mid=2.05,
                           iv=0.20, expiry=date(2026, 6, 23))
    eng = WhatIfEngine(snap)
    s_range = np.linspace(80, 120, 41)
    df = eng.sweep(contract, s_range=s_range, t_days=30.0)
    peak_idx = int(df["gamma"].idxmax())
    assert abs(peak_idx - 20) <= 2


from options_simulator.engine import IVShockEngine


def test_ivshock_sweep_returns_dataframe():
    snap = _make_snapshot_with_history(np.full(10, 100.0))
    contract = ContractRow(strike=100.0, kind="call", bid=2.0, ask=2.1, mid=2.05,
                           iv=0.20, expiry=date(2026, 6, 23))
    eng = IVShockEngine(snap)
    df = eng.sweep(contract, multipliers=[0.5, 1.0, 2.0])
    assert len(df) == 3
    assert set(df.columns) >= {"sigma_mult", "sigma", "theo_price", "vega"}


def test_ivshock_vega_dsigma_approximates_price_change_for_small_shocks():
    snap = _make_snapshot_with_history(np.full(10, 100.0))
    contract = ContractRow(strike=100.0, kind="call", bid=2.0, ask=2.1, mid=2.05,
                           iv=0.20, expiry=date(2026, 6, 23))
    eng = IVShockEngine(snap)
    df = eng.sweep(contract, multipliers=[1.00, 1.05])
    price_change = df["theo_price"].iloc[1] - df["theo_price"].iloc[0]
    vega_at_base = df["vega"].iloc[0]
    dsigma = df["sigma"].iloc[1] - df["sigma"].iloc[0]
    predicted = vega_at_base * dsigma * 100.0
    assert price_change == pytest.approx(predicted, rel=0.05)


from unittest.mock import MagicMock
from options_simulator.data import fetch_snapshot


def _make_chain_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "underlyingPrice": 580.0,
        "callExpDateMap": {
            "2026-05-30:7": {
                "580.0": [{"bid": 2.10, "ask": 2.20, "volatility": 18.0,
                           "strikePrice": 580.0, "putCall": "CALL"}]
            }
        },
        "putExpDateMap": {
            "2026-05-30:7": {
                "580.0": [{"bid": 2.00, "ask": 2.10, "volatility": 18.0,
                           "strikePrice": 580.0, "putCall": "PUT"}]
            }
        },
    }
    return resp


def test_fetch_snapshot_returns_chainsnapshot_for_happy_path():
    client = MagicMock()
    client.Options.ContractType.ALL = "ALL"
    client.get_option_chain.return_value = _make_chain_response()
    ph_resp = MagicMock()
    ph_resp.status_code = 200
    ph_resp.json.return_value = {
        "candles": [
            {"datetime": 1716480000000, "close": 579.5},
            {"datetime": 1716480060000, "close": 580.0},
        ]
    }
    client.get_price_history_every_minute.return_value = ph_resp

    snap = fetch_snapshot(client, "SPY", date(2026, 5, 30))
    assert snap.spot == 580.0
    assert len(snap.contracts) == 2
    # iv stored as decimal (Schwab returns percent; we divide by 100)
    assert snap.contracts[0].iv == pytest.approx(0.18)
    assert not snap.price_history.empty


def test_fetch_snapshot_empty_history_on_failure():
    client = MagicMock()
    client.Options.ContractType.ALL = "ALL"
    chain_resp = MagicMock()
    chain_resp.status_code = 200
    chain_resp.json.return_value = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {}, "putExpDateMap": {},
    }
    client.get_option_chain.return_value = chain_resp
    client.get_price_history_every_minute.side_effect = RuntimeError("network down")

    snap = fetch_snapshot(client, "SPY", date(2026, 5, 30))
    assert snap.price_history.empty
    assert snap.spot == 100.0


def test_fetch_snapshot_falls_back_to_daily_when_minute_method_missing():
    """Proxy clients (SchwabPyProxyClient) only expose get_price_history_every_day.
    fetch_snapshot must fall back instead of crashing."""

    class ProxyLikeClient:
        """Bare object — no get_price_history_every_minute attribute."""
        def __init__(self):
            self.Options = MagicMock()
            self.Options.ContractType.ALL = "ALL"

        def get_option_chain(self, symbol, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "underlyingPrice": 250.0,
                "callExpDateMap": {}, "putExpDateMap": {},
            }
            return resp

        def get_price_history_every_day(self, symbol):
            resp = MagicMock()
            resp.json.return_value = {
                "candles": [
                    {"datetime": 1716480000000, "close": 248.0},
                    {"datetime": 1716566400000, "close": 250.0},
                ]
            }
            return resp

    snap = fetch_snapshot(ProxyLikeClient(), "TSLA")
    # Replay history populated from daily candles instead of crashing
    assert not snap.price_history.empty
    assert len(snap.price_history) == 2


def test_fetch_snapshot_no_expiry_returns_all_with_parsed_expiries():
    """When expiry is None, fetch_snapshot pulls a multi-expiry window and
    each contract's expiry is parsed from Schwab's response key."""
    client = MagicMock()
    client.Options.ContractType.ALL = "ALL"
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "underlyingPrice": 250.0,
        "callExpDateMap": {
            "2026-06-05:13": {
                "250.0": [{"bid": 3.0, "ask": 3.1, "volatility": 22.0,
                           "strikePrice": 250.0, "putCall": "CALL"}]
            },
            "2026-06-19:27": {
                "260.0": [{"bid": 1.5, "ask": 1.6, "volatility": 24.0,
                           "strikePrice": 260.0, "putCall": "CALL"}]
            },
        },
        "putExpDateMap": {},
    }
    client.get_option_chain.return_value = resp
    client.get_price_history_every_minute.side_effect = RuntimeError("skip history")

    snap = fetch_snapshot(client, "TSLA")  # no expiry argument
    expiries = sorted({c.expiry for c in snap.contracts})
    assert expiries == [date(2026, 6, 5), date(2026, 6, 19)]
    # The 250-strike contract should carry the 2026-06-05 expiry
    c_250 = [c for c in snap.contracts if c.strike == 250.0][0]
    assert c_250.expiry == date(2026, 6, 5)


# ---- Position / Leg / aggregate_position ----------------------------------

def _call_contract(strike, iv=0.20):
    return ContractRow(strike=strike, kind="call", bid=2.0, ask=2.1, mid=2.05,
                       iv=iv, expiry=date(2026, 6, 23))


def test_position_single_long_call_signs_positive():
    p = Position.single(_call_contract(100.0), direction="buy", symbol="TSLA")
    assert len(p.legs) == 1
    assert p.legs[0].sign == +1
    assert "Long" in p.label
    assert "TSLA" in p.label


def test_position_single_short_call_is_naked_short():
    p = Position.single(_call_contract(100.0), direction="sell", symbol="TSLA")
    assert p.legs[0].sign == -1
    assert "naked" in p.label.lower()


def test_position_vertical_validates_same_kind():
    call = _call_contract(100.0)
    put = ContractRow(strike=100.0, kind="put", bid=1.0, ask=1.1, mid=1.05,
                      iv=0.20, expiry=date(2026, 6, 23))
    with pytest.raises(ValueError, match="same kind"):
        Position.vertical(call, put, symbol="TSLA")


def test_position_vertical_validates_same_expiry():
    a = _call_contract(100.0)
    b = ContractRow(strike=105.0, kind="call", bid=1.0, ask=1.1, mid=1.05,
                    iv=0.20, expiry=date(2026, 7, 23))
    with pytest.raises(ValueError, match="same expiry"):
        Position.vertical(a, b, symbol="TSLA")


def test_position_vertical_labels_with_strikes():
    p = Position.vertical(_call_contract(100.0), _call_contract(105.0), symbol="TSLA")
    assert "100" in p.label and "105" in p.label
    assert "Call Spread" in p.label


def test_aggregate_position_single_long_passes_through():
    p = Position.single(_call_contract(100.0), direction="buy")
    snap = _make_snapshot_with_history(np.full(10, 100.0))
    eng = WhatIfEngine(snap)
    agg = aggregate_position(p, lambda c: eng.sweep(c, s_range=np.linspace(90, 110, 11), t_days=30))
    direct = eng.sweep(_call_contract(100.0), s_range=np.linspace(90, 110, 11), t_days=30)
    assert np.allclose(agg["delta"].values, direct["delta"].values)


def test_aggregate_position_short_negates_greeks():
    """Selling a call means delta should be negative across the S sweep."""
    p = Position.single(_call_contract(100.0), direction="sell")
    snap = _make_snapshot_with_history(np.full(10, 100.0))
    eng = WhatIfEngine(snap)
    agg = aggregate_position(p, lambda c: eng.sweep(c, s_range=np.linspace(90, 110, 11), t_days=30))
    assert (agg["delta"] <= 0).all()
    # ATM gamma is positive for long, negative for short
    atm_row = agg.iloc[5]
    assert atm_row["gamma"] < 0


def test_aggregate_position_vertical_call_spread_delta_caps():
    """A 100/105 long call spread should have delta between 0 and 1 across S."""
    long_leg = _call_contract(100.0)
    short_leg = _call_contract(105.0)
    p = Position.vertical(long_leg, short_leg, symbol="TSLA")
    snap = _make_snapshot_with_history(np.full(10, 102.5))
    eng = WhatIfEngine(snap)
    agg = aggregate_position(p, lambda c: eng.sweep(c, s_range=np.linspace(80, 130, 51), t_days=30))
    # Net delta of a debit call spread is bounded in [0, 1]
    assert (agg["delta"] >= -0.01).all()
    assert (agg["delta"] <= 1.01).all()
    # Max delta should be near the long strike, dropping off above the short strike
    deep_itm_idx = (agg["S"] >= 125).idxmax()
    assert agg.loc[deep_itm_idx, "delta"] < 0.5  # spread caps profit above short strike
