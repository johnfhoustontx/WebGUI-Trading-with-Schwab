"""NiceGUI multi-page shell for the Trading With Schwab webgui app.

A single NiceGUI server with a left-nav per feature. Each feature gets its own
page route; in Phase 2 the page bodies are stubs that Phase 3 replaces with the
ported engines. A proxy-down banner is shown on every page when the
schwab-proxy (:8100) is unreachable.

Run order: start schwab-proxy first, then ``python webgui/main.py``.
"""
import html
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
import logging  # noqa: E402
import time as _time  # noqa: E402
from zoneinfo import ZoneInfo as _ZoneInfo  # noqa: E402

import alerts  # noqa: E402
import app_settings  # noqa: E402
import bus_client  # noqa: E402
import page_help  # noqa: E402
import proxy  # noqa: E402
from pages.options import theme  # noqa: E402  (config/theme.toml typography + menu)
from pages.ui_guard import guard_async  # noqa: E402
from pages.ui_guard import install_deleted_slot_log_filter  # noqa: E402
from repo_paths import IS_DEV, NICEGUI_PORT, SERVICE_URLS  # noqa: E402

# Silence the benign NiceGUI timer-disconnect-race traceback ("The parent slot of
# the element has been deleted.") — it escapes the ui_guard callback decorators
# (raised by Timer._run_in_loop BEFORE the callback runs) and is logged by
# NiceGUI's default handler. See pages/ui_guard.py.
install_deleted_slot_log_filter()

# Serve bundled static assets (alert sounds) at /static.
_STATIC_DIR = _REPO_ROOT / "webgui" / "static"
if _STATIC_DIR.is_dir():
    app.add_static_files("/static", str(_STATIC_DIR))

_CT = _ZoneInfo("America/Chicago")


def sync_ticker_setting() -> None:
    """Re-assert the ticker toggle to market_svc at startup (best-effort).

    ``ticker_enabled`` is a webgui setting, but it also gates market_svc's ~20-min
    Claude verdict (see pages/settings.py:apply_ticker_enabled). The service reads
    that flag from Redis and defaults to ENABLED when the key is missing, so a
    wiped/restarted Memurai would silently resume the API calls while the GUI still
    showed the ticker as off. Settings.json is the source of truth — restate it on
    every startup. Never raises: a down bus must not stop the web GUI from booting
    (the toggle re-syncs on the next change or startup).
    """
    try:
        enabled = bool(app_settings.get("ticker_enabled"))
        bus_client.request(
            "market", {"type": "enable_summary" if enabled else "disable_summary"})
    except Exception:  # noqa: BLE001
        logging.getLogger("webgui").warning("ticker setting resync failed", exc_info=True)


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


@app.get("/options/gamma-history")
def _serve_gamma_history():
    """Serve the latest on-demand regenerated Gamma briefing HISTORY report.

    The Gamma page's history picker enqueues a ``gamma_history`` command (date +
    optional slot); options_svc rebuilds the HTML from the stored structured
    analysis and caches it here. Raw HTMLResponse so the doc's own <style> applies.
    """
    import bus_client
    return HTMLResponse(analyze_html(bus_client.read("options:gamma_history")))


# ── EquityDeepDive (Trade Analyzer) — Deep Dive report + AI Query serve routes ──
_DEEPDIVE_EMPTY = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><title>Deep Dive</title></head>"
    "<body style='font-family:system-ui,sans-serif;background:#0c0f15;color:#e9edf3;padding:40px'>"
    "<h3>No Deep Dive generated yet</h3><p>Open the Trade Analyzer, enter a symbol, and click "
    "<b>Deep Dive</b>.</p></body></html>")


def deepdive_html(payload):
    """Standalone deep-dive report HTML from the cached payload (or a placeholder)."""
    html = (payload or {}).get("html")
    return html if isinstance(html, str) and html.strip() else _DEEPDIVE_EMPTY


def deepdive_query_html(payload):
    """Wrap the cached chat-prompt markdown in a dark, copyable page (read-only
    textarea + Copy button) so the user can paste it straight into a chat."""
    import html as _h
    md = (payload or {}).get("markdown")
    sym = (payload or {}).get("symbol", "")
    if not (isinstance(md, str) and md.strip()):
        return ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>AI Query</title></head>"
                "<body style='font-family:system-ui;background:#0c0f15;color:#e9edf3;padding:40px'>"
                "<h3>No query generated yet</h3><p>Click <b>AI Query</b> on the Trade Analyzer.</p>"
                "</body></html>")
    esc = _h.escape(md)
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>AI Query — {_h.escape(sym)}</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#0c0f15;color:#e9edf3;margin:0;padding:24px}"
        "h3{margin:0 0 12px}button{background:#2563eb;color:#fff;border:0;border-radius:8px;"
        "padding:10px 16px;font-weight:600;cursor:pointer}button:hover{background:#1d4fd1}"
        "textarea{width:100%;height:75vh;margin-top:12px;background:#101a30;color:#e7edf8;"
        "border:1px solid #243353;border-radius:8px;padding:12px;font-family:ui-monospace,monospace;"
        "font-size:12px;box-sizing:border-box}</style></head><body>"
        f"<h3>AI Query — {_h.escape(sym)} "
        "<button onclick=\"navigator.clipboard.writeText(document.getElementById('q').value)."
        "then(()=>{this.textContent='Copied!';setTimeout(()=>this.textContent='Copy',1200)})\">Copy</button></h3>"
        f"<textarea id='q' readonly>{esc}</textarea></body></html>")


@app.get("/trade/deepdive")
def _serve_deepdive():
    """Serve the latest Deep Dive report as a raw standalone page (its own <style>
    applies). Opened in a new browser tab from the Trade page."""
    import bus_client
    return HTMLResponse(deepdive_html(bus_client.read("trade:deepdive")))


@app.get("/trade/deepdive-query")
def _serve_deepdive_query():
    """Serve the latest AI Query as a copyable page. Opened in a new tab."""
    import bus_client
    return HTMLResponse(deepdive_query_html(bus_client.read("trade:deepdive_query")))


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

# Options is a menu GROUP; each child is a tab in the strip. Ordered by the
# trading workflow: find → analyze → track → repair. (route, label, icon)
OPTIONS_CHILDREN = [
    ("/", "Market Scanner", "radar"),
    ("/options/swing", "Strategy Finder", "swap_vert"),
    ("/options/expected-move", "Expected Move", "candlestick_chart"),
    ("/options/captured", "Captured Signals", "bookmark"),
    ("/options/paper", "Paper Ledger", "request_quote"),
    ("/options/portfolio", "Paper Account", "account_balance_wallet"),
    ("/options/rescue", "Rescue", "healing"),
]

# Market context is a menu GROUP: the macro tile board first (the broadest lens),
# then the sentiment reads. NOTE ``_nav_group_link`` navigates to children[0], so
# this group's RAIL item lands on /market. (route, label, icon)
SENTIMENT_CHILDREN = [
    ("/market", "Market Dashboard", "dashboard"),
    ("/sentiment", "Sentiment", "insights"),
    ("/sentiment/sectors", "Sector & Industry", "table_chart"),
    ("/sentiment/rotation", "Sector Rotation", "donut_large"),
    ("/sentiment/rrg", "RRG", "scatter_plot"),
    ("/sentiment/momentum", "Momentum", "trending_up"),
]

# Standalone MAIN-MENU (rail) pages shown directly UNDER the Options group. Each
# is its own page with NO tab strip — deliberately NOT Options tab-strip entries.
# (route, label, icon)
OPTIONS_RAIL = [
    ("/options/gamma", "Dealer Positioning", "stacked_line_chart"),
    ("/options/matrix", "Opportunity Board", "grid_on"),
]

# The two MODELLING tools, paired as their own group (2026-07-28). They are the
# app's most tightly coupled pages — shared leg editor / strategy templates /
# page-state snapshot, plus a Copy-to-each-other button — but used to straddle two
# nav levels (Calculator a standalone rail page, Simulator an Options tab), so the
# copy button threw you between them. They are deliberately NOT Options tabs: that
# strip is the find → analyze → track → repair workflow over signals the app
# FINDS, whereas these two model legs you bring yourself. (route, label, icon)
STRATEGY_TOOLS_CHILDREN = [
    ("/options/calculator", "Calculator", "calculate"),
    ("/options/simulator", "Simulator", "science"),
]

# Flat top-level items (single-page apps). (route, label, icon)
FLAT_NAV = [
    ("/trade", "Trade Analyzer", "query_stats"),
    ("/portfolio", "Portfolio", "account_balance"),
    ("/driver", "Claude Trades", "smart_toy"),
]

# "More" is a menu GROUP for reports / diagnostics / config. Its tab strip is
# MORE_CHILDREN + SETTINGS_CHILDREN, so User Manuals renders as a flat PEER tab
# of Settings — the old indented sub-group is retired. (route, label, icon)
MORE_CHILDREN = [
    ("/eod", "EOD Report", "summarize"),
    ("/status", "System Status", "monitor_heart"),
    ("/settings", "Settings", "settings"),
    ("/terminate", "Stop All Services", "power_settings_new"),
]

# Sub-menu items nested under the Settings entry. (route, label, icon)
SETTINGS_CHILDREN = [
    ("/manuals", "User Manuals", "menu_book"),
]

# ── Main-menu groups (2026-07-11 nav redesign) ───────────────────────────────
# The drawer shows ONE flat item per group; the group's child pages render as a
# compact TAB STRIP across the top of the page (small padding), replacing the old
# expandable sub-menus. A group's drawer badge is the SUM of its children's badge
# counts; the per-page badges float on the tabs. (label, icon, children)
# Lookup order is irrelevant here (routes are unique, and both consumers below
# ITERATE) — the drawer decides where each group actually sits. Appended rather
# than inserted so the positional _NAV_GROUPS[0..2] reads in _layout stay valid.
_NAV_GROUPS = [
    ("Options", "candlestick_chart", OPTIONS_CHILDREN),
    ("Market Trend & Sentiment", "speed", SENTIMENT_CHILDREN),
    ("More", "more_horiz", MORE_CHILDREN + SETTINGS_CHILDREN),
    ("Strategy Tools", "build", STRATEGY_TOOLS_CHILDREN),
]


def _group_children(active: str):
    """The child list of the group containing ``active``, or None (flat page)."""
    for _label, _icon, children in _NAV_GROUPS:
        if any(path == active for path, _l, _i in children):
            return children
    return None


# ── Deep Slate shell helpers (Phase 2) — pure, unit-tested in test_shell.py ──
def breadcrumb_parts(active: str):
    """(section, tab) for the header breadcrumb ``{Section} · {Tab}``.

    A grouped page → (group label, this page's label); a flat single page →
    (page label, "") so the breadcrumb shows just the section (no "· Tab")."""
    for label, _icon, children in _NAV_GROUPS:
        if any(path == active for path, _l, _i in children):
            return label, _NAV_LABEL.get(active, "")
    return _NAV_LABEL.get(active, theme.BRAND_NAME), ""


def brand_mark_src(static_dir=None):
    """The header logo's URL, or ``""`` when there is no usable image.

    ``[brand].mark`` is a URL under ``/static``; this maps it back to disk and
    returns it ONLY if the file is actually there, so a missing asset renders the
    wordmark alone instead of a broken-image icon. Any oddity (blank config, a
    path outside /static, an unreadable directory) degrades to ``""``."""
    url = str(getattr(theme, "BRAND_MARK", "") or "").strip()
    if not url.startswith("/static/"):
        return ""
    root = pathlib.Path(static_dir) if static_dir else _STATIC_DIR
    try:
        if (root / url[len("/static/"):]).is_file():
            return url
    except Exception:  # noqa: BLE001 — chrome must never break a page render.
        pass
    return ""


def brand_lockup_html(static_dir=None):
    """The header lockup: the logo mark (when present) + the two-tone wordmark.

    Raw HTML rather than NiceGUI elements because each wordmark half needs a
    gradient clipped to its text (``theme.build_brand_css``), which Tailwind's
    bundled JIT can't express. The name comes from ``[brand]`` config, so it is
    HTML-escaped.

    In dev the lockup carries a DEV chip: two identical-looking tabs that write
    to DIFFERENT paper books is a mistake waiting to happen. Inline style for the
    same reason as the rest of this function — it is a raw HTML string, not a
    NiceGUI element with ``.classes()``."""
    mark = brand_mark_src(static_dir)
    img = (f'<img src="{html.escape(mark)}" class="brand-mark" alt="">'
           if mark else "")
    chip = ('<span style="margin-left:8px;padding:1px 7px;border-radius:4px;'
            'background:#b45309;color:#fff;font-size:10px;font-weight:700;'
            'letter-spacing:.06em">DEV</span>') if IS_DEV else ""
    return (f'<div style="display:flex;align-items:center;gap:9px">{img}'
            f'<span class="brand-word">'
            f'<span class="a">{html.escape(theme.BRAND_NAME_A)}</span>'
            f'<span class="b">{html.escape(theme.BRAND_NAME_B)}</span>'
            f'</span>{chip}</div>')


def window_title(page: str | None = None):
    """The browser-tab / taskbar title, environment-tagged.

    Prefixed in dev so a tab is identifiable before it renders (and in the
    taskbar, where the favicon is per-route and the title is the only tell).
    Prod is EXACTLY the unprefixed title — unchanged from before environments.

    ``page`` is the per-page label. It is a parameter because ``_layout`` calls
    ``ui.page_title`` on EVERY page, and that OVERRIDES the ``ui.run(title=…)``
    default — so tagging only the ``ui.run`` title left every real page reading
    e.g. "Market Scanner" in both environments, which is precisely where the
    distinction is needed: the chip is in the page, but the tab strip is what
    you read when the tabs are narrow. Verified in a live browser, not inferred.
    """
    base = page or theme.BRAND_NAME
    return f"DEV · {base}" if IS_DEV else base


def market_status_parts(now=None):
    """("MARKET OPEN"|"MARKET CLOSED", is_open) for the header pill.

    Uses the shared trading-day + 08:00–15:00 CT gate (``alerts.in_market_hours``)."""
    from datetime import datetime
    try:
        n = now if now is not None else datetime.now(alerts.CT)
        return ("MARKET OPEN", True) if alerts.in_market_hours(n) else ("MARKET CLOSED", False)
    except Exception:  # noqa: BLE001 — chrome must never break a page render.
        return "MARKET CLOSED", False


# ── Nav rail geometry (2026-07-15) ──────────────────────────────────────────
# The drawer is an ICON RAIL that expands on hover. NAV_WIDTH_RAIL is the width
# Quasar lays out with (so the page's left offset is always the rail width);
# NAV_WIDTH_OPEN is what the ``_NAV_CSS`` hover rule widens the ASIDE to, and
# what ``_layout`` lays the drawer out at when PINNED. The 248 is duplicated as a
# literal in that CSS rule — a test pins the two together.
NAV_WIDTH_RAIL = 64
NAV_WIDTH_OPEN = 248


def drawer_width(pinned: bool) -> int:
    """Quasar ``width`` prop for the nav drawer: the full menu when pinned, else
    the icon rail (hover widens it via CSS only — the layout offset stays here)."""
    return NAV_WIDTH_OPEN if pinned else NAV_WIDTH_RAIL


# ── Browser-tab title + per-page favicon color ──────────────────────────────
# Each page's BROWSER TAB shows the selected menu-item name (derived from the nav
# lists above, so it never drifts) and a DISTINCT colored favicon, so several open
# tabs are tellable-apart at a glance. Applied per page in ``_layout`` via
# ``ui.page_title`` + a tiny colored-square SVG ``<link rel=icon>``.
_NAV_LABEL = {route: label for route, label, _icon in
              OPTIONS_CHILDREN + OPTIONS_RAIL + STRATEGY_TOOLS_CHILDREN
              + SENTIMENT_CHILDREN + FLAT_NAV
              + MORE_CHILDREN + SETTINGS_CHILDREN}

# One distinct color per route (the favicon fill). Material hues, all visually apart.
_TAB_COLOR = {
    "/": "#42a5f5",                       # Market Scanner — blue
    "/options/matrix": "#4dd0e1",         # Opportunity Board — cyan
    "/options/paper": "#66bb6a",          # Paper Ledger — green
    "/options/captured": "#ab47bc",       # Captured Signals — purple
    "/options/portfolio": "#26a69a",      # Paper Account — teal
    "/options/calculator": "#ffa726",     # Calculator — amber
    "/options/swing": "#ec407a",          # Strategy Finder — pink
    "/options/gamma": "#7e57c2",          # Dealer Positioning — deep purple
    "/options/simulator": "#29b6f6",      # Simulator — light blue
    "/options/expected-move": "#ffca28",  # Expected Move — yellow
    "/options/rescue": "#ef5350",         # Rescue — red
    "/sentiment": "#5c6bc0",              # Sentiment — indigo
    "/sentiment/sectors": "#7986cb",      # Sector & Industry — indigo light
    "/sentiment/rotation": "#8d6e63",     # Sector Rotation — brown
    "/sentiment/rrg": "#a1887f",          # RRG — brown light
    "/sentiment/momentum": "#9ccc65",     # Momentum — light green
    "/market": "#00bfa5",                # Market Dashboard — teal-green
    "/trade": "#26c6da",                 # Trade — cyan
    "/portfolio": "#9ccc65",             # Portfolio — light green
    "/driver": "#ff7043",                # Driver — deep orange
    "/eod": "#78909c",                   # EOD Report — blue grey
    "/status": "#d4e157",                # System Status — lime
    "/settings": "#90a4ae",              # Settings — blue grey light
    "/terminate": "#b71c1c",             # Stop All Services — dark red
    "/manuals": "#4db6ac",               # User Manuals — teal
}


def _favicon_link(color: str) -> str:
    """A rounded-square SVG favicon (data-URI) filled ``color``, as BOTH the modern
    ``rel=icon`` and the legacy ``rel="shortcut icon"``.

    NiceGUI injects a default ``rel="shortcut icon"`` .ico earlier in <head>; ours are
    added after it, so the last-declared link of each rel wins — guaranteeing the
    colored favicon shows in the tab regardless of which rel the browser prefers."""
    from urllib.parse import quote
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
           f'<rect width="32" height="32" rx="7" fill="{color}"/></svg>')
    uri = f"data:image/svg+xml,{quote(svg)}"
    return f'<link rel="icon" href="{uri}"><link rel="shortcut icon" href="{uri}">'

# Single-user nav-badge state. _NAV_BADGES holds route->count; _ALERT_STATE
# tracks what's been acknowledged/alerted so we badge/chime only on genuinely
# new items. _badge_refs maps route->badge element (on the top tab strip + the
# flat drawer links); _group_badge_refs maps group label->(badge, member paths)
# for the drawer group items (badge = sum of member counts).
_NAV_BADGES: dict[str, int] = {}
_ALERT_STATE: dict = {
    "acked_scan": set(), "alerted": set(), "alerted_init": None,
    "captured_seen": None, "rescue_seen": None,
    # Health/staleness (R4b/R8): the set of currently stale/down component keys
    # already alerted, so we chime only on transition INTO bad (fire-on-transition,
    # clear-on-heal). Seeded on the first tick so a service that's already stale/down
    # at launch doesn't chime immediately.
    "health_alerted": set(), "health_init": None,
    # Options-flow alerts (put/call premium crossover + unusual activity): the set
    # of alert ids already surfaced, so each fires once. Seeded on the first tick so
    # a page load doesn't replay the day's backlog.
    "flow_acked": set(), "flow_init": None,
}
_badge_refs: dict = {}
_group_badge_refs: dict = {}

# Per-page-build slot directly under the top tab strip, where a page can mount
# its own view SUBTABS (see _layout; e.g. the Gamma GEX/Charm/... row). Rebuilt
# on every _layout; None on pages without a strip.
_SUBTAB_SLOT: dict = {"el": None}


def subtab_slot():
    """The container under the main tab strip for a page's view subtabs (or None)."""
    return _SUBTAB_SLOT["el"]

# ── Health / staleness surfacing (R4b / R8) ──────────────────────────────────
# Representative SCHEDULED cache views (mirrors the scheduled rows of
# status.py:_FRESHNESS) — a view older than alerts.STALE_AFTER_SEC means the
# owning service is up-but-wedged (or gone). On-demand views (trade/driver) are
# excluded: they're expected to be old.
_HEALTH_VIEWS = [
    "sentiment:composite",
    "options:scan",
    "options:gex_status",
    "portfolio:positions",
]

# Tier-2 services probed by a lightweight /health GET. Throttled (see
# _HEALTH_PROBE_INTERVAL_SEC) so we do NOT probe all services every 2s tick.
_HEALTH_SERVICES = list(SERVICE_URLS.keys())
_HEALTH_PROBE_INTERVAL_SEC = 30.0
_HEALTH_HTTP_TIMEOUT = 2.0
# Last successful service-health probe result + when (monotonic). Reused between
# probes so the 2s watcher only pays the HTTP fan-out every ~30s.
_svc_health_cache: dict = {"data": {}, "ts": 0.0}

# Log-once memo for a bus (Memurai) outage: True once "bus down" has been logged,
# reset when the bus recovers so a later outage logs again. Prevents a full
# traceback every 2s on every open page when Memurai is down (R9).
_bus_outage: dict = {"logged": False}
_LOG = logging.getLogger("webgui.watcher")


def _probe_services_health(now_mono: float) -> dict:
    """Throttled Tier-2 /health probe → ``{service: up_bool_or_None}``.

    Runs the HTTP fan-out at most once per ``_HEALTH_PROBE_INTERVAL_SEC``; between
    probes it returns the cached result (so a 2s watcher tick is cheap and does NOT
    hit all five services). ``None`` for a service means "couldn't determine" and is
    treated as healthy by the pure alert logic (no false alarm). Never raises.
    """
    if (not _svc_health_cache["data"]
            or now_mono - _svc_health_cache["ts"] >= _HEALTH_PROBE_INTERVAL_SEC):
        import requests  # local import — keep module load light
        out: dict = {}
        for svc in _HEALTH_SERVICES:
            url = SERVICE_URLS.get(svc)
            if not url:
                out[svc] = None
                continue
            try:
                r = requests.get(f"{url}/health", timeout=_HEALTH_HTTP_TIMEOUT)
                out[svc] = (r.status_code == 200 and r.json().get("up") is True)
            except Exception:  # noqa: BLE001 — a probe must never raise
                out[svc] = False
        _svc_health_cache["data"] = out
        _svc_health_cache["ts"] = now_mono
    return dict(_svc_health_cache["data"])


def _freshness_facts(now_utc) -> dict:
    """``{view: is_stale}`` for the representative scheduled views (never raises).

    ONE pipelined ``read_metas`` probe of the tiny ``:ver``/``:ts`` side keys —
    this runs on every 2s watcher tick, so it must never deserialize the payload
    envelopes (the old per-view ``read_meta`` loop re-parsed ~0.5–1 MB/tick,
    including ``options:scan`` a second time)."""
    try:
        metas = bus_client.read_metas(_HEALTH_VIEWS)
    except Exception:  # noqa: BLE001
        # A read failure is a bus problem, handled by the tick guard — don't
        # flag views stale off a transient read error.
        return {view: False for view in _HEALTH_VIEWS}
    facts: dict = {}
    for view in _HEALTH_VIEWS:
        try:
            _ver, ts = metas.get(view, (None, None))
            facts[view] = _is_view_stale(ts, now_utc, view)
        except Exception:  # noqa: BLE001
            facts[view] = False
    return facts


def _is_view_stale(ts, now_utc, view=None) -> bool:
    """Mirror of status.py:is_stale for a scheduled view (no import to avoid pulling
    the page module + requests at webgui module load). A missing ts => stale. The
    threshold is per-view (alerts.stale_after) so a slow-cadence view like the 15-min
    ``options:scan`` isn't falsely flagged between its scans."""
    if not ts:
        return True
    try:
        when = _dt.datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return (now_utc - when).total_seconds() > alerts.stale_after(view)

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

# Quasar-internal styling — only the rules component .classes() can't reach live
# here (nav-link/icon/badge/title visuals moved to Tailwind utilities in Phase 1).
# The drawer is a FLAT main menu (2026-07-11) — each group's child pages render as
# the .compact-tabs strip across the top of the page instead of expandable
# sub-menus, so the old expansion rules are gone.
_NAV_CSS = """
.nav-drawer { gap: 2px; }
/* Active nav item pill — the "Deep Slate" look: a SUBTLE navy tint (not a solid
   accent fill), paired with the item's own icon, which carries the active state
   (see the .nav-active .nav-icon accent below). This wash and the .compact-tabs
   fill below are both HARDCODED rgba here — neither rides [menu].accent /
   --q-primary, because the bundled JIT emits neither var() nor rgba()
   arbitraries reliably. Change the accent knob and these do NOT follow. */
.nav-drawer .nav-active { background: rgba(107,134,255,0.13); }
.nav-drawer .nav-active .nav-label { color: #eef1f6; font-weight: 600; }
.nav-drawer .q-item { border-radius: 10px; }
/* ── Icon rail (2026-07-15) ────────────────────────────────────────────────
   THE DOM SPLIT — the trap that cost hours; do not re-learn it. `.nav-drawer` is
   NOT the <aside>. NiceGUI puts the drawer's classes on Quasar's inner content
   div, so the tree is:
       <aside class="q-drawer" style="width:64px">   ← the WIDTH lives here
         <div class="q-drawer__content fit scroll nicegui-drawer nav-drawer">
   The width must therefore be widened on the ASIDE, reached from our class via
   :has(). A rule on `.nav-drawer` itself targets a CHILD of the element holding
   the width and can never win.
   Why !important: Quasar writes the width as an INLINE style, and only an author
   !important declaration beats an inline one. It works here because the aside
   carries no LAYERED !important rule. Note the asymmetry: an unlayered
   !important does NOT beat NiceGUI's layer(quasar_importants) rules — e.g.
   `.fit{width:100%!important}` — but .fit is on the CONTENT div, and 100% of a
   248px aside is what we want anyway.
   Quasar's LAYOUT still uses the rail width, so .q-page-container's padding never
   changes — the expanded menu OVERLAYS the content instead of reflowing it (this
   app's Highcharts have no ResizeObserver, so a reflow on every hover would leave
   charts mis-sized). :focus-within expands for keyboard users, who otherwise get
   a rail of unreadable opacity:0 labels. .nav-pinned opts out: the drawer is
   already laid out at the open width.
   The width prop + the .nav-pinned class are wired in _layout/_toggle_pin.
   The 248px must equal NAV_WIDTH_OPEN; a test pins them together. */
.q-drawer:has(> .nav-drawer:not(.nav-pinned)) { transition: width .18s ease; }
.q-drawer:has(> .nav-drawer:not(.nav-pinned)):hover,
.q-drawer:has(> .nav-drawer:not(.nav-pinned)):focus-within {
    width: 248px !important; box-shadow: 0 12px 40px rgba(0,0,0,.5); }
/* .nav-drawer IS the real scroller (Quasar's .scroll sets overflow:auto), so the
   clip belongs here: it stops the 248px of content from raising a horizontal
   scrollbar in the 64px rail. It cannot clip the corner count badges — they sit
   ~40px from the drawer's left edge, well inside the rail. */
.nav-drawer { overflow-x: hidden; }
/* Labels + the group title clip (not wrap) in the rail and fade in as it opens.
   Only the rail's fade lives here: it keys off an ANCESTOR's hover/focus/pinned
   state, which no Tailwind utility can express (the rest of the nav's typography
   is in .classes()). */
.nav-drawer .nav-label { white-space: nowrap; }
.nav-drawer .nav-title, .nav-drawer .nav-label {
    opacity: 0; transition: opacity .14s ease; }
.nav-drawer.nav-pinned .nav-title, .nav-drawer.nav-pinned .nav-label,
.nav-drawer:hover .nav-title, .nav-drawer:hover .nav-label,
.nav-drawer:focus-within .nav-title, .nav-drawer:focus-within .nav-label {
    opacity: 1; }
/* Active icon accent. MUST be !important AND 3 classes: theme.build_nav_css
   emits `.nav-drawer .q-icon{color:<[menu].text>!important}` (2 classes) and is
   injected AFTER this block, so equal-specificity would lose. */
.nav-drawer .nav-active .nav-icon { color: #6b86ff !important; }
/* Compact tab strip (the sub-menu tabs under the header): Deep Slate PILL tabs in
   a raised rounded container — no folder baseline. The active pill is a soft navy
   tint; inactive are plain. Quasar-internal (q-tab). */
.compact-tabs {
  background: #111731; border-radius: 12px; padding: 4px 5px; min-height: 0;
}
.compact-tabs .q-tab {
  min-height: 30px; padding: 0 13px; margin-right: 2px;
  border-radius: 8px; background: transparent; color: #8891ab;
}
.compact-tabs .q-tab--active {
  background: rgba(107,134,255,0.16); color: #dbe2ff;
}
.compact-tabs .q-tab__indicator { display: none; }
.compact-tabs .q-tab__label { font-size: 12.5px; font-weight: 500; }
/* Subtab row (a page's own view tabs, e.g. Gamma GEX/Charm/DEX/Vanna/Flow/Term)
   — the same pill shape one size smaller, on a fainter inset container so the
   hierarchy under the main strip reads clearly. */
.compact-subtabs {
  background: #0f1428; border-radius: 10px; padding: 3px 4px; min-height: 0;
}
.compact-subtabs .q-tab {
  min-height: 26px; padding: 0 11px; margin-right: 2px;
  border-radius: 7px; background: transparent; color: #8891ab;
}
.compact-subtabs .q-tab--active { background: rgba(255,255,255,.08); color: #eef1f6; }
.compact-subtabs .q-tab__indicator { display: none; }
.compact-subtabs .q-tab__label { font-size: 12px; }
/* Flush tab panels — Quasar gives each q-tab-panel 16px padding; pages whose
   panels should hug their card/table edges opt in with .flush-panels. */
.flush-panels .q-tab-panel { padding: 4px 0 0 0; }
/* Page-help tooltips — mounted on every nav TAB + drawer item (the header "?"
   fab was removed 2026-07-12); shown after the mouse rests 2 s (q-tooltip delay). */
.q-tooltip.help-tip { background: #1e2735; color: #e7edf5; font-size: .82rem;
    line-height: 1.5; padding: 12px 16px; border: 1px solid rgba(255,255,255,.14);
    border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,.45); }
.q-tooltip.help-tip strong { color: #fff; }
.q-tooltip.help-tip p { margin: .4em 0; }
.q-tooltip.help-tip ul { padding-left: 1.15em; margin: .35em 0; }
.q-tooltip.help-tip li { margin: .2em 0; }
/* ── Deep Slate shell chrome (Phase 2) ─────────────────────────────────────
   Market-status pill. rgba lives in raw CSS (not Tailwind classes) so the
   bundled JIT never has to emit rgba arbitraries. The header brand lockup
   (logo mark + two-tone wordmark) is themed separately in theme.BRAND_CSS,
   built from the [brand] config block. */
.mkt-pill { display: flex; align-items: center; gap: 7px; height: 28px;
  padding: 0 11px; border-radius: 8px; }
.mkt-pill .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.mkt-open { background: rgba(53,194,129,0.12); border: 1px solid rgba(53,194,129,0.28); }
.mkt-open .dot { background: #35c281; box-shadow: 0 0 8px rgba(53,194,129,0.8); }
.mkt-open .lbl { color: #5fd6a2; }
.mkt-closed { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.09); }
.mkt-closed .dot { background: #6d76a0; }
.mkt-closed .lbl { color: #8891ab; }
"""

# Global table chrome (app-wide standard): EVERY data table gets a fixed (sticky)
# header over a bounded, scrolling body, so the column headers stay visible as a long
# table scrolls. Injected once per page in ``_layout``. Per-page table CSS
# (.paper-table / .captured-table / .driver-table) may still set its own max-height —
# its more-specific selector + later injection win over this baseline.
_TABLE_CSS = """
.q-table__middle { max-height: 65vh; }
/* Deep Slate table header: sticky, dark #141a30 inset, with uppercase faint
   column labels (10.5px / 600 / .06em) — the trading-terminal look. */
.q-table thead tr th {
  position: sticky; top: 0; z-index: 1; background: #141a30;
  font-size: 10.5px; font-weight: 600; letter-spacing: .06em;
  text-transform: uppercase; color: #6d76a0;
}
/* Faint row dividers (Deep Slate) between body rows. */
.q-table tbody tr:not(:last-child) td { border-bottom: 1px solid rgba(255,255,255,.04); }
"""


def _acknowledge(active: str, scan=None) -> None:
    """Clear the badge for the page being viewed.

    ``scan`` — the ``options:scan`` payload, when the caller (``_layout``) already
    read it this navigation, so the (large) scan payload isn't deserialized 2-3×
    per page build (the ack + the badge recompute would each re-read it)."""
    if active == "/":                                   # Scanner
        if scan is None:
            scan = bus_client.read("options:scan") or {}
        _ALERT_STATE["acked_scan"] = alerts.scanner_keys(scan)
    elif active == "/options/captured":
        _ALERT_STATE["captured_seen"] = bus_client.read_version("options:captured")
    elif active == "/options/rescue":
        # Acknowledge the current rescue-summary version so the badge clears on
        # open and only re-appears when the manage cycle publishes a new summary.
        _ALERT_STATE["rescue_seen"] = bus_client.read_version("options:rescue_summary")
    _recompute_badges(scan)


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
    DECIDE whether to alert. Returns an alert dict ``{"scanner": (...), "health":
    (...)}`` (each value or None), or None if nothing fires. Does NO UI work (safe
    to run via ``run.io_bound``) — the caller fires the chime/notification on the
    UI thread.

    Also surfaces STALE scheduled views + DOWN Tier-2 services (R4b/R8): the
    service health fan-out is THROTTLED to once per ``_HEALTH_PROBE_INTERVAL_SEC``
    (NOT probed every tick), and staleness/down alerts fire only on transition INTO
    bad (deduped while persistent, cleared on recovery). A "⚠"-count badge on the
    System Status nav item reflects the current unhealthy count regardless of the
    chime gate.
    """
    scan = bus_client.read("options:scan") or {}   # read ONCE; passed to badges below
    flow_view = bus_client.read("options:flow_alerts")
    keys = alerts.scanner_keys(scan)
    s = app_settings.load()                        # in-memory cached (no disk hit)
    now = _dt.datetime.now(tz=_CT)
    now_mono = _time.monotonic()
    now_utc = _dt.datetime.now(_dt.timezone.utc)

    # ── health / staleness facts (throttled probe) ──────────────────────────
    freshness = _freshness_facts(now_utc)
    svc_health = _probe_services_health(now_mono)
    unhealthy_n = len(alerts.unhealthy_keys(freshness, svc_health))
    _NAV_BADGES["/status"] = unhealthy_n           # badge tracks count, ungated

    # Seed on the first tick so pre-existing signals / already-down services don't
    # alert on launch.
    if _ALERT_STATE["alerted_init"] is None:
        _ALERT_STATE["alerted"] = keys
        _ALERT_STATE["alerted_init"] = True
        _ALERT_STATE["health_alerted"] = alerts.unhealthy_keys(freshness, svc_health)
        _ALERT_STATE["health_init"] = True
        _ALERT_STATE["flow_acked"] = alerts.new_flow_alerts(flow_view, set())[1]
        # Only count the seed as done if the view was actually READABLE. On a
        # restart the first tick can hit a not-yet-ready bus and return None, which
        # seeds an EMPTY acked set — then the next tick treats the whole day's
        # backlog (up to the 50-alert cap) as new and replays it as toasts.
        _ALERT_STATE["flow_init"] = flow_view is not None
        _recompute_badges(scan)
        return None
    _recompute_badges(scan)

    # ── scanner alert ────────────────────────────────────────────────────────
    q = alerts.qualifying_new(scan, _ALERT_STATE["alerted"], s["alert_min_score"])
    scanner = None
    if alerts.should_alert(s, q, now):
        scanner = (s["alert_sound"], s["alert_volume"],
                   bool(s.get("desktop_notifications")), len(q))
    # Mark everything currently present as alerted so each signal chimes once.
    _ALERT_STATE["alerted"] |= keys

    # ── health / staleness alert (transition-deduped) ────────────────────────
    fire, next_alerted = alerts.new_health_alerts(
        freshness, svc_health, _ALERT_STATE["health_alerted"], s, now)
    _ALERT_STATE["health_alerted"] = next_alerted
    health = None
    if fire:
        health = (s["alert_sound"], s["alert_volume"],
                  bool(s.get("desktop_notifications")), len(fire))

    # ── options-flow alert (crossover + unusual activity) ────────────────────
    # ALWAYS advance the acked set (even when the toggle is off) so toggling the
    # feature back on doesn't dump the day's backlog; only FIRE when enabled.
    new_flow, _ALERT_STATE["flow_acked"] = alerts.new_flow_alerts(
        flow_view, _ALERT_STATE["flow_acked"])
    flow = None
    if not _ALERT_STATE["flow_init"]:
        # The seeding tick couldn't read the view, so flow_acked started EMPTY.
        # Adopt the current backlog SILENTLY rather than replaying the whole day.
        _ALERT_STATE["flow_init"] = flow_view is not None
    elif alerts.should_flow_alert(s, new_flow, now):
        flow = (s["alert_sound"], s["alert_volume"],
                bool(s.get("desktop_notifications")), new_flow)

    if scanner is None and health is None and flow is None:
        return None
    return {"scanner": scanner, "health": health, "flow": flow}


def _guarded_compute():
    """Run ``_watcher_compute`` but survive a Memurai/bus outage (R9).

    When the bus is down, every open page's 2s tick would otherwise raise deep in
    ``bus_client`` and NiceGUI would log a full traceback every 2 seconds. Here we
    swallow the failure, log a SINGLE "bus down" warning (memoized via
    ``_bus_outage``), and return None so the tick is a clean no-op. When the bus
    recovers, the memo resets and a one-line "bus recovered" is logged, then normal
    operation resumes.
    """
    try:
        result = _watcher_compute()
    except Exception as exc:  # noqa: BLE001 — a watcher outage must not spam logs
        if not _bus_outage["logged"]:
            _LOG.warning("watcher: backbone/bus unavailable (%s) — alerts paused; "
                         "logging once until it recovers.", type(exc).__name__)
            _bus_outage["logged"] = True
        return None
    if _bus_outage["logged"]:
        _LOG.info("watcher: backbone/bus recovered — alerts resumed.")
        _bus_outage["logged"] = False
    return result


def _help_tooltip(path: str) -> None:
    """Page-help tooltip mounted inside the CURRENT element (a nav tab / drawer
    item): the plain-language guide for ``path`` (page_help), shown after the
    mouse RESTS on the element for 2 s (q-tooltip ``delay``). Replaced the old
    header "?" fab (2026-07-12) — the help now lives on the menu items themselves."""
    with ui.tooltip().props("delay=2000 max-width=480px").classes("help-tip"):
        ui.markdown(page_help.help_md(path)).classes("text-left")


def _count_badge(n: int) -> ui.badge:
    """A red count badge floating on its parent's top-right corner, hidden at 0.

    The drawer rail and the tab strip both mount one; the 2s watcher then keeps it
    current via ``_set_badge``. ``floating`` is position:absolute, so the parent
    must be POSITIONED: the rail's wrapper is explicitly ``relative`` (see
    ``_nav_icon``), while the tab-strip call site relies on Quasar's ``.q-tab``
    already being position:relative."""
    badge = ui.badge(str(n) if n else "").props("color=red rounded floating")
    badge.set_visibility(bool(n))
    return badge


def _set_badge(badge: ui.badge, n: int) -> None:
    """Point a mounted count badge at ``n`` — the update half of ``_count_badge``
    (0 blanks the text AND hides it, so an empty badge never renders as a dot)."""
    badge.text = str(n) if n else ""
    badge.set_visibility(bool(n))


def _nav_icon(icon: str, count: int) -> ui.badge:
    """The rail's icon plus its corner count badge.

    The icon is the ONLY thing visible when the rail is collapsed, so it carries
    the active state, and the badge rides its top-right corner in BOTH states.
    Returns the badge so the caller can register it for the 2s watcher.

    Takes no is_active: the accent is painted by _NAV_CSS's
    `.nav-drawer .nav-active .nav-icon`, keyed off the LINK's .nav-active, which
    the caller already sets. It has to be keyed there — it needs 3 classes to
    out-specify theme.build_nav_css's `.nav-drawer .q-icon{...!important}`
    ([menu].text), injected after _NAV_CSS, so a 2-class marker on the icon
    itself would tie and lose."""
    # ``relative`` is load-bearing, not layout garnish: Quasar's ``floating`` badge is
    # position:absolute, so it anchors to the nearest POSITIONED ancestor. Drop this
    # and the badge escapes up the tree instead of sitting on the icon corner.
    with ui.element("div").classes(
            "relative flex items-center justify-center flex-none w-6 h-6"):
        ui.icon(icon).classes("nav-icon text-[20px]")
        badge = _count_badge(count)
    return badge


def _nav_link(path: str, label: str, icon: str, active: str) -> None:
    base = ("w-full no-underline items-center rounded-[10px] px-3 py-1 "
            "transition-colors hover:bg-white/[0.06]")
    # nav-active is a plain CSS rule in _NAV_CSS (a soft rgba navy wash) — NOT a
    # Tailwind arbitrary class: the bundled Tailwind JIT emits neither var(...)
    # nor rgba(...) arbitraries reliably (plain-hex ones are fine), so the old
    # bg-[var(--q-primary)] silently produced no rule at all. The pill is now
    # decoupled from --q-primary on purpose — see the rule's comment in _NAV_CSS.
    is_active = path == active
    state = " nav-active" if is_active else ""
    with ui.link(target=path).classes(base + state):
        _help_tooltip(path)   # rest the mouse 2 s for this page's guide
        with ui.row().classes("items-center gap-3 w-full no-wrap"):
            _badge_refs[path] = _nav_icon(icon, _NAV_BADGES.get(path, 0))
            ui.label(label).classes("nav-label")


def _nav_group_link(label: str, icon: str, children, active: str) -> None:
    """One flat drawer item for a GROUP: navigates to the group's first page,
    highlights when the active route is any of its children, and carries a badge
    with the SUM of the children's badge counts (updated by the watcher via
    ``_group_badge_refs``). The children themselves render as the top tab strip."""
    paths = [p for p, _l, _i in children]
    base = ("w-full no-underline items-center rounded-[10px] px-3 py-1 "
            "transition-colors hover:bg-white/[0.06]")
    is_active = active in paths
    state = " nav-active" if is_active else ""
    with ui.link(target=paths[0]).classes(base + state):
        _help_tooltip(paths[0])   # the group's landing page's guide (2 s rest)
        with ui.row().classes("items-center gap-3 w-full no-wrap"):
            n = sum(_NAV_BADGES.get(p, 0) for p in paths)
            _group_badge_refs[label] = (_nav_icon(icon, n), paths)
            ui.label(label).classes("nav-label")


def _toggle_pin(drawer) -> None:
    """Pin/unpin the nav rail (the header hamburger). Pinned = laid out at the
    open width with the hover rule disabled; unpinned = the icon rail. Quasar
    reacts to the width prop, so no page reload. Persisted so the drawer opens
    the way it was left."""
    pinned = not app_settings.get("nav_pinned")
    app_settings.set("nav_pinned", pinned)
    drawer.props(f"width={drawer_width(pinned)}")
    if pinned:
        drawer.classes(add="nav-pinned")
    else:
        drawer.classes(remove="nav-pinned")


@contextmanager
def _layout(active: str, title: str):
    """Render shared chrome (header, nav drawer, proxy banner).

    Yields the content container the page should populate.
    """
    _badge_refs.clear()
    _group_badge_refs.clear()
    # Read the (potentially large) options:scan payload ONCE per navigation and
    # share it with both the initial badge recompute here and the _acknowledge
    # below — each used to re-read it (2-3 deserializes of the day's scan per page).
    _scan = bus_client.read("options:scan") or {}
    _recompute_badges(_scan)
    ui.add_css(_NAV_CSS)
    ui.add_css(_TABLE_CSS)   # app-wide fixed (sticky) table headers
    # config/theme.toml [typography] + [menu] — app-wide text categories and menu
    # styling, injected AFTER the baseline CSS so a configured override wins.
    # Both are "" / no-ops when the config keeps the defaults.
    if theme.FONT_HEAD_HTML:
        # config/theme.toml [typography].font_url — loads the web font (IBM Plex)
        # so [typography].family resolves. "" (no font_url) → no-op / no request.
        ui.add_head_html(theme.FONT_HEAD_HTML)
    if theme.TYPOGRAPHY_CSS:
        ui.add_css(theme.TYPOGRAPHY_CSS)
    if theme.NAV_THEME_CSS:
        ui.add_css(theme.NAV_THEME_CSS)
    # Brand identity (header lockup). The font is loaded separately from the
    # body font — it styles the wordmark only, never the data tables.
    if theme.BRAND_FONT_HEAD_HTML:
        ui.add_head_html(theme.BRAND_FONT_HEAD_HTML)
    ui.add_css(theme.BRAND_CSS)
    if theme.MENU_ACCENT:
        # [menu].accent → the Quasar primary, which reaches ONLY Quasar-colored
        # controls (switches, sliders, color=primary buttons). The HEADER BAR is
        # kept dark by [menu].header_bg (build_nav_css), decoupled from the accent
        # — else a blue accent would paint the whole header blue. The active nav
        # pill / tab fills / icon accent do NOT ride this either: they're hardcoded
        # rgba in _NAV_CSS (the JIT can't emit var()/rgba() arbitraries).
        ui.colors(primary=theme.MENU_ACCENT)
    # Browser tab: title = the selected menu item; favicon = this page's color.
    ui.page_title(window_title(_NAV_LABEL.get(active, theme.BRAND_NAME)))
    ui.add_head_html(_favicon_link(_TAB_COLOR.get(active, "#42a5f5")))
    # Icon rail: laid out at the rail width (or the open width when pinned); the
    # _NAV_CSS :hover rule expands only the ASIDE over the content (see the rail
    # comment there). behavior=desktop keeps Quasar from flipping it to a mobile
    # overlay at narrow viewports.
    _pinned = bool(app_settings.get("nav_pinned"))
    drawer = (ui.left_drawer(value=True, bordered=True)
              .classes("nav-drawer" + (" nav-pinned" if _pinned else ""))
              .props(f"behavior=desktop width={drawer_width(_pinned)}"))
    with drawer:
        # Deep Slate rail: the "WORKSPACE" caption + the icon-per-item nav.
        with ui.column().classes("h-full w-full flex flex-col gap-[2px]"):
            # nav-title = the [menu].title theme hook (build_nav_css sets its color).
            ui.label("WORKSPACE").classes(
                "nav-title font-semibold tracking-[.14em] text-[10.5px] px-3 pt-1 pb-2")
            # Flat main menu: one item per GROUP (its child pages render as the top
            # tab strip) + the single-page apps. No expandable sub-menus.
            opts_label, opts_icon, opts_children = _NAV_GROUPS[0]
            _nav_group_link(opts_label, opts_icon, opts_children, active)
            # Modelling tools (Calculator + Simulator) — a group, so it gets a tab
            # strip, sitting where the standalone Calculator rail item used to.
            tools_label, tools_icon, tools_children = _NAV_GROUPS[3]
            _nav_group_link(tools_label, tools_icon, tools_children, active)
            # Standalone rail pages that sit directly under the Options group.
            for _rp, _rl, _ri in OPTIONS_RAIL:
                _nav_link(_rp, _rl, _ri, active)
            sent_label, sent_icon, sent_children = _NAV_GROUPS[1]
            _nav_group_link(sent_label, sent_icon, sent_children, active)
            for path, label, icon in FLAT_NAV:
                _nav_link(path, label, icon, active)
            more_label, more_icon, more_children = _NAV_GROUPS[2]
            _nav_group_link(more_label, more_icon, more_children, active)

    with ui.header().classes("items-center justify-between px-4"):
        with ui.row().classes("items-center gap-3 no-wrap"):
            ui.button(icon="menu", on_click=lambda: _toggle_pin(drawer)).props(
                "flat round dense color=white size=sm").tooltip("Pin / unpin the menu")
            ui.html(brand_lockup_html())
        with ui.row().classes("items-center gap-4 no-wrap"):
            _section, _tab = breadcrumb_parts(active)
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.label(_section).classes("text-[12.5px] text-[#8891ab]")
                if _tab:
                    ui.element("div").classes("w-[4px] h-[4px] rounded-full bg-[#4a557a]")
                    ui.label(_tab).classes("text-[12.5px] text-[#a9b6ff] font-medium")
            _mkt_label, _mkt_open = market_status_parts()
            with ui.row().classes(
                    f"mkt-pill {'mkt-open' if _mkt_open else 'mkt-closed'} no-wrap"):
                ui.element("div").classes("dot")
                ui.label(_mkt_label).classes("lbl text-[11.5px] font-medium tracking-[.03em]")

    # Sub-menu TAB STRIP (2026-07-11): the active group's child pages as
    # folder-style tabs across the top of the page (.compact-tabs in _NAV_CSS).
    # Clicking a tab navigates; the per-page alert badges float on the tabs. A
    # SUBTAB slot sits directly beneath the strip — a page with its own view tabs
    # (e.g. Gamma's GEX/Charm/DEX/Vanna/Flow/Term) renders them there via
    # ``main.subtab_slot()`` so they read as a second tab level, not page chrome.
    _SUBTAB_SLOT["el"] = None
    children = _group_children(active)
    if children:
        with ui.element("div").classes("w-full px-2 pt-1"):
            strip = ui.tabs(value=active).classes("compact-tabs w-full").props(
                "dense no-caps align=left inline-label")
            with strip:
                for path, label, _icon in children:
                    with ui.tab(path, label=label):
                        _help_tooltip(path)   # rest the mouse 2 s for the guide
                        _badge_refs[path] = _count_badge(_NAV_BADGES.get(path, 0))
            strip.on_value_change(lambda e: ui.navigate.to(e.value)
                                  if e.value != active else None)
            _SUBTAB_SLOT["el"] = ui.element("div").classes("w-full pl-3")
    else:
        # Flat pages (no group strip) still get the slot so a page's own view
        # tabs (e.g. Portfolio Holdings/Sectors/Performance) mount at the top in
        # the same position as the group pages' subtabs.
        with ui.element("div").classes("w-full px-2 pt-1"):
            _SUBTAB_SLOT["el"] = ui.element("div").classes("w-full")

    # Hidden audio element used by play_alert (one per page/client).
    ui.html('<audio id="alert-audio" preload="auto"></audio>')

    # Acknowledge the badge for the page being opened, then run the app-wide
    # alert/badge watcher on every page so the chime fires regardless of route.
    # Reuse the scan payload already read above (no second deserialize).
    _acknowledge(active, scan=_scan)

    @guard_async
    async def _tick():
        # Run the blocking bus reads + the proxy health re-warm OFF the event loop;
        # then do the (UI-thread-only) chime + badge updates back here after await.
        # ``_guarded_compute`` swallows a Memurai/bus outage (logging once, not a
        # traceback every 2s) → None; ``@guard_async`` swallows a client-disconnect
        # race after the await. Either way the tick is a clean no-op.
        decision = await run.io_bound(_guarded_compute)
        # Re-warm the proxy-health memo through its TTL gate (cached_health), NOT
        # _refresh_health directly — the unconditional call made every open tab
        # issue a proxy HTTP GET every 2s, defeating the _HEALTH_TTL_SEC memo.
        await run.io_bound(cached_health)
        if decision:
            scanner = decision.get("scanner")
            health = decision.get("health")
            if scanner:
                sound, volume, desktop, n = scanner
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
            if health:
                sound, volume, desktop, n = health
                play_alert(sound, volume)
                # Amber warning toast — distinct from the blue scanner toast so a
                # service alert is not confused with a new trading signal.
                ui.notify(alerts.health_alert_text(n), icon="warning", color="orange-9")
                if desktop:
                    notify_desktop("Service alert",
                                   f"{n} component(s) stale or down — see System Status.")
            flow = decision.get("flow")
            if flow:
                sound, volume, desktop, new_flow = flow
                play_alert(sound, volume)   # one chime per tick, not per alert
                for a in new_flow:
                    bullish = (a.get("type") == "crossover" and a.get("side") == "calls_over") \
                        or (a.get("type") == "uoa" and a.get("side") == "call")
                    ui.notify(a.get("text", ""), icon="insights",
                              color="green-8" if bullish else "red-8")
                if desktop and new_flow:
                    notify_desktop("Options-flow alert", new_flow[0].get("text", ""))
        for route, badge in _badge_refs.items():
            _set_badge(badge, _NAV_BADGES.get(route, 0))
        for _label, (badge, paths) in _group_badge_refs.items():
            _set_badge(badge, sum(_NAV_BADGES.get(p, 0) for p in paths))

    ui.timer(2.0, _tick)

    # Fixed bottom market-summary marquee on every page (gated by the Settings
    # toggle). ui.footer() is fixed-position, so its DOM order doesn't matter.
    from pages import ticker
    ticker.render_ticker(active)

    # pb-10 keeps the fixed footer marquee from covering the last content row.
    with ui.column().classes("w-full p-4 gap-3 pb-10") as content:
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
    with _layout("/", "Options · Market Scanner"):
        from pages.options import scanner
        scanner.render()


@ui.page("/options/paper")
def options_paper_page() -> None:
    with _layout("/options/paper", "Options · Paper Ledger"):
        from pages.options import paper
        paper.render()


@ui.page("/options/captured")
def options_captured_page() -> None:
    with _layout("/options/captured", "Options · Captured Signals"):
        from pages.options import captured
        captured.render()


@ui.page("/options/portfolio")
def options_portfolio_page() -> None:
    with _layout("/options/portfolio", "Options · Paper Account"):
        from pages.options import portfolio
        portfolio.render()


@ui.page("/options/calculator")
def options_calculator_page() -> None:
    with _layout("/options/calculator", "Calculator"):
        from pages.options import calculator
        calculator.render()


@ui.page("/options/swing")
def options_swing_page() -> None:
    with _layout("/options/swing", "Options · Strategy Finder"):
        from pages.options import swing
        swing.render()


@ui.page("/options/gamma")
def options_gamma_page() -> None:
    with _layout("/options/gamma", "Dealer Positioning"):
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


@ui.page("/options/matrix")
def options_matrix_page() -> None:
    with _layout("/options/matrix", "Opportunity Board"):
        from pages.options import matrix
        matrix.render()


@ui.page("/sentiment")
def sentiment_page() -> None:
    with _layout("/sentiment", "Sentiment"):
        from pages import sentiment
        sentiment.render()


@ui.page("/sentiment/sectors")
def sentiment_sectors_page() -> None:
    with _layout("/sentiment/sectors", "Sector & Industry"):
        from pages import sentiment_sectors
        sentiment_sectors.render()


@ui.page("/sentiment/rotation")
def sentiment_rotation_page() -> None:
    with _layout("/sentiment/rotation", "Sector Rotation"):
        from pages import sentiment_rotation
        sentiment_rotation.render()


@ui.page("/sentiment/rrg")
def sentiment_rrg_page() -> None:
    with _layout("/sentiment/rrg", "RRG"):
        from pages import sentiment_rrg
        sentiment_rrg.render()


@ui.page("/sentiment/momentum")
def sentiment_momentum_page(level: str = "industry") -> None:
    # ?level=stock deep-links the Stocks view (the dropdown still switches it
    # in place); render() coerces anything unknown back to industry.
    with _layout("/sentiment/momentum", "Momentum"):
        from pages import sentiment_momentum
        sentiment_momentum.render(level=level)


@ui.page("/trade")
def trade_page() -> None:
    with _layout("/trade", "Trade Analyzer"):
        from pages import trade
        trade.render()


@ui.page("/portfolio")
def portfolio_page() -> None:
    with _layout("/portfolio", "Portfolio"):
        from pages import portfolio
        portfolio.render()


@ui.page("/driver")
def driver_page() -> None:
    with _layout("/driver", "Claude Trades"):
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


@ui.page("/market")
def market_page() -> None:
    with _layout("/market", "Market Trend & Sentiment · Market Dashboard"):
        from pages import market
        market.render()


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
    with _layout("/terminate", "Stop All Services"):
        from pages import terminate
        terminate.render()


if __name__ in {"__main__", "__mp_main__"}:
    # Lifecycle handlers register HERE, not at module scope: pages `import main`
    # lazily at request time (e.g. pages/options/scanner.py for subtab_slot), and
    # because this script runs as __main__ that re-executes this file as a second
    # module object AFTER NiceGUI has started — where app.on_startup() raises and
    # 500s the page. Inside this guard it runs once, before ui.run().
    app.on_startup(sync_ticker_setting)

    # Bind to localhost only (single-user, localhost-first app): reachable at
    # http://localhost:8500 from this PC. This avoids listening on every network
    # interface (the default host="0.0.0.0"), which on Windows produced benign but
    # noisy `OSError [WinError 64] "network name is no longer available"` accept
    # tracebacks whenever a transient/virtual adapter (link-local 169.254.x, WSL/
    # Docker) dropped — and keeps the trading app off the LAN.
    ui.run(host="127.0.0.1", port=NICEGUI_PORT, title=window_title(),
           dark=True, reload=False, show=False)
