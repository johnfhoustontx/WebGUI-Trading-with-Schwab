"""The activation guarantee: before 2026-08-17 the app must behave EXACTLY as
it did before extended-hours support existed. If any of these fail, the feature
is leaking across its date gate.

2026-08-14 is the Friday before activation; 2026-08-17 is the Monday it goes
live. Every inertness assertion is paired with a POWER CHECK on the activation
date -- an inertness test that passes because the gate is broken in the *other*
direction (nothing ever fires) is worthless.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from services.options_svc import compute, scheduler
from shared import market_calendar as mc

_CT = ZoneInfo("America/Chicago")

BEFORE = dt.date(2026, 8, 14)     # Friday before activation
ACTIVE = dt.date(2026, 8, 17)     # activation Monday


def _ct(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=_CT)


def test_activation_date_is_what_these_tests_assume():
    """Guards the whole file: if the configured date moves, these dates lie."""
    assert mc.activation_date() == ACTIVE
    assert mc.extended_hours_active(BEFORE) is False
    assert mc.extended_hours_active(ACTIVE) is True


# ── The slot gate ───────────────────────────────────────────────────────────


def test_no_gth_collection_before_activation():
    for hh, mm in [(6, 30), (7, 0), (7, 59)]:
        due, _ = scheduler.gex_due(_ct(2026, 8, 14, hh, mm), None)
        assert due is False, f"{hh}:{mm}"


def test_collection_still_starts_at_0800_before_activation():
    due, _ = scheduler.gex_due(_ct(2026, 8, 14, 8, 0), None)
    assert due is True


def test_collection_still_stops_at_1520_before_activation():
    """The stop is untouched by this phase and stays EXCLUSIVE."""
    assert scheduler.gex_due(_ct(2026, 8, 14, 15, 19), None)[0] is True
    assert scheduler.gex_due(_ct(2026, 8, 14, 15, 20), None)[0] is False


def test_gth_collection_fires_on_the_activation_date():
    """Power check -- the inertness tests above must not be vacuously true."""
    due, _ = scheduler.gex_due(_ct(2026, 8, 17, 7, 0), None)
    assert due is True


# ── The symbol restriction ──────────────────────────────────────────────────


def _fake_collector(monkeypatch, *, eligible=None):
    """Fake the lazily-imported collector modules and the eligibility cache.

    ``eligible`` is the cached ``{symbol: bool}`` map, dated to the PRIOR
    session (which is what a real 06:30 read sees -- the session date pivots at
    08:00 CT). Returns the recorder dict; ``rec["symbols"]`` is the argument
    ``poll_once`` was actually called with, which is the whole point.
    """
    import sys as _sys
    import types as _types

    rec = {"poll_n": 0, "symbols": "unset"}

    class _Conn:
        def close(self):
            pass

    def _poll(client, engine, conn, lock=None, symbols=None, on_chain=None):
        rec["poll_n"] += 1
        rec["symbols"] = symbols

    fake_gc = _types.SimpleNamespace(
        LOCK_PATH="LOCK", SYMBOLS=["$SPX", "SPY"],
        collection_symbols=lambda: ["$SPX", "SPY", "NVDA", "TSLA"],
        acquire_collector_lock=lambda path, **kw: True,
        touch_lock=lambda path, **kw: None,
        ensure_file_logging=lambda *a, **k: None,
        poll_once=_poll,
        log=_types.SimpleNamespace(info=lambda *a, **k: None,
                                   debug=lambda *a, **k: None),
    )
    fake_gh = _types.SimpleNamespace(connect=lambda: _Conn(),
                                     init_schema=lambda conn: None,
                                     purge_keep_sessions=lambda conn, **kw: 0)
    fake_gt = _types.SimpleNamespace(GammaEngine=lambda: "ENGINE")
    monkeypatch.setitem(_sys.modules, "gex_collector", fake_gc)
    monkeypatch.setitem(_sys.modules, "gex_history_db", fake_gh)
    monkeypatch.setitem(_sys.modules, "gamma_tool", fake_gt)
    monkeypatch.setattr(compute, "_LAST_PURGE_DATE", None)
    monkeypatch.setattr(compute, "_GEX_SCHEMA_READY", False)
    monkeypatch.setattr(compute, "_publish_eth_eligibility", lambda seen: None)

    stored = None if eligible is None else {"date": "2026-08-13",
                                            "symbols": dict(eligible)}

    class _Bus:
        def cache_get(self, key):
            return None if stored is None else _types.SimpleNamespace(
                payload=stored)

        def cache_set(self, key, payload, **kw):
            return 1

    monkeypatch.setattr(compute, "_briefing_bus", lambda: _Bus())
    return rec


_ELIGIBLE = {"NVDA": True, "TSLA": True, "SPY": False}


def test_full_universe_is_polled_before_activation(monkeypatch):
    """An ETH-eligible symbol gets no special treatment pre-activation.

    At 08:00 on 2026-08-14 -- the only time collection runs that day -- the
    poll must still cover the whole universe (``symbols=None``), even though
    NVDA/TSLA are cached as eligible.
    """
    rec = _fake_collector(monkeypatch, eligible=_ELIGIBLE)

    n = compute.collect_gex_snapshots(now=_ct(2026, 8, 14, 8, 0))

    assert rec["symbols"] is None
    assert n == 4


def test_gth_time_before_activation_still_polls_the_full_universe(monkeypatch):
    """Belt-and-braces: even if something invoked a poll at 07:00 on 2026-08-14
    -- which the slot gate above forbids -- it must NOT narrow the universe."""
    rec = _fake_collector(monkeypatch, eligible=_ELIGIBLE)

    n = compute.collect_gex_snapshots(now=_ct(2026, 8, 14, 7, 0))

    assert rec["symbols"] is None
    assert n == 4


def test_only_eligible_symbols_are_polled_on_the_activation_date(monkeypatch):
    """Power check for the two tests above -- the restriction is real, and it
    reads the PRIOR session's eligibility map (dated 2026-08-13 here)."""
    rec = _fake_collector(monkeypatch, eligible=_ELIGIBLE)

    n = compute.collect_gex_snapshots(now=_ct(2026, 8, 17, 7, 0))

    assert rec["symbols"] == ["NVDA", "TSLA"]
    assert n == 2


def test_activation_day_regular_hours_still_polls_everything(monkeypatch):
    """The restriction is scoped to GTH -- 08:00 onward is unchanged forever."""
    rec = _fake_collector(monkeypatch, eligible=_ELIGIBLE)

    n = compute.collect_gex_snapshots(now=_ct(2026, 8, 17, 9, 30))

    assert rec["symbols"] is None
    assert n == 4


def test_cold_eligibility_cache_skips_the_gth_poll(monkeypatch):
    """Never guess the universe: with nothing cached, no fetch happens."""
    rec = _fake_collector(monkeypatch, eligible=None)

    n = compute.collect_gex_snapshots(now=_ct(2026, 8, 17, 7, 0))

    assert n == 0
    assert rec["poll_n"] == 0


# ── The calendar itself ─────────────────────────────────────────────────────


def test_collection_window_is_minute_identical_before_activation():
    """Exhaustive: over the whole pre-activation Friday, the eth_eligible flag
    buys an eligible symbol exactly zero extra minutes."""
    day = dt.datetime(2026, 8, 14, 0, 0, tzinfo=_CT)
    extra = sum(
        1 for i in range(24 * 60)
        if mc.in_collection_window(day + dt.timedelta(minutes=i),
                                   eth_eligible=True)
        != mc.in_collection_window(day + dt.timedelta(minutes=i),
                                   eth_eligible=False))
    assert extra == 0


def test_collection_window_gains_exactly_90_minutes_on_activation():
    """Power check: 06:30-07:59 inclusive = 90 minutes, and no more."""
    day = dt.datetime(2026, 8, 17, 0, 0, tzinfo=_CT)
    extra = sum(
        1 for i in range(24 * 60)
        if mc.in_collection_window(day + dt.timedelta(minutes=i),
                                   eth_eligible=True)
        != mc.in_collection_window(day + dt.timedelta(minutes=i),
                                   eth_eligible=False))
    assert extra == 90
