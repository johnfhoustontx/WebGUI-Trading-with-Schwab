"""The Strategy Finder's credit-spread pass: sized against the Paper Ledger's cap,
and its pre-scoring rejections published so an empty list can say why.

Reuses ``test_income_scan``'s seams: ``swing_scan`` is one function for both the
Finder and the Income Window, and that fixture already stubs its seven I/O seams.
"""
import pytest

import services.options_svc.compute as compute
from services.options_svc.tests.test_income_scan import income_seams  # noqa: F401


def _scan():
    return compute.swing_scan("SPY", 30, 45, -0.20, -0.10, 0.10, 0.20, 0.10)


def test_the_width_search_is_sized_against_the_ledger_cap(income_seams, monkeypatch):
    # Patched to a value neither book ships, so the test proves WHICH constant
    # is read - both caps ship at $750 - and that it is read at call time.
    import config_paper
    monkeypatch.setattr(config_paper, "LEDGER_MAX_RISK_PER_TRADE", 123.0)
    monkeypatch.setattr(config_paper, "MAX_RISK_PER_TRADE", 456.0)
    _scan()
    assert income_seams["screen"]["kw"]["max_risk_dollars"] == 123.0


def test_the_reject_tally_rides_back_on_the_result(income_seams):
    out = _scan()
    assert out["credit_spreads"] == {
        "strikes": 9, "built": 2,
        "reasons": {"outside_move": 1, "edge_floor": 4, "credit_floor": 2}}


def test_no_tally_when_the_credit_families_were_not_requested(income_seams):
    out = compute.swing_scan("SPY", 30, 45, -0.20, -0.10, 0.10, 0.20, 0.10,
                             families=["DIRECTIONAL"])
    assert "screen" not in income_seams
    assert out["credit_spreads"] is None


def test_an_early_empty_answer_carries_the_key_too():
    assert compute._scan_result(expiries_failed=0, spot=None)["credit_spreads"] is None


# ── credit_spread_summary (pure) ─────────────────────────────────────────────

def test_summary_folds_stages_into_reader_reasons():
    funnel = {"delta_pass": 209, "width_found": 0, "em_fail": 49,
              "liq_fail_short": 15, "mark_fail": 0,
              "width_reasons": {"credit_floor": 82, "edge_floor": 63,
                                "long_leg_illiquid": 2, "over_trade_cap": 3,
                                "no_contracts": 1, "sanity_cap": 5}}
    assert compute.credit_spread_summary(funnel) == {
        "strikes": 209, "built": 0,
        "reasons": {"outside_move": 49, "illiquid": 17, "credit_floor": 82,
                    "edge_floor": 63, "over_cap": 4, "other": 5}}


def test_summary_accepts_a_published_plain_dict_and_drops_zeroes():
    out = compute.credit_spread_summary({"delta_pass": 3, "width_found": 3,
                                         "em_fail": 0, "width_reasons": {}})
    assert out == {"strikes": 3, "built": 3, "reasons": {}}


@pytest.mark.parametrize("bad", [None, [], "x"])
def test_summary_of_no_funnel_is_none(bad):
    assert compute.credit_spread_summary(bad) is None
