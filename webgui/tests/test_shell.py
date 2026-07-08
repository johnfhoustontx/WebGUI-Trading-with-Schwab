"""Tests for webgui/main.py — the NiceGUI nav shell.

These import the module (which runs the @ui.page decorators) and inspect the
NiceGUI page registry; they do NOT start the server.
"""
from nicegui import Client


def test_shell_registers_all_pages():
    """The shell registers the Options child routes plus the flat feature pages."""
    import main  # noqa: F401  -- importing registers the @ui.page routes

    routes = set(Client.page_routes.values())
    expected = (
        "/", "/options/paper", "/options/captured", "/options/portfolio",
        "/options/calculator", "/options/swing", "/options/gamma",
        "/options/simulator", "/options/expected-move", "/options/rescue",
        "/sentiment", "/sentiment/rotation",
        "/trade", "/portfolio", "/driver", "/settings",
        "/eod", "/eod/detail", "/status", "/manuals", "/terminate",
        "/market",
    )
    for path in expected:
        assert path in routes, f"missing page route {path}; have {sorted(routes)}"


def test_shell_imports_proxy_for_banner():
    """The shell wires the proxy health helper for the down-banner."""
    import main

    assert hasattr(main, "proxy")
    assert callable(main.proxy.health)


def test_cached_health_memoizes_within_ttl(monkeypatch):
    """cached_health() probes the proxy at most once per TTL window."""
    import main

    calls = {"n": 0}
    monkeypatch.setattr(main.proxy, "health",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1),
                                         {"up": True})[1])
    main._health_cache.update(data=None, ts=0.0)  # cold

    first = main.cached_health()
    second = main.cached_health()
    assert first == {"up": True} and second == {"up": True}
    assert calls["n"] == 1  # second read served from cache

    # Expire the TTL -> next call re-probes.
    main._health_cache["ts"] -= main._HEALTH_TTL_SEC + 1
    main.cached_health()
    assert calls["n"] == 2


def test_nav_css_has_no_reachable_rules():
    """Phase 1: nav-link/nav-title/nav-icon/nav-badge styling moved to .classes();
    _NAV_CSS keeps only Quasar-internal selectors."""
    import main
    css = main._NAV_CSS
    assert "a.nav-link:hover" not in css           # moved to hover:bg-* utility
    assert ".nav-title {" not in css and ".nav-title{" not in css
    assert ".help-fab {" not in css and ".help-fab{" not in css  # position moved
    assert ".nicegui-expansion-content" in css     # Quasar-internal stays
    assert ".q-tooltip.help-tip" in css            # teleported tooltip stays


def test_recompute_badges_uses_passed_scan(monkeypatch):
    """_recompute_badges(scan) must not re-read options:scan from the bus."""
    import main

    reads = []
    real_read = main.bus_client.read

    def tracking_read(view):
        reads.append(view)
        return {} if view == "options:scan" else real_read(view)

    monkeypatch.setattr(main.bus_client, "read", tracking_read)
    monkeypatch.setattr(main.bus_client, "read_version", lambda v: None)
    monkeypatch.setattr(main.bus_client, "read_full", lambda v: (None, None))

    main._recompute_badges(scan={"signals": []})
    assert "options:scan" not in reads  # used the passed scan, no extra read


# ── Health / staleness watcher (R4b / R8 / R9) ───────────────────────────────
def _reset_health_state(main):
    main._ALERT_STATE.update(
        alerted=set(), alerted_init=None, health_alerted=set(), health_init=None)
    main._svc_health_cache.update(data={}, ts=0.0)
    main._bus_outage.update(logged=False)
    main._NAV_BADGES.clear()


def test_probe_services_throttled_to_interval(monkeypatch):
    """The Tier-2 /health fan-out runs at most once per interval, NOT every tick."""
    import main

    calls = {"n": 0}

    class _Resp:
        status_code = 200

        def json(self):
            return {"up": True}

    def fake_get(url, timeout=None):
        calls["n"] += 1
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    main._svc_health_cache.update(data={}, ts=0.0)

    n_svc = len(main._HEALTH_SERVICES)
    # First probe at t0 -> one GET per service.
    main._probe_services_health(1000.0)
    assert calls["n"] == n_svc
    # A tick 2s later reuses the cache -> no new GETs (throttled).
    main._probe_services_health(1002.0)
    assert calls["n"] == n_svc
    # Past the interval -> re-probes.
    main._probe_services_health(1000.0 + main._HEALTH_PROBE_INTERVAL_SEC + 1)
    assert calls["n"] == 2 * n_svc


def test_probe_services_health_never_raises(monkeypatch):
    """A dead service (connection error) maps to False, never propagates."""
    import main
    import requests

    def boom(url, timeout=None):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", boom)
    main._svc_health_cache.update(data={}, ts=0.0)
    out = main._probe_services_health(5000.0)
    assert set(out.keys()) == set(main._HEALTH_SERVICES)
    assert all(v is False for v in out.values())


def test_is_view_stale_threshold(monkeypatch):
    import datetime as dt
    import main

    now = dt.datetime(2026, 6, 17, 15, 0, tzinfo=dt.timezone.utc)
    fresh = (now - dt.timedelta(seconds=10)).isoformat()
    old = (now - dt.timedelta(seconds=main.alerts.STALE_AFTER_SEC + 60)).isoformat()
    assert main._is_view_stale(fresh, now) is False
    assert main._is_view_stale(old, now) is True
    assert main._is_view_stale(None, now) is True          # no publish yet -> stale
    assert main._is_view_stale("garbage", now) is True     # unparseable -> stale

    # options:scan autoscans every 15 min → it has a longer per-view threshold, so a
    # 12-min-old scan is NOT stale for it, but IS for a default-threshold view. This
    # is the fix for the false "scanner stale" toast ~10 min after each scan.
    twelve_min = (now - dt.timedelta(minutes=12)).isoformat()
    assert main._is_view_stale(twelve_min, now, "options:scan") is False
    assert main._is_view_stale(twelve_min, now, "sentiment:composite") is True
    # ...but a genuinely wedged scanner (older than its 20-min threshold) still flags.
    dead_scan = (now - dt.timedelta(minutes=25)).isoformat()
    assert main._is_view_stale(dead_scan, now, "options:scan") is True


def test_watcher_seeds_health_then_alerts_on_transition(monkeypatch):
    """First tick seeds (no alert); a service going down after seeding fires once,
    then dedups while it stays down."""
    import main

    _reset_health_state(main)
    monkeypatch.setattr(main.bus_client, "read", lambda v: {})
    monkeypatch.setattr(main, "_recompute_badges", lambda scan=None: None)
    monkeypatch.setattr(main.app_settings, "load", lambda: {
        "alert_enabled": True, "alert_market_hours_only": False,
        "alert_min_score": 0, "alert_sound": "chime", "alert_volume": 0.6,
        "desktop_notifications": False})
    monkeypatch.setattr(main, "_freshness_facts", lambda now_utc: {})

    health = {"data": {"options": True}}
    monkeypatch.setattr(main, "_probe_services_health", lambda mono: health["data"])

    # Seed tick: everything up -> no alert, no badge.
    assert main._watcher_compute() is None
    assert main._NAV_BADGES.get("/status", 0) == 0

    # options_svc goes down -> transition -> health alert fires once.
    health["data"] = {"options": False}
    d = main._watcher_compute()
    assert d and d["health"] is not None and d["health"][3] == 1
    assert main._NAV_BADGES["/status"] == 1

    # Still down next tick -> deduped (no new alert), badge persists.
    d2 = main._watcher_compute()
    assert d2 is None or d2.get("health") is None
    assert main._NAV_BADGES["/status"] == 1

    # Recovers -> badge clears, and can alert again if it breaks later.
    health["data"] = {"options": True}
    assert main._watcher_compute() is None
    assert main._NAV_BADGES["/status"] == 0


def test_guarded_compute_logs_once_on_bus_outage(monkeypatch, caplog):
    """A bus outage logs a single warning (not a traceback every tick) and returns
    None; recovery logs once and resumes."""
    import logging
    import main

    _reset_health_state(main)

    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ConnectionError("memurai down")

    monkeypatch.setattr(main, "_watcher_compute", boom)
    with caplog.at_level(logging.WARNING, logger="webgui.watcher"):
        assert main._guarded_compute() is None
        assert main._guarded_compute() is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1                    # logged ONCE despite two outages
    assert main._bus_outage["logged"] is True
    assert calls["n"] == 2                        # still attempted each tick

    # Recovery: next successful compute resets the memo + logs once at INFO.
    monkeypatch.setattr(main, "_watcher_compute", lambda: {"scanner": None, "health": None})
    with caplog.at_level(logging.INFO, logger="webgui.watcher"):
        assert main._guarded_compute() == {"scanner": None, "health": None}
    assert main._bus_outage["logged"] is False
