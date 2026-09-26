"""The daily watchlist dividend pull (news v2, Task 13).

trade_svc asks the proxy for each followed symbol's quote ``fundamental`` block
and writes the forward ex/pay dates into the shared store
(``shared/dividends.py``) that news_svc reads. Schwab's field names are
unverified on prod, so the parser accepts both documented spellings and
tolerates missing or odd fields.

No network, no proxy and no live store: every fetch is a stub and every store
lives under ``tmp_path``.
"""
import json
import pathlib

import pytest

from services import _degrade
from services.trade_svc import dividends
from shared import dividends as store

_FIX = pathlib.Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


QUOTE_FUND = _load("quote_fundamental_jpm.json")
INSTR_FUND = _load("quote_fundamental_jpm_instruments_spelling.json")
NONPAYER = _load("quote_fundamental_nonpayer.json")


def test_quote_spelling():
    rows, status = dividends.parse_fundamental("JPM", QUOTE_FUND, today="2026-09-26")
    assert status == "ok"
    assert {"symbol": "JPM", "ex_date": "2026-10-06", "pay_date": "2026-10-31",
            "amount": 1.40, "frequency": 4, "declared_date": "2026-09-16"} in rows


def test_instruments_spelling_is_accepted_too():
    assert dividends.parse_fundamental("JPM", INSTR_FUND)[1] == "ok"


@pytest.mark.parametrize("raw", ["2026-10-06", "2026-10-06T00:00:00Z", "2026-10-06 00:00:00.0"])
def test_date_formats(raw):
    assert dividends._date(raw) == "2026-10-06"


def test_per_payment_amount_preferred_then_annual_over_frequency():
    assert dividends._amount({"divPayAmount": 1.4, "divAmount": 5.6, "divFreq": 4}) == 1.4
    assert dividends._amount({"divAmount": "5.60", "divFreq": 4}) == 1.4
    assert dividends._amount({"divAmount": float("nan"), "divFreq": 4}) is None


def test_non_payer_is_none_status_not_error():
    assert dividends.parse_fundamental("NVDX", NONPAYER) == ([], "none")


def test_junk_is_error_status_never_raises():
    assert dividends.parse_fundamental("X", "junk")[1] == "error"


def test_refresh_skips_indices_and_runs_once_a_day(tmp_path):
    calls = []
    fake = lambda sym: calls.append(sym) or {sym: {"fundamental": QUOTE_FUND}}  # noqa: E731
    n = dividends.refresh(fetch=fake, symbols=["$SPX", "JPM"], db_path=tmp_path / "d.db", today="2026-09-26")
    assert calls == ["JPM"] and n == 1
    assert dividends.refresh(fetch=fake, symbols=["JPM"], db_path=tmp_path / "d.db", today="2026-09-26") == 0


def test_one_failing_symbol_is_an_error_row_not_an_abort(tmp_path):
    _degrade.reset()

    def fetch(sym):
        if sym == "AAA":
            raise RuntimeError("proxy down")
        return {sym: {"fundamental": QUOTE_FUND}}

    dividends.refresh(fetch=fetch, symbols=["AAA", "JPM"], db_path=tmp_path / "d.db",
                      today="2026-09-26")
    conn = store.init_db(tmp_path / "d.db")
    try:
        assert store.coverage(conn, ["AAA", "JPM"]) == {"AAA": "error", "JPM": "ok"}
    finally:
        store.close_db(conn)
    assert _degrade.counts().get("trade.dividends") == 1


# ── beyond the plan's list: the edges the brief called out ──────────────────

def test_a_missing_or_nan_amount_is_stored_none_never_zero():
    fund = {"divExDate": "2026-10-06T00:00:00Z", "divAmount": float("nan"), "divFreq": 4}
    rows, status = dividends.parse_fundamental("JPM", fund, today="2026-09-26")
    assert status == "ok"
    assert rows[0]["amount"] is None
    fund = {"divExDate": "2026-10-06T00:00:00Z"}
    assert dividends.parse_fundamental("JPM", fund, today="2026-09-26")[0][0]["amount"] is None


def test_odd_fields_are_tolerated():
    assert dividends._date(None) is None
    assert dividends._date("") is None
    assert dividends._date("not a date") is None
    assert dividends._date(True) is None
    assert dividends._amount({"divAmount": 5.6, "divFreq": 0}) is None
    assert dividends._amount({"divAmount": 5.6, "divFreq": "junk"}) is None
    assert dividends._amount({"divPayAmount": True}) is None
    assert dividends._amount({"divPayAmount": float("inf")}) is None


def test_a_quote_without_a_fundamental_block_is_error(tmp_path):
    fetch = lambda sym: {sym: {"quote": {"lastPrice": 1.0}}}  # noqa: E731
    dividends.refresh(fetch=fetch, symbols=["JPM"], db_path=tmp_path / "d.db", today="2026-09-26")
    conn = store.init_db(tmp_path / "d.db")
    try:
        assert store.coverage(conn, ["JPM"]) == {"JPM": "error"}
    finally:
        store.close_db(conn)


def _fund_with(ex, pay):
    return {"divExDate": ex, "divPayDate": pay, "divPayAmount": 1.4, "divFreq": 4}


def test_a_revised_date_leaves_no_stale_row_and_an_error_keeps_what_it_had(tmp_path):
    db = tmp_path / "d.db"
    funds = {"JPM": _fund_with("2026-10-06", "2026-10-31"),
             "KO": _fund_with("2026-10-10", "2026-11-01")}
    fetch = lambda sym: {sym: {"fundamental": funds[sym]}}  # noqa: E731
    dividends.refresh(fetch=fetch, symbols=["JPM", "KO"], db_path=db, today="2026-09-25")

    # Next day: JPM's date is revised, KO's fetch fails.
    funds["JPM"] = _fund_with("2026-10-08", "2026-10-31")

    def fetch2(sym):
        if sym == "KO":
            raise RuntimeError("proxy down")
        return {sym: {"fundamental": funds[sym]}}

    dividends.refresh(fetch=fetch2, symbols=["JPM", "KO"], db_path=db, today="2026-09-26")
    conn = store.init_db(db)
    try:
        rows = store.upcoming(conn, ["JPM", "KO"], "2026-09-26", "2026-12-31")
    finally:
        store.close_db(conn)
    assert [(r["symbol"], r["ex_date"]) for r in rows] == [("JPM", "2026-10-08"),
                                                          ("KO", "2026-10-10")]


def test_a_suspended_dividend_clears_the_forward_row(tmp_path):
    db = tmp_path / "d.db"
    fund = {"JPM": _fund_with("2026-10-06", "2026-10-31")}
    fetch = lambda sym: {sym: {"fundamental": fund[sym]}}  # noqa: E731
    dividends.refresh(fetch=fetch, symbols=["JPM"], db_path=db, today="2026-09-25")
    fund["JPM"] = NONPAYER
    dividends.refresh(fetch=fetch, symbols=["JPM"], db_path=db, today="2026-09-26")
    conn = store.init_db(db)
    try:
        assert store.upcoming(conn, ["JPM"], "2026-09-26", "2026-12-31") == []
        assert store.coverage(conn, ["JPM"]) == {"JPM": "none"}
    finally:
        store.close_db(conn)


def test_old_rows_are_pruned_past_the_lookback(tmp_path):
    db = tmp_path / "d.db"
    conn = store.init_db(db)
    store.upsert(conn, [{"symbol": "KO", "ex_date": "2026-01-02", "amount": 0.5}], now="t")
    store.close_db(conn)
    fetch = lambda sym: {sym: {"fundamental": NONPAYER}}  # noqa: E731
    dividends.refresh(fetch=fetch, symbols=["JPM"], db_path=db, today="2026-09-26")
    conn = store.init_db(db)
    try:
        assert store.upcoming(conn, ["KO"], "2000-01-01", "2030-01-01") == []
    finally:
        store.close_db(conn)


def test_a_proxy_failure_reply_is_error_and_a_degrade(tmp_path):
    _degrade.reset()
    dividends.refresh(fetch=lambda sym: None, symbols=["JPM"], db_path=tmp_path / "d.db",
                      today="2026-09-26")
    conn = store.init_db(tmp_path / "d.db")
    try:
        assert store.coverage(conn, ["JPM"]) == {"JPM": "error"}
    finally:
        store.close_db(conn)
    assert _degrade.counts().get("trade.dividends") == 1


# ── review fixes ────────────────────────────────────────────────────────────

def _store_rows(db):
    conn = store.init_db(db)
    try:
        return store.upcoming(conn, ["KO", "JPM"], "2000-01-01", "2099-12-31")
    finally:
        store.close_db(conn)


def test_the_lookback_is_read_from_config_not_always_three(tmp_path, monkeypatch):
    """calendar_config() drops sub-tables, so reading ["dividends"] off it
    always fell back to 3. The pull must read [calendar.dividends]."""
    from shared import news_config
    monkeypatch.setattr(news_config, "dividends_config",
                        lambda: {"enabled": True, "refresh_at": "06:40",
                                 "horizon_days": 30, "lookback_days": 10})
    assert dividends._lookback_days() == 10
    db = tmp_path / "d.db"
    conn = store.init_db(db)
    store.upsert(conn, [{"symbol": "KO", "ex_date": "2026-09-20", "amount": 0.5},
                        {"symbol": "KO", "ex_date": "2026-09-10", "amount": 0.5}], now="t")
    store.close_db(conn)
    fetch = lambda sym: {sym: {"fundamental": NONPAYER}}  # noqa: E731
    dividends.refresh(fetch=fetch, symbols=["JPM"], db_path=db, today="2026-09-26")
    # 9/20 is 6 days back: kept under a 10-day lookback (a 3-day one prunes it).
    assert [r["ex_date"] for r in _store_rows(db)] == ["2026-09-20"]


def test_a_past_ex_date_hands_its_amount_to_the_next_forward_row():
    fund = {"divExDate": "2026-09-10", "divPayDate": "2026-09-30", "divPayAmount": 1.4,
            "divFreq": 4, "nextDivExDate": "2026-12-10", "nextDivPayDate": "2026-12-31"}
    rows, status = dividends.parse_fundamental("JPM", fund, today="2026-09-26")
    assert status == "ok"
    forward = [r for r in rows if r["ex_date"] >= "2026-09-26"]
    assert forward == [{"symbol": "JPM", "ex_date": "2026-12-10", "pay_date": "2026-12-31",
                        "amount": 1.4, "frequency": 4, "declared_date": None}]


def test_a_future_ex_date_keeps_the_next_row_amount_unknown():
    rows, _ = dividends.parse_fundamental("JPM", QUOTE_FUND, today="2026-09-26")
    nxt = [r for r in rows if r["ex_date"] == "2027-01-06"]
    assert nxt and nxt[0]["amount"] is None


def test_a_payer_with_only_a_past_date_is_ok_and_a_bare_past_date_is_none():
    fund = {"divExDate": "2026-09-10", "divPayAmount": 1.4, "divFreq": 4}
    rows, status = dividends.parse_fundamental("JPM", fund, today="2026-09-26")
    assert status == "ok"
    assert all(r["ex_date"] < "2026-09-26" for r in rows)
    assert dividends.parse_fundamental("JPM", {"divExDate": "2026-09-10"},
                                       today="2026-09-26") == ([], "none")
    zero = {"divExDate": "2026-09-10", "divPayAmount": 0.0, "divAmount": 0.0}
    assert dividends.parse_fundamental("JPM", zero, today="2026-09-26") == ([], "none")


def test_a_future_ex_date_with_zero_amounts_is_ok_with_amount_none():
    fund = {"divExDate": "2026-10-06", "divPayAmount": 0.0, "divAmount": 0.0, "divFreq": 4}
    rows, status = dividends.parse_fundamental("JPM", fund, today="2026-09-26")
    assert status == "ok"
    assert [(r["ex_date"], r["amount"]) for r in rows] == [("2026-10-06", None)]


@pytest.mark.parametrize("value, expected", [
    (1791244800, "2026-10-06"),          # epoch SECONDS
    (1791244800000, "2026-10-06"),       # epoch milliseconds
    (1791244800.0, "2026-10-06"),
    (86400, None),                       # 1970 - before 2000
    (946684799, None),                   # 1999-12-31T23:59:59Z
    (4133980800, None),                  # 2101-01-01 in seconds
    (4133980800000, None),               # 2101-01-01 in ms
    ("1999-12-31", None),
    ("2101-01-01", None),
    ("2000-01-01", "2000-01-01"),
    ("2100-12-31", "2100-12-31"),
])
def test_epoch_units_and_the_plausible_date_range(value, expected):
    assert dividends._date(value) == expected


@pytest.mark.parametrize("bad", ["2026-W40-1", "20261006", "2026-10-061", "2026-1O-06"])
def test_strings_parse_through_the_stores_strict_parser(bad):
    assert dividends._date(bad) is None
    assert store.iso_date(bad) is None


def test_the_store_parser_is_public_and_the_old_name_is_an_alias():
    assert store.iso_date is store._iso_date


@pytest.mark.parametrize("bad", ["junk", "2026-W40-1", "", True, 20260926])
def test_an_unusable_today_argument_raises(tmp_path, bad):
    with pytest.raises(ValueError):
        dividends.refresh(fetch=lambda s: None, symbols=["JPM"], db_path=tmp_path / "d.db",
                          today=bad)
    conn = store.init_db(tmp_path / "d.db")
    try:
        assert store.last_run_day(conn) is None
    finally:
        store.close_db(conn)


def test_a_date_object_is_a_usable_today(tmp_path):
    import datetime as dt
    fetch = lambda sym: {sym: {"fundamental": QUOTE_FUND}}  # noqa: E731
    assert dividends.refresh(fetch=fetch, symbols=["JPM"], db_path=tmp_path / "d.db",
                             today=dt.date(2026, 9, 26)) == 1


@pytest.mark.parametrize("step", ["set_coverage", "prune"])
def test_last_run_day_is_recorded_even_when_a_post_loop_step_raises(tmp_path, monkeypatch, step):
    """A DB-lock error after the loop must not make every tick refetch."""
    def boom(*a, **k):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(store, step, boom)
    calls = []
    fetch = lambda sym: calls.append(sym) or {sym: {"fundamental": QUOTE_FUND}}  # noqa: E731
    db = tmp_path / "d.db"
    with pytest.raises(RuntimeError):
        dividends.refresh(fetch=fetch, symbols=["JPM"], db_path=db, today="2026-09-26")
    conn = store.init_db(db)
    try:
        assert store.last_run_day(conn) == "2026-09-26"
    finally:
        store.close_db(conn)
    assert dividends.refresh(fetch=fetch, symbols=["JPM"], db_path=db, today="2026-09-26") == 0
    assert calls == ["JPM"]


def test_force_bypasses_the_once_a_day_guard(tmp_path):
    calls = []
    fetch = lambda sym: calls.append(sym) or {sym: {"fundamental": QUOTE_FUND}}  # noqa: E731
    db = tmp_path / "d.db"
    assert dividends.refresh(fetch=fetch, symbols=["JPM"], db_path=db, today="2026-09-26") == 1
    assert dividends.refresh(fetch=fetch, symbols=["JPM"], db_path=db, today="2026-09-26") == 0
    assert dividends.refresh(fetch=fetch, symbols=["JPM"], db_path=db, today="2026-09-26",
                             force=True) == 1
    assert calls == ["JPM", "JPM"]


def test_a_proxy_outage_still_records_the_day(tmp_path):
    """No same-day retry: the budget is one call per symbol per day."""
    db = tmp_path / "d.db"
    dividends.refresh(fetch=lambda sym: None, symbols=["JPM"], db_path=db, today="2026-09-26")
    conn = store.init_db(db)
    try:
        assert store.last_run_day(conn) == "2026-09-26"
    finally:
        store.close_db(conn)


def test_a_non_string_ticker_is_skipped_not_an_attribute_error(tmp_path):
    calls = []
    fetch = lambda sym: calls.append(sym) or {sym: {"fundamental": QUOTE_FUND}}  # noqa: E731
    n = dividends.refresh(fetch=fetch, symbols=[None, 42, ["JPM"], True, " jpm "],
                          db_path=tmp_path / "d.db", today="2026-09-26")
    assert (n, calls) == (1, ["JPM"])
