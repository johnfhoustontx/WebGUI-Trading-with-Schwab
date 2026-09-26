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
    rows, status = dividends.parse_fundamental("JPM", QUOTE_FUND)
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
    rows, status = dividends.parse_fundamental("JPM", fund)
    assert status == "ok"
    assert rows[0]["amount"] is None
    fund = {"divExDate": "2026-10-06T00:00:00Z"}
    assert dividends.parse_fundamental("JPM", fund)[0][0]["amount"] is None


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
