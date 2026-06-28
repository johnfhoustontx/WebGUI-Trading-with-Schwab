"""Compact Options header strip: SPX/SPY/QQQ + VIX/regime + sentiment dot.

Tier-3 reader: this strip holds **no proxy/engine call**. The options service
computes the whole header view (quotes + VIX regime + sentiment dot) each 30 s
and writes it to the Redis bus under ``cache:options:header``; this strip only
**reads** that payload and paints it. The pure helpers (``sentiment_dot``,
``quote_last``, ``vix_regime``) now live in ``services/options_svc/compute.py``.

Cache view read: ``options:header`` → ``{prices:{$SPX,SPY,QQQ}, vix,
vix_regime:{label,color}, sentiment:{color,label}}``. Graceful-empty: paint
"—"/blank when the service is cold. A fetch-free version-poll ``ui.timer``
repaints when the bus cache version changes.
"""
import bus_client
from nicegui import ui

from pages.ui_guard import guard


# Finite label → Tailwind class maps. The payload still carries the engine's `color`
# hex, but the page maps the (also-in-payload) `label` to a fixed palette token —
# per the styling standard's "map a finite STATE → a fixed class" rule.
#
# Sentiment dot labels → text classes (hexes from compute.sentiment_dot: Bullish
# #1D9E75 / Bearish #E24B4A / Neutral #EFC347 / No data #666666). These differ from
# detail.py's 4-set, so they're LOCAL (do NOT reuse TXT_POS etc.).
_DOT_CLASS = {
    "Bullish": "text-[#1D9E75]",
    "Bearish": "text-[#E24B4A]",
    "Neutral": "text-[#EFC347]",
    "No data": "text-[#666666]",
}
# VIX regime labels → bg classes (traced from scanner_engine.vix_regime,
# options-scanner/scanner_engine.py:158-161 — exact engine colors preserved).
_REGIME_BG = {
    "Low vol": "bg-[#1D9E75]",
    "Normal": "bg-[#378ADD]",
    "Elevated": "bg-[#EF9F27]",
    "High vol": "bg-[#E24B4A]",
}
# Space-joined value sets for the reactive .classes(remove=…) swap (these colors
# are set on EVERY repaint, so a plain add= would accumulate conflicting classes).
# sorted() only for a stable, deterministic string — order is irrelevant to remove=.
_ALL_DOT = " ".join(sorted(set(_DOT_CLASS.values()) | {"text-[#666666]"}))
_ALL_REGIME_BG = " ".join(sorted(set(_REGIME_BG.values()) | {"bg-[#666666]"}))


def sentiment_dot_class(label):
    return _DOT_CLASS.get(label, "text-[#666666]")


def regime_badge_class(label):
    return _REGIME_BG.get(label, "bg-[#666666]")


def _fmt_price(v):
    return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"


def render():
    """Build the header strip; returns a repaint() callable (call contract kept
    for callers like scanner.py/swing.py, which currently ignore it)."""
    with ui.row().classes("items-center gap-4 w-full q-pa-sm rounded bg-grey-9"):
        price_lbls = {s: ui.label(f"{s} —") for s in ("$SPX", "SPY", "QQQ")}
        vix_lbl = ui.label("VIX —").classes("font-bold")
        # color=None drops Quasar's default bg-primary so the mapped bg-[...] class wins.
        regime_badge = ui.badge("", color=None).classes("text-xs")
        ui.label("Sentiment").classes("opacity-70 text-sm")
        dot = ui.icon("circle").classes("text-xs")
        sent_lbl = ui.label("").classes("text-sm")

    # Last-seen bus cache version for the fetch-free repaint timer.
    seen = {"version": None}

    def _paint(hdr):
        hdr = hdr or {}
        prices = hdr.get("prices") or {}
        for s in ("$SPX", "SPY", "QQQ"):
            price_lbls[s].text = f"{s} {_fmt_price(prices.get(s))}"
        vix_lbl.text = f"VIX {_fmt_price(hdr.get('vix'))}"
        reg = hdr.get("vix_regime") or {}
        regime_badge.text = reg.get("label", "")
        regime_badge.classes(remove=_ALL_REGIME_BG,
                             add=regime_badge_class(reg.get("label", "")))
        sent = hdr.get("sentiment") or {}
        dot.classes(remove=_ALL_DOT, add=sentiment_dot_class(sent.get("label", "")))
        sent_lbl.text = sent.get("label", "")

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    seen["version"] = bus_client.read_version("options:header")
    _paint(bus_client.read("options:header") or {})

    @guard
    def _maybe_repaint():
        # Fetch-free: compare the bus cache version to the last-painted one and
        # only re-read + repaint on change. The service bumps it each ~30s tick.
        version = bus_client.read_version("options:header")
        if version == seen["version"]:
            return
        seen["version"] = version
        _paint(bus_client.read("options:header") or {})

    ui.timer(5.0, _maybe_repaint)
    return _maybe_repaint
