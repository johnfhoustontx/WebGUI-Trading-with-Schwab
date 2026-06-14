"""Compact Options header strip: SPX/SPY/QQQ + VIX/regime + sentiment dot.

Cheap data (one quotes call + a file-read for sentiment), refreshed on a timer.
Not a full scan. Pure helpers (sentiment_dot, quote_last) are unit-tested.
"""
import sys

from nicegui import ui

import proxy
from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

from scanner_engine import vix_regime  # noqa: E402
from regime_filter import evaluate_regime  # noqa: E402

SYMBOLS = ["$SPX", "SPY", "QQQ", "$VIX"]

_DOT_NO_DATA = ("#666666", "No data")
_DOT_BULLISH = ("#1D9E75", "Bullish")
_DOT_BEARISH = ("#E24B4A", "Bearish")
_DOT_NEUTRAL = ("#EFC347", "Neutral")


def sentiment_dot(regime):
    """(color, label) for the sentiment indicator from an evaluate_regime() dict."""
    if not regime or not regime.get("active"):
        return _DOT_NO_DATA
    if not regime.get("allow_ccs"):
        return _DOT_BULLISH      # CCS blocked -> market biased up
    if not regime.get("allow_pcs"):
        return _DOT_BEARISH      # PCS blocked -> market biased down
    return _DOT_NEUTRAL


def quote_last(raw, symbol):
    """Extract lastPrice for a symbol from a proxy /quotes payload; None if absent."""
    if not isinstance(raw, dict):
        return None
    info = raw.get(symbol)
    if not isinstance(info, dict):
        return None
    q = info.get("quote", info.get("reference", info))
    return q.get("lastPrice") if isinstance(q, dict) else None


def _fmt_price(v):
    return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"


def render():
    """Build the header strip; returns a refresh() the caller can also timer."""
    with ui.row().classes("items-center gap-4 w-full q-pa-sm rounded bg-grey-9"):
        price_lbls = {s: ui.label(f"{s} —") for s in ("$SPX", "SPY", "QQQ")}
        vix_lbl = ui.label("VIX —").classes("font-bold")
        regime_badge = ui.badge("").classes("text-xs")
        ui.label("Sentiment").classes("opacity-70 text-sm")
        dot = ui.icon("circle").classes("text-xs")
        sent_lbl = ui.label("").classes("text-sm")

    def refresh():
        try:
            raw = proxy.schwab_py_client.get_quotes(SYMBOLS).json() or {}
        except Exception:
            raw = {}
        for s in ("$SPX", "SPY", "QQQ"):
            price_lbls[s].text = f"{s} {_fmt_price(quote_last(raw, s))}"
        vix = quote_last(raw, "$VIX")
        vix_lbl.text = f"VIX {_fmt_price(vix)}"
        if isinstance(vix, (int, float)):
            reg = vix_regime(vix) or {}
            regime_badge.text = reg.get("label", "")
            if reg.get("color"):
                regime_badge.style(f"background-color:{reg['color']}")
        try:
            color, label = sentiment_dot(evaluate_regime())
        except Exception:
            color, label = _DOT_NO_DATA
        dot.style(f"color:{color}")
        sent_lbl.text = label

    refresh()
    ui.timer(30.0, refresh)
    return refresh
