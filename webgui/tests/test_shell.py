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
        "/sentiment", "/sentiment/sectors", "/sentiment/rotation", "/sentiment/rrg",
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
    _NAV_CSS keeps only Quasar-internal selectors. The 2026-07-11 nav redesign
    replaced the expandable sub-menus with the top tab strip, so the expansion
    rules are gone and the compact-tabs rule (small tab padding) is in."""
    import main
    css = main._NAV_CSS
    assert "a.nav-link:hover" not in css           # moved to hover:bg-* utility
    assert ".nav-title {" not in css and ".nav-title{" not in css
    assert ".help-fab {" not in css and ".help-fab{" not in css  # position moved
    assert ".nicegui-expansion-content" not in css  # no expandable sub-menus anymore
    assert ".compact-tabs .q-tab" in css           # small-padding tab strips
    assert ".q-tooltip.help-tip" in css            # teleported tooltip stays


def test_group_children_maps_routes_to_their_group():
    """The top tab strip shows the active route's group; flat pages have none."""
    import main
    opts = main._group_children("/options/gamma")
    assert ("/", "Scanner", "radar") in opts                       # Options group
    assert main._group_children("/sentiment/rotation") == main.SENTIMENT_CHILDREN
    more = main._group_children("/manuals")                        # Settings child
    assert ("/eod", "EOD Report", "summarize") in more             # merged into More
    assert main._group_children("/trade") is None                  # flat page — no strip
    assert main._group_children("/driver") is None


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


# ── Deep Slate shell helpers (Phase 2) ──────────────────────────────────────
def test_breadcrumb_parts_grouped_and_flat():
    import main
    # Grouped page → (group label, page label)
    assert main.breadcrumb_parts("/") == ("Options", "Scanner")
    assert main.breadcrumb_parts("/options/gamma") == ("Options", "Gamma")
    assert main.breadcrumb_parts("/sentiment/rotation") == (
        "Market Trend & Sentiment", "Sector Rotation")
    assert main.breadcrumb_parts("/status") == ("More", "System Status")
    # Flat single page → (page label, "") — no "· Tab"
    assert main.breadcrumb_parts("/trade") == ("Trade Analyzer", "")
    assert main.breadcrumb_parts("/market") == ("Market Dashboard", "")


def test_market_status_parts():
    import main
    from datetime import datetime
    # A Wednesday 10:00 CT (2026-07-08) is open; a Sunday is closed.
    open_dt = datetime(2026, 7, 8, 10, 0, tzinfo=main.alerts.CT)
    closed_dt = datetime(2026, 7, 5, 10, 0, tzinfo=main.alerts.CT)  # Sunday
    assert main.market_status_parts(open_dt) == ("MARKET OPEN", True)
    assert main.market_status_parts(closed_dt) == ("MARKET CLOSED", False)


# ── ticker setting resync at startup ───────────────────────────────────────
# The ticker toggle lives in settings.json (webgui) but gates a Claude call in
# market_svc, mirrored through Redis. Re-assert it at startup so a wiped/restarted
# Redis (key gone → service defaults back to enabled) can't silently resume the
# API calls while the GUI still says the ticker is off.


def test_sync_ticker_setting_reasserts_the_flag(monkeypatch):
    import main

    sent = []
    monkeypatch.setattr(main.bus_client, "request",
                        lambda domain, cmd: sent.append((domain, cmd)))
    monkeypatch.setattr(main.app_settings, "get", lambda k: False)
    main.sync_ticker_setting()
    assert sent == [("market", {"type": "disable_summary"})]

    sent.clear()
    monkeypatch.setattr(main.app_settings, "get", lambda k: True)
    main.sync_ticker_setting()
    assert sent == [("market", {"type": "enable_summary"})]


def test_sync_ticker_setting_survives_a_down_bus(monkeypatch):
    import main

    def _boom(domain, cmd):
        raise RuntimeError("redis down")

    monkeypatch.setattr(main.bus_client, "request", _boom)
    monkeypatch.setattr(main.app_settings, "get", lambda k: True)
    main.sync_ticker_setting()  # startup must not fail because Memurai is down


def test_sync_ticker_setting_registered_inside_the_main_guard():
    import inspect

    import main

    src = inspect.getsource(main)
    head, guard, tail = src.partition('if __name__ in {"__main__", "__mp_main__"}:')
    assert guard, "the __main__ guard moved — this test needs updating"
    # Registered exactly once, and only on the entry path (see the reimport test).
    assert "app.on_startup(" not in head
    assert "app.on_startup(sync_ticker_setting)" in tail


def test_reimporting_main_after_startup_does_not_raise():
    """Pages do `import main as _shell` (e.g. pages/options/scanner.py) at REQUEST
    time. The entry script runs as __main__, so that re-executes main.py as a
    SECOND module object — after NiceGUI has started. Any module-level
    `app.on_startup()` raises RuntimeError there and 500s every page, so lifecycle
    registration must live inside the __main__ guard.
    """
    import importlib.util
    import pathlib

    from nicegui import app as ng_app
    from nicegui.app.app import State

    main_py = pathlib.Path(__file__).resolve().parents[1] / "main.py"
    prev = ng_app._state
    ng_app._state = State.STARTED  # simulate "NiceGUI has already been started"
    try:
        spec = importlib.util.spec_from_file_location("main_reimport_probe", main_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # must not raise
    finally:
        ng_app._state = prev


def test_drawer_icons_are_present_and_distinct():
    """The drawer is becoming a 64px icon rail (hover-to-expand) whose collapsed
    state shows ONLY icons — so each drawer item needs a non-empty, distinct icon.
    ``_nav_link``/``_nav_group_link`` now render the ``icon`` arg (the dot is
    retired), but the rail CSS that collapses the labels lands in a later task.
    Scope is the 7 drawer items (3 groups + FLAT_NAV); child-page icons are not
    rail affordances (the tab strip renders labels only)."""
    from collections import Counter

    import main

    items = ([(label, icon) for label, icon, _c in main._NAV_GROUPS]
             + [(label, icon) for _p, label, icon in main.FLAT_NAV])
    # Pinned count: all()/set-length are vacuously true on an empty list, so this
    # is the non-vacuity guard. A legitimate new drawer item should bump it.
    assert len(items) == 7, f"expected 7 drawer items, got {len(items)}: {items}"
    assert not [l for l, i in items if not i], \
        f"drawer items with no icon: {[l for l, i in items if not i]}"
    dupes = {i: [l for l, x in items if x == i]
             for i, n in Counter(i for _l, i in items).items() if n > 1}
    assert not dupes, f"drawer icons collide: {dupes}"
    by_label = dict(items)
    # The two curated changes (design doc 2026-07-15).
    assert by_label["Market Trend & Sentiment"] == "speed"
    assert by_label["Trade Analyzer"] == "query_stats"


def test_drawer_width_pinned_vs_rail():
    """Pinned = the full menu (Quasar offsets content to match). Unpinned = the
    64px icon rail; the CSS :hover rule widens it WITHOUT changing this number,
    which is exactly why hovering overlays instead of reflowing the page."""
    import main
    assert main.drawer_width(True) == main.NAV_WIDTH_OPEN == 248
    assert main.drawer_width(False) == main.NAV_WIDTH_RAIL == 64


def test_the_retired_dot_is_gone_for_good():
    """The dot is retired repo-wide — the icon carries active state now. A negative
    source assertion is the cheap global guard against it creeping back."""
    import inspect
    import main
    assert "nav-dot" not in inspect.getsource(main)


def _nav_wrapper_of(badge):
    """The ``relative`` div ``_nav_icon`` mounts the icon + badge into."""
    return badge.parent_slot.parent


def test_nav_link_badge_floats_on_the_icon_and_registers_per_route():
    """The badge must sit INSIDE the relative wrapper (Quasar's floating badge is
    position:absolute — it anchors to the nearest positioned ancestor), and the ref
    the 2s watcher writes to must be registered under the item's route."""
    from nicegui import ui

    import main
    main._NAV_BADGES.clear()
    main._NAV_BADGES["/trade"] = 3
    main._badge_refs.clear()
    with ui.card():
        main._nav_link("/trade", "Trade Analyzer", "query_stats", "/trade")

    badge = main._badge_refs["/trade"]          # registration, per route
    assert badge.text == "3" and badge.visible
    assert badge._props.get("floating") is True

    wrapper = _nav_wrapper_of(badge)            # re-parenting guard
    assert "relative" in wrapper.classes, \
        "floating is position:absolute — it anchors to the nearest positioned " \
        "ancestor, so the badge must stay inside the relative wrapper"
    icons = [c for c in wrapper.default_slot.children if isinstance(c, ui.icon)]
    assert [i._props["name"] for i in icons] == ["query_stats"], \
        "the icon is the rail's affordance and shares the badge's wrapper"
    assert any("nav-icon-active" in i.classes for i in icons), \
        "the active item's icon carries .nav-icon-active (task 5 styles it)"


def test_nav_link_badge_hidden_at_zero_and_icon_idle_when_inactive():
    """The 0/inactive complement of the test above — a count of 0 renders no badge
    text AND is hidden, and a non-active item's icon must NOT claim active state."""
    from nicegui import ui

    import main
    main._NAV_BADGES.clear()
    main._badge_refs.clear()
    with ui.card():
        main._nav_link("/trade", "Trade Analyzer", "query_stats", "/market")

    badge = main._badge_refs["/trade"]
    assert badge.text == "" and not badge.visible
    icons = [c for c in _nav_wrapper_of(badge).default_slot.children
             if isinstance(c, ui.icon)]
    assert not any("nav-icon-active" in i.classes for i in icons)


def test_nav_group_link_badge_sums_across_the_groups_paths():
    """A group item shows the SUM of its children's counts (the watcher recomputes
    that same sum via _group_badge_refs' paths), on its own icon's corner."""
    from nicegui import ui

    import main
    main._NAV_BADGES.clear()
    main._NAV_BADGES.update({"/": 2, "/options/captured": 5})
    main._group_badge_refs.clear()
    children = [("/", "Scanner", "radar"), ("/options/captured", "Captured", "bookmark")]
    with ui.card():
        main._nav_group_link("Options", "insights", children, "/")

    badge, paths = main._group_badge_refs["Options"]   # registered shape: (badge, paths)
    assert paths == ["/", "/options/captured"]
    assert badge.text == "7" and badge.visible, "2 + 5 across the group's paths"

    wrapper = _nav_wrapper_of(badge)
    assert "relative" in wrapper.classes
    icons = [c for c in wrapper.default_slot.children if isinstance(c, ui.icon)]
    assert [i._props["name"] for i in icons] == ["insights"]
