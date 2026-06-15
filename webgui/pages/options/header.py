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


def _fmt_price(v):
    return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"


def render():
    """Build the header strip; returns a repaint() callable (call contract kept
    for callers like scanner.py/swing.py, which currently ignore it)."""
    with ui.row().classes("items-center gap-4 w-full q-pa-sm rounded bg-grey-9"):
        price_lbls = {s: ui.label(f"{s} —") for s in ("$SPX", "SPY", "QQQ")}
        vix_lbl = ui.label("VIX —").classes("font-bold")
        regime_badge = ui.badge("").classes("text-xs")
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
        if reg.get("color"):
            regime_badge.style(f"background-color:{reg['color']}")
        sent = hdr.get("sentiment") or {}
        dot.style(f"color:{sent.get('color', '#666666')}")
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
