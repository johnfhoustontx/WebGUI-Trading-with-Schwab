"""The /ES and /NQ chain feed's snapshot models.

ThinkorSwim prints ``N/A`` for a value it does not have and a percent sign on a
volatility; the row model must turn the first into an absent value and the second
into the number, and must never turn either into zero.
"""
from datetime import date

import pytest
from pydantic import ValidationError

from shared.contracts.futures_chain import FuturesChainSnapshot, FuturesContractRow


def _row(**kw):
    base = dict(series="E1AX26", side="C", strike=7600)
    return FuturesContractRow(**{**base, **kw})


def test_row_turns_na_into_absent_and_a_percent_string_into_a_number():
    r = FuturesContractRow(series="E1AX26", side="C", strike=7600, mark="N/A", bid=None,
                           ask="", volume=float("nan"), open_int=25, iv="12.51%", delta="N/A")
    assert (r.mark, r.bid, r.ask, r.volume, r.open_int, r.iv, r.delta) == (
        None, None, None, None, 25, 12.51, None)


@pytest.mark.parametrize("raw", ["abc%", "%", "1.2.3", float("inf"), float("-inf"),
                                 True, [1], {"a": 1}])
def test_an_unreadable_value_is_absent_not_zero(raw):
    assert _row(iv=raw).iv is None


def test_a_number_given_as_text_is_read():
    assert _row(mark=" 11.25 ", open_int="30").mark == 11.25
    assert _row(open_int="30").open_int == 30


@pytest.mark.parametrize("field", ["mark", "bid", "ask", "volume", "open_int"])
def test_a_negative_price_or_count_is_absent(field):
    assert getattr(_row(**{field: -1}), field) is None


def test_a_negative_delta_is_kept_it_is_a_put():
    assert _row(side="P", delta=-0.4).delta == -0.4


@pytest.mark.parametrize("strike", [0, -5, float("nan"), float("inf")])
def test_a_strike_must_be_a_positive_number(strike):
    with pytest.raises(ValidationError):
        _row(strike=strike)


def test_side_is_c_or_p():
    with pytest.raises(ValidationError):
        _row(side="CALL")


def test_snapshot_round_trips_through_json():
    snap = FuturesChainSnapshot(
        instrument="ES", expiry=date(2026, 11, 2), captured_at=1.8e9,
        descriptions={"E1AX26": "x"}, underlying_symbol="/ESZ26:XCME",
        underlying_price=7600.25, contracts=[_row(mark="N/A", iv="15%")])
    back = FuturesChainSnapshot.from_json(snap.to_json())
    assert back == snap
    assert back.contracts[0].mark is None and back.contracts[0].iv == 15.0
