"""The Go / No-Go checklist over rows the REAL producers build.

``webgui/pages/options/checks`` is unit-tested over hand-written fixtures, and
that is exactly how its first short-premium rule shipped wrong: the scanner's
``net_vega`` is ``short.vega - long.vega`` (POSITIVE for a credit spread, not
position-signed), ``adapt_credit_spread`` carried it across unchanged until
2026-09-16, and an adapted iron condor's legs carry vega 0. Measured on these producers, a Finder
put or call credit spread lost its Vol rank / Expected move / Walls / Dealer
gamma lines and a Finder iron condor read "Clear · 3 checked". A fixture written
to the assumption cannot see that; only the producers can.

Rows come from ``scanner_engine.screen_spreads`` / ``build_iron_condors`` and
``strategy_scanner``'s adapters and builders, stamped by the real
``compute.stamp_candidate``.
"""
import datetime as dt
import math
import pathlib

import pytest

from services.options_svc import compute

_REPO = pathlib.Path(__file__).resolve().parents[3]
_WEBGUI = _REPO / "webgui"
_SCANNER_TESTS = _REPO / "options-scanner" / "tests"

MATRIX = {"spot": 100.4, "put_wall": 80.0, "call_wall": 120.0, "gex_regime": "above",
          "trend_dir": 0.4, "trend_state": "up"}
REGIME = {"direction": 1}
CAPS = {"limits": {"max_positions_per_symbol": 3, "max_risk_per_symbol": 750.0,
                   "max_positions_per_expiry": 5, "max_positions_per_sector": 5,
                   "max_risk_per_sector": 1500.0, "max_deployed_risk_pct": 0.2,
                   "max_risk_per_trade": 250.0},
        "equity": 25000.0, "open": [], "sectors": {"ORCL": "IT"}}
_SHORT_PREMIUM_LINES = {"vol", "em", "wall", "gamma"}


@pytest.fixture
def checks(monkeypatch):
    monkeypatch.syspath_prepend(str(_WEBGUI))
    from pages.options import checks as mod
    return mod


@pytest.fixture
def rows(monkeypatch):
    import scanner_engine
    import strategy_scanner as ssn

    monkeypatch.syspath_prepend(str(_SCANNER_TESTS))
    from test_scanner_engine import _chain_at

    # The liquidity gate reads the clock; pin it open so the rows do not depend
    # on the hour the suite runs.
    monkeypatch.setattr(scanner_engine, "_is_options_market_open", lambda: True)
    spot = 100.0
    exp = (dt.date.today() + dt.timedelta(days=12)).isoformat()
    chain = _chain_at(spot, exp, 12)
    # The fixture's vega is flat (0.10 everywhere), which would make every
    # spread's net vega exactly 0 and hide the defect. Real vega falls away from
    # the money, so a credit spread's short.vega - long.vega comes out POSITIVE.
    for side in ("putExpDateMap", "callExpDateMap"):
        for strike, contracts in chain[side][f"{exp}:12"].items():
            contracts[0]["vega"] = round(0.15 * math.exp(-((float(strike) - spot) / 10) ** 2), 4)

    spreads = scanner_engine.screen_spreads(chain, "ORCL", 5, 20, -0.35, -0.10, 0.10, 0.35,
                                            0.0, "SWING", max_risk_dollars=5000)
    pcs = next(s for s in spreads if s["type"] == "PCS")
    ccs = next(s for s in spreads if s["type"] == "CCS")
    ics = scanner_engine.build_iron_condors(spreads)
    assert ics, "the producers built no iron condor - the test would be vacuous"

    out = {"raw PCS": dict(pcs),
           "adapted PCS": ssn.adapt_credit_spread(dict(pcs)),
           "adapted CCS": ssn.adapt_credit_spread(dict(ccs)),
           "adapted IC": ssn.adapt_iron_condor(dict(ics[0]))}
    for row in ssn.build_debit_verticals(chain, "ORCL", spot, 0.18, 5, 20):
        out["debit " + row["type"]] = row
    for row in ssn.build_directional(chain, "ORCL", spot, 0.18, 5, 20):
        if row["type"].startswith("LONG_"):
            out["long " + row["type"]] = row
    assert any(k.startswith("debit ") for k in out) and any(k.startswith("long ") for k in out)

    # The real stamps, plus the facts the page adds (a Finder row's Fit score,
    # the Paper gate) - none of which the short-premium rule reads.
    for name, row in out.items():
        compute.stamp_candidate(row, trade_type="SWING", earnings=("none_scheduled", None),
                                iv_rank_known=True)
        row.setdefault("iv_rank", 55.0)
        row["daily_em"] = 2.0
        row["em_to_expiry"] = compute._em_to_expiry(row)
        row["_allow_paper"] = row.get("ledger_risk_per_contract") is not None
        if not name.startswith("raw"):
            row["fit_score"] = 60.0
    return out


def _keys(checks, row, regime=REGIME):
    return {c["key"]: c for c in checks.build_checks(row, MATRIX, regime, {}, CAPS)}


@pytest.mark.parametrize("name", ["raw PCS", "adapted PCS", "adapted CCS", "adapted IC"])
def test_credit_structures_get_the_short_premium_lines(checks, rows, name):
    got = _keys(checks, rows[name])
    assert _SHORT_PREMIUM_LINES <= set(got), (name, sorted(got), rows[name].get("net_vega"))


def test_debit_verticals_and_long_options_get_none_of_them(checks, rows):
    long_rows = {k: v for k, v in rows.items() if k.startswith(("debit ", "long "))}
    for name, row in long_rows.items():
        assert not _SHORT_PREMIUM_LINES & set(_keys(checks, row)), name


def test_an_adapted_iron_condor_has_no_direction_line(checks, rows):
    assert "direction" not in _keys(checks, rows["adapted IC"])


def test_an_adapted_call_spread_reads_bearish_against_a_rising_market(checks, rows):
    line = _keys(checks, rows["adapted CCS"], regime={"direction": 1})["direction"]
    assert line["tone"] == "warn" and "bearish" in line["text"]
