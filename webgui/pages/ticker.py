"""Market Summary Ticker (bottom of every page) — Tier-1, engine-free.

Reads cache:market:dashboard + cache:sentiment:composite (live items) and
cache:market:summary (Claude verdict), renders a fixed bottom marquee. The pure
builders here (``ticker_items``/``item_class``/``speed_class``) carry the coverage;
``render_ticker`` does the widget + timer wiring (Task 5).

Tailwind-first: NO inline styles. The marquee ``@keyframes`` animation lives in the
ONE ``ui.add_css`` escape hatch (``_TICKER_CSS``); the scroll speed is a finite
class (slow/med/fast), and item colors map from a finite ``tone`` set to fixed
Tailwind text classes.
"""
import app_settings
import bus_client
from nicegui import run, ui

from pages.fmt import float_or  # the ONE copy (pages/fmt.py)
from pages.ui_guard import guard, guard_async

def _as_float(v, default):
    return float_or(v, default)


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

# Cache views the ticker version-polls (cheap :ver counters, one round-trip).
VIEWS = ("market:dashboard", "sentiment:composite", "market:summary")


# tone → fixed Tailwind text class (finite map, Tailwind-first).
_TONE = {
    "risk_on": "text-emerald-400",
    "risk_off": "text-rose-400",
    "neutral": "text-slate-300",
    "warn": "text-amber-400",
}


def item_class(tone):
    """Map a finite ``tone`` to a fixed Tailwind text class (neutral fallback)."""
    return _TONE.get(tone, _TONE["neutral"])


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


def _tone_from_state(color_state):
    if not color_state:
        return "neutral"
    if color_state.startswith("risk_on"):
        return "risk_on"
    if color_state.startswith("risk_off"):
        return "risk_off"
    return "neutral"


def _fmt_pct(v):
    try:
        f = float(v)
        return f"{'+' if f >= 0 else ''}{f:.1f}%"
    except (TypeError, ValueError):
        return ""


def _fmt(v, nd=2):
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _tiles_by_cat(dashboard):
    out = {}
    for c in (dashboard or {}).get("categories", []):
        out[c.get("category")] = c.get("tiles", [])
    return out


def ticker_items(dashboard, sentiment):
    """List of ``{text, tone}`` live items from the two cache payloads (PURE)."""
    if not dashboard and not sentiment:
        return []
    byc = _tiles_by_cat(dashboard)
    live = (sentiment or {}).get("live") or {}
    der = (sentiment or {}).get("derived") or {}
    comp = live.get("composite") or {}
    trend = der.get("trend") or {}
    items = []

    # Sentiment + trend headline (tone by score band).
    if comp.get("bias"):
        score = comp.get("total_score")
        sv = _as_float(score, 5)
        tone = "risk_off" if sv < 4.5 else ("risk_on" if sv > 6.5 else "neutral")
        items.append({"text": f"Sentiment {comp['bias']} {score}/10", "tone": tone})
    if trend.get("label"):
        ts = trend.get("score")
        tv = _as_float(ts, 50)
        tone = "risk_off" if tv < 45 else ("risk_on" if tv > 55 else "neutral")
        items.append({"text": f"Trend {trend['label']} {_fmt(ts, 1)}", "tone": tone})

    # Breadth.
    br = (live.get("breadth") or {}).get("interpretation")
    if br:
        tone = "warn" if ("weak" in br.lower() or "bearish" in br.lower()) else "neutral"
        items.append({"text": f"Breadth {br}", "tone": tone})

    # Volatility (VIX/VIX1D/SKEW) — inverted feel already baked into color_state.
    for t in byc.get("Volatility", []):
        text = f"{t.get('display', '')} {_fmt(t.get('last'))} {_fmt_pct(t.get('change_pct'))}".strip()
        items.append({"text": text, "tone": _tone_from_state(t.get("color_state"))})

    # Put/Call.
    pcr = live.get("sector_pcr")
    if pcr is not None:
        tone = "risk_off" if _as_float(pcr, 1) > 1.0 else "risk_on"
        items.append({"text": f"P/C {_fmt(pcr)}", "tone": tone})

    # Indices.
    for t in byc.get("Cash Index", []):
        items.append({"text": f"{t.get('display', '')} {_fmt_pct(t.get('change_pct'))}".strip(),
                      "tone": _tone_from_state(t.get("color_state"))})

    # Top 10 composite (equal-weighted avg day move + breadth over the 10 mega-caps).
    mag = next((t for t in byc.get("Top 10", []) if t.get("basket")), None)
    if mag and mag.get("avg_pct") is not None:
        breadth = mag.get("breadth_text", "")
        text = f"BIG10 {_fmt_pct(mag.get('avg_pct'))}" + (f" ({breadth})" if breadth else "")
        items.append({"text": text, "tone": _tone_from_state(mag.get("color_state"))})

    # Top movers (sector + thematic), by |change|.
    movers = [t for c in ("Sector SPDR", "Thematic / Industry ETF") for t in byc.get(c, [])
              if t.get("change_pct") is not None]
    movers.sort(key=lambda t: abs(t.get("change_pct") or 0), reverse=True)
    for t in movers[:4]:
        items.append({"text": f"{t.get('display', '')} {_fmt_pct(t.get('change_pct'))}".strip(),
                      "tone": _tone_from_state(t.get("color_state"))})

    return items


def render_ticker(active):
    """Fixed bottom marquee on every page (gated by the Settings toggle).

    Renders nothing when ``ticker_enabled`` is off. Reads the two live caches +
    the Claude narrative, version-gated on a 4s timer so it's cheap on every page.
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

    state = {"versions": None, "sig": None}

    def _read():
        """Read the caches → ``(narrative, items)`` (the rendered content)."""
        dash = bus_client.read("market:dashboard")
        sent = bus_client.read("sentiment:composite")
        summ = bus_client.read("market:summary") or {}
        narrative = (summ.get("narrative") or "").strip()
        return narrative, ticker_items(dash, sent)

    def _paint(narrative, items):
        """Rebuild the marquee DOM from already-built ``(narrative, items)``."""
        scroll.clear()
        with scroll:
            if narrative:
                ui.label(f"⚠ {narrative}").classes("text-amber-300 text-xs font-medium")
                ui.label("·").classes("text-slate-600 text-xs")
            if not items and not narrative:
                ui.label("Market data loading…").classes("text-slate-400 text-xs")
            for i, it in enumerate(items):
                ui.label(it["text"]).classes(f"text-xs {item_class(it['tone'])}")
                if i < len(items) - 1:
                    ui.label("·").classes("text-slate-600 text-xs")

    def _signature(narrative, items):
        # Signature of the RENDERED content — NOT the raw cache version (which bumps
        # ~every 2s during RTH for tile fields the ticker never shows). Only a change
        # in the displayed strings/tones triggers a DOM rebuild, so the marquee keeps
        # scrolling smoothly through the common no-visible-change RTH tick.
        return (narrative, tuple((i["text"], i["tone"]) for i in items))

    @guard_async
    async def _poll():
        # First gate (cheap): skip everything unless a watched version advanced.
        # The :ver batch + the 3 payload reads are Redis round-trips → run them OFF
        # the event loop (this timer fires on EVERY page). Only the paint (UI) runs
        # back on the loop after the awaits.
        vers = await run.io_bound(bus_client.read_versions, VIEWS)
        if vers == state["versions"]:
            return
        state["versions"] = vers
        # Second gate: only clear+rebuild when the displayed content actually changed.
        narrative, items = await run.io_bound(_read)
        sig = _signature(narrative, items)
        if sig != state["sig"]:
            state["sig"] = sig
            _paint(narrative, items)

    narrative, items = _read()
    _paint(narrative, items)
    state["sig"] = _signature(narrative, items)
    state["versions"] = bus_client.read_versions(VIEWS)
    ui.timer(4.0, _poll)
