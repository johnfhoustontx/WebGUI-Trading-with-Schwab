"""The /ES and /NQ feed's seams in the engine and the store, and the promise that
leaves every existing symbol alone.

A futures option's session runs from the evening before to mid-afternoon, so the
engine's clock formula (hours to 15:00 CT, clamped at zero) is wrong for it by
design: the feed passes the time left itself. Its rows also span two calendar
dates, which the retention, the archive and the session loaders must tolerate
without costing a stock or index symbol a kept session.
Design: docs/plans/2026-10-31-futures-0dte-chain-feed-design.md
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import gex_archive as ga
import gex_collector as gc
import gex_history_db as gh
import pytest
from gamma_tool import GammaEngine
from shared.raw_minute import RawMinute

TZ = ZoneInfo("America/Chicago")


def ct(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def _stamp(at):
    return int(at.timestamp())


@pytest.fixture
def conn(tmp_path):
    handle = gh.sqlite3.connect(tmp_path / "seams.db")
    gh.init_schema(handle)
    yield handle
    handle.close()


def insert(conn, symbol, view, ts, dte=None):
    gh.insert_snapshot(conn, symbol, view, {"ts": _stamp(ts), "spot": 5000.0},
                       {5000.0: {"call": 1.0, "put": -1.0, "net": 0.0}}, dte)
    conn.commit()


def count(conn, symbol):
    return conn.execute("SELECT COUNT(*) FROM snapshots WHERE symbol = ?",
                        (symbol,)).fetchone()[0]


def chain_fixture(key="2026-11-02:0", symbol="SPY"):
    def leg(iv, oi, delta, gamma):
        return [{"volatility": iv, "openInterest": oi, "delta": delta,
                 "gamma": gamma, "daysToExpiration": 0}]
    return {
        "symbol": symbol, "underlyingPrice": 500.0,
        "callExpDateMap": {key: {"495.0": leg(22.0, 1000, 0.7, 0.05),
                                 "500.0": leg(20.0, 2000, 0.52, 0.06)}},
        "putExpDateMap": {key: {"495.0": leg(23.0, 1100, -0.30, 0.05),
                                "500.0": leg(21.0, 2200, -0.48, 0.06)}},
    }


def span_fixture():
    return gh.SessionSpan(_stamp(ct(2026, 11, 1, 17, 0)), _stamp(ct(2026, 11, 2, 15, 0)))


def spy_on_calc_all(monkeypatch):
    """Record the ``hours_left`` each symbol's chain was priced with, keyed by the
    chain's own symbol; the real engine still runs."""
    seen = {}
    real = GammaEngine.calc_all_from_chain

    def spy(self, chain, *args, **kwargs):
        seen[chain["symbol"]] = kwargs.get("hours_left")
        return real(self, chain, *args, **kwargs)

    monkeypatch.setattr(GammaEngine, "calc_all_from_chain", spy)
    return seen


def archived_symbols_after_running_the_archive(conn, tmp_path):
    archive = ga.connect_archive(tmp_path / "archive.db")
    ga.archive_new_sessions(conn, archive, ct(2026, 11, 2, 16, 0))
    return {r[0] for r in archive.execute("SELECT DISTINCT symbol FROM snapshots")}


# ── the engine ───────────────────────────────────────────────────────────────

def test_hours_left_overrides_the_clock_formula():
    # 18:00 the evening before: the existing formula clamps to 0 hours.
    eng = GammaEngine()
    now = ct(2026, 11, 1, 18, 0)
    a = eng.calc_all_from_chain(chain_fixture(key="2026-11-02:0"), now=now, hours_left=21.0)
    # A different wall instant, the same time left: charm depends only on that.
    b = eng.calc_all_from_chain(chain_fixture(key="2026-11-02:0"),
                                now=now + timedelta(hours=2), hours_left=21.0)
    assert a[1]["gex"] == b[1]["gex"]
    assert a[3]["gex"] == b[3]["gex"]
    clamped = eng.calc_all_from_chain(chain_fixture(key="2026-11-02:0"), now=now)
    assert a[1]["gex"] != clamped[1]["gex"]


def test_more_time_left_moves_charm():
    eng = GammaEngine()
    now = ct(2026, 11, 1, 18, 0)
    short = eng.calc_all_from_chain(chain_fixture(), now=now, hours_left=3.0)
    long_ = eng.calc_all_from_chain(chain_fixture(), now=now, hours_left=21.0)
    assert short[1]["gex"] != long_[1]["gex"]


def test_the_time_left_floors_where_the_clock_formula_does():
    eng = GammaEngine()
    now = ct(2026, 11, 2, 15, 30)
    floor = eng.calc_all_from_chain(chain_fixture(), now=now)
    assert eng.calc_all_from_chain(chain_fixture(), now=now, hours_left=0.0) == floor
    assert eng.calc_all_from_chain(chain_fixture(), now=now, hours_left=-4.0) == floor


def test_a_time_left_that_is_not_a_number_floors_rather_than_poisons():
    eng = GammaEngine()
    now = ct(2026, 11, 2, 15, 30)
    floor = eng.calc_all_from_chain(chain_fixture(), now=now)
    assert eng.calc_all_from_chain(chain_fixture(), now=now,
                                   hours_left=float("nan")) == floor


def test_no_hours_left_is_byte_identical():
    eng = GammaEngine()
    now = ct(2026, 11, 2, 10, 0)
    assert eng.calc_all_from_chain(chain_fixture(), now=now) == \
        eng.calc_all_from_chain(chain_fixture(), now=now, hours_left=None)


def test_no_hours_left_still_prices_from_the_clock():
    # The two numbers an existing symbol's rows move with: five hours to the close
    # at 10:00 CT, none at 16:00. A change to the clock path moves one of them.
    eng = GammaEngine()
    ten = eng.calc_all_from_chain(chain_fixture(), now=ct(2026, 11, 2, 10, 0))
    same = eng.calc_all_from_chain(chain_fixture(), now=ct(2026, 11, 2, 10, 0),
                                   hours_left=5.0)
    assert ten == same


# ── the minute ───────────────────────────────────────────────────────────────

def test_derive_minute_passes_a_symbols_hours_left_and_only_that_symbols(monkeypatch, conn):
    seen = spy_on_calc_all(monkeypatch)
    minute = RawMinute(ts=_stamp(ct(2026, 11, 2, 10, 0)),
                       chains=[("FOP.ES", chain_fixture(symbol="FOP.ES")),
                               ("SPY", chain_fixture(symbol="SPY"))],
                       hours_left={"FOP.ES": 21.0})
    gc.derive_minute(GammaEngine(), conn, minute)
    assert seen == {"FOP.ES": 21.0, "SPY": None}


def test_a_minute_with_no_hours_left_calls_the_engine_as_it_always_did():
    class Strict:
        _last_dte = 0

        def calc_all_from_chain(self, chain, use_volume=False, now=None):
            return None, None, None, None

    minute = RawMinute(ts=_stamp(ct(2026, 11, 2, 10, 0)),
                       chains=[("SPY", chain_fixture())])
    assert minute.hours_left == {}
    gc.derive_minute(Strict(), None, minute)


# ── the session loaders ──────────────────────────────────────────────────────

def test_session_span_reads_across_two_calendar_dates(conn):
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 11, 1, 18, 0))
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 11, 2, 9, 0))
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 11, 2, 16, 0))
    assert len(gh.load_date_with_grid(conn, "FOP.ES", "gex", date=span_fixture())) == 2


def test_a_span_is_half_open_like_a_day():
    start, end = gh._local_unix_range(span_fixture())
    assert (start, end) == (span_fixture().start_ts, span_fixture().end_ts)


@pytest.mark.parametrize("loader", [gh.load_flow_series, gh.load_hedge_series])
def test_the_other_session_loaders_take_a_span(conn, loader):
    gh.insert_snapshot(conn, "FOP.ES", "gex",
                       {"ts": _stamp(ct(2026, 11, 1, 18, 0)), "spot": 5000.0,
                        "hedge_pressure": 1.0}, {}, 0)
    gh.insert_snapshot(conn, "FOP.ES", "dex",
                       {"ts": _stamp(ct(2026, 11, 1, 18, 0)), "spot": 5000.0,
                        "hedge_pressure": 1.0}, {}, 0)
    conn.commit()
    assert loader(conn, "FOP.ES", span_fixture())


def test_session_dte_takes_a_span(conn):
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 11, 1, 18, 0), dte=0)
    assert gh.session_dte(conn, "FOP.ES", "gex", span_fixture()) == 0


def test_a_date_still_means_its_calendar_day():
    import datetime as dt
    start, end = gh._local_unix_range(dt.date(2026, 10, 28))
    assert end - start == 86400
    assert datetime.fromtimestamp(start).date() == dt.date(2026, 10, 28)


# ── retention and the archive ────────────────────────────────────────────────

def test_a_sunday_of_feed_rows_costs_no_symbol_a_kept_session(conn):
    for day in (26, 27, 28, 29, 30):
        insert(conn, "$SPX", "gex", ts=ct(2026, 10, day, 10, 0))
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 11, 1, 18, 0))
    gh.purge_keep_sessions(conn, keep_sessions=5)
    assert count(conn, "$SPX") == 5 and count(conn, "FOP.ES") == 1


def test_a_feed_row_older_than_the_cutoff_is_still_purged(conn):
    for day in (26, 27, 28, 29, 30):
        insert(conn, "$SPX", "gex", ts=ct(2026, 10, day, 10, 0))
    insert(conn, "$SPX", "gex", ts=ct(2026, 10, 23, 10, 0))
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 10, 23, 18, 0))
    gh.purge_keep_sessions(conn, keep_sessions=5)
    assert count(conn, "$SPX") == 5 and count(conn, "FOP.ES") == 0


def test_a_symbol_that_only_resembles_the_feed_still_counts_as_a_session(conn):
    # "FOP" alone, or a ticker that merely begins with the letters, is not the feed.
    for day, symbol in ((26, "$SPX"), (27, "$SPX"), (28, "FOPX"), (29, "FOP"), (30, "$SPX")):
        insert(conn, symbol, "gex", ts=ct(2026, 10, day, 10, 0))
    assert gh._distinct_session_dates(conn) == [
        "2026-10-30", "2026-10-29", "2026-10-28", "2026-10-27", "2026-10-26"]


def test_the_archive_holds_no_feed_symbols_and_no_feed_only_date(conn, tmp_path):
    insert(conn, "$SPX", "gex", ts=ct(2026, 10, 30, 10, 0))
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 10, 30, 18, 0))
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 11, 1, 18, 0))
    assert ga.closed_sessions(conn, ct(2026, 11, 2, 16, 0)) == ["2026-10-30"]
    assert archived_symbols_after_running_the_archive(conn, tmp_path) == {"$SPX"}
