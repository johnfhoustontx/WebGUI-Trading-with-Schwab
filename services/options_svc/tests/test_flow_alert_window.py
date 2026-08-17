"""The flow-alert window gate: DETECTION is gated, not just the push.

Why detection. ``run_flow_alerts`` mutates the day-scoped cooldown map (which
doubles as the seen-set) at DETECTION time -- ``flow_alerts.detect_flow_alerts``
writes ``cooldowns[key]`` in place, and the UOA loop writes ``cooldowns[cid]``
before pushing. Suppressing only ``send_flow_alert`` would therefore mark a
06:45 GTH signal as seen and it would NEVER fire again, not even at the open.
The alert would be destroyed, not deferred. ``test_gth_signal_still_fires_at_the_open``
is the property that proves the fix.

Why the collection window (08:00-15:20 CT) and not the REGULAR session
(08:30-15:00) or the push window (08:00-15:00): ``send_flow_alert`` has NO
market-hours gate (``_in_market_hours`` guards ``notify_signals`` and
``state_alert``, neither of which is on the flow path), so flow alerts detect
AND push across the whole 08:00-15:20 window TODAY. Either narrower predicate
would silently delete a working feature's first 30 / last 20 minutes.
``test_todays_window_is_minute_identical`` pins that.

2026-08-14 is the Friday before activation; 2026-08-17 the Monday it goes live.
"""
import datetime as dt
import sqlite3

import pytest

from services.options_svc import handlers
from shared import market_calendar as mc
from shared.bus import Bus

BEFORE = dt.date(2026, 8, 14)     # Friday before activation
ACTIVE = dt.date(2026, 8, 17)     # activation Monday

# A decisive, large-premium crossover (calls overtake puts) -- the same shape
# test_handlers.py uses for the happy path.
_SERIES = [(60, 100.0, 0, 0, 100000.0, 200000.0),
           (120, 100.0, 0, 0, 260000.0, 200000.0)]


def _at(d: dt.date, h: int, m: int) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, h, m, tzinfo=mc.CT)


@pytest.fixture(autouse=True)
def _fresh_session_config():
    """The session config is mtime-cached process-wide; drop it either side so a
    test that pokes at sessions.toml cannot leak into (or inherit from) another."""
    mc.reset_config_cache()
    yield
    mc.reset_config_cache()


def test_activation_date_is_what_these_tests_assume():
    """Guards the whole file: if the configured date moves, these dates lie."""
    assert mc.activation_date() == ACTIVE
    assert mc.extended_hours_active(BEFORE) is False
    assert mc.extended_hours_active(ACTIVE) is True


# -- The predicate -----------------------------------------------------------


def test_todays_window_is_minute_identical():
    """Requirement: behavior in the window that collects TODAY is unchanged.

    Every minute of 08:00-15:19 CT is open, and identically so before and after
    activation -- so nothing the running app does today moves. 15:20 is the
    EXCLUSIVE collection stop, matching ``in_collection_window``.
    """
    for d in (BEFORE, ACTIVE):
        for h, m in [(8, 0), (8, 15), (8, 29), (8, 30), (12, 0),
                     (15, 0), (15, 14), (15, 19)]:
            assert handlers._flow_window_open(_at(d, h, m)) is True, f"{d} {h}:{m}"
        assert handlers._flow_window_open(_at(d, 15, 20)) is False


def test_gth_is_closed_on_the_activation_date():
    """The newly-added 06:30-08:00 CT stretch is the ONLY thing this gate closes."""
    for h, m in [(6, 30), (7, 0), (7, 30), (7, 59)]:
        assert handlers._flow_window_open(_at(ACTIVE, h, m)) is False, f"{h}:{m}"


def test_gate_is_inert_before_activation():
    """Pre-activation the GTH stretch is unreachable anyway (``gex_due`` does not
    fire), so the gate can only agree with today's behavior -- it never opens a
    window that was closed."""
    for h, m in [(6, 30), (7, 59)]:
        assert handlers._flow_window_open(_at(BEFORE, h, m)) is False
    assert handlers._flow_window_open(_at(BEFORE, 3, 0)) is False


def test_weekend_and_holiday_are_closed():
    assert handlers._flow_window_open(_at(dt.date(2026, 8, 15), 10, 0)) is False
    assert handlers._flow_window_open(_at(dt.date(2026, 7, 3), 10, 0)) is False


def test_config_opt_in_reopens_extended_hours(monkeypatch):
    """``[alerts].fire_in_extended_hours = true`` re-enables GTH detection."""
    assert handlers._flow_window_open(_at(ACTIVE, 7, 0)) is False
    monkeypatch.setattr(mc, "alerts_fire_in_extended_hours", lambda: True)
    assert handlers._flow_window_open(_at(ACTIVE, 7, 0)) is True
    # Still closed at 03:00 -- the flag opts into the SESSIONS, not into 24/7.
    assert handlers._flow_window_open(_at(ACTIVE, 3, 0)) is False


def test_gate_reads_the_clock_when_not_passed(monkeypatch):
    monkeypatch.setattr(handlers, "_alert_now", lambda: _at(ACTIVE, 7, 0))
    assert handlers._flow_window_open() is False
    monkeypatch.setattr(handlers, "_alert_now", lambda: _at(ACTIVE, 10, 0))
    assert handlers._flow_window_open() is True


# -- run_flow_alerts ---------------------------------------------------------


def _wire(monkeypatch, *, series=_SERIES, sent=None, detected=None):
    """Wire run_flow_alerts onto a fake universe + series, recording pushes."""
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["$SPX"])
    monkeypatch.setattr(handlers, "_load_flow_series_for",
                        lambda conn, sym, limit: series)
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 120)
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert",
                        lambda a, **k: (sent if sent is not None else []).append(a))
    if detected is not None:
        real = handlers.flow_alerts.detect_flow_alerts

        def _spy(*a, **k):
            detected.append(a[0])
            return real(*a, **k)
        monkeypatch.setattr(handlers.flow_alerts, "detect_flow_alerts", _spy)


def test_gth_tick_detects_nothing_and_leaves_the_cooldown_map_untouched(monkeypatch):
    """The load-bearing assertion: no detection call, no cooldown write, no feed
    write. If the cooldown map were mutated the signal would be burned."""
    bus = Bus(fake=True)
    sent, detected = [], []
    _wire(monkeypatch, sent=sent, detected=detected)
    monkeypatch.setattr(handlers, "_alert_now", lambda: _at(ACTIVE, 6, 45))

    handlers.run_flow_alerts(bus)

    assert detected == [], "detection ran during GTH"
    assert sent == []
    assert bus.cache_get(handlers._FLOW_COOLDOWN_KEY) is None
    assert bus.cache_get("cache:options:flow_alerts") is None


def test_gth_signal_still_fires_at_the_open(monkeypatch):
    """THE deferral property. A qualifying signal present during GTH must not be
    consumed there -- it must fire once, in full, when the window opens."""
    bus = Bus(fake=True)
    sent = []
    _wire(monkeypatch, sent=sent)

    # Several GTH ticks with the signal already qualifying.
    for hh, mm in [(6, 30), (7, 0), (7, 59)]:
        monkeypatch.setattr(handlers, "_alert_now", lambda h=hh, m=mm: _at(ACTIVE, h, m))
        handlers.run_flow_alerts(bus)
    assert sent == [], "GTH consumed the signal"

    # 08:00 -- the window opens on the SAME series.
    monkeypatch.setattr(handlers, "_alert_now", lambda: _at(ACTIVE, 8, 0))
    handlers.run_flow_alerts(bus)

    assert len(sent) == 1, "the deferred signal did not fire at the open"
    env = bus.cache_get("cache:options:flow_alerts")
    assert any(a["type"] == "crossover" for a in env.payload["alerts"])

    # ...and exactly once: the next in-window tick is still on cooldown.
    monkeypatch.setattr(handlers, "_alert_now", lambda: _at(ACTIVE, 8, 1))
    handlers.run_flow_alerts(bus)
    assert len(sent) == 1


def test_uoa_stash_is_not_drained_during_gth(monkeypatch):
    """The UOA path writes ``cooldowns[cid]`` before pushing, so it burns a
    contract just as thoroughly. The gate must precede the drain."""
    bus = Bus(fake=True)
    contract = {"type": "uoa", "side": "call", "symbol": "SPY", "strike": 450.0,
                "expiry": "2026-08-21", "dte": 4, "cost": 1.85, "volume": 8200,
                "oi": 1300, "vol_oi": 6.3, "premium": 1517000.0}
    drained = []

    def _stash():
        drained.append(True)
        return {"SPY": [dict(contract)]}

    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["SPY"])
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda c, s, n: [])
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 1000)
    monkeypatch.setattr(handlers.compute, "take_uoa_stash", _stash)
    sent = []
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert",
                        lambda a, **k: sent.append(a))

    monkeypatch.setattr(handlers, "_alert_now", lambda: _at(ACTIVE, 6, 45))
    handlers.run_flow_alerts(bus)
    assert drained == [] and sent == []

    monkeypatch.setattr(handlers, "_alert_now", lambda: _at(ACTIVE, 8, 0))
    handlers.run_flow_alerts(bus)
    assert sent and sent[0]["id"] == "SPY|uoa|call|450|2026-08-21"


def test_config_opt_in_lets_gth_alerts_fire(monkeypatch):
    bus = Bus(fake=True)
    sent = []
    _wire(monkeypatch, sent=sent)
    monkeypatch.setattr(handlers, "_alert_now", lambda: _at(ACTIVE, 6, 45))
    monkeypatch.setattr(mc, "alerts_fire_in_extended_hours", lambda: True)

    handlers.run_flow_alerts(bus)

    assert len(sent) == 1


def test_regular_window_detection_is_unaffected(monkeypatch):
    """Power check for the inertness tests above: the 08:00 and 15:19 edges --
    both OUTSIDE the 08:30-15:00 REGULAR session -- still detect and push."""
    for hh, mm in [(8, 0), (8, 15), (15, 19)]:
        bus = Bus(fake=True)
        sent = []
        _wire(monkeypatch, sent=sent)
        monkeypatch.setattr(handlers, "_alert_now", lambda: _at(BEFORE, hh, mm))
        handlers.run_flow_alerts(bus)
        assert len(sent) == 1, f"{hh}:{mm} lost its alerts"


# -- publish_flow_skew -------------------------------------------------------


def test_flow_skew_is_not_published_during_gth(monkeypatch):
    """Only ``$SPX`` is ETH-eligible, and ``latest_skew_by_symbol`` has no date
    filter -- a GTH publish would pair a thin fresh $SPX ``rr_delta`` with
    SPY/QQQ frozen at yesterday's close and hand it to the aggression axis as
    one current snapshot."""
    bus = Bus(fake=True)
    built = []
    monkeypatch.setattr(handlers.compute, "flow_skew_view",
                        lambda: built.append(True) or {"$SPX": {"rr_25d": 1.0}})

    monkeypatch.setattr(handlers, "_alert_now", lambda: _at(ACTIVE, 6, 45))
    handlers.publish_flow_skew(bus)
    assert built == [] and bus.cache_get("cache:options:flow_skew") is None

    monkeypatch.setattr(handlers, "_alert_now", lambda: _at(ACTIVE, 8, 0))
    handlers.publish_flow_skew(bus)
    assert built == [True]
    assert bus.cache_get("cache:options:flow_skew").payload == {"$SPX": {"rr_25d": 1.0}}


def test_a_missing_history_db_degrades_instead_of_raising(monkeypatch):
    """``gex_history.db`` is gitignored DATA and legitimately absent on a fresh
    install, so ``run_flow_alerts`` opens it defensively and carries on with no
    series when it cannot.

    This test exists because the conftest ``_in_memory_gex_db`` fixture now makes
    ``connect`` always succeed — which is right (tests must not depend on machine
    state) but silently removed the only coverage this degrade path had. Assert
    it deliberately rather than by accident.
    """
    import gex_history_db as gh

    def _boom(read_only=False):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(gh, "connect", _boom)
    bus = Bus(fake=True)
    sent = []
    _wire(monkeypatch, sent=sent)
    monkeypatch.setattr(handlers, "_alert_now", lambda: _at(BEFORE, 10, 0))

    handlers.run_flow_alerts(bus)          # must not raise

    assert sent == [], "no history means no series, so nothing to alert on"
