"""poll_once == derive_minute(fetch_minute(...)), and the RawMinute carries
everything derive needs (carried chains included).

The raw feed (docs/plans/2026-10-14-raw-feed-and-catch-up-design.md) stores what
fetch_minute returns and derives it later, so anything derive reads that is not
in the RawMinute would make a replayed minute differ from a live one."""
import datetime as dt
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import gex_collector as gc
from shared.raw_minute import RawMinute
from tests.test_gex_collector_tiers import TIERS, _client, _not_due

CT = ZoneInfo("America/Chicago")
RTH = dt.datetime(2026, 10, 5, 10, 0, tzinfo=CT)


class _Engine:
    """Records what the engine was asked to price, and returns nothing to write."""

    def __init__(self):
        self._last_dte = 0
        self.seen = []

    def calc_all_from_chain(self, chain, use_volume=False, now=None):
        self.seen.append((chain["symbol"], chain.get("underlyingPrice")))
        return (None, None, None, None)


def test_fetch_minute_returns_a_raw_minute_with_the_snapped_ts():
    m = gc.fetch_minute(_client(), symbols=["SPY", "QQQ"],
                        now=RTH + dt.timedelta(seconds=37), poll_term=False)
    assert isinstance(m, RawMinute)
    assert m.ts == int(RTH.timestamp())
    assert [s for s, _ in m.chains] == ["SPY", "QQQ"]
    assert m.term is None and m.carried == set()
    assert m.symbols == ["SPY", "QQQ"]


def test_poll_once_is_fetch_then_derive():
    a, b = _Engine(), _Engine()
    gc.poll_once(_client(), a, MagicMock(), symbols=["SPY", "QQQ"], poll_term=False, now=RTH)
    m = gc.fetch_minute(_client(), symbols=["SPY", "QQQ"], now=RTH, poll_term=False)
    gc.derive_minute(b, MagicMock(), m)
    assert a.seen == b.seen == [("SPY", 100.0), ("QQQ", 100.0)]


def test_a_carried_symbol_is_marked_and_not_handed_to_on_chain():
    now = _not_due("AAPL")
    m = gc.fetch_minute(_client(ages={"AAPL": 90.0}), symbols=["SPY", "AAPL"], now=now,
                        tiers=TIERS, poll_term=False)
    assert "AAPL" in m.carried and m.carried_age["AAPL"] == 90.0
    handed = []
    gc.derive_minute(_Engine(), MagicMock(), m, on_chain=lambda s, c: handed.append(s))
    assert handed == ["SPY"]


def test_the_term_chain_rides_the_raw_minute_when_polled():
    m = gc.fetch_minute(_client(), symbols=["SPY"], now=RTH, poll_term=True)
    assert m.term is not None and m.term["symbol"] == "$SPX"


def test_derive_minute_does_not_commit():
    conn = MagicMock()
    m = gc.fetch_minute(_client(), symbols=["SPY"], now=RTH, poll_term=False)
    gc.derive_minute(_Engine(), conn, m)
    conn.commit.assert_not_called()


def test_on_minute_sees_the_fetched_minute_before_it_is_derived():
    order = []
    engine = _Engine()
    real_calc = engine.calc_all_from_chain

    def _calc(chain, use_volume=False, now=None):
        order.append("derive")
        return real_calc(chain, use_volume)

    engine.calc_all_from_chain = _calc
    got = []
    gc.poll_once(_client(), engine, MagicMock(), symbols=["SPY"], poll_term=False, now=RTH,
                 on_minute=lambda m: (order.append("minute"), got.append(m)))
    assert order == ["minute", "derive"]
    assert got[0].ts == int(RTH.timestamp()) and [s for s, _ in got[0].chains] == ["SPY"]


def test_a_raising_on_minute_never_stops_the_poll():
    engine = _Engine()

    def _boom(minute):
        raise OSError("disk full")

    gc.poll_once(_client(), engine, MagicMock(), symbols=["SPY"], poll_term=False, now=RTH,
                 on_minute=_boom)
    assert engine.seen == [("SPY", 100.0)]
