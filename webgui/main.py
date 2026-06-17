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
from nicegui import app, ui  # noqa: E402

import datetime as _dt  # noqa: E402
from zoneinfo import ZoneInfo as _ZoneInfo  # noqa: E402

import alerts  # noqa: E402
import app_settings  # noqa: E402
import bus_client  # noqa: E402
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
    ("/settings", "Settings", "settings"),
]

# Persisted left-nav expansion state (single-user); None/absent = use active-route default.
_NAV_OPEN: dict[str, bool] = {}

# Single-user nav-badge state (mirrors _NAV_OPEN). _NAV_BADGES holds route->count;
# _ALERT_STATE tracks what's been acknowledged/alerted so we badge/chime only on
# genuinely new items. _badge_refs maps route->badge element for the current page.
_NAV_BADGES: dict[str, int] = {}
_ALERT_STATE: dict = {
    "acked_scan": set(), "alerted": set(), "alerted_init": None,
    "captured_seen": None, "driver_seen": None,
}
_badge_refs: dict = {}

# Modernized drawer styling (scoped to .nav-drawer).
_NAV_CSS = """
.nav-drawer .q-item, .nav-drawer a.nav-link { border-radius: 10px; }
.nav-drawer a.nav-link { transition: background .12s ease; padding: 8px 12px; }
.nav-drawer a.nav-link:hover { background: rgba(255,255,255,.06); }
.nav-drawer a.nav-link.active { background: var(--q-primary); color: #fff; }
.nav-drawer .nav-icon { font-size: 20px; opacity: .9; }
.nav-drawer .nav-badge { margin-left: auto; }
.nav-title { font-weight: 700; letter-spacing: .04em; font-size: .8rem;
             padding: 4px 12px 10px; opacity: .55; }
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
    _recompute_badges()


def _recompute_badges() -> None:
    """Refresh _NAV_BADGES from the current bus state (idempotent)."""
    scan = bus_client.read("options:scan") or {}
    _NAV_BADGES["/"] = alerts.unread_count(
        alerts.scanner_keys(scan), _ALERT_STATE["acked_scan"])
    cap_ver = bus_client.read_version("options:captured")
    _NAV_BADGES["/options/captured"] = 1 if (
        cap_ver is not None and cap_ver != _ALERT_STATE["captured_seen"]) else 0
    drv = bus_client.read("driver:approvals") or {}
    drv_ver = bus_client.read_version("driver:approvals")
    _NAV_BADGES["/driver"] = 1 if (
        drv.get("status") == "pending" and drv_ver != _ALERT_STATE["driver_seen"]) else 0


def _run_watcher() -> None:
    """One watcher tick: recompute badges + fire alerts on new qualifying signals."""
    scan = bus_client.read("options:scan") or {}
    # Seed on the first tick so pre-existing signals don't alert on launch.
    if _ALERT_STATE["alerted_init"] is None:
        _ALERT_STATE["alerted"] = alerts.scanner_keys(scan)
        _ALERT_STATE["alerted_init"] = True
        _recompute_badges()
        return
    _recompute_badges()
    s = app_settings.load()
    q = alerts.qualifying_new(scan, _ALERT_STATE["alerted"], s["alert_min_score"])
    now = _dt.datetime.now(tz=_CT)
    if alerts.should_alert(s, q, now):
        play_alert(s["alert_sound"], s["alert_volume"])
        if s.get("desktop_notifications"):
            notify_desktop("New scanner signal",
                           f"{len(q)} new signal(s) meet your criteria.")
    # Mark everything currently present as alerted so each signal chimes once.
    _ALERT_STATE["alerted"] |= alerts.scanner_keys(scan)


def _nav_link(path: str, label: str, icon: str, active: str) -> None:
    classes = "nav-link w-full no-underline items-center" + (" active" if path == active else "")
    with ui.link(target=path).classes(classes):
        with ui.row().classes("items-center gap-3 w-full no-wrap"):
            ui.icon(icon).classes("nav-icon")
            ui.label(label)
            n = _NAV_BADGES.get(path, 0)
            badge = ui.badge(str(n) if n else "").classes("nav-badge").props("color=red rounded")
            badge.set_visibility(bool(n))
            _badge_refs[path] = badge


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
    drawer = ui.left_drawer(value=True, bordered=True).classes("gap-1 nav-drawer").props("behavior=desktop")
    with drawer:
        ui.label("SCHWAB TRADING").classes("nav-title")
        options_active = active == "/" or active.startswith("/options")
        options_exp = ui.expansion(
            "Options", icon="candlestick_chart", value=_NAV_OPEN.get("Options", options_active)
        ).classes("w-full")
        options_exp.on_value_change(lambda e: _NAV_OPEN.__setitem__("Options", e.value))
        with options_exp:
            for path, label, icon in OPTIONS_CHILDREN:
                _nav_link(path, label, icon, active)
        sentiment_active = active.startswith("/sentiment")
        sentiment_exp = ui.expansion(
            "Sentiment", icon="insights", value=_NAV_OPEN.get("Sentiment", sentiment_active)
        ).classes("w-full")
        sentiment_exp.on_value_change(lambda e: _NAV_OPEN.__setitem__("Sentiment", e.value))
        with sentiment_exp:
            for path, label, icon in SENTIMENT_CHILDREN:
                _nav_link(path, label, icon, active)
        for path, label, icon in FLAT_NAV:
            _nav_link(path, label, icon, active)

    with ui.header().classes("items-center justify-between"):
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat round color=white")
            ui.label("Schwab Trading").classes("text-lg font-bold")
        ui.label(title).classes("text-base opacity-80")

    # Hidden audio element used by play_alert (one per page/client).
    ui.html('<audio id="alert-audio" preload="auto"></audio>')

    # Acknowledge the badge for the page being opened, then run the app-wide
    # alert/badge watcher on every page so the chime fires regardless of route.
    _acknowledge(active)

    def _tick():
        _run_watcher()
        for route, badge in _badge_refs.items():
            n = _NAV_BADGES.get(route, 0)
            badge.text = str(n) if n else ""
            badge.set_visibility(bool(n))

    ui.timer(2.0, _tick)

    with ui.column().classes("w-full p-4 gap-3") as content:
        health = proxy.health()
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


@ui.page("/settings")
def settings_page() -> None:
    with _layout("/settings", "Settings"):
        from pages import settings
        settings.render()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(port=NICEGUI_PORT, title="Schwab Trading", dark=True, reload=False, show=False)
