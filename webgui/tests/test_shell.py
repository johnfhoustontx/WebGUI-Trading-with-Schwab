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
        "/options/matrix", "/options/flow",
        "/sentiment", "/sentiment/sectors", "/sentiment/rotation", "/sentiment/rrg",
        "/sentiment/momentum",
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


def test_nav_css_carries_only_what_tailwind_cannot_express():
    """The nav's layout/typography lives in ``.classes()`` (Tailwind-first). What
    stays in _NAV_CSS is only what a utility class genuinely CANNOT express:
    ancestor-state selectors (the rail's :hover/:focus-within/.nav-pinned label
    fade), rgba washes (the JIT won't emit rgba arbitraries), and Quasar-internal
    or teleported DOM (.q-tab, .q-tooltip).

    So this guard is deliberately NOT "no .nav-* selector may appear" — _NAV_CSS
    legitimately styles .nav-label/.nav-title/.nav-icon under exactly those
    ancestor-state and rgba rules. It bans the things that DID move out and must
    not creep back, and pins the Quasar-internal rules that must stay."""
    import main
    css = main._NAV_CSS
    assert "a.nav-link:hover" not in css           # moved to hover:bg-* utility
    assert ".help-fab {" not in css and ".help-fab{" not in css  # position moved
    assert ".nicegui-expansion-content" not in css  # no expandable sub-menus anymore
    assert ".compact-tabs .q-tab" in css           # small-padding tab strips
    assert ".q-tooltip.help-tip" in css            # teleported tooltip stays


def test_group_children_maps_routes_to_their_group():
    """The top tab strip shows the active route's group; flat + rail pages have none."""
    import main
    opts = main._group_children("/options/rescue")
    assert ("/", "Market Scanner", "radar") in opts                # Options group
    assert main._group_children("/sentiment/rotation") == main.SENTIMENT_CHILDREN
    assert main._group_children("/market") == main.SENTIMENT_CHILDREN  # folded in
    more = main._group_children("/manuals")                        # Settings child
    assert ("/eod", "EOD Report", "summarize") in more             # merged into More
    assert main._group_children("/trade") is None                  # flat page — no strip
    assert main._group_children("/driver") is None
    # Rail pages are standalone: promoted OUT of the Options tab strip.
    for route, _label, _icon in main.OPTIONS_RAIL:
        assert main._group_children(route) is None, route


def test_watcher_tick_rewarns_health_through_ttl_cache():
    """The 2s watcher must re-warm the proxy health via the TTL-gated
    cached_health, NOT the unconditional _refresh_health — otherwise every open
    tab makes a proxy HTTP GET every 2s, bypassing the memoization."""
    import inspect
    import main

    src = inspect.getsource(main._layout)
    assert "run.io_bound(cached_health)" in src
    assert "run.io_bound(_refresh_health)" not in src


def test_freshness_facts_uses_one_batched_meta_read(monkeypatch):
    """_freshness_facts runs on every 2s watcher tick — it must probe all views in
    ONE pipelined read_metas call (tiny :ver/:ts keys), never per-view read_meta
    full-payload deserializes."""
    import datetime as dt
    import main

    calls = {"metas": 0, "meta": 0}

    def fake_read_metas(views):
        calls["metas"] += 1
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        return {v: (1, now) for v in views}

    monkeypatch.setattr(main.bus_client, "read_metas", fake_read_metas)
    monkeypatch.setattr(
        main.bus_client, "read_meta",
        lambda v: (_ for _ in ()).throw(AssertionError("read_meta must not be used")))

    facts = main._freshness_facts(dt.datetime.now(dt.timezone.utc))
    assert calls["metas"] == 1
    assert set(facts) == set(main._HEALTH_VIEWS)
    assert all(v is False for v in facts.values())  # fresh ts -> not stale


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


def test_acknowledge_scanner_reads_scan_once(monkeypatch):
    """Navigating to `/` (Scanner) must read the (large) options:scan payload ONCE
    — _acknowledge reuses the read it already did for _recompute_badges instead of
    each reading it (was 2-3 reads per navigation)."""
    import main

    reads = []
    real_read = main.bus_client.read

    def tracking_read(view):
        reads.append(view)
        return {} if view == "options:scan" else real_read(view)

    monkeypatch.setattr(main.bus_client, "read", tracking_read)
    monkeypatch.setattr(main.bus_client, "read_version", lambda v: None)
    monkeypatch.setattr(main.bus_client, "read_full", lambda v: (None, None))

    main._acknowledge("/")
    assert reads.count("options:scan") == 1


def test_captured_badge_survives_reprice_but_fires_on_new_signal(monkeypatch):
    """After opening Captured, a reprice-republish (version bumps, SAME signal ids)
    must NOT re-raise the badge — that was the "keeps showing" bug (the badge was
    version-based, so every 5-min reprice re-fired it). Only a genuinely NEW
    captured signal (a new signal_id) fires it again."""
    import main

    state = {"payload": {"signals": [{"signal_id": "s1"}, {"signal_id": "s2"}]},
             "ver": 1}
    monkeypatch.setattr(main.bus_client, "read",
                        lambda v: state["payload"] if v == "options:captured" else {})
    monkeypatch.setattr(main.bus_client, "read_version",
                        lambda v: state["ver"] if v == "options:captured" else None)
    monkeypatch.setattr(main.bus_client, "read_full", lambda v: (None, None))

    # Open Captured -> acknowledge the current ids -> badge clears.
    main._acknowledge("/options/captured", scan={"signals": []})
    assert main._NAV_BADGES["/options/captured"] == 0

    # A reprice: SAME ids, version bumps 1 -> 2. Badge must STAY cleared.
    state["ver"] = 2
    main._recompute_badges(scan={"signals": []})
    assert main._NAV_BADGES["/options/captured"] == 0     # was 1 (the bug)

    # A genuinely NEW capture: new id s3, version bumps -> badge re-raises.
    state["payload"] = {"signals": [{"signal_id": "s1"}, {"signal_id": "s2"},
                                    {"signal_id": "s3"}]}
    state["ver"] = 3
    main._recompute_badges(scan={"signals": []})
    assert main._NAV_BADGES["/options/captured"] == 1


def test_acknowledge_reuses_injected_scan(monkeypatch):
    """When _layout has already read options:scan, it passes it to _acknowledge,
    which then does NOT re-read it."""
    import main

    reads = []
    real_read = main.bus_client.read

    def tracking_read(view):
        reads.append(view)
        return {} if view == "options:scan" else real_read(view)

    monkeypatch.setattr(main.bus_client, "read", tracking_read)
    monkeypatch.setattr(main.bus_client, "read_version", lambda v: None)
    monkeypatch.setattr(main.bus_client, "read_full", lambda v: (None, None))

    main._acknowledge("/", scan={"signals": []})
    assert "options:scan" not in reads


# ── Health / staleness watcher (R4b / R8 / R9) ───────────────────────────────
def _reset_health_state(main):
    main._ALERT_STATE.update(
        alerted=set(), alerted_init=None, health_alerted=set(), health_init=None,
        flow_acked=set(), flow_init=None)
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


def test_watcher_flow_alerts_seed_fire_and_toggle(monkeypatch):
    """The flow-alert branch: seed absorbs the backlog (no fire), a new id fires
    when enabled, and with the toggle off nothing fires but the acked set still
    advances (so re-enabling doesn't dump the backlog)."""
    import main

    _reset_health_state(main)
    monkeypatch.setattr(main, "_recompute_badges", lambda scan=None: None)
    monkeypatch.setattr(main, "_freshness_facts", lambda now_utc: {})
    monkeypatch.setattr(main, "_probe_services_health", lambda mono: {})

    settings = {"data": {
        "alert_enabled": True, "alert_market_hours_only": False,
        "alert_min_score": 0, "alert_sound": "chime", "alert_volume": 0.6,
        "desktop_notifications": False, "flow_alerts_enabled": True}}
    monkeypatch.setattr(main.app_settings, "load", lambda: settings["data"])

    flow = {"view": {"alerts": [{"id": "A", "type": "crossover", "side": "calls_over",
                                 "text": "seeded"}]}}
    monkeypatch.setattr(main.bus_client, "read",
                        lambda v: flow["view"] if v == "options:flow_alerts" else {})

    # Seed tick: the pre-existing alert "A" is absorbed, nothing fires.
    assert main._watcher_compute() is None
    assert main._ALERT_STATE["flow_acked"] == {"A"}

    # A new alert id "B" arrives -> flow fires, carrying the new alert dict.
    flow["view"] = {"alerts": [{"id": "A"}, {"id": "B", "type": "spike", "side": "put",
                                            "text": "IWM puts spiking"}]}
    d = main._watcher_compute()
    assert d and d["flow"] is not None
    _sound, _vol, _desk, new_flow = d["flow"]
    assert [a["id"] for a in new_flow] == ["B"]
    assert main._ALERT_STATE["flow_acked"] == {"A", "B"}

    # Toggle off: a further new id does NOT fire, but the acked set still advances.
    settings["data"]["flow_alerts_enabled"] = False
    flow["view"] = {"alerts": [{"id": "B"}, {"id": "C", "text": "off"}]}
    d2 = main._watcher_compute()
    assert d2 is None or d2.get("flow") is None
    assert "C" in main._ALERT_STATE["flow_acked"]


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
def test_breadcrumb_trail_starts_at_a_section_for_every_page():
    """The whole point of the trail (2026-08-16): the FIRST crumb is always the
    section, so the breadcrumb means the same thing wherever you are.

    It previously did not. A rail page rendered as its own bare name while a
    grouped page rendered "Group › Page", which put "Dealer Positioning" (a page)
    and "Options" (a group) in the same slot."""
    import main
    # A page inside a group → three crumbs, the full path through the menu.
    assert main.breadcrumb_trail("/") == ["Strategy", "Options", "Market Scanner"]
    assert main.breadcrumb_trail("/sentiment/rotation") == [
        "Markets", "Trend & Sentiment", "Sector Rotation"]
    assert main.breadcrumb_trail("/market") == [
        "Markets", "Trend & Sentiment", "Market Dashboard"]
    assert main.breadcrumb_trail("/eod") == ["Account", "More", "EOD Report"]
    assert main.breadcrumb_trail("/options/calculator") == [
        "Strategy", "Strategy Tools", "Calculator"]
    # A standalone rail page → two crumbs, NOT a bare page name.
    assert main.breadcrumb_trail("/options/gamma") == ["Markets", "Dealer Positioning"]
    assert main.breadcrumb_trail("/options/matrix") == ["Markets", "Opportunity Board"]
    assert main.breadcrumb_trail("/options/flow") == ["Markets", "Flow Alerts"]
    assert main.breadcrumb_trail("/trade") == ["Strategy", "Trade Analyzer"]
    assert main.breadcrumb_trail("/driver") == ["Strategy", "Claude Trades"]
    assert main.breadcrumb_trail("/portfolio") == ["Account", "Portfolio"]
    # The bottom-pinned block is not a NAV_SECTIONS caption, so it names its own.
    assert main.breadcrumb_trail("/status") == ["System", "System Status"]
    assert main.breadcrumb_trail("/settings") == ["System", "Settings"]
    assert main.breadcrumb_trail("/terminate") == ["System", "Stop All Services"]
    # Non-vacuity: every single nav route must produce a trail that STARTS with a
    # section, which is the invariant a future page is most likely to break.
    sections = {c.title() for c, _e in main.NAV_SECTIONS} | {main.SYSTEM_SECTION}
    for route in main._NAV_LABEL:
        trail = main.breadcrumb_trail(route)
        assert trail[0] in sections, f"{route} has no section: {trail}"
        assert trail[-1] == main._NAV_LABEL[route], f"{route} ends wrong: {trail}"


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


# ── manual-paper break-even lifecycle setting resync (Task 3) ───────────────
# Mirrors the ticker/captured-autoclose resync pattern: options_svc defaults
# this flag OFF on a missing key too, so a resync can't silently flip a user's
# explicit OFF back to ON — but a wiped Memurai must still be told the GUI's
# current (possibly ON) choice at startup.


def test_sync_manual_paper_lifecycle_setting_reasserts_the_flag(monkeypatch):
    import main

    sent = []
    monkeypatch.setattr(main.bus_client, "request",
                        lambda domain, cmd: sent.append((domain, cmd)))
    monkeypatch.setattr(main.app_settings, "get", lambda k: False)
    main.sync_manual_paper_lifecycle_setting()
    assert sent == [("options", {"type": "set_manual_paper_lifecycle",
                                 "args": {"enabled": False}})]

    sent.clear()
    monkeypatch.setattr(main.app_settings, "get", lambda k: True)
    main.sync_manual_paper_lifecycle_setting()
    assert sent == [("options", {"type": "set_manual_paper_lifecycle",
                                 "args": {"enabled": True}})]


def test_sync_manual_paper_lifecycle_setting_survives_a_down_bus(monkeypatch):
    import main

    def _boom(domain, cmd):
        raise RuntimeError("redis down")

    monkeypatch.setattr(main.bus_client, "request", _boom)
    monkeypatch.setattr(main.app_settings, "get", lambda k: True)
    main.sync_manual_paper_lifecycle_setting()  # startup must not fail


def test_sync_manual_paper_lifecycle_setting_registered_inside_the_main_guard():
    import inspect

    import main

    src = inspect.getsource(main)
    head, guard, tail = src.partition('if __name__ in {"__main__", "__mp_main__"}:')
    assert guard, "the __main__ guard moved — this test needs updating"
    assert "app.on_startup(" not in head
    assert "app.on_startup(sync_manual_paper_lifecycle_setting)" in tail


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


def _drawer_items():
    """(label, icon) for every item the drawer actually renders — the NAV_SECTIONS
    entries plus the SYSTEM_RAIL block at the foot."""
    import main
    items = []
    for _caption, entries in main.NAV_SECTIONS:
        for entry in entries:
            # ("group", label, icon, children) | ("page", route, label, icon)
            items.append((entry[1], entry[2]) if entry[0] == "group"
                         else (entry[2], entry[3]))
    return items + [(label, icon) for _p, label, icon in main.SYSTEM_RAIL]


def test_drawer_icons_are_present_and_distinct():
    """The drawer is a 68px icon rail (hover-to-expand) whose collapsed state shows
    ONLY icons (_NAV_CSS fades the labels to opacity:0) — so each drawer item needs
    a non-empty, distinct icon. ``_nav_link``/``_nav_group_link`` render the
    ``icon`` arg; the dot is retired. Scope is the 13 drawer items (the 10
    NAV_SECTIONS entries + the 3 SYSTEM_RAIL pages at the foot); child-page icons
    are not rail affordances (the tab strip renders labels only)."""
    from collections import Counter

    items = _drawer_items()
    # Pinned count: all()/set-length are vacuously true on an empty list, so this
    # is the non-vacuity guard. A legitimate new drawer item should bump it.
    assert len(items) == 13, f"expected 13 drawer items, got {len(items)}: {items}"
    assert not [l for l, i in items if not i], \
        f"drawer items with no icon: {[l for l, i in items if not i]}"
    dupes = {i: [l for l, x in items if x == i]
             for i, n in Counter(i for _l, i in items).items() if n > 1}
    assert not dupes, f"drawer icons collide: {dupes}"
    by_label = dict(items)
    # The two curated changes (design doc 2026-07-15).
    assert by_label["Trend & Sentiment"] == "speed"
    assert by_label["Trade Analyzer"] == "query_stats"


def test_drawer_width_pinned_vs_rail():
    """Pinned = the full menu (Quasar offsets content to match). Unpinned = the
    68px icon rail; the CSS :hover rule widens it WITHOUT changing this number,
    which is exactly why hovering overlays instead of reflowing the page.

    The literals are the point — they are the supplied design's measurements
    (2026-08-16), so a silent drift in either constant should fail here rather
    than only show up as a clipped menu in a browser."""
    import main
    assert main.drawer_width(True) == main.NAV_WIDTH_OPEN == 264
    assert main.drawer_width(False) == main.NAV_WIDTH_RAIL == 68


def test_hamburger_pins_instead_of_toggling_the_drawer():
    """The rail is always visible, so the hamburger's job changed from show/hide
    to pin/unpin — and the pin must PERSIST (it's a preference, not per-page)."""
    import inspect
    import main
    src = inspect.getsource(main._layout)
    assert "drawer.toggle" not in src, "hamburger now pins, it does not hide the rail"
    assert "_toggle_pin" in src
    assert "drawer_width(" in src, "the drawer's width comes from the pin state"
    assert 'app_settings.set("nav_pinned"' in inspect.getsource(main._toggle_pin)


def test_toggle_pin_flips_the_width_prop_the_class_and_the_setting(monkeypatch):
    """The BEHAVIOR the rail CSS depends on, not just its source text.

    test_nav_rail_css_widens_the_aside_not_the_content_div pins the CSS's premise
    — that .nav-pinned sits on the SAME element as .nav-drawer (the content div),
    so `.q-drawer:has(> .nav-drawer:not(.nav-pinned))` can opt the pinned drawer
    out of the hover rule. Nothing pinned that _toggle_pin actually puts it there:
    move the class to another element and the rail silently stops responding to
    the pin with a green suite. This closes that gap, and pins the persistence.

    NOTE: Quasar props serialize to STRINGS, so the width compares against
    str(NAV_WIDTH_*), not the int."""
    from nicegui import ui

    import main

    saved = {}
    monkeypatch.setattr(main.app_settings, "get", lambda k: saved.get(k, False))
    monkeypatch.setattr(main.app_settings, "set", saved.__setitem__)

    drawer = ui.element("div").classes("nav-drawer")

    main._toggle_pin(drawer)
    assert saved["nav_pinned"] is True, "the pin must persist — it's a preference"
    assert drawer._props["width"] == str(main.NAV_WIDTH_OPEN)
    # Same element as .nav-drawer — the :has(> .nav-drawer:not(.nav-pinned)) premise.
    assert "nav-pinned" in drawer.classes and "nav-drawer" in drawer.classes

    main._toggle_pin(drawer)
    assert saved["nav_pinned"] is False
    assert drawer._props["width"] == str(main.NAV_WIDTH_RAIL)
    assert "nav-pinned" not in drawer.classes


def test_the_retired_dot_is_gone_for_good():
    """The dot is retired repo-wide — the icon carries active state now. A negative
    source assertion is the cheap global guard against it creeping back."""
    import inspect
    import main
    assert "nav-dot" not in inspect.getsource(main)


def _nav_wrapper_of(badge):
    """The ``relative`` div ``_nav_icon`` mounts the icon + badge into."""
    return badge.parent_slot.parent


def _nav_link_of(badge):
    """The <a> ancestor of a mounted nav badge — wrapper -> row -> link. It is what
    carries .nav-active, which the icon accent is keyed off."""
    return _nav_wrapper_of(badge).parent_slot.parent.parent_slot.parent


def test_nav_link_mounts_a_dot_pair_and_registers_it_per_route():
    """A drawer row shows PRESENCE, not a count (2026-08-16): the number is
    unreadable at 68px and used to sit on top of the icon. Two dots are mounted
    because their POSITION differs by drawer state and one element cannot be
    both — the corner of the icon when collapsed, the right end of the row when
    open. Only the corner one may live inside the ``relative`` wrapper; it is
    position:absolute, so it anchors to the nearest positioned ancestor."""
    from nicegui import ui

    import main
    main._NAV_BADGES.clear()
    main._NAV_BADGES["/trade"] = 3
    main._alert_refs.clear()
    with ui.card():
        main._nav_link("/trade", "Trade Analyzer", "query_stats", "/trade")

    rail_dot, open_dot = main._alert_refs["/trade"]     # registration, per route
    assert "nav-alert-rail" in rail_dot.classes and "absolute" in rail_dot.classes
    assert "nav-alert-open" in open_dot.classes
    assert "ml-auto" in open_dot.classes, \
        "the open dot must be pushed clear of the label, to the row's right edge"
    # Presence is a CLASS, not set_visibility — the state rules in _NAV_CSS would
    # out-specify Tailwind's single-class .hidden.
    for dot in (rail_dot, open_dot):
        assert "nav-alert-on" in dot.classes
    # No number anywhere on the row: the exact count lives on the tab strip.
    assert not [c for c in open_dot.classes if c.isdigit()]

    wrapper = _nav_wrapper_of(rail_dot)                 # re-parenting guard
    assert "relative" in wrapper.classes
    icons = [c for c in wrapper.default_slot.children if isinstance(c, ui.icon)]
    assert [i._props["name"] for i in icons] == ["query_stats"], \
        "the icon is the rail's affordance and shares the corner dot's wrapper"
    assert "nav-active" in _nav_link_of(rail_dot).classes, \
        "the accent is keyed off the LINK (.nav-active .nav-icon) — it needs 3 " \
        "classes to out-specify theme's .nav-drawer .q-icon !important override"


def test_nav_link_dots_stay_off_at_zero_and_icon_idle_when_inactive():
    """The 0/inactive complement — neither dot may carry nav-alert-on, and a
    non-active item must NOT claim active state."""
    from nicegui import ui

    import main
    main._NAV_BADGES.clear()
    main._alert_refs.clear()
    with ui.card():
        main._nav_link("/trade", "Trade Analyzer", "query_stats", "/market")

    rail_dot, open_dot = main._alert_refs["/trade"]
    for dot in (rail_dot, open_dot):
        assert "nav-alert-on" not in dot.classes
    icons = [c for c in _nav_wrapper_of(rail_dot).default_slot.children
             if isinstance(c, ui.icon)]
    assert [i._props["name"] for i in icons] == ["query_stats"]
    assert "nav-active" not in _nav_link_of(rail_dot).classes


def test_set_alert_toggles_both_dots_without_stacking_classes():
    """The watcher repaints every 2 s, so the on/off write must be idempotent —
    an add-only toggle would leave nav-alert-on stuck after the count cleared."""
    from nicegui import ui

    import main
    main._NAV_BADGES.clear()
    main._alert_refs.clear()
    with ui.card():
        main._nav_link("/trade", "Trade Analyzer", "query_stats", "/market")
    dots = main._alert_refs["/trade"]

    main._set_alert(dots, 4)
    assert all("nav-alert-on" in d.classes for d in dots)
    main._set_alert(dots, 4)                     # repaint, same state
    assert all(d.classes.count("nav-alert-on") == 1 for d in dots)
    main._set_alert(dots, 0)
    assert all("nav-alert-on" not in d.classes for d in dots)


def test_nav_group_link_dot_lights_when_any_child_has_alerts():
    """A group row can only say 'something in here' — the per-page numbers are on
    the tab strip. The watcher still sums over the group's paths, so the registered
    shape stays (dots, paths)."""
    from nicegui import ui

    import main
    main._NAV_BADGES.clear()
    main._NAV_BADGES.update({"/": 2, "/options/captured": 5})
    main._group_alert_refs.clear()
    children = [("/", "Scanner", "radar"), ("/options/captured", "Captured", "bookmark")]
    with ui.card():
        main._nav_group_link("Options", "insights", children, "/")

    dots, paths = main._group_alert_refs["Options"]
    assert paths == ["/", "/options/captured"]
    assert all("nav-alert-on" in d.classes for d in dots), "2 + 5 across the paths"

    rail_dot, _open = dots
    wrapper = _nav_wrapper_of(rail_dot)
    assert "relative" in wrapper.classes
    icons = [c for c in wrapper.default_slot.children if isinstance(c, ui.icon)]
    assert [i._props["name"] for i in icons] == ["insights"]


def test_nav_rail_css_widens_the_aside_not_the_content_div():
    """.nav-drawer is NOT the <aside> — NiceGUI puts our classes on Quasar's inner
    .q-drawer__content div, while the inline width='64px' lives on the parent
    <aside class="q-drawer">. So the rail must widen the ASIDE, reached via :has();
    a rule on .nav-drawer targets a CHILD of the width-holder and can never win.
    !important is still required to beat the inline declaration."""
    import main
    css = main._NAV_CSS
    assert ".q-drawer:has(> .nav-drawer:not(.nav-pinned)):hover" in css
    assert ".nav-drawer:not(.nav-pinned):hover {" not in css, \
        "widening .nav-drawer targets the content div, not the aside's width"
    assert f"width: {main.NAV_WIDTH_OPEN}px !important" in css


def test_nav_rail_expands_on_keyboard_focus_too():
    """Labels sit at opacity:0 in the rail, so a keyboard user tabbing through the
    nav would read nothing without :focus-within."""
    import main
    css = main._NAV_CSS
    assert ".q-drawer:has(> .nav-drawer:not(.nav-pinned)):focus-within" in css
    assert ".nav-drawer:focus-within .nav-label" in css


def test_nav_active_icon_accent_outspecifies_the_menu_text_override():
    """theme.build_nav_css emits '.nav-drawer .q-icon{color:<[menu].text>!important}'
    (0,2,0) and _layout injects it AFTER _NAV_CSS, so the accent must be BOTH
    !important and 3 classes — keyed off the parent link's .nav-active. A marker
    class on the icon itself would be (0,2,0): a tie, and ties go to the later
    sheet, which is theme's."""
    import main
    from pages.options.theme import build_nav_css

    css = main._NAV_CSS
    assert ".nav-drawer .nav-active .nav-icon" in css   # 3 classes > theme's 2
    assert "!important" in css.split(".nav-drawer .nav-active .nav-icon")[1][:40]
    # Pin the claim to the REAL competing rule, not a comment about it: if the
    # [menu].text override ever gains a class, our accent must gain one too.
    rival = build_nav_css({"menu": {"header_bg": "", "drawer_bg": "", "hover_bg": "",
                                    "title": "", "text": "#98a1c0"}})
    assert ".nav-drawer .q-icon{color:#98a1c0!important;}" in rival, \
        "the rule our accent must out-specify — 2 classes, injected after _NAV_CSS"


# ── Brand identity: the NeuralStrike header lockup (2026-07-27) ─────────────
def test_brand_mark_src_requires_the_file_to_exist(tmp_path):
    """A configured mark URL is used ONLY when the asset is really on disk, so a
    missing file renders the wordmark alone instead of a broken-image icon."""
    import main

    (tmp_path / "img").mkdir()
    # Configured + present → the URL is served.
    (tmp_path / "img" / "neuralstrike-mark.png").write_bytes(b"\x89PNG\r\n")
    assert main.brand_mark_src(tmp_path) == "/static/img/neuralstrike-mark.png"
    # Configured + absent → no image (NOT a dangling src).
    (tmp_path / "img" / "neuralstrike-mark.png").unlink()
    assert main.brand_mark_src(tmp_path) == ""


def test_brand_mark_src_rejects_paths_outside_static(monkeypatch, tmp_path):
    """Only /static/ URLs map to disk; anything else degrades to no image."""
    import main
    from pages.options import theme

    for bad in ("", "   ", "https://example.com/logo.png", "../../etc/passwd"):
        monkeypatch.setattr(theme, "BRAND_MARK", bad)
        assert main.brand_mark_src(tmp_path) == "", bad


def test_brand_lockup_html_renders_both_wordmark_halves(monkeypatch, tmp_path):
    """The lockup carries the two gradient halves as separate spans (each needs
    its own background-clip:text gradient) and escapes the configured name."""
    import main
    from pages.options import theme

    monkeypatch.setattr(theme, "BRAND_MARK", "")          # no image this time
    monkeypatch.setattr(theme, "BRAND_NAME_A", "Neural")
    monkeypatch.setattr(theme, "BRAND_NAME_B", "Strike")
    out = main.brand_lockup_html(tmp_path)
    assert '<span class="a">Neural</span>' in out
    assert '<span class="b">Strike</span>' in out
    assert "brand-word" in out
    assert "<img" not in out                               # no mark configured

    # A name from config is HTML-escaped, never injected raw.
    monkeypatch.setattr(theme, "BRAND_NAME_A", "<script>x</script>")
    assert "<script>" not in main.brand_lockup_html(tmp_path)


def test_brand_lockup_includes_the_mark_when_present(tmp_path):
    """With the asset on disk the lockup leads with the logo image."""
    import main

    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "neuralstrike-mark.png").write_bytes(b"\x89PNG\r\n")
    out = main.brand_lockup_html(tmp_path)
    assert 'class="brand-mark"' in out
    assert 'src="/static/img/neuralstrike-mark.png"' in out


def test_brand_assets_are_shipped():
    """The header renders a real file, not a hopeful URL — so it must be in the
    repo. Pins BOTH the mark the header uses and the source lockup it is cropped
    from (regenerating the mark needs the source)."""
    import main

    assert (main._STATIC_DIR / "img" / "neuralstrike-mark.png").is_file()
    assert (main._STATIC_DIR / "img" / "neuralstrike-logo.jpg").is_file()


def test_app_name_comes_from_brand_config():
    """Browser titles + the breadcrumb fallback derive from [brand], so renaming
    the app is a config edit, not a code hunt."""
    import inspect

    import main
    from pages.options import theme

    assert theme.BRAND_NAME == "NeuralStrike"
    assert main.breadcrumb_trail("/no/such/route") == ["NeuralStrike"]
    src = inspect.getsource(main)
    assert "Schwab Trading" not in src, "stale app name left in main.py"


def test_dev_lockup_carries_a_dev_chip(monkeypatch, tmp_path):
    """Two identical-looking tabs writing to different paper books is a mistake
    waiting to happen — dev's header says DEV."""
    import main
    from pages.options import theme

    monkeypatch.setattr(theme, "BRAND_MARK", "")
    monkeypatch.setattr(main, "IS_DEV", True)
    assert ">DEV<" in main.brand_lockup_html(tmp_path)


def test_prod_lockup_has_no_dev_chip(monkeypatch, tmp_path):
    """Non-vacuity partner: the chip is conditional, not always painted.
    (Cannot fail if the chip is deleted — see the dev test above.)"""
    import main
    from pages.options import theme

    monkeypatch.setattr(theme, "BRAND_MARK", "")
    monkeypatch.setattr(main, "IS_DEV", False)
    assert "DEV" not in main.brand_lockup_html(tmp_path)


def test_window_title_is_prefixed_in_dev(monkeypatch):
    """The browser tab title (and so the taskbar entry) names the environment."""
    import main
    from pages.options import theme

    monkeypatch.setattr(main, "IS_DEV", True)
    assert main.window_title() == f"DEV · {theme.BRAND_NAME}"


def test_window_title_is_unchanged_in_prod(monkeypatch):
    """Non-vacuity partner: prod's title is EXACTLY the brand name, unprefixed."""
    import main
    from pages.options import theme

    monkeypatch.setattr(main, "IS_DEV", False)
    assert main.window_title() == theme.BRAND_NAME


def test_ui_run_takes_its_title_from_window_title():
    """The chip is worthless if ui.run() still hard-codes the brand name."""
    import inspect

    import main

    src = inspect.getsource(main)
    assert "title=window_title()" in src


def test_brand_css_clips_gradients_to_the_wordmark_text():
    """Each half needs -webkit-background-clip:text FIRST (Chromium still wants
    the prefix) plus a transparent fill, else the gradient paints a block."""
    from pages.options.theme import build_brand_css

    css = build_brand_css({"brand": {"font_family": "Montserrat", "font_weight": "800",
                                     "a_from": "#C9A356", "a_to": "#FBEAA0",
                                     "b_from": "#2C6FB4", "b_to": "#35A3F5"}})
    assert "-webkit-background-clip: text" in css
    assert "-webkit-text-fill-color: transparent" in css
    assert "#C9A356" in css and "#35A3F5" in css
    assert "'Montserrat'" in css
    # A blank family must not emit a stray leading comma in the font stack.
    bare = build_brand_css({"brand": {"font_family": "", "font_weight": "800",
                                      "a_from": "#1", "a_to": "#2",
                                      "b_from": "#3", "b_to": "#4"}})
    assert "font-family: 'Segoe UI'" in bare


def test_strategy_tools_group_pairs_calculator_with_simulator():
    """Calculator + Simulator are the app's two modelling tools — they share the leg
    editor, the strategy templates, the page-state snapshot and a Copy-to-each-other
    button, so they live under ONE rail item with two tabs instead of straddling two
    nav levels (Calculator was a standalone rail page, Simulator an Options tab)."""
    import main
    assert main.STRATEGY_TOOLS_CHILDREN == [
        ("/options/calculator", "Calculator", "calculate"),
        ("/options/simulator", "Simulator", "science"),
    ]
    # It is a GROUP, so both pages get the tab strip (rail pages get none).
    for route, _l, _i in main.STRATEGY_TOOLS_CHILDREN:
        assert main._group_children(route) == main.STRATEGY_TOOLS_CHILDREN, route
    # ...and the breadcrumb reads "Strategy › Strategy Tools › <page>".
    assert main.breadcrumb_trail("/options/simulator") == [
        "Strategy", "Strategy Tools", "Simulator"]


def test_strategy_tools_moved_out_of_their_old_homes():
    """Neither page may remain in its previous list, or it would render twice."""
    import main
    assert not [r for r, _l, _i in main.OPTIONS_CHILDREN if r == "/options/simulator"]
    assert not [r for r, _l, _i in main.OPTIONS_RAIL if r == "/options/calculator"]
    # The Options strip keeps its find -> analyze -> track -> repair workflow.
    assert [r for r, _l, _i in main.OPTIONS_CHILDREN] == [
        "/", "/options/swing", "/options/expected-move", "/options/captured",
        "/options/paper", "/options/portfolio", "/options/rescue"]
    # The rail keeps the standalone market-wide pages (Flow Alerts joined 2026-08-09).
    assert [r for r, _l, _i in main.OPTIONS_RAIL] == [
        "/options/gamma", "/options/matrix", "/options/flow"]


def test_strategy_tools_group_is_reachable_from_the_drawer():
    """A group only renders if _NAV_GROUPS carries it (that list drives
    _group_children + breadcrumb_parts) AND the drawer actually builds it — a
    group present in the data but never rendered is unreachable.

    Until 2026-08-16 this counted ``_nav_group_link(`` calls in ``_layout``'s
    source, which worked only while every group had its own hand-written call.
    The drawer now LOOPS over NAV_SECTIONS, so reachability is a property of that
    data instead: a group in _NAV_GROUPS but absent from NAV_SECTIONS is exactly
    the unreachable case the old count was standing in for."""
    import main
    assert any(label == "Strategy Tools" for label, _i, _c in main._NAV_GROUPS)
    placed = {e[1] for _c, entries in main.NAV_SECTIONS
              for e in entries if e[0] == "group"}
    missing = [label for label, _i, _c in main._NAV_GROUPS if label not in placed]
    assert not missing, f"groups never rendered in the drawer: {missing}"


def test_nav_sections_partition_the_rail_with_nothing_lost_or_doubled():
    """The regrouping's load-bearing guard.

    Moving ten items into three captioned lists is exactly the kind of edit that
    silently DROPS one (it renders nowhere and the page becomes reachable only by
    typing its URL) or DOUBLES one (it renders in two sections). Neither shows up
    in any other test, so assert the partition directly: NAV_SECTIONS covers every
    group and every standalone rail page, each exactly once."""
    import main

    groups = [e[1] for _c, entries in main.NAV_SECTIONS
              for e in entries if e[0] == "group"]
    pages = [e[1] for _c, entries in main.NAV_SECTIONS
             for e in entries if e[0] == "page"]

    assert sorted(groups) == sorted(l for l, _i, _c in main._NAV_GROUPS)
    assert sorted(pages) == sorted(
        p for p, _l, _i in main.OPTIONS_RAIL + main.FLAT_NAV)
    # ...each exactly once (sorted-equality above already implies it, but this
    # names the failure when it happens).
    assert len(set(groups)) == len(groups), f"a group is placed twice: {groups}"
    assert len(set(pages)) == len(pages), f"a page is placed twice: {pages}"
    # SYSTEM_RAIL is the footer block and must NOT also appear in a section.
    assert not [p for p, _l, _i in main.SYSTEM_RAIL if p in pages]


def test_nav_section_captions_and_their_derived_counts():
    """The captions are the design's three, in its order, and each count is
    DERIVED from the section's length rather than written down — a literal would
    go stale the first time a page moved."""
    import inspect
    import main
    assert [c for c, _e in main.NAV_SECTIONS] == ["MARKETS", "STRATEGY", "ACCOUNT"]
    assert [len(e) for _c, e in main.NAV_SECTIONS] == [4, 4, 2]
    # The renderer takes the count as an argument; the drawer passes len(entries).
    src = inspect.getsource(main._layout)
    assert "_nav_section_header(caption, len(entries), first=(_i == 0))" in src


def test_only_the_first_section_header_skips_the_separating_gap():
    """The gap is what groups the rail — with 2px between rows, a 26px caption box
    alone did not read as a break and STRATEGY looked like an eleventh item. It
    must NOT be applied above the first caption, where it would just push the whole
    menu down."""
    from nicegui import ui

    import main
    with ui.card():
        first = ui.element("div")
        with first:
            main._nav_section_header("MARKETS", 4, first=True)
        later = ui.element("div")
        with later:
            main._nav_section_header("STRATEGY", 4)

    assert "mt-4" not in first.default_slot.children[0].classes
    assert "mt-4" in later.default_slot.children[0].classes


def test_sec_helpers_refuse_an_unknown_group_or_route():
    """NAV_SECTIONS references items by name, so a typo must fail at IMPORT rather
    than quietly leave a page out of the menu."""
    import pytest

    import main
    with pytest.raises(KeyError):
        main._sec_group("No Such Group")
    with pytest.raises(KeyError):
        main._sec_page("/no/such/route")


def test_stop_all_services_is_a_danger_button_and_sits_last():
    """The one irreversible item in the rail must not look like — or sit among —
    the navigation rows it neighbours."""
    import inspect
    import main
    assert main.SYSTEM_RAIL[-1][0] == main.SYSTEM_DANGER_ROUTE == "/terminate"
    # Settings must come BEFORE it: aiming for Settings and overshooting should
    # not land on "stop the whole stack".
    assert [p for p, _l, _i in main.SYSTEM_RAIL] == [
        "/status", "/settings", "/terminate"]
    src = inspect.getsource(main._layout)
    assert "_nav_danger_link(" in src, "the danger route gets its own renderer"
    # It claims no active state (a navy active wash under a rose outline reads as
    # a rendering bug) and carries no dot.
    danger = inspect.getsource(main._nav_danger_link)
    assert "nav-active" not in danger and "_alert_dot" not in danger


def test_stop_all_services_lines_up_with_every_other_drawer_row():
    """It is a nav row in LAYOUT and a danger button only in COLOUR. A footer whose
    three rows start at three different x-positions reads as broken rather than as
    emphasis, so the icon and label must sit on the shared columns: the same px-3
    link padding, the same 24px icon box, the same gap-3."""
    from nicegui import ui

    import main
    main._NAV_BADGES.clear()
    main._alert_refs.clear()
    with ui.card():
        plain = ui.element("div")
        with plain:
            main._nav_link("/settings", "Settings", "settings", "/")
        danger = ui.element("div")
        with danger:
            main._nav_danger_link("/terminate", "Stop All Services", "power")

    plain_link = plain.default_slot.children[0]
    danger_link = danger.default_slot.children[0]
    for cls in ("py-1", "rounded-[10px]", "items-center"):
        assert cls in plain_link.classes and cls in danger_link.classes, cls
    # box-sizing is border-box, so the 1px outline eats into the padding box —
    # 11 + 1 lands the glyph on the same column as the plain rows' 12.
    assert "px-3" in plain_link.classes
    assert "px-[11px]" in danger_link.classes
    # Centring is what threw the alignment off; it must not come back.
    assert "justify-center" not in danger_link.classes

    def _icon_box(link):
        row = link.default_slot.children[-1]
        return row.default_slot.children[0]

    assert "gap-3" in plain_link.default_slot.children[-1].classes
    assert "gap-3" in danger_link.default_slot.children[-1].classes
    # Same 24px box, so the two glyphs land on the same column...
    for cls in ("w-6", "h-6", "flex-none"):
        assert cls in _icon_box(plain_link).classes and cls in _icon_box(danger_link).classes

    # ...and the same glyph SIZE, or a smaller one centres inside that box 1px
    # off, which at this scale reads as a misalignment rather than a weight.
    def _glyph(box):
        return [c for c in box.default_slot.children if isinstance(c, ui.icon)][0]

    assert "text-[20px]" in _glyph(_icon_box(plain_link)).classes
    assert "text-[20px]" in _glyph(_icon_box(danger_link)).classes


def test_the_nav_column_never_wraps():
    """A latent bug the section gaps finally tripped: NiceGUI's column wraps and
    `h-full` caps the height, so once the items are taller than the drawer the
    overflow forms a SECOND COLUMN at 16 + 35 + 2 = 53px rather than scrolling.
    Measured: Stop All Services alone jumped to x=53 while every other row sat at
    16, and collapsed, its corner dot landed at x=82 — outside the 68px rail.
    Nothing about the row itself was wrong, which is what made it hard to find."""
    import inspect
    import main
    src = inspect.getsource(main._layout)
    assert "flex-nowrap" in src, \
        "the nav column must not wrap, or a tall menu breaks into two columns"


def test_window_title_tags_the_PAGE_title_not_just_the_default(monkeypatch):
    """The per-page title is what a browser tab actually shows.

    `_layout` calls `ui.page_title` on every page, which OVERRIDES the
    `ui.run(title=...)` default — so tagging only the ui.run title left every
    real page reading "Market Scanner" in BOTH environments. Caught in a live
    browser (document.title), not by the original test, which only checked that
    `ui.run` was handed `window_title()`.
    """
    import main

    monkeypatch.setattr(main, "IS_DEV", True)
    assert main.window_title("Market Scanner") == "DEV · Market Scanner"
    monkeypatch.setattr(main, "IS_DEV", False)
    assert main.window_title("Market Scanner") == "Market Scanner"


def test_layout_routes_the_page_title_through_window_title():
    """Source inspection: `_layout` runs per-request and cannot execute here.

    Without this, a future edit could set `ui.page_title(...)` directly again
    and silently restore the untagged-tab bug.
    """
    import inspect

    import main

    src = inspect.getsource(main._layout)
    assert "ui.page_title(window_title(" in src, (
        "_layout must pass the page label through window_title(), or dev tabs "
        "lose their prefix again"
    )


# ── Footer service-status card (2026-08-16) ─────────────────────────────────
def test_status_card_facts_reads_live_when_everything_is_up():
    import main
    f = main.status_card_facts(
        {"options": True, "sentiment": True, "market": True}, 0, 42.4)
    assert f["tone"] == "live"
    assert f["title"] == "Data feed live"
    assert f["detail"] == "3 services · 42 ms"
    assert f["count"] == 0


def test_status_card_facts_degrades_and_shows_the_up_fraction():
    """A degraded feed says so in three places at once — tone, title and the
    up/total fraction — because the dot is the only one of them a glance reads."""
    import main
    f = main.status_card_facts(
        {"options": True, "sentiment": False, "market": None}, 2, 8.0)
    assert f["tone"] == "warn"
    assert f["title"] == "Data feed degraded"
    assert f["detail"] == "1/3 services · 8 ms"
    assert f["count"] == 2


def test_status_card_reports_unknown_rather_than_live_without_a_probe():
    """The defect class this card had to be designed around: a defensive default
    that RENDERS as a confident measurement. No probe data must never come out as
    'Data feed live' — nor as a reassuring zero-warning green dot."""
    import main
    for empty in ({}, None):
        f = main.status_card_facts(empty, 0, 42.0)
        assert f["tone"] == "unknown"
        assert f["title"] == "Data feed unknown"
        assert f["detail"] == "no probe yet", "no latency is claimed either"
        assert f["count"] == 0
    # The seeded module state must start there too — the first paint of a page
    # happens before any tick has run.
    assert main._STATUS_CARD["tone"] == "unknown"


def test_status_card_omits_latency_it_could_not_measure():
    """None (every probed service timed out, so nothing was timed) and a garbage
    value both drop the figure rather than rendering 'None ms' or raising."""
    import main
    assert main.status_card_facts({"options": True}, 0, None)["detail"] == "1 services"
    assert main.status_card_facts({"options": True}, 0, "nope")["detail"] == "1 services"


def test_status_card_count_is_the_same_number_as_the_system_status_badge(monkeypatch):
    """Not merely equal today — the SAME computation. Two independent counts of
    'what is unhealthy' would eventually disagree, and the card would quietly
    contradict the badge three rows below it."""
    import main

    _reset_health_state(main)
    monkeypatch.setattr(main.bus_client, "read", lambda v: {})
    monkeypatch.setattr(main, "_recompute_badges", lambda scan=None: None)
    monkeypatch.setattr(main.app_settings, "load", lambda: {
        "alert_enabled": False, "alert_market_hours_only": False,
        "alert_min_score": 0, "alert_sound": "chime", "alert_volume": 0.6,
        "desktop_notifications": False})
    monkeypatch.setattr(main, "_freshness_facts", lambda now_utc: {})
    health = {"data": {"options": True, "sentiment": True}}
    monkeypatch.setattr(main, "_probe_services_health", lambda mono: health["data"])

    main._watcher_compute()                       # seed
    assert main._STATUS_CARD["count"] == main._NAV_BADGES["/status"] == 0
    assert main._STATUS_CARD["tone"] == "live"

    health["data"] = {"options": False, "sentiment": True}
    main._watcher_compute()
    assert main._STATUS_CARD["count"] == main._NAV_BADGES["/status"] == 1
    assert main._STATUS_CARD["tone"] == "warn"


def test_guarded_compute_drops_the_card_to_unknown_on_a_bus_outage(monkeypatch):
    """When the tick dies the card must not keep displaying its last good
    reading: 'Data feed live' over a dead backbone is the worst thing it could
    say, and it would say it indefinitely."""
    import main

    main._STATUS_CARD.update(main.status_card_facts({"options": True}, 0, 5.0))
    assert main._STATUS_CARD["tone"] == "live"

    def boom():
        raise RuntimeError("bus down")

    monkeypatch.setattr(main, "_watcher_compute", boom)
    assert main._guarded_compute() is None
    assert main._STATUS_CARD["tone"] == "unknown"


def test_probe_records_the_latency_of_services_that_answered(monkeypatch):
    """A timed-out service contributes the full HTTP timeout, which would turn the
    reported latency into a description of the failure rather than of the feed —
    saying which service is down is the count badge's job."""
    import main

    class _Resp:
        status_code = 200

        def json(self):
            return {"up": True}

    calls = {"n": 0}

    def fake_get(url, timeout=None):
        calls["n"] += 1
        if "8210" in url or calls["n"] % 2 == 0:
            raise OSError("refused")
        return _Resp()

    monkeypatch.setitem(__import__("sys").modules, "requests",
                        type("M", (), {"get": staticmethod(fake_get)}))
    main._svc_health_cache.update({"data": {}, "ts": 0.0, "latency_ms": None})
    main._probe_services_health(10_000.0)
    lat = main._svc_health_cache["latency_ms"]
    assert lat is None or lat < main._HEALTH_HTTP_TIMEOUT * 1000, \
        "a refused/timed-out probe must not be counted into the mean"


# ── Collapsed-rail section dividers + the static AI pill ────────────────────
def test_section_captions_swap_for_hairlines_in_the_collapsed_rail():
    """A caption is unreadable at 68px, so .nav-sep is the exact INVERSE of the
    .nav-title fade — visible by default, hidden under the same three 'drawer is
    open' selectors. Both rules must exist or the rail shows captions clipped to
    four letters (only .nav-title present) or nothing at all (only .nav-sep)."""
    import main
    css = main._NAV_CSS
    assert ".nav-drawer .nav-sep { opacity: 1;" in css
    for sel in ("nav-drawer.nav-pinned .nav-sep", ".nav-drawer:hover .nav-sep",
                ".nav-drawer:focus-within .nav-sep"):
        assert sel in css, f"the collapsed-rail divider never hides for {sel}"


def test_status_card_keeps_its_dot_when_the_rail_is_collapsed():
    """The card survives the collapse — the feed's health is the one footer fact
    worth seeing without opening anything — but only its DOT fits at 68px.

    So the CARD itself is never display:none (an earlier build dropped the whole
    thing and the rail said nothing about the feed at all); the text column and
    the count are what disappear, via display so they surrender their width
    rather than pushing the dot out of the rail."""
    import inspect

    import main
    css = main._NAV_CSS
    assert ".nav-drawer .nav-status-card { display: none; }" not in css, \
        "the card must stay visible in the rail; only its text collapses"
    assert ".nav-drawer .nav-status-text, .nav-drawer .nav-status-count " \
           "{ display: none; }" in css
    assert ".nav-drawer:hover .nav-status-text," in css
    # The dot rides the shared 24px icon box, so with the text gone it still
    # lines up with the icons above rather than floating mid-rail.
    src = inspect.getsource(main._status_card)
    assert "flex-none w-6 h-6" in src and "px-[11px]" in src


def test_status_count_presence_is_a_class_not_set_visibility():
    """Same specificity trap as the alert dots: NiceGUI's set_visibility toggles
    the single-class .hidden, which the two-class state rules out-specify — a zero
    count would pop back into view the moment the drawer opened."""
    import inspect

    import main
    css = main._NAV_CSS
    assert ".nav-status-count.nav-status-count-on" in css
    for fn in (main._status_card, main._apply_status_card):
        # ".set_visibility(" — the CALL, so the comment explaining why we avoid it
        # doesn't fail its own test.
        assert ".set_visibility(" not in inspect.getsource(fn)
    assert "nav-status-count-on" in inspect.getsource(main._apply_status_card)


def test_claude_trades_carries_a_static_ai_pill_that_the_watcher_never_touches():
    """The AI marker is a fixed label, not watcher state, so it must NOT be
    registered in _alert_refs — the 2s tick would otherwise blank it on the first
    pass (there is no _NAV_BADGES entry to write back)."""
    from nicegui import ui

    import main
    assert main._NAV_PILLS == {"/driver": "AI"}
    main._NAV_BADGES.clear()
    main._alert_refs.clear()
    with ui.card():
        main._nav_link("/driver", "Claude Trades", "smart_toy", "/")

    rail_dot, _open = main._alert_refs["/driver"]
    labels = [c for c in _nav_wrapper_of(rail_dot).parent_slot.parent
              .default_slot.children if isinstance(c, ui.label)]
    assert [l.text for l in labels] == ["Claude Trades", "AI"]
    # The pill rides the label fade, which is what keeps it out of the 68px rail,
    # but takes its COLOUR from .nav-pill — see the specificity test below.
    assert "nav-label" in labels[1].classes and "nav-pill" in labels[1].classes


def test_rail_colours_outspecify_the_menu_text_and_active_overrides():
    """Three colours in the rail have to WIN a specificity fight, and all three
    lost when first written — caught in a live browser, not by any test here.

    ``theme.build_nav_css`` emits ``.nav-drawer a{color:<[menu].text>!important}``
    and ``_NAV_CSS`` itself emits ``.nav-drawer .nav-active .nav-label`` (3
    classes). A Tailwind ``text-[#…]`` utility is ONE class with no !important, so
    it loses to both: the danger button rendered in menu grey rather than rose,
    and the AI pill turned white on the one row it ever appears on — the active
    one. Measured: rgb(152,161,192) and rgb(238,241,246) where rose and blue were
    intended.

    The fix is a rule per case, each !important and at least 3 classes. This test
    pins the SHAPE, since the failure is invisible to a DOM-free assertion."""
    import inspect
    import re

    import main
    css = main._NAV_CSS
    for sel in (".nav-drawer .nav-danger .nav-icon",
                ".nav-drawer .nav-danger .nav-label",
                ".nav-drawer .nav-pill"):
        assert sel in css, f"{sel} has no colour rule and will inherit menu grey"
    # Each of those rules must carry !important — without it the [menu].text
    # override wins regardless of class count.
    for block in re.findall(r"\.nav-drawer \.nav-(?:danger|pill)[^{]*\{([^}]*)\}", css):
        assert "!important" in block, f"rule loses to [menu].text: {{{block}}}"
    # And the pill must NOT be coloured by a Tailwind utility any more, or the
    # active-row regression silently returns.
    assert "text-[#4da3ff]" not in inspect.getsource(main._nav_link)
