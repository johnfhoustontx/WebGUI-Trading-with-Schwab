"""Portfolio compute module — NiceGUI-free engine-call layer for ``portfolio_svc``.

The service-side orchestration the legacy desktop ``portfolio_analyzer.py``
performed across its build worker + streamed Tk redraws: pull positions and price
history through the proxy, classify into sectors, run the four sector comparisons
(``build_portfolio``), compute the slow per-symbol baselines, score the live
scorecard (``evaluate_portfolio`` + ``suggest``), and **format** the whole thing
into display-ready rows via that app's ``view_model``. Formatting lives here (Tier
2) so the GUI tier stays a thin renderer with no engine imports.

This module must NOT import ``nicegui`` or anything from ``webgui/``. It depends
only on the shared ``services._proxy`` accessor and the copied
``portfolio-analyzer/src`` engines.

**Isolated engine imports.** ``portfolio-analyzer`` exposes its engines as a
top-level ``src`` package (so does ``trade-analyzer`` — the two collide if both
are on ``sys.path`` in one process). Because ``portfolio_svc`` runs in its own
process, pinning ``src`` here cannot clash with the other domains' engines (the
same isolation ``sentiment_svc``/``trade_svc`` rely on). Run the service test
suites **per folder**, never all of ``services`` at once.

Every engine-call function is defensive (degrades to an empty/error result rather
than raising), so one bad position or proxy hiccup can never crash the service.
"""
import datetime
import sys
from datetime import datetime as _dt, timezone

from repo_paths import PORTFOLIO_ANALYZER
from services import _proxy
from services._parallel import parallel_map

# ── isolated engine imports (separate process — no cross-app name collision) ──
if str(PORTFOLIO_ANALYZER) not in sys.path:
    sys.path.insert(0, str(PORTFOLIO_ANALYZER))

from src.benchmark import load_benchmark_weights  # noqa: E402
from src.data import PortfolioData  # noqa: E402
from src.entries import weighted_avg_entry  # noqa: E402
from src.evaluation import compute_baseline, evaluate_portfolio  # noqa: E402
from src.live import apply_tick, stream_symbols  # noqa: E402, F401  (re-exported: compute.apply_tick / compute.stream_symbols)
from src.portfolio import build_portfolio  # noqa: E402
from src.suggestions import suggest  # noqa: E402
from src.sync import sync_trades  # noqa: E402
from src.thresholds import Thresholds, load_thresholds  # noqa: E402
from src.trade_store import ENTRIES_PATH, load_store, save_store  # noqa: E402
from src.view_model import (  # noqa: E402
    format_holdings_rows, format_performance_rows, format_sector_rows)

# Settings file for the suggestion thresholds (mirrors the desktop app's path).
_SETTINGS_PATH = PORTFOLIO_ANALYZER / "data" / "eval_settings.json"


def _now_iso():
    return _dt.now(timezone.utc).isoformat()


def make_data():
    """A fresh proxy-backed ``PortfolioData`` (owns the SSE stream consumer)."""
    return PortfolioData()


def proxy_up() -> bool:
    """True when the schwab-proxy is reachable + healthy; never raises."""
    try:
        return bool(_proxy.health().get("up"))
    except Exception:  # noqa: BLE001
        return False


def load_benchmark() -> dict:
    """S&P benchmark sector weights for comparison #2; ``{}`` on any failure."""
    try:
        return load_benchmark_weights()
    except Exception:  # noqa: BLE001 — a missing benchmark just skips comparison #2.
        return {}


def load_trades(data, *, path=ENTRIES_PATH, today=None) -> list:
    """Load the trade store and run the daily incremental proxy sync.

    Mirrors the desktop launch flow (``load_store`` → ``sync_trades`` → persist).
    Defensive end-to-end: a corrupt store reads as empty; a proxy/sync failure
    leaves the existing trades intact. ``today`` is injectable for tests.
    """
    try:
        store = load_store(path)
    except Exception:  # noqa: BLE001
        return []
    today = today or datetime.date.today().isoformat()
    try:
        before = (store.get("last_sync"), len(store.get("trades", [])))
        store = sync_trades(data, store, today=today)
        if (store.get("last_sync"), len(store.get("trades", []))) != before:
            save_store(path, store)
    except Exception:  # noqa: BLE001 — sync failure must not lose the local store.
        pass
    return store.get("trades", [])


def build_raw_model(data, trades, *, benchmark=None, ranker=None, classify=None):
    """Build the raw ``{holdings, sectors}`` portfolio model defensively.

    Returns ``(model, errors)``. ``classify``/``ranker`` are injectable (tests
    pass a fake classifier to stay offline; production uses the real lazy
    classifier and ``ranker=None`` → every tailwind is ``None``).
    """
    if benchmark is None:
        benchmark = load_benchmark()
    try:
        kwargs = {"benchmark": benchmark, "ranker": ranker}
        if classify is not None:
            kwargs["classify"] = classify
        model = build_portfolio(data, trades, **kwargs)
        return model, []
    except Exception as exc:  # noqa: BLE001 — surface, never crash the service.
        return {"holdings": [], "sectors": []}, [f"Failed to build portfolio: {exc}"]


def baseline_signature(model, trades, day=None):
    """A comparable signature of the inputs that determine the baselines.

    Baselines (the slow per-symbol history pass) only need recomputing when the
    set of EQUITY holdings (symbol + sector ETF), the cost-weighted entries, or
    the calendar day changes — NOT on every periodic rebuild. ``rebuild`` reuses
    the cached baselines when this is unchanged, eliminating the ~2N+1 serial
    history fetches the 10-min rebuild used to make unconditionally. The day is
    included so history-derived baselines still refresh at least daily even when
    holdings are static. Defensive — malformed inputs degrade to a best-effort
    signature rather than raising."""
    if day is None:
        day = datetime.date.today().isoformat()
    try:
        holdings = tuple(sorted(
            (h.get("symbol"), h.get("sector_etf"))
            for h in (model.get("holdings") or [])
            if h.get("asset_type") == "EQUITY" and h.get("symbol")
        ))
    except Exception:  # noqa: BLE001
        holdings = ()
    try:
        entries = weighted_avg_entry(trades or [])
        entry_sig = tuple(sorted(
            (sym, round(float(e.get("avg_price") or 0.0), 4),
             e.get("entry_date"), round(float(e.get("total_quantity") or 0.0), 4))
            for sym, e in entries.items()
        ))
    except Exception:  # noqa: BLE001
        entry_sig = ()
    return (day, holdings, entry_sig)


def compute_baselines(data, model, trades) -> dict:
    """SLOW per-EQUITY baseline pass (history-derived stats), defensively.

    Ports the desktop ``_compute_baselines``: fetch daily history for each
    symbol + its sector ETF (SPY once), look up the trade-store entry, and run
    ``compute_baseline``. Per-symbol failures degrade to a missing baseline
    rather than failing the lot. The per-symbol fetches (each ~2 proxy calls) run
    concurrently — they are independent and I/O-bound — so a portfolio of N
    equities no longer serializes ~2N round-trips.
    """
    try:
        entries = weighted_avg_entry(trades or [])
    except Exception:  # noqa: BLE001 — a bad store row must not kill the pass.
        entries = {}
    try:
        spy_df = data.get_daily_history("SPY")
    except Exception:  # noqa: BLE001 — proxy hiccup: spy_ret degrades to None.
        spy_df = None

    equities = [h for h in (model.get("holdings") or [])
                if h.get("asset_type") == "EQUITY" and h.get("symbol")]

    def _one(holding):
        """(symbol, baseline) for one equity, or None on any per-symbol failure."""
        symbol = holding.get("symbol")
        try:
            stock_df = data.get_daily_history(symbol)
            sector_etf = holding.get("sector_etf")
            sector_df = (data.get_daily_history(sector_etf)
                         if sector_etf else None)
            return (symbol, compute_baseline(
                holding, stock_df, sector_df, spy_df, entries.get(symbol)))
        except Exception:  # noqa: BLE001 — skip this symbol, keep the rest.
            return None

    return {sym: bl for sym, bl in
            (r for r in parallel_map(_one, equities) if r is not None)}


def _thresholds() -> Thresholds:
    """User-tuned suggestion thresholds (defaults if the file is absent)."""
    try:
        return load_thresholds(_SETTINGS_PATH)
    except Exception:  # noqa: BLE001
        return Thresholds()


def format_payload(model, baselines, *, proxy_up, streaming, errors,
                   thresholds=None) -> dict:
    """Format a raw model + baselines into the cache-ready display payload.

    Reuses the desktop's ``_refresh_performance`` logic: score every holding
    (``evaluate_portfolio``), run the advisory rules (``suggest``), and turn the
    model + cards into display rows via ``view_model``. Pure given its inputs.
    """
    thresholds = thresholds or _thresholds()
    holdings = model.get("holdings") or []
    cards = evaluate_portfolio(model, baselines or {})

    portfolio_value = sum(h.get("market_value") or 0 for h in holdings)
    avg_weight = 1.0 / len(holdings) if holdings else None
    etf_by_symbol = {h.get("symbol"): h.get("sector_etf") for h in holdings}

    suggestions: dict = {}
    for symbol, card in cards.items():
        ctx = {"portfolio_value": portfolio_value, "avg_weight": avg_weight,
               "sector_etf": etf_by_symbol.get(symbol), "top_sector": None}
        suggestions[symbol] = suggest(card, ctx, thresholds)

    return {
        "holdings_rows": format_holdings_rows(model),
        "sector_rows": format_sector_rows(model),
        "performance_rows": format_performance_rows(cards, suggestions),
        "suggestions": suggestions,
        "proxy_up": bool(proxy_up),
        "streaming": bool(streaming),
        "errors": list(errors or []),
        "timestamp": _now_iso(),
    }
