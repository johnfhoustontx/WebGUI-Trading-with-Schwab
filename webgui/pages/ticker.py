"""Market Summary Ticker (bottom of every page) — Tier-1, engine-free.

Scrolls the latest published NeuralStrike market report: its verdict headline,
then its section highlights, then which report they came from. The ONE source is
``cache:market:summary``, which market_svc reads off the report page on disk
(``services/market_svc/report_summary.py``) — no Claude call and no Schwab call
sits behind anything this bar shows. Until 2026-09-21 it also scrolled live
quote items built from ``cache:market:dashboard`` and ``cache:sentiment:composite``
(both fed by Schwab polling); those belong to the Market Dashboard and the Desk.

The pure builder ``report_items`` carries the coverage; ``render_ticker`` does the
widget + timer wiring.

Tailwind-first: NO inline styles. The marquee ``@keyframes`` animation lives in the
ONE ``ui.add_css`` escape hatch (``_TICKER_CSS``); the scroll speed is a finite
class (slow/med/fast), and each item kind maps to a fixed Tailwind text class.
"""
import app_settings
import bus_client
from nicegui import run, ui

from pages.ui_guard import guard_async


# The ONE ui.add_css escape hatch for this component: a keyframe marquee animation
# (not expressible as a Tailwind utility) + the three finite scroll-speed buckets
# (a genuinely-continuous duration, kept a 3-class set so the page stays
# inline-style-free). Everything else is Tailwind classes.
_TICKER_CSS = """
@keyframes mkt-marquee { from { transform: translateX(100%); } to { transform: translateX(-100%); } }
.mkt-ticker-scroll { display: inline-flex; white-space: nowrap; will-change: transform;
  animation: mkt-marquee var(--mkt-dur, 60s) linear infinite; }
.mkt-ticker-scroll.mkt-dur-slow { --mkt-dur: 90s; }
.mkt-ticker-scroll.mkt-dur-med  { --mkt-dur: 60s; }
.mkt-ticker-scroll.mkt-dur-fast { --mkt-dur: 35s; }
.mkt-ticker-wrap:hover .mkt-ticker-scroll { animation-play-state: paused; }
"""

# The one cache view the ticker reads (version-polled on the cheap :ver counter).
VIEW = "market:summary"

# What the bar says when no report has been published yet.
EMPTY_TEXT = "No market report published yet"

# item kind → fixed Tailwind text class (finite map, Tailwind-first).
_KIND = {
    "headline": "text-amber-300 font-medium",
    "highlight": "text-slate-200",
    "stamp": "text-slate-400",
}


def item_class(kind):
    """Map a finite item ``kind`` to a fixed Tailwind text class (highlight fallback)."""
    return _KIND.get(kind, _KIND["highlight"])


def _squash(text):
    return " ".join(str(text or "").split())


def report_stamp_text(summary):
    """"Market close report · 2026-09-14 · 16:20 CT" — which report the bar is
    quoting, or "" when the payload names none (PURE)."""
    s = summary if isinstance(summary, dict) else {}
    label = _squash(s.get("slot_label"))
    parts = [f"{label} report" if label else ""]
    parts += [_squash(s.get("report_date")), _squash(s.get("as_of"))]
    parts = [p for p in parts if p]
    return " · ".join(parts)


def report_items(summary):
    """List of ``{text, kind}`` from the market-report summary payload (PURE).

    The headline first, then each highlight in report order (a highlight that
    only repeats the headline — a report with no sections — is dropped), then
    the report's own stamp. An empty or malformed payload gives ``[]``: the bar
    never invents a line."""
    s = summary if isinstance(summary, dict) else {}
    items = []
    headline = _squash(s.get("headline"))
    if headline:
        items.append({"text": headline, "kind": "headline"})
    raw = s.get("highlights") if isinstance(s.get("highlights"), list) else []
    for h in raw:
        text = _squash(h)
        if text and text != headline:
            items.append({"text": text, "kind": "highlight"})
    if items:
        stamp = report_stamp_text(s)
        if stamp:
            items.append({"text": stamp, "kind": "stamp"})
    return items


def speed_class(speed):
    """Map a marquee-duration number (seconds) to a finite scroll-speed CSS class.

    Higher seconds = slower scroll. Bucketed into three fixed classes defined in
    ``_TICKER_CSS`` so the page stays inline-style-free (Tailwind-first). Any
    unparseable value falls back to the medium bucket.
    """
    try:
        s = float(speed)
    except (TypeError, ValueError):
        return "mkt-dur-med"
    if s >= 75:
        return "mkt-dur-slow"
    if s <= 45:
        return "mkt-dur-fast"
    return "mkt-dur-med"


def render_ticker(active):
    """Fixed bottom marquee on every page (gated by the Settings toggle).

    Renders nothing when ``ticker_enabled`` is off. Reads ONE cache view — the
    latest market report's summary — version-gated on a 4s timer, so it is cheap
    on every page and changes only when a new report is published.
    """
    if not app_settings.get("ticker_enabled"):
        return
    ui.add_css(_TICKER_CSS)
    speed = app_settings.get("ticker_speed") or 60
    with ui.footer().classes(
            "mkt-ticker-wrap bg-slate-950/95 border-t border-slate-700 "
            "h-8 px-3 flex items-center overflow-hidden z-[2200]"):
        scroll = ui.row().classes(
            f"mkt-ticker-scroll {speed_class(speed)} items-center gap-2")

    state = {"version": None, "items": None}

    def _read():
        """Read the report summary → the rendered items."""
        return report_items(bus_client.read(VIEW))

    def _paint(items):
        """Rebuild the marquee DOM from already-built items."""
        scroll.clear()
        with scroll:
            if not items:
                ui.label(EMPTY_TEXT).classes("text-slate-400 text-xs")
            for i, it in enumerate(items):
                ui.label(it["text"]).classes(f"text-xs {item_class(it['kind'])}")
                if i < len(items) - 1:
                    ui.label("·").classes("text-slate-600 text-xs")

    @guard_async
    async def _poll():
        # First gate (cheap): skip unless the summary's version advanced. The
        # Redis round-trips run OFF the event loop (this timer fires on EVERY
        # page); only the paint runs back on the loop.
        ver = await run.io_bound(bus_client.read_version, VIEW)
        if ver == state["version"]:
            return
        state["version"] = ver
        # Second gate: only clear+rebuild when the displayed content changed, so
        # the marquee keeps scrolling smoothly otherwise.
        items = await run.io_bound(_read)
        if items != state["items"]:
            state["items"] = items
            _paint(items)

    items = _read()
    _paint(items)
    state["items"] = items
    state["version"] = bus_client.read_version(VIEW)
    ui.timer(4.0, _poll)
