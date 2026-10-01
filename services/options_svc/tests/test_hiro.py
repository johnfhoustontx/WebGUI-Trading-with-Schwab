import math

import pytest

from services.options_svc import hiro


@pytest.mark.parametrize("last,bid,ask,want", [
    (1.10, 1.00, 1.10, 1),     # at the ask -> customer bought
    (1.20, 1.00, 1.10, 1),     # through the ask
    (1.00, 1.00, 1.10, -1),    # at the bid -> customer sold
    (0.95, 1.00, 1.10, -1),
    (1.08, 1.00, 1.10, 1),     # above mid
    (1.02, 1.00, 1.10, -1),    # below mid
    (1.05, 1.00, 1.10, 0),     # exactly mid -> unclassified, never guessed
    (0.15, 0.10, 0.20, 0),     # mid that binary floats miss by one ulp
    (2.35, 2.30, 2.40, 0),     # ...and another
    (1.02, 1.00, 1.05, -1),    # control: a real gap below mid still labels
])
def test_classify_side_quote_rule(last, bid, ask, want):
    assert hiro.classify_side(last, bid, ask) == want


@pytest.mark.parametrize("last,bid,ask", [
    (None, 1.0, 1.1), (1.0, None, 1.1), (1.0, 1.0, None),
    (math.nan, 1.0, 1.1), (1.0, math.inf, 1.1), (True, 1.0, 1.1),
    (0.0, 1.0, 1.1),           # no print
    (1.0, 1.2, 1.1),           # crossed quote
    (1.0, -999.0, 1.1),        # sentinel
    (1.00, 1.00, 1.00),        # locked quote -> no side to read
])
def test_classify_side_unusable_quote_is_unclassified(last, bid, ask):
    assert hiro.classify_side(last, bid, ask) == 0


def _c(osi, vol, delta, last, bid=1.00, ask=1.10):
    return {"symbol": osi, "totalVolume": vol, "delta": delta,
            "last": last, "bid": bid, "ask": ask}


def _chain(calls=(), puts=(), spot=500.0):
    def emap(cs, put_call):
        return {"2026-10-02:1": {f"{100 + i}.0": [dict(c, putCall=put_call)]
                                 for i, c in enumerate(cs)}}
    return {"underlyingPrice": spot, "callExpDateMap": emap(calls, "CALL"),
            "putExpDateMap": emap(puts, "PUT")}


def test_measure_first_sight_seeds_baseline_and_books_nothing():
    chain = _chain(calls=[_c("C1", 1000, 0.5, 1.10)])
    row, prev = hiro.measure_chain(chain, {})
    assert prev == {"C1": 1000.0}
    assert row["impact"] == 0.0 and row["classified_vol"] == 0.0
    assert row["unclassified_vol"] == 0.0 and row["spot"] == 500.0


def test_measure_signs_call_and_put_buys_correctly():
    prev = {"C1": 1000.0, "P1": 50.0}
    chain = _chain(calls=[_c("C1", 1010, 0.5, 1.10)],      # 10 bought at ask
                   puts=[_c("P1", 54, -0.4, 1.10)])        # 4 bought at ask
    row, new_prev = hiro.measure_chain(chain, prev)
    # call: +1 * 0.5 * 10 * 100 * 500 = +250,000 ; put: +1 * -0.4 * 4 * 100 * 500 = -80,000
    assert row["impact"] == pytest.approx(170_000.0)
    assert row["classified_vol"] == 14.0
    assert new_prev == {"C1": 1010.0, "P1": 54.0}


def test_measure_customer_sell_of_call_is_dealer_selling():
    row, _ = hiro.measure_chain(_chain(calls=[_c("C1", 1010, 0.5, 1.00)]), {"C1": 1000.0})
    assert row["impact"] == pytest.approx(-250_000.0)


def test_measure_mid_print_is_unclassified_and_contributes_nothing():
    row, _ = hiro.measure_chain(_chain(calls=[_c("C1", 1010, 0.5, 1.05)]), {"C1": 1000.0})
    assert row["impact"] == 0.0
    assert row["unclassified_vol"] == 10.0 and row["classified_vol"] == 0.0


@pytest.mark.parametrize("delta", [math.nan, -999.0, 1.5, None, True])
def test_measure_drops_bad_delta(delta):
    row, prev = hiro.measure_chain(_chain(calls=[_c("C1", 1010, delta, 1.10)]), {"C1": 1000.0})
    assert row["impact"] == 0.0
    assert row["classified_vol"] == 0.0 and row["unclassified_vol"] == 10.0
    assert prev["C1"] == 1010.0           # baseline still advances


def test_measure_wrong_sign_call_delta_is_unlabelled():
    row, prev = hiro.measure_chain(_chain(calls=[_c("C1", 1010, -0.5, 1.10)]), {"C1": 1000.0})
    assert row["impact"] == 0.0
    assert row["classified_vol"] == 0.0 and row["unclassified_vol"] == 10.0
    assert prev["C1"] == 1010.0


def test_measure_wrong_sign_put_delta_is_unlabelled():
    row, _ = hiro.measure_chain(_chain(puts=[_c("P1", 1010, 0.4, 1.10)]), {"P1": 1000.0})
    assert row["impact"] == 0.0
    assert row["classified_vol"] == 0.0 and row["unclassified_vol"] == 10.0


@pytest.mark.parametrize("delta", [0.0, -0.0])
def test_measure_zero_delta_is_labelled_with_no_impact(delta):
    row, _ = hiro.measure_chain(_chain(calls=[_c("C1", 1010, delta, 1.10)],
                                       puts=[_c("P1", 1010, delta, 1.10)]),
                                {"C1": 1000.0, "P1": 1000.0})
    assert row["impact"] == 0.0
    assert row["classified_vol"] == 20.0 and row["unclassified_vol"] == 0.0


def test_measure_volume_reset_books_nothing():
    row, prev = hiro.measure_chain(_chain(calls=[_c("C1", 5, 0.5, 1.10)]), {"C1": 1000.0})
    assert row["impact"] == 0.0 and prev["C1"] == 5.0


@pytest.mark.parametrize("spot", [None, 0, -1, math.nan])
def test_measure_unusable_spot_returns_no_row(spot):
    chain = _chain(calls=[_c("C1", 1010, 0.5, 1.10)], spot=spot)
    row, prev = hiro.measure_chain(chain, {"C1": 1000.0})
    assert row is None
    assert prev == {"C1": 1000.0}          # nothing consumed: the next good minute books it


@pytest.mark.parametrize("bad", [None, {}, "x", {"callExpDateMap": "x", "underlyingPrice": 500}])
def test_measure_malformed_chain_is_total(bad):
    row, prev = hiro.measure_chain(bad, {})
    assert prev == {}
    if isinstance(bad, dict) and bad.get("underlyingPrice") == 500:
        # a usable spot with no readable contracts is a MEASURED zero, not "no data"
        assert row == {"spot": 500.0, "impact": 0.0, "classified_vol": 0.0,
                       "unclassified_vol": 0.0}


def test_measure_does_not_mutate_callers_prev():
    prev = {"C1": 1000.0}
    hiro.measure_chain(_chain(calls=[_c("C1", 1010, 0.5, 1.10),
                                     _c("C2", 50, 0.3, 1.10)]), prev)
    assert prev == {"C1": 1000.0}


def test_measure_carries_forward_a_contract_absent_this_minute():
    row, new_prev = hiro.measure_chain(_chain(calls=[_c("C1", 1010, 0.5, 1.10)]),
                                       {"C1": 1000.0, "C9": 77.0})
    assert new_prev == {"C1": 1010.0, "C9": 77.0}


def test_measure_mixed_labelled_and_mid_prints_fill_both_counters():
    chain = _chain(calls=[_c("C1", 1010, 0.5, 1.10),      # 10 bought at ask
                          _c("C2", 506, 0.5, 1.05)])      # 6 at the midpoint
    row, _ = hiro.measure_chain(chain, {"C1": 1000.0, "C2": 500.0})
    assert row["classified_vol"] == 10.0 and row["unclassified_vol"] == 6.0
    assert row["impact"] == pytest.approx(250_000.0)
