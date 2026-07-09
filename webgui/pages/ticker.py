"""Market Summary Ticker (bottom of every page) — Tier-1, engine-free.

Reads cache:market:dashboard + cache:sentiment:composite (live items) and
cache:market:summary (Claude verdict), renders a fixed bottom marquee. The pure
builders here (``ticker_items``/``item_class``/``speed_class``) carry the coverage;
``render_ticker`` does the widget + timer wiring (Task 5).

Tailwind-first: NO ``.style()``. The marquee ``@keyframes`` animation lives in the
ONE ``ui.add_css`` escape hatch (``_TICKER_CSS``); the scroll speed is a finite
class (slow/med/fast), and item colors map from a finite ``tone`` set to fixed
Tailwind text classes.
"""

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
    ``_TICKER_CSS`` so the page stays ``.style()``-free (Tailwind-first). Any
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


def _as_float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


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
        text = f"{t['display']} {_fmt(t.get('last'))} {_fmt_pct(t.get('change_pct'))}".strip()
        items.append({"text": text, "tone": _tone_from_state(t.get("color_state"))})

    # Put/Call.
    pcr = live.get("sector_pcr")
    if pcr is not None:
        tone = "risk_off" if _as_float(pcr, 1) > 1.0 else "risk_on"
        items.append({"text": f"P/C {_fmt(pcr)}", "tone": tone})

    # Indices.
    for t in byc.get("Cash Index", []):
        items.append({"text": f"{t['display']} {_fmt_pct(t.get('change_pct'))}",
                      "tone": _tone_from_state(t.get("color_state"))})

    # Top movers (sector + thematic), by |change|.
    movers = [t for c in ("Sector SPDR", "Thematic / Industry ETF") for t in byc.get(c, [])
              if t.get("change_pct") is not None]
    movers.sort(key=lambda t: abs(t.get("change_pct") or 0), reverse=True)
    for t in movers[:4]:
        items.append({"text": f"{t['display']} {_fmt_pct(t.get('change_pct'))}",
                      "tone": _tone_from_state(t.get("color_state"))})

    return items
