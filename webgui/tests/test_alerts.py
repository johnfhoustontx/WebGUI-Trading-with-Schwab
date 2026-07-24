import datetime as dt
from zoneinfo import ZoneInfo
import alerts

CT = ZoneInfo("America/Chicago")


def _scan():
    return {
        "signals_0dte": [
            {"symbol": "SPX", "type": "PUT", "short_strike": 5000, "long_strike": 4990,
             "expiration": "2026-06-17", "composite_score": 80},
        ],
        "signals_swing": [
            {"symbol": "QQQ", "type": "CALL", "short_strike": 500, "long_strike": 510,
             "expiration": "2026-06-20", "composite_score": 40},
        ],
    }


def test_scanner_keys_covers_both_tables():
    assert len(alerts.scanner_keys(_scan())) == 2
    assert alerts.scanner_keys({}) == set()


def test_new_signal_text_singular_and_plural():
    assert alerts.new_signal_text(1) == "1 new scanner signal"
    assert alerts.new_signal_text(3) == "3 new scanner signals"


def test_unread_count_is_current_minus_acked():
    keys = alerts.scanner_keys(_scan())
    assert alerts.unread_count(keys, set()) == 2
    one = {next(iter(keys))}
    assert alerts.unread_count(keys, one) == 1
    assert alerts.unread_count(keys, keys) == 0


def test_qualifying_new_respects_min_score_and_alerted():
    scan = _scan()
    # min_score 70 → only the 80-score SPX signal qualifies
    q = alerts.qualifying_new(scan, alerted=set(), min_score=70)
    assert len(q) == 1
    # already-alerted keys are excluded
    assert alerts.qualifying_new(scan, alerted=q, min_score=70) == set()
    # min_score 0 → both qualify
    assert len(alerts.qualifying_new(scan, alerted=set(), min_score=0)) == 2


def test_in_market_hours():
    assert alerts.in_market_hours(dt.datetime(2026, 6, 17, 10, 0, tzinfo=CT))   # Wed 10:00
    assert not alerts.in_market_hours(dt.datetime(2026, 6, 17, 16, 0, tzinfo=CT))  # after 15:00
    assert not alerts.in_market_hours(dt.datetime(2026, 6, 13, 10, 0, tzinfo=CT))  # Saturday


def test_in_market_hours_excludes_holidays():
    # Thanksgiving 2026-11-26 (a Thursday) — closed despite being an in-window weekday.
    assert not alerts.in_market_hours(dt.datetime(2026, 11, 26, 10, 0, tzinfo=CT))
    # Christmas 2026-12-25 (a Friday) — closed.
    assert not alerts.in_market_hours(dt.datetime(2026, 12, 25, 10, 0, tzinfo=CT))
    # New Year's Day 2027-01-01 (a Friday) — closed (holiday, not weekend).
    assert not alerts.in_market_hours(dt.datetime(2027, 1, 1, 10, 0, tzinfo=CT))
    # the regular weekday right before Thanksgiving IS open.
    assert alerts.in_market_hours(dt.datetime(2026, 11, 25, 10, 0, tzinfo=CT))  # Wed


def test_alerts_suppressed_on_holiday():
    """Both scanner and health-alert gates are closed on a market holiday."""
    holiday = dt.datetime(2026, 11, 26, 10, 0, tzinfo=CT)  # Thanksgiving, in-window
    base = {"alert_enabled": True, "alert_market_hours_only": True}
    assert not alerts.should_alert(base, {"k"}, holiday)
    assert not alerts.health_alert_gate(base, holiday)
    # ...but with the market-hours gate OFF, the user opted into off-hours alerts.
    assert alerts.should_alert({**base, "alert_market_hours_only": False}, {"k"}, holiday)


def test_should_alert_truth_table():
    now_open = dt.datetime(2026, 6, 17, 10, 0, tzinfo=CT)
    now_closed = dt.datetime(2026, 6, 17, 16, 0, tzinfo=CT)
    q = {"k"}
    base = {"alert_enabled": True, "alert_market_hours_only": True}
    assert alerts.should_alert(base, q, now_open)
    assert not alerts.should_alert(base, q, now_closed)         # gated by market hours
    assert not alerts.should_alert(base, set(), now_open)       # nothing new
    assert not alerts.should_alert({**base, "alert_enabled": False}, q, now_open)
    # market-hours gate off → fires even after close
    assert alerts.should_alert({**base, "alert_market_hours_only": False}, q, now_closed)


# ── Health / staleness alerts (R4b / R8) ─────────────────────────────────────
NOW_OPEN = dt.datetime(2026, 6, 17, 10, 0, tzinfo=CT)     # Wed 10:00 CT (in-hours)
NOW_CLOSED = dt.datetime(2026, 6, 17, 16, 0, tzinfo=CT)   # after 15:00 CT
GATE_ON = {"alert_enabled": True, "alert_market_hours_only": True}
GATE_OFF_HOURS = {"alert_enabled": True, "alert_market_hours_only": False}


def test_unhealthy_keys_namespaces_stale_and_down():
    fresh = {"options:scan": True, "sentiment:composite": False}
    health = {"options": False, "trade": True, "driver": None}
    keys = alerts.unhealthy_keys(fresh, health)
    # stale view + down service; healthy view, up service, and None-health excluded
    assert keys == {"stale:options:scan", "down:options"}
    assert alerts.unhealthy_keys({}, {}) == set()
    assert alerts.unhealthy_keys(None, None) == set()


def test_new_health_alerts_fires_on_transition_only():
    fresh = {"options:scan": True}
    health = {}
    fire, nxt = alerts.new_health_alerts(fresh, health, set(), GATE_ON, NOW_OPEN)
    assert fire == {"stale:options:scan"}
    assert nxt == {"stale:options:scan"}
    # Persistent: same problem next tick -> deduped (no new fire), still tracked.
    fire2, nxt2 = alerts.new_health_alerts(fresh, health, nxt, GATE_ON, NOW_OPEN)
    assert fire2 == set()
    assert nxt2 == {"stale:options:scan"}


def test_new_health_alerts_clears_and_refires_on_recovery():
    fresh_bad = {"options:scan": True}
    fresh_ok = {"options:scan": False}
    _, alerted = alerts.new_health_alerts(fresh_bad, {}, set(), GATE_ON, NOW_OPEN)
    # Recovered -> dropped from carry-forward set.
    fire, alerted = alerts.new_health_alerts(fresh_ok, {}, alerted, GATE_ON, NOW_OPEN)
    assert fire == set()
    assert alerted == set()
    # Breaks again -> fires again (fire-on-transition).
    fire, alerted = alerts.new_health_alerts(fresh_bad, {}, alerted, GATE_ON, NOW_OPEN)
    assert fire == {"stale:options:scan"}


def test_new_health_alerts_respects_gate_but_still_tracks():
    fresh = {"options:scan": True}
    # Master toggle off -> no fire, but the problem is still tracked so it won't
    # chime the instant alerts are re-enabled if it's still broken.
    fire, nxt = alerts.new_health_alerts(
        fresh, {}, set(), {"alert_enabled": False}, NOW_OPEN)
    assert fire == set()
    assert nxt == {"stale:options:scan"}
    # Off-hours with the market-hours gate on -> no fire, still tracked.
    fire, nxt = alerts.new_health_alerts(fresh, {}, set(), GATE_ON, NOW_CLOSED)
    assert fire == set()
    assert nxt == {"stale:options:scan"}
    # Gate off -> fires even after close.
    fire, _ = alerts.new_health_alerts(fresh, {}, set(), GATE_OFF_HOURS, NOW_CLOSED)
    assert fire == {"stale:options:scan"}


def test_health_alert_text_singular_plural():
    assert alerts.health_alert_text(1) == "1 service alert — stale or down"
    assert alerts.health_alert_text(2) == "2 service alerts — stale or down"


def test_new_flow_alerts():
    from webgui import alerts
    view = {"alerts": [{"id": "A"}, {"id": "B"}]}
    new, acked = alerts.new_flow_alerts(view, set())
    assert [a["id"] for a in new] == ["A", "B"] and acked == {"A", "B"}
    new2, acked2 = alerts.new_flow_alerts(view, {"A", "B"})
    assert new2 == [] and acked2 == {"A", "B"}
    # Defensive: None / malformed view -> ([], acked unchanged).
    assert alerts.new_flow_alerts(None, {"A"}) == ([], {"A"})
    assert alerts.new_flow_alerts({"alerts": "nope"}, set()) == ([], set())


# ── Flow alerts: market-hours gate + backlog-replay guard ────────────────────
_RTH = dt.datetime(2026, 7, 23, 10, 0, tzinfo=CT)      # Thu 10:00 CT
_AFTER = dt.datetime(2026, 7, 23, 21, 0, tzinfo=CT)    # Thu 21:00 CT
_NEW = [{"id": "SPY|uoa|C|737|2026-07-23", "text": "SPY 737C UNUSUAL"}]


def test_should_flow_alert_fires_during_market_hours():
    s = {"flow_alerts_enabled": True, "alert_market_hours_only": True}
    assert alerts.should_flow_alert(s, _NEW, _RTH) is True


def test_should_flow_alert_silent_after_rth():
    """The server stops publishing at 15:20 CT, but a GUI-side backlog replay can
    surface alerts at any hour -- so the gate has to live here too."""
    s = {"flow_alerts_enabled": True, "alert_market_hours_only": True}
    assert alerts.should_flow_alert(s, _NEW, _AFTER) is False


def test_should_flow_alert_after_rth_when_gate_disabled():
    s = {"flow_alerts_enabled": True, "alert_market_hours_only": False}
    assert alerts.should_flow_alert(s, _NEW, _AFTER) is True


def test_should_flow_alert_respects_feature_toggle_and_emptiness():
    on = {"flow_alerts_enabled": True, "alert_market_hours_only": True}
    off = {"flow_alerts_enabled": False, "alert_market_hours_only": True}
    assert alerts.should_flow_alert(off, _NEW, _RTH) is False
    assert alerts.should_flow_alert(on, [], _RTH) is False


def test_should_flow_alert_silent_on_weekend():
    s = {"flow_alerts_enabled": True, "alert_market_hours_only": True}
    sat = dt.datetime(2026, 7, 25, 10, 0, tzinfo=CT)
    assert alerts.should_flow_alert(s, _NEW, sat) is False


def test_new_flow_alerts_seeds_empty_from_unreadable_view():
    """Pins the precondition of the replay bug: an unreadable (None) view yields an
    EMPTY acked set, so main.py must not treat that as a completed seed."""
    assert alerts.new_flow_alerts(None, set()) == ([], set())
