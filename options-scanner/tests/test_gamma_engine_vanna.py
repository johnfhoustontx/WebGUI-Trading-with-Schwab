"""Tests for GammaEngine.calc_vanna_from_chain and 4-tuple calc_all_from_chain."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from gamma_tool import GammaEngine

TZ = ZoneInfo("America/Chicago")


def _make_chain(spot=500.0, dte_days=2):
    """Synthetic 3-strike call+put chain for testing."""
    exp_date = (datetime.now(TZ) + timedelta(days=dte_days)).strftime("%Y-%m-%d")
    exp_key = f"{exp_date}:{dte_days}"
    return {
        "underlyingPrice": spot,
        "callExpDateMap": {
            exp_key: {
                "495.0": [{"volatility": 22.0, "openInterest": 1000, "delta": 0.7,
                           "gamma": 0.05, "daysToExpiration": dte_days}],
                "500.0": [{"volatility": 20.0, "openInterest": 2000, "delta": 0.52,
                           "gamma": 0.06, "daysToExpiration": dte_days}],
                "505.0": [{"volatility": 21.0, "openInterest": 1500, "delta": 0.34,
                           "gamma": 0.05, "daysToExpiration": dte_days}],
            }
        },
        "putExpDateMap": {
            exp_key: {
                "495.0": [{"volatility": 23.0, "openInterest": 1100, "delta": -0.30,
                           "gamma": 0.05, "daysToExpiration": dte_days}],
                "500.0": [{"volatility": 21.0, "openInterest": 2200, "delta": -0.48,
                           "gamma": 0.06, "daysToExpiration": dte_days}],
                "505.0": [{"volatility": 22.0, "openInterest": 1300, "delta": -0.66,
                           "gamma": 0.05, "daysToExpiration": dte_days}],
            }
        },
    }


def test_vanna_returns_none_on_empty_chain():
    eng = GammaEngine()
    assert eng.calc_vanna_from_chain({}) is None
    assert eng.calc_vanna_from_chain(None) is None


def test_vanna_returns_none_on_zero_spot():
    eng = GammaEngine()
    assert eng.calc_vanna_from_chain({"underlyingPrice": 0}) is None


def test_vanna_result_shape_matches_charm():
    eng = GammaEngine()
    chain = _make_chain()
    res = eng.calc_vanna_from_chain(chain)
    assert res is not None
    assert "spot" in res
    assert "gex" in res
    assert "strike_count" in res
    for strike, vals in res["gex"].items():
        assert set(vals.keys()) == {"call", "put", "net"}
        assert vals["net"] == pytest.approx(vals["call"] + vals["put"], abs=1e-6)


def test_vanna_puts_add_with_same_sign_as_calls():
    """Per design §4: vanna puts use additive sign (same as Charm/DEX), not subtractive.
    At ATM with positive r, raw vanna is slightly negative — both call and put contributions
    should have the same sign at ATM (both negative), since the convention is additive."""
    eng = GammaEngine()
    chain = _make_chain()
    res = eng.calc_vanna_from_chain(chain)
    atm = res["gex"][500.0]
    # Same sign (both positive or both negative) — additive convention
    assert (atm["call"] > 0) == (atm["put"] > 0), (
        f"Calls and puts should share sign under additive convention: "
        f"call={atm['call']}, put={atm['put']}"
    )


def test_vanna_skips_contracts_with_zero_iv():
    eng = GammaEngine()
    chain = _make_chain()
    exp_key = next(iter(chain["callExpDateMap"]))
    chain["callExpDateMap"][exp_key]["500.0"][0]["volatility"] = 0
    res = eng.calc_vanna_from_chain(chain)
    assert res["gex"][500.0]["call"] == 0.0
    assert res["gex"][500.0]["put"] != 0.0


def test_calc_all_returns_four_tuple():
    eng = GammaEngine()
    chain = _make_chain()
    result = eng.calc_all_from_chain(chain)
    assert len(result) == 4
    gex, charm, dex, vanna = result
    assert gex is not None and "gex" in gex
    assert charm is not None and "gex" in charm
    assert dex is not None and "gex" in dex
    assert vanna is not None and "gex" in vanna


def test_calc_all_vanna_matches_standalone():
    """4-tuple vanna result must equal the standalone calc_vanna_from_chain output."""
    eng_a = GammaEngine()
    eng_b = GammaEngine()
    chain = _make_chain()
    standalone = eng_a.calc_vanna_from_chain(chain)
    _, _, _, bundled = eng_b.calc_all_from_chain(chain)
    assert set(standalone["gex"].keys()) == set(bundled["gex"].keys())
    for k in standalone["gex"]:
        for side in ("call", "put", "net"):
            assert standalone["gex"][k][side] == pytest.approx(
                bundled["gex"][k][side], abs=1e-6,
            )


def test_calc_all_vanna_matches_standalone_after_close(monkeypatch):
    """Deterministic regression for the post-close T-clamp bug.

    Until this fix, `calc_vanna_from_chain` computed
        hours_left = (CLOSE_HOUR_CT - now.hour) + (CLOSE_MIN_CT - now.minute) / 60
    without clamping the result to >= 0. `calc_all_from_chain` applied
    `max(0.0, hours_left_trading)` in the same place. When the test ran
    after market close (now.hour >= CLOSE_HOUR_CT), the two T values
    diverged by ~2% and the per-strike vanna values drifted by hundreds —
    way above the 1e-6 tolerance in test_calc_all_vanna_matches_standalone.

    This test monkeypatches datetime.now in gamma_tool to a fixed
    post-close timestamp so the divergence is reproducible regardless of
    wall-clock time. After the fix it must pass; before the fix it would
    fail with a hundreds-magnitude drift.
    """
    import gamma_tool
    from datetime import datetime as _dt, timedelta
    fixed_now = _dt(2026, 5, 28, 16, 5, 0, tzinfo=TZ)  # 16:05 CT, after 15:00 close

    class _FakeDt(_dt):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now
            return fixed_now.astimezone(tz)

    monkeypatch.setattr(gamma_tool, "datetime", _FakeDt)

    # Build a chain with exp_date deterministic relative to the fake "today".
    base_date = _dt.strptime("2026-05-28", "%Y-%m-%d")
    dte_days = 2
    exp_date = (base_date + timedelta(days=dte_days)).strftime("%Y-%m-%d")
    exp_key = f"{exp_date}:{dte_days}"
    chain = {
        "underlyingPrice": 500.0,
        "callExpDateMap": {exp_key: {
            "495.0": [{"volatility": 22.0, "openInterest": 1000, "delta": 0.7,
                       "gamma": 0.05, "daysToExpiration": dte_days}],
            "500.0": [{"volatility": 20.0, "openInterest": 2000, "delta": 0.52,
                       "gamma": 0.06, "daysToExpiration": dte_days}],
            "505.0": [{"volatility": 21.0, "openInterest": 1500, "delta": 0.34,
                       "gamma": 0.05, "daysToExpiration": dte_days}],
        }},
        "putExpDateMap": {exp_key: {
            "495.0": [{"volatility": 23.0, "openInterest": 1100, "delta": -0.30,
                       "gamma": 0.05, "daysToExpiration": dte_days}],
            "500.0": [{"volatility": 21.0, "openInterest": 2200, "delta": -0.48,
                       "gamma": 0.06, "daysToExpiration": dte_days}],
            "505.0": [{"volatility": 22.0, "openInterest": 1300, "delta": -0.66,
                       "gamma": 0.05, "daysToExpiration": dte_days}],
        }},
    }

    eng_a = GammaEngine()
    eng_b = GammaEngine()
    standalone = eng_a.calc_vanna_from_chain(chain)
    _, _, _, bundled = eng_b.calc_all_from_chain(chain)
    assert standalone is not None and bundled is not None
    assert set(standalone["gex"].keys()) == set(bundled["gex"].keys())
    for k in standalone["gex"]:
        for side in ("call", "put", "net"):
            assert standalone["gex"][k][side] == pytest.approx(
                bundled["gex"][k][side], abs=1e-6,
            )


def test_calc_all_none_on_empty():
    eng = GammaEngine()
    result = eng.calc_all_from_chain({})
    assert result == (None, None, None, None)


def test_project_exposure_forward_vanna():
    """project_exposure_forward must support view='vanna' and return a strike->dollar map."""
    eng = GammaEngine()
    chain = _make_chain()
    # Populate _last_chain by calling calc_vanna_from_chain first.
    eng.calc_vanna_from_chain(chain)
    out = eng.project_exposure_forward("vanna", T_future=1 / 365.0)
    assert isinstance(out, dict)
    assert len(out) > 0
    for strike, val in out.items():
        assert isinstance(strike, float)
        assert isinstance(val, float)


def test_project_exposure_forward_unknown_view():
    """Unknown view should produce an empty dict (or raise — match existing convention)."""
    eng = GammaEngine()
    chain = _make_chain()
    eng.calc_vanna_from_chain(chain)
    try:
        out = eng.project_exposure_forward("nonsense", T_future=1 / 365.0)
        assert out == {}
    except (ValueError, KeyError):
        pass
