"""Pure forward-projection math (compute.project_gex_grid / project_em_cone)."""
import datetime as dt
from zoneinfo import ZoneInfo

from services.options_svc import compute

CT = ZoneInfo("America/Chicago")


def _chain(spot=100.0, dte=0):
    """Minimal Schwab-shaped chain: one nearest expiry, a few strikes, call+put."""
    exp = f"2026-07-11:{dte}"
    def leg(strike, gamma, oi, iv=20.0):
        return [{"strike": strike, "gamma": gamma, "openInterest": oi,
                 "volatility": iv, "delta": 0.5, "daysToExpiration": dte}]
    strikes = {95.0: (0.03, 1000), 100.0: (0.06, 4000), 105.0: (0.03, 1500)}
    call_map = {exp: {f"{k:.1f}": leg(k, g, oi) for k, (g, oi) in strikes.items()}}
    put_map = {exp: {f"{k:.1f}": leg(k, g, oi) for k, (g, oi) in strikes.items()}}
    return {"underlyingPrice": spot, "callExpDateMap": call_map, "putExpDateMap": put_map}


def test_future_marks_to_close():
    now = dt.datetime(2026, 7, 11, 13, 5, tzinfo=CT)   # 1:05pm CT
    marks = compute._future_marks_ct(now)
    assert marks[0].strftime("%H:%M") == "13:15"       # next quarter hour
    assert marks[-1].strftime("%H:%M") == "15:00"      # the close
    assert all(m.minute % 15 == 0 for m in marks)


def test_future_marks_empty_after_close():
    now = dt.datetime(2026, 7, 11, 15, 30, tzinfo=CT)
    assert compute._future_marks_ct(now) == []


def test_project_grid_shape_and_seam():
    import gamma_tool as gt
    eng = gt.GammaEngine()
    chain = _chain(dte=0)
    now = dt.datetime(2026, 7, 11, 13, 0, tzinfo=CT)
    proj = compute.project_gex_grid(eng, chain, 100.0, now)
    assert proj["times"] and len(proj["times"]) == len(next(iter(proj["grid"].values())))
    assert proj["spot"] == 100.0
    # 0-DTE net gamma concentrates toward ATM into the close: the ATM strike's
    # |net| at the LAST future mark >= its |net| at the FIRST (time decay sharpens).
    atm = proj["grid"]["100.0"]
    assert abs(atm[-1]) >= abs(atm[0])


def test_project_grid_empty_after_close():
    import gamma_tool as gt
    now = dt.datetime(2026, 7, 11, 15, 30, tzinfo=CT)
    proj = compute.project_gex_grid(gt.GammaEngine(), _chain(), 100.0, now)
    assert proj["times"] == [] and proj["grid"] == {}


def test_project_grid_defensive_on_bad_chain():
    import gamma_tool as gt
    now = dt.datetime(2026, 7, 11, 13, 0, tzinfo=CT)
    assert compute.project_gex_grid(gt.GammaEngine(), {}, 0, now)["grid"] == {}


def test_em_cone_sqrt_time_fan():
    now = dt.datetime(2026, 7, 11, 13, 0, tzinfo=CT)
    marks = compute._future_marks_ct(now)
    cone = compute.project_em_cone(100.0, 0.20, marks, now)
    assert len(cone["mid"]) == len(marks)
    assert all(m == 100.0 for m in cone["mid"])          # flat spot midline
    assert (cone["up"][-1] - 100.0) > (cone["up"][0] - 100.0)   # widens with sqrt(time)
    assert cone["down"][0] < 100.0 < cone["up"][0]
