"""C6/C7/C8 consistency guards.

C6 — the simulator's option-expiry settlement is now 16:00 US/Eastern (was a
naive ``hour=15``), matching the calculator's ``time_to_expiry_years`` convention.
C7 — a single ``RISK_FREE_RATE`` source of truth (0.045) is used across the
calculator, simulator ``fetch_snapshot``, and ``compute.calc_iv``.
C8 — the calculator's PoP functions are documented as risk-neutral LOGNORMAL
(drift = r), distinct from the Swing Scanner's zero-drift NORMAL PoP.
"""
from datetime import date, datetime
import math

import numpy as np
import pandas as pd
import pytest

import options_calculator as oc
from options_calculator import RISK_FREE_RATE, expiry_time_to_years
from options_simulator.engine import (
    ChainSnapshot, ContractRow, IVShockEngine, ReplayEngine,
)


# ── C7: single risk-free rate ────────────────────────────────────────────────

def test_risk_free_rate_constant_is_0045():
    assert RISK_FREE_RATE == 0.045


def test_fetch_snapshot_uses_shared_rate():
    """The simulator's snapshot r is the shared constant, not the old 0.04."""
    from unittest.mock import MagicMock
    from options_simulator.data import fetch_snapshot

    client = MagicMock()
    client.Options.ContractType.ALL = "ALL"
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"underlyingPrice": 100.0,
                              "callExpDateMap": {}, "putExpDateMap": {}}
    client.get_option_chain.return_value = resp
    client.get_price_history_every_minute.side_effect = RuntimeError("skip")

    snap = fetch_snapshot(client, "SPY", date(2026, 6, 30))
    assert snap.r == RISK_FREE_RATE
    assert snap.r != 0.04


# ── C6: 16:00 ET expiry settlement, calculator-consistent T ──────────────────

def test_expiry_time_to_years_settles_at_1600_naive():
    """3 hours before the 16:00 close on expiry day → ~3/24/365 years."""
    ref = datetime(2026, 6, 30, 13, 0, 0)   # naive wall clock, 1pm on expiry day
    T = expiry_time_to_years(ref, date(2026, 6, 30))
    assert T == pytest.approx(3.0 / 24.0 / 365.0, rel=1e-6)


def test_expiry_time_to_years_zero_after_close():
    ref = datetime(2026, 6, 30, 17, 0, 0)   # past the 16:00 close
    assert expiry_time_to_years(ref, date(2026, 6, 30)) == 0.0


def test_expiry_time_to_years_multiday():
    ref = datetime(2026, 6, 30, 16, 0, 0)   # exactly at close, 3 days before expiry
    T = expiry_time_to_years(ref, date(2026, 7, 3))
    assert T == pytest.approx(3.0 / 365.0, rel=1e-6)


def test_expiry_time_to_years_handles_tz_aware():
    from zoneinfo import ZoneInfo
    ref = datetime(2026, 6, 30, 13, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    T = expiry_time_to_years(ref, date(2026, 6, 30))
    assert T == pytest.approx(3.0 / 24.0 / 365.0, rel=1e-6)


def test_helper_matches_compute_time_to_expiry_years():
    """The options-scanner helper must produce the SAME T as the canonical
    services/options_svc compute.time_to_expiry_years for a tz-aware now."""
    from zoneinfo import ZoneInfo

    ref = datetime(2026, 6, 30, 11, 30, 0, tzinfo=ZoneInfo("America/New_York"))
    exp = date(2026, 6, 30)
    t_helper = expiry_time_to_years(ref, exp)
    # Mirror compute's math directly (16:00 ET tz-aware, /365) to avoid importing
    # the whole options_svc package graph.
    settlement = datetime(2026, 6, 30, 16, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    t_compute = max((settlement - ref).total_seconds(), 0.0) / (365.0 * 86400.0)
    assert t_helper == pytest.approx(t_compute, rel=1e-9)


def _snap_with_history(prices, as_of):
    ts = pd.date_range(end=as_of, periods=len(prices), freq="1min")
    return ChainSnapshot(spot=float(prices[-1]), as_of=as_of, r=RISK_FREE_RATE,
                         contracts=[], price_history=pd.Series(prices, index=ts))


def test_replay_T_uses_1600_not_1500():
    """At 15:30 on expiry day, T reflects 0.5h-to-16:00, NOT a negative/zero
    (which the old naive hour=15 produced — 15:30 was already 'past' the 15:00
    close). The last bar's theo price must carry residual time value."""
    as_of = datetime(2026, 6, 30, 15, 30, 0)
    snap = _snap_with_history(np.full(5, 100.0), as_of)
    contract = ContractRow(strike=100.0, kind="call", bid=1.0, ask=1.1, mid=1.05,
                           iv=0.30, expiry=date(2026, 6, 30))
    trace = ReplayEngine(snap).full_trace(contract)
    # An ATM call with 0.5h of life left + 30% IV has a small but POSITIVE theo.
    # Under the old hour=15 basis, 15:30 was past close → T floored at 1e-6 →
    # theo ~ 0 (intrinsic only). 16:00 basis gives a visible time premium.
    last_theo = trace["theo_price"].iloc[-1]
    T_expected = 0.5 / 24.0 / 365.0
    theo_expected = oc.bs_price(100.0, 100.0, T_expected, RISK_FREE_RATE, 0.30, "call")
    assert last_theo == pytest.approx(theo_expected, rel=1e-6)
    assert last_theo > 0.0


def test_ivshock_T_uses_1600():
    as_of = datetime(2026, 6, 30, 15, 0, 0)   # exactly 1h to the 16:00 close
    snap = ChainSnapshot(spot=100.0, as_of=as_of, r=RISK_FREE_RATE, contracts=[],
                         price_history=pd.Series(dtype=float))
    contract = ContractRow(strike=100.0, kind="call", bid=1.0, ask=1.1, mid=1.05,
                           iv=0.25, expiry=date(2026, 6, 30))
    df = IVShockEngine(snap).sweep(contract, multipliers=[1.0])
    theo = df["theo_price"].iloc[0]
    T_expected = 1.0 / 24.0 / 365.0
    theo_expected = oc.bs_price(100.0, 100.0, T_expected, RISK_FREE_RATE, 0.25, "call")
    assert theo == pytest.approx(theo_expected, rel=1e-6)


# ── C8: PoP convention documented (lognormal, r-drift) ───────────────────────

def test_estimate_pop_docstring_names_lognormal_convention():
    doc = oc._estimate_pop.__doc__ or ""
    assert "lognormal" in doc.lower()
    assert "zero-drift" in doc.lower()  # references the distinct scanner convention


def test_calc_summary_generic_docstring_names_convention():
    doc = oc.calc_summary_generic.__doc__ or ""
    assert "lognormal" in doc.lower()


def test_dividend_yield_assumption_documented():
    doc = oc.bs_price.__doc__ or ""
    assert "dividend" in doc.lower()
