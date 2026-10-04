"""The chain store: keep a fetched chain, answer the same request again, and
answer a narrower date window by cutting a stored one."""
import json
import pathlib
import sys

import pytest

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


# ---- gaps found by mutation testing ----------------------------------------

def _three_keys():
    return [ms.ChainKey(sym, from_date="2026-10-05", to_date="2026-10-12")
            for sym in ("A", "B", "C")]


def _held(s, key, now=1010.0):
    return s.lookup(key, max_age=45, now=now, state="REGULAR") is not None


def test_an_age_limit_of_zero_never_hits_even_at_age_zero():
    s = _store()
    assert s.lookup(WIDE, max_age=0, now=1000.0, state="REGULAR") is None
    assert s.lookup(NARROW, max_age=0, now=1000.0, state="REGULAR") is None


def test_an_age_limit_that_is_not_a_real_number_never_hits():
    s = _store()
    for bad in (float("inf"), float("nan"), True, None, "45"):
        assert s.lookup(WIDE, max_age=bad, now=1000.5, state="REGULAR") is None
        assert s.lookup(NARROW, max_age=bad, now=1000.5, state="REGULAR") is None


def test_a_window_that_starts_after_the_request_does_not_cover_it():
    s = ms.ChainStore()
    late = ms.ChainKey("SPY", from_date="2026-10-06", to_date="2026-10-12")
    s.put(late, chain(exps=EXPS[1:]), now=1000.0, state="REGULAR")
    assert s.lookup(NARROW, max_age=45, now=1001.0, state="REGULAR") is None


def test_an_unusable_limit_keeps_the_one_in_force():
    s = ms.ChainStore(max_entries=10)
    a, b, c = _three_keys()
    for i, k in enumerate((a, b, c)):
        s.put(k, chain(), now=1000.0 + i, state="REGULAR", max_entries=2)
    # Held: B, C. Neither of these changes the bound of 2.
    s.put(a, chain(), now=1004.0, state="REGULAR", max_entries="many")
    assert not _held(s, b) and _held(s, c) and _held(s, a)
    s.put(b, chain(), now=1005.0, state="REGULAR", max_entries=float("inf"))
    assert not _held(s, c) and _held(s, a) and _held(s, b)


def test_putting_a_held_chain_again_evicts_nothing_and_makes_it_the_newest():
    s = ms.ChainStore(max_entries=2)
    a, b, c = _three_keys()
    s.put(a, chain(), now=1000.0, state="REGULAR")
    s.put(b, chain(), now=1001.0, state="REGULAR")
    s.put(a, chain(), now=1002.0, state="REGULAR")
    assert _held(s, a) and _held(s, b)
    s.put(c, chain(), now=1003.0, state="REGULAR")      # B is now the oldest
    assert not _held(s, b) and _held(s, a) and _held(s, c)


def test_an_empty_chain_is_never_stored():
    # A transient empty reply must not be repeated for the whole age limit.
    s = ms.ChainStore()
    empty = {**chain(), "callExpDateMap": {}, "putExpDateMap": {}}
    assert s.put(WIDE, empty, now=1000.0, state="REGULAR") is False
    assert s.lookup(WIDE, max_age=45, now=1001.0, state="REGULAR") is None
    assert s.lookup(NARROW, max_age=45, now=1001.0, state="REGULAR") is None


def test_a_chain_with_one_empty_side_is_stored():
    s = ms.ChainStore()
    calls_only = {**chain(), "putExpDateMap": {}}
    assert s.put(WIDE, calls_only, now=1000.0, state="REGULAR") is True
    body = json.loads(s.lookup(WIDE, max_age=45, now=1001.0, state="REGULAR").body)
    assert sorted(body["callExpDateMap"]) == sorted(EXPS) and body["putExpDateMap"] == {}
    puts_only = {**chain(), "callExpDateMap": {}}
    assert s.put(WIDE, puts_only, now=1002.0, state="REGULAR") is True
    body = json.loads(s.lookup(WIDE, max_age=45, now=1003.0, state="REGULAR").body)
    assert sorted(body["putExpDateMap"]) == sorted(EXPS) and body["callExpDateMap"] == {}


# ---- review of Tasks 4-7 ---------------------------------------------------

def _only(exps_calls=EXPS, exps_puts=EXPS):
    """A chain whose two sides hold different expirations."""
    return {**chain(), "callExpDateMap": chain(exps=exps_calls)["callExpDateMap"],
            "putExpDateMap": chain(exps=exps_puts)["putExpDateMap"]}


def test_a_cut_that_keeps_no_expiration_is_never_served():
    # Schwab answers an empty window with underlyingPrice 0.0, not the real
    # price, so an empty cut is not what Schwab would have sent: refetch.
    s = ms.ChainStore()
    s.put(WIDE, chain(exps=EXPS[3:]), now=1000.0, state="REGULAR")
    assert s.lookup(NARROW, max_age=45, now=1001.0, state="REGULAR") is None
    assert s.cut(WIDE, NARROW) is None
    assert s.wide_key(NARROW, today=TODAY) is None      # one upstream call, not two


def test_an_older_covering_entry_with_an_expiration_is_served_over_an_empty_newer_one():
    s = _store()
    wider = ms.ChainKey("SPY", from_date="2026-10-05", to_date="2026-10-20")
    s.put(wider, chain(exps=EXPS[3:], spot=101.0), now=1020.0, state="REGULAR")
    got = s.lookup(NARROW, max_age=45, now=1030.0, state="REGULAR")
    assert got.kind == "subset" and got.age == 30.0
    assert json.loads(got.body)["underlyingPrice"] == 100.0


def test_a_reversed_window_is_never_served_from_a_cut():
    backwards = ms.ChainKey("SPY", from_date="2026-10-09", to_date="2026-10-05")
    s = _store()
    assert not WIDE.covers(backwards)
    assert s.lookup(backwards, max_age=45, now=1001.0, state="REGULAR") is None
    assert s.cut(WIDE, backwards) is None
    assert s.wide_key(backwards, today=TODAY) is None


@pytest.mark.parametrize("full", ["callExpDateMap", "putExpDateMap"])
def test_a_window_where_only_one_side_has_an_expiration_is_still_served(full):
    sides = {"exps_calls": EXPS[3:], "exps_puts": EXPS[3:]}
    sides["exps_calls" if full == "callExpDateMap" else "exps_puts"] = EXPS
    empty = "putExpDateMap" if full == "callExpDateMap" else "callExpDateMap"
    s = ms.ChainStore()
    s.put(WIDE, _only(**sides), now=1000.0, state="REGULAR")
    got = s.lookup(NARROW, max_age=45, now=1001.0, state="REGULAR")
    assert got.kind == "subset"
    body = json.loads(got.body)
    assert sorted(body[full]) == list(EXPS[:3]) and body[empty] == {}
    assert body["numberOfContracts"] == 3 * len(STRIKES)
    assert json.loads(s.cut(WIDE, NARROW)) == body
    assert s.wide_key(NARROW, today=TODAY) == WIDE


def test_cut_never_answers_a_window_the_entry_does_not_cover():
    s = _store()
    far = ms.ChainKey("SPY", from_date="2026-10-10", to_date="2026-10-20")
    filtered = ms.ChainKey("SPY", strike_range="NTM", strike_count=50,
                           from_date="2026-10-05", to_date="2026-10-09")
    assert s.cut(WIDE, far) is None
    assert s.cut(WIDE, filtered) is None
    assert s.cut(WIDE, ms.ChainKey("SPY")) is None


@pytest.mark.parametrize("only", [{"contract_type": "CALL"},
                                  {"strike_range": "NTM"},
                                  {"strike_count": 50}])
def test_any_one_filter_alone_keeps_a_chain_out_of_the_cutting_path(only):
    # A filtered consumer sums over exactly the contracts it asked for.
    asked = ms.ChainKey("SPY", from_date="2026-10-05", to_date="2026-10-09", **only)
    held = ms.ChainKey("SPY", from_date="2026-10-05", to_date="2026-10-12", **only)
    assert not asked.plain and not held.plain

    plain_store = _store()                    # holds the plain wide chain
    assert plain_store.lookup(asked, max_age=45, now=1001.0, state="REGULAR") is None
    assert plain_store.wide_key(asked, today=TODAY) is None
    assert plain_store.cut(WIDE, asked) is None

    filtered_store = ms.ChainStore()          # holds only the filtered wide chain
    assert filtered_store.put(held, chain(), now=1000.0, state="REGULAR") is True
    assert filtered_store.lookup(NARROW, max_age=45, now=1001.0, state="REGULAR") is None
    assert filtered_store.wide_key(NARROW, today=TODAY) is None
    assert filtered_store.cut(held, NARROW) is None
    # ... while the identical filtered request is still an exact hit.
    assert filtered_store.lookup(held, max_age=45, now=1001.0, state="REGULAR").kind == "hit"


def test_a_cut_never_crosses_a_session_change():
    s = ms.ChainStore()
    s.put(WIDE, chain(), now=1000.0, state="CLOSED")
    assert s.lookup(NARROW, max_age=1800, now=1001.0, state="REGULAR") is None
    assert s.lookup(NARROW, max_age=1800, now=1001.0, state="CLOSED").kind == "subset"


def test_a_cut_drops_expirations_before_its_window_too():
    later = ms.ChainKey("SPY", from_date="2026-10-07", to_date="2026-10-12")
    body = json.loads(_store().lookup(later, max_age=45, now=1001.0,
                                      state="REGULAR").body)
    assert sorted(body["callExpDateMap"]) == list(EXPS[1:])
    assert sorted(body["putExpDateMap"]) == list(EXPS[1:])


def _plus(days):
    return ms.ChainKey("SPY", from_date=TODAY.isoformat(),
                       to_date=(TODAY + dt.timedelta(days=days)).isoformat())


def test_a_near_miss_never_widens_to_a_long_window():
    s = ms.ChainStore()
    s.put(_plus(45), chain(), now=1000.0, state="REGULAR")
    assert s.wide_key(_plus(4), today=TODAY) is None
    s.put(_plus(7), chain(), now=1001.0, state="REGULAR")
    assert s.wide_key(_plus(4), today=TODAY) == _plus(7)


def test_the_widest_window_refetched_is_the_cap():
    # The collector's own window: a 7-day request is never turned into the
    # 10-day term-structure window or a ~9-day Strategy Finder one.
    assert ms.WIDE_REFETCH_MAX_DAYS == 7
    over = ms.ChainStore()
    over.put(_plus(ms.WIDE_REFETCH_MAX_DAYS + 1), chain(), now=1000.0, state="REGULAR")
    assert over.wide_key(_plus(4), today=TODAY) is None
    at = ms.ChainStore()
    at.put(_plus(ms.WIDE_REFETCH_MAX_DAYS), chain(), now=1000.0, state="REGULAR")
    assert at.wide_key(_plus(4), today=TODAY) == _plus(ms.WIDE_REFETCH_MAX_DAYS)


def test_a_held_window_with_an_unreadable_date_is_not_refetched():
    s = ms.ChainStore()
    odd = ms.ChainKey("SPY", from_date=TODAY.isoformat(), to_date="2026-10-1x")
    s.put(odd, chain(), now=1000.0, state="REGULAR")
    assert odd.covers(NARROW)                 # as strings it does cover
    assert s.wide_key(NARROW, today=TODAY) is None


def test_a_hit_returns_schwabs_header_untouched_and_a_cut_recounts():
    s = ms.ChainStore()
    s.put(WIDE, {**chain(), "numberOfContracts": 999}, now=1000.0, state="REGULAR")
    hit = s.lookup(WIDE, max_age=45, now=1001.0, state="REGULAR")
    assert json.loads(hit.body)["numberOfContracts"] == 999
    sub = s.lookup(NARROW, max_age=45, now=1001.0, state="REGULAR")
    assert json.loads(sub.body)["numberOfContracts"] == 2 * 3 * len(STRIKES)


def test_an_entry_from_the_future_is_not_served():
    s = _store()                              # fetched at 1000.0
    assert s.lookup(WIDE, max_age=45, now=999.0, state="REGULAR") is None
    assert s.lookup(NARROW, max_age=45, now=999.0, state="REGULAR") is None


def test_the_narrowest_window_inside_the_cap_is_the_one_refetched():
    s = ms.ChainStore()
    s.put(_plus(10), chain(), now=1000.0, state="REGULAR")
    s.put(_plus(7), chain(), now=1001.0, state="REGULAR")
    s.put(_plus(9), chain(), now=1002.0, state="REGULAR")
    assert s.wide_key(_plus(4), today=TODAY, max_days=10) == _plus(7)
    assert s.wide_key(_plus(8), today=TODAY, max_days=10) == _plus(9)


# ---- the widest refetched window is a setting -------------------------------

def test_the_widest_window_refetched_can_be_given_on_the_call():
    s = ms.ChainStore()
    s.put(_plus(7), chain(), now=1000.0, state="REGULAR")
    assert s.wide_key(_plus(4), today=TODAY, max_days=7) == _plus(7)
    assert s.wide_key(_plus(4), today=TODAY, max_days=6) is None
    long = ms.ChainStore()
    long.put(_plus(45), chain(), now=1000.0, state="REGULAR")
    assert long.wide_key(_plus(4), today=TODAY) is None          # the built-in cap
    assert long.wide_key(_plus(4), today=TODAY, max_days=44) is None
    assert long.wide_key(_plus(4), today=TODAY, max_days=45) == _plus(45)


def test_a_widest_window_that_is_not_a_usable_number_keeps_the_built_in_one():
    over, at = ms.ChainStore(), ms.ChainStore()
    over.put(_plus(ms.WIDE_REFETCH_MAX_DAYS + 1), chain(), now=1000.0, state="REGULAR")
    at.put(_plus(ms.WIDE_REFETCH_MAX_DAYS), chain(), now=1000.0, state="REGULAR")
    for bad in (None, "45", float("nan"), float("inf"), True, -1):
        assert over.wide_key(_plus(4), today=TODAY, max_days=bad) is None
        assert (at.wide_key(_plus(4), today=TODAY, max_days=bad)
                == _plus(ms.WIDE_REFETCH_MAX_DAYS))


# ---- a stored body is valid JSON --------------------------------------------

def _strict(text):
    """Parse as JSON proper: NaN and the infinities are not JSON."""
    def refuse(token):
        raise ValueError(token)
    return json.loads(text, parse_constant=refuse)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_chain_holding_a_number_that_is_not_json_is_not_stored(bad):
    in_a_contract = chain()
    in_a_contract["putExpDateMap"]["2026-10-07:2"]["100.0"][0]["gamma"] = bad
    in_the_header = {**chain(), "underlyingPrice": bad}
    for payload in (in_a_contract, in_the_header):
        s = ms.ChainStore()
        assert s.put(WIDE, payload, now=1000.0, state="REGULAR") is False
        assert s.lookup(WIDE, max_age=45, now=1001.0, state="REGULAR") is None


def test_every_body_the_store_renders_is_strict_json():
    s = _store()
    assert _strict(s.lookup(WIDE, max_age=45, now=1001.0, state="REGULAR").body) == chain()
    assert _strict(s.lookup(NARROW, max_age=45, now=1001.0, state="REGULAR").body)
    assert _strict(s.cut(WIDE, NARROW))


# ---- the newest held chain is the one served ---------------------------------

FAR = ms.ChainKey("SPY", from_date="2026-10-05", to_date="2026-11-19")   # 45 days


def test_a_newer_covering_chain_is_served_over_an_older_exact_one():
    # The collector's own week, 80 s old, beside the scan's 45-day chain, 10 s
    # old: real data is in hand and it is the newer chain.
    s = ms.ChainStore()
    s.put(WIDE, chain(spot=100.0), now=1000.0, state="REGULAR")
    s.put(FAR, chain(spot=101.0), now=1070.0, state="REGULAR")
    got = s.lookup(WIDE, max_age=210, now=1080.0, state="REGULAR")
    assert (got.kind, got.age) == ("subset", 10.0)
    body = json.loads(got.body)
    assert body["underlyingPrice"] == 101.0 and sorted(body["callExpDateMap"]) == list(EXPS)


def test_a_newer_exact_chain_is_served_over_an_older_covering_one():
    s = ms.ChainStore()
    s.put(FAR, chain(spot=101.0), now=1000.0, state="REGULAR")
    s.put(WIDE, chain(spot=100.0), now=1070.0, state="REGULAR")
    got = s.lookup(WIDE, max_age=210, now=1080.0, state="REGULAR")
    assert (got.kind, got.age) == ("hit", 10.0)
    assert json.loads(got.body) == chain(spot=100.0)        # Schwab's own, uncut


def test_an_exact_and_a_covering_chain_of_the_same_age_serve_the_exact_one():
    s = ms.ChainStore()
    s.put(FAR, chain(spot=101.0), now=1000.0, state="REGULAR")
    s.put(WIDE, chain(spot=100.0), now=1000.0, state="REGULAR")
    got = s.lookup(WIDE, max_age=210, now=1080.0, state="REGULAR")
    assert got.kind == "hit" and json.loads(got.body)["underlyingPrice"] == 100.0


def test_a_newer_covering_chain_with_nothing_in_the_window_leaves_the_exact_hit():
    s = ms.ChainStore()
    s.put(NARROW, chain(exps=EXPS[:3], spot=100.0), now=1000.0, state="REGULAR")
    s.put(FAR, chain(exps=EXPS[3:], spot=101.0), now=1070.0, state="REGULAR")
    got = s.lookup(NARROW, max_age=210, now=1080.0, state="REGULAR")
    assert (got.kind, got.age) == ("hit", 80.0)
    assert json.loads(got.body)["underlyingPrice"] == 100.0


def test_a_newer_covering_chain_that_is_not_fresh_leaves_the_exact_hit():
    s = ms.ChainStore()
    s.put(WIDE, chain(spot=100.0), now=1000.0, state="REGULAR")
    s.put(FAR, chain(spot=101.0), now=1070.0, state="CLOSED")   # another session
    got = s.lookup(WIDE, max_age=210, now=1080.0, state="REGULAR")
    assert (got.kind, got.age) == ("hit", 80.0)
    # ... and a strike-filtered one never covers at all.
    filtered = ms.ChainKey("SPY", strike_range="NTM", strike_count=50,
                           from_date="2026-10-05", to_date="2026-11-19")
    s.put(filtered, chain(spot=102.0), now=1075.0, state="REGULAR")
    assert s.lookup(WIDE, max_age=210, now=1080.0, state="REGULAR").kind == "hit"


def test_a_stale_exact_chain_is_still_answered_by_a_fresh_covering_one():
    s = ms.ChainStore()
    s.put(WIDE, chain(spot=100.0), now=1000.0, state="REGULAR")
    s.put(FAR, chain(spot=101.0), now=1070.0, state="REGULAR")
    got = s.lookup(WIDE, max_age=20, now=1080.0, state="REGULAR")
    assert (got.kind, got.age) == ("subset", 10.0)
    assert s.lookup(WIDE, max_age=5, now=1080.0, state="REGULAR") is None


def test_the_newest_of_several_covering_chains_beats_an_exact_one_between_them():
    s = ms.ChainStore()
    wider = ms.ChainKey("SPY", from_date="2026-10-05", to_date="2026-10-20")
    s.put(wider, chain(spot=99.0), now=1000.0, state="REGULAR")
    s.put(WIDE, chain(spot=100.0), now=1030.0, state="REGULAR")
    s.put(FAR, chain(spot=101.0), now=1060.0, state="REGULAR")
    got = s.lookup(WIDE, max_age=210, now=1080.0, state="REGULAR")
    assert (got.kind, got.age) == ("subset", 20.0)
    assert json.loads(got.body)["underlyingPrice"] == 101.0


# ---- the shadow verdict: what differs between two chains (audit AC-103) -------

def test_two_chains_that_differ_only_in_values_do_not_differ():
    assert ms.chain_difference(chain(spot=100.0), chain(spot=101.5)) is None


def test_a_different_day_count_in_the_keys_is_a_difference():
    # Stored before midnight, compared after: same dates, every count one less.
    later = tuple(f"{e.split(':')[0]}:{int(e.split(':')[1]) + 1}" for e in EXPS)
    said = ms.chain_difference(chain(exps=later), chain())
    assert said is not None and "2026-10-05:1" in said and "2026-10-05:0" in said


def test_a_different_contract_count_at_one_strike_is_a_difference():
    fresh = chain()
    fresh["callExpDateMap"]["2026-10-07:2"]["100.0"].append({"putCall": "CALL"})
    said = ms.chain_difference(chain(), fresh)
    assert said is not None and "2026-10-07:2" in said and "100.0" in said


def test_a_different_header_count_is_a_difference():
    fresh = chain()
    fresh["numberOfContracts"] += 4
    said = ms.chain_difference(chain(), fresh)
    assert said is not None and "numberOfContracts" in said


def test_a_zero_underlying_price_against_a_real_one_is_a_difference():
    # What Schwab sends for a window holding no expiration; a moving price is not.
    said = ms.chain_difference(chain(spot=100.0), chain(spot=0.0))
    assert said is not None and "underlyingPrice" in said


def test_a_missing_expiration_and_a_missing_strike_are_named():
    said = ms.chain_difference(chain(), chain(exps=EXPS[:3]))
    assert said is not None and "2026-10-12:7" in said
    said = ms.chain_difference(chain(), chain(strikes=STRIKES[:2]))
    assert said is not None and "105.0" in said


def test_a_payload_that_is_not_a_chain_differs_without_raising():
    assert ms.chain_difference(chain(), None) is not None
    odd = chain()
    odd["callExpDateMap"]["2026-10-07:2"] = 5
    assert ms.chain_difference(chain(), odd) is not None
