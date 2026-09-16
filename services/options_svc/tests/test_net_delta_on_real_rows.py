"""An adapted credit structure's ``net_delta`` over rows the REAL producers build.

``strategy_scanner._normalize_credit`` took ``net_delta`` from ``payoff_metrics``
over the legs ``_credit_leg`` reconstructs, and those legs carry ``short_delta``
on the SHORT leg and **0 on every long leg** - an adapted iron condor carries 0
on all four. So an adapted PCS/CCS read ``-short_delta``, ignoring the long leg
that offsets it, and an adapted IC read exactly 0 whatever its skew.

``strategy_scoring.fit_directional`` scores ``tanh(net_delta / DELTA_SCALE)``,
and Finder/Income credit spreads share ONE ranked table with native families
whose ``net_delta`` is leg-exact. Measured on prod's nightly Redis dumps
(2026-09-11/14/15 Income boards) the adapted delta overstated the position's by
3-24x on the narrow spreads the board actually shows (IREN 38/37 PCS: 0.246 vs
0.032), moving ``fit_dir`` by 6-15 points and composite by 1.2-2.7: one board
re-ordered, and one row (IREN 35/33 PCS, 51.0 -> 49.5) sat above the Finder's
50-point cut only on the overstated delta. Across 905 stored PCS/CCS signals the
overstatement ratio's median is 7x.

``screen_spreads`` already writes the exact ``entry_net_delta_position``
(``long.delta - short.delta``); ``build_iron_condors`` now writes its two
verticals' sum; the adapter prefers it, as it does for theta and vega.
"""
import datetime as dt
import math
import pathlib

import pytest

from services.options_svc import compute

import scanner_engine  # noqa: E402  (compute's import put options-scanner on sys.path)
import strategy_scanner as ssn  # noqa: E402
import strategy_scoring  # noqa: E402

_SCANNER_TESTS = pathlib.Path(__file__).resolve().parents[3] / "options-scanner" / "tests"
_SPOT = 100.0
_DTE = 35
_EXP = (dt.date.today() + dt.timedelta(days=_DTE)).isoformat()


def _real_chain(monkeypatch):
    """``test_scanner_engine._chain_at`` - delta already falls with strike - with
    vega and theta peaked at the money, as in ``test_vol_gate_on_real_rows``, and
    a PUT SKEW: OTM put deltas 20% fatter than the mirror-image calls.

    The skew is load-bearing. ``_chain_at`` is exactly symmetric about spot, so
    the iron condors the producers rank first pair mirror-image verticals and are
    delta-neutral to the last digit - where a fabricated 0 and the true sum agree.
    Real index and equity chains carry exactly this skew."""
    monkeypatch.syspath_prepend(str(_SCANNER_TESTS))
    from test_scanner_engine import _chain_at

    chain = _chain_at(_SPOT, _EXP, _DTE)
    for side in ("putExpDateMap", "callExpDateMap"):
        for strike, contracts in chain[side][f"{_EXP}:{_DTE}"].items():
            bump = math.exp(-((float(strike) - _SPOT) / 10) ** 2)
            contracts[0]["vega"] = round(0.15 * bump, 4)
            contracts[0]["theta"] = round(-0.06 * bump, 4)
            if side == "putExpDateMap" and float(strike) < _SPOT:
                contracts[0]["delta"] = max(-0.99, round(contracts[0]["delta"] * 1.2, 4))
    return chain


def _chain_delta(chain, side, strike):
    key = "putExpDateMap" if side == "put" else "callExpDateMap"
    return chain[key][f"{_EXP}:{_DTE}"][str(float(strike))][0]["delta"]


@pytest.fixture
def chain(monkeypatch):
    return _real_chain(monkeypatch)


@pytest.fixture
def rows(monkeypatch, chain):
    monkeypatch.setattr(scanner_engine, "_is_options_market_open", lambda: True)
    spreads = scanner_engine.screen_spreads(chain, "IREN", 20, 50, -0.35, -0.10, 0.10, 0.35,
                                            0.0, "INCOME", max_risk_dollars=5000)
    raw_pcs = next(s for s in spreads if s["type"] == "PCS")
    raw_ccs = next(s for s in spreads if s["type"] == "CCS")
    ics = scanner_engine.build_iron_condors(spreads)
    assert ics, "the producers built no iron condor - the IC tests would be vacuous"
    return {"raw PCS": raw_pcs, "raw CCS": raw_ccs, "raw IC": ics[0],
            "PCS": ssn.adapt_credit_spread(dict(raw_pcs)),
            "CCS": ssn.adapt_credit_spread(dict(raw_ccs)),
            "IC": ssn.adapt_iron_condor(dict(ics[0]))}


# ── the fixture has to be able to show the defect ────────────────────────────

@pytest.mark.parametrize("raw_key", ["raw PCS", "raw CCS"])
def test_the_long_leg_carries_real_delta_on_this_chain(rows, chain, raw_key):
    """VACUITY GUARD. On a chain whose long leg had delta ~0 the short-leg-only
    reading and the exact one would agree, and every test below would pass on the
    old code. Pin that they differ materially, and that the producer's explicit
    field is exactly long minus short off the chain."""
    raw = rows[raw_key]
    side = "put" if raw["type"] == "PCS" else "call"
    short_d = _chain_delta(chain, side, raw["short_strike"])
    long_d = _chain_delta(chain, side, raw["long_strike"])
    assert abs(long_d) > 0.02, long_d
    assert raw["entry_net_delta_position"] == pytest.approx(long_d - short_d, abs=1e-4)
    assert abs(raw["entry_net_delta_position"] - (-raw["short_delta"])) > 0.02
    assert "net_delta" not in raw     # the premise: no raw net_delta to fall back on


# ── the normalized contract is position-exact ────────────────────────────────

@pytest.mark.parametrize("kind", ["PCS", "CCS"])
def test_an_adapted_credit_spread_net_delta_counts_the_long_leg(rows, kind):
    raw = rows[f"raw {kind}"]
    assert rows[kind]["net_delta"] == pytest.approx(raw["entry_net_delta_position"], abs=1e-4)


def test_the_sign_follows_the_structure(rows):
    assert rows["PCS"]["net_delta"] > 0
    assert rows["CCS"]["net_delta"] < 0


def test_a_raw_iron_condor_carries_its_two_verticals_position_delta(rows):
    ic = rows["raw IC"]
    p, c = ic["_pcs_signal"], ic["_ccs_signal"]
    assert ic["entry_net_delta_position"] == pytest.approx(
        p["entry_net_delta_position"] + c["entry_net_delta_position"], abs=1e-4)


def test_an_adapted_iron_condor_net_delta_is_not_a_fabricated_zero(rows):
    raw = rows["raw IC"]
    assert abs(raw["entry_net_delta_position"]) > 1e-3, (
        "VACUITY: this IC is exactly delta-neutral, so 0 would pass by accident")
    assert rows["IC"]["net_delta"] == pytest.approx(raw["entry_net_delta_position"], abs=1e-4)


@pytest.mark.parametrize("kind", ["PCS", "CCS", "IC"])
def test_the_adapted_row_stamps_the_explicit_position_field(rows, kind):
    assert rows[kind]["entry_net_delta_position"] == pytest.approx(rows[kind]["net_delta"])


def test_an_iron_condor_keeps_no_raw_net_delta(rows):
    """``options-scanner/scoring.py`` (the Market Scanner composite) must not start
    reading a scanner-convention ``net_delta`` it never had."""
    assert "net_delta" not in rows["raw IC"]


# ── the scorer reads the exact delta ─────────────────────────────────────────

@pytest.mark.parametrize("kind", ["PCS", "CCS"])
def test_fit_directional_scores_the_position_not_the_short_leg(rows, kind):
    view = {"direction": "bullish" if kind == "PCS" else "bearish", "conviction": 0.76}
    exact = strategy_scoring.fit_directional(rows[f"raw {kind}"]["entry_net_delta_position"], view)
    overstated = strategy_scoring.fit_directional(-rows[f"raw {kind}"]["short_delta"], view)
    assert exact != pytest.approx(overstated, abs=0.5)          # VACUITY
    assert strategy_scoring.fit_directional(rows[kind]["net_delta"], view) == pytest.approx(exact)


def test_swing_scan_scores_an_adapted_spread_off_its_exact_delta(rows, chain, monkeypatch):
    """End to end through the one call site both the Finder and the Income board
    use. IV rank above the floor so the rows are published, not gated."""
    monkeypatch.setattr(compute.se, "fetch_option_chain", lambda *a, **k: chain)
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda s: {"last": _SPOT})
    monkeypatch.setattr(compute.se, "fetch_price_history", lambda *a, **k: None)
    monkeypatch.setattr(compute.se, "calc_technicals", lambda *a, **k: {})
    monkeypatch.setattr(compute, "run_iv_analysis", lambda *a, **k: {
        "iv_rank": 69.2, "expected_moves": {"daily": {"move_dollars": 2.0}}})
    monkeypatch.setattr(compute, "SWING_MIN_SCORE", 0.0)
    monkeypatch.setattr(compute, "SWING_EXCLUDED_GRADES", ())

    out = compute.swing_scan("IREN", 20, 50, -0.35, -0.10, 0.10, 0.35, 0.0,
                             trade_type="NOT_A_WINDOW")
    spreads = [s for s in out["signals"] if s["type"] in ("PCS", "CCS")]
    assert spreads, sorted({s["type"] for s in out["signals"]})   # VACUITY
    for s in spreads:
        assert s["net_delta"] == pytest.approx(s["entry_net_delta_position"], abs=1e-4), s["id"]
        assert s["factor_scores"]["fit_dir"] == pytest.approx(
            strategy_scoring.fit_directional(s["entry_net_delta_position"], out["view"]), abs=0.05)
