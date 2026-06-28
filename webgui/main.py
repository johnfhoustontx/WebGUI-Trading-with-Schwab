"""NiceGUI multi-page shell for the Trading With Schwab webgui app.

A single NiceGUI server with a left-nav per feature. Each feature gets its own
page route; in Phase 2 the page bodies are stubs that Phase 3 replaces with the
ported engines. A proxy-down banner is shown on every page when the
schwab-proxy (:8100) is unreachable.

Run order: start schwab-proxy first, then ``python webgui/main.py``.
"""
import pathlib
import sys
from contextlib import contextmanager

# Runtime sys.path glue (mirrors conftest for non-test execution).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "webgui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi.responses import HTMLResponse  # noqa: E402
from nicegui import app, run, ui  # noqa: E402

import datetime as _dt  # noqa: E402
import time as _time  # noqa: E402
from zoneinfo import ZoneInfo as _ZoneInfo  # noqa: E402

import alerts  # noqa: E402
import app_settings  # noqa: E402
import bus_client  # noqa: E402
import page_help  # noqa: E402
import proxy  # noqa: E402
from repo_paths import NICEGUI_PORT  # noqa: E402

# Serve bundled static assets (alert sounds) at /static.
_STATIC_DIR = _REPO_ROOT / "webgui" / "static"
if _STATIC_DIR.is_dir():
    app.add_static_files("/static", str(_STATIC_DIR))

_CT = _ZoneInfo("America/Chicago")


def play_alert(sound: str, volume: float) -> None:
    """Play a bundled alert WAV in the connected browser at the given volume."""
    sound = sound if sound in ("chime", "bell", "ping") else "chime"
    vol = max(0.0, min(1.0, float(volume if volume is not None else 0.6)))
    ui.run_javascript(
        f"(() => {{ const a = document.getElementById('alert-audio'); if (!a) return; "
        f"a.src = '/static/sounds/{sound}.wav'; a.volume = {vol}; "
        f"a.play().catch(() => {{}}); }})()")


def notify_desktop(title: str, body: str) -> None:
    """Fire a desktop Notification if permission was granted (best-effort)."""
    safe = body.replace("'", "\\'")
    ui.run_javascript(
        "(() => { if (window.Notification && Notification.permission === 'granted') "
        f"new Notification('{title}', {{ body: '{safe}' }}); }})()")

_EXPLAIN_EMPTY = (
    "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>Gamma Explain</title></head>"
    "<body style=\"font-family:system-ui,sans-serif;background:#0c0f15;color:#e9edf3;padding:40px;\">"
    "<h3>No Gamma Explain generated yet</h3>"
    "<p>Open the Gamma page and click <b>Explain</b> first.</p></body></html>")


def explain_html(payload):
    """Standalone infographic HTML from the cached gamma_explain payload (or a
    friendly placeholder)."""
    html = (payload or {}).get("html")
    return html if isinstance(html, str) and html.strip() else _EXPLAIN_EMPTY


@app.get("/options/explain")
def _serve_explain():
    """Serve the latest Gamma Explain infographic as a raw standalone page.

    Reads straight from the Redis bus cache (written by options_svc) and returns
    a raw HTMLResponse so the document's own <style>/fonts apply — NiceGUI's
    ``ui.html`` would strip them. Opened in a new browser tab from the Gamma page.
    """
    import bus_client
    return HTMLResponse(explain_html(bus_client.read("options:gamma_explain")))


_ANALYZE_EMPTY = (
    "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>Gamma Analysis</title></head>"
    "<body style=\"font-family:system-ui,sans-serif;background:#0c0f15;color:#e9edf3;padding:40px;\">"
    "<h3>No Gamma analysis generated yet</h3>"
    "<p>Open the Gamma page and click <b>Analyze</b> first.</p></body></html>")


def analyze_html(payload):
    """Standalone analysis HTML from the cached gamma_analyze payload (or a
    friendly placeholder)."""
    html = (payload or {}).get("html")
    return html if isinstance(html, str) and html.strip() else _ANALYZE_EMPTY


# Scheduled-briefing views (one per daily slot), kept separate from the ad-hoc
# ``options:gamma_analyze`` key so a scheduled run never auto-opens a tab. Mirror of
# handlers.CACHE_GAMMA_ANALYZE_SCHED (Tier-1 can't import the service, so the view
# names are listed here). ``/options/analyze?slot=<slot>`` serves the matching one;
# no/unknown slot → the ad-hoc Analyze result.
_ANALYZE_SLOT_VIEWS = {
    "premarket": "options:gamma_analyze_premarket",
    "open": "options:gamma_analyze_open",
    "midday": "options:gamma_analyze_midday",
    "close": "options:gamma_analyze_close",
}


def analyze_view_for(slot):
    """Bus view name for a scheduled ``slot``, or the ad-hoc view for None/unknown."""
    return _ANALYZE_SLOT_VIEWS.get(slot, "options:gamma_analyze")


@app.get("/options/analyze")
def _serve_analyze(slot: str = None):
    """Serve a Gamma Analysis (Claude-written) as a raw standalone page.

    Mirrors ``_serve_explain``: reads the Redis bus cache written by options_svc and
    returns a raw HTMLResponse so the document's own <style> applies. With no ``slot``
    it serves the ad-hoc Analyze result (``gamma_analyze`` command); ``?slot=premarket
    |open|midday|close`` serves that day's auto-generated briefing. Opened in a new
    browser tab from the Gamma page.
    """
    import bus_client
    return HTMLResponse(analyze_html(bus_client.read(analyze_view_for(slot))))


@app.get("/eod/file")
def _serve_eod_file(date: str, which: str = "summary"):
    """Serve an archived EOD report file (summary.html / detail.html) raw, so its
    own <style> applies — NiceGUI's ``ui.html`` would strip it."""
    import re
    from pathlib import Path

    from pages import eod
    if which not in ("summary", "detail") or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    path = Path(eod.ARCHIVE_ROOT) / date / f"{which}.html"
    if not path.is_file():
        return HTMLResponse(
            "<h1>No report for that date — click Generate first.</h1>",
            status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/manuals/file")
def _serve_manual(name: str):
    """Serve a generated online manual (self-contained HTML) raw, so its own
    embedded <style> applies — NiceGUI's ``ui.html`` would strip it. ``name`` is a
    whitelist key into ``pages.manuals.MANUALS`` (no path traversal)."""
    from pages import manuals
    entry = manuals.MANUALS.get(name)
    if entry is None:
        return HTMLResponse("<h1>Unknown manual</h1>", status_code=404)
    path = _REPO_ROOT / "docs" / "manuals" / entry["file"]
    if not path.is_file():
        return HTMLResponse(
            "<h1>Manual not built yet — run docs/manuals/build_docs.py.</h1>",
            status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


# Options scanning + auto-scan now live in services/options_svc (Tier 2): the
# service owns the engine and the 08:00–15:15 CT schedule, writes results to the
# Redis bus, and the GUI reads the cache + enqueues rescan commands. Sentiment
# refresh likewise lives in services/sentiment_svc.

# Options is an expandable group; each child is its own route. (route, label, icon)
OPTIONS_CHILDREN = [
    ("/", "Scanner", "radar"),
    ("/options/paper", "Paper Trades", "request_quote"),
    ("/options/captured", "Captured Signals", "bookmark"),
    ("/options/portfolio", "Paper Portfolio", "account_balance_wallet"),
    ("/options/calculator", "Calculator", "calculate"),
    ("/options/swing", "Swing Scanner", "swap_vert"),
    ("/options/gamma", "Gamma", "stacked_line_chart"),
    ("/options/simulator", "Simulator", "science"),
    ("/options/expected-move", "Expected Move", "candlestick_chart"),
    ("/options/rescue", "Rescue", "healing"),
]

# Sentiment is an expandable group; each child is its own route. (route, label, icon)
SENTIMENT_CHILDREN = [
    ("/sentiment", "Sentiment", "insights"),
    ("/sentiment/rotation", "Sector Rotation", "donut_large"),
]

# Flat top-level items (non-Options apps). (route, label, icon)
FLAT_NAV = [
    ("/trade", "Trade", "analytics"),
    ("/portfolio", "Portfolio", "account_balance"),
    ("/driver", "Driver", "smart_toy"),
]

# "More" is an expandable group for reports / diagnostics / config. (route, label, icon)
# Settings is itself a nested sub-group (its children render indented beneath it).
MORE_CHILDREN = [
    ("/eod", "EOD Report", "summarize"),
    ("/status", "System Status", "monitor_heart"),
    ("/settings", "Settings", "settings"),
    ("/terminate", "Terminate", "power_settings_new"),
]

# Sub-menu items nested under the Settings entry. (route, label, icon)
SETTINGS_CHILDREN = [
    ("/manuals", "User Manuals", "menu_book"),
]

# Persisted left-nav expansion state (single-user); None/absent = use active-route default.
_NAV_OPEN: dict[str, bool] = {}

# Single-user nav-badge state (mirrors _NAV_OPEN). _NAV_BADGES holds route->count;
# _ALERT_STATE tracks what's been acknowledged/alerted so we badge/chime only on
# genuinely new items. _badge_refs maps route->badge element for the current page.
_NAV_BADGES: dict[str, int] = {}
_ALERT_STATE: dict = {
    "acked_scan": set(), "alerted": set(), "alerted_init": None,
    "captured_seen": None, "driver_seen": None, "rescue_seen": None,
}
_badge_refs: dict = {}

# proxy.health() is shown as a down-banner on EVERY page build. The call is a
# blocking HTTP GET with a 3s timeout — without caching, every navigation paid it
# before first paint (and stalled up to 3s if the proxy was unreachable). Memoize
# it for a few seconds across pages; the 2s watcher tick re-warms it off-thread.
_HEALTH_TTL_SEC = 4.0
_health_cache: dict = {"data": None, "ts": 0.0}


def cached_health() -> dict:
    """proxy.health() memoized for _HEALTH_TTL_SEC across page builds (never raises)."""
    now = _time.monotonic()
    if _health_cache["data"] is None or now - _health_cache["ts"] >= _HEALTH_TTL_SEC:
        _refresh_health()
    return _health_cache["data"]


def _refresh_health() -> None:
    """Probe the proxy and update the health cache (blocking — run off-thread)."""
    _health_cache["data"] = proxy.health()
    _health_cache["ts"] = _time.monotonic()

# Modernized drawer styling (scoped to .nav-drawer).
# Inter-item spacing is intentionally tight (~50% of Quasar's defaults): the
# flex gap (4px→2px), the nav-link vertical padding (8px→4px), and the expansion
# header min-height (48px→24px) are all halved so the menu reads denser.
_NAV_CSS = """
.nav-drawer { gap: 2px; }
/* Children INSIDE each expandable group also stack tight — NiceGUI wraps the
   expansion body in a flex column (.nicegui-expansion-content) that defaults to a
   1rem/16px gap. */
.nav-drawer .nicegui-expansion-content { gap: 2px; }
.nav-drawer .q-item { border-radius: 10px; }
.nav-drawer .q-expansion-item .q-item { min-height: 24px; }
.nav-drawer .nav-subgroup .q-expansion-item__content { padding-left: 14px; }
/* Page-help "?" — tucked into the bottom-right corner of the header banner.
   Positioning is on the element (Tailwind); these stay Quasar-internal. */
.help-fab .help-btn { font-size: 11px; min-height: 0; min-width: 0; }
.help-fab .help-btn .q-btn__content { padding: 3px; }
.q-tooltip.help-tip { background: #1e2735; color: #e7edf5; font-size: .82rem;
    line-height: 1.5; padding: 12px 16px; border: 1px solid rgba(255,255,255,.14);
    border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,.45); }
.q-tooltip.help-tip strong { color: #fff; }
.q-tooltip.help-tip p { margin: .4em 0; }
.q-tooltip.help-tip ul { padding-left: 1.15em; margin: .35em 0; }
.q-tooltip.help-tip li { margin: .2em 0; }
"""


def _acknowledge(active: str) -> None:
    """Clear the badge for the page being viewed."""
    if active == "/":                                   # Scanner
        scan = bus_client.read("options:scan") or {}
        _ALERT_STATE["acked_scan"] = alerts.scanner_keys(scan)
    elif active == "/options/captured":
        _ALERT_STATE["captured_seen"] = bus_client.read_version("options:captured")
    elif active == "/driver":
        _ALERT_STATE["driver_seen"] = bus_client.read_version("driver:approvals")
    elif active == "/options/rescue":
        # Acknowledge the current rescue-summary version so the badge clears on
        # open and only re-appears when the manage cycle publishes a new summary.
        _ALERT_STATE["rescue_seen"] = bus_client.read_version("options:rescue_summary")
    _recompute_badges()


def _recompute_badges(scan=None) -> None:
    """Refresh _NAV_BADGES from the current bus state (idempotent).

    ``scan`` may be passed in by a caller that already read ``options:scan`` this
    tick, so the (potentially large) scan payload isn't deserialized twice.
    """
    if scan is None:
        scan = bus_client.read("options:scan") or {}
    _NAV_BADGES["/"] = alerts.unread_count(
        alerts.scanner_keys(scan), _ALERT_STATE["acked_scan"])
    cap_ver = bus_client.read_version("options:captured")  # cheap :ver probe
    _NAV_BADGES["/options/captured"] = 1 if (
        cap_ver is not None and cap_ver != _ALERT_STATE["captured_seen"]) else 0
    drv, drv_ver = bus_client.read_full("driver:approvals")  # payload+version, one read
    drv = drv or {}
    _NAV_BADGES["/driver"] = 1 if (
        drv.get("status") == "pending" and drv_ver != _ALERT_STATE["driver_seen"]) else 0
    # Rescue: count of at-risk paper positions (tested + critical) from the small
    # rescue_summary view. Cleared on open (version acknowledged), so the count
    # only re-appears when the manage cycle republishes a changed summary.
    rescue, rescue_ver = bus_client.read_full("options:rescue_summary")
    rescue = rescue or {}
    n_rescue = int(rescue.get("n_tested", 0) or 0) + int(rescue.get("n_critical", 0) or 0)
    _NAV_BADGES["/options/rescue"] = n_rescue if (
        n_rescue and rescue_ver != _ALERT_STATE["rescue_seen"]) else 0


def _watcher_compute():
    """Off-thread part of a watcher tick: read the bus once, recompute badges, and
    DECIDE whether to alert. Returns an alert tuple ``(sound, volume, desktop, n)``
    or None. Does NO UI work (safe to run via ``run.io_bound``) — the caller fires
    the chime/notification on the UI thread.
    """
    scan = bus_client.read("options:scan") or {}   # read ONCE; passed to badges below
    keys = alerts.scanner_keys(scan)
    # Seed on the first tick so pre-existing signals don't alert on launch.
    if _ALERT_STATE["alerted_init"] is None:
        _ALERT_STATE["alerted"] = keys
        _ALERT_STATE["alerted_init"] = True
        _recompute_badges(scan)
        return None
    _recompute_badges(scan)
    s = app_settings.load()                        # in-memory cached (no disk hit)
    q = alerts.qualifying_new(scan, _ALERT_STATE["alerted"], s["alert_min_score"])
    now = _dt.datetime.now(tz=_CT)
    decision = None
    if alerts.should_alert(s, q, now):
        decision = (s["alert_sound"], s["alert_volume"],
                    bool(s.get("desktop_notifications")), len(q))
    # Mark everything currently present as alerted so each signal chimes once.
    _ALERT_STATE["alerted"] |= keys
    return decision


def _nav_link(path: str, label: str, icon: str, active: str) -> None:
    base = ("w-full no-underline items-center rounded-[10px] px-3 py-1 "
            "transition-colors hover:bg-white/[0.06]")
    state = " bg-[var(--q-primary)] text-white" if path == active else ""
    with ui.link(target=path).classes(base + state):
        with ui.row().classes("items-center gap-3 w-full no-wrap"):
            ui.icon(icon).classes("text-xl opacity-90")
            ui.label(label)
            n = _NAV_BADGES.get(path, 0)
            badge = ui.badge(str(n) if n else "").classes("ml-auto").props("color=red rounded")
            badge.set_visibility(bool(n))
            _badge_refs[path] = badge


def _settings_group(active: str) -> None:
    """Render Settings as a nested sub-group: the header is the real /settings nav
    link (native navigation), and only the caret toggles the sub-menu, beneath
    which SETTINGS_CHILDREN (e.g. User Manuals) render indented.

    ``expand-icon-toggle`` confines the toggle to the caret so a header click
    follows the link instead of expanding.
    """
    exp = ui.expansion(
        value=_NAV_OPEN.get("Settings", True)
    ).classes("w-full nav-subgroup").props("expand-icon-toggle dense")
    exp.on_value_change(lambda e: _NAV_OPEN.__setitem__("Settings", e.value))
    with exp.add_slot("header"):
        _nav_link("/settings", "Settings", "settings", active)
    with exp:
        for path, label, icon in SETTINGS_CHILDREN:
            _nav_link(path, label, icon, active)


@contextmanager
def _layout(active: str, title: str):
    """Render shared chrome (header, nav drawer, proxy banner).

    Yields the content container the page should populate.
    """
    # Drawer starts open and is toggleable via the header menu button, so the
    # nav stays reachable at any viewport width (Quasar hides it as an overlay
    # below the layout breakpoint otherwise).
    _badge_refs.clear()
    _recompute_badges()
    ui.add_css(_NAV_CSS)
    drawer = ui.left_drawer(value=True, bordered=True).classes("nav-drawer").props("behavior=desktop")
    with drawer:
        ui.label("SCHWAB TRADING").classes(
            "font-bold tracking-[.04em] text-[.8rem] px-3 pt-1 pb-1.5 opacity-55")
        # Groups start EXPANDED by default (value=True) and stay open until the user
        # manually collapses one — _NAV_OPEN persists each manual toggle (single-user,
        # like the badges), so a collapse sticks across navigation.
        options_exp = ui.expansion(
            "Options", icon="candlestick_chart", value=_NAV_OPEN.get("Options", True)
        ).classes("w-full")
        options_exp.on_value_change(lambda e: _NAV_OPEN.__setitem__("Options", e.value))
        with options_exp:
            for path, label, icon in OPTIONS_CHILDREN:
                _nav_link(path, label, icon, active)
        sentiment_exp = ui.expansion(
            "Sentiment", icon="insights", value=_NAV_OPEN.get("Sentiment", True)
        ).classes("w-full")
        sentiment_exp.on_value_change(lambda e: _NAV_OPEN.__setitem__("Sentiment", e.value))
        with sentiment_exp:
            for path, label, icon in SENTIMENT_CHILDREN:
                _nav_link(path, label, icon, active)
        for path, label, icon in FLAT_NAV:
            _nav_link(path, label, icon, active)
        more_exp = ui.expansion(
            "More", icon="more_horiz", value=_NAV_OPEN.get("More", True)
        ).classes("w-full")
        more_exp.on_value_change(lambda e: _NAV_OPEN.__setitem__("More", e.value))
        with more_exp:
            for path, label, icon in MORE_CHILDREN:
                if path == "/settings":
                    _settings_group(active)
                else:
                    _nav_link(path, label, icon, active)

    with ui.header().classes("items-center justify-between"):
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat round color=white")
            ui.label("Schwab Trading").classes("text-lg font-bold")
        ui.label(title).classes("text-base opacity-80")

        # Small "?" tucked into the bottom-right corner of the header banner. Hover
        # for a plain-language "idiot's guide" to THIS page (keyed by route).
        with ui.element("div").classes(
            "help-fab absolute right-[6px] bottom-[2px] z-[2300]"):
            with ui.button(icon="help").props("round size=xs color=blue").classes("help-btn"):
                # Quote the multi-word anchor/self values — NiceGUI .props() splits on
                # spaces, so unquoted "bottom right" would be mis-parsed. This pins the
                # popup's top-right corner just under the "?" (extends down-left).
                with ui.tooltip().props(
                    'max-width=480px anchor="bottom right" self="top right"'
                ).classes("help-tip"):
                    ui.markdown(page_help.help_md(active)).classes("text-left")

    # Hidden audio element used by play_alert (one per page/client).
    ui.html('<audio id="alert-audio" preload="auto"></audio>')

    # Acknowledge the badge for the page being opened, then run the app-wide
    # alert/badge watcher on every page so the chime fires regardless of route.
    _acknowledge(active)

    async def _tick():
        # Run the blocking bus reads + the proxy health re-warm OFF the event loop;
        # then do the (UI-thread-only) chime + badge updates back here after await.
        decision = await run.io_bound(_watcher_compute)
        await run.io_bound(_refresh_health)
        if decision:
            sound, volume, desktop, n = decision
            play_alert(sound, volume)
            # In-app toast styled to MATCH the scanner's blue "new" badge — same
            # blue + a "new"-signal icon — so the notification and the in-row
            # marker read as the same thing.
            # blue-8 (#1565c0) == the scanner row "new" badge color (Quasar notify
            # takes a palette NAME, not a hex).
            ui.notify(alerts.new_signal_text(n), icon="fiber_new", color="blue-8")
            if desktop:
                notify_desktop("New scanner signal",
                               f"{n} new signal(s) meet your criteria.")
        for route, badge in _badge_refs.items():
            n = _NAV_BADGES.get(route, 0)
            badge.text = str(n) if n else ""
            badge.set_visibility(bool(n))

    ui.timer(2.0, _tick)

    with ui.column().classes("w-full p-4 gap-3") as content:
        health = cached_health()  # memoized — no blocking HTTP on every navigation
        if not health.get("up"):
            with ui.row().classes(
                "w-full bg-red-2 text-red-10 rounded p-3 items-center gap-2"
            ):
                ui.icon("warning")
                ui.label(
                    f"schwab-proxy is not reachable at {proxy.PROXY_URL} — "
                    "start it first (python schwab-proxy/schwab_proxy.py)."
                )
        yield content


@ui.page("/")
def options_scanner_page() -> None:
    with _layout("/", "Options · Scanner"):
        from pages.options import scanner
        scanner.render()


@ui.page("/options/paper")
def options_paper_page() -> None:
    with _layout("/options/paper", "Options · Paper Trades"):
        from pages.options import paper
        paper.render()


@ui.page("/options/captured")
def options_captured_page() -> None:
    with _layout("/options/captured", "Options · Captured Signals"):
        from pages.options import captured
        captured.render()


@ui.page("/options/portfolio")
def options_portfolio_page() -> None:
    with _layout("/options/portfolio", "Options · Paper Portfolio"):
        from pages.options import portfolio
        portfolio.render()


@ui.page("/options/calculator")
def options_calculator_page() -> None:
    with _layout("/options/calculator", "Options · Calculator"):
        from pages.options import calculator
        calculator.render()


@ui.page("/options/swing")
def options_swing_page() -> None:
    with _layout("/options/swing", "Options · Swing Scanner"):
        from pages.options import swing
        swing.render()


@ui.page("/options/gamma")
def options_gamma_page() -> None:
    with _layout("/options/gamma", "Options · Gamma"):
        from pages.options import gamma
        gamma.render()


@ui.page("/options/simulator")
def options_simulator_page() -> None:
    with _layout("/options/simulator", "Options · Simulator"):
        from pages.options import simulator
        simulator.render()


@ui.page("/options/expected-move")
def options_expected_move_page() -> None:
    with _layout("/options/expected-move", "Options · Expected Move"):
        from pages.options import expected_move
        expected_move.render()


@ui.page("/options/rescue")
def options_rescue_page() -> None:
    with _layout("/options/rescue", "Options · Rescue"):
        from pages.options import rescue
        rescue.render()


@ui.page("/sentiment")
def sentiment_page() -> None:
    with _layout("/sentiment", "Sentiment"):
        from pages import sentiment
        sentiment.render()


@ui.page("/sentiment/rotation")
def sentiment_rotation_page() -> None:
    with _layout("/sentiment/rotation", "Sector Rotation"):
        from pages import sentiment_rotation
        sentiment_rotation.render()


@ui.page("/trade")
def trade_page() -> None:
    with _layout("/trade", "Trade"):
        from pages import trade
        trade.render()


@ui.page("/portfolio")
def portfolio_page() -> None:
    with _layout("/portfolio", "Portfolio"):
        from pages import portfolio
        portfolio.render()


@ui.page("/driver")
def driver_page() -> None:
    with _layout("/driver", "Driver"):
        from pages import driver
        driver.render()


@ui.page("/eod")
def eod_page() -> None:
    with _layout("/eod", "EOD Report"):
        from pages import eod
        eod.render()


@ui.page("/eod/detail")
def eod_detail_page() -> None:
    with _layout("/eod", "EOD Report — Detail"):
        from pages import eod
        eod.render_detail()


@ui.page("/status")
def status_page() -> None:
    with _layout("/status", "System Status"):
        from pages import status
        status.render()


@ui.page("/settings")
def settings_page() -> None:
    with _layout("/settings", "Settings"):
        from pages import settings
        settings.render()


@ui.page("/manuals")
def manuals_page() -> None:
    with _layout("/manuals", "User Manuals"):
        from pages import manuals
        manuals.render()


@ui.page("/terminate")
def terminate_page() -> None:
    with _layout("/terminate", "Terminate"):
        from pages import terminate
        terminate.render()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(port=NICEGUI_PORT, title="Schwab Trading", dark=True, reload=False, show=False)
