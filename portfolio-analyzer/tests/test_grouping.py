"""Tests for ``src.grouping`` - one account's positions into Shares / Options.

The rule under test is the one the Holdings tab shows: a long and a short leg
of the same underlying, expiration and right pair into ONE vertical spread,
credit or debit by the strikes, never by today's prices.
"""
import pytest

from src.grouping import group_positions, group_value, pair_options, parse_option_symbol


def _opt(symbol, qty, avg, mv, day=0.0, total=0.0, underlying="SPY"):
    return {"symbol": symbol, "asset_type": "OPTION", "underlying": underlying,
            "quantity": qty, "avg_price": avg, "market_value": mv,
            "day_pl": day, "total_pl": total}


def _eq(symbol, qty=10, asset_type="EQUITY"):
    return {"symbol": symbol, "asset_type": asset_type, "underlying": symbol,
            "quantity": qty, "avg_price": 100.0, "market_value": qty * 110.0,
            "day_pl": 5.0, "total_pl": qty * 10.0}


# --- parse_option_symbol ----------------------------------------------------

def test_parse_occ_symbol():
    assert parse_option_symbol("SPY   261218P00600000") == {
        "root": "SPY", "expiry": "2026-12-18", "right": "P", "strike": 600.0}
    assert parse_option_symbol("AAPL  270115C00187500")["strike"] == 187.5


@pytest.mark.parametrize("bad", ["AAPL", "", None, 42, "SPY 2612P600"])
def test_parse_refuses_what_is_not_occ(bad):
    assert parse_option_symbol(bad) is None


# --- shares / options / other -------------------------------------------------

def test_group_positions_splits_by_asset_type():
    got = group_positions([
        _eq("MSFT"), _eq("SPY", asset_type="COLLECTIVE_INVESTMENT"), _eq("AAPL"),
        _opt("SPY   261218C00700000", 1, 2.0, 150.0),
        {"symbol": "/ES", "asset_type": "FUTURE", "quantity": 1, "market_value": 0.0},
    ])
    assert [p["symbol"] for p in got["shares"]] == ["AAPL", "MSFT", "SPY"]
    assert len(got["options"]) == 1
    assert [p["symbol"] for p in got["other"]] == ["/ES"]


def test_group_positions_tolerates_none():
    assert group_positions(None) == {"shares": [], "options": [], "other": []}


# --- the four verticals ---------------------------------------------------------

@pytest.mark.parametrize("short_sym,long_sym,label,credit", [
    ("SPY   261218P00600000", "SPY   261218P00595000", "Put Credit Spread", True),
    ("SPY   261218C00700000", "SPY   261218C00705000", "Call Credit Spread", True),
    ("SPY   261218C00705000", "SPY   261218C00700000", "Bull Call Spread", False),
    ("SPY   261218P00595000", "SPY   261218P00600000", "Bear Put Spread", False),
])
def test_a_long_and_a_short_leg_pair_into_one_spread(short_sym, long_sym, label, credit):
    groups = pair_options([_opt(short_sym, -2, 3.0, -500.0),
                           _opt(long_sym, 2, 1.0, 150.0)])
    assert len(groups) == 1
    g = groups[0]
    assert g["kind"] == "spread"
    assert g["label"] == label
    assert g["credit"] is credit
    assert g["contracts"] == 2
    assert g["expiry"] == "2026-12-18"


def test_credit_spread_value_is_net_of_both_legs():
    g = pair_options([
        _opt("SPY   261218P00600000", -2, 3.00, -500.0, day=-20.0, total=100.0),
        _opt("SPY   261218P00595000", 2, 1.00, 150.0, day=8.0, total=-50.0),
    ])[0]
    v = group_value(g)
    assert v["market_value"] == pytest.approx(-350.0)
    assert v["day_pl"] == pytest.approx(-12.0)
    assert v["total_pl"] == pytest.approx(50.0)
    assert v["entry"] == pytest.approx(2.00)      # 3.00 taken - 1.00 paid
    assert v["mark"] == pytest.approx(1.75)       # 350 / (2 contracts x 100)


def test_debit_spread_entry_reads_as_the_debit_paid():
    g = pair_options([
        _opt("SPY   261218C00700000", 1, 4.00, 450.0),
        _opt("SPY   261218C00705000", -1, 1.50, -200.0),
    ])[0]
    assert g["credit"] is False
    v = group_value(g)
    assert v["entry"] == pytest.approx(2.50)
    assert v["mark"] == pytest.approx(2.50)


# --- what does NOT pair --------------------------------------------------------

def test_different_expirations_never_pair():
    groups = pair_options([_opt("SPY   261218P00600000", -1, 3.0, -300.0),
                           _opt("SPY   270115P00595000", 1, 2.0, 200.0)])
    assert [g["kind"] for g in groups] == ["single", "single"]


def test_a_call_and_a_put_never_pair():
    groups = pair_options([_opt("SPY   261218P00600000", -1, 3.0, -300.0),
                           _opt("SPY   261218C00700000", 1, 2.0, 200.0)])
    assert [g["kind"] for g in groups] == ["single", "single"]


def test_two_longs_stay_two_options():
    groups = pair_options([_opt("SPY   261218P00600000", 1, 3.0, 300.0),
                           _opt("SPY   261218P00595000", 1, 2.0, 200.0)])
    assert [g["label"] for g in groups] == ["Long Put", "Long Put"]


def test_different_underlyings_never_pair():
    groups = pair_options([
        _opt("SPY   261218P00600000", -1, 3.0, -300.0),
        _opt("QQQ   261218P00500000", 1, 2.0, 200.0, underlying="QQQ"),
    ])
    assert [g["kind"] for g in groups] == ["single", "single"]


# --- uneven and many legs ---------------------------------------------------------

def test_uneven_legs_pair_the_smaller_count_and_leave_the_rest_single():
    groups = pair_options([
        _opt("SPY   261218P00600000", -3, 3.0, -900.0, day=-30.0, total=60.0),
        _opt("SPY   261218P00595000", 1, 1.0, 100.0),
    ])
    spread = [g for g in groups if g["kind"] == "spread"][0]
    single = [g for g in groups if g["kind"] == "single"][0]
    assert spread["contracts"] == 1
    assert single["contracts"] == -2 and single["label"] == "Short Put"
    # the short leg's money splits by contract count: 1/3 to the spread, 2/3 left
    assert spread["legs"][0]["market_value"] == pytest.approx(-300.0)
    assert single["legs"][0]["market_value"] == pytest.approx(-600.0)
    assert single["legs"][0]["total_pl"] == pytest.approx(40.0)
    # nothing is created or lost by the split
    total_mv = sum(group_value(g)["market_value"] for g in groups)
    assert total_mv == pytest.approx(-800.0)


def test_iron_condor_shows_as_its_two_credit_spreads():
    groups = pair_options([
        _opt("SPY   261218P00590000", -1, 2.0, -150.0),
        _opt("SPY   261218P00585000", 1, 1.0, 80.0),
        _opt("SPY   261218C00620000", -1, 2.0, -150.0),
        _opt("SPY   261218C00625000", 1, 1.0, 80.0),
    ])
    assert sorted(g["label"] for g in groups) == ["Call Credit Spread", "Put Credit Spread"]
    put = [g for g in groups if g["right"] == "P"][0]
    assert put["short_strike"] == 590.0 and put["long_strike"] == 585.0


def test_closest_strikes_pair_first():
    # Short 600; longs at 595 and 580. The 595 is the closer wing.
    groups = pair_options([
        _opt("SPY   261218P00600000", -1, 3.0, -300.0),
        _opt("SPY   261218P00580000", 1, 0.5, 50.0),
        _opt("SPY   261218P00595000", 1, 1.0, 100.0),
    ])
    spread = [g for g in groups if g["kind"] == "spread"][0]
    assert spread["strikes"] == (595.0, 600.0)
    single = [g for g in groups if g["kind"] == "single"][0]
    assert single["strikes"] == (580.0,) and single["label"] == "Long Put"


def test_an_unparseable_option_is_still_shown():
    groups = pair_options([_opt("WEIRD", 1, 1.0, 100.0)])
    assert len(groups) == 1 and groups[0]["legs"][0]["symbol"] == "WEIRD"


def test_inputs_are_not_mutated():
    short = _opt("SPY   261218P00600000", -3, 3.0, -900.0)
    long_ = _opt("SPY   261218P00595000", 1, 1.0, 100.0)
    before = (dict(short), dict(long_))
    pair_options([short, long_])
    assert (short, long_) == before
