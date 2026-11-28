"""A NaN or an infinity is no reading on the Strategy detail panel.

Measured 2026-11-28 before the fix: ``strategy_table._num`` returned any int or
float unchanged, so a NaN or infinite ``net_credit`` reached the panel as the
credit and the net cost, and a NaN ``max_loss`` was shown as the max loss. The
panel's fields must now read exactly as they do for a missing value.
"""
import pytest

from pages.options import strategy_table as st

BAD = [float("nan"), float("inf"), float("-inf"), True, "nan", "inf"]
IDS = ["nan", "inf", "-inf", "True", "'nan'", "'inf'"]

SIG = {"strategy": "SHORT_PUT", "net_credit": 150.0, "max_loss": 4850.0,
       "breakevens": [498.5]}
PANEL = ("credit", "max_loss", "net_cost", "breakeven")


def _panel(sig):
    out = st.detail_signal(sig)
    return {k: repr(out.get(k)) for k in PANEL}


CASES = {
    "net_credit": lambda v: {**SIG, "net_credit": v},
    "max_loss": lambda v: {**SIG, "max_loss": v},
    "net_debit": lambda v: {"strategy": "LONG_CALL", "net_debit": v},
    "credit": lambda v: {"strategy": "LONG_CALL", "credit": v},
}


@pytest.mark.parametrize("bad", BAD, ids=IDS)
@pytest.mark.parametrize("field", sorted(CASES))
def test_a_bad_number_reads_as_a_missing_one(field, bad):
    build = CASES[field]
    assert _panel(build(bad)) == _panel(build(None))


@pytest.mark.parametrize("field", sorted(CASES))
def test_the_cases_are_not_vacuous(field):
    build = CASES[field]
    assert _panel(build(250.0)) != _panel(build(None))


def test_the_input_is_still_not_mutated():
    sig = {**SIG, "max_loss": float("nan")}
    st.detail_signal(sig)
    assert sig["max_loss"] != sig["max_loss"]          # still the NaN it was
