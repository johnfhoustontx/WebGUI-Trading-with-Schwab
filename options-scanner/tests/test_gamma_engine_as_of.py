"""A collected minute's Greeks depend on that minute, not on when they are computed.

``calc_all_from_chain`` prices charm, delta and vanna with the time left to the
close and picks the nearest expiration by today's date. Read from the wall
clock, a minute derived later -- the raw feed's shadow check, or catch-up after
a restart (docs/plans/2026-10-14-raw-feed-and-catch-up-design.md) -- would come
out different from the same minute derived live."""
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import gamma_tool
import gex_collector as gc
import gex_history_db as db
from gamma_tool import GammaEngine
from shared.raw_minute import RawMinute

TZ = ZoneInfo("America/Chicago")
AS_OF = datetime(2026, 10, 14, 10, 0, tzinfo=TZ)
EXP = "2026-10-16:2"


def _chain():
    def leg(iv, oi, delta, gamma):
        return [{"volatility": iv, "openInterest": oi, "delta": delta,
                 "gamma": gamma, "daysToExpiration": 2}]
    return {
        "symbol": "SPY", "underlyingPrice": 500.0,
        "callExpDateMap": {EXP: {"495.0": leg(22.0, 1000, 0.7, 0.05),
                                 "500.0": leg(20.0, 2000, 0.52, 0.06)}},
        "putExpDateMap": {EXP: {"495.0": leg(23.0, 1100, -0.30, 0.05),
                                "500.0": leg(21.0, 2200, -0.48, 0.06)}},
    }


def _wall_clock(monkeypatch, at):
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at if tz is None else at.astimezone(tz)

    monkeypatch.setattr(gamma_tool, "datetime", _Clock)


def test_the_as_of_time_decides_the_greeks_not_the_wall_clock(monkeypatch):
    _wall_clock(monkeypatch, datetime(2026, 10, 14, 14, 0, tzinfo=TZ))
    first = GammaEngine().calc_all_from_chain(_chain(), now=AS_OF)
    _wall_clock(monkeypatch, datetime(2026, 10, 14, 11, 30, tzinfo=TZ))
    second = GammaEngine().calc_all_from_chain(_chain(), now=AS_OF)
    assert first == second


def test_the_as_of_time_is_actually_used(monkeypatch):
    # Charm and vanna move with the time left, so a different as-of must differ.
    early = GammaEngine().calc_all_from_chain(_chain(), now=AS_OF)
    late = GammaEngine().calc_all_from_chain(
        _chain(), now=datetime(2026, 10, 14, 14, 30, tzinfo=TZ))
    assert early[1] != late[1]


def test_without_an_as_of_the_wall_clock_is_used_as_before(monkeypatch):
    _wall_clock(monkeypatch, AS_OF)
    assert GammaEngine().calc_all_from_chain(_chain()) == \
        GammaEngine().calc_all_from_chain(_chain(), now=AS_OF)


def test_derive_minute_prices_at_the_minute(monkeypatch):
    written = []
    monkeypatch.setattr(db, "insert_snapshot",
                        lambda conn, sym, view, summary, grid, dte:
                        written.append((sym, view, summary.get("net_total"), grid)))
    minute = RawMinute(ts=int(AS_OF.timestamp()), chains=[("SPY", _chain())],
                       symbols=["SPY"])
    _wall_clock(monkeypatch, datetime(2026, 10, 14, 10, 0, 40, tzinfo=TZ))
    gc.derive_minute(GammaEngine(), MagicMock(), minute)
    live = list(written)
    written.clear()
    _wall_clock(monkeypatch, datetime(2026, 10, 14, 13, 5, tzinfo=TZ))
    gc.derive_minute(GammaEngine(), MagicMock(), minute)
    assert live and written == live


class _CountingConn:
    """A real SQLite connection that counts commits (sqlite3.Connection's own
    attributes cannot be patched)."""

    def __init__(self):
        import sqlite3
        self._conn = sqlite3.connect(":memory:")
        db.init_schema(self._conn)
        db.init_term_schema(self._conn)
        self.commits = 0

    def commit(self):
        self.commits += 1
        self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_derive_minute_never_commits_even_on_a_term_minute():
    # The raw feed's deriver writes a minute's rows and its cursor in ONE
    # transaction, so derive_minute must leave the commit to its caller -- on
    # the term minutes (every 5th) as well.
    conn = _CountingConn()
    minute = RawMinute(ts=int(AS_OF.timestamp()), chains=[("SPY", _chain())],
                       term=dict(_chain(), symbol="$SPX"), symbols=["SPY"])
    gc.derive_minute(GammaEngine(), conn, minute)
    assert conn.execute("SELECT COUNT(*) FROM gex_term_snapshots").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] > 0
    assert conn.commits == 0
