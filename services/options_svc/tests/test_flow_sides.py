"""Bought / sold / unlabelled volume per contract, and the open-interest verdict.

Design: docs/plans/2026-10-04-flow-alert-sides-design.md.
"""
import math

import pytest

from services.options_svc import flow_sides as fs


def _c(osi, vol, last=1.05, bid=1.00, ask=1.10, oi=None):
    c = {"symbol": osi, "totalVolume": vol, "last": last, "bid": bid, "ask": ask}
    if oi is not None:
        c["openInterest"] = oi
    return c


def _chain(calls=(), puts=()):
    def emap(cs):
        return {"2026-10-09:5": {f"{100 + i}.0": [c] for i, c in enumerate(cs)}}
    return {"underlyingPrice": 500.0, "callExpDateMap": emap(calls),
            "putExpDateMap": emap(puts)}


def _total(entry):
    return sum(fs.tally(entry).values())


# --- the poll ---------------------------------------------------------------

def test_first_sight_of_an_unseeded_symbol_is_unlabelled():
    # Volume the service did not watch print: counted, never labelled.
    book = {}
    fs.advance(_chain(calls=[_c("C1", 1000, last=1.10)]), book, seeded=False)
    assert fs.tally(book["C1"]) == {"bought": 0.0, "sold": 0.0, "unlabelled": 1000.0}


def test_new_volume_at_the_ask_is_bought():
    book = {}
    fs.advance(_chain(calls=[_c("C1", 1000, last=1.05)]), book, seeded=False)
    fs.advance(_chain(calls=[_c("C1", 1400, last=1.10)]), book, seeded=True)
    assert fs.tally(book["C1"]) == {"bought": 400.0, "sold": 0.0, "unlabelled": 1000.0}


def test_new_volume_at_the_bid_is_sold():
    book = {}
    fs.advance(_chain(puts=[_c("P1", 1000)]), book, seeded=False)
    fs.advance(_chain(puts=[_c("P1", 1250, last=1.00)]), book, seeded=True)
    assert fs.tally(book["P1"]) == {"bought": 0.0, "sold": 250.0, "unlabelled": 1000.0}


@pytest.mark.parametrize("last,bid,ask", [
    (1.05, 1.00, 1.10),      # exactly the midpoint
    (1.05, 1.20, 1.10),      # crossed quote
    (1.05, None, 1.10),      # no bid
    (math.nan, 1.00, 1.10),  # no usable trade price
])
def test_new_volume_with_no_readable_side_is_unlabelled(last, bid, ask):
    book = {}
    fs.advance(_chain(calls=[_c("C1", 100)]), book, seeded=False)
    fs.advance(_chain(calls=[_c("C1", 160, last=last, bid=bid, ask=ask)]),
               book, seeded=True)
    assert fs.tally(book["C1"]) == {"bought": 0.0, "sold": 0.0, "unlabelled": 160.0}


def test_a_contract_first_seen_in_a_seeded_symbol_is_labelled():
    # The symbol was polled a minute ago and this contract had no volume then,
    # so all of its volume is new and takes this minute's label.
    book = {}
    fs.advance(_chain(calls=[_c("C1", 0)]), book, seeded=False)
    assert "C1" not in book
    fs.advance(_chain(calls=[_c("C1", 300, last=1.10)]), book, seeded=True)
    assert fs.tally(book["C1"]) == {"bought": 300.0, "sold": 0.0, "unlabelled": 0.0}


def test_a_poll_gap_books_the_change_as_unlabelled():
    book = {}
    fs.advance(_chain(calls=[_c("C1", 1000)]), book, seeded=False)
    fs.advance(_chain(calls=[_c("C1", 1900, last=1.10)]), book, seeded=True,
               label=False)
    assert fs.tally(book["C1"]) == {"bought": 0.0, "sold": 0.0, "unlabelled": 1900.0}


def test_a_gap_also_unlabels_a_contract_first_seen_after_it():
    book = {}
    fs.advance(_chain(calls=[_c("C1", 500, last=1.10)]), book, seeded=True,
               label=False)
    assert fs.tally(book["C1"]) == {"bought": 0.0, "sold": 0.0, "unlabelled": 500.0}


def test_a_glitch_read_of_zero_books_nothing_and_is_not_rebooked():
    book = {}
    fs.advance(_chain(calls=[_c("C1", 1000)]), book, seeded=False)
    fs.advance(_chain(calls=[_c("C1", 1400, last=1.10)]), book, seeded=True)
    assert fs.advance(_chain(calls=[_c("C1", 0, last=1.10)]), book, seeded=True) == 0
    fs.advance(_chain(calls=[_c("C1", 1400, last=1.10)]), book, seeded=True)
    assert fs.tally(book["C1"]) == {"bought": 400.0, "sold": 0.0, "unlabelled": 1000.0}


def test_a_contract_at_zero_volume_has_no_entry():
    book = {}
    fs.advance(_chain(calls=[_c("C1", 0)], puts=[_c("P1", 0)]), book, seeded=True)
    assert book == {}


@pytest.mark.parametrize("vol", [None, math.nan, math.inf, -5, "n/a", True])
def test_an_unusable_volume_is_skipped(vol):
    book = {}
    assert fs.advance(_chain(calls=[_c("C1", vol)]), book, seeded=True) == 0
    assert book == {}


def test_a_contract_with_no_symbol_is_skipped():
    book = {}
    c = _c("C1", 500)
    del c["symbol"]
    assert fs.advance(_chain(calls=[c]), book, seeded=True) == 0
    assert book == {}


def test_open_interest_is_kept_and_a_later_bad_read_does_not_erase_it():
    book = {}
    fs.advance(_chain(calls=[_c("C1", 100, oi=4200)]), book, seeded=False)
    assert book["C1"][fs.OI] == 4200.0
    fs.advance(_chain(calls=[_c("C1", 150, oi=math.nan)]), book, seeded=True)
    assert book["C1"][fs.OI] == 4200.0
    fs.advance(_chain(calls=[_c("C1", 150, oi=-999)]), book, seeded=True)
    assert book["C1"][fs.OI] == 4200.0


def test_open_interest_of_zero_is_a_reading():
    book = {}
    fs.advance(_chain(calls=[_c("C1", 100, oi=0)]), book, seeded=False)
    assert book["C1"][fs.OI] == 0.0


def test_a_zero_never_replaces_a_positive_open_interest_from_the_same_session():
    # Open interest does not change within a session, and index open interest
    # is known to read zero around the edges of one: a single zero at the last
    # regular-hours poll would otherwise make tomorrow's verdict "opened" for
    # every such row (code review, 2026-10-04).
    book = {}
    fs.advance(_chain(calls=[_c("C1", 100, oi=9985)]), book, seeded=False)
    fs.advance(_chain(calls=[_c("C1", 150, oi=0)]), book, seeded=True)
    assert book["C1"][fs.OI] == 9985.0


def test_a_positive_open_interest_replaces_an_earlier_zero():
    book = {}
    fs.advance(_chain(calls=[_c("C1", 100, oi=0)]), book, seeded=False)
    fs.advance(_chain(calls=[_c("C1", 150, oi=9985)]), book, seeded=True)
    assert book["C1"][fs.OI] == 9985.0


def test_open_interest_is_not_read_when_the_caller_says_not_to():
    # Index open interest reads zero outside the regular session: the caller
    # passes read_oi=False there, and the last regular-hours figure survives.
    book = {}
    fs.advance(_chain(calls=[_c("C1", 100, oi=4200)]), book, seeded=False)
    fs.advance(_chain(calls=[_c("C1", 150, oi=0)]), book, seeded=True, read_oi=False)
    assert book["C1"][fs.OI] == 4200.0
    assert book["C1"][fs.HW] == 150.0          # the volume is still booked


def test_a_watched_contract_gets_an_entry_at_zero_volume():
    # Yesterday's flagged contract may not trade today, and its open interest
    # is still the reading the follow-up needs.
    book = {}
    chain = _chain(calls=[_c("C1", 0, oi=1700), _c("C2", 0, oi=50)])
    assert fs.advance(chain, book, seeded=True, watch={"C1"}) == 0
    assert list(book) == ["C1"]
    assert book["C1"][fs.OI] == 1700.0
    assert fs.tally(book["C1"]) == {"bought": 0.0, "sold": 0.0, "unlabelled": 0.0}


def test_the_tally_always_sums_to_the_volume():
    book = {}
    reads = [(1000, 1.05, True, True), (1400, 1.10, True, True),
             (1400, 1.10, True, True), (0, 1.10, True, True),
             (1650, 1.00, True, True), (2100, 1.05, True, True),
             (2600, 1.10, True, False)]
    seeded = False
    for vol, last, _s, label in reads:
        fs.advance(_chain(calls=[_c("C1", vol, last=last)]), book,
                   seeded=seeded, label=label)
        seeded = True
    assert _total(book["C1"]) == 2600.0
    assert book["C1"][fs.HW] == 2600.0


def test_advance_returns_how_many_contracts_booked():
    book = {}
    chain = _chain(calls=[_c("C1", 100), _c("C2", 0)], puts=[_c("P1", 50)])
    assert fs.advance(chain, book, seeded=False) == 2
    assert fs.advance(chain, book, seeded=True) == 0


@pytest.mark.parametrize("chain", [None, 5, "x", [], {"callExpDateMap": 7}])
def test_a_malformed_chain_books_nothing(chain):
    book = {"C1": fs.new_entry()}
    assert fs.advance(chain, book, seeded=True) == 0
    assert book == {"C1": fs.new_entry()}


# --- the stream -------------------------------------------------------------

def _tick(osi="C1", **kw):
    return {"symbol": osi, **kw}


def test_the_first_tick_only_seeds():
    # The stream measures what trades AFTER the alert: the volume a contract
    # already had when it was subscribed is the poll's to report.
    book, quotes = {}, {}
    assert fs.advance_tick(book, quotes, _tick(total_volume=5000, last=1.10,
                                               bid=1.00, ask=1.10)) is False
    assert fs.tally(book["C1"]) == {"bought": 0.0, "sold": 0.0, "unlabelled": 0.0}
    assert book["C1"][fs.HW] == 5000.0


def test_an_entry_restored_without_a_volume_mark_seeds_and_keeps_its_tally():
    # After a restart the stored stream tally is put back, but the volume the
    # stream had reached is unknown: the next tick seeds it and books nothing.
    entry = fs.new_entry()
    entry[fs.HW], entry[fs.BOUGHT], entry[fs.SOLD] = None, 300.0, 50.0
    book, quotes = {"C1": entry}, {}
    assert fs.advance_tick(book, quotes, _tick(total_volume=9000, last=1.10,
                                               bid=1.00, ask=1.10)) is False
    assert book["C1"][fs.HW] == 9000.0
    fs.advance_tick(book, quotes, _tick(total_volume=9100))
    assert fs.tally(book["C1"]) == {"bought": 400.0, "sold": 50.0, "unlabelled": 0.0}


def test_ticks_are_deltas_and_the_quote_is_merged():
    book, quotes = {}, {}
    fs.advance_tick(book, quotes, _tick(total_volume=5000, last=1.05,
                                        bid=1.00, ask=1.10))
    # A later tick carries only what changed: the trade price, then the volume.
    fs.advance_tick(book, quotes, _tick(last=1.10))
    assert fs.advance_tick(book, quotes, _tick(total_volume=5300)) is True
    assert fs.tally(book["C1"]) == {"bought": 300.0, "sold": 0.0, "unlabelled": 0.0}
    fs.advance_tick(book, quotes, _tick(last=1.00, total_volume=5350))
    assert fs.tally(book["C1"]) == {"bought": 300.0, "sold": 50.0, "unlabelled": 0.0}


def test_a_tick_field_of_none_does_not_erase_the_merged_quote():
    book, quotes = {}, {}
    fs.advance_tick(book, quotes, _tick(total_volume=10, last=1.10, bid=1.00, ask=1.10))
    fs.advance_tick(book, quotes, _tick(total_volume=40, last=None, bid=None, ask=None))
    assert fs.tally(book["C1"])["bought"] == 30.0


def test_an_unlabelled_stream_tick_still_counts_its_volume():
    book, quotes = {}, {}
    fs.advance_tick(book, quotes, _tick(total_volume=10, last=1.10, bid=1.00, ask=1.10))
    fs.advance_tick(book, quotes, _tick(total_volume=90), label=False)
    assert fs.tally(book["C1"]) == {"bought": 0.0, "sold": 0.0, "unlabelled": 80.0}


def test_stream_volume_is_a_high_water_mark():
    book, quotes = {}, {}
    fs.advance_tick(book, quotes, _tick(total_volume=100, last=1.10, bid=1.00, ask=1.10))
    fs.advance_tick(book, quotes, _tick(total_volume=160))
    assert fs.advance_tick(book, quotes, _tick(total_volume=0)) is False
    fs.advance_tick(book, quotes, _tick(total_volume=160))
    assert fs.tally(book["C1"])["bought"] == 60.0


@pytest.mark.parametrize("tick", [
    None, 5, {}, {"symbol": ""}, {"symbol": "C1"},
    {"symbol": "C1", "total_volume": None},
    {"symbol": "C1", "total_volume": math.nan},
    {"symbol": "C1", "total_volume": -1},
])
def test_a_tick_with_no_symbol_or_no_volume_books_nothing(tick):
    book, quotes = {}, {}
    assert fs.advance_tick(book, quotes, tick) is False
    assert book == {}


# --- the open-interest verdict ----------------------------------------------

_KW = dict(expiry="2026-10-09", session_date="2026-10-02",
           opened_ratio=0.5, closed_ratio=-0.5)


@pytest.mark.parametrize("oi_prev,oi_next,volume,want", [
    (1000, 1600, 1000, ("opened", 0.6)),
    (1000, 400, 1000, ("closed", -0.6)),
    (1000, 1100, 1000, ("mixed", 0.1)),
    (1000, 1500, 1000, ("opened", 0.5)),     # the bound is inclusive
    (1000, 500, 1000, ("closed", -0.5)),     # ...on both sides
    (0, 800, 1000, ("opened", 0.8)),         # a new contract: zero is a reading
])
def test_verdict(oi_prev, oi_next, volume, want):
    code, ratio = fs.verdict(oi_prev, oi_next, volume, **_KW)
    assert code == want[0]
    assert ratio == pytest.approx(want[1])


@pytest.mark.parametrize("expiry", ["2026-10-02", "2026-10-01"])
def test_a_contract_that_expired_on_or_before_its_alert_day_has_no_reading(expiry):
    kw = dict(_KW, expiry=expiry)
    assert fs.verdict(1000, 1600, 1000, **kw) == ("expired", None)


@pytest.mark.parametrize("oi_prev,oi_next,volume", [
    (None, 1600, 1000), (1000, None, 1000), (1000, 1600, None),
    (math.nan, 1600, 1000), (1000, math.inf, 1000),
    (1000, 1600, 0), (1000, 1600, -5),
    (-999, 1600, 1000), (1000, -999, 1000),
])
def test_an_unusable_input_is_no_reading_never_a_guess(oi_prev, oi_next, volume):
    assert fs.verdict(oi_prev, oi_next, volume, **_KW) == ("none", None)


def test_a_missing_expiry_does_not_read_as_expired():
    kw = dict(_KW, expiry=None)
    assert fs.verdict(1000, 1600, 1000, **kw)[0] == "opened"
