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

import scanner_engine as se  # noqa: E402
from scanner_engine import run_full_scan, vix_regime  # noqa: E402
from regime_filter import evaluate_regime  # noqa: E402
from iv_analysis import run_iv_analysis  # noqa: E402

from services import _proxy  # noqa: E402


def run_scan() -> dict:
    """Run one full scan cycle against the live proxy. Returns the engine dict.

    Thin wrapper: ``run_full_scan`` needs the schwab-py-compatible client, so we
    pass ``_proxy.schwab_py_client`` (mirrors the page). Any exception is left to
    propagate — the handler catches it (matching the sentiment compute, whose
    loaders likewise let the handler own error handling)."""
    return run_full_scan(_proxy.schwab_py_client)


# ── Swing scan (ported from webgui/pages/options/swing.py `_swing_scan`) ─────
# A user-parameterized on-demand credit-spread scan. The pipeline is ported
# VERBATIM from the page (same engine calls, same arg order, same two-client
# usage). ``min_cr_fraction`` arrives already converted percent→fraction by the
# page (``pct_to_fraction``); the service is given the fraction directly.


def assign_ids(signals, symbol):
    """Ensure each signal has a unique ``id`` (for detail lookup). Pure."""
    for i, s in enumerate(signals or []):
        if not s.get("id"):
            s["id"] = f"{symbol}_{i}_{s.get('type','')}_{s.get('short_strike','')}"
    return signals


def swing_scan(symbol, dte_min, dte_max, put_d_min, put_d_max,
               call_d_min, call_d_max, min_cr_fraction) -> list:
    """Run the swing scan pipeline; returns scored signals (list of dicts).

    Two-client usage mirrors the page exactly: ``_proxy.schwab_py_client`` is the
    schwab-py-compatible client passed into the engine calls, while
    ``_proxy.schwab_client.get_quote(symbol)`` (SchwabClient-compatible) fetches
    the quote. ``min_cr_fraction`` is already a fraction.

    The page wrapped ``scoring.score_all_signals`` in an ``options_scoring()``
    collision guard because the GUI process also loads the sentiment ``scoring``
    package. This service process loads NO sentiment code, so ``import scoring``
    binds options-scanner's ``scoring.py`` unambiguously — the guard is
    intentionally NOT ported and ``score_all_signals`` is called directly.

    ``scoring`` is imported lazily here (not at module top) to avoid binding the
    process-wide ``sys.modules['scoring']`` to options-scanner's module merely by
    importing this module — that matters only for the *combined* test run where
    all services share one process and the sentiment service also imports its own
    ``scoring`` package. In the real (process-isolated) service, the lazy import
    still resolves to options-scanner's ``scoring.py``. Mirrors ``run_full_scan``,
    which likewise imports ``scoring`` lazily.
    """
    import datetime as dt

    import scoring

    client = _proxy.schwab_py_client

    today = dt.date.today()
    chain = se.fetch_option_chain(client, symbol, from_date=today,
                                  to_date=today + dt.timedelta(days=dte_max + 2))
    quote = _proxy.schwab_client.get_quote(symbol) or {}
    spot = quote.get("last") or chain.get("underlyingPrice")
    hist = se.fetch_price_history(client, symbol)
    tech = se.calc_technicals(hist) if hist is not None else {}
    iv = run_iv_analysis(client, symbol, price=spot, hist=hist, chain=chain) or {}
    dem = ((iv.get("expected_moves") or {}).get("daily") or {}).get("move_dollars")

    spreads = se.screen_spreads(chain, symbol, dte_min, dte_max, put_d_min, put_d_max,
                                call_d_min, call_d_max, min_cr_fraction, "SWING",
                                spot=spot, daily_expected_move=dem)
    signals = list(spreads) + list(se.build_iron_condors(spreads))
    scoring.score_all_signals(signals, {symbol: iv}, {symbol: tech})
    return assign_ids(signals, symbol)


# ── Paper account (ported from webgui/pages/options/portfolio.py) ───────────
# The page read the paper account directly (snapshot + open positions + fills)
# and ran the entry/manage/reset actions itself. Those reads + actions now live
# here so the GUI tier only reads the cached view and enqueues commands.
#
# LAZY IMPORTS (IMPORTANT): ``paper_engine`` pulls in options-scanner's
# ``scoring`` module. Importing it at module top would bind the process-wide
# ``sys.modules['scoring']`` to options-scanner's ``scoring.py`` merely by
# importing this module — which breaks the sentiment service's ``scoring``
# package in the *combined* pytest run (all services share one process). So
# ``paper_engine``/``paper_account_db``/``signal_db`` are imported LAZILY inside
# each function. The page's ``options_scoring()`` collision guard is therefore
# NOT ported (process-isolated service; lazy ``import scoring`` happens inside
# ``paper_engine`` and resolves to options-scanner's unambiguously).


def paper_account_view() -> dict:
    """Read the paper account view: snapshot + open positions + fills + flag.

    Each sub-read is defensively guarded (snapshot→None, lists→[] on failure),
    mirroring the page's per-read try/except. ``has_account`` lets the GUI show
    the no-account state without a separate read."""
    import paper_account_db
    import paper_engine

    try:
        snapshot = paper_engine.account_snapshot()
    except Exception:
        snapshot = None
    try:
        positions = paper_account_db.fetch_open_positions(None)
    except Exception:
        positions = []
    try:
        orders = paper_account_db.fetch_orders(None, limit=100, status="FILLED")
    except Exception:
        orders = []
    try:
        has_account = paper_account_db.get_account() is not None
    except Exception:
        has_account = False

    return {
        "snapshot": snapshot,
        "positions": positions,
        "orders": orders,
        "has_account": has_account,
    }


def run_entry_cycle() -> None:
    """Run the paper auto-entry cycle: scan open captured signals, open positions.

    No ``options_scoring()`` guard (process-isolated; the lazy ``import scoring``
    happens inside ``paper_engine``). Mirrors the page's entry branch."""
    import datetime as dt

    import paper_engine
    import signal_db

    signals = signal_db.get_open_signals_with_latest_mark()
    paper_engine.run_entry_cycle(_proxy.schwab_py_client, dt.date.today().isoformat(), signals)


def run_manage_cycle() -> None:
    """Run the paper auto-management cycle: reprice + auto-close hits. Mirrors the page."""
    import datetime as dt

    import paper_engine

    paper_engine.run_manage_cycle(_proxy.schwab_py_client, dt.date.today().isoformat())


def reset_paper_account(starting_balance: float) -> None:
    """Reset the paper account to ``starting_balance``. Mirrors the page's reset."""
    import paper_account_db

    paper_account_db.reset_account(starting_balance=starting_balance)


def has_paper_account() -> bool:
    """True if a paper account exists (entry/manage short-circuit on False)."""
    import paper_account_db

    return paper_account_db.get_account() is not None


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
