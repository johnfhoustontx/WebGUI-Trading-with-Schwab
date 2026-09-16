"""The volatility floor over rows the REAL producers build.

B2 (docs/plans/2026-09-12-volatility-gate-design.md) keys the floor on a
candidate's own ``net_vega`` sign, and its tests handed ``swing_scan`` invented
rows like ``{"type": "PCS", "net_vega": -0.31}``. No producer emits that.
``scanner_engine.screen_spreads`` writes ``net_vega = short.vega - long.vega``,
POSITIVE for a credit spread, and ``strategy_scanner._normalize_credit`` carried
it into the normalized contract unchanged; an adapted iron condor read 0.0 from
its vega-0 legs. So every Strategy Finder and Income Window credit spread and
iron condor was invisible to the floor. Measured on prod's nightly Redis dumps:
the 2026-09-14 Income board, published two days AFTER the gate shipped, was two
IREN put credit spreads at IV rank 2.3, and nothing refused them.

The same inverted sign reached ``strategy_scoring.fit_vol``, which rewarded a
credit spread for LOW volatility: a SPY call credit spread on the 2026-09-11
board scored fit_vol 57.1 where the position-signed vega gives 42.9.

Every row here comes from ``screen_spreads`` / ``build_iron_condors`` and the
adapters, or the native builders, over a chain whose vega and theta fall away
from the money the way real ones do. A flat-greek chain makes every spread's net
vega exactly 0 and hides the defect entirely.
"""
import datetime as dt
import math
import pathlib

import pytest

from services.options_svc import compute

import paper_trader  # noqa: E402  (compute's import put options-scanner on sys.path)
import scanner_engine  # noqa: E402
import strategy_scanner as ssn  # noqa: E402
import strategy_scoring  # noqa: E402

from shared import vol_gate  # noqa: E402

_SCANNER_TESTS = pathlib.Path(__file__).resolve().parents[3] / "options-scanner" / "tests"
_SPOT = 100.0
_DTE = 35
_EXP = (dt.date.today() + dt.timedelta(days=_DTE)).isoformat()
_CREDIT = ("PCS", "CCS", "IC")


def _real_chain(monkeypatch):
    """``test_scanner_engine._chain_at`` with vega AND theta peaked at the money."""
    monkeypatch.syspath_prepend(str(_SCANNER_TESTS))
    from test_scanner_engine import _chain_at

    chain = _chain_at(_SPOT, _EXP, _DTE)
    for side in ("putExpDateMap", "callExpDateMap"):
        for strike, contracts in chain[side][f"{_EXP}:{_DTE}"].items():
            bump = math.exp(-((float(strike) - _SPOT) / 10) ** 2)
            contracts[0]["vega"] = round(0.15 * bump, 4)
            contracts[0]["theta"] = round(-0.06 * bump, 4)
    return chain


@pytest.fixture
def rows(monkeypatch):
    # The liquidity gate reads the clock; pin it open.
    monkeypatch.setattr(scanner_engine, "_is_options_market_open", lambda: True)
    chain = _real_chain(monkeypatch)
    spreads = scanner_engine.screen_spreads(chain, "IREN", 20, 50, -0.35, -0.10, 0.10, 0.35,
                                            0.0, "INCOME", max_risk_dollars=5000)
    raw_pcs = next(s for s in spreads if s["type"] == "PCS")
    raw_ccs = next(s for s in spreads if s["type"] == "CCS")
    ics = scanner_engine.build_iron_condors(spreads)
    assert ics, "the producers built no iron condor - the IC tests would be vacuous"
    out = {"raw PCS": raw_pcs, "raw IC": ics[0],
           "PCS": ssn.adapt_credit_spread(dict(raw_pcs)),
           "CCS": ssn.adapt_credit_spread(dict(raw_ccs)),
           "IC": ssn.adapt_iron_condor(dict(ics[0]))}
    for row in ssn.build_directional(chain, "IREN", _SPOT, 0.30, 20, 50):
        out.setdefault(row["type"], row)
    for row in ssn.build_debit_verticals(chain, "IREN", _SPOT, 0.30, 20, 50):
        out.setdefault(row["type"], row)
    assert {"SHORT_PUT", "LONG_CALL"} <= set(out), sorted(out)
    assert any(k in out for k in ("BULL_CALL", "BEAR_PUT")), sorted(out)
    return out


# ── the fixture has to be able to show the defect ────────────────────────────

def test_the_producer_really_emits_the_short_minus_long_convention(rows):
    """VACUITY GUARD. If ``screen_spreads`` ever emits position-signed greeks, the
    adapter's conversion would invert them and every test below would still need
    re-deriving - so pin the premise the fix is built on."""
    raw = rows["raw PCS"]
    assert raw["net_vega"] > 0 and raw["net_theta"] < 0
    assert raw["entry_net_theta_position"] == pytest.approx(-raw["net_theta"])


# ── the normalized contract is position-signed for every family ──────────────

@pytest.mark.parametrize("kind", _CREDIT)
def test_an_adapted_credit_structure_carries_negative_vega_and_positive_theta(rows, kind):
    row = rows[kind]
    assert row["net_vega"] < 0, (kind, row["net_vega"])
    assert row["net_theta"] > 0, (kind, row["net_theta"])


def test_an_adapted_credit_spread_agrees_in_sign_with_a_native_short_put(rows):
    """One jointly-ranked table: a short put and a put credit spread both SELL
    premium, so their vega must point the same way."""
    assert rows["SHORT_PUT"]["net_vega"] < 0
    assert rows["PCS"]["net_vega"] < 0


def test_the_adapted_iron_condor_vega_is_the_sum_of_its_two_sides(rows):
    ic = rows["IC"]
    p, c = rows["raw IC"]["_pcs_signal"], rows["raw IC"]["_ccs_signal"]
    assert ic["net_vega"] == pytest.approx(-(p["net_vega"] + c["net_vega"]), abs=1e-3)


# ── the floor now reaches them ───────────────────────────────────────────────

@pytest.mark.parametrize("kind", _CREDIT)
def test_the_floor_refuses_a_credit_structure_on_cheap_volatility(rows, kind):
    assert vol_gate.signal_blocks(rows[kind], floor=30, iv_rank=0.1) == vol_gate.IV_TOO_LOW


@pytest.mark.parametrize("kind", _CREDIT)
def test_the_floor_keeps_a_credit_structure_above_it(rows, kind):
    assert vol_gate.signal_blocks(rows[kind], floor=30, iv_rank=69.2) is None


def test_the_floor_still_keeps_the_long_premium_half(rows):
    for kind in ("LONG_CALL", "BULL_CALL", "BEAR_PUT"):
        if kind in rows:
            assert vol_gate.signal_blocks(rows[kind], floor=30, iv_rank=0.1) is None, kind


# ── the scorer reads the same sign ───────────────────────────────────────────

@pytest.mark.parametrize("kind", _CREDIT)
def test_fit_vol_penalises_selling_in_a_low_regime_and_rewards_it_in_a_high_one(rows, kind):
    vega = rows[kind]["net_vega"]
    assert strategy_scoring.fit_vol(vega, "low") < 50.0
    assert strategy_scoring.fit_vol(vega, "high") > 50.0


# ── end to end through swing_scan, the one call site for both surfaces ───────

def test_income_scan_at_a_cheap_iv_rank_publishes_no_credit_structure(rows, monkeypatch):
    """The live 2026-09-14 case: IV rank 2.3, floor 30. Real screen_spreads,
    build_iron_condors, adapters and scorer; only the fetches are stubbed."""
    chain = _real_chain(monkeypatch)
    monkeypatch.setattr(compute.se, "fetch_option_chain", lambda *a, **k: chain)
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda s: {"last": _SPOT})
    monkeypatch.setattr(compute.se, "fetch_price_history", lambda *a, **k: None)
    monkeypatch.setattr(compute.se, "calc_technicals", lambda *a, **k: {})
    monkeypatch.setattr(compute, "run_iv_analysis", lambda *a, **k: {
        "iv_rank": 2.3, "expected_moves": {"daily": {"move_dollars": 2.0}}})
    monkeypatch.setattr(compute, "_income_earnings", lambda s: ("none_scheduled", None))
    monkeypatch.setattr(compute, "SWING_MIN_SCORE", 0.0)
    monkeypatch.setattr(compute, "SWING_EXCLUDED_GRADES", ())

    unfloored = compute.swing_scan("IREN", 20, 50, -0.35, -0.10, 0.10, 0.35, 0.0,
                                   trade_type="NOT_A_WINDOW")
    built = {s["type"] for s in unfloored["signals"]}
    assert {"PCS", "CCS"} <= built, sorted(built)     # VACUITY: they exist ungated

    out = compute.swing_scan("IREN", 20, 50, -0.35, -0.10, 0.10, 0.35, 0.0,
                             trade_type="INCOME")
    kept = {s["type"] for s in out["signals"]}
    assert not kept & {"PCS", "CCS", "IC", "IRON_CONDOR", "SHORT_PUT", "SHORT_CALL"}, sorted(kept)
    assert out["vol_filtered"] >= sum(1 for s in unfloored["signals"]
                                      if s["type"] in ("PCS", "CCS", "IC"))


# ── the paper hand-off must not double-negate ────────────────────────────────

@pytest.mark.parametrize("raw_key,kind", [("raw PCS", "PCS"), ("raw IC", "IC")])
def test_a_finder_row_and_its_raw_scanner_row_book_the_same_greeks(rows, raw_key, kind):
    """``paper_trader`` stored ``entry_vega = -signal["net_vega"]``, correct for
    the scanner's short-minus-long row. A Finder row sent from the Strategy
    Finder is now position-signed, so negating it again would book a credit
    spread as LONG vega. Both paths must land on the same ledger values."""
    raw_trade = paper_trader.create_paper_trade(dict(rows[raw_key]), quantity=1)
    finder_trade = paper_trader.create_paper_trade(dict(rows[kind]), quantity=1)
    assert finder_trade["entry_vega"] < 0 and finder_trade["entry_theta"] > 0
    assert finder_trade["entry_vega"] == pytest.approx(raw_trade["entry_vega"], abs=1e-3)
    assert finder_trade["entry_theta"] == pytest.approx(raw_trade["entry_theta"], abs=1e-3)
    # The ledger's own ``net_theta`` column keeps the scanner convention the
    # Paper page reads, whichever page sent the trade.
    assert finder_trade["net_theta"] == pytest.approx(raw_trade["net_theta"], abs=1e-3)
