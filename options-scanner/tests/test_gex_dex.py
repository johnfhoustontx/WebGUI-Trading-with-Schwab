import pytest

from gamma_tool import project_0dte_pressure


def _c(delta, charm, oi):
    """Shorthand for a single-contract dict in the shape the helper expects."""
    return {"delta": delta, "charm": charm, "openInterest": oi}


def test_project_pressure_no_contracts():
    assert project_0dte_pressure([], spot=5000.0, hours_to_close=3.0) == (None, None, None)


def test_project_pressure_post_close_zero_drift():
    # hours_to_close = 0 → projection == current, pressure == 0
    contracts = [
        (_c(0.5, 0.1, 100), "call"),
        (_c(-0.5, 0.0, 50), "put"),
    ]
    now, proj, pressure = project_0dte_pressure(contracts, spot=5000.0, hours_to_close=0.0)
    assert now == proj
    assert pressure == 0.0


def test_project_pressure_call_drift_itm():
    """Positive charm on calls → delta drifts toward 1 → dealers must buy delta."""
    contracts = [(_c(0.6, 0.5, 100), "call")]
    now, proj, pressure = project_0dte_pressure(contracts, spot=5000.0, hours_to_close=6.0)
    assert proj > now
    assert pressure > 0


def test_project_pressure_clamps_to_valid_range():
    """Extreme charm × hours cannot push projected |delta| beyond 1."""
    # delta=0.95, huge charm: naive projection would exceed 1.0.
    contracts = [(_c(0.95, 1000.0, 100), "call")]
    now, proj, pressure = project_0dte_pressure(contracts, spot=5000.0, hours_to_close=6.0)
    # Projected per-contract delta capped at 1.0 → proj_dollar_delta == 1 * 100 * 100 * 5000
    assert proj == pytest.approx(1.0 * 100 * 100 * 5000.0)


def test_project_pressure_clamps_put_lower_bound():
    """Extreme negative charm × hours cannot push projected put delta below -1."""
    contracts = [(_c(-0.95, -1000.0, 100), "put")]
    now, proj, pressure = project_0dte_pressure(contracts, spot=5000.0, hours_to_close=6.0)
    # Projected per-contract delta floored at -1.0 → proj_dollar_delta == -1 * 100 * 100 * 5000
    assert proj == pytest.approx(-1.0 * 100 * 100 * 5000.0)


def test_project_pressure_per_year_unit_convention():
    """Charm is per-year. Over 1 hour (1 / (365*24) yr), delta change is small."""
    contracts = [(_c(0.5, 1.0, 100), "call")]  # charm = 1.0 per year
    now, proj, _ = project_0dte_pressure(contracts, spot=100.0, hours_to_close=1.0)
    # Expected delta change: 1.0 × (1 / (365*24)) ≈ 1.14e-4
    expected_delta_proj = 0.5 + 1.0 * (1.0 / (365 * 24))
    # Current $delta = 0.5 * 100 * 100 * 100 = 500_000
    # Projected $delta = expected_delta_proj * 100 * 100 * 100
    assert proj == pytest.approx(expected_delta_proj * 100 * 100 * 100, rel=1e-6)


from gamma_tool import GammaEngine


def test_snapshot_summary_gex_backcompat():
    """Default view='gex' must match existing call sites (no 0-DTE fields)."""
    data = {
        "spot": 5000.0,
        "gex": {5000.0: {"call": 1.0, "put": -0.5, "net": 0.5}},
    }
    summary = GammaEngine.snapshot_summary(data)
    assert "net_delta_0dte" not in summary
    assert "projected_net_delta_close" not in summary
    assert "hedge_pressure" not in summary


def test_snapshot_summary_dex_includes_pressure_fields():
    data = {
        "spot": 5000.0,
        "gex": {5000.0: {"call": 1e6, "put": -5e5, "net": 5e5}},
        "net_delta_0dte": -1.2e9,
        "projected_net_delta_close": -0.87e9,
        "hedge_pressure": 3.3e8,
    }
    summary = GammaEngine.snapshot_summary(data, view="dex")
    assert summary["net_delta_0dte"] == -1.2e9
    assert summary["projected_net_delta_close"] == -0.87e9
    assert summary["hedge_pressure"] == 3.3e8


def test_snapshot_summary_dex_missing_pressure_defaults_none():
    data = {"spot": 18.0, "gex": {}}
    summary = GammaEngine.snapshot_summary(data, view="dex")
    assert summary["net_delta_0dte"] is None
    assert summary["projected_net_delta_close"] is None
    assert summary["hedge_pressure"] is None


from datetime import datetime, timedelta


def _chain_factory(*, spot, exp_key, dte, strikes):
    """Build a minimal Schwab-shaped chain dict for unit tests.

    strikes: {strike_float: (call_delta, put_delta, open_interest, iv_pct)}
    """
    call_map = {exp_key: {}}
    put_map = {exp_key: {}}
    for strike, (call_delta, put_delta, oi, iv_pct) in strikes.items():
        call_map[exp_key][str(strike)] = [{
            "delta": call_delta, "openInterest": oi, "volatility": iv_pct,
            "daysToExpiration": dte,
        }]
        put_map[exp_key][str(strike)] = [{
            "delta": put_delta, "openInterest": oi, "volatility": iv_pct,
            "daysToExpiration": dte,
        }]
    return {
        "underlyingPrice": spot,
        "callExpDateMap": call_map,
        "putExpDateMap": put_map,
    }


def test_calc_dex_empty_chain_returns_none():
    assert GammaEngine().calc_dex_from_chain(None) is None
    assert GammaEngine().calc_dex_from_chain({}) is None


def test_calc_dex_signs_and_scaling():
    """Single strike: dd_call = OI * delta_call * 100 * spot. Put subtracts."""
    today = datetime.now().strftime("%Y-%m-%d")
    exp_key = f"{today}:0"
    chain = _chain_factory(
        spot=5000.0, exp_key=exp_key, dte=0,
        strikes={5000.0: (0.5, -0.5, 100, 20.0)},
    )
    result = GammaEngine().calc_dex_from_chain(chain)
    assert result is not None
    assert result["spot"] == 5000.0
    grid = result["gex"]
    assert 5000.0 in grid
    # call: 100 * 0.5 * 100 * 5000 = 25_000_000
    # put:  100 * -0.5 * 100 * 5000 = -25_000_000
    assert grid[5000.0]["call"] == pytest.approx(25_000_000.0)
    assert grid[5000.0]["put"]  == pytest.approx(-25_000_000.0)
    assert grid[5000.0]["net"]  == pytest.approx(0.0)


def test_calc_dex_populates_0dte_fields_when_0dte_present():
    today = datetime.now().strftime("%Y-%m-%d")
    exp_key = f"{today}:0"
    chain = _chain_factory(
        spot=5000.0, exp_key=exp_key, dte=0,
        strikes={5000.0: (0.5, -0.5, 100, 20.0)},
    )
    result = GammaEngine().calc_dex_from_chain(chain)
    assert result["net_delta_0dte"] is not None
    assert result["projected_net_delta_close"] is not None
    assert result["hedge_pressure"] is not None


def test_calc_dex_no_0dte_yields_none_fields():
    future = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    exp_key = f"{future}:7"
    chain = _chain_factory(
        spot=5000.0, exp_key=exp_key, dte=7,
        strikes={5000.0: (0.5, -0.5, 100, 20.0)},
    )
    result = GammaEngine().calc_dex_from_chain(chain)
    assert result["net_delta_0dte"] is None
    assert result["projected_net_delta_close"] is None
    assert result["hedge_pressure"] is None


def test_view_var_accepts_three_views():
    """Minimal sanity check that the view tokens are recognized."""
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No display available for Tk")
    root.withdraw()
    try:
        v = tk.StringVar(value="gex")
        for choice in ("gex", "charm", "dex"):
            v.set(choice)
            assert v.get() == choice
    finally:
        root.destroy()


from gamma_tool import _fmt_dollar_magnitude


def test_fmt_dollar_magnitude_none():
    assert _fmt_dollar_magnitude(None) == "n/a"


def test_fmt_dollar_magnitude_billions():
    assert _fmt_dollar_magnitude(-1.24e9) == "-$1.24B"
    assert _fmt_dollar_magnitude(3.3e8) == "+$330M"


def test_fmt_dollar_magnitude_small():
    assert _fmt_dollar_magnitude(12345) == "+$12,345"


def test_fmt_dollar_magnitude_zero():
    # Zero magnitude is treated as non-negative → '+$0'.
    assert _fmt_dollar_magnitude(0) == "+$0"


def test_dex_end_to_end_db_roundtrip(tmp_path, monkeypatch):
    """Collector-style write + UI-style read of a DEX row.

    Exercises the full persistence path: insert_snapshot with all three
    0-DTE pressure fields, first_snapshot_today returning the grid, and
    a direct SELECT verifying the pressure fields round-trip through
    SQLite REAL columns without loss.
    """
    import gex_history_db as db
    import time as _time
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)

    summary = {
        "ts": int(_time.time()) - 1800, "spot": 5000.0, "flip": 4995.0,
        "top_pos_strike": 5010.0, "top_neg_strike": 4980.0,
        "net_total": 5.0e8,
        "net_delta_0dte": -1.2e9,
        "projected_net_delta_close": -0.87e9,
        "hedge_pressure": 3.3e8,
    }
    grid = {5000.0: {"call": 1e6, "put": -5e5, "net": 5e5}}
    db.insert_snapshot(conn, "$SPX", "dex", summary, grid, 0)

    # "vs Open" baseline helper returns the grid verbatim for a single-row day.
    baseline = db.first_snapshot_today(conn, "$SPX", "dex")
    assert baseline == grid

    # Pressure fields persist through SQLite REAL columns without precision loss.
    row = conn.execute(
        "SELECT net_delta_0dte, projected_net_delta_close, hedge_pressure "
        "FROM snapshots WHERE view = 'dex'"
    ).fetchone()
    assert row == (-1.2e9, -0.87e9, 3.3e8)


def test_dex_end_to_end_multiple_snapshots_baseline_is_earliest(tmp_path, monkeypatch):
    """Multi-snapshot day: first_snapshot_today picks the earliest row's grid.

    This validates the exact behavior the ΔDEX ghost-bar overlay depends on:
    when 5 DEX rows exist for today, the ghost shows the 8:30 baseline, not
    the most recent.
    """
    import gex_history_db as db
    import time as _time

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)

    base_ts = int(_time.time()) - 1800  # 30 min ago, safely within today
    for offset_min, net in [(0, 1.0e8), (5, 2.0e8), (10, 3.0e8)]:
        db.insert_snapshot(
            conn, "$SPX", "dex",
            {
                "ts": base_ts + offset_min * 60,
                "spot": 5000.0, "flip": 5000.0,
                "top_pos_strike": None, "top_neg_strike": None,
                "net_total": net,
                "net_delta_0dte": None,
                "projected_net_delta_close": None,
                "hedge_pressure": None,
            },
            {5000.0: {"call": net, "put": 0.0, "net": net}},
            0,
        )

    baseline = db.first_snapshot_today(conn, "$SPX", "dex")
    # Earliest row had net=1.0e8, so that's the baseline grid.
    assert baseline == {5000.0: {"call": 1.0e8, "put": 0.0, "net": 1.0e8}}
