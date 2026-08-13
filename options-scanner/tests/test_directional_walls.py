"""Tests for directional call/put walls (GEX basis + OI basis).

Pure functions. Synthetic grid/chain; single far-future expiry key keeps the
suite date-independent.
"""
from gamma_tool import (
    get_directional_walls,
    get_oi_walls,
    build_analysis_dict,
    build_explain_text,
    GammaEngine,
)

EXP = "2099-12-31:5"


# ── get_directional_walls (GEX basis) ──

def _grid(cells):
    """cells: {strike: (call, put)}; net auto-computed."""
    return {
        "gex": {k: {"call": c, "put": p, "net": c + p}
                for k, (c, p) in cells.items()},
        "spot": 100.0,
    }


def test_directional_walls_picks_above_and_below_spot():
    data = _grid({
        95.0:  (10.0, -500.0),   # below spot — strong put
        98.0:  (5.0,  -100.0),
        102.0: (200.0, -5.0),
        105.0: (800.0, -10.0),   # above spot — strongest call
    })
    w = get_directional_walls(data, spot=100.0)
    assert w["call_wall"] == 105.0
    assert w["put_wall"] == 95.0


def test_directional_walls_none_when_side_empty():
    data = _grid({105.0: (800.0, -10.0)})  # nothing below spot
    w = get_directional_walls(data, spot=100.0)
    assert w["call_wall"] == 105.0
    assert w["put_wall"] is None


def test_directional_walls_empty_grid():
    w = get_directional_walls({"gex": {}, "spot": 100.0}, spot=100.0)
    assert w == {"call_wall": None, "put_wall": None}


# ── get_oi_walls (OI basis) ──

def _chain(spot, calls, puts):
    def _side(oi):
        return {EXP: {f"{k:.1f}": [{"openInterest": v}] for k, v in oi.items()}}
    return {
        "underlyingPrice": spot,
        "callExpDateMap": _side(calls),
        "putExpDateMap": _side(puts),
    }


def test_oi_walls_picks_above_and_below_spot():
    chain = _chain(
        spot=100.0,
        calls={102.0: 300, 105.0: 900, 98.0: 50},
        puts={95.0: 700, 98.0: 200, 105.0: 30},
    )
    w = get_oi_walls(chain, spot=100.0)
    assert w["call_wall"] == 105.0
    assert w["put_wall"] == 95.0


def test_oi_walls_none_when_side_empty():
    chain = _chain(spot=100.0, calls={105.0: 900}, puts={105.0: 30})
    w = get_oi_walls(chain, spot=100.0)
    assert w["call_wall"] == 105.0
    assert w["put_wall"] is None


# ── build_analysis_dict wiring ──

def test_analysis_dict_includes_walls_block():
    chain = {
        "underlyingPrice": 5800.0,
        "callExpDateMap": {EXP: {
            "5850.0": [{"delta": 0.3, "gamma": 0.04, "openInterest": 900,
                        "totalVolume": 0, "strikePrice": 5850}],
            "5800.0": [{"delta": 0.5, "gamma": 0.05, "openInterest": 100,
                        "totalVolume": 0, "strikePrice": 5800}],
        }},
        "putExpDateMap": {EXP: {
            "5750.0": [{"delta": -0.3, "gamma": 0.04, "openInterest": 800,
                        "totalVolume": 0, "strikePrice": 5750}],
            "5800.0": [{"delta": -0.5, "gamma": 0.05, "openInterest": 100,
                        "totalVolume": 0, "strikePrice": 5800}],
        }},
    }
    gex = GammaEngine().calc_from_chain(chain)
    result = build_analysis_dict(gex, "gex", "SPX", dte=0,
                                 expected_move=40.0, chain=chain)
    assert "walls" in result
    assert set(result["walls"].keys()) == {"gex", "oi"}
    assert result["walls"]["oi"]["call_wall"] == 5850.0
    assert result["walls"]["oi"]["put_wall"] == 5750.0


# ── Explain wiring ──

def test_explain_gex_shows_directional_walls():
    ctx = {
        "symbol": "SPX", "spot": 5805.0, "dte": 0,
        "vix_now": None, "vix_delta": None,
        "gex_summary": {"spot": 5805.0, "flip": 5800.0,
                        "top_pos_strike": 5850.0, "top_neg_strike": 5750.0,
                        "net_total": 1.0e9},
        "walls": {"gex": {"call_wall": 5850.0, "put_wall": 5750.0},
                  "oi": {"call_wall": 5860.0, "put_wall": 5740.0}},
        "sentiment": {"active": False},
    }
    text = build_explain_text("gex", ctx)
    assert "Call wall" in text and "Put wall" in text
    assert "OI" in text  # OI-basis comparison line present


# ── max_pct bound (tail-strike rejection) ──

def test_directional_walls_unbounded_by_default_picks_tail_strike():
    """The historical behaviour, kept: no bound means the extreme strike wins.

    This is the production failure that motivated max_pct — a deep tail strike
    carrying the largest put entry once the near-the-money rows dropped out.
    """
    data = _grid({
        47.0:  (1.0, -900.0),    # deep tail — 53% below spot, biggest put
        95.0:  (10.0, -500.0),
        105.0: (400.0, -5.0),
    })
    assert get_directional_walls(data, 100.0)["put_wall"] == 47.0


def test_directional_walls_max_pct_rejects_tail_strike():
    data = _grid({
        47.0:  (1.0, -900.0),    # 53% away — excluded
        95.0:  (10.0, -500.0),   # 5% away — the real wall
        105.0: (400.0, -5.0),
    })
    out = get_directional_walls(data, 100.0, max_pct=10.0)
    assert out["put_wall"] == 95.0
    assert out["call_wall"] == 105.0


def test_directional_walls_max_pct_none_when_every_strike_is_far():
    """Better no wall than a confident wrong one."""
    data = _grid({47.0: (1.0, -900.0), 160.0: (400.0, -5.0)})
    out = get_directional_walls(data, 100.0, max_pct=10.0)
    assert out["put_wall"] is None
    assert out["call_wall"] is None


def test_directional_walls_max_pct_keeps_boundary_strike():
    """The bound is inclusive — a strike exactly at the limit still counts."""
    data = _grid({90.0: (1.0, -900.0), 110.0: (400.0, -5.0)})
    out = get_directional_walls(data, 100.0, max_pct=10.0)
    assert out["put_wall"] == 90.0
    assert out["call_wall"] == 110.0
