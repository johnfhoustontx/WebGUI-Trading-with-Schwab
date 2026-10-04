"""The front-month futures contract, computed from the date.

``services/market_svc/symbols.py`` named ``/ESU26`` and ``/NQU26`` as literals.
The September 2026 contract expired on 2026-09-18 and the Macro Board's two
Equity Index Futures tiles went blank, because a quote for a dead contract
returns nothing - no error, no log line, an em-dash on the public page.

Every date below is worked by hand from a calendar, never from the code under
test: September 2026 opens on a Tuesday, so its Fridays are the 4th, 11th and
18th; December 2026 likewise (4, 11, 18); March 2027 opens on a Monday (5, 12,
19).
"""
from datetime import date

import pytest

from shared import futures, symbols


@pytest.fixture(autouse=True)
def _fresh():
    symbols.reset_cache()
    yield
    symbols.reset_cache()


# ── expiry ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("year, month, expected", [
    (2026, 9, date(2026, 9, 18)),
    (2026, 12, date(2026, 12, 18)),
    (2027, 3, date(2027, 3, 19)),
    (2027, 9, date(2027, 9, 17)),
])
def test_expiry_is_the_third_friday_of_the_contract_month(year, month, expected):
    assert futures.expiry_date(year, month) == expected
    assert expected.weekday() == 4


def test_expiry_moves_to_the_prior_session_when_the_third_friday_is_a_holiday():
    """June 2026's third Friday is the 19th - Juneteenth, a full closure. The
    final settlement needs an opening print, so it is the Thursday before. The
    closure comes from shared.market_calendar; there is no date list here.

    June 2027 does it again by a different route: the 19th is a Saturday, so
    Juneteenth is OBSERVED on Friday the 18th, which is that month's third
    Friday. (The first draft of this file listed 2027-06-18 as an ordinary
    expiry; the calendar knew better.)"""
    assert futures.expiry_date(2026, 6) == date(2026, 6, 18)
    assert futures.expiry_date(2027, 6) == date(2027, 6, 17)
    for d in (date(2026, 6, 18), date(2027, 6, 17)):
        assert d.weekday() == 3


def test_only_the_quarterly_months_are_contracts():
    with pytest.raises(ValueError):
        futures.expiry_date(2026, 10)


# ── the roll: the four dates the fix is about ───────────────────────────────

def test_the_day_before_the_roll_is_still_the_expiring_contract():
    c = futures.front_month("/ES", date(2026, 9, 9), roll_days=8)
    assert (c.code, c.quote_symbol, c.display) == ("U26", "/ESU26", "/ES[U26]")


def test_the_roll_day_itself_is_the_next_contract():
    """Eight days before Friday the 18th is Thursday the 10th - the Thursday of
    the week before expiry, the conventional roll."""
    assert date(2026, 9, 10).weekday() == 3
    c = futures.front_month("/ES", date(2026, 9, 10), roll_days=8)
    assert (c.code, c.quote_symbol, c.display) == ("Z26", "/ESZ26", "/ES[Z26]")


@pytest.mark.parametrize("today", [date(2026, 9, 19), date(2026, 10, 4)])
def test_after_expiry_the_front_month_is_december(today):
    """2026-10-04 is the Sunday evening the blank tiles were seen on."""
    c = futures.front_month("/NQ", today, roll_days=8)
    assert c.quote_symbol == "/NQZ26"
    assert c.expiry == date(2026, 12, 18)
    assert c.label == "Dec 2026"


def test_december_rolls_into_march_of_the_next_year():
    """The year boundary: Z of one year hands to H of the next, and the year
    digits must move with the month - before 1 January, not on it."""
    assert futures.front_month("/ES", date(2026, 12, 9), roll_days=8).code == "Z26"
    after = futures.front_month("/ES", date(2026, 12, 10), roll_days=8)
    assert (after.code, after.quote_symbol, after.label) == ("H27", "/ESH27", "Mar 2027")
    assert futures.front_month("/ES", date(2026, 12, 31), roll_days=8).code == "H27"
    assert futures.front_month("/ES", date(2027, 1, 4), roll_days=8).code == "H27"


def test_the_cycle_is_h_m_u_z_in_order_across_a_year():
    seen = []
    for month in range(1, 13):
        code = futures.front_month("/ES", date(2027, month, 1), roll_days=8).code
        if code not in seen:
            seen.append(code)
    assert seen == ["H27", "M27", "U27", "Z27"]


def test_a_zero_offset_rolls_on_expiry_day():
    """The contract stops trading at the open on expiry day, so even with no
    lead time that day already belongs to the next one."""
    assert futures.front_month("/ES", date(2026, 9, 17), roll_days=0).code == "U26"
    assert futures.front_month("/ES", date(2026, 9, 18), roll_days=0).code == "Z26"


def test_the_roll_is_measured_from_the_real_expiry_not_the_third_friday():
    """June 2026 settles Thursday the 18th (Juneteenth), so eight days before
    is Wednesday the 10th, a day earlier than the third Friday alone implies."""
    assert futures.front_month("/ES", date(2026, 6, 9), roll_days=8).code == "M26"
    assert futures.front_month("/ES", date(2026, 6, 10), roll_days=8).code == "U26"


# ── the offset is config ────────────────────────────────────────────────────

def test_the_shipped_offset_is_eight_days():
    assert symbols.futures_roll_days() == 8
    assert symbols.DEFAULTS["futures"]["roll_days_before_expiry"] == 8


def test_front_month_reads_the_offset_from_config_when_none_is_passed(monkeypatch):
    """The discriminating test: move the config and the answer must follow. A
    test that only compared the default to the default would pass unwired."""
    monkeypatch.setattr(symbols, "futures_roll_days", lambda: 0)
    assert futures.front_month("/ES", date(2026, 9, 12)).code == "U26"
    monkeypatch.setattr(symbols, "futures_roll_days", lambda: 8)
    assert futures.front_month("/ES", date(2026, 9, 12)).code == "Z26"


@pytest.mark.parametrize("bad", ["8", 8.5, -1, True, None, 400, [8]])
def test_an_unusable_offset_degrades_to_the_default(monkeypatch, bad):
    """A typo in a config file must not blank the tiles or skip a contract."""
    monkeypatch.setattr(symbols, "load",
                        lambda: {"futures": {"roll_days_before_expiry": bad}})
    assert symbols.futures_roll_days() == 8


def test_a_missing_section_degrades_to_the_default(monkeypatch):
    monkeypatch.setattr(symbols, "load", lambda: {})
    assert symbols.futures_roll_days() == 8


# ── today ───────────────────────────────────────────────────────────────────

def test_front_month_defaults_to_todays_central_date(monkeypatch):
    monkeypatch.setattr(futures, "today_ct", lambda: date(2026, 10, 4))
    assert futures.front_month("/ES", roll_days=8).quote_symbol == "/ESZ26"
    monkeypatch.setattr(futures, "today_ct", lambda: date(2026, 9, 1))
    assert futures.front_month("/ES", roll_days=8).quote_symbol == "/ESU26"


def test_today_is_a_central_date():
    from datetime import datetime

    from shared import market_calendar as mc
    assert futures.today_ct() == datetime.now(mc.CT).date()


# ── vocabulary ──────────────────────────────────────────────────────────────

def test_the_bracket_form_and_the_bare_form_describe_one_contract():
    """Display is ``/ES[Z26]``; Schwab's /quotes wants ``/ESZ26``. Both come off
    one object so they cannot name different contracts."""
    c = futures.front_month("/NQ", date(2026, 10, 4), roll_days=8)
    assert c.display.replace("[", "").replace("]", "") == c.quote_symbol
    assert c.root == "/NQ"
