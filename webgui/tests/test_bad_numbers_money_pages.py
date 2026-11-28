"""A NaN, an infinity or a bool is no reading on the Rescue, Captured and Paper
pages: each must render exactly what a missing value renders.

Measured 2026-11-28 before the fix, with each page's bare-``float()`` helper:
Rescue's ``cash_text`` printed the no-reading dash in RED instead of $0.00,
``heat_border_class`` tinted a row with no heat, and ``adhoc_spec_from_legs``
(also the public Rescue form's reader) RAISED on a quantity of nan or inf
despite "never raises", and built a spread with ``short_strike: inf``.
Captured printed ``$nan``, raised in ``_count``, and counted a NaN mark as
priced. Paper handed the detail panel a NaN or infinite max loss and credit.
"""
import pytest

from pages.options import captured, paper, rescue

BAD = [float("nan"), float("inf"), float("-inf"), True, "nan", "inf"]
BAD_IDS = ["nan", "inf", "-inf", "True", "'nan'", "'inf'"]

LEGS = [{"option_type": "put", "side": "short", "strike": 500, "expiry": "2026-12-18",
         "qty": 1, "premium": 3.0},
        {"option_type": "put", "side": "long", "strike": 495, "expiry": "2026-12-18",
         "qty": 1, "premium": 1.0}]

TRADE = {"symbol": "SPY", "strategy": "PCS", "entry_credit": 1.2, "max_loss_per": 3.8,
         "short_delta": -0.2, "width": 5.0, "net_theta": 0.03,
         "underlying_at_entry": 500.0, "entry_vega": -0.1, "expiration": "2026-12-18"}


def _legs_with(key, value):
    return [{**LEGS[0], key: value}, LEGS[1]]


def _synth(key, value):
    out = paper.synth_from_trade({**TRADE, key: value})
    out.pop("dte", None)                 # the day count, not under test
    return out


CASES = {
    "rescue cash_text": rescue.cash_text,
    "rescue cash_class": rescue.cash_class,
    "rescue heat_border_class": rescue.heat_border_class,
    "rescue adhoc legs: qty": lambda v: rescue.adhoc_spec_from_legs("SPY", _legs_with("qty", v)),
    "rescue adhoc legs: premium":
        lambda v: rescue.adhoc_spec_from_legs("SPY", _legs_with("premium", v)),
    "rescue adhoc legs: strike":
        lambda v: rescue.adhoc_spec_from_legs("SPY", _legs_with("strike", v)),
    "captured _money": captured._money,
    "captured _count": captured._count,
    "captured priced_count":
        lambda v: captured.priced_count([{"unrealized_pnl": 10.0}, {"unrealized_pnl": v}]),
    "captured _pop_from": lambda v: captured._pop_from({"entry_short_delta": v}),
    "captured _breakeven_from": lambda v: captured._breakeven_from(
        {"strategy": "PCS", "entry_credit": v, "short_strike": 500.0}),
    "paper synth: max_loss_per": lambda v: _synth("max_loss_per", v),
    "paper synth: entry_credit": lambda v: _synth("entry_credit", v),
    "paper synth: short_delta": lambda v: _synth("short_delta", v),
    "paper synth: width": lambda v: _synth("width", v),
    "paper synth: entry_vega": lambda v: _synth("entry_vega", v),
}


@pytest.mark.parametrize("bad", BAD, ids=BAD_IDS)
@pytest.mark.parametrize("case", sorted(CASES))
def test_a_bad_number_renders_as_a_missing_one(case, bad):
    build = CASES[case]
    assert build(bad) == build(None)


def test_the_rescue_reader_never_raises_on_a_bad_quantity():
    for bad in BAD:
        spec = rescue.adhoc_spec_from_legs("SPY", _legs_with("qty", bad))
        assert spec["quantity"] == 1             # the documented default


@pytest.mark.parametrize("value, want", [
    ("2.5", 2.5), (" 495 ", 495.0), (0, 0.0), (None, None), ("x", None),
])
def test_the_page_helpers_still_read_numeric_text(value, want):
    # Ledger and signal rows carry numbers stored as text (a breakeven, a
    # credit); the fix must not stop reading them.
    assert paper._num(value) == want
    assert captured._float(value) == want
    assert rescue._num(value) == want


def test_the_rescue_helper_keeps_its_default():
    assert rescue._num(None, 1) == 1
    assert rescue._num(float("nan"), 0.0) == 0.0
    assert rescue._num("3", 1) == 3.0
