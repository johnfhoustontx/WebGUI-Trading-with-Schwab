"""The Strategy Finder's large-chain chooser: typed expirations and the four load
choices (2026-09-14).

A scan over more than LARGE_CHAIN_EXPIRIES listed expirations asks first. These
are the pure pieces it asks with: the typed ``/expirationchain`` rows, the range
count, the dates each choice keeps and the summary the page draws.

DTE is Schwab's ``daysToExpiration``, the same number the chain keys carry, so the
chooser and the builders agree on a boundary expiry even between 23:00 and 24:00
CT. The calendar difference is only a fallback, and every test that can reach it
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


def _cal(date):
    return (dt.date.fromisoformat(date) - TODAY).days


# Shaped like $SPX on 2026-09-14: dailies/weeklies inside 30 days, standard
# monthlies (S), quarterlies (Q), a month-end (M) and a LEAPS monthly. DTE noted.
SPX_PAIRS = [
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
SPX_ROWS = [(d, t, _cal(d)) for d, t in SPX_PAIRS]


def _payload(pairs, dtes=None):
    """Rows with Schwab's daysToExpiration set to the calendar DTE unless ``dtes``
    overrides it per date."""
    dtes = dtes or {}
    return {"status": "SUCCESS", "expirationList": [
        {"expirationDate": d, "daysToExpiration": dtes.get(d, _cal(d)),
         "expirationType": t, "optionRoots": "SPX", "settlementType": "P",
         "standard": True}
        for d, t in pairs]}


def _dates(rows):
    return [r[0] for r in rows]


# ── parse_expiration_rows ────────────────────────────────────────────────────

def test_parse_keeps_every_type_sorted_by_date():
    pairs = [("2026-10-16", "S"), ("2026-09-15", "W"), ("2026-12-31", "Q"),
             ("2026-11-30", "M")]
    assert compute.parse_expiration_rows(_payload(pairs), today=TODAY) == [
        ("2026-09-15", "W", 1), ("2026-10-16", "S", 32), ("2026-11-30", "M", 77),
        ("2026-12-31", "Q", 108)]


def test_parse_drops_junk_rows_like_the_date_list():
    payload = {"expirationList": [
        {"expirationDate": "2026-09-18", "expirationType": "S", "daysToExpiration": 4},
        {"expirationDate": "not-a-date", "expirationType": "W"},
        {"expirationDate": None, "expirationType": "W"},
        {"expirationType": "W"},
        "2026-09-22",
        None,
        {"expirationDate": "2026-09-22T00:00:00", "expirationType": "W",
         "daysToExpiration": 8},
    ]}
    assert compute.parse_expiration_rows(payload, today=TODAY) == [
        ("2026-09-18", "S", 4), ("2026-09-22", "W", 8)]


def test_parse_missing_or_non_string_type_is_none_not_a_guess():
    payload = {"expirationList": [
        {"expirationDate": "2026-09-18", "daysToExpiration": 4},
        {"expirationDate": "2026-09-22", "expirationType": "", "daysToExpiration": 8},
        {"expirationDate": "2026-09-23", "expirationType": "   ", "daysToExpiration": 9},
        {"expirationDate": "2026-09-24", "expirationType": 7, "daysToExpiration": 10},
        {"expirationDate": "2026-09-25", "expirationType": ["S"], "daysToExpiration": 11},
    ]}
    assert [r[1] for r in compute.parse_expiration_rows(payload, today=TODAY)] == \
        [None, None, None, None, None]


def test_parse_type_is_stripped_and_upper_cased():
    payload = {"expirationList": [
        {"expirationDate": "2026-09-18", "expirationType": " s ", "daysToExpiration": 4},
        {"expirationDate": "2026-09-22", "expirationType": "w", "daysToExpiration": 8},
    ]}
    rows = compute.parse_expiration_rows(payload, today=TODAY)
    assert rows == [("2026-09-18", "S", 4), ("2026-09-22", "W", 8)]
    assert compute.choice_dates(rows, "monthly", 0, None) == ["2026-09-18"]


@pytest.mark.parametrize("payload", [None, [], "x", {}, {"expirationList": None}])
def test_parse_junk_payload_is_empty(payload):
    assert compute.parse_expiration_rows(payload, today=TODAY) == []


def test_parse_duplicate_dates_collapse_and_a_standard_monthly_wins():
    # The monthly choice keeps "S"; a duplicate listed under another type must not
    # hide the standard monthly, whichever order it arrives in.
    for pairs in ([("2026-09-18", "W"), ("2026-09-18", "S")],
                  [("2026-09-18", "S"), ("2026-09-18", "W")]):
        assert compute.parse_expiration_rows(_payload(pairs), today=TODAY) == [
            ("2026-09-18", "S", 4)]


def test_parse_duplicate_without_a_standard_keeps_the_first_listed_type():
    pairs = [("2026-09-30", "Q"), ("2026-09-30", "W"), ("2026-09-30", "M")]
    assert compute.parse_expiration_rows(_payload(pairs), today=TODAY) == [
        ("2026-09-30", "Q", 16)]
    payload = {"expirationList": [{"expirationDate": "2026-09-30"},
                                  {"expirationDate": "2026-09-30", "expirationType": "W",
                                   "daysToExpiration": 16}]}
    assert compute.parse_expiration_rows(payload, today=TODAY) == [("2026-09-30", "W", 16)]


def test_parse_agrees_with_the_date_list():
    payload = _payload(SPX_PAIRS + [("2026-09-18", "W")])
    assert _dates(compute.parse_expiration_rows(payload, today=TODAY)) == \
        compute.parse_expiration_list(payload)


def test_parse_dte_is_schwabs_number_not_the_host_calendar():
    # 23:30 CT on 2026-09-14: Schwab has already rolled to the 15th, so the Oct 15
    # expiry is 30 days out on its side and 31 on the host's calendar.
    payload = _payload([("2026-10-15", "W")], dtes={"2026-10-15": 30})
    assert compute.parse_expiration_rows(payload, today=TODAY) == [("2026-10-15", "W", 30)]


def test_parse_whole_float_dte_is_accepted():
    payload = _payload([("2026-10-15", "W")], dtes={"2026-10-15": 30.0})
    rows = compute.parse_expiration_rows(payload, today=TODAY)
    assert rows == [("2026-10-15", "W", 30)]
    assert isinstance(rows[0][2], int)


@pytest.mark.parametrize("raw", [None, "30", True, False, float("nan"), float("inf"),
                                 30.5, -1, -1.0, [30], {"d": 30}])
def test_parse_unusable_dte_falls_back_to_the_calendar(raw):
    payload = {"expirationList": [{"expirationDate": "2026-10-15", "expirationType": "W",
                                   "daysToExpiration": raw}]}
    assert compute.parse_expiration_rows(payload, today=TODAY) == [("2026-10-15", "W", 31)]


def test_parse_missing_dte_falls_back_to_the_calendar():
    payload = {"expirationList": [{"expirationDate": "2026-10-15", "expirationType": "W"}]}
    assert compute.parse_expiration_rows(payload, today=TODAY) == [("2026-10-15", "W", 31)]


def test_parse_an_implausible_schwab_dte_counts_by_the_calendar_and_speaks_once():
    # A 0 on every row: no clock skew explains it, so the calendar decides, and
    # the call is reported once, naming the first date it distrusted.
    from services import _degrade

    _degrade.reset()
    pairs = SPX_PAIRS[1:]                      # from Sep 18: every row >= 4 days out
    payload = _payload(pairs, dtes={d: 0 for d, _ in pairs})
    assert compute.parse_expiration_rows(payload, today=TODAY) == SPX_ROWS[1:]
    assert _degrade.counts().get("options.expiration_dte") == 1


def test_parse_a_schwab_dte_one_day_off_the_calendar_is_trusted_silently():
    # 23:00-24:00 CT: Schwab one day ahead of the host, in either direction by
    # exactly one day.
    from services import _degrade

    _degrade.reset()
    payload = _payload([("2026-10-15", "W"), ("2026-10-16", "S")],
                       dtes={"2026-10-15": 30, "2026-10-16": 33})
    assert compute.parse_expiration_rows(payload, today=TODAY) == [
        ("2026-10-15", "W", 30), ("2026-10-16", "S", 33)]
    assert "options.expiration_dte" not in _degrade.counts()


def test_parse_a_duplicate_can_supply_the_plausible_dte_a_bad_row_did_not():
    # The date is decided by Schwab's number, not the calendar, so nothing fell
    # back and nothing is reported - in either row order.
    from services import _degrade

    good = {"expirationDate": "2026-10-15", "expirationType": "W", "daysToExpiration": 30}
    bad = {"expirationDate": "2026-10-15", "expirationType": "W", "daysToExpiration": 0}
    for order in ([bad, good], [good, bad]):
        _degrade.reset()
        assert compute.parse_expiration_rows({"expirationList": order}, today=TODAY) == [
            ("2026-10-15", "W", 30)]
        assert "options.expiration_dte" not in _degrade.counts()


def test_parse_reports_the_first_date_that_fell_back(monkeypatch):
    from services import _degrade

    seen = []
    monkeypatch.setattr(_degrade, "degraded", lambda area, **k: seen.append((area, k)))
    payload = {"expirationList": [
        {"expirationDate": "2026-10-15", "expirationType": "W", "daysToExpiration": 0},
        {"expirationDate": "2026-10-15", "expirationType": "W", "daysToExpiration": 30},
        {"expirationDate": "2026-10-16", "expirationType": "S", "daysToExpiration": 0},
    ]}
    assert compute.parse_expiration_rows(payload, today=TODAY) == [
        ("2026-10-15", "W", 30), ("2026-10-16", "S", 32)]
    assert seen == [("options.expiration_dte",
                     {"detail": "2026-10-16", "exc_info": False})]


def test_parse_duplicate_takes_the_first_usable_schwab_dte():
    payload = {"expirationList": [
        {"expirationDate": "2026-10-15", "expirationType": "W", "daysToExpiration": "x"},
        {"expirationDate": "2026-10-15", "expirationType": "W", "daysToExpiration": 30},
        {"expirationDate": "2026-10-15", "expirationType": "W", "daysToExpiration": 29},
    ]}
    assert compute.parse_expiration_rows(payload, today=TODAY) == [("2026-10-15", "W", 30)]


# ── option_expiration_rows ───────────────────────────────────────────────────

def test_option_expiration_rows_returns_typed_rows(monkeypatch):
    seen = []

    def _exps(api):
        seen.append(api)
        return _Resp(_payload(SPX_PAIRS))

    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_expirations",
                        _exps, raising=False)
    # ``today`` is load-bearing even though every row carries a daysToExpiration:
    # the plausibility check compares that number to the calendar difference, so
    # without it this test read the HOST date and failed from 2026-09-16, once the
    # fixture's 09-14 fell more than a day behind.
    assert compute.option_expiration_rows("$SPX", today=TODAY) == SPX_ROWS
    assert seen == ["$SPX"]


def test_option_expiration_rows_non_200_is_empty(monkeypatch):
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_expirations",
                        lambda api: _Resp(_payload(SPX_PAIRS), status=502), raising=False)
    assert compute.option_expiration_rows("$SPX") == []


def test_option_expiration_rows_conftest_default_is_empty():
    # The autouse stub answers 503: no test reaches the live list by accident.
    assert compute.option_expiration_rows("SPY") == []


def test_option_expiration_rows_client_without_the_method_is_empty(monkeypatch):
    monkeypatch.setattr(compute._proxy, "schwab_py_client", types.SimpleNamespace())
    assert compute.option_expiration_rows("SPY") == []


# ── rows_in_range ────────────────────────────────────────────────────────────

def test_rows_in_range_no_upper_bound_keeps_the_long_end():
    past = [("2026-09-11", "W", -3)]
    assert compute.rows_in_range(past + SPX_ROWS, 0, None) == SPX_ROWS


def test_rows_in_range_floor_and_ceiling_are_inclusive():
    assert _dates(compute.rows_in_range(SPX_ROWS, 4, 32)) == [
        "2026-09-18", "2026-09-22", "2026-09-30", "2026-10-02", "2026-10-14",
        "2026-10-16"]


def test_rows_in_range_dte_min_floor_only():
    assert _dates(compute.rows_in_range(SPX_ROWS, 90, None)) == [
        "2026-12-18", "2026-12-31", "2027-12-17"]


def test_rows_in_range_negative_dte_min_never_admits_a_past_row():
    past = [("2026-09-11", "W", -3), ("2026-09-13", "W", -1)]
    assert compute.rows_in_range(past + SPX_ROWS, -5, None) == SPX_ROWS
    assert compute.rows_in_range(past, -5, 10) == []


def test_rows_in_range_uses_the_row_dte():
    # Oct 15 is 31 calendar days out but 30 by Schwab: a 30-day ceiling keeps it.
    rows = [("2026-10-15", "W", 30)]
    assert compute.rows_in_range(rows, 0, 30) == rows


def test_rows_in_range_empty_input():
    assert compute.rows_in_range([], 0, None) == []
    assert compute.rows_in_range(None, 0, None) == []


# ── choice_dates ─────────────────────────────────────────────────────────────

def test_choice_next_30_keeps_dte_up_to_30_inclusive():
    assert compute.choice_dates(SPX_ROWS, "next_30", 0, None) == [
        "2026-09-15", "2026-09-18", "2026-09-22", "2026-09-30", "2026-10-02",
        "2026-10-14"]


def test_choice_next_90_keeps_dte_up_to_90():
    assert compute.choice_dates(SPX_ROWS, "next_90", 0, None) == [
        "2026-09-15", "2026-09-18", "2026-09-22", "2026-09-30", "2026-10-02",
        "2026-10-14", "2026-10-16", "2026-10-30", "2026-11-20", "2026-11-30"]


def test_choice_monthly_is_standard_only_not_quarterly_or_month_end():
    assert compute.choice_dates(SPX_ROWS, "monthly", 0, None) == [
        "2026-09-18", "2026-10-16", "2026-11-20", "2026-12-18", "2027-12-17"]


def test_choice_all_is_everything_in_range():
    assert compute.choice_dates(SPX_ROWS, "all", 0, None) == _dates(SPX_ROWS)


def test_choices_count_inside_the_requested_range():
    # dte_min 7 drops the 1- and 4-day expiries from next_30; dte_max 100 drops
    # the Dec 31 quarterly and the LEAPS from everything.
    assert compute.choice_dates(SPX_ROWS, "next_30", 7, 100) == [
        "2026-09-22", "2026-09-30", "2026-10-02", "2026-10-14"]
    assert compute.choice_dates(SPX_ROWS, "monthly", 7, 100) == [
        "2026-10-16", "2026-11-20", "2026-12-18"]
    assert compute.choice_dates(SPX_ROWS, "all", 7, 100) == [
        "2026-09-22", "2026-09-30", "2026-10-02", "2026-10-14", "2026-10-16",
        "2026-10-30", "2026-11-20", "2026-11-30", "2026-12-18"]


def test_choice_next_30_counts_by_schwabs_dte_at_the_boundary():
    # Parsed from Schwab's rows at 23:30 CT: 30 by Schwab, 31 by the host calendar.
    rows = compute.parse_expiration_rows(
        _payload([("2026-10-14", "W"), ("2026-10-15", "W"), ("2026-10-16", "S")],
                 dtes={"2026-10-14": 29, "2026-10-15": 30, "2026-10-16": 31}),
        today=TODAY)
    assert compute.choice_dates(rows, "next_30", 0, None) == ["2026-10-14", "2026-10-15"]


def test_a_choice_narrower_than_the_range_floor_is_empty_not_an_error():
    assert compute.choice_dates(SPX_ROWS, "next_30", 60, None) == []


@pytest.mark.parametrize("choice", ["", "weekly", None, "NEXT_30"])
def test_unknown_choice_raises(choice):
    with pytest.raises(ValueError):
        compute.choice_dates(SPX_ROWS, choice, 0, None)


# ── choice_summary ───────────────────────────────────────────────────────────

def test_choices_are_the_four_in_order():
    assert [k for k, _ in compute.EXPIRY_CHOICES] == ["next_30", "next_90", "monthly", "all"]


def test_summary_order_counts_and_estimates():
    got = compute.choice_summary(SPX_ROWS, 0, None)
    assert [(c["key"], c["label"]) for c in got] == list(compute.EXPIRY_CHOICES)
    assert [c["count"] for c in got] == [6, 10, 5, 13]
    # 6 x 0.75 = 4.5 and 10 x 0.75 = 7.5: a half rounds up, not to even.
    assert [c["est_seconds"] for c in got] == [5, 8, 4, 10]
    assert all(set(c) == {"key", "label", "count", "est_seconds"} for c in got)


def test_summary_estimate_half_rounds_up():
    rows = [("2026-09-15", "W", 1), ("2026-09-16", "W", 2)]       # 2 x 0.75 = 1.5
    got = {c["key"]: c for c in compute.choice_summary(rows, 0, None)}
    assert got["all"]["count"] == 2
    assert got["all"]["est_seconds"] == 2


def test_summary_lists_a_zero_choice():
    rows = [("2026-12-31", "Q", 108), ("2027-03-31", "Q", 198)]
    got = compute.choice_summary(rows, 0, None)
    assert [(c["key"], c["count"], c["est_seconds"]) for c in got] == [
        ("next_30", 0, 0), ("next_90", 0, 0), ("monthly", 0, 0), ("all", 2, 2)]


def test_summary_estimate_uses_the_constant(monkeypatch):
    monkeypatch.setattr(compute, "SCAN_SEC_PER_EXPIRY", 2.0)
    got = compute.choice_summary(SPX_ROWS, 0, None)
    assert [c["est_seconds"] for c in got] == [12, 20, 10, 26]


def test_large_chain_threshold():
    assert compute.LARGE_CHAIN_EXPIRIES == 30
    assert compute.SCAN_SEC_PER_EXPIRY == 0.75
