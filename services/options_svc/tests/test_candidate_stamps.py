"""Per-candidate stamps the checklist reads. Rows are built by real producers."""
import math

import pytest

from services.options_svc import compute


def _raw_pcs():
    """A screen_spreads-shaped PCS - the exact keys the scanner emits."""
    return {"id": "ORCL_PCS_2026-10-17_100.0_97.5", "symbol": "ORCL", "type": "PCS",
            "trade_type": "SWING", "expiration": "2026-10-17", "dte": 12,
            "short_strike": 100.0, "long_strike": 97.5, "width": 2.5,
            "short_mark": 0.95, "long_mark": 0.35, "credit": 0.60, "max_loss": 1.90,
            "spread_bid": 0.55, "spread_ask": 0.65, "net_vega": 0.02,
            "underlying_price": 110.0,
            "expected_moves": {"daily": {"move_dollars": 2.0}}}


def test_ledger_risk_equals_what_the_ledger_books_for_one_contract():
    import paper_trader
    row = _raw_pcs()
    stamped = compute.stamp_candidate(dict(row), trade_type="SWING")
    assert stamped["ledger_risk_per_contract"] == pytest.approx(
        paper_trader.create_paper_trade(row, 1)["max_loss_total"])
    assert stamped["ledger_risk_per_contract"] == pytest.approx(190.0)


def test_ledger_risk_for_a_normalized_spread_is_not_100x():
    import strategy_scanner
    norm = strategy_scanner.adapt_credit_spread(_raw_pcs())
    stamped = compute.stamp_candidate(dict(norm), trade_type="SWING")
    assert stamped["ledger_risk_per_contract"] == pytest.approx(190.0, abs=1.0)


def test_ledger_risk_is_none_for_a_structure_the_ledger_refuses():
    stamped = compute.stamp_candidate({"symbol": "SPY", "type": "LONG_STRADDLE"},
                                      trade_type="SWING")
    assert stamped["ledger_risk_per_contract"] is None


def test_friction_is_the_spread_width_over_the_credit():
    stamped = compute.stamp_candidate(_raw_pcs(), trade_type="SWING")
    assert stamped["friction_pct"] == pytest.approx(0.10 / 0.60 * 100, abs=0.05)


def test_friction_sums_leg_widths_when_there_are_no_spread_quotes():
    row = {"symbol": "X", "type": "LONG_CALL", "net_debit": 300.0,
           "legs": [{"side": "long", "kind": "call", "bid": 2.90, "ask": 3.10}]}
    stamped = compute.stamp_candidate(row, trade_type="SWING")
    assert stamped["friction_pct"] == pytest.approx(0.20 / 3.00 * 100, abs=0.05)


def test_friction_is_none_when_a_leg_has_no_quote():
    row = {"symbol": "X", "type": "LONG_CALL", "net_debit": 300.0,
           "legs": [{"side": "long", "kind": "call"}]}
    assert compute.stamp_candidate(row, trade_type="SWING")["friction_pct"] is None


def test_em_to_expiry_uses_the_daily_move_times_root_dte():
    stamped = compute.stamp_candidate(_raw_pcs(), trade_type="SWING")
    assert stamped["em_to_expiry"] == pytest.approx(2.0 * math.sqrt(12))


def test_em_to_expiry_reads_a_finder_rows_daily_em():
    row = {"symbol": "X", "type": "PCS", "dte": 0, "daily_em": 1.5}
    assert compute.stamp_candidate(row, trade_type="SWING")["em_to_expiry"] == pytest.approx(1.5)


def test_vol_floor_is_the_trade_types_floor():
    from shared import scanner_config
    stamped = compute.stamp_candidate(_raw_pcs(), trade_type="0-DTE")
    assert stamped["vol_floor"] is not None
    assert stamped["vol_floor"] == scanner_config.min_iv_rank().get("0-DTE")


def test_earnings_are_stamped_from_the_lookup_given(monkeypatch):
    stamped = compute.stamp_candidate(_raw_pcs(), trade_type="SWING",
                                      earnings=("upcoming", "2026-10-09"))
    assert (stamped["earnings_status"], stamped["earnings_date"]) == ("upcoming", "2026-10-09")


def test_iv_rank_known_is_stamped_when_given():
    stamped = compute.stamp_candidate(_raw_pcs(), trade_type="SWING", iv_rank_known=False)
    assert stamped["iv_rank_known"] is False


def test_friction_of_an_iron_condor_uses_its_summed_spread_quotes():
    row = {"symbol": "SPY", "type": "IC", "credit": 1.20,
           "spread_bid": 1.10, "spread_ask": 1.34}
    assert compute.stamp_candidate(row, trade_type="SWING")["friction_pct"] == pytest.approx(20.0)


def test_a_crossed_or_missing_quote_is_not_a_zero_friction():
    crossed = {**_raw_pcs(), "spread_bid": 0.70, "spread_ask": 0.60, "legs": []}
    assert compute.stamp_candidate(crossed, trade_type="SWING")["friction_pct"] is None
    no_credit = {"symbol": "X", "type": "PCS", "spread_bid": 0.5, "spread_ask": 0.6}
    assert compute.stamp_candidate(no_credit, trade_type="SWING")["friction_pct"] is None


def test_stamping_never_raises_on_a_junk_row():
    stamped = compute.stamp_candidate({"type": object()}, trade_type="SWING")
    assert stamped["ledger_risk_per_contract"] is None
    assert stamped["friction_pct"] is None and stamped["em_to_expiry"] is None


def test_swing_scan_stamps_its_daily_move_on_every_row():
    import inspect
    src = inspect.getsource(compute.swing_scan)
    assert 's["daily_em"] = dem' in src


def test_share_structures_have_no_friction_reading(monkeypatch):
    """A covered call / protective put / collar's net_debit is mostly the share
    purchase, so a friction % over it would read ~0 - "free", never measured."""
    import datetime as dt
    import pathlib

    import strategy_scanner

    tests_dir = pathlib.Path(__file__).resolve().parents[3] / "options-scanner" / "tests"
    monkeypatch.syspath_prepend(str(tests_dir))
    from test_scanner_engine import _chain_at

    exp = (dt.date.today() + dt.timedelta(days=10)).isoformat()
    chain = _chain_at(100.0, exp, 10)
    rows = strategy_scanner.build_stock_structures(chain, "ORCL", 100.0, 0.18, 5, 15)
    assert rows, "the producer built no share structures - the test would be vacuous"
    for row in rows:
        stamped = compute.stamp_candidate(dict(row), trade_type="SWING")
        assert stamped["friction_pct"] is None, row.get("type")


def test_a_non_positive_daily_move_has_no_expected_move():
    row = {"dte": 5, "daily_em": -1.0}
    assert compute.stamp_candidate(row, trade_type="SWING")["em_to_expiry"] is None
