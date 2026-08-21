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

from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: E402
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
from pages.ui_guard import guard  # noqa: E402
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


def sync_captured_autoclose_setting() -> None:
    """Re-assert the captured auto-close toggle to options_svc at startup (best-effort).

    ``captured_autoclose_enabled`` is a webgui setting that gates the service's
    scheduled captured-manage cycle. The service defaults to ENABLED on a missing
    key, so a wiped/restarted Memurai would silently resume auto-close while the GUI
    showed it off — settings.json is the source of truth, so restate it every
    startup. Never raises (a down bus must not stop the web GUI from booting)."""
    try:
        enabled = bool(app_settings.get("captured_autoclose_enabled"))
        bus_client.request("options", {"type": "set_autoclose",
                                       "args": {"enabled": enabled}})
    except Exception:  # noqa: BLE001
        logging.getLogger("webgui").warning(
            "captured autoclose setting resync failed", exc_info=True)


def sync_manual_paper_lifecycle_setting() -> None:
    """Re-assert the manual-paper break-even-lifecycle toggle to options_svc at
    startup (best-effort).

    ``manual_paper_lifecycle_enabled`` is a webgui setting (default OFF) that
    gates the manual paper account's opt-in captured-style lifecycle. Mirrors
    ``sync_captured_autoclose_setting``: settings.json is the source of truth, so
    restate it every startup — a wiped/restarted Memurai defaults the service's
    own copy back OFF too, but an explicit user ON must still be re-asserted.
    Never raises (a down bus must not stop the web GUI from booting)."""
    try:
        enabled = bool(app_settings.get("manual_paper_lifecycle_enabled"))
        bus_client.request("options", {"type": "set_manual_paper_lifecycle",
                                       "args": {"enabled": enabled}})
    except Exception:  # noqa: BLE001
        logging.getLogger("webgui").warning(
            "manual paper lifecycle setting resync failed", exc_info=True)


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
    ("/options/scanner", "Market Scanner", "radar"),
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
    ("/sentiment/bullbear", "Bull / Bear Map", "account_tree"),
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
    ("/options/flow", "Flow Alerts", "bolt"),
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
    ("/desk", "Desk", "space_dashboard"),
    ("/trade", "Trade Analyzer", "query_stats"),
    ("/portfolio", "Portfolio", "account_balance"),
    ("/driver", "Claude Trades", "smart_toy"),
]

# "More" is a menu GROUP for reports / documentation. Its tab strip is
# MORE_CHILDREN + SETTINGS_CHILDREN, so User Manuals renders as a flat PEER tab
# of EOD Report — the old indented sub-group is retired. (route, label, icon)
MORE_CHILDREN = [
    ("/eod", "EOD Report", "summarize"),
]

# Sub-menu items that used to nest under the Settings entry; Settings is now a
# standalone rail page (SYSTEM_RAIL), so this renders as a More tab. (route, label, icon)
SETTINGS_CHILDREN = [
    ("/manuals", "User Manuals", "menu_book"),
]

# Standalone MAIN-MENU (rail) pages pinned to the BOTTOM of the drawer (2026-08-12).
# These are the machine-level controls — health, configuration, shutdown — not part
# of any analysis workflow, so they were lifted out of the "More" tab strip and given
# their own block at the foot of the rail (the conventional place for them). Like
# OPTIONS_RAIL they are standalone: no tab strip, breadcrumb is just the page name.
# (route, label, icon)
SYSTEM_RAIL = [
    ("/status", "System Status", "monitor_heart"),
    ("/settings", "Settings", "settings"),
    ("/terminate", "Stop All Services", "power_settings_new"),
]

# The one DESTRUCTIVE item in the rail. It is rendered as a danger-outlined
# button rather than a fourth navigation row (see ``_nav_danger_link``) and sits
# last, so "stop everything" never sits mid-list where Settings is aimed for.
SYSTEM_DANGER_ROUTE = "/terminate"

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
    ("Trend & Sentiment", "speed", SENTIMENT_CHILDREN),
    ("More", "more_horiz", MORE_CHILDREN + SETTINGS_CHILDREN),
    ("Strategy Tools", "build", STRATEGY_TOOLS_CHILDREN),
]


# ── Captioned rail sections (2026-08-16 nav redesign) ────────────────────────
# The drawer's reading order used to be implicit in the SEQUENCE of render calls
# inside ``_layout`` — the only way to reorder it was to edit render code. It is
# now data: three captioned sections, each a list of entries that are either a
# GROUP (rendered by ``_nav_group_link``, its children becoming the top tab
# strip) or a standalone RAIL PAGE (``_nav_link``).
#
# Entries reference their group/page BY NAME rather than restating it, so
# _NAV_GROUPS / OPTIONS_RAIL / FLAT_NAV stay the single source of each item's
# label + icon (which is also what the icon-distinctness test iterates). A typo
# raises at import — the module fails loudly instead of silently dropping a page
# out of the menu.
#
# Note the deliberate split: Dealer Positioning / Opportunity Board / Flow Alerts
# sit under MARKETS as market-WIDE reads, while the Options group under STRATEGY
# is the per-signal find → analyze → track → repair workflow.
def _sec_group(label: str):
    """NAV_SECTIONS entry for the ``_NAV_GROUPS`` group called ``label``."""
    for lbl, icon, children in _NAV_GROUPS:
        if lbl == label:
            return ("group", lbl, icon, children)
    raise KeyError(f"no nav group named {label!r}")


def _sec_page(route: str):
    """NAV_SECTIONS entry for the standalone rail page at ``route``."""
    for path, label, icon in OPTIONS_RAIL + FLAT_NAV:
        if path == route:
            return ("page", path, label, icon)
    raise KeyError(f"no rail page at {route!r}")


# (caption, entries). The caption's COUNT is derived from len(entries) at render
# time — never written down, so it cannot go stale when a page is added.
#
# A caption of None means "render NO header at all" — not an empty one. The Desk
# is the landing page, so it is pinned ALONE above every caption: the rail's
# mirror of the bottom-pinned SYSTEM_RAIL block, marking it as home rather than
# filing it under one of the three workflow sections. Its breadcrumb is likewise
# just ["Desk"], since there is no section to name above it.
NAV_SECTIONS = [
    (None, [_sec_page("/desk")]),
    ("MARKETS", [
        _sec_page("/options/gamma"),      # Dealer Positioning
        _sec_page("/options/matrix"),     # Opportunity Board
        _sec_page("/options/flow"),       # Flow Alerts
        _sec_group("Trend & Sentiment"),
    ]),
    ("STRATEGY", [
        _sec_group("Strategy Tools"),
        _sec_group("Options"),
        _sec_page("/trade"),              # Trade Analyzer
        _sec_page("/driver"),             # Claude Trades
    ]),
    ("ACCOUNT", [
        _sec_page("/portfolio"),
        _sec_group("More"),
    ]),
]


def _group_children(active: str):
    """The child list of the group containing ``active``, or None (flat page)."""
    for _label, _icon, children in _NAV_GROUPS:
        if any(path == active for path, _l, _i in children):
            return children
    return None


# ── Deep Slate shell helpers (Phase 2) — pure, unit-tested in test_shell.py ──
# The section the SYSTEM_RAIL pages belong to. They sit in their own bottom-pinned
# block rather than under a NAV_SECTIONS caption, so the breadcrumb names it here —
# leaving them section-less would reintroduce the very inconsistency the trail
# below exists to remove.
SYSTEM_SECTION = "System"


def breadcrumb_trail(active: str):
    """The header breadcrumb as a LIST of labels, root first.

    Every page reads the same way — the path you would take through the menu to
    reach it::

        Markets   ›  Dealer Positioning              (a standalone rail page)
        Markets   ›  Trend & Sentiment  ›  RRG       (a page inside a group)
        Strategy  ›  Options  ›  Market Scanner
        System    ›  Settings                        (the footer block)

    Until 2026-08-16 a rail page rendered as its own name with NO parent while a
    grouped page rendered "Group › Page", so the first crumb meant a different
    thing depending on where you happened to be — "Dealer Positioning" and
    "Options" occupied the same slot despite being a page and a group. Captioned
    sections make one scheme possible for every item.

    Captions are stored upper-case for the rail's mono type; the header wants
    them as words, hence ``.title()``. Empty labels are dropped so a route
    missing from ``_NAV_LABEL`` degrades to its section rather than rendering a
    trailing separator with nothing after it.
    """
    for caption, entries in NAV_SECTIONS:
        # A None caption is the pinned landing block (see NAV_SECTIONS): it has no
        # section name, so its pages render as a bare leaf rather than growing an
        # empty leading crumb and a stray separator.
        head = caption.title() if caption else ""
        for entry in entries:
            if entry[0] == "group":
                _kind, label, _icon, children = entry
                if any(path == active for path, _l, _i in children):
                    return [c for c in (head, label,
                                        _NAV_LABEL.get(active, "")) if c]
            elif entry[1] == active:
                return [c for c in (head, entry[2]) if c]
    for path, label, _icon in SYSTEM_RAIL:
        if path == active:
            return [SYSTEM_SECTION, label]
    return [_NAV_LABEL.get(active, theme.BRAND_NAME)]


# Breadcrumb crumb styling — the trailing crumb is the thing you are looking at,
# everything before it is context. Named because the LEAF swaps them at runtime.
_CRUMB_LEAF = "text-[13px] text-[#e6ecf9] font-semibold"
_CRUMB_CONTEXT = "text-[13px] text-[#5d6a88]"

# The optional FOURTH crumb: a page's own active view (Dealer Positioning ›
# Gamma, Simulator › Replay …). Single-user module state, rebuilt per layout like
# the badge refs. "parent" is the last trail crumb, which has to be demoted to
# context when a view is named after it.
_breadcrumb_leaf: dict = {}


def _view_name(value):
    """A subtab's name from whatever a ``ui.tabs`` element holds.

    Pages build their tabs either by NAME (``ui.tab("Replay")`` → the value is
    the string) or by ELEMENT (Rescue passes the tab object to ``ui.tab_panels``,
    so the value can be the element). Reading the element's ``name`` prop covers
    both without every call site having to know which it is."""
    if value is None:
        return ""
    props = getattr(value, "_props", None)
    if isinstance(props, dict) and props.get("name"):
        return str(props["name"])
    return str(value)


def set_breadcrumb_leaf(label, refs=None) -> None:
    """Show ``label`` as the last breadcrumb crumb, or hide the leaf when falsy.

    ``refs`` targets a SPECIFIC header's elements instead of whichever page built
    the layout most recently. That matters because ``_breadcrumb_leaf`` is
    module-level, and unlike the badge refs — which every client rewrites with the
    same numbers, so the sharing is invisible — each page writes a DIFFERENT view
    name here. With two tabs open the second page's build reassigns the module
    state and the first tab's tab-change handler then writes into the second
    tab's header: caught in prod, where the promote script opens a tab on the
    Scanner and Dealer Positioning's header went on to read "› 0-DTE".

    Never raises and is a no-op without a mounted header, so a page may call it
    unconditionally."""
    refs = _breadcrumb_leaf if refs is None else refs
    if not refs.get("label"):
        return
    text = str(label or "").strip()
    refs["label"].text = text
    refs["label"].set_visibility(bool(text))
    refs["caret"].set_visibility(bool(text))
    parent = refs.get("parent")
    if parent is not None:
        # The page name stops being the leaf the moment a view is named after it.
        parent.classes(remove=f"{_CRUMB_LEAF} {_CRUMB_CONTEXT}",
                       add=_CRUMB_CONTEXT if text else _CRUMB_LEAF)


def bind_breadcrumb_leaf(tabs, labeller=None, initial=None) -> None:
    """Track a page's own view tabs in the breadcrumb: ``… › Page › View``.

    A page's subtabs ARE a level of the hierarchy — Dealer Positioning read the
    same in the header whether you were on Gamma or on Net Prem — but they switch
    CLIENT-side without rebuilding the layout, so the crumb has to ride the same
    event that switches the view.

    Registers an ADDITIONAL ``on_value_change`` handler (NiceGUI appends them), so
    a page's existing handler is untouched, and paints the initial value at build
    time so the first render is already correct rather than correcting itself on
    the first click. ``labeller`` maps the raw tab value to what the header should
    read — Gamma needs it, since its "GEX" tab is displayed as "Gamma".

    ``initial`` is required by every page that builds a bare ``ui.tabs()`` and
    names its default on the ``ui.tab_panels`` instead — four of the five do. That
    default reaches the tabs element through NiceGUI's BINDING, which propagates
    on a later cycle, so ``tabs.value`` is still None while the page is being
    built and the crumb would sit blank until the first click.

    The header elements are CAPTURED here rather than looked up when the handler
    fires: ``_breadcrumb_leaf`` is module-level, so a second tab's page build
    reassigns it and this page's handler would otherwise write its view name into
    the other tab's header (see ``set_breadcrumb_leaf``)."""
    fmt = labeller or _view_name
    refs = dict(_breadcrumb_leaf)
    set_breadcrumb_leaf(fmt(tabs.value if tabs.value is not None else initial), refs)

    @guard
    def _sync(e) -> None:
        set_breadcrumb_leaf(fmt(e.value), refs)

    tabs.on_value_change(_sync)


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


def brand_lockup_html(static_dir=None, *, mark=True):
    """The header lockup: the logo mark (when present) + the two-tone wordmark.

    Raw HTML rather than NiceGUI elements because each wordmark half needs a
    gradient clipped to its text (``theme.build_brand_css``), which Tailwind's
    bundled JIT can't express. The name comes from ``[brand]`` config, so it is
    HTML-escaped.

    In dev the lockup carries a DEV chip: two identical-looking tabs that write
    to DIFFERENT paper books is a mistake waiting to happen. Inline style for the
    same reason as the rest of this function — it is a raw HTML string, not a
    NiceGUI element with ``.classes()``.

    ``mark=False`` emits the WORDMARK only. ``_layout`` uses that, because since
    2026-08-16 the logo is the menu's pin control and therefore has to be a real
    NiceGUI element with a click handler and a tooltip — it cannot live inside an
    inert HTML string."""
    mark = brand_mark_src(static_dir) if mark else ""
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


def nav_pin_tooltip():
    """Hover text for the logo, which is the menu's pin control.

    States BOTH directions rather than the current one. The tooltip is built once
    per page render while ``_toggle_pin`` changes the state without rebuilding, so
    a state-aware string ("Unpin the menu") would be wrong the moment you used it
    — and storing a ref to correct it would reintroduce the cross-tab sharing bug
    the breadcrumb leaf just had. The old wording, "Pin / unpin the menu", named
    the control rather than the outcome and left you to work out which half
    applied to the state you were in."""
    return "Pin the menu open, or unpin it back to the icon rail"


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
# what ``_layout`` lays the drawer out at when PINNED. The open width is
# interpolated into that CSS rule from this constant — a test pins the two
# together, so changing it here is enough.
NAV_WIDTH_RAIL = 68
NAV_WIDTH_OPEN = 264


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
              + MORE_CHILDREN + SETTINGS_CHILDREN + SYSTEM_RAIL}

# One distinct color per route (the favicon fill). Material hues, all visually apart.
_TAB_COLOR = {
    "/desk": "#f5c542",                   # Desk — gold, the landing page
    "/options/scanner": "#42a5f5",        # Market Scanner — blue
    "/options/matrix": "#4dd0e1",         # Opportunity Board — cyan
    "/options/flow": "#d500f9",           # Flow Alerts — magenta
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
    "/sentiment/bullbear": "#651fff",     # Bull / Bear Map — violet
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
# Version-gated payload memos for the 2 s watcher. `options:scan` (148 KB) and
# `options:flow_alerts` (90 KB) were read+parsed on EVERY tick per open tab —
# ~43,200 ticks/day x 237 KB is ~10 GB/day/tab for views that change a handful of
# times an hour. read_gated pays a tiny :ver probe instead (2026-08-20).
_WATCH_SCAN_MEMO: dict = {}
_WATCH_FLOW_MEMO: dict = {}


def reset_watcher_memos() -> None:
    """Drop the watcher's payload memos (test helper; also safe after a manual
    cache edit)."""
    _WATCH_SCAN_MEMO.clear()
    _WATCH_FLOW_MEMO.clear()


_ALERT_STATE: dict = {
    "acked_scan": set(), "alerted": set(), "alerted_init": None,
    # Captured badge: the SET of acknowledged captured signal ids (not a version),
    # plus a version-gated cache of the current ids so the badge fires on a
    # genuinely new capture rather than on every reprice-republish. captured_keys is
    # recomputed only when captured_keys_ver moves (the cheap :ver probe gates the
    # payload deserialize).
    "captured_acked": set(), "captured_keys": set(), "captured_keys_ver": None,
    "rescue_seen": None,
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

# Drawer notification dots (2026-08-16). The RAIL shows counts nowhere — a number
# is unreadable at 68px and was overlapping the icon — so a drawer row signals
# "something here" with a plain red dot and the exact count stays on the tab
# strip, which is where you go to act on it. Two dots per row, one per drawer
# state (see _alert_dot): a corner dot while collapsed, a right-aligned dot once
# the labels are visible. Keyed by route / group label, exactly like the badge
# refs above, and driven from the same _NAV_BADGES numbers.
_alert_refs: dict = {}
_group_alert_refs: dict = {}

# Footer status-card state + element refs, in the same shape as the badge pair
# above: _STATUS_CARD is what the (off-thread) watcher computes, _status_refs is
# what the UI-thread tick writes it into. Seeded "unknown" so the very first
# paint — before any probe has run — cannot claim a live feed.
_STATUS_CARD: dict = {"tone": "unknown", "title": "Data feed unknown",
                      "detail": "no probe yet", "count": 0}
_status_refs: dict = {}

# Static, non-numeric badges on rail items (route -> short label). Distinct from
# _NAV_BADGES, which is live watcher state: these never change, so they are not
# registered for the 2s tick to update.
_NAV_PILLS = {"/driver": "AI"}

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
_svc_health_cache: dict = {"data": {}, "ts": 0.0, "latency_ms": None}

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
        took: list = []
        for svc in _HEALTH_SERVICES:
            url = SERVICE_URLS.get(svc)
            if not url:
                out[svc] = None
                continue
            t0 = _time.monotonic()
            try:
                r = requests.get(f"{url}/health", timeout=_HEALTH_HTTP_TIMEOUT)
                out[svc] = (r.status_code == 200 and r.json().get("up") is True)
                # Time only the calls that ANSWERED: a timed-out service
                # contributes _HEALTH_HTTP_TIMEOUT and would drag the reported
                # latency to a number describing the failure, not the feed. That
                # a service is down is the count badge's job to say.
                if out[svc]:
                    took.append((_time.monotonic() - t0) * 1000.0)
            except Exception:  # noqa: BLE001 — a probe must never raise
                out[svc] = False
        _svc_health_cache["data"] = out
        _svc_health_cache["latency_ms"] = (sum(took) / len(took)) if took else None
        _svc_health_cache["ts"] = now_mono
    return dict(_svc_health_cache["data"])


# One tone per state; swapped with .classes(remove=…, add=…) so repeated repaints
# can't stack conflicting colours (the documented reactive-recolour idiom).
_STATUS_TONE_CLASSES = "nav-status-live nav-status-warn nav-status-unknown"


def status_card_facts(health, unhealthy_n, latency_ms=None):
    """``{tone, title, detail, count}`` for the drawer's footer status card — PURE.

    * ``health`` — ``_probe_services_health``'s ``{service: up|None}``.
    * ``unhealthy_n`` — the count already driving ``_NAV_BADGES["/status"]``, so
      the card and the System Status badge are the SAME number by construction
      and cannot drift apart.
    * ``latency_ms`` — the mean round-trip of the services that ANSWERED the last
      probe (see ``_probe_services_health``), or None.

    An empty/missing ``health`` map means the probe has not run or could not run,
    and that renders as **unknown** — never "live". A defensive default that
    reads as a confident measurement is the exact failure this app has been
    bitten by before; a status card claiming a live feed over no data would be a
    new instance of it.
    """
    if not health:
        return {"tone": "unknown", "title": "Data feed unknown",
                "detail": "no probe yet", "count": 0}
    up = sum(1 for v in health.values() if v)
    total = len(health)
    n = max(0, int(unhealthy_n or 0))
    detail = f"{total} services" if up == total else f"{up}/{total} services"
    if latency_ms is not None:
        try:
            detail += f" · {int(round(float(latency_ms)))} ms"
        except (TypeError, ValueError):
            pass
    return {"tone": "live" if n == 0 else "warn",
            "title": "Data feed live" if n == 0 else "Data feed degraded",
            "detail": detail, "count": n}


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
    # A view whose publisher only runs during the session says nothing about
    # health outside it — the scanner's newest write is legitimately ~43 h old on
    # a Sunday, which used to read as one degraded component all weekend. See
    # alerts.RTH_ONLY_VIEWS for why this is a narrow list and not a blanket gate.
    if view is not None and not alerts.expects_updates(view, now_utc):
        return False
    if not ts:
        return True
    try:
        when = _dt.datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return (now_utc - when).total_seconds() > alerts.stale_after(view, now_utc)

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
   The open width is interpolated from NAV_WIDTH_OPEN — see the f-string
   appended after this block. */
.q-drawer:has(> .nav-drawer:not(.nav-pinned)) { transition: width .18s ease; }
/* .nav-drawer IS the real scroller (Quasar's .scroll sets overflow:auto), so the
   clip belongs here: it stops the 248px of content from raising a horizontal
   scrollbar in the 64px rail. It cannot clip the corner count badges — they sit
   ~40px from the drawer's left edge, well inside the rail. */
.nav-drawer { overflow-x: hidden; }
/* A thin scrollbar, because the rail cannot spare a default one. .nav-drawer IS
   the scroller, and once the menu is taller than the window (measured: 637px of
   content against 579px at a 720px-tall window — a laptop, not a contrived case)
   a stock ~15px scrollbar takes a fifth of the 68px rail, squeezing every row and
   the status card down to 24px. flex-nowrap makes that scroll happen instead of
   wrapping, so this is the other half of that fix. */
.nav-drawer { scrollbar-width: thin; scrollbar-color: rgba(255,255,255,.16) transparent; }
.nav-drawer::-webkit-scrollbar { width: 6px; }
.nav-drawer::-webkit-scrollbar-track { background: transparent; }
.nav-drawer::-webkit-scrollbar-thumb {
    background: rgba(255,255,255,.16); border-radius: 3px; }
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
/* Section captions (MARKETS / STRATEGY / ACCOUNT) are unreadable in a 68px rail,
   so the collapsed state shows a hairline where the caption would be. .nav-sep is
   the exact INVERSE of the .nav-title rule above — visible by default, faded out
   under the same three "drawer is open" selectors. Both live inside one
   fixed-height, position:relative header box (see _nav_section_header), so this
   is a cross-fade in place: neither state reflows the rail. */
.nav-drawer .nav-sep { opacity: 1; transition: opacity .14s ease; }
.nav-drawer.nav-pinned .nav-sep,
.nav-drawer:hover .nav-sep,
.nav-drawer:focus-within .nav-sep { opacity: 0; }
/* ── Notification dots ─────────────────────────────────────────────────────
   A drawer row shows a plain red dot, never a number: at 68px a count is
   unreadable and it sat ON the icon. Each row mounts TWO dots and this swaps
   them, because their POSITION has to change with the drawer state and a single
   element cannot be both. Collapsed → the corner of the icon, the only thing
   visible. Open → the right end of the row, clear of the label (an ml-auto dot
   in the rail would sit at x~250, i.e. clipped out of sight).
   Both are hidden unless the row carries .nav-alert-on, which _alert_dot adds —
   deliberately NOT set_visibility, whose single-class .hidden these rules would
   out-specify, showing a dot on every row the moment the drawer opened.
   The open-state rules need the extra pseudo-class to beat the 3-class rail rule
   above them; that is why they repeat .nav-alert-on rather than relaxing it. */
.nav-drawer .nav-alert-rail, .nav-drawer .nav-alert-open { display: none; }
.nav-drawer .nav-alert-rail.nav-alert-on { display: block; }
.nav-drawer.nav-pinned .nav-alert-rail.nav-alert-on,
.nav-drawer:hover .nav-alert-rail.nav-alert-on,
.nav-drawer:focus-within .nav-alert-rail.nav-alert-on { display: none; }
.nav-drawer.nav-pinned .nav-alert-open.nav-alert-on,
.nav-drawer:hover .nav-alert-open.nav-alert-on,
.nav-drawer:focus-within .nav-alert-open.nav-alert-on { display: block; }
/* Footer service-status card. The CARD stays in both states — the feed's health
   is the one thing in the footer you want visible without opening anything — but
   at 68px only its DOT fits, so the text column and the count are dropped and the
   dot sits on the shared icon column. display, not opacity, so the hidden parts
   surrender their width instead of pushing the dot out of the rail.
   The count needs the same .nav-status-count-on gate the alert dots use: NiceGUI's
   set_visibility toggles the single-class .hidden, which these two-class state
   rules would out-specify, so a zero count would reappear the moment the drawer
   opened.
   @keyframes is the one thing no Tailwind utility expresses, so it belongs in
   this Quasar-internal block rather than being a new exception. */
.nav-drawer .nav-status-text, .nav-drawer .nav-status-count { display: none; }
.nav-drawer.nav-pinned .nav-status-text,
.nav-drawer:hover .nav-status-text,
.nav-drawer:focus-within .nav-status-text { display: flex; }
.nav-drawer.nav-pinned .nav-status-count.nav-status-count-on,
.nav-drawer:hover .nav-status-count.nav-status-count-on,
.nav-drawer:focus-within .nav-status-count.nav-status-count-on { display: block; }
@keyframes ns-pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: .35; transform: scale(.82); }
}
.nav-status-dot { animation: ns-pulse 2.4s ease-in-out infinite; }
.nav-status-live { background: #3ddc97; box-shadow: 0 0 0 3px rgba(61,220,151,.15); }
.nav-status-warn { background: #f5a524; box-shadow: 0 0 0 3px rgba(245,165,36,.15); }
/* Unknown = the probe or the bus failed. Deliberately NOT animated: a pulse says
   "live", and a card that cannot measure anything must not claim to be. */
.nav-status-unknown { background: #6b7898; animation: none; }
/* Active icon accent. MUST be !important AND 3 classes: theme.build_nav_css
   emits `.nav-drawer .q-icon{color:<[menu].text>!important}` (2 classes) and is
   injected AFTER this block, so equal-specificity would lose. */
.nav-drawer .nav-active .nav-icon { color: #6b86ff !important; }
/* Same specificity argument for the danger button, and it applies to its LABEL as
   well as its icon — measured in a live browser, where the label rendered
   #98a1c0: build_nav_css's `.nav-drawer a{color:<[menu].text>!important}` paints
   the <a>, and a label with no colour of its own simply inherits it. A Tailwind
   text-[#ff8f92] on the link cannot win against an !important. */
.nav-drawer .nav-danger .nav-icon,
.nav-drawer .nav-danger .nav-label { color: #ff8f92 !important; }
/* The static AI pill, for the third instance of the same problem: on the ACTIVE
   row, `.nav-drawer .nav-active .nav-label` (3 classes) beat the pill's 1-class
   Tailwind colour, so the pill turned white on exactly the one row it is on. */
.nav-drawer .nav-pill { color: #4da3ff !important; }
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
/* Header brand mark — theme.build_brand_css sizes it; this adds the design's
   hairline so the logo doesn't float on the bar. */
.brand-mark { border: 1px solid rgba(255,255,255,.1); }
/* The logo IS the menu's pin control (2026-08-16), so it is a q-btn — which
   arrives with its own padding and min-height and would otherwise inflate the
   bar. Stripping those lets the image alone define the button's box.
   The header's own vertical padding is halved at the same time: the bar measured
   60px = 16px padding x2 + 28px of content, so the padding is exactly what was
   capping the logo. 8 + 44 + 8 is the same 60px with a 44px mark — the largest
   that fits while keeping real clearance and not moving the bar by a pixel. */
.q-header { padding-top: 8px; padding-bottom: 8px; }
.brand-mark-btn.q-btn {
  padding: 0; min-height: 0; min-width: 0; border-radius: 12px;
}
.brand-mark-btn .q-btn__content { padding: 0; }
.brand-mark-btn:hover .brand-mark { border-color: rgba(255,255,255,.28); }
"""

# The rail's open width is the ONE CSS value that must equal a Python constant —
# Quasar lays the drawer out at the RAIL width and only this rule widens the
# aside on hover, so a drift between the two would leave the expanded menu
# clipped. Appended as its own f-string because the block above is a plain
# literal (interpolating it would mean escaping every CSS brace in the file).
_NAV_CSS += f"""
.q-drawer:has(> .nav-drawer:not(.nav-pinned)):hover,
.q-drawer:has(> .nav-drawer:not(.nav-pinned)):focus-within {{
    width: {NAV_WIDTH_OPEN}px !important; box-shadow: 0 12px 40px rgba(0,0,0,.5); }}
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
    if active == "/options/scanner":                    # Scanner
        if scan is None:
            scan = bus_client.read("options:scan") or {}
        _ALERT_STATE["acked_scan"] = alerts.scanner_keys(scan)
    elif active == "/options/captured":
        # Acknowledge the current SET of captured signal ids (not the version), so
        # the badge clears on open and only re-appears on a genuinely NEW capture —
        # not on the 5-min reprice-republish, which keeps the same ids but bumps the
        # version (that version-churn was the "badge keeps showing" bug).
        _ALERT_STATE["captured_keys_ver"] = bus_client.read_version("options:captured")
        _ALERT_STATE["captured_keys"] = alerts.captured_keys(
            bus_client.read("options:captured") or {})
        _ALERT_STATE["captured_acked"] = set(_ALERT_STATE["captured_keys"])
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
    _NAV_BADGES["/options/scanner"] = alerts.unread_count(
        alerts.scanner_keys(scan), _ALERT_STATE["acked_scan"])
    # Captured: fire on a genuinely NEW captured signal (a new signal_id), NOT on
    # the periodic reprice-republish (same ids, bumped version). Deserialize the
    # payload only when the version actually moved — the cheap :ver probe gates it.
    cap_ver = bus_client.read_version("options:captured")
    if cap_ver != _ALERT_STATE["captured_keys_ver"]:
        _ALERT_STATE["captured_keys"] = alerts.captured_keys(
            bus_client.read("options:captured") or {})
        _ALERT_STATE["captured_keys_ver"] = cap_ver
    _NAV_BADGES["/options/captured"] = 1 if alerts.unread_count(
        _ALERT_STATE["captured_keys"], _ALERT_STATE["captured_acked"]) else 0
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
    # Version-gated: a tick where neither view moved costs two tiny :ver probes
    # and no JSON parsing (see reset_watcher_memos). `scan` is read ONCE here and
    # passed to the badge helpers below.
    scan = bus_client.read_gated("options:scan", _WATCH_SCAN_MEMO)[0] or {}
    flow_view = bus_client.read_gated("options:flow_alerts", _WATCH_FLOW_MEMO)[0]
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
    # The footer card reads exactly these facts, so its count and the System
    # Status badge are one computation. Done here, off the event loop, so the 2s
    # UI tick only has labels to write.
    _STATUS_CARD.update(status_card_facts(
        svc_health, unhealthy_n, _svc_health_cache.get("latency_ms")))

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
        # The tick died before it could measure anything, so the footer card must
        # fall back to UNKNOWN rather than keep displaying the last good reading.
        # A card still saying "Data feed live" while the backbone is down is
        # precisely the confident-looking stale value this design set out to avoid.
        _STATUS_CARD.update(status_card_facts({}, 0))
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

    Since 2026-08-16 this serves the TAB STRIP only — the drawer shows a plain dot
    instead (``_alert_dot``), so the exact number lives on the tabs, which is
    where you go to act on it. The 2s watcher keeps it current via ``_set_badge``.
    ``floating`` is position:absolute, so the parent must be POSITIONED; the
    tab-strip call site relies on Quasar's ``.q-tab`` already being relative."""
    badge = ui.badge(str(n) if n else "").props("color=red rounded floating")
    badge.set_visibility(bool(n))
    return badge


def _set_badge(badge: ui.badge, n: int) -> None:
    """Point a mounted count badge at ``n`` — the update half of ``_count_badge``
    (0 blanks the text AND hides it, so an empty badge never renders as a dot)."""
    badge.text = str(n) if n else ""
    badge.set_visibility(bool(n))


def _alert_dot(count: int, *, rail: bool):
    """One of a nav row's two red notification dots — corner (rail) or right (open).

    Visibility is a CLASS (``nav-alert-on``), not ``set_visibility``. NiceGUI's
    set_visibility toggles Tailwind's single-class ``.hidden{display:none}``,
    which the two-class state rules in ``_NAV_CSS`` would out-specify — a row with
    zero alerts would show its dot the moment the drawer opened. Making "has
    alerts" a class the state rules require instead means the two facts compose
    rather than fight.
    """
    where = ("nav-alert-rail absolute top-0 right-0" if rail
             else "nav-alert-open ml-2")
    dot = ui.element("div").classes(
        f"{where} w-[7px] h-[7px] rounded-full bg-[#e5484d] flex-none")
    if count:
        dot.classes(add="nav-alert-on")
    return dot


def _set_alert(dots, n: int) -> None:
    """Point a row's dot pair at ``n`` — the update half of ``_alert_dot``."""
    for dot in dots:
        if n:
            dot.classes(add="nav-alert-on")
        else:
            dot.classes(remove="nav-alert-on")


def _nav_icon(icon: str, count: int):
    """The rail's icon plus its COLLAPSED-state notification dot.

    The icon is the ONLY thing visible when the rail is collapsed, so it carries
    the active state, and the corner dot rides it there. Once the drawer opens the
    dot moves out to the right of the row (``_alert_dot(rail=False)``) so it stops
    sitting on top of the icon — the two swap by CSS, see _NAV_CSS.

    Takes no is_active: the accent is painted by _NAV_CSS's
    `.nav-drawer .nav-active .nav-icon`, keyed off the LINK's .nav-active, which
    the caller already sets. It has to be keyed there — it needs 3 classes to
    out-specify theme.build_nav_css's `.nav-drawer .q-icon{...!important}`
    ([menu].text), injected after _NAV_CSS, so a 2-class marker on the icon
    itself would tie and lose."""
    # ``relative`` is load-bearing, not layout garnish: the corner dot is
    # position:absolute, so it anchors to the nearest POSITIONED ancestor. Drop
    # this and it escapes up the tree instead of sitting on the icon corner.
    with ui.element("div").classes(
            "relative flex items-center justify-center flex-none w-6 h-6"):
        ui.icon(icon).classes("nav-icon text-[20px]")
        dot = _alert_dot(count, rail=True)
    return dot


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
    n = _NAV_BADGES.get(path, 0)
    with ui.link(target=path).classes(base + state):
        _help_tooltip(path)   # rest the mouse 2 s for this page's guide
        with ui.row().classes("items-center gap-3 w-full no-wrap"):
            rail_dot = _nav_icon(icon, n)
            ui.label(label).classes("nav-label")
            pill = _NAV_PILLS.get(path)
            if pill:
                # A fixed marker (e.g. "AI"), not watcher state — so it is built
                # once and never registered for the tick. .nav-label rides the
                # existing fade, which is what keeps it out of the 68px rail;
                # .nav-pill carries the colour, because .nav-label's own active
                # rule would otherwise repaint it white on the active row.
                ui.label(pill).classes(
                    "nav-label nav-pill ml-auto font-mono text-[10px] "
                    "leading-none px-[6px] py-[2px] rounded-[5px] "
                    "bg-[#4da3ff]/[0.14]")
            # ml-auto pushes the open-state dot to the row's right edge, clear of
            # the label — mt-[1px] centres it optically against the cap height.
            open_dot = _alert_dot(n, rail=False).classes(
                "ml-auto mt-[1px]" if not pill else "")
            _alert_refs[path] = (rail_dot, open_dot)


def _nav_group_link(label: str, icon: str, children, active: str) -> None:
    """One flat drawer item for a GROUP: navigates to the group's first page,
    highlights when the active route is any of its children, and carries a dot
    when ANY of its children has alerts (updated by the watcher via
    ``_group_alert_refs``). The children themselves render as the top tab strip,
    where the exact per-page counts live."""
    paths = [p for p, _l, _i in children]
    base = ("w-full no-underline items-center rounded-[10px] px-3 py-1 "
            "transition-colors hover:bg-white/[0.06]")
    is_active = active in paths
    state = " nav-active" if is_active else ""
    with ui.link(target=paths[0]).classes(base + state):
        _help_tooltip(paths[0])   # the group's landing page's guide (2 s rest)
        with ui.row().classes("items-center gap-3 w-full no-wrap"):
            n = sum(_NAV_BADGES.get(p, 0) for p in paths)
            rail_dot = _nav_icon(icon, n)
            ui.label(label).classes("nav-label")
            open_dot = _alert_dot(n, rail=False).classes("ml-auto mt-[1px]")
            _group_alert_refs[label] = ((rail_dot, open_dot), paths)


def _nav_danger_link(path: str, label: str, icon: str) -> None:
    """Stop All Services — a nav row in LAYOUT, a danger button in COLOUR.

    Its icon and label sit on exactly the same two columns as every other drawer
    item (the ``px-3`` link padding, then the shared 24px ``_nav_icon``-sized
    wrapper, then a ``gap-3``), because a footer whose three rows start at three
    different x-positions reads as broken rather than as emphasis. The rose
    outline alone carries the warning — it is the only item in the rail that does
    something irreversible to the running stack.

    Carries no dot (nothing counts toward shutting the stack down) and claims no
    active state — the navy active wash under a rose outline reads as a rendering
    bug, and the page it opens is a confirm gate you leave immediately.

    The rose icon AND label colours each need their own ``_NAV_CSS`` rule:
    ``theme.build_nav_css`` emits ``.nav-drawer a{color:…!important}`` for
    ``[menu].text``, so anything inherited here silently loses to it.
    """
    # px-[11px], not px-3: box-sizing is border-box, so the 1px outline eats into
    # the padding box and would otherwise push this row's glyph 1px right of every
    # other one. 11 + 1 = the 12px the plain rows use.
    with ui.link(target=path).classes(
            "nav-danger w-full no-underline items-center rounded-[10px] "
            "px-[11px] py-1 mt-[6px] border border-[#e5484d]/[0.32] "
            "transition-colors hover:bg-[#e5484d]/[0.14]"):
        _help_tooltip(path)
        with ui.row().classes("items-center gap-3 w-full no-wrap"):
            # Same 24px box _nav_icon uses, so the glyph lands on the shared
            # column even though this row mounts no dot.
            with ui.element("div").classes(
                    "relative flex items-center justify-center flex-none w-6 h-6"):
                # text-[20px] like every other rail icon: the box is already on the
                # shared column, but a smaller glyph centres inside it 1px off, and
                # at this size that reads as a misalignment rather than a weight.
                ui.icon(icon).classes("nav-icon text-[20px]")
            ui.label(label).classes("nav-label font-semibold")


def _nav_section_header(caption: str, count: int, first: bool = False) -> None:
    """A rail section caption (MARKETS / STRATEGY / ACCOUNT) and its item count.

    Two children of ONE fixed-height, position:relative box, each absolutely
    placed so they overlap: the caption row (``.nav-title``, which the existing
    rail rule fades IN as the drawer opens) and a hairline (``.nav-sep``, whose
    rule is the exact inverse). The result is a cross-fade in place — the rail
    shows dividers, the open menu shows captions, and neither state reflows the
    other. Both rules live in ``_NAV_CSS`` because they key off an ANCESTOR's
    hover/focus/pinned state, which no Tailwind utility can express.

    ``count`` is passed in from ``len(entries)`` rather than written down, so a
    section can never advertise a stale number after a page is added.

    Every section but the ``first`` gets a wide top margin. With 2px between rows,
    a 26px caption box alone did not separate the groups by enough to read as a
    break — STRATEGY looked like an eleventh item rather than a new heading. The
    gap does the grouping; the caption only names it. It applies in the collapsed
    rail too, where it spaces the hairlines by the same amount.
    """
    gap = "" if first else " mt-4"
    with ui.element("div").classes(f"relative w-full h-[26px] shrink-0{gap}"):
        ui.element("div").classes(
            "nav-sep absolute left-[10px] right-[10px] top-[12px] h-px "
            "bg-white/[0.07]")
        with ui.row().classes(
                "nav-title absolute inset-0 items-center justify-between "
                "px-3 no-wrap"):
            ui.label(caption).classes(
                "font-mono font-medium tracking-[.15em] text-[10px] "
                "text-[#5d6a88] leading-none")
            ui.label(f"{count:02d}").classes(
                "font-mono text-[10px] text-[#3f4a66] leading-none")


def _status_card() -> None:
    """The drawer footer's live service-status card.

    Registers its three mutable parts in ``_status_refs`` so the 2s tick can
    repaint them from ``_STATUS_CARD`` without rebuilding the card. Painted at
    build time from whatever the watcher last computed, so a navigation shows the
    current state immediately rather than "unknown" for up to two seconds.

    Hidden entirely in the 68px rail (``.nav-status-card``, display-toggled in
    ``_NAV_CSS``) — the System Status icon two rows below already carries the
    same count as a corner badge when collapsed.
    """
    facts = dict(_STATUS_CARD)
    # px-[11px] + the 24px dot box put the dot on the SAME column as every rail
    # icon (border-box means the 1px outline eats into the padding). That is what
    # lets the card survive the collapse: with the text gone, the dot still lines
    # up with the icons above it instead of floating in the middle of the rail.
    with ui.row().classes(
            "nav-status-card w-full items-center gap-3 no-wrap "
            "px-[11px] py-[7px] mb-1 rounded-[9px] "
            "bg-white/[0.035] border border-white/[0.05]"):
        with ui.element("div").classes(
                "flex items-center justify-center flex-none w-6 h-6"):
            _status_refs["dot"] = ui.element("div").classes(
                f"nav-status-dot w-[7px] h-[7px] rounded-full flex-none "
                f"nav-status-{facts['tone']}")
        with ui.column().classes("nav-status-text gap-[2px] min-w-0"):
            _status_refs["title"] = ui.label(facts["title"]).classes(
                "text-[11.5px] font-semibold text-[#cdd7ec] whitespace-nowrap")
            _status_refs["detail"] = ui.label(facts["detail"]).classes(
                "font-mono text-[10px] text-[#6b7898] whitespace-nowrap")
        count = ui.label(str(facts["count"]) if facts["count"] else "").classes(
            "nav-status-count ml-auto font-mono text-[10px] px-[6px] py-[2px] "
            "rounded-[5px] bg-[#e5484d]/[0.16] text-[#ff8f92]")
        if facts["count"]:
            count.classes(add="nav-status-count-on")
        _status_refs["count"] = count


def _apply_status_card() -> None:
    """Write ``_STATUS_CARD`` into the mounted card (UI thread; never raises).

    The tone swap goes through ``remove=`` over the finite class set — repainting
    every 2 s, an ``add``-only recolour would stack three conflicting tone
    classes within a minute and the last one declared would win at random."""
    refs = _status_refs
    if not refs.get("dot"):
        return
    facts = _STATUS_CARD
    refs["dot"].classes(remove=_STATUS_TONE_CLASSES,
                        add=f"nav-status-{facts['tone']}")
    refs["title"].text = facts["title"]
    refs["detail"].text = facts["detail"]
    refs["count"].text = str(facts["count"]) if facts["count"] else ""
    # A class, not set_visibility — see the .nav-status-count rule in _NAV_CSS.
    if facts["count"]:
        refs["count"].classes(add="nav-status-count-on")
    else:
        refs["count"].classes(remove="nav-status-count-on")


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
    _alert_refs.clear()
    _group_alert_refs.clear()
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
        # flex-nowrap is load-bearing, and its absence was a LATENT bug this
        # redesign's section gaps finally tripped. NiceGUI's column wraps, and
        # `h-full` caps the height — so as soon as the items are taller than the
        # drawer, the overflow wraps into a SECOND COLUMN at 16 + 35 + 2 = 53px
        # instead of scrolling. Measured: Stop All Services alone jumped to x=53
        # while every other row sat at 16, and in the collapsed rail its corner
        # dot landed at x=82, outside the 68px viewport entirely. Nothing about
        # the row itself was wrong, which is what made it hard to see.
        with ui.column().classes(
                "h-full w-full flex flex-col flex-nowrap gap-[2px]"):
            # The rail's ORDER is data (NAV_SECTIONS), not the sequence of calls
            # here — regrouping the menu is now a list edit, not a render edit.
            # There is no lockup at the top: the brand lives once, in the header,
            # so the rail opens straight onto its first section caption.
            _status_refs.clear()
            for _i, (caption, entries) in enumerate(NAV_SECTIONS):
                # A None caption renders NO header — the pinned landing block.
                # Note `first` is now never True: index 0 is that block, so the
                # first VISIBLE caption (MARKETS) keeps its mt-4 gap, which is
                # what separates it from the Desk row pinned above. Its header
                # also supplies the hairline that marks the split in the
                # collapsed rail, where captions cross-fade out.
                if caption is not None:
                    _nav_section_header(caption, len(entries), first=(_i == 0))
                for entry in entries:
                    if entry[0] == "group":
                        _kind, _label, _icon, _children = entry
                        _nav_group_link(_label, _icon, _children, active)
                    else:
                        _kind, _path, _label, _icon = entry
                        _nav_link(_path, _label, _icon, active)
            # Machine-level controls, pushed to the FOOT of the rail: mt-auto eats
            # the leftover column height so they sit at the bottom edge (the column
            # is h-full flex-col), while still reading as the last items when the
            # menu is long enough to fill it. A hairline separates them from the
            # workflow pages above.
            # NB: mb-2 for the gap below, NOT my-2 — `my-*` also sets margin-top
            # and would fight the mt-auto that does the pushing.
            # These stay DIRECT children of the column. Wrapping them in a nested
            # ui.column was measured to shift the whole block 37px right of every
            # other row and carry the footer's corner dots outside the 68px rail
            # entirely — the rows are laid out against the drawer's own padding,
            # and an extra flex parent breaks that shared column.
            ui.element("div").classes(
                "mt-auto mb-2 w-full h-px bg-white/[0.07] shrink-0")
            _status_card()
            for path, label, icon in SYSTEM_RAIL:
                if path == SYSTEM_DANGER_ROUTE:
                    _nav_danger_link(path, label, icon)
                else:
                    _nav_link(path, label, icon, active)

    with ui.header().classes("items-center justify-between px-4"):
        # LEFT: hamburger · brand lockup · hairline · breadcrumb. The breadcrumb
        # reads as a continuation of the brand ("NeuralStrike ▸ Strategy ▸
        # Strategy Tools"), which is why it moved off the right edge — over there
        # it competed with the market pill for the eye and won neither.
        with ui.row().classes("items-center gap-3 no-wrap"):
            # The LOGO is the menu control (2026-08-16) — the hamburger is gone.
            # It is a real button, not a clickable div, so it keeps keyboard focus
            # and activation for free; the wordmark beside it stays inert.
            _mark = brand_mark_src()
            if _mark:
                with ui.button(on_click=lambda: _toggle_pin(drawer)).props(
                        "flat dense").classes("brand-mark-btn"):
                    ui.html(f'<img src="{html.escape(_mark)}" '
                            f'class="brand-mark" alt="">')
                    ui.tooltip(nav_pin_tooltip()).props("delay=350")
            else:
                # No logo file → fall back to the hamburger rather than losing the
                # ONLY way to pin the menu. brand_mark_src returns "" for a missing
                # asset, so this is a real path, not a defensive flourish.
                ui.button(icon="menu", on_click=lambda: _toggle_pin(drawer)).props(
                    "flat round dense color=white size=sm").tooltip(nav_pin_tooltip())
            ui.html(brand_lockup_html(mark=False))
            ui.element("div").classes("w-px h-[22px] bg-white/[0.09] mx-1 flex-none")
            with ui.row().classes("items-center gap-2 no-wrap"):
                _trail = breadcrumb_trail(active)
                _last_crumb = None
                for _i, _crumb in enumerate(_trail):
                    if _i:
                        ui.icon("chevron_right").classes(
                            "text-[14px] text-[#3f4a66]")
                    # Only the last crumb is the thing you are looking at; the
                    # ancestors stay muted so the trail reads as context.
                    _last_crumb = ui.label(_crumb).classes(
                        _CRUMB_LEAF if _i == len(_trail) - 1 else _CRUMB_CONTEXT)
                # A hidden slot for the page's own active view, filled by
                # bind_breadcrumb_leaf when the page has subtabs (see the
                # function). Built here, always, so a page never has to reach
                # into the header to create one.
                _leaf_caret = ui.icon("chevron_right").classes(
                    "text-[14px] text-[#3f4a66]")
                _leaf_label = ui.label("").classes(_CRUMB_LEAF)
                _leaf_caret.set_visibility(False)
                _leaf_label.set_visibility(False)
                _breadcrumb_leaf.clear()
                _breadcrumb_leaf.update({"caret": _leaf_caret,
                                         "label": _leaf_label,
                                         "parent": _last_crumb})
        # RIGHT: the market-status pill alone. The design's search pill and
        # notification bell are deliberately not ported — see the design doc's
        # "Rejected" section.
        with ui.row().classes("items-center gap-4 no-wrap"):
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
        # Drawer dots — same numbers, rendered as presence rather than a count.
        for route, dots in _alert_refs.items():
            _set_alert(dots, _NAV_BADGES.get(route, 0))
        for _label, (dots, paths) in _group_alert_refs.items():
            _set_alert(dots, sum(_NAV_BADGES.get(p, 0) for p in paths))
        # Footer status card — repainted from the same tick, since _watcher_compute
        # already produced its facts off the loop.
        _apply_status_card()

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


# The app's LANDING page is the DESK (2026-08-18). It answers, in one screen and
# in order, the four questions a session actually opens with — what is the market
# doing, where is the structure, what should I act on, what am I holding — by
# aggregating the highest glance-value element of each page. It supersedes the
# Market Dashboard here (2026-08-16 → 2026-08-18): the macro tape is the widest
# read in the app, but it is one of those four answers, not all of them, and it
# now sits a click away under Trend & Sentiment.
#
# This is a REDIRECT rather than a second render of the Desk: rendering it at both
# paths would give one page two URLs, and the shell keys the active nav item, the
# tab strip and the breadcrumb off the route — so `/` would highlight nothing.
@app.get("/")
def _root_to_desk():
    return RedirectResponse(url="/desk")


@ui.page("/desk")
def desk_page() -> None:
    with _layout("/desk", "Desk"):
        from pages import desk
        desk.render()


@ui.page("/options/scanner")
def options_scanner_page() -> None:
    with _layout("/options/scanner", "Options · Market Scanner"):
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


@ui.page("/options/flow")
def options_flow_page() -> None:
    with _layout("/options/flow", "Flow Alerts"):
        from pages.options import flow
        flow.render()


@ui.page("/sentiment")
def sentiment_page() -> None:
    with _layout("/sentiment", "Sentiment"):
        from pages import sentiment
        sentiment.render()


@ui.page("/sentiment/bullbear")
def sentiment_bullbear_page() -> None:
    with _layout("/sentiment/bullbear", "Bull / Bear Map"):
        from pages import sentiment_bullbear
        sentiment_bullbear.render()


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
    app.on_startup(sync_captured_autoclose_setting)
    app.on_startup(sync_manual_paper_lifecycle_setting)

    # Bind to localhost only (single-user, localhost-first app): reachable at
    # http://localhost:8500 from this PC. This avoids listening on every network
    # interface (the default host="0.0.0.0"), which on Windows produced benign but
    # noisy `OSError [WinError 64] "network name is no longer available"` accept
    # tracebacks whenever a transient/virtual adapter (link-local 169.254.x, WSL/
    # Docker) dropped — and keeps the trading app off the LAN.
    ui.run(host="127.0.0.1", port=NICEGUI_PORT, title=window_title(),
           dark=True, reload=False, show=False)
