"""The heatmaps' projected afternoon (services/options_svc/forward_grid.py).

Design: docs/plans/2026-10-23-heatmap-full-session-forward-design.md. The value
at the first mark must be the collected cell itself (the seam), and every later
mark the same open interest priced with less time left."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import forward_grid as fg

CT = ZoneInfo("America/Chicago")
FRIDAY = dt.date(2026, 10, 23)


def _at(h, m, s=0, d=FRIDAY):
    return dt.datetime(d.year, d.month, d.day, h, m, s, tzinfo=CT)


def _chain(spot=100.0, d=FRIDAY, dte=0, iv=20.0, delta=True):
    """Schwab-shaped nearest expiry, call-heavy so every net is non-zero."""
    exp = f"{d:%Y-%m-%d}:{dte}"
    book = {95.0: (0.03, 800, 1200), 100.0: (0.06, 5000, 1500),
            105.0: (0.03, 1600, 600), 110.0: (0.01, 300, 100)}

    def leg(k, g, oi, side):
        c = {"strike": k, "gamma": g, "openInterest": oi, "volatility": iv,
             "daysToExpiration": dte}
        if delta:
            c["delta"] = (0.5 if side == "call" else -0.5) * (1 if k == 100.0 else 0.6)
        return [c]

    calls = {exp: {f"{k:.1f}": leg(k, g, coi, "call") for k, (g, coi, poi) in book.items()}}
    puts = {exp: {f"{k:.1f}": leg(k, g, poi, "put") for k, (g, coi, poi) in book.items()}}
    return {"underlyingPrice": spot, "callExpDateMap": calls, "putExpDateMap": puts}


ALL = {v: {95.0, 100.0, 105.0, 110.0} for v in fg.VIEWS}


# ── marks ────────────────────────────────────────────────────────────────────

def test_marks_from_the_open():
    marks = fg.forward_marks(_at(8, 31, 20), 5)
    assert marks[0] == _at(8, 31, 20)
    assert marks[1] == _at(8, 35)
    assert marks[-1] == _at(14, 59)
    assert all(m.minute % 5 == 0 and m.second == 0 for m in marks[1:-1])
    assert marks == sorted(marks) and len(set(marks)) == len(marks)


def test_a_mark_on_the_step_is_not_repeated():
    marks = fg.forward_marks(_at(10, 0), 15)
    assert marks[:3] == [_at(10, 0), _at(10, 15), _at(10, 30)]
    assert marks[-2:] == [_at(14, 45), _at(14, 59)]


def test_no_marks_before_the_open_on_a_weekend_a_holiday_or_in_the_last_minute():
    assert fg.forward_marks(_at(8, 20), 5) == []
    assert fg.forward_marks(_at(10, 0, d=dt.date(2026, 10, 24)), 5) == []    # Saturday
    assert fg.forward_marks(_at(10, 0, d=dt.date(2026, 11, 26)), 5) == []    # Thanksgiving
    assert fg.forward_marks(_at(14, 59, 30), 5) == []
    assert fg.forward_marks(_at(15, 30), 5) == []


def test_marks_stop_a_minute_before_the_calendars_close():
    from shared import market_calendar as cal
    marks = fg.forward_marks(_at(12, 2), 5)
    assert marks[-1] == cal.regular_close_on(FRIDAY) - dt.timedelta(minutes=1)


# ── grids ────────────────────────────────────────────────────────────────────

def test_shape():
    marks = fg.forward_marks(_at(13, 2), 5)
    out = fg.build(_chain(), 100.0, marks, ALL)
    assert set(out) == set(fg.VIEWS)
    for view in fg.VIEWS:
        f = out[view]
        assert f["spot"] == 100.0
        assert f["times"][0] == "13:02" and f["times"][-1] == "14:59"
        assert f["ts"] == [int(m.timestamp()) for m in marks]
        assert set(f["call"]) == set(f["put"]) == {"95.0", "100.0", "105.0", "110.0"}
        assert all(len(v) == len(marks) for v in f["call"].values())


@pytest.mark.parametrize("hour", [8, 10, 14])
def test_the_first_mark_is_the_collected_cell(hour):
    """The seam: priced at ``now`` the forward value is the engine's own cell."""
    import gamma_tool as gt
    chain = _chain()
    now = _at(hour, 41, 10)
    marks = fg.forward_marks(now, 5)
    out = fg.build(chain, 100.0, marks, ALL)
    collected = dict(zip(fg.VIEWS, gt.GammaEngine().calc_all_from_chain(chain, now=now)))
    for view in fg.VIEWS:
        grid = collected[view]["gex"]
        for k in (95.0, 100.0, 105.0, 110.0):
            for side in ("call", "put"):
                want = grid[k][side]
                got = out[view][side][str(k)][0]
                assert got == pytest.approx(want, rel=1e-5, abs=1e-9), (view, k, side)


def test_0dte_atm_gamma_and_charm_grow_into_the_close():
    out = fg.build(_chain(), 100.0, fg.forward_marks(_at(9, 0), 5), ALL)
    for view in ("GEX", "Charm"):
        atm = out[view]["call"]["100.0"]
        assert abs(atm[-1]) > abs(atm[0]) > 0


def test_a_later_expiry_still_decays_toward_the_close():
    """dte 2: time left is two days plus the hours to 15:00, so values move."""
    out = fg.build(_chain(dte=2), 100.0, fg.forward_marks(_at(9, 0), 5), ALL)
    atm = out["Charm"]["call"]["100.0"]
    assert atm[0] != atm[-1]


def test_only_the_given_strikes_are_priced_and_only_the_views_asked_for():
    out = fg.build(_chain(), 100.0, fg.forward_marks(_at(9, 0), 5),
                   {"GEX": {100.0, 105.0}, "DEX": {95.0}})
    assert set(out) == {"GEX", "DEX"}
    assert set(out["GEX"]["call"]) == {"100.0", "105.0"}
    assert set(out["DEX"]["put"]) == {"95.0"}


def test_no_volatility_holds_gex_and_dex_flat_and_prices_no_charm():
    out = fg.build(_chain(iv=0.0), 100.0, fg.forward_marks(_at(9, 0), 5), ALL)
    for view in ("GEX", "DEX"):
        row = out[view]["call"]["100.0"]
        assert row[0] != 0 and all(v == row[0] for v in row)
    assert all(v == 0 for row in out["Charm"]["call"].values() for v in row)


def test_dex_falls_back_to_the_model_without_a_chain_delta():
    """The engine's rule: no usable chain delta means Black-Scholes delta."""
    import gamma_tool as gt
    chain = _chain(delta=False)
    now = _at(11, 0)
    out = fg.build(chain, 100.0, fg.forward_marks(now, 5), ALL)
    dex = gt.GammaEngine().calc_all_from_chain(chain, now=now)[2]["gex"]
    assert out["DEX"]["call"]["105.0"][0] == pytest.approx(dex[105.0]["call"], rel=1e-5)
    assert out["DEX"]["call"]["105.0"][-1] != out["DEX"]["call"]["105.0"][0]


def test_significant_figures_match_the_history_and_never_make_a_nan():
    """Far-out strikes price to denormal floats; scaling one up to six figures
    overflowed to inf and came back NaN, which is not JSON a browser can read."""
    import math
    import sys
    vals = [123456789.0, -0.000123456789, 1e-310, -5e-324, 0.0, 1e-200, 2.5e15]
    out = fg._significant(vals).tolist()
    assert all(math.isfinite(v) for v in out)
    assert out[0] == pytest.approx(123457000.0) and out[1] == pytest.approx(-0.000123457)
    assert out[2] == out[3] == out[4] == 0.0
    assert out[5] == pytest.approx(1e-200) and out[6] == pytest.approx(2.5e15)
    assert sys.float_info.min > 1e-310            # the case really is a denormal


# ── attach ───────────────────────────────────────────────────────────────────

def _views():
    """Cropped entries: a by-strike grid and a history row, keys as floats."""
    row = (1, 100.0, None, None, None, None, {105.0: {"net": 1.0}, 110.0: {"net": 1.0}})
    return {v: {"data": {"gex": {95.0: {}, 100.0: {}}}, "history": [row]} for v in fg.VIEWS}


def _cone(marks):
    return {"mid": [100.0] * len(marks), "up": [101.0] * len(marks),
            "down": [99.0] * len(marks)}


def test_kept_strikes_are_the_grid_and_the_history():
    assert fg.kept_strikes(_views()["GEX"]) == {95.0, 100.0, 105.0, 110.0}
    assert fg.kept_strikes({"data": {"gex": {"100.0": {}}}, "history": [(1, 2)]}) == {100.0}
    assert fg.kept_strikes(None) == set()


def test_attach_puts_a_band_and_the_cone_on_every_view():
    views = fg.attach(_views(), _chain(), 100.0, _at(10, 1), _cone)
    for view in fg.VIEWS:
        band = views[view]["forward"]
        assert set(band["call"]) == {"95.0", "100.0", "105.0", "110.0"}
        assert band["cone"]["up"][0] == 101.0
        assert len(band["cone"]["mid"]) == len(band["ts"])


def test_attach_reads_the_step(monkeypatch):
    from shared import gamma_heat_config as cfg
    monkeypatch.setattr(cfg, "forward", lambda: {"enabled": True, "step_min": 30})
    band = fg.attach(_views(), _chain(), 100.0, _at(10, 1), _cone)["GEX"]["forward"]
    assert band["times"][:3] == ["10:01", "10:30", "11:00"]


def test_attach_does_nothing_when_switched_off_or_out_of_session(monkeypatch):
    from shared import gamma_heat_config as cfg
    views = fg.attach(_views(), _chain(), 100.0, _at(10, 0, d=dt.date(2026, 10, 24)), _cone)
    assert not any("forward" in e for e in views.values())
    monkeypatch.setattr(cfg, "forward", lambda: {"enabled": False, "step_min": 5})
    views = fg.attach(_views(), _chain(), 100.0, _at(10, 0), _cone)
    assert not any("forward" in e for e in views.values())


def test_attach_survives_a_failing_cone():
    def boom(marks):
        raise RuntimeError("no iv")
    views = fg.attach(_views(), _chain(), 100.0, _at(10, 0), boom)
    assert not any("forward" in e for e in views.values())


@pytest.mark.parametrize("chain,spot", [({}, 100.0), (None, 100.0), ("junk", 100.0),
                                        ({"callExpDateMap": {}, "putExpDateMap": {}}, 100.0)])
def test_bad_input_is_empty(chain, spot):
    assert fg.build(chain, spot, fg.forward_marks(_at(9, 0), 5), ALL) == {}


def test_no_spot_or_no_marks_is_empty():
    assert fg.build(_chain(), 0.0, fg.forward_marks(_at(9, 0), 5), ALL) == {}
    assert fg.build(_chain(), 100.0, [], ALL) == {}
