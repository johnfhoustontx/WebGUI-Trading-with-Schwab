"""Write-side models for the five money-path views (audit AR-08).

These views were bare dictionaries: nothing checked a payload before it replaced
the last good one. Each model is small on purpose. It checks the container
shapes and the few numbers a wrong value of would be read as a real one, and it
keeps every key it does not name, because the service publishes the payload it
validated, not a re-serialized copy.
"""
import pytest
from pydantic import ValidationError

from shared.contracts.options import (LedgerCaps, PaperAccountView,
                                      PaperCreateResult, PaperTradesView,
                                      RescueSummary)

ACCOUNT = {"snapshot": {"cash": 24000.0, "equity": 24184.2}, "positions": [{"position_id": 1}],
           "orders": [], "lots": [], "has_account": True, "perf": None,
           "greeks": {"net_delta": 1.4}}


def test_the_paper_account_view_validates_and_keeps_every_key():
    data = dict(ACCOUNT, something_new=[1, 2])
    assert PaperAccountView(**data).model_dump()["something_new"] == [1, 2]


def test_an_account_with_no_account_is_valid():
    PaperAccountView(snapshot=None, positions=[], orders=[], lots=[],
                     has_account=False, perf=None, greeks=None)


@pytest.mark.parametrize("bad", [
    {"positions": {"1": {}}},            # a mapping where the page iterates rows
    {"positions": [1, 2]},               # rows that are not rows
    {"orders": "none"},
    {"lots": [None]},
    {"snapshot": [1]},
    {"has_account": "maybe"},
])
def test_a_misshapen_paper_account_is_refused(bad):
    with pytest.raises(ValidationError):
        PaperAccountView(**dict(ACCOUNT, **bad))


def test_the_rescue_summary_counts_are_whole_and_not_negative():
    RescueSummary(n_tested=2, n_critical=1, position_ids=[5, "SIG1"])
    RescueSummary()
    for bad in ({"n_tested": -1}, {"n_critical": 1.5}, {"n_tested": None},
                {"position_ids": 5}, {"n_critical": float("nan")}):
        with pytest.raises(ValidationError):
            RescueSummary(**bad)


def test_the_ledger_view_is_a_list_of_rows():
    PaperTradesView(trades=[{"trade_id": 1}], extra_key=True)
    PaperTradesView()
    for bad in ({"trades": {"1": {}}}, {"trades": [3]}, {"trades": None}):
        with pytest.raises(ValidationError):
            PaperTradesView(**bad)


CAPS = {"open": [{"symbol": "SPY", "max_loss_total": 250.0}], "starting_balance": 25000.0,
        "realized_pnl": -120.5, "equity": 24879.5,
        "limits": {"max_risk_per_trade": 750}, "sectors": {"SPY": "INDEX"},
        "unmapped_prefix": "?"}


def test_the_ledger_caps_book_validates():
    assert LedgerCaps(**CAPS).equity == 24879.5


@pytest.mark.parametrize("field", ["starting_balance", "realized_pnl", "equity"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "a lot", True])
def test_a_caps_book_with_a_number_that_is_not_one_is_refused(field, bad):
    # A NaN equity makes every cap comparison False: the caps switch off and
    # the dialog says the trade fits.
    with pytest.raises(ValidationError):
        LedgerCaps(**dict(CAPS, **{field: bad}))


def test_a_caps_figure_that_is_not_known_is_none_not_a_number():
    # The preview skips a cap it has no equity for; None says "not known".
    assert LedgerCaps(**dict(CAPS, equity=None)).equity is None


def test_a_caps_book_with_misshapen_rows_or_limits_is_refused():
    for bad in ({"open": {"SPY": 1}}, {"open": ["SPY"]}, {"limits": [750]},
                {"sectors": ["INDEX"]}):
        with pytest.raises(ValidationError):
            LedgerCaps(**dict(CAPS, **bad))


def test_a_paper_create_answer_names_one_of_the_four_outcomes():
    for status in ("opened", "refused", "stale", "error"):
        PaperCreateResult(status=status, seq=1, ts="2026-10-05T10:00:00-05:00")
    for bad in ({"status": "ok"}, {"status": None}, {"status": "opened", "seq": "x"},
                {"status": "refused", "rungs": "all"}, {}):
        with pytest.raises(ValidationError):
            PaperCreateResult(**bad)


def test_a_paper_create_answer_keeps_its_rungs_and_message():
    out = PaperCreateResult(status="refused", seq=3, rungs=[{"code": "SYMBOL_RISK"}],
                            message="The symbol's risk cap is full.",
                            symbol="SPY", code="SYMBOL_RISK").model_dump()
    assert out["rungs"] == [{"code": "SYMBOL_RISK"}] and out["code"] == "SYMBOL_RISK"
