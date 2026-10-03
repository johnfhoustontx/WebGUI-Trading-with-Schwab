"""The trade tracker must not ask Schwab, or log, about a trade it cannot follow
every 30 seconds for as long as the trade stays open.

Found on prod 2026-10-03. The paper ledger held two open DEBIT trades (a
LONG_PUT on SOFI and a CONDOR_CALL on CRWD; strikes in ``legs``, the four strike
columns NULL). The tracker follows credit spreads only, but it found that out
AFTER fetching the trade's option chain, and the reconcile loop kept no memory
of the failure. So each of those trades cost one Schwab chain call and one ERROR
line every 30 seconds: measured at 4.0 calls a minute for the two of them, and
48,462 ERROR lines in the journal since 2026-09-15.
"""
import datetime as dt
import logging
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import schwab_proxy  # noqa: E402
import trade_registry as tr  # noqa: E402

CHAIN = {
    "putExpDateMap": {"x:0": {
        "100.0": [{"symbol": "AAA   P00100000"}], "95.0": [{"symbol": "AAA   P00095000"}]}},
    "callExpDateMap": {"x:0": {
        "110.0": [{"symbol": "AAA   C00110000"}], "115.0": [{"symbol": "AAA   C00115000"}]}},
}
OK = {"status_code": 200, "data": CHAIN, "error": None}
EXPIRY = (dt.datetime.now(schwab_proxy.CENTRAL_TZ).date() + dt.timedelta(days=6)).isoformat()


def row(trade_id, strategy, **over):
    """An OPEN ``trades`` row as ``_read_open_trades`` returns it."""
    base = {"trade_id": trade_id, "symbol": "AAA", "strategy": strategy,
            "expiration": EXPIRY, "quantity": 1, "entry_credit": 1.20,
            "short_strike": 100.0, "long_strike": 95.0,
            "call_short": None, "call_long": None}
    base.update(over)
    return base


# The two rows exactly as they sat in prod's trades.db.
PROD_LONG_PUT = row("64a7869c", "LONG_PUT", symbol="SOFI", quantity=10,
                    entry_credit=-0.48, short_strike=None, long_strike=None)
PROD_CONDOR = row("db660aad", "CONDOR_CALL", symbol="CRWD", entry_credit=-5.27,
                  short_strike=None, long_strike=None)


class Token:
    """Stands in for the token manager: counts chain calls, answers as told."""

    def __init__(self):
        self.calls = []
        self.reply = OK
        self.raises = None            # callable(n) -> the exception to raise

    def api_request(self, endpoint, params=None):
        self.calls.append((endpoint, dict(params or {})))
        if self.raises is not None:
            raise self.raises(len(self.calls))
        return self.reply(len(self.calls)) if callable(self.reply) else self.reply


# The fixture below replaces it; two tests need the real one.
REAL_RETRY_CAPS = schwab_proxy._tracker_retry_caps


@pytest.fixture
def proxy(monkeypatch):
    token = Token()
    monkeypatch.setattr(schwab_proxy, "token_mgr", token)
    monkeypatch.setattr(schwab_proxy, "_registry", tr.TradeRegistry())
    monkeypatch.setattr(schwab_proxy, "_track_attempts", tr.TrackAttempts(base_sec=30))
    monkeypatch.setattr(schwab_proxy, "_last_tracker_counts", None)
    monkeypatch.setattr(schwab_proxy, "_tracker_retry_caps", lambda: (1800, 300))
    monkeypatch.setattr(schwab_proxy, "_subscribe", lambda osis: None)
    monkeypatch.setattr(schwab_proxy, "_unsubscribe", lambda osis: None)
    monkeypatch.setattr(schwab_proxy.perf_writer, "load_fired", lambda trade_id: set())
    return token


def said(caplog, level=logging.INFO, about="track "):
    """The tracker's log lines at ``level`` or above that start with ``about``:
    ``"track "`` for one trade's outcome, ``"reconcile"`` for the loop's own."""
    return [r for r in caplog.records
            if r.name == schwab_proxy.logger.name and r.levelno >= level
            and r.getMessage().startswith(about)]


def cycles(open_trades, n, step=30.0, start=0.0):
    for i in range(n):
        schwab_proxy._reconcile_once(open_trades, now=start + i * step)


def attempt_times(proxy, open_trades, seconds):
    """The seconds at which a reconcile actually called Schwab."""
    tried = []
    for second in range(0, seconds, 30):
        before = len(proxy.calls)
        schwab_proxy._reconcile_once(open_trades, now=float(second))
        if len(proxy.calls) > before:
            tried.append(second)
    return tried


def debug(caplog):
    return caplog.at_level(logging.DEBUG, logger=schwab_proxy.logger.name)


# ---- the prod case ---------------------------------------------------------

def test_the_two_prod_trades_cost_no_schwab_call_and_no_error(proxy, caplog):
    open_trades = {r["trade_id"]: r for r in (PROD_LONG_PUT, PROD_CONDOR)}
    with debug(caplog):
        cycles(open_trades, 20)                       # ten minutes of reconciles
    assert proxy.calls == []
    assert said(caplog, logging.ERROR, about="") == []
    lines = [r.getMessage() for r in said(caplog)]
    assert len(lines) == 2                            # once per trade, not per cycle
    assert any("64a7869c" in m and "LONG_PUT" in m for m in lines)
    assert any("db660aad" in m and "CONDOR_CALL" in m for m in lines)


def test_track_refuses_an_unfollowed_structure_before_fetching_a_chain(proxy):
    res = schwab_proxy._track(dict(PROD_LONG_PUT))
    assert res["status"] == "skipped" and "LONG_PUT" in res["detail"]
    assert proxy.calls == []
    assert PROD_LONG_PUT["trade_id"] not in schwab_proxy._registry


def test_a_credit_spread_is_still_tracked_on_the_first_cycle(proxy):
    cycles({"t1": row("t1", "PCS")}, 3)
    assert len(proxy.calls) == 1                      # tracked once, then left alone
    state = schwab_proxy._registry.get("t1")
    assert state["legs"] == {"put_short": "AAA   P00100000", "put_long": "AAA   P00095000"}
    assert (state["target_mid"], state["stop_mid"]) == (0.60, 3.60)   # as before the fix


def test_an_iron_condor_under_the_finders_name_is_tracked_as_one(proxy):
    cycles({"t1": row("t1", "IRON_CONDOR", call_short=110.0, call_long=115.0)}, 1)
    state = schwab_proxy._registry.get("t1")
    assert state["strategy"] == "IC"                  # the name the detector reads
    assert set(state["legs"]) == {"put_short", "put_long", "call_short", "call_long"}


@pytest.mark.parametrize("over", [
    {"short_strike": None}, {"entry_credit": None}, {"entry_credit": 0},
    {"symbol": None}, {"expiration": None}, {"expiration": ""}])
def test_a_spread_missing_something_it_needs_costs_no_call(proxy, caplog, over):
    with debug(caplog):
        cycles({"t1": row("t1", "PCS", **over)}, 20)
    assert proxy.calls == [] and said(caplog, logging.ERROR, about="") == []
    assert len(said(caplog)) == 1 and "t1" not in schwab_proxy._registry


def test_an_expired_open_row_is_mentioned_once(proxy, caplog):
    past = (dt.datetime.now(schwab_proxy.CENTRAL_TZ).date() - dt.timedelta(days=1)).isoformat()
    with debug(caplog):
        cycles({"t1": row("t1", "PCS", expiration=past)}, 5)
    assert proxy.calls == [] and len(said(caplog)) == 1
    assert past in said(caplog)[0].getMessage()


# ---- a failure that might clear: retry, but back off -----------------------

def test_when_schwab_does_not_send_the_chain_the_gap_grows_to_five_minutes(proxy):
    proxy.reply = {"status_code": 502, "data": None, "error": "bad gateway"}
    tried = attempt_times(proxy, {"t1": row("t1", "PCS")}, 7200)
    gaps = [b - a for a, b in zip(tried, tried[1:])]
    assert tried[0] == 0
    assert gaps[:4] == [30, 60, 120, 240]
    assert set(gaps[4:]) == {300}                     # short cap: resume soon after
    assert len(tried) < 30                            # was 240 in two hours


def test_a_strike_that_is_not_in_the_chain_backs_off_to_half_an_hour(proxy):
    tried = attempt_times(proxy, {"t1": row("t1", "PCS", short_strike=101.0)}, 14400)
    gaps = [b - a for a, b in zip(tried, tried[1:])]
    assert gaps[:6] == [30, 60, 120, 240, 480, 960]
    assert set(gaps[6:]) == {1800}


def test_tracking_resumes_within_five_minutes_of_schwab_recovering(proxy):
    proxy.reply = {"status_code": 502, "data": None, "error": "bad gateway"}
    open_trades = {"t1": row("t1", "PCS")}
    attempt_times(proxy, open_trades, 3600)           # an hour-long outage
    proxy.reply = OK
    for second in range(3600, 3600 + 330, 30):
        schwab_proxy._reconcile_once(open_trades, now=float(second))
    assert "t1" in schwab_proxy._registry


def test_a_dead_token_is_retried_on_the_short_limit_and_reported_once(proxy, caplog):
    # The weekly re-authorization: api_request RAISES (it never gets as far as a
    # status code), with text that differs between attempts. That is Schwab not
    # sending the chain, so tracking must resume soon after the token is back.
    proxy.raises = lambda n: RuntimeError(f"Token refresh failed: attempt {n}")
    open_trades = {"t1": row("t1", "PCS")}
    with debug(caplog):
        tried = attempt_times(proxy, open_trades, 3600)           # an hour without a token
        gaps = [b - a for a, b in zip(tried, tried[1:])]
        assert gaps[:4] == [30, 60, 120, 240] and set(gaps[4:]) == {300}
        errors = said(caplog, logging.ERROR, about="")
        assert len(errors) == 1 and not errors[0].exc_info       # a known condition: no traceback
        assert "Token refresh failed" in errors[0].getMessage()
        proxy.raises = None
        for second in range(3600, 3600 + 330, 30):
            schwab_proxy._reconcile_once(open_trades, now=float(second))
    assert "t1" in schwab_proxy._registry


def test_a_repeated_failure_is_an_error_once_then_quiet(proxy, caplog):
    proxy.reply = {"status_code": 502, "data": None, "error": "bad gateway"}
    with debug(caplog):
        cycles({"t1": row("t1", "PCS")}, 40)
    assert len(proxy.calls) > 2                       # it did retry
    assert len(said(caplog, logging.ERROR)) == 1


def test_an_error_body_that_differs_every_time_is_still_reported_once(proxy, caplog):
    # Schwab's error body carries a per-request id; the text is never the same.
    proxy.reply = lambda n: {"status_code": 502, "data": None,
                             "error": f'{{"errors":[{{"id":"req-{n}"}}]}}'}
    with debug(caplog):
        cycles({"t1": row("t1", "PCS")}, 40)
    assert len(proxy.calls) > 2
    errors = said(caplog, logging.ERROR)
    assert len(errors) == 1 and "req-1" in errors[0].getMessage()


def test_a_different_failure_is_an_error_again(proxy, caplog):
    proxy.reply = {"status_code": 502, "data": None, "error": "bad gateway"}
    open_trades = {"t1": row("t1", "PCS", short_strike=101.0)}   # not in the chain
    with debug(caplog):
        schwab_proxy._reconcile_once(open_trades, now=0.0)
        proxy.reply = OK
        schwab_proxy._reconcile_once(open_trades, now=30.0)
        proxy.reply = {"status_code": 429, "data": None, "error": "slow down"}
        schwab_proxy._reconcile_once(open_trades, now=90.0)
    errors = [r.getMessage() for r in said(caplog, logging.ERROR)]
    assert len(errors) == 3
    assert "502" in errors[0] and "101" in errors[1] and "429" in errors[2]


def test_a_trade_that_starts_working_is_tracked_and_forgotten(proxy):
    proxy.reply = {"status_code": 502, "data": None, "error": "bad gateway"}
    open_trades = {"t1": row("t1", "PCS")}
    schwab_proxy._reconcile_once(open_trades, now=0.0)
    proxy.reply = OK
    schwab_proxy._reconcile_once(open_trades, now=30.0)
    assert "t1" in schwab_proxy._registry
    assert schwab_proxy._track_attempts.last_key("t1") is None


def test_a_row_that_breaks_the_reconcile_itself_is_backed_off_too(proxy, caplog):
    open_trades = {"t1": "not a row"}                 # dict() of it raises
    with debug(caplog):
        cycles(open_trades, 20)
    errors = said(caplog, logging.ERROR, about="reconcile: track t1")
    assert len(errors) == 1 and errors[0].exc_info                # with its traceback
    assert proxy.calls == [] and "t1" not in schwab_proxy._registry


def test_an_unexpected_fault_inside_track_is_reported_once_and_backed_off(
        proxy, caplog, monkeypatch):
    seen = iter(range(100))

    def boom(*a, **k):
        raise RuntimeError(f"registry is broken #{next(seen)}")   # text differs each time

    monkeypatch.setattr(schwab_proxy._registry, "add", boom)
    with debug(caplog):
        cycles({"t1": row("t1", "PCS")}, 20)
    errors = said(caplog, logging.ERROR)
    assert len(errors) == 1 and errors[0].exc_info                # with its traceback
    assert 2 <= len(proxy.calls) <= 6                 # retried, with a growing gap


def unreadable(name):
    raise RuntimeError("settings unreadable")


@pytest.mark.parametrize("section", [
    unreadable,
    lambda name: {"retry_max_sec": 1800, "fetch_retry_max_sec": 0},   # Settings allowed 0
    lambda name: {"retry_max_sec": 1800},
    lambda name: None])
def test_an_unusable_fetch_limit_still_means_five_minutes(proxy, monkeypatch, section):
    # Each limit falls back to ITS OWN built-in: a bad fetch limit must not turn
    # a five-minute recovery into a half-hour one.
    monkeypatch.setattr(schwab_proxy, "_tracker_retry_caps", REAL_RETRY_CAPS)
    monkeypatch.setattr(schwab_proxy._marketdata_config, "section", section)
    proxy.reply = {"status_code": 502, "data": None, "error": "bad gateway"}
    tried = attempt_times(proxy, {"t1": row("t1", "PCS")}, 7200)
    gaps = [b - a for a, b in zip(tried, tried[1:])]
    assert gaps[:4] == [30, 60, 120, 240] and set(gaps[4:]) == {300}


def test_the_limits_come_from_the_settings_file(monkeypatch):
    monkeypatch.setattr(schwab_proxy._marketdata_config, "section",
                        lambda name: {"retry_max_sec": 900, "fetch_retry_max_sec": 120})
    assert schwab_proxy._tracker_retry_caps() == (900, 120)
    monkeypatch.setattr(schwab_proxy._marketdata_config, "section", unreadable)
    assert schwab_proxy._tracker_retry_caps() == (1800, 300)


@pytest.mark.parametrize("bad", [None, 0, -5, float("nan"), float("inf"), True, "300"])
def test_each_unusable_limit_is_replaced_by_its_own_built_in(monkeypatch, bad):
    monkeypatch.setattr(schwab_proxy._marketdata_config, "section",
                        lambda name: {"retry_max_sec": bad, "fetch_retry_max_sec": 120})
    assert schwab_proxy._tracker_retry_caps() == (1800, 120)
    monkeypatch.setattr(schwab_proxy._marketdata_config, "section",
                        lambda name: {"retry_max_sec": 900, "fetch_retry_max_sec": bad})
    assert schwab_proxy._tracker_retry_caps() == (900, 300)


# ---- bookkeeping ------------------------------------------------------------

def test_a_closed_trade_is_forgotten(proxy):
    schwab_proxy._reconcile_once({"t1": dict(PROD_LONG_PUT, trade_id="t1")}, now=0.0)
    assert schwab_proxy._track_attempts.last_key("t1") is not None
    schwab_proxy._reconcile_once({}, now=30.0)
    assert schwab_proxy._track_attempts.last_key("t1") is None


def test_a_trade_the_rest_call_started_tracking_is_forgotten(proxy):
    proxy.reply = {"status_code": 502, "data": None, "error": "bad gateway"}
    open_trades = {"t1": row("t1", "PCS")}
    cycles(open_trades, 4)                            # backed off
    proxy.reply = OK
    assert schwab_proxy.track(dict(open_trades["t1"]))["status"] == "ok"
    schwab_proxy._reconcile_once(open_trades, now=200.0)
    assert schwab_proxy._track_attempts.last_key("t1") is None


def test_a_tracked_trade_is_untracked_when_it_closes(proxy):
    schwab_proxy._reconcile_once({"t1": row("t1", "PCS")}, now=0.0)
    assert "t1" in schwab_proxy._registry
    added, removed = schwab_proxy._reconcile_once({}, now=30.0)
    assert (added, removed) == (0, 1) and "t1" not in schwab_proxy._registry


def test_one_bad_trade_does_not_hold_back_a_good_one(proxy):
    open_trades = {"bad": dict(PROD_CONDOR, trade_id="bad"), "good": row("good", "CCS",
                   short_strike=110.0, long_strike=115.0)}
    added, _removed = schwab_proxy._reconcile_once(open_trades, now=0.0)
    assert added == 1 and "good" in schwab_proxy._registry
    assert len(proxy.calls) == 1


def test_the_rest_endpoint_answers_skipped_and_says_so_once(proxy, caplog):
    with debug(caplog):
        res = schwab_proxy.track(dict(PROD_CONDOR))
    assert res["status"] == "skipped" and proxy.calls == []
    assert set(res) == {"status", "detail"}           # no internal fields leak
    assert len(said(caplog)) == 1 and said(caplog, logging.ERROR) == []


# ---- the standing signal ----------------------------------------------------

def test_the_summary_line_is_loud_only_when_the_counts_change(proxy, caplog):
    open_trades = {"t1": dict(PROD_LONG_PUT, trade_id="t1")}
    with debug(caplog):
        cycles(open_trades, 10)
        lines = [r.getMessage() for r in said(caplog, about="reconcile:")]
        assert len(lines) == 1 and "not_followed=1 failing=0" in lines[0]
        schwab_proxy._reconcile_once({}, now=600.0)   # it closed
    lines = [r.getMessage() for r in said(caplog, about="reconcile:")]
    assert len(lines) == 2 and "not_followed=0 failing=0" in lines[1]


def test_stats_report_what_the_tracker_is_refusing_and_retrying(proxy):
    proxy.reply = {"status_code": 502, "data": None, "error": "bad gateway"}
    schwab_proxy._reconcile_once({"a": dict(PROD_LONG_PUT, trade_id="a"),
                                  "b": row("b", "PCS")}, now=0.0)
    assert schwab_proxy.api_call_stats()["tracker"] == {
        "tracked": 0, "not_followed": 1, "failing": 1}


# ---- through the real ledger table -----------------------------------------

def test_rows_read_from_the_ledger_table_are_handled_end_to_end(proxy, tmp_path, monkeypatch):
    """The reconcile reads REAL columns out of SQLite: a credit spread comes back
    as floats and is tracked; a debit trade comes back with NULL strikes and a
    negative credit and is refused without a call."""
    db = tmp_path / "trades.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE trades (trade_id TEXT PRIMARY KEY, status TEXT, symbol TEXT, "
        "strategy TEXT, expiration TEXT, quantity INTEGER, entry_credit REAL, "
        "short_strike REAL, long_strike REAL, call_short REAL, call_long REAL)")
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        ("credit", "OPEN", "AAA", "PCS", EXPIRY, 2, 1.2, 100, 95, None, None),
        ("debit", "OPEN", "SOFI", "LONG_PUT", EXPIRY, 10, -0.48, None, None, None, None),
        ("closed", "CLOSED", "AAA", "PCS", EXPIRY, 1, 1.2, 100, 95, None, None)])
    conn.commit()
    conn.close()
    monkeypatch.setattr(schwab_proxy, "OPTIONSCANNER_TRADES_DB", db)

    open_trades = schwab_proxy._read_open_trades()
    assert set(open_trades) == {"credit", "debit"}
    added, _ = schwab_proxy._reconcile_once(open_trades, now=0.0)
    assert added == 1 and "credit" in schwab_proxy._registry
    assert [c[1]["symbol"] for c in proxy.calls] == ["AAA"]      # none for the debit trade
    assert schwab_proxy._track_attempts.counts() == {"not_followed": 1, "failing": 0}
