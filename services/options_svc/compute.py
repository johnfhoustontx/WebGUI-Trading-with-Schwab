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


# ── Paper trades ledger (ported from webgui/pages/options/paper.py) ─────────
# The page read the paper-trade ledger directly (``paper_trader.get_all_trades``)
# and ran the close/delete/delete-all/analyze actions itself. Those reads +
# actions now live here so the GUI tier only reads the cached view and enqueues
# commands.
#
# LAZY IMPORTS (IMPORTANT): ``paper_trader``/``trade_analyzer`` may pull in
# options-scanner's ``scoring`` transitively. Importing them at module top would
# bind the process-wide ``sys.modules['scoring']`` merely by importing this
# module — which breaks the sentiment service's ``scoring`` package in the
# *combined* pytest run (all services share one process). So both are imported
# LAZILY inside each function. ``analyze_trade`` is called with
# ``_proxy.schwab_py_client`` (mirrors the page).


def paper_trades_view() -> dict:
    """Read the paper-trade ledger view: ``{"trades": [...]}``.

    Defensively guarded → ``{"trades": []}`` on any failure, mirroring the page's
    per-read try/except. The GUI tier reads this cached view directly."""
    import paper_trader

    try:
        return {"trades": paper_trader.get_all_trades()}
    except Exception:
        return {"trades": []}


def create_paper_trade(signal: dict, qty: int) -> dict:
    """Create + persist a paper trade from a scanner/swing ``signal``.

    Mirrors the page's ``handoff.send_to_paper`` engine calls VERBATIM:
    ``paper_trader.create_paper_trade(signal, qty)`` builds the trade dict, then
    ``paper_trader.add_trade`` persists it to the ledger. Returns the created
    trade dict (so the handler can surface its ``trade_id`` if it ever wants to)."""
    import paper_trader

    trade = paper_trader.create_paper_trade(signal, int(qty))
    paper_trader.add_trade(trade)
    return trade


def _find_trade(trade_id):
    """Look up a ledger trade dict by ``trade_id`` (None if absent)."""
    import paper_trader

    return next((t for t in paper_trader.get_all_trades()
                 if t.get("trade_id") == trade_id), None)


def close_paper(trade_id, debit: float) -> None:
    """Close a paper trade at ``debit`` (per spread). No-op if the trade is gone.

    Mirrors the page: find the trade, ``close_paper_trade`` to compute the closed
    dict, then ``update_trade`` to persist it."""
    import paper_trader

    t = _find_trade(trade_id)
    if t:
        closed = paper_trader.close_paper_trade(t, float(debit), "MANUAL_CLOSE")
        paper_trader.update_trade(trade_id, closed)


def delete_paper(trade_id) -> None:
    """Delete a paper trade by id. Mirrors the page's delete."""
    import paper_trader

    paper_trader.delete_trade(trade_id)


def delete_closed_paper() -> None:
    """Delete all closed/expired paper trades. Mirrors the page's delete-all-closed."""
    import paper_trader

    paper_trader.delete_closed_trades()


def _analyze_detail(result) -> dict | None:
    """Map a ``trade_analyzer.analyze_trade`` result onto the detail-panel field
    names (live Greeks/IV/breakeven/underlying/PoP). None when there's no result."""
    if not result:
        return None
    cur = (result.get("greeks") or {}).get("current") or {}
    market = result.get("market") or {}
    pos = result.get("position") or {}
    delta = cur.get("delta")
    atm_iv = market.get("atm_iv")
    return {
        "short_delta": delta,
        "net_theta": cur.get("theta"),
        "net_vega": cur.get("vega"),
        "short_iv": atm_iv,
        "current_iv": atm_iv,
        "iv_rank": market.get("iv_rank_now"),
        "breakeven": (result.get("profit_target") or {}).get("breakeven"),
        "underlying_price": pos.get("underlying_now"),
        "dte": pos.get("dte_remaining"),
        "unrealized_pnl": pos.get("unrealized_pnl"),
        # PoP ≈ 1 − |live short-leg delta|.
        "pop_pct": round((1.0 - abs(delta)) * 100, 1) if isinstance(delta, (int, float)) and delta else None,
    }


def _expiry_note(trade) -> str | None:
    """Return a human note when ``trade``'s option has already expired, else None.

    An expired expiration has no live option chain on Schwab, so
    ``trade_analyzer.analyze_trade`` raises ``No option chain for …`` for it.
    Detecting this up front lets the GUI show a clear "expired — no live chain"
    note instead of a vague failure, and skips a pointless proxy round-trip.
    Unparseable/missing expirations return None (let the live path try)."""
    import datetime as _dt

    exp = (trade or {}).get("expiration")
    try:
        exp_d = _dt.date.fromisoformat(str(exp)[:10])
    except (TypeError, ValueError):
        return None
    if exp_d < _dt.date.today():
        return f"Expired {exp_d.isoformat()} — no live option chain to analyze"
    return None


def analyze_paper(trade_id) -> dict:
    """Analyze a paper trade (live Greeks/IV) → ``{trade_id, symbol, action, detail, note}``.

    ``detail`` carries the live values mapped onto the detail-panel field names
    (see ``_analyze_detail``) so the GUI can overlay them on the stored view.
    ``note`` is None on success, else a human-readable reason the live analysis
    couldn't run — so the GUI can say *why* instead of a vague "live data
    unavailable":

    * trade not found → ``"Trade not found"``;
    * the option already expired (no live chain exists) → ``action="EXPIRED"`` +
      an expiry note, WITHOUT calling the engine (avoids a doomed proxy fetch);
    * any other live-fetch failure (after-hours / no chain / RuntimeError from
      ``analyze_trade``) → the exception text, ``action="—"``, ``detail=None``.

    Uses ``_proxy.schwab_py_client`` (mirrors the page)."""
    import trade_analyzer

    t = _find_trade(trade_id)
    if t is None:
        return {"trade_id": trade_id, "symbol": None, "action": "—",
                "detail": None, "note": "Trade not found"}

    expired = _expiry_note(t)
    if expired:
        return {"trade_id": trade_id, "symbol": t.get("symbol"),
                "action": "EXPIRED", "detail": None, "note": expired}

    note = None
    try:
        result = trade_analyzer.analyze_trade(_proxy.schwab_py_client, t, None)
    except Exception as exc:
        result = None
        note = f"Live data unavailable: {exc}"
    return {
        "trade_id": trade_id,
        "symbol": t.get("symbol"),
        "action": ((result or {}).get("verdict") or {}).get("action", "—"),
        "detail": _analyze_detail(result),
        "note": note,
    }


# ── Captured signals (ported from webgui/pages/options/captured.py) ─────────
# The page read open signals directly (``signal_db.get_open_signals_with_latest_mark``)
# and ran the reprice-marks + manual-close actions itself. Those reads + actions
# now live here so the GUI tier only reads the cached view and enqueues commands.
#
# LAZY IMPORTS (IMPORTANT): ``signal_db``/``signal_repricer``/``signal_recommender``
# can pull in options-scanner's ``scoring`` transitively. Importing them at module
# top would bind the process-wide ``sys.modules['scoring']`` merely by importing
# this module — which breaks the sentiment service's ``scoring`` package in the
# *combined* pytest run (all services share one process). So all three are
# imported LAZILY inside each function. ``reprice_swing`` is called with
# ``_proxy.schwab_py_client`` (mirrors the page).

_STOP_CODES = ("TARGET_HIT", "MONEY_STOP", "DELTA_STOP", "TIME_STOP")


def captured_view() -> dict:
    """Read the open-signals view: ``{"signals": [...]}``.

    Defensively guarded → ``{"signals": []}`` on any failure, mirroring the
    page's per-read try/except. The GUI tier reads this cached view directly."""
    import signal_db

    try:
        return {"signals": signal_db.get_open_signals_with_latest_mark()}
    except Exception:
        return {"signals": []}


def reprice_captured() -> dict:
    """Reprice all open signals; merge mark fields into the rows + collect flags.

    Ports the page's ``_reprice_all`` loop into compute: for each open signal,
    reprice + build a mark, merge the mark's display fields into the signal dict
    (``unrealized_pnl``/``current_score``/``score_drift``/``recommendation`` — NOT
    persisted), and flag any signal whose recommendation code is one of the four
    stop/target codes. Defensive per-signal (continue on failure). Returns
    ``{"signals": [...repriced...], "flags": [{"symbol","code"}, ...]}``."""
    import datetime as dt

    import signal_db
    import signal_recommender
    import signal_repricer

    try:
        sigs = signal_db.get_open_signals_with_latest_mark()
    except Exception:
        sigs = []

    now = dt.datetime.now(dt.timezone.utc)
    flags = []
    for r in sigs:
        try:
            rep = signal_repricer.reprice_swing(r, _proxy.schwab_py_client)
            mark = signal_recommender.build_mark(r, rep, now)
        except Exception:
            continue
        if not mark:
            continue
        # Merge the mark's display fields into the row (mirrors the page; not
        # persisted — the GUI reads these off the cached repriced list).
        r["unrealized_pnl"] = mark.get("unrealized_pnl")
        r["current_score"] = mark.get("current_score")
        r["score_drift"] = mark.get("score_drift")
        if mark.get("recommendation") is not None:
            r["recommendation"] = mark.get("recommendation")
        code = (mark.get("recommendation_code") or "").upper()
        if code in _STOP_CODES:
            flags.append({"symbol": r.get("symbol"), "code": code})
    return {"signals": sigs, "flags": flags}


def close_captured(signal_id, exit_val: float, reason: str) -> None:
    """Manually close a captured signal at ``exit_val``. Mirrors the page's close."""
    import signal_db

    signal_db.close_signal_manually(signal_id, float(exit_val), reason or "MANUAL_CLOSE")


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


# ── Gamma (ported from webgui/pages/options/gamma.py) ───────────────────────
# The heaviest options page: a live option-chain fetch + GammaEngine compute
# (GEX/Charm/DEX/Vanna) + per-view summary/walls/history grids + a term grid +
# the Explain document + the multi-symbol Analyze prompt. All of it now runs
# here so the GUI tier only reads a cached snapshot and enqueues commands.
#
# LAZY IMPORTS (IMPORTANT): ``gamma_tool``/``gex_history_db``/``html_render``/
# ``regime_filter`` can pull options-scanner's ``scoring`` (and other heavy
# deps) transitively. Importing them at module top would bind the process-wide
# ``sys.modules['scoring']`` to options-scanner's ``scoring.py`` merely by
# importing this module — which breaks the sentiment service's ``scoring``
# package in the *combined* pytest run (all services share one process). So each
# is imported LAZILY inside the functions below.

# view name -> (tuple index from calc_all_from_chain, engine view string).
# Mirrors the page's _VIEWS; the page keeps its own copy for the figure builders.
_GAMMA_VIEWS = {"GEX": (0, "gex"), "Charm": (1, "charm"),
                "DEX": (2, "dex"), "Vanna": (3, "vanna")}


def _gamma_fetch_chain(symbol):
    """Fetch the option chain for ``symbol`` (today → +7d). None on non-200/empty.

    Mirrors the page's ``do_fetch``/``_analyze_prompt`` chain pull exactly."""
    import datetime as dt

    resp = _proxy.schwab_py_client.get_option_chain(
        symbol, contract_type="ALL", from_date=dt.date.today(),
        to_date=dt.date.today() + dt.timedelta(days=7))
    return resp.json() if getattr(resp, "status_code", None) == 200 else None


def gamma_walls(vname, data, spot):
    """[put_wall, call_wall] strikes for GEX/DEX (one per side), else [].

    Reuses the engine's directional-wall picker (call wall = strike > spot with
    largest call GEX; put wall = strike < spot with most-negative put GEX). The
    DEX per-strike map is keyed 'dex', so it is remapped to 'gex' for the picker.
    Defensive: any failure degrades to []. ``gamma_tool`` is imported lazily (see
    the LAZY IMPORTS note above)."""
    import gamma_tool as gt
    try:
        if vname == "GEX":
            w = gt.get_directional_walls(data, spot)
        elif vname == "DEX":
            w = gt.get_directional_walls({"gex": (data or {}).get("dex")}, spot)
        else:
            return []
    except Exception:
        return []
    return [s for s in (w.get("put_wall"), w.get("call_wall")) if s is not None]


def gamma_snapshot(symbol: str) -> dict | None:
    """Fetch + compute the full Gamma snapshot for ``symbol``.

    Returns a JSON-serializable dict the GUI paints from:

        {"symbol", "spot", "dte",
         "views": {"GEX"/"Charm"/"DEX"/"Vanna": {
             "data": <per-strike dict>, "summary": {...}, "walls": [...],
             "flip": <float|None>, "history": [<rows>], ["hedge": {...}]}},
         "term": <term_grid>}

    Returns None if the chain fetch fails or GammaEngine can't compute — the
    handler caches a graceful-empty view in that case. Per-view sub-failures are
    defensive (a single view degrades to empty fields) so one bad view never
    aborts the whole snapshot.

    The per-strike ``data`` dicts have FLOAT keys; once cached as JSON those keys
    round-trip to STRINGS, so the GUI re-floats them before feeding the pure
    figure builders (``gamma._refloat_keys``)."""
    import gamma_tool as gt
    import gex_history_db as gh

    chain = _gamma_fetch_chain(symbol)
    if not chain:
        return None

    eng = gt.GammaEngine()
    res = eng.calc_all_from_chain(chain)
    if not res:
        return None
    gex, charm, dex, vanna = res
    by_index = {0: gex, 1: charm, 2: dex, 3: vanna}
    dte = eng._last_dte
    spot = (gex or {}).get("spot")

    def _walls(vname, data):
        return gamma_walls(vname, data, spot)

    def _history(vstr):
        try:
            conn = gh.connect(read_only=True)
            return gh.load_today_with_grid(conn, symbol, vstr)
        except Exception:
            return []

    views = {}
    for vname, (idx, vstr) in _GAMMA_VIEWS.items():
        data = by_index.get(idx) or {}
        try:
            summary = eng.snapshot_summary(data, vstr)
        except Exception:
            summary = {}
        entry = {
            "data": data,
            "summary": summary,
            "walls": _walls(vname, data),
            "flip": (summary or {}).get("flip"),
            "history": _history(vstr),
        }
        if vname == "DEX":
            entry["hedge"] = {
                "net_delta_0dte": data.get("net_delta_0dte"),
                "projected_net_delta_close": data.get("projected_net_delta_close"),
                "hedge_pressure": data.get("hedge_pressure"),
            }
        views[vname] = entry

    try:
        term = eng.compute_term_grid(chain)
    except Exception:
        term = {}

    return {"symbol": symbol, "spot": spot, "dte": dte,
            "views": views, "term": term}


# ── Intraday GEX history collection (Tier-2 owner) ──────────────────────────
# The Gamma page's strike×time heatmap reads gex_history.db. That DB used to be
# written ONLY by the standalone options-scanner/gex_collector.py process,
# launched in its own console window by start_all.bat. When that window died
# (closed, machine sleep, or lock contention from a double launch) collection
# silently stopped and the heatmap froze at the first snapshots — "no data past
# the first hour". The always-on options service now owns collection: the
# scheduler calls this on every 2-min slot within market hours, so history
# accrues for the whole session whenever the service is up.

def collect_gex_snapshots() -> int:
    """Fetch + persist one snapshot round (GEX/Charm/DEX/Vanna + term) for the
    tracked symbols. Returns ``len(gex_collector.collection_symbols())`` (the
    dynamic collection universe), or ``0`` when a fresh foreign collector owns
    the advisory lock (we defer).

    Reuses options-scanner's ``gex_collector.poll_once`` (engine compute +
    ``gex_history_db.insert_snapshot``) VERBATIM so the snapshot schema + symbol
    list stay in ONE place. The schwab-py client comes from the shared proxy
    accessor (mirrors ``run_scan``/``gamma_snapshot``); the GammaEngine + write
    connection are built here. The collector's own advisory lock
    (``data/gex_collector.lock``) makes any still-running standalone
    ``gex_collector.py`` defer to this service, so only one writer runs.
    Lazy imports (like ``gamma_snapshot``) keep module import light + dodge the
    cross-app name collisions documented in the root CLAUDE.md."""
    import os
    import time

    import gex_collector as gc

    gc.ensure_file_logging()  # poll warnings/errors land in gex_collector.log
    owner = f"options_svc:pid:{os.getpid()}"
    if not gc.acquire_collector_lock(gc.LOCK_PATH, source="options_svc",
                                     owner=owner, now=int(time.time())):
        gc.log.info("Another collector owns the lock; options_svc deferring.")
        return 0

    import gamma_tool as gt
    import gex_history_db as gh

    conn = gh.connect()
    try:
        gh.init_schema(conn)
        gc.log.info("Polling GEX history (options_svc)")
        gc.poll_once(_proxy.schwab_py_client, gt.GammaEngine(), conn)
        gc.touch_lock(gc.LOCK_PATH, source="options_svc", owner=owner,
                      now=int(time.time()))
    finally:
        conn.close()
    return len(gc.collection_symbols())


def _gex_next_scan(now):
    """Next 2-min GEX-collection boundary strictly after ``now`` within the
    08:30–15:20 CT window, or None if ``now`` is past the window end.

    Reuses the scheduler's ``_GEX_START``/``_GEX_STOP``/``_GEX_INTERVAL_MIN``
    cadence (08:30–15:20 CT, every 2 min). Returns a CT-aware datetime or None.
    Before 08:30 → the window's first slot (08:30 today). At/after 15:20 → None.

    The ``scheduler`` import is LAZY (inside the function) on purpose: ``scheduler``
    imports ``handlers`` which imports this module, so importing ``scheduler`` at
    module top would be a circular import.
    """
    import datetime as _dt

    from services.options_svc import scheduler as _sched

    start_h, start_m = _sched._GEX_START
    stop_h, stop_m = _sched._GEX_STOP
    step = _sched._GEX_INTERVAL_MIN

    start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    stop = now.replace(hour=stop_h, minute=stop_m, second=0, microsecond=0)
    if now < start:
        return start
    if now >= stop:
        return None
    # Round up to the next ``step``-min boundary strictly after now.
    floored = now.replace(second=0, microsecond=0)
    nxt = floored + _dt.timedelta(minutes=step - (floored.minute % step))
    if nxt <= now:
        nxt = nxt + _dt.timedelta(minutes=step)
    if nxt >= stop:
        return None
    return nxt


def _fmt_clock(d):
    """Format a datetime as a short local clock string (e.g. ``9:05 AM``)."""
    return d.strftime("%I:%M %p").lstrip("0")


def gex_status_view(now=None) -> dict:
    """Build the GEX-collector status view the Gamma page's status bar reads.

    Returns ``{"status_label", "status_color", "last_scan", "next_scan",
    "age_seconds"}`` — all JSON-serializable. ``status_label``/``status_color``
    come from options-scanner's ``gex_status.classify_collector_status`` over the
    latest ``$SPX``/``gex`` snapshot age (read-only DB open). ``last_scan`` is the
    last snapshot's local clock time (None if no data); ``next_scan`` is the next
    5-min collection boundary within the 08:30–15:20 CT window (None outside it).

    Fully defensive: any failure (DB locked/missing, import error) degrades to a
    safe default dict so the page's status bar never breaks."""
    import datetime as _dt
    from zoneinfo import ZoneInfo

    try:
        if now is None:
            now = _dt.datetime.now(ZoneInfo("America/Chicago"))

        import gex_history_db as gh
        import gex_status as gs

        conn = gh.connect(read_only=True)
        try:
            age, last_ts = gh.last_snapshot_age(conn, "$SPX", "gex")
        finally:
            try:
                conn.close()
            except Exception:
                pass
        has_data = last_ts is not None
        label, color = gs.classify_collector_status(age, now, has_data, last_ts)

        last_scan = None
        if last_ts is not None:
            last_scan = _fmt_clock(
                _dt.datetime.fromtimestamp(last_ts, ZoneInfo("America/Chicago")))

        nxt = _gex_next_scan(now)
        next_scan = _fmt_clock(nxt) if nxt is not None else None

        return {"status_label": label, "status_color": color,
                "last_scan": last_scan, "next_scan": next_scan,
                "age_seconds": age}
    except Exception:
        return {"status_label": "Collector status unknown",
                "status_color": "#666666", "last_scan": None,
                "next_scan": None, "age_seconds": None}


def build_gamma_read(symbol, spot, gex_summary, charm_summary, dex_summary,
                     vanna_summary, walls, regime):
    """Map the gamma-engine summaries + walls + sentiment → a GammaRead.

    Pure: numbers in, ``gamma_infographic.GammaRead`` out. Missing levels fall
    back to spot so the infographic's axis math never sees ``None``; missing
    sentiment uses neutral defaults; a missing Vanna net leaves ``vex`` None so
    the card renders 'awaiting data'."""
    from gamma_infographic import GammaRead

    s = spot if isinstance(spot, (int, float)) else 0.0
    gx, ch, dx, vn = (gex_summary or {}), (charm_summary or {}), (dex_summary or {}), (vanna_summary or {})
    reg, walls = (regime or {}), (walls or {})

    def _lvl(v):
        return v if isinstance(v, (int, float)) else s

    def _num(v, default=None):
        return v if isinstance(v, (int, float)) else default

    score = reg.get("composite_score")
    score = int(round(score)) if isinstance(score, (int, float)) else 6
    conf = reg.get("aggregate_confidence")
    conf = int(round(conf)) if isinstance(conf, (int, float)) else 100
    trend = reg.get("bias") or reg.get("trend_state") or "neutral"

    return GammaRead(
        spot=s,
        call_wall=_lvl(walls.get("call_wall")),
        put_wall=_lvl(walls.get("put_wall")),
        gamma_flip=_lvl(gx.get("flip")),
        charm_flip=_lvl(ch.get("flip")),
        charm_max_pos=_lvl(ch.get("top_pos_strike")),
        charm_max_neg=_lvl(ch.get("top_neg_strike")),
        dex_flow_usd=_num(dx.get("net_total"), 0.0),
        vex_notional_usd=_num(vn.get("net_total")),
        sentiment_score=score,
        sentiment_trend=str(trend),
        sentiment_confidence=conf,
        symbol=symbol,
    )


def gamma_explain(symbol: str, style: str = "terminal") -> dict:
    """Build the Explain **infographic** for ``symbol`` → ``{"symbol", "html"}``.

    Re-fetches + recomputes the chain, maps the GEX/Charm/DEX/Vanna summaries +
    directional walls + sentiment into a ``gamma_infographic.GammaRead`` and
    renders a self-contained HTML infographic (the GUI serves it in a new browser
    tab via a raw HTMLResponse route — so the doc's own CSS/fonts apply).

    Defensive: a fetch/compute failure yields a minimal standalone page so the
    GUI always has something to show."""
    import gamma_infographic
    import gamma_tool as gt

    try:
        from regime_filter import evaluate_regime
    except Exception:
        evaluate_regime = lambda: {"active": False}  # noqa: E731

    def _fallback(msg):
        return {"symbol": symbol,
                "html": ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                         f"<title>{symbol} — Gamma Read</title></head>"
                         "<body style=\"font-family:system-ui,sans-serif;background:#0c0f15;"
                         "color:#e9edf3;padding:40px;\">"
                         f"<h2>{symbol} — Gamma Read</h2><p>{msg}</p></body></html>")}

    chain = _gamma_fetch_chain(symbol)
    if not chain:
        return _fallback("No chain data available.")
    eng = gt.GammaEngine()
    res = eng.calc_all_from_chain(chain)
    if not res:
        return _fallback("No chain data available.")
    gex, charm, dex, vanna = res
    spot = (gex or {}).get("spot")

    try:
        regime = evaluate_regime() or {"active": False}
    except Exception:
        regime = {"active": False}

    walls = gt.get_directional_walls(gex, spot)
    read = build_gamma_read(
        symbol, spot,
        gt.GammaEngine.snapshot_summary(gex, "gex"),
        gt.GammaEngine.snapshot_summary(charm, "charm"),
        gt.GammaEngine.snapshot_summary(dex, "dex"),
        gt.GammaEngine.snapshot_summary(vanna, "vanna"),
        walls, regime)
    try:
        html = gamma_infographic.render_infographic(read, style=style)
    except Exception as exc:  # never let a render glitch break the command
        return _fallback(f"Infographic render failed: {exc}")
    return {"symbol": symbol, "html": html}


def gamma_symbol_options() -> list:
    """Dropdown universe for the Gamma page: the collected symbols minus ``$VIX``
    ($SPX first). $VIX is still collected (sentiment bridge) but isn't a useful
    Gamma selection. Defensive: any failure → the index trio so the page always
    gets a usable list. ``gex_collector`` is imported lazily (see LAZY IMPORTS)."""
    try:
        import gex_collector as gc
        return [s for s in gc.collection_symbols() if s != "$VIX"]
    except Exception:
        return ["$SPX", "SPY", "QQQ"]


def _gamma_blocks_for(symbol, chain):
    """Build the per-view analysis blocks for one symbol (ported from the page).

    Returns ``{"gex","charm","dex","vanna"}`` analysis dicts (or None for a view
    with no snapshot), or None if the chain can't be computed."""
    import gamma_tool as gt

    eng = gt.GammaEngine()
    res = eng.calc_all_from_chain(chain)
    if not res:
        return None
    gex, charm, dex, vanna = res
    try:
        em = eng.calc_expected_move_from_chain(chain)
    except Exception:
        em = None
    dte = eng._last_dte

    def bd(snap, view):
        if not snap:
            return None
        return gt.build_analysis_dict(snap, view, symbol, dte,
                                      expected_move=em, grouping=1, chain=chain)

    return {"gex": bd(gex, "gex"), "charm": bd(charm, "charm"),
            "dex": bd(dex, "dex"), "vanna": bd(vanna, "vanna")}


def gamma_analyze() -> dict:
    """Build the bundled SPX/SPY/QQQ Analyze prompt → ``{"prompt": <text>}``.

    Ports the page's ``_analyze_prompt``: fetch each of $SPX/SPY/QQQ, build its
    analysis blocks (defensive per-symbol → None on failure), then bundle them
    via ``build_summary_prompt_bundled``."""
    import gamma_tool as gt

    blocks = {}
    for key, sym in (("spx", "$SPX"), ("spy", "SPY"), ("qqq", "QQQ")):
        try:
            chain = _gamma_fetch_chain(sym)
            blocks[key] = _gamma_blocks_for(sym, chain) if chain else None
        except Exception:
            blocks[key] = None
    prompt = gt.build_summary_prompt_bundled(blocks["spx"], blocks["spy"], blocks["qqq"])
    return {"prompt": prompt}


# ── Calculator (ported from webgui/pages/options/calculator.py) ──────────────
# The page used to fetch the symbol quote + option chain itself and run the
# ``options_calculator`` math (summary tiles + P&L grid) on every Calculate. Both
# the FETCH (``calc_load_symbol``, mirroring the page's ``_load_symbol_data``)
# and the MATH (``calc_compute``, porting ``do_calc`` verbatim) now run here so
# the GUI tier only reads the cached chain dict + result and enqueues commands.
#
# The option chain is a plain JSON dict (``resp.json()``) so it round-trips
# through the cache fine; the page keeps its PURE chain-extractors (extract_atm_iv
# /extract_premium/chain_expiries/chain_strikes) and runs them on the cached dict.
#
# LAZY IMPORTS (IMPORTANT): ``options_calculator`` is imported lazily inside each
# function — merely importing this module never drags the calculator engine into
# the process, keeping the combined pytest run clean (mirrors the other compute
# fns).


def calc_load_symbol(symbol) -> dict:
    """Fetch the quote + option chain for ``symbol`` → JSON-safe loader payload.

    Mirrors the page's ``_load_symbol_data`` + ``load_symbol``: map the symbol to
    its Schwab API form ($SPX for SPX), pull the quote (lastPrice) and the
    today→+60d ``ALL`` chain, then compute the default price range via
    ``oc.generate_price_range``. Returns ``{"symbol", "api", "price", "range_lo",
    "range_hi", "chain"}`` — ``chain`` is the raw JSON dict the page extracts from
    locally. Defensive: a non-200 quote/chain degrades to ``{}``/None."""
    import datetime as dt

    import options_calculator as oc

    api = "$SPX" if symbol.upper() == "SPX" else symbol.upper()

    qresp = _proxy.schwab_py_client.get_quotes([api])
    quote = qresp.json() if getattr(qresp, "status_code", None) == 200 else {}
    cresp = _proxy.schwab_py_client.get_option_chain(
        api, contract_type="ALL", from_date=dt.date.today(),
        to_date=dt.date.today() + dt.timedelta(days=60))
    chain = cresp.json() if getattr(cresp, "status_code", None) == 200 else None

    info = (quote or {}).get(api, {})
    q = info.get("quote", info.get("reference", info)) if isinstance(info, dict) else {}
    price = q.get("lastPrice") if isinstance(q, dict) else None

    lo, hi = oc.generate_price_range(price) if price else (0.0, 0.0)
    return {"symbol": symbol, "api": api, "price": price,
            "range_lo": lo, "range_hi": hi, "chain": chain}


def symmetric_price_range(spot, strikes, pct=0.05):
    """Price range symmetric about spot, widened to include all strikes.
    Returns (low, high) with midpoint == spot."""
    half = spot * pct
    for k in strikes or []:
        if k is not None:
            half = max(half, abs(k - spot))
    return (round(spot - half, 2), round(spot + half, 2))


def calc_compute(strategy, spot, iv, rate, ivadj, qty, expiry, legs,
                 range_min, range_max, range_pct) -> dict:
    """Run the calculator math → ``{"summary", "eval_labels", "pnl_data"}``.

    Ports the page's ``do_calc`` math VERBATIM: time-to-expiry in years (clamped
    ≥ 1 day), the ``calc_summary`` tiles, the ``generate_eval_dates`` columns, the
    price range (explicit min/max when valid, else ``symmetric_price_range`` —
    symmetric about spot, widened to span all leg strikes — at ``range_pct``),
    and the ``calc_spread_pnl`` grid. ``expiry`` arrives as an ISO
    string (parsed with ``date.fromisoformat``). Eval dates are PRE-FORMATTED to
    ``MM/DD`` strings server-side so the page's grid header needs no date objects.

    ``legs``/``summary``/``pnl_data`` are JSON-safe (dicts/lists of numbers)."""
    import datetime as dt

    import options_calculator as oc

    expiry_date = dt.date.fromisoformat(str(expiry))
    today = dt.date.today()

    T = max((expiry_date - today).days, 0) / 365.0 or 1 / 365.0
    summary = oc.calc_summary(legs, strategy, spot, r=rate, iv=iv, T=T)
    eval_dates = oc.generate_eval_dates(today, expiry_date)
    if range_min and range_max and range_max > range_min:
        price_range = (range_min, range_max)
    else:
        # Symmetric about spot (spot dead-center), widened so every leg's strike
        # falls inside the grid. Replaces ``oc.generate_price_range`` (which could
        # render asymmetrically / clip the strikes) — engine math is unchanged.
        strikes = [leg.get("strike") for leg in (legs or [])]
        price_range = symmetric_price_range(spot, strikes, pct=range_pct)
    pnl_data = oc.calc_spread_pnl(legs, spot, iv, rate, eval_dates, price_range,
                                  expiry_date, iv_adjustment=ivadj)

    eval_labels = [d.strftime("%m/%d") if hasattr(d, "strftime") else str(d)
                   for d in eval_dates]
    return {"summary": summary, "eval_labels": eval_labels, "pnl_data": pnl_data}


# ── Simulator (ported from webgui/pages/options/simulator.py) ────────────────
# The What-if price sweep + IV-shock simulator. The page used to fetch a
# ChainSnapshot OBJECT and call the pure ``options_simulator`` engines over it on
# every selector/slider change. That snapshot is a Python object (not
# JSON-serializable as a whole), so it stays IN-PROCESS here: ``sim_fetch`` pulls
# it once and stashes it in ``_SIM_SNAPSHOTS`` (symbol → snapshot); ``sim_run``
# looks it up by symbol and computes both sweeps, returning only JSON-safe rows.
# Single-user, single-process service, so a module-level dict is fine.
#
# LAZY IMPORTS (IMPORTANT): ``options_simulator.data``/``.engine`` (and numpy)
# are imported lazily inside the functions, mirroring the other compute fns — so
# merely importing this module never drags the simulator engine (and its deps)
# into the process, keeping the combined pytest run clean.

# symbol -> ChainSnapshot object (in-process; never serialized whole).
_SIM_SNAPSHOTS: dict = {}


def expiries_of(snapshot):
    """Sorted unique expiries (as ISO strings) in the snapshot. (Moved from page.)"""
    return sorted({str(c.expiry) for c in getattr(snapshot, "contracts", []) or []})


def strikes_of(snapshot, expiry, kind):
    """Sorted unique strikes for an expiry + kind (call/put). (Moved from page.)"""
    out = {c.strike for c in getattr(snapshot, "contracts", []) or []
           if str(c.expiry) == str(expiry) and c.kind == kind}
    return sorted(out)


def find_contract(snapshot, expiry, kind, strike):
    """Find the matching ContractRow (None if absent). (Moved from page.)"""
    for c in getattr(snapshot, "contracts", []) or []:
        if str(c.expiry) == str(expiry) and c.kind == kind and c.strike == strike:
            return c
    return None


def _sim_records(df):
    """Normalize a DataFrame or list-of-dicts to a list of dict rows.

    Mirrors the page's ``_records`` so the what-if sweep is returned as a plain
    JSON-safe list of dict rows the page paints from."""
    if hasattr(df, "to_dict"):
        return df.to_dict("records")
    return list(df or [])


def sim_fetch(symbol: str) -> dict:
    """Fetch the ChainSnapshot for ``symbol``, stash it in-process, return meta.

    The whole snapshot is a Python object (price-history series + ContractRow
    list) and is NOT JSON-serializable as a unit, so it stays in
    ``_SIM_SNAPSHOTS`` keyed by symbol. We return only the page-selector metadata
    the GUI needs to populate its expiry/strike dropdowns: spot, contract count,
    the sorted expiries, and a nested ``strikes`` map (expiry → {call, put}).
    Computing the full nested strike map up front (vs. a per-(expiry,kind)
    follow-up command) keeps selector changes instant on the page with no extra
    round-trip — the per-symbol cost is one pass over the contracts list."""
    from options_simulator import data as sdata

    snap = sdata.fetch_snapshot(_proxy.schwab_py_client, symbol)
    _SIM_SNAPSHOTS[symbol] = snap
    exps = expiries_of(snap)
    return {
        "symbol": snap.symbol,
        "spot": snap.spot,
        "n_contracts": len(snap.contracts),
        "expiries": exps,
        "strikes": {exp: {"call": strikes_of(snap, exp, "call"),
                          "put": strikes_of(snap, exp, "put")}
                    for exp in exps},
    }


def sim_run(symbol, expiry, kind, strike, direction, dt, mult) -> dict:
    """Compute BOTH simulator sweeps for the selected contract → JSON-safe dict.

    Ports the page's render logic verbatim: the what-if sweep over an 81-point
    ±20% price range at ``dt`` days-to-event, and the IV-shock base-vs-shock pair
    at ``[1.0, mult]``. The snapshot is looked up by symbol from the in-process
    stash; a missing snapshot (service restarted / never fetched) → ``{}`` so the
    page can prompt a re-fetch, and a missing contract → ``{}`` (page prompts a
    selection). Returns ``{"spot", "whatif_rows", "ivshock"}`` where ``ivshock``
    is ``{"base", "shock"}`` (or None if the engine returned <2 rows)."""
    from options_simulator import engine as seng
    import numpy as np

    snap = _SIM_SNAPSHOTS.get(symbol)
    if snap is None:
        return {}
    contract = find_contract(snap, expiry, kind, strike)
    if contract is None:
        return {}

    pos = seng.Position.single(contract, direction, snap.symbol)

    # What-if: 81-point ±20% underlying sweep at ``dt`` days (clamp 0 → 0.01).
    s_range = np.linspace(snap.spot * 0.8, snap.spot * 1.2, 81)
    whatif_eng = seng.WhatIfEngine(snap)
    wdf = seng.aggregate_position(
        pos, lambda c: whatif_eng.sweep(c, s_range, float(dt) or 0.01))
    whatif_rows = _sim_records(wdf)

    # IV-shock: base (×1.0) vs shock (×mult).
    shock_eng = seng.IVShockEngine(snap)
    sdf = seng.aggregate_position(pos, lambda c: shock_eng.sweep(c, [1.0, float(mult)]))
    rows = sdf.to_dict("records") if hasattr(sdf, "to_dict") else list(sdf or [])
    ivshock = {"base": rows[0], "shock": rows[1]} if len(rows) >= 2 else None

    return {"spot": snap.spot, "whatif_rows": whatif_rows, "ivshock": ivshock}


_REPLAY_OVERRIDES = {
    "1m_1d":   {"freq_type": "minute", "minutes": 1,  "days": 1,  "label": "1-min · 1d"},
    "5m_3d":   {"freq_type": "minute", "minutes": 5,  "days": 3,  "label": "5-min · 3d"},
    "5m_5d":   {"freq_type": "minute", "minutes": 5,  "days": 5,  "label": "5-min · 5d"},
    "15m_10d": {"freq_type": "minute", "minutes": 15, "days": 10, "label": "15-min · 10d"},
    "1d_20d":  {"freq_type": "daily",  "months": 1,   "bars": 20, "label": "daily · 20d"},
}


def replay_lookback_spec(dte, override="auto") -> dict:
    """Map ``(dte, override)`` → a price-history fetch spec for the Replay path.

    ``override`` of ``"auto"`` (or any unknown key) uses the DTE tiers
    (0 → 1-min/1d · ≤5 → 5-min/3d · ≤15 → 5-min/5d · >15 → daily/~½×DTE); any
    known override key selects a fixed window. Always returns a dict with
    ``freq_type`` ('minute'|'daily') plus the params the fetch helper needs
    (``minutes``/``days`` for intraday, ``months``/``bars`` for daily) and a
    human ``label``."""
    import math
    if override and override != "auto" and override in _REPLAY_OVERRIDES:
        return dict(_REPLAY_OVERRIDES[override])
    try:
        dte = int(dte)
    except (TypeError, ValueError):
        dte = 15
    if dte <= 0:
        return {"freq_type": "minute", "minutes": 1, "days": 1, "label": "1-min · 1d"}
    if dte <= 5:
        return {"freq_type": "minute", "minutes": 5, "days": 3, "label": "5-min · 3d"}
    if dte <= 15:
        return {"freq_type": "minute", "minutes": 5, "days": 5, "label": "5-min · 5d"}
    bars = math.ceil(dte / 2)
    months = max(1, math.ceil(bars / 21))
    return {"freq_type": "daily", "months": months, "bars": bars,
            "label": f"daily · {bars}d"}


def sim_replay(symbol, expiry, kind, strike, direction, lookback="auto") -> dict:
    """Re-price the selected contract along the underlying's recent price path.

    Ports the legacy Tk Replay tab: ``ReplayEngine.full_trace`` over a price
    path, plus the gap-compression / session-boundary layout the page needs to
    draw a clean integer x-axis (overnight/weekend breaks collapsed onto
    consecutive indices). The path is a **DTE-aware** window fetched here
    (``replay_lookback_spec`` → ``_fetch_replay_history``), NOT the snapshot's
    fixed 2-day history — the expiry/DTE is only known at replay time, and
    ``lookback`` ('auto' or an override key) lets the page widen/narrow it.
    Returns a JSON-safe dict; ``{}`` if the snapshot/contract is missing (page
    prompts a re-fetch / selection), or ``{"error": ...}`` if IV is unavailable
    or there's no price history. Replay depends ONLY on the contract selector +
    look-back — not the dt/mult sliders — so it is its own command/cache view,
    separate from ``sim_run`` (keeps slider-driven sweeps cheap)."""
    from options_simulator import engine as seng
    import numpy as np
    import dataclasses
    import datetime as dt

    snap = _SIM_SNAPSHOTS.get(symbol)
    if snap is None:
        return {}
    contract = find_contract(snap, expiry, kind, strike)
    if contract is None:
        return {}
    if contract.iv <= 0:
        return {"error": "IV unavailable - cannot simulate"}

    # DTE-aware history window (fetched here, not the snapshot's fixed 2-day path).
    try:
        dte = (contract.expiry - dt.date.today()).days
    except Exception:
        dte = 15
    spec = replay_lookback_spec(dte, lookback)
    hist = _fetch_replay_history(snap.symbol, spec)
    if hist is None or hist.empty:
        return {"error": "Replay unavailable - no price history"}

    # Re-price along the fetched path (shallow-copy the snapshot's history so the
    # cached snapshot is untouched).
    snap_path = dataclasses.replace(snap, price_history=hist)
    pos = seng.Position.single(contract, direction, snap.symbol)
    trace = seng.aggregate_position(
        pos, lambda c: seng.ReplayEngine(snap_path).full_trace(c))
    if trace is None or trace.empty:
        return {"error": "Replay unavailable - no price history"}

    # Compress overnight/weekend gaps onto an integer x-axis: a "gap" is any
    # inter-bar interval bigger than ~3× the typical bar spacing (≥1h), exactly
    # as the legacy window did.
    if len(hist) >= 2:
        deltas = (hist.index[1:] - hist.index[:-1]).total_seconds()
        median_delta_s = float(np.median(deltas))
        gap_threshold_s = max(median_delta_s * 3, 60 * 60)
        gap_indices = [i + 1 for i, d in enumerate(deltas) if d > gap_threshold_s]
    else:
        median_delta_s = 0.0
        gap_indices = []

    sessions = []
    starts = [0] + gap_indices
    ends = gap_indices + [len(hist)]
    for s, e in zip(starts, ends):
        if e > s:
            sessions.append({"start": int(s), "end": int(e),
                             "date": hist.index[s].strftime("%Y-%m-%d")})

    sessions_n = len(gap_indices) + 1 if len(hist) else 0
    if len(hist) >= 2:
        if median_delta_s < 120:
            resolution = f"{len(hist)} bars, 1-min × {sessions_n} sessions"
        elif median_delta_s < 3600:
            resolution = (f"{len(hist)} bars, {int(round(median_delta_s/60))}-min "
                          f"× {sessions_n} sessions")
        else:
            span_days = (hist.index[-1] - hist.index[0]).days or 1
            resolution = f"{len(hist)} bars, ~{span_days}d daily"
    else:
        resolution = f"{len(hist)} bar"

    # Up to 8 HH:MM ticks spread across the integer axis (time-of-day cue).
    if len(hist) >= 4:
        tick_pos = np.linspace(0, len(hist) - 1, min(8, len(hist))).astype(int)
        ticks = {"pos": [int(i) for i in tick_pos],
                 "labels": [hist.index[int(i)].strftime("%H:%M") for i in tick_pos]}
    else:
        ticks = {"pos": list(range(len(hist))),
                 "labels": [hist.index[i].strftime("%H:%M") for i in range(len(hist))]}

    def _f(seq):
        return [float(v) for v in seq]

    return {
        "spot": snap.spot,
        "timestamps": [ts.isoformat() for ts in hist.index],
        "x": list(range(len(hist))),
        "prices": _f(hist.values),
        "greeks": {g: _f(trace[g].values)
                   for g in ("delta", "gamma", "theta", "vega", "rho")},
        "gaps": [int(i) for i in gap_indices],
        "sessions": sessions,
        "ticks": ticks,
        "resolution": resolution,
        "lookback": {"label": spec.get("label", ""), "key": lookback or "auto"},
    }


def _fetch_replay_history(symbol, spec):
    """Fetch a price-history Series for a Replay ``spec`` (from
    ``replay_lookback_spec``) via the flexible proxy client. Intraday specs use
    ``get_intraday_history(minutes, days)``; daily specs use
    ``get_daily_history(months)`` sliced to the last ``bars`` rows. Defensive:
    returns an EMPTY Series on any failure (caller degrades to an error payload)."""
    import pandas as pd
    sc = _proxy.schwab_client
    try:
        if spec.get("freq_type") == "minute":
            df = sc.get_intraday_history(symbol, minutes=spec["minutes"], days=spec["days"])
        else:
            df = sc.get_daily_history(symbol, months=spec.get("months", 1))
        if df is None or len(df) == 0:
            return pd.Series(dtype=float)
        series = pd.Series(df["close"].values, index=pd.to_datetime(df["datetime"]))
        bars = spec.get("bars")
        if bars:
            series = series.iloc[-int(bars):]
        return series
    except Exception:
        return pd.Series(dtype=float)


def atm_iv_from_chain(chain, spot, expiry=None):
    """ATM implied vol (DECIMAL, e.g. 0.18) for ``expiry`` from a chain payload.

    Picks the contract whose strike is closest to ``spot`` and reads its
    ``volatility`` (Schwab returns a percent or a decimal — normalize to decimal).
    When ``expiry`` (YYYY-MM-DD) is given, only that expiry is considered. Falls
    back to the nearest listed expiry if the exact one has no usable vol. Returns
    None if no volatility is found. Mirrors webgui calculator.extract_atm_iv but
    returns a decimal (not a percent)."""
    if not isinstance(chain, dict) or not isinstance(spot, (int, float)):
        return None
    exp_iso = str(expiry) if expiry is not None else None

    def _scan(require_exp):
        best_diff, best = float("inf"), None
        for map_key in ("callExpDateMap", "putExpDateMap"):
            for exp_key, strikes in (chain.get(map_key) or {}).items():
                if require_exp and exp_iso and exp_key.split(":")[0] != exp_iso:
                    continue
                for strike_str, contracts in (strikes or {}).items():
                    try:
                        strike = float(strike_str)
                    except (ValueError, TypeError):
                        continue
                    if not (isinstance(contracts, list) and contracts):
                        continue
                    vol = contracts[0].get("volatility")
                    if vol is None:
                        continue
                    diff = abs(strike - spot)
                    if diff < best_diff:
                        best_diff = diff
                        best = vol if vol < 5.0 else vol / 100.0
        return best

    if exp_iso:
        exact = _scan(True)
        if exact is not None:
            return exact
    return _scan(False)


_DAY_MS = 86_400_000


def em_cone(spot, atm_iv, dte, start_ts_ms):
    """Forward expected-move cone points anchored at ``spot`` on ``start_ts_ms``.

    Returns {"upper": [[ts_ms, v], ...], "lower": [...]} with one point per
    calendar day t = 0..dte. width(t) = spot * atm_iv * sqrt(t/365). Empty dict
    values on non-positive dte or missing spot/iv (defensive — never raises)."""
    import math
    if not isinstance(spot, (int, float)) or not isinstance(atm_iv, (int, float)):
        return {"upper": [], "lower": []}
    try:
        dte = int(dte)
    except (TypeError, ValueError):
        return {"upper": [], "lower": []}
    if dte <= 0 or atm_iv < 0:
        return {"upper": [], "lower": []}
    upper, lower = [], []
    for t in range(dte + 1):
        ts = int(start_ts_ms) + t * _DAY_MS
        width = spot * atm_iv * math.sqrt(t / 365.0)
        upper.append([ts, round(spot + width, 2)])
        lower.append([ts, round(spot - width, 2)])
    return {"upper": upper, "lower": lower}


def _now_iso():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat()


_EM_HISTORY_BARS = 130  # ~6 months of trading days


def compute_expected_move(symbol, expiry, legs) -> dict:
    """Build the Expected Move payload for a symbol/expiry/legs (defensive).

    Fetches ~6mo daily candles + the option chain, derives ATM IV for ``expiry``
    and the spot, and builds the forward cone. Always returns a JSON-safe dict;
    on any failure ``error`` is set and the data fields are empty."""
    import datetime as dt

    base = {"symbol": symbol, "expiry": expiry, "spot": None, "atm_iv": None,
            "dte": None, "candles": [], "em_upper": [], "em_lower": [],
            "legs": legs or [], "generated_at": _now_iso(), "error": None}
    try:
        api = "$SPX" if (symbol or "").upper() == "SPX" else (symbol or "").upper()
        if not api:
            base["error"] = "No symbol."
            return base
        today = dt.date.today()

        cresp = _proxy.schwab_py_client.get_price_history_every_day(api)
        raw = cresp.json().get("candles", []) if getattr(cresp, "status_code", None) == 200 else []
        candles = [[int(c["datetime"]), c["open"], c["high"], c["low"], c["close"]]
                   for c in raw
                   if c.get("datetime") is not None
                   and c.get("open") is not None and c.get("high") is not None
                   and c.get("low") is not None and c.get("close") is not None]
        candles.sort(key=lambda r: r[0])
        candles = candles[-_EM_HISTORY_BARS:]
        if not candles:
            base["error"] = f"No price history for {api}."
            return base
        base["candles"] = candles

        try:
            exp_date = dt.date.fromisoformat(str(expiry))
        except Exception:
            base["error"] = f"Bad expiry: {expiry!r}."
            return base
        oresp = _proxy.schwab_py_client.get_option_chain(
            api, contract_type="ALL", from_date=today, to_date=exp_date)
        chain = oresp.json() if getattr(oresp, "status_code", None) == 200 else None

        spot = None
        q = _proxy.schwab_client.get_quote(api) or {}
        if isinstance(q, dict):
            spot = q.get("last")
        if not spot:
            spot = candles[-1][4]
        base["spot"] = spot

        atm_iv = atm_iv_from_chain(chain or {}, spot, expiry=str(expiry))
        base["atm_iv"] = atm_iv

        dte = (exp_date - today).days
        base["dte"] = dte
        if atm_iv is None:
            base["error"] = f"No ATM IV for {api} {expiry}."
            return base

        cone = em_cone(spot, atm_iv, dte, candles[-1][0])
        base["em_upper"] = cone["upper"]
        base["em_lower"] = cone["lower"]
        return base
    except Exception as exc:
        base["error"] = f"{type(exc).__name__}: {exc}"
        return base
