"""The Strategy Finder's large-chain chooser: typed expirations and the four load
choices (2026-09-14).

A scan over more than LARGE_CHAIN_EXPIRIES listed expirations asks first. These
are the pure pieces it asks with: the typed ``/expirationchain`` rows, the range
count, the dates each choice keeps and the summary the page draws. Every test
passes ``today=`` so none of them flakes at midnight.
"""
import datetime as dt
import types

import pytest

from services.options_svc import compute

TODAY = dt.date(2026, 9, 14)


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


# Shaped like $SPX on 2026-09-14: dailies/weeklies inside 30 days, standard
# monthlies (S), quarterlies (Q), a month-end (M) and a LEAPS monthly. DTE noted.
SPX_ROWS = [
    ("2026-09-15", "W"),   # 1
    ("2026-09-18", "S"),   # 4
    ("2026-09-22", "W"),   # 8
    ("2026-09-30", "Q"),   # 16
    ("2026-10-02", "W"),   # 18
    ("2026-10-14", "W"),   # 30 - the next_30 boundary, inclusive
    ("2026-10-16", "S"),   # 32
    ("2026-10-30", "W"),   # 46
    ("2026-11-20", "S"),   # 67
    ("2026-11-30", "M"),   # 77
    ("2026-12-18", "S"),   # 95
    ("2026-12-31", "Q"),   # 108
    ("2027-12-17", "S"),   # 459
]


def _payload(rows):
    return {"status": "SUCCESS", "expirationList": [
        {"expirationDate": d, "daysToExpiration": 0, "expirationType": t,
         "optionRoots": "SPX", "settlementType": "P", "standard": True}
        for d, t in rows]}


# ── parse_expiration_rows ────────────────────────────────────────────────────

def test_parse_keeps_every_type_sorted_by_date():
    rows = [("2026-10-16", "S"), ("2026-09-15", "W"), ("2026-12-31", "Q"),
            ("2026-11-30", "M")]
    assert compute.parse_expiration_rows(_payload(rows)) == [
        ("2026-09-15", "W"), ("2026-10-16", "S"), ("2026-11-30", "M"),
        ("2026-12-31", "Q")]


def test_parse_drops_junk_rows_like_the_date_list():
    payload = {"expirationList": [
        {"expirationDate": "2026-09-18", "expirationType": "S"},
        {"expirationDate": "not-a-date", "expirationType": "W"},
        {"expirationDate": None, "expirationType": "W"},
        {"expirationType": "W"},
        "2026-09-22",
        None,
        {"expirationDate": "2026-09-22T00:00:00", "expirationType": "W"},
    ]}
    assert compute.parse_expiration_rows(payload) == [
        ("2026-09-18", "S"), ("2026-09-22", "W")]


def test_parse_missing_type_is_none_not_a_guess():
    payload = {"expirationList": [{"expirationDate": "2026-09-18"},
                                  {"expirationDate": "2026-09-22", "expirationType": ""}]}
    assert compute.parse_expiration_rows(payload) == [
        ("2026-09-18", None), ("2026-09-22", None)]


@pytest.mark.parametrize("payload", [None, [], "x", {}, {"expirationList": None}])
def test_parse_junk_payload_is_empty(payload):
    assert compute.parse_expiration_rows(payload) == []


def test_parse_duplicate_dates_collapse_and_a_standard_monthly_wins():
    # The monthly choice keeps "S"; a duplicate listed under another type must not
    # hide the standard monthly, whichever order it arrives in.
    for rows in ([("2026-09-18", "W"), ("2026-09-18", "S")],
                 [("2026-09-18", "S"), ("2026-09-18", "W")]):
        assert compute.parse_expiration_rows(_payload(rows)) == [("2026-09-18", "S")]


def test_parse_duplicate_without_a_standard_keeps_the_first_listed_type():
    rows = [("2026-09-30", "Q"), ("2026-09-30", "W"), ("2026-09-30", "M")]
    assert compute.parse_expiration_rows(_payload(rows)) == [("2026-09-30", "Q")]
    payload = {"expirationList": [{"expirationDate": "2026-09-30"},
                                  {"expirationDate": "2026-09-30", "expirationType": "W"}]}
    assert compute.parse_expiration_rows(payload) == [("2026-09-30", "W")]


def test_parse_agrees_with_the_date_list():
    payload = _payload(SPX_ROWS + [("2026-09-18", "W")])
    assert [d for d, _ in compute.parse_expiration_rows(payload)] == \
        compute.parse_expiration_list(payload)


# ── option_expiration_rows ───────────────────────────────────────────────────

def test_option_expiration_rows_returns_typed_rows(monkeypatch):
    seen = []

    def _exps(api):
        seen.append(api)
        return _Resp(_payload(SPX_ROWS))

    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_expirations",
                        _exps, raising=False)
    assert compute.option_expiration_rows("$SPX") == SPX_ROWS
    assert seen == ["$SPX"]


def test_option_expiration_rows_non_200_is_empty(monkeypatch):
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_expirations",
                        lambda api: _Resp(_payload(SPX_ROWS), status=502), raising=False)
    assert compute.option_expiration_rows("$SPX") == []


def test_option_expiration_rows_conftest_default_is_empty():
    # The autouse stub answers 503: no test reaches the live list by accident.
    assert compute.option_expiration_rows("SPY") == []


def test_option_expiration_rows_client_without_the_method_is_empty(monkeypatch):
    monkeypatch.setattr(compute._proxy, "schwab_py_client", types.SimpleNamespace())
    assert compute.option_expiration_rows("SPY") == []


# ── rows_in_range ────────────────────────────────────────────────────────────

def test_rows_in_range_no_upper_bound_keeps_the_long_end():
    past = [("2026-09-11", "W")]
    got = compute.rows_in_range(past + SPX_ROWS, 0, None, today=TODAY)
    assert got == SPX_ROWS


def test_rows_in_range_floor_and_ceiling_are_inclusive():
    got = compute.rows_in_range(SPX_ROWS, 4, 32, today=TODAY)
    assert [d for d, _ in got] == ["2026-09-18", "2026-09-22", "2026-09-30",
                                   "2026-10-02", "2026-10-14", "2026-10-16"]


def test_rows_in_range_dte_min_floor_only():
    got = compute.rows_in_range(SPX_ROWS, 90, None, today=TODAY)
    assert [d for d, _ in got] == ["2026-12-18", "2026-12-31", "2027-12-17"]


def test_rows_in_range_empty_input():
    assert compute.rows_in_range([], 0, None, today=TODAY) == []
    assert compute.rows_in_range(None, 0, None, today=TODAY) == []


# ── choice_dates ─────────────────────────────────────────────────────────────

def test_choice_next_30_keeps_dte_up_to_30_inclusive():
    assert compute.choice_dates(SPX_ROWS, "next_30", 0, None, today=TODAY) == [
        "2026-09-15", "2026-09-18", "2026-09-22", "2026-09-30", "2026-10-02",
        "2026-10-14"]


def test_choice_next_90_keeps_dte_up_to_90():
    assert compute.choice_dates(SPX_ROWS, "next_90", 0, None, today=TODAY) == [
        "2026-09-15", "2026-09-18", "2026-09-22", "2026-09-30", "2026-10-02",
        "2026-10-14", "2026-10-16", "2026-10-30", "2026-11-20", "2026-11-30"]


def test_choice_monthly_is_standard_only_not_quarterly_or_month_end():
    assert compute.choice_dates(SPX_ROWS, "monthly", 0, None, today=TODAY) == [
        "2026-09-18", "2026-10-16", "2026-11-20", "2026-12-18", "2027-12-17"]


def test_choice_all_is_everything_in_range():
    assert compute.choice_dates(SPX_ROWS, "all", 0, None, today=TODAY) == \
        [d for d, _ in SPX_ROWS]


def test_choices_count_inside_the_requested_range():
    # dte_min 7 drops the 1- and 4-day expiries from next_30; dte_max 100 drops
    # the Dec 31 quarterly and the LEAPS from everything.
    assert compute.choice_dates(SPX_ROWS, "next_30", 7, 100, today=TODAY) == [
        "2026-09-22", "2026-09-30", "2026-10-02", "2026-10-14"]
    assert compute.choice_dates(SPX_ROWS, "monthly", 7, 100, today=TODAY) == [
        "2026-10-16", "2026-11-20", "2026-12-18"]
    assert compute.choice_dates(SPX_ROWS, "all", 7, 100, today=TODAY) == [
        "2026-09-22", "2026-09-30", "2026-10-02", "2026-10-14", "2026-10-16",
        "2026-10-30", "2026-11-20", "2026-11-30", "2026-12-18"]


def test_a_choice_narrower_than_the_range_floor_is_empty_not_an_error():
    assert compute.choice_dates(SPX_ROWS, "next_30", 60, None, today=TODAY) == []


@pytest.mark.parametrize("choice", ["", "weekly", None, "NEXT_30"])
def test_unknown_choice_raises(choice):
    with pytest.raises(ValueError):
        compute.choice_dates(SPX_ROWS, choice, 0, None, today=TODAY)


# ── choice_summary ───────────────────────────────────────────────────────────

def test_choices_are_the_four_in_order():
    assert [k for k, _ in compute.EXPIRY_CHOICES] == ["next_30", "next_90", "monthly", "all"]


def test_summary_order_counts_and_estimates():
    got = compute.choice_summary(SPX_ROWS, 0, None, today=TODAY)
    assert [(c["key"], c["label"]) for c in got] == list(compute.EXPIRY_CHOICES)
    assert [c["count"] for c in got] == [6, 10, 5, 13]
    # 6 x 0.75 = 4.5 and 10 x 0.75 = 7.5: half-up, not banker's rounding.
    assert [c["est_seconds"] for c in got] == [5, 8, 4, 10]
    assert all(set(c) == {"key", "label", "count", "est_seconds"} for c in got)


def test_summary_estimate_half_rounds_up():
    rows = [(f"2026-09-{15 + i:02d}", "W") for i in range(2)]       # 2 x 0.75 = 1.5
    got = {c["key"]: c for c in compute.choice_summary(rows, 0, None, today=TODAY)}
    assert got["all"]["count"] == 2
    assert got["all"]["est_seconds"] == 2


def test_summary_lists_a_zero_choice():
    rows = [("2026-12-31", "Q"), ("2027-03-31", "Q")]
    got = compute.choice_summary(rows, 0, None, today=TODAY)
    assert [(c["key"], c["count"], c["est_seconds"]) for c in got] == [
        ("next_30", 0, 0), ("next_90", 0, 0), ("monthly", 0, 0), ("all", 2, 2)]


def test_summary_estimate_uses_the_constant(monkeypatch):
    monkeypatch.setattr(compute, "SCAN_SEC_PER_EXPIRY", 2.0)
    got = compute.choice_summary(SPX_ROWS, 0, None, today=TODAY)
    assert [c["est_seconds"] for c in got] == [12, 20, 10, 26]


def test_large_chain_threshold():
    assert compute.LARGE_CHAIN_EXPIRIES == 30
    assert compute.SCAN_SEC_PER_EXPIRY == 0.75
