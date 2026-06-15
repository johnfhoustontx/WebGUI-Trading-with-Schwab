"""Options compute module — NiceGUI-free engine-call layer.

Extracted from ``webgui/pages/options/scanner.py`` so the backend options
service owns the heavy scanner-engine call (the GUI tier will later consume the
cached result instead of running the scan itself). This module must NOT import
``nicegui`` or anything from ``webgui/`` — it depends only on the shared
``services._proxy`` accessor and the copied options-scanner engine.

The module-top ``sys.path`` glue + eager engine import mirror the page's. Now
that this runs inside the (process-isolated) options service, the ``scoring``
package-vs-module collision documented in the root CLAUDE.md can NOT occur: no
sentiment code is loaded in this process, so ``from scoring import ...`` (done
lazily inside ``run_full_scan``) resolves to options-scanner's ``scoring.py``
unambiguously. Therefore the page's ``options_scoring()`` collision guard is
intentionally NOT ported here — ``run_full_scan`` is called directly.
"""
import sys

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

from scanner_engine import run_full_scan, vix_regime  # noqa: E402
from regime_filter import evaluate_regime  # noqa: E402

from services import _proxy  # noqa: E402


def run_scan() -> dict:
    """Run one full scan cycle against the live proxy. Returns the engine dict.

    Thin wrapper: ``run_full_scan`` needs the schwab-py-compatible client, so we
    pass ``_proxy.schwab_py_client`` (mirrors the page). Any exception is left to
    propagate — the handler catches it (matching the sentiment compute, whose
    loaders likewise let the handler own error handling)."""
    return run_full_scan(_proxy.schwab_py_client)


# ── Header strip (ported from webgui/pages/options/header.py) ───────────────
# These were the GUI's header helpers; they're pure and now run here so the GUI
# tier reads the whole header view from the bus (no proxy/engine call). As with
# run_scan, the ``scoring`` collision can't occur in this process (no sentiment
# code is loaded), so the eager imports above bind ``vix_regime``/``evaluate_regime``
# unambiguously.

HEADER_SYMBOLS = ["$SPX", "SPY", "QQQ", "$VIX"]

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


def refresh_header() -> dict:
    """Compute the compact header view (quotes + VIX regime + sentiment dot).

    Returns ``{"prices": {"$SPX","SPY","QQQ"}, "vix", "vix_regime", "sentiment"}``.
    Defensive throughout: a quotes failure yields blank prices/regime; a sentiment
    failure yields the no-data dot — the view is always a well-formed dict."""
    try:
        raw = _proxy.schwab_py_client.get_quotes(HEADER_SYMBOLS).json() or {}
    except Exception:
        raw = {}

    prices = {s: quote_last(raw, s) for s in ("$SPX", "SPY", "QQQ")}
    vix = quote_last(raw, "$VIX")
    regime = vix_regime(vix) or {} if isinstance(vix, (int, float)) else {}

    try:
        dot_color, dot_label = sentiment_dot(evaluate_regime())
    except Exception:
        dot_color, dot_label = _DOT_NO_DATA

    return {
        "prices": prices,
        "vix": vix,
        "vix_regime": regime,
        "sentiment": {"color": dot_color, "label": dot_label},
    }
