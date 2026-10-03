"""The chain store: keep a fetched chain, answer the same request again, and
answer a narrower date window by cutting a stored one."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import market_store as ms  # noqa: E402

EXPS = ("2026-10-05:0", "2026-10-07:2", "2026-10-09:4", "2026-10-12:7")
STRIKES = (95.0, 100.0, 105.0)


def chain(exps=EXPS, strikes=STRIKES, spot=100.0):
    def side(put_call):
        return {e: {str(k): [{"putCall": put_call, "strikePrice": k, "gamma": 0.05,
                              "openInterest": 100, "totalVolume": 10}]
                    for k in strikes} for e in exps}
    return {"symbol": "SPY", "status": "SUCCESS", "underlyingPrice": spot,
            "numberOfContracts": 2 * len(exps) * len(strikes),
            "callExpDateMap": side("CALL"), "putExpDateMap": side("PUT")}


WIDE = ms.ChainKey("SPY", from_date="2026-10-05", to_date="2026-10-12")
NARROW = ms.ChainKey("SPY", from_date="2026-10-05", to_date="2026-10-09")


def _store(**kw):
    s = ms.ChainStore(**kw)
    s.put(WIDE, chain(), now=1000.0, state="REGULAR")
    return s


def test_the_same_request_is_answered_from_the_store():
    got = _store().lookup(WIDE, max_age=45, now=1010.0, state="REGULAR")
    assert got.kind == "hit" and got.age == 10.0
    assert json.loads(got.body) == chain()


def test_an_entry_past_its_age_limit_is_not_served():
    assert _store().lookup(WIDE, max_age=45, now=1046.0, state="REGULAR") is None


def test_an_age_limit_of_zero_never_hits():
    assert _store().lookup(WIDE, max_age=0, now=1000.5, state="REGULAR") is None


def test_an_entry_never_crosses_a_session_change():
    # Stored while closed, asked for after the open: refetch, however young.
    s = ms.ChainStore()
    s.put(WIDE, chain(), now=1000.0, state="CLOSED")
    assert s.lookup(WIDE, max_age=1800, now=1001.0, state="REGULAR") is None


def test_a_narrower_window_is_cut_from_the_wide_chain():
    got = _store().lookup(NARROW, max_age=45, now=1010.0, state="REGULAR")
    assert got.kind == "subset"
    body = json.loads(got.body)
    assert sorted(body["callExpDateMap"]) == ["2026-10-05:0", "2026-10-07:2", "2026-10-09:4"]
    assert sorted(body["putExpDateMap"]) == ["2026-10-05:0", "2026-10-07:2", "2026-10-09:4"]
    assert body["numberOfContracts"] == 2 * 3 * len(STRIKES)
    assert body["underlyingPrice"] == 100.0
    assert body["callExpDateMap"]["2026-10-07:2"] == chain()["callExpDateMap"]["2026-10-07:2"]


def test_a_wider_window_is_never_answered_from_a_narrower_chain():
    s = ms.ChainStore()
    s.put(NARROW, chain(exps=EXPS[:3]), now=1000.0, state="REGULAR")
    assert s.lookup(WIDE, max_age=45, now=1001.0, state="REGULAR") is None


def test_a_strike_filtered_request_is_never_cut_from_an_all_strikes_chain():
    # The sector put/call ratio sums volume over the strikes it receives.
    filtered = ms.ChainKey("SPY", strike_range="NTM", strike_count=50,
                           from_date="2026-10-05", to_date="2026-10-09")
    assert _store().lookup(filtered, max_age=45, now=1001.0, state="REGULAR") is None


def test_a_strike_filtered_chain_never_answers_an_all_strikes_request():
    s = ms.ChainStore()
    filtered = ms.ChainKey("SPY", strike_range="NTM", strike_count=50,
                           from_date="2026-10-05", to_date="2026-10-12")
    s.put(filtered, chain(), now=1000.0, state="REGULAR")
    assert s.lookup(NARROW, max_age=45, now=1001.0, state="REGULAR") is None


def test_another_symbols_chain_is_never_served():
    other = ms.ChainKey("QQQ", from_date="2026-10-05", to_date="2026-10-09")
    assert _store().lookup(other, max_age=45, now=1001.0, state="REGULAR") is None


def test_a_request_without_dates_is_exact_match_only():
    undated = ms.ChainKey("SPY")
    assert _store().lookup(undated, max_age=45, now=1001.0, state="REGULAR") is None


def test_the_newest_covering_chain_wins():
    s = _store()
    wider = ms.ChainKey("SPY", from_date="2026-10-05", to_date="2026-10-20")
    s.put(wider, chain(spot=101.0), now=1020.0, state="REGULAR")
    got = s.lookup(NARROW, max_age=45, now=1030.0, state="REGULAR")
    assert json.loads(got.body)["underlyingPrice"] == 101.0 and got.age == 10.0


def test_a_malformed_payload_is_not_stored():
    s = ms.ChainStore()
    for bad in (None, [], "x", {"callExpDateMap": []}, {"callExpDateMap": {"k": 5},
                                                         "putExpDateMap": {}}):
        s.put(WIDE, bad, now=1000.0, state="REGULAR")
    assert s.lookup(WIDE, max_age=45, now=1001.0, state="REGULAR") is None


def test_the_oldest_entries_are_dropped_past_the_limit():
    s = ms.ChainStore(max_entries=2)
    keys = [ms.ChainKey(sym, from_date="2026-10-05", to_date="2026-10-12")
            for sym in ("A", "B", "C")]
    for i, k in enumerate(keys):
        s.put(k, chain(), now=1000.0 + i, state="REGULAR")
    assert s.lookup(keys[0], max_age=45, now=1003.0, state="REGULAR") is None
    assert s.lookup(keys[2], max_age=45, now=1003.0, state="REGULAR") is not None


def test_the_limit_can_be_changed_on_a_put():
    s = ms.ChainStore(max_entries=10)
    keys = [ms.ChainKey(sym, from_date="2026-10-05", to_date="2026-10-12")
            for sym in ("A", "B", "C")]
    for i, k in enumerate(keys):
        s.put(k, chain(), now=1000.0 + i, state="REGULAR", max_entries=2)
    assert s.lookup(keys[0], max_age=45, now=1003.0, state="REGULAR") is None
    s.put(keys[0], chain(), now=1004.0, state="REGULAR", max_entries="many")
    assert s.lookup(keys[2], max_age=45, now=1004.0, state="REGULAR") is not None


def test_key_from_request_parameters_round_trips():
    params = {"symbol": "SPY", "contractType": "ALL", "range": "ALL",
              "fromDate": "2026-10-05", "toDate": "2026-10-12"}
    key = ms.ChainKey.from_params(params)
    assert key == WIDE and key.params() == params
    filtered = ms.ChainKey.from_params({**params, "range": "NTM", "strikeCount": "50"})
    assert filtered.strike_count == 50 and not filtered.plain
    assert filtered.params()["strikeCount"] == 50


def test_chain_shape_is_the_set_of_contracts_not_their_values():
    assert ms.chain_shape(chain(spot=1.0)) == ms.chain_shape(chain(spot=2.0))
    assert ms.chain_shape(chain()) != ms.chain_shape(chain(exps=EXPS[:3]))
    assert ms.chain_shape(None) == frozenset()


def test_chain_shape_skips_an_expiration_that_is_not_a_strike_map():
    # Shadow mode runs this over whatever Schwab sent; it must not raise.
    good = chain(exps=EXPS[:1])
    odd = chain(exps=EXPS[:1])
    odd["callExpDateMap"]["2026-10-07:2"] = 5
    odd["putExpDateMap"]["2026-10-07:2"] = ["x"]
    assert ms.chain_shape(odd) == ms.chain_shape(good)


import datetime as dt

TODAY = dt.date(2026, 10, 5)


def test_a_near_miss_refetches_the_wider_window_already_held():
    s = _store()                               # holds today -> +7, at any age
    assert s.wide_key(NARROW, today=TODAY) == WIDE


def test_no_wider_refetch_for_a_symbol_nobody_fetched_wide():
    assert ms.ChainStore().wide_key(NARROW, today=TODAY) is None


def test_no_wider_refetch_when_the_request_is_the_held_window_itself():
    assert _store().wide_key(WIDE, today=TODAY) is None


def test_no_wider_refetch_for_a_window_the_held_one_does_not_cover():
    far = ms.ChainKey("SPY", from_date="2026-10-10", to_date="2026-10-20")
    assert _store().wide_key(far, today=TODAY) is None


def test_no_wider_refetch_for_a_strike_filtered_request():
    filtered = ms.ChainKey("SPY", strike_range="NTM", strike_count=50,
                           from_date="2026-10-05", to_date="2026-10-09")
    assert _store().wide_key(filtered, today=TODAY) is None


def test_the_narrowest_covering_window_is_the_one_refetched():
    s = _store()
    wider = ms.ChainKey("SPY", from_date="2026-10-05", to_date="2026-11-19")
    s.put(wider, chain(), now=1500.0, state="REGULAR")
    assert s.wide_key(NARROW, today=TODAY) == WIDE


def test_a_window_that_started_on_an_earlier_day_is_not_refetched():
    s = ms.ChainStore()
    yesterday = ms.ChainKey("SPY", from_date="2026-10-04", to_date="2026-10-11")
    s.put(yesterday, chain(), now=1000.0, state="REGULAR")
    assert s.wide_key(NARROW, today=TODAY) is None


def test_cut_answers_from_the_wide_entry_whatever_its_age():
    body = json.loads(_store().cut(WIDE, NARROW))
    assert sorted(body["callExpDateMap"]) == ["2026-10-05:0", "2026-10-07:2", "2026-10-09:4"]
    assert ms.ChainStore().cut(WIDE, NARROW) is None
