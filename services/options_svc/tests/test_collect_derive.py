"""The per-chain hooks of a collected minute run on THAT minute's clock.

The raw feed derives minutes after the fact (catch-up after a restart), so a
hook that read the wall clock would stamp a replayed 10:42 with 13:00 and
compute its gaps against the wrong time. Live collection uses the same context,
so live and replay cannot drift (docs/plans/2026-10-14-raw-feed-and-catch-up-design.md)."""
import datetime as dt
import types
from zoneinfo import ZoneInfo

from services.options_svc import collect_derive, flow_alerts, flow_sides_tick, traded_premium

CT = ZoneInfo("America/Chicago")
MINUTE = int(dt.datetime(2026, 10, 14, 10, 42, tzinfo=CT).timestamp())
CHAIN = {"symbol": "SPY", "underlyingPrice": 600.0, "callExpDateMap": {},
         "putExpDateMap": {}}


def _hooks(rec):
    return types.SimpleNamespace(
        clear_stashes=lambda: rec.append("clear"),
        stash_tick_chain=lambda s, c: rec.append(("tick", s)),
        stash_uoa=lambda s, hits: rec.append(("uoa", s)),
        stash_big_delta=lambda s, hits: rec.append(("big_delta", s)),
        hiro_tick_row=lambda s, c, d, ts: {"symbol": s, "date": d, "ts": ts},
    )


def test_hooks_receive_the_minutes_clock_not_the_wall_clock(monkeypatch):
    seen = []
    monkeypatch.setattr(flow_sides_tick, "on_chain", lambda s, c, now: seen.append(now))
    monkeypatch.setattr(traded_premium, "on_chain", lambda s, c, now: seen.append(now))
    ctx = collect_derive.build_context(MINUTE, capture=set(), hooks=_hooks([]))
    ctx.on_chain("SPY", CHAIN)
    assert seen == [dt.datetime.fromtimestamp(MINUTE, CT)] * 2


def test_a_minute_with_seconds_is_floored():
    ctx = collect_derive.build_context(MINUTE + 37, capture=set(), hooks=_hooks([]))
    assert ctx.minute_ts == MINUTE
    assert (ctx.minute_dt.hour, ctx.minute_dt.minute, ctx.minute_dt.second) == (10, 42, 0)


def test_hiro_rows_are_stamped_with_the_minute(monkeypatch):
    monkeypatch.setattr(flow_alerts, "load_thresholds",
                        lambda: {"hiro": {"enabled": True, "symbols": ["SPY"]}})
    ctx = collect_derive.build_context(MINUTE, capture=set(), hooks=_hooks([]))
    ctx.on_chain("SPY", CHAIN)
    assert ctx.hiro_rows["SPY"] == {"symbol": "SPY", "date": "2026-10-14", "ts": MINUTE}


def test_hiro_stays_off_outside_the_regular_session(monkeypatch):
    monkeypatch.setattr(flow_alerts, "load_thresholds",
                        lambda: {"hiro": {"enabled": True, "symbols": ["SPY"]}})
    early = int(dt.datetime(2026, 10, 14, 7, 0, tzinfo=CT).timestamp())
    ctx = collect_derive.build_context(early, capture=set(), hooks=_hooks([]))
    ctx.on_chain("SPY", CHAIN)
    assert ctx.hiro_rows == {}


def test_only_captured_symbols_are_stashed_for_the_gamma_refresh():
    rec = []
    ctx = collect_derive.build_context(MINUTE, capture={"QQQ"}, hooks=_hooks(rec))
    ctx.on_chain("SPY", CHAIN)
    ctx.on_chain("QQQ", dict(CHAIN, symbol="QQQ"))
    assert ("tick", "QQQ") in rec and ("tick", "SPY") not in rec
    assert rec[0] == "clear"
