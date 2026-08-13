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
import copy
import datetime as _dt
import json as _json
import logging
import sys
import threading
from zoneinfo import ZoneInfo

from repo_paths import DRIVER_PAPER_DB, ENV_FLAGS, OPTIONS_SCANNER

log = logging.getLogger(__name__)

# Central time (4pm ET cash close = 15:00 CT) — shared by the forward-projection
# helpers (_future_marks_ct / project_gex_grid).
_PROJ_CT_TZ = ZoneInfo("America/Chicago")

# Regular trading hours in CT (09:30–16:00 ET). COLLECTION deliberately runs wider
# (08:00–15:20 CT, see scheduler._GEX_START / gex_due) so the pre/post-market chain is
# captured and stored; the Gamma page's time-axis charts (strike×time heatmap + the
# Flow series) DISPLAY only RTH. Off-hours snapshots are thin — the index doesn't tick
# pre-open and OI is static — so they added ~50 near-flat columns that stretched the
# session without informing it. Display-only: nothing here changes what is collected.
_RTH_START = (8, 30)
_RTH_END = (15, 0)

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import scanner_engine as se  # noqa: E402
from scanner_engine import run_full_scan, vix_regime  # noqa: E402
from regime_filter import evaluate_regime  # noqa: E402
from iv_analysis import run_iv_analysis  # noqa: E402

from services import _proxy  # noqa: E402
from services.options_svc import commission  # noqa: E402  (round-trip $ for the break-even floor)


def run_scan() -> dict:
    """Run one full scan cycle against the live proxy. Returns the engine dict.

    Thin wrapper: ``run_full_scan`` needs the schwab-py-compatible client, so we
    pass ``_proxy.schwab_py_client`` (mirrors the page). Any exception is left to
    propagate — the handler catches it (matching the sentiment compute, whose
    loaders likewise let the handler own error handling)."""
    return run_full_scan(_proxy.schwab_py_client)


# ── Day-persistent scan union ───────────────────────────────────────────────
# The Scanner table shows the DAY's signals, not just the last scan's. This is
# published to its own key (cache:options:scan_day); cache:options:scan keeps
# its live-only semantics because the autonomous driver reads it and must never
# be offered a signal that no longer qualifies.

_DAY_LISTS = ("signals_0dte", "signals_swing", "signals_directional")

# Fields stripped from DAY entries only. `gex_walls`/`dex_walls` are attached to
# every signal by scanner_engine but have ZERO consumers outside options-scanner's
# own intra-scan scoring (scoring.norm_gex_proximity / norm_dex_proximity read them
# during the scan, before anything is cached). They are ~11% of a signal's ~800 B;
# dead weight in cache:options:scan, but the day union multiplies them by the day's
# scan count. NOT stripped from cache:options:scan — the driver reads that key and
# it is deliberately out of scope here.
_DAY_STRIP = ("gex_walls", "dex_walls")

# Per-list backstop. Sized from measured numbers, not a round guess:
#   * live ceiling per list ~= 360 (45 watchlist symbols x 8 per symbol per scan:
#     pcs[:3] + ccs[:3] + ics<=2, and SINGLE_LEG_MAX_PER_SYMBOL=8 for directional),
#     so at 2000 the cap can never be the thing that drops a live signal;
#   * a measured mid-churn day lands ~1,750/list -- BELOW this cap, so the day's
#     coverage promise holds on a normal day and the cap only trims a pathological
#     one (~5,600/list). Measured through the real cache_set path, that worst case
#     serializes to 4.45 MB capped vs 14.06 MB unbounded -- and 14 MB is the scale
#     that forced the documented cache:options:gamma crop.
# The eviction is oldest-stale-first and is LOGGED (see below): a silent cap would
# read as "covered the whole day" when it didn't.
_DAY_MAX_PER_LIST = 2000


def _day_entry(signal):
    """Deep-copy a signal for the day union, minus the provably-dead fields."""
    out = copy.deepcopy(signal)
    for dead in _DAY_STRIP:
        out.pop(dead, None)
    return out


def _cap_day_list(merged, key, max_per_list):
    """Trim ``merged`` to ``max_per_list``, evicting OLDEST-STALE-FIRST. Never
    evicts a ``live`` signal: if live alone exceeds the cap, the cap yields (the
    day's live set is the feature's core promise) and the overflow is logged.

    Returns ``(kept, n_dropped)`` — the count rides out to the envelope so the
    PAGE can say the day is incomplete. A server-side log the user never sees is
    still a silent cap to them."""
    over = len(merged) - max_per_list
    if over <= 0:
        return merged, 0
    kept, dropped = [], 0
    for s in merged:                      # list order is oldest-first
        if dropped < over and not s.get("live"):
            dropped += 1
            continue
        kept.append(s)
    if len(kept) > max_per_list:
        log.warning(
            "day union %s: %d live signals exceed the %d cap — keeping them all "
            "(live is never evicted); %d stale evicted",
            key, len(kept), max_per_list, dropped)
    else:
        log.warning("day union %s: evicted %d oldest stale signal(s) at the %d cap "
                    "— the day's coverage is truncated", key, dropped, max_per_list)
    return kept, dropped


def merge_day_signals(prev, current, today, now_iso=None, max_per_list=None):
    """Merge one scan's signals into the day's accumulated union. PURE.

    ``prev``    -- the previous ``{date, signals_*}`` envelope (or None/garbage).
    ``current`` -- the fresh scan dict (the engine result / ScanResult dump).
    ``today``   -- date string 'YYYY-MM-DD'. Callers MUST pass a CT-based date
                   (``shared.notify.channels._today_ct``) so this agrees with the
                   scheduler's and push_notify's CT date bases — see ``rescan``.

    Per signal, keyed on the engine's unique ``id``:
      * present in ``current``  -> take it FRESH, ``live=True`` (still qualifying,
        and the numbers cost nothing -- the engine just computed them),
      * absent from ``current`` -> carry the last-seen copy forward FROZEN,
        ``live=False`` + ``stale_since`` stamped once.

    A ``date`` mismatch (or an unusable ``prev``) resets the day wholesale. The
    reset CONTRACT matches push_notify's seen-set; the date BASIS is the caller's
    (push_notify derives its own CT date internally, this takes one).

    CONSUMER OBLIGATION: the returned ``date`` is load-bearing for READERS, not
    just for the reset. ``rescan`` deliberately leaves a stale envelope in place
    when the merge fails (writing an empty one would destroy the day's data), so
    on a failure at the first scan of a new day the key still holds YESTERDAY's
    envelope -- including ``live=True`` entries. A consumer MUST gate on ``date``
    before trusting ``live``, or it will render day-old signals as live.

    ``truncated`` -- ``{list_name: n_dropped}``, present ONLY when the cap evicted
    something (absent = nothing dropped, and also what a pre-cap envelope looks
    like; both render the same, so absence is unambiguously "no notice"). The page
    SHOULD surface it: the cap does not bind on a calm or mid-churn day, so when
    it fires it fires on a volatile one -- exactly the day a trader is watching --
    and without this the day looks complete when it isn't.

    Signals with no ``id`` are dropped (they cannot be tracked across scans).
    Never mutates its inputs; never raises.
    """
    max_per_list = _DAY_MAX_PER_LIST if max_per_list is None else max_per_list
    now_iso = now_iso or _dt.datetime.now().isoformat(timespec="seconds")

    if not isinstance(prev, dict) or prev.get("date") != today:
        prev = {}

    out = {"date": today}
    truncated = {}
    for key in _DAY_LISTS:
        cur_list = current.get(key) if isinstance(current, dict) else None
        cur_list = cur_list if isinstance(cur_list, list) else []
        cur_by_id = {s["id"]: s for s in cur_list
                     if isinstance(s, dict) and s.get("id")}

        prev_list = prev.get(key)
        prev_list = prev_list if isinstance(prev_list, list) else []

        merged = []
        seen = set()
        # Carried-forward first (stable order: oldest first, newcomers appended).
        for s in prev_list:
            if not isinstance(s, dict) or not s.get("id") or s["id"] in seen:
                continue
            sid = s["id"]
            seen.add(sid)
            if sid in cur_by_id:
                fresh = _day_entry(cur_by_id[sid])
                fresh["live"] = True
                fresh["stale_since"] = None
                merged.append(fresh)
            else:
                kept = _day_entry(s)
                kept["live"] = False
                if not kept.get("stale_since"):
                    kept["stale_since"] = now_iso
                merged.append(kept)
        for sid, s in cur_by_id.items():
            if sid in seen:
                continue
            fresh = _day_entry(s)
            fresh["live"] = True
            fresh["stale_since"] = None
            merged.append(fresh)
        out[key], dropped = _cap_day_list(merged, key, max_per_list)
        if dropped:
            truncated[key] = dropped
    if truncated:
        out["truncated"] = truncated
    return out


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


# Phase-1 candidate families. ``families=None`` ⇒ build all of these.
_SWING_FAMILIES = ("DIRECTIONAL", "VERTICAL", "NEUTRAL")

# Emission cut for the Strategy Finder: a candidate must reach SWING_MIN_SCORE on
# strategy_scoring's Fit+Quality composite AND not carry an excluded grade, or it
# is dropped before the page ever sees it.
#
# Applies to EVERY family (directional, debit + adapted credit verticals, iron
# condors) because they land in ONE jointly-ranked table -- cutting only one
# family would leave Weak iron condors ranked above directional rows that were
# removed. The dropped count rides back on the result as ``filtered_out`` so an
# empty table can say WHY (see the page's ``status_text``).
#
# Deliberately SEPARATE constants from scanner_engine.SINGLE_LEG_MIN_SCORE /
# SINGLE_LEG_EXCLUDED_GRADES, which cut the Market Scanner's Directional tab:
# same values today, but two pages with two independently tunable bars.
#
# NOTE the two cuts here are redundant TODAY: a hard-gate failure pins the
# composite at strategy_scoring.GATE_FAIL_CAP (39) and the post-grade state tilt
# adds at most +6, so a Weak candidate tops out at 45 and can never clear 50.
# Both are kept because they express DIFFERENT intents ("no low-scoring trades"
# vs "no gate-failing trades") and either constant can move independently.
SWING_MIN_SCORE = 50.0
SWING_EXCLUDED_GRADES = ("Weak",)


def _passes_swing_cut(sig):
    """True when a scored candidate is good enough to publish. Pure."""
    return ((sig.get("composite_score") or 0) >= SWING_MIN_SCORE
            and sig.get("grade") not in SWING_EXCLUDED_GRADES)


def swing_scan(symbol, dte_min, dte_max, put_d_min, put_d_max,
               call_d_min, call_d_max, min_cr_fraction, families=None,
               market_state=None) -> dict:
    """Run the multi-strategy swing scan pipeline; returns ``{"signals", "view"}``.

    The pipeline builds NORMALIZED candidates across families
    (``strategy_scanner``, Unit A) and scores them on a unified 0-100 Fit+Quality
    scale (``strategy_scoring``, Unit B) against a market view inferred from the
    symbol's technicals + IV regime:

      1. fetch chain / quote / spot / history / technicals / IV analysis (the daily
         expected move feeds breakeven-vs-EM scoring),
      2. derive ``atm_iv`` (decimal) from the engine's authoritative dollar daily EM
         (``dem = spot·iv·√(1/365)`` ⇒ invert to a fraction; this dodges the
         percent/decimal trap — ``run_iv_analysis``'s ``current_iv`` is a PERCENT),
      3. infer the market view,
      4. build candidates by family — DIRECTIONAL (long/short calls & puts) and
         VERTICAL (debit verticals + adapted PCS/CCS credit spreads) and NEUTRAL
         (iron condors). Credit spreads feed BOTH the VERTICAL credit set AND the
         NEUTRAL iron condors, so ``screen_spreads`` runs whenever EITHER family is
         requested,
      5. score + rank + assign ids.

    ``families`` (default all Phase-1 families) restricts which candidate families
    are built. ``market_state`` (optional) is the live committed five-state
    classifier label (bullish / lack_of_bullishness / neutral / lack_of_bearishness
    / bearish); it is threaded to ``score_all`` for the low-weight family-ranking
    tilt. ``None`` (absent/bad composite) applies no tilt. The caller (the ``swing``
    command handler) reads it from ``cache:sentiment:composite`` — compute stays
    proxy-only. Two-client usage mirrors the page: ``_proxy.schwab_py_client`` is
    the schwab-py-compatible client passed into the engine calls, while
    ``_proxy.schwab_client.get_quote(symbol)`` fetches the quote.
    ``min_cr_fraction`` arrives already as a fraction.

    ``strategy_scanner`` / ``strategy_scoring`` are imported lazily here (not at
    module top) to avoid binding the process-wide ``sys.modules`` entries merely by
    importing this module — the same rationale the old ``import scoring`` carried
    (``strategy_scoring`` itself lazy-imports options-scanner's ``scoring`` for the
    liquidity normalizer). In the real (process-isolated) service these resolve
    unambiguously to options-scanner's modules.
    """
    import datetime as dt
    import math

    import strategy_scanner as ssn
    import strategy_scoring as ssc

    client = _proxy.schwab_py_client

    today = dt.date.today()
    chain = se.fetch_option_chain(client, symbol, from_date=today,
                                  to_date=today + dt.timedelta(days=dte_max + 2))
    # Off-hours/weekend the chain fetch can return None; the candidate builders
    # below would AttributeError on chain.get(...)/extract_options(None). Degrade
    # to an explicit empty result so the handler still publishes a fresh view.
    if not chain:
        return {"signals": [], "view": {}, "filtered_out": 0}
    quote = _proxy.schwab_client.get_quote(symbol) or {}
    spot = quote.get("last") or chain.get("underlyingPrice")
    # Off-hours the quote can miss AND the chain dict can lack ``underlyingPrice``
    # (the engine defaults that key to 0; compute uses a bare .get()), leaving
    # spot None. The candidate builders price off spot (spot*0.20, spot*atm_iv),
    # so a None spot would TypeError and the scaffold would swallow it -> NO cache
    # write -> the page hangs on "Scanning…". Degrade to an explicit empty result
    # (matching the no-chain guard above) BEFORE any builder runs.
    if not spot:
        return {"signals": [], "view": {}, "filtered_out": 0}
    hist = se.fetch_price_history(client, symbol)
    tech = se.calc_technicals(hist) if hist is not None else {}
    iv = run_iv_analysis(client, symbol, price=spot, hist=hist, chain=chain) or {}
    dem = ((iv.get("expected_moves") or {}).get("daily") or {}).get("move_dollars")

    # ATM IV (DECIMAL fraction) from the engine's authoritative dollar daily EM —
    # avoids the percent/decimal trap (``dem = spot·iv_dec·√(1/365)``).
    atm_iv = None
    if dem and spot and spot > 0:
        atm_iv = (dem * math.sqrt(365.0)) / spot
    if not atm_iv:
        civ = (iv or {}).get("current_iv")
        atm_iv = (civ / 100.0) if (civ and civ > 1.5) else (civ or 0.20)

    # 1-sigma dollar move to the front of the trade horizon (breakeven-vs-EM factor).
    em_1sd = (dem or 0.0) * math.sqrt(max(dte_min, 1))

    view = ssc.infer_market_view(tech or {}, iv or {})

    fams = set(families) if families else set(_SWING_FAMILIES)
    signals = []
    if "DIRECTIONAL" in fams:
        signals += ssn.build_directional(chain, symbol, spot, atm_iv, dte_min, dte_max)

    # Credit spreads feed BOTH the VERTICAL credit set AND the NEUTRAL iron condors,
    # so compute screen_spreads if EITHER family is requested.
    spreads = []
    if {"VERTICAL", "NEUTRAL"} & fams:
        spreads = list(se.screen_spreads(chain, symbol, dte_min, dte_max, put_d_min,
                                         put_d_max, call_d_min, call_d_max,
                                         min_cr_fraction, "SWING", spot=spot,
                                         daily_expected_move=dem))
    if "VERTICAL" in fams:
        signals += ssn.build_debit_verticals(chain, symbol, spot, atm_iv, dte_min, dte_max)
        signals += [ssn.adapt_credit_spread(s) for s in spreads]
    if "NEUTRAL" in fams:
        signals += [ssn.adapt_iron_condor(ic) for ic in se.build_iron_condors(spreads)]

    signals = ssc.score_all(signals, view, atm_iv, em_1sd, market_state=market_state)

    # Quality cut, BEFORE ids are assigned so every emitted row is addressable
    # and the detail-panel lookup can't miss one.
    scored_n = len(signals)
    signals = [s for s in signals if _passes_swing_cut(s)]
    filtered_out = scored_n - len(signals)

    assign_ids(signals, symbol)
    # Surface the symbol's IV Rank onto every candidate for the table's IV Rank
    # column. Single-symbol scan, so all candidates share the one value; None when
    # the IV analysis couldn't compute a rank.
    iv_rank = iv.get("iv_rank")
    for s in signals:
        s["iv_rank"] = iv_rank
    return {"signals": signals, "view": view, "filtered_out": filtered_out}


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


# ── Isolated driver paper account (DRIVER_PAPER_DB) ──────────────────────────
# The autonomous Driver trades into a SEPARATE DB file so its book, P&L, and
# $500/halt logic are fully isolated from the user's manual paper account. The
# paper_engine/paper_account_db machinery is already db_path-parameterized, so
# these wrappers just thread ``DRIVER_PAPER_DB`` (kept as a module global so the
# tests can monkeypatch it onto a tmp DB without touching the real account).


def ensure_driver_account(starting_balance: float = 25000.0) -> None:
    """Seed the dedicated driver paper account if absent (idempotent). Must run
    before the first open/manage — the engine indexes get_account()['halted']."""
    import datetime as dt

    import paper_account_db

    paper_account_db.ensure_account(DRIVER_PAPER_DB, starting_balance=starting_balance,
                                    session_date=dt.date.today().isoformat())


def has_driver_account() -> bool:
    """True if the driver account row has been seeded (False on any failure)."""
    import paper_account_db

    try:
        return paper_account_db.get_account(DRIVER_PAPER_DB) is not None
    except Exception:
        return False


def driver_shared_reads():
    """One read of the driver book's FULL positions + account snapshot, shared by
    ``driver_account_view`` / ``driver_account_perf`` / ``driver_analytics`` so the
    5-min refresh reads the (tiny) DB ONCE instead of three times. Defensive →
    ``([], None)`` on failure."""
    import paper_account_db
    import paper_engine

    try:
        positions = paper_account_db.fetch_all_positions(DRIVER_PAPER_DB)
    except Exception:
        positions = []
    try:
        snapshot = paper_engine.account_snapshot(DRIVER_PAPER_DB)
    except Exception:
        snapshot = None
    return positions, snapshot


def driver_account_view(all_positions=None, snapshot=None) -> dict:
    """Driver account snapshot + open positions (mirrors ``paper_account_view`` on
    the DRIVER db). No rescue overlay (that reads the manual account). Each
    sub-read is defensively guarded so a partial failure still returns a view.

    ``all_positions``/``snapshot`` — pass the shared driver_shared_reads() result
    to avoid re-fetching them (the 5-min refresh injects both); when omitted they
    are fetched here so standalone callers still work."""
    import paper_account_db
    import paper_engine

    if snapshot is None:
        try:
            snapshot = paper_engine.account_snapshot(DRIVER_PAPER_DB)
        except Exception:
            snapshot = None
    try:
        positions = paper_account_db.fetch_open_positions(DRIVER_PAPER_DB)
    except Exception:
        positions = []
    try:
        orders = paper_account_db.fetch_orders(DRIVER_PAPER_DB, limit=100, status="FILLED")
    except Exception:
        orders = []
    try:
        if all_positions is None:
            all_positions = paper_account_db.fetch_all_positions(DRIVER_PAPER_DB)
        closed_positions = [p for p in all_positions
                            if (p.get("status") or "").upper() != "OPEN"]
    except Exception:
        closed_positions = []
    return {"snapshot": snapshot, "positions": positions, "orders": orders,
            "closed_positions": closed_positions, "has_account": has_driver_account()}


def driver_account_perf(positions=None, snapshot=None) -> dict:
    """Performance scorecard over the driver account (driver_perf.build_scorecard).
    Defensive → an empty scorecard on any failure. ``positions``/``snapshot`` may
    be injected (see driver_shared_reads) to avoid a re-fetch."""
    import paper_account_db
    import paper_engine

    from services.options_svc import driver_perf

    if positions is None:
        try:
            positions = paper_account_db.fetch_all_positions(DRIVER_PAPER_DB)
        except Exception:
            positions = []
    if snapshot is None:
        try:
            snapshot = paper_engine.account_snapshot(DRIVER_PAPER_DB)
        except Exception:
            snapshot = {}
    return driver_perf.build_scorecard(positions, snapshot or {})


def _book_analytics(db_path, *, starting_balance=25000.0, positions=None) -> dict:
    """Performance analytics over ANY paper book — equity curve + posture post-mortem +
    MAE/MFE excursions (``perf_analytics.build_analytics``). Reads the book's full position
    history. The equity baseline is the fixed account seed ($25k — both books are seeded
    there); the curve is realized equity from that base. Defensive → an empty-shaped
    payload on any failure. ``positions`` may be injected to avoid a re-fetch."""
    import paper_account_db

    from services.options_svc import perf_analytics

    if positions is None:
        try:
            positions = paper_account_db.fetch_all_positions(db_path)
        except Exception:
            positions = []
    return perf_analytics.build_analytics(positions, starting_balance=starting_balance)


def driver_analytics(positions=None) -> dict:
    """Performance analytics over the DRIVER account (see ``_book_analytics``).
    ``positions`` may be injected (see driver_shared_reads) to avoid a re-fetch."""
    return _book_analytics(DRIVER_PAPER_DB, positions=positions)


def manual_analytics() -> dict:
    """Performance analytics over the MANUAL paper account (default DB). The scanner-
    baseline book: it auto-trades every captured signal, so its equity curve / MAE-MFE
    are the benchmark to compare the driver (Claude's selection) against. The posture
    post-mortem is naturally empty here (manual opens carry no entry_context)."""
    return _book_analytics(None)


def _eod_book_summary(snapshot, all_positions, *, has_account, today, label) -> dict:
    """PURE per-book end-of-day stats from an account snapshot + full positions list.

    ``day_pnl``/``equity``/``open_count``/``halted`` are read straight off the snapshot
    (``session_pnl`` already = session realized + open unrealized, resetting each
    session). The closed-TODAY win/loss/realized are computed from positions whose
    ``exit_ts`` date matches ``today`` and whose status is CLOSED/EXPIRED. Tolerates
    sparse/None rows — never raises.
    """
    snap = snapshot or {}
    closed_today = []
    for p in all_positions or []:
        if not isinstance(p, dict):
            continue
        if (p.get("status") or "").upper() not in ("CLOSED", "EXPIRED"):
            continue
        if str(p.get("exit_ts") or "")[:10] != today:
            continue
        closed_today.append(p)
    realized = [r for r in (p.get("realized_pnl") for p in closed_today)
                if isinstance(r, (int, float))]
    wins = [r for r in realized if r > 0]
    losses = [r for r in realized if r < 0]
    _num = lambda v: v if isinstance(v, (int, float)) else None
    return {
        "label": label,
        "has_account": bool(has_account),
        "day_pnl": _num(snap.get("session_pnl")),
        "equity": _num(snap.get("equity")),
        "open_count": snap.get("open_count") if isinstance(snap.get("open_count"), int) else 0,
        "halted": bool(snap.get("halted")),
        "closed_today": len(closed_today),
        "wins": len(wins),
        "losses": len(losses),
        "realized_today": round(sum(realized), 2) if realized else 0.0,
    }


def collect_eod_summary(now_ct=None) -> dict:
    """Assemble the end-of-day per-book P&L summary for the scheduled post-close push.

    Reports the two ENGINE paper books the auto-manage cycles actually trade — the
    user's MANUAL account (default DB) and the isolated DRIVER account
    (``DRIVER_PAPER_DB``) — each via ``_eod_book_summary``. Book state is read AS-IS at
    call time (no manage cycle is forced first), so a 0-DTE that expired but hasn't yet
    been settled still contributes its unrealized to ``day_pnl``. Defensive: a per-book
    read failure yields that book's empty (no-account) summary; never raises. ``now_ct``
    defaults to the live CT clock; inject for deterministic tests.
    """
    import datetime as _dt
    from zoneinfo import ZoneInfo

    import paper_account_db
    import paper_engine

    now_ct = now_ct or _dt.datetime.now(ZoneInfo("America/Chicago"))
    today = now_ct.date().isoformat()

    def _book(db_path, label):
        try:
            snap = paper_engine.account_snapshot(db_path)
        except Exception:
            snap = None
        try:
            positions = paper_account_db.fetch_all_positions(db_path)
        except Exception:
            positions = []
        try:
            has = paper_account_db.get_account(db_path) is not None
        except Exception:
            has = False
        return _eod_book_summary(snap, positions, has_account=has, today=today, label=label)

    return {
        "date": today,
        "books": {"manual": _book(None, "Manual"),
                  "driver": _book(DRIVER_PAPER_DB, "Driver")},
        "generated_at": now_ct.isoformat(),
    }


# The driver account's per-trade risk cap for the PAPER SIZER on the open path.
# Kept SEPARATE from the manual account's ``config_paper.MAX_RISK_PER_TRADE`` ($250)
# so raising the driver's cap never changes the user's manual paper trades. Must match
# ``driver_svc.settings.PER_TRADE_MAX_RISK`` (the guardrail's per-trade cap) so the
# driver can't approve a qty the sizer then zeroes to RISK_TOO_HIGH. Raised to fund
# liquid index/large-cap spreads ($SPX ~$700-1,150/contract, MU ~$400) that a $250
# cap sized to 0 — the reason $SPX/MU picks logged "Executed" but never opened.
_DRIVER_MAX_RISK_PER_TRADE = 3000.0


def open_driver_position(signal: dict, qty: int, broker=None, context=None) -> dict:
    """Open ONE driver position into ``DRIVER_PAPER_DB`` at ``min(clamped qty,
    fill-sized)``.

    ``context`` (optional) is the decision context at open — the directional posture,
    the market_read summary, and whether the shadow gate would have blocked this trade —
    stamped onto the position's ``entry_context`` (JSON) so a later post-mortem can
    correlate the entry regime to the realized outcome. A ``None``/non-dict context stores
    NULL (exactly as before).

    Adapts the per-signal open block of ``paper_engine.run_entry_cycle``
    (size → submit → **re-size off the actual fill** → guard → reserve BP →
    insert) for a single guardrail-approved signal, threading ``DRIVER_PAPER_DB``.

    Qty reconciliation (load-bearing): the driver brings a guardrail-CLAMPED
    ``qty``; the engine independently re-sizes off the ACTUAL fill credit
    (``size_contracts(fill, width)``). We open at ``min(int(qty), sized)`` — the
    clamp is a CEILING the engine can only size *down* from, never up.

    Returns ``{"status": "opened"|"rejected"|"error", ...}``. NEVER raises — the
    whole body is guarded so a bad signal/broker degrades to an error result.
    The order row is recorded via ``paper_engine._record_order`` (the same path
    the manual entry cycle uses) so ``entry_order_id`` links to the position.
    """
    import datetime as dt

    import config_paper
    import paper_account_db
    import paper_broker
    import paper_engine
    import paper_sizing

    broker = broker or paper_broker            # module exposes submit_order(order, client)
    try:
        # Normalize the signal shape. The driver feeds RAW scanner signals
        # (cache:options:scan), which key structure under ``type``, credit under
        # ``credit``, and id under ``id`` — but this engine path (lifted from the
        # captured-DB entry cycle) reads ``strategy``/``entry_credit``/``signal_id``.
        # Without this map every driver open KeyError'd on 'signal_id' and silently
        # degraded to status=error, so NOTHING ever landed in the driver account
        # (the decision log showed "executed" — only the ENQUEUE — but no position).
        signal = dict(signal)
        signal.setdefault("signal_id", signal.get("id"))
        signal.setdefault("strategy", signal.get("type"))
        signal.setdefault("entry_credit", signal.get("credit"))
        signal.setdefault("dte_at_entry", signal.get("dte", 0))
        ensure_driver_account()
        # Clear a STALE (prior-day) drawdown halt before checking it: a new session
        # un-halts + resets the daily counters. Idempotent (no-op if already today),
        # and a SAME-day halt (banked $500 / hit the loss cap today) is preserved.
        # Matters only for a manual open before the 5-min manage tick rolls the session.
        paper_account_db.roll_session_if_needed(DRIVER_PAPER_DB, dt.date.today().isoformat())
        if paper_account_db.get_account(DRIVER_PAPER_DB)["halted"]:
            return {"status": "rejected", "reason": "halted"}
        q = int(qty)   # the guardrail-clamped request (a CEILING — see the re-size below)
        order = {"signal_id": signal["signal_id"], "symbol": signal["symbol"],
                 "side": "SELL_TO_OPEN", "strategy": signal["strategy"],
                 "short_strike": signal["short_strike"], "long_strike": signal["long_strike"],
                 "call_short": signal.get("call_short"), "call_long": signal.get("call_long"),
                 "expiration": signal["expiration"], "quantity": q,
                 "limit_price": signal["entry_credit"], "legs": []}
        resp = broker.submit_order(order, _proxy.schwab_py_client)
        if resp.get("status") != "FILLED":
            return {"status": "rejected", "reason": resp.get("status")}
        fill = resp["price"]
        # Reject garbage opening-auction fills (a real credit spread never fills
        # at a near-zero / negative net credit).
        if fill < config_paper.MIN_FILL_CREDIT:
            return {"status": "rejected", "reason": "LOW_CREDIT"}
        # Re-size on the ACTUAL fill credit (keeps realized risk within the cap).
        sized, max_loss_per = paper_sizing.size_contracts(
            fill, signal["width"], max_risk=_DRIVER_MAX_RISK_PER_TRADE)  # driver cap, not manual $250
        open_qty = min(q, sized)               # the guardrail clamp is a CEILING
        if max_loss_per <= 0 or open_qty < 1:
            return {"status": "rejected", "reason": "RISK_TOO_HIGH"}
        max_loss_total = round(max_loss_per * open_qty, 2)
        if max_loss_total > paper_account_db.get_account(DRIVER_PAPER_DB)["cash"]:
            return {"status": "rejected", "reason": "INSUFFICIENT_BUYING_POWER"}
        order["quantity"] = open_qty           # persist the re-sized qty actually opened
        oid = paper_engine._record_order(DRIVER_PAPER_DB, order, resp)
        paper_account_db.reserve_buying_power(DRIVER_PAPER_DB, max_loss_total)
        paper_account_db.insert_position(DRIVER_PAPER_DB, {
            "signal_id": signal["signal_id"], "symbol": signal["symbol"],
            "strategy": signal["strategy"], "short_strike": signal["short_strike"],
            "long_strike": signal["long_strike"], "call_short": signal.get("call_short"),
            "call_long": signal.get("call_long"), "width": signal["width"],
            "expiration": signal["expiration"], "dte_at_entry": signal.get("dte_at_entry", 0),
            "quantity": open_qty, "entry_credit": fill, "entry_order_id": oid,
            "max_loss_per": max_loss_per, "max_loss_total": max_loss_total,
            "entry_ts": resp["enteredTime"],
            "entry_context": _json.dumps(context) if isinstance(context, dict) else None})
        return {"status": "opened", "symbol": signal["symbol"], "qty": open_qty,
                "entry_credit": fill, "max_loss_total": max_loss_total}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}


def run_driver_manage_cycle() -> None:
    """Reprice + auto-close the DRIVER account's open positions
    (``paper_engine.run_manage_cycle`` on ``DRIVER_PAPER_DB``). No-op-safe if the
    driver account doesn't exist yet (gated on ``has_driver_account``).

    The driver's isolated book is intentionally EXCLUDED from the manual paper
    account's opt-in break-even lifecycle — ``lifecycle=False`` is passed
    explicitly (never reads ``handlers.manual_paper_lifecycle_enabled``, the
    Settings toggle), so the driver always keeps today's plain TAKE_PROFIT-at-
    +50% regardless of what the manual account is configured to do."""
    import datetime as dt

    import paper_engine

    if not has_driver_account():
        return
    try:
        paper_engine.run_manage_cycle(_proxy.schwab_py_client, dt.date.today().isoformat(),
                                      db_path=DRIVER_PAPER_DB, lifecycle=False)
    except Exception:  # noqa: BLE001 — a reprice/proxy failure must not propagate out of
        # the wrapper (the 5-min tick retries; matches the driver wrappers).
        log.exception("driver manage cycle degraded (retries on next tick)")


def run_entry_cycle() -> None:
    """Run the paper auto-entry cycle: scan open captured signals, open positions.

    No ``options_scoring()`` guard (process-isolated; the lazy ``import scoring``
    happens inside ``paper_engine``). Mirrors the page's entry branch."""
    import datetime as dt

    import paper_engine
    import signal_db

    signals = signal_db.get_open_signals_with_latest_mark()
    paper_engine.run_entry_cycle(_proxy.schwab_py_client, dt.date.today().isoformat(), signals)


def _manual_paper_be_level(pos: dict) -> float:
    """Break-even close floor ($) for a MANUAL-paper lifecycle position = round-trip
    commissions for its structure (mirrors ``_captured_be_level``, the captured-
    signal analog). Defensive → 0.0 on any failure (break-even stop at pnl<=$0)."""
    try:
        strat = pos.get("strategy") or pos.get("type")
        return commission.round_trip_commission(strat, pos.get("symbol"), 1)
    except Exception:
        return 0.0


def run_manage_cycle(lifecycle: bool = False) -> None:
    """Run the paper auto-management cycle: reprice + auto-close hits. Mirrors the page.

    ``lifecycle`` (default False — unchanged plain TAKE_PROFIT-at-+50%) opts the
    MANUAL paper account into the captured-style break-even lifecycle; see
    ``paper_engine.run_manage_cycle``. Threaded from
    ``handlers.run_manage_and_refresh`` via ``handlers.manual_paper_lifecycle_enabled``
    (the Settings toggle, default OFF). When enabled, ``_manual_paper_be_level``
    supplies the commission-based break-even close floor — the same model the
    captured-signal cycle uses (``compute._captured_be_level``)."""
    import datetime as dt

    import paper_engine

    paper_engine.run_manage_cycle(
        _proxy.schwab_py_client, dt.date.today().isoformat(),
        lifecycle=lifecycle,
        be_level_fn=_manual_paper_be_level if lifecycle else None)


def reset_paper_account(starting_balance: float) -> None:
    """Reset the paper account to ``starting_balance``. Mirrors the page's reset."""
    import paper_account_db

    paper_account_db.reset_account(starting_balance=starting_balance)


def has_paper_account() -> bool:
    """True if a paper account exists (entry/manage short-circuit on False)."""
    import paper_account_db

    return paper_account_db.get_account() is not None


def reconcile_paper_buying_power() -> dict:
    """R6: reconcile ``buying_power_reserved`` against open positions for BOTH the
    manual paper account (default DB) AND the isolated driver account
    (``DRIVER_PAPER_DB``).

    Opening a position is a non-atomic 3-commit sequence (record_order →
    reserve_buying_power → insert_position); a crash between the reserve and the
    insert orphans reserved BP against no position, which corrupts the driver's
    halt/loss-cap math. Called once at service startup to self-heal any drift left
    by a prior crash. Idempotent + defensive — ``reconcile_buying_power`` never
    raises and is a no-op when the book is consistent. Returns the per-book
    corrected drift (0.0 = clean / absent) for logging/observability."""
    import paper_account_db

    out = {"manual": 0.0, "driver": 0.0}
    try:
        out["manual"] = paper_account_db.reconcile_buying_power(None)  # default DB
    except Exception:  # noqa: BLE001 — startup self-heal must never crash the loop.
        log.exception("manual BP reconcile degraded")
    try:
        out["driver"] = paper_account_db.reconcile_buying_power(DRIVER_PAPER_DB)
    except Exception:  # noqa: BLE001
        log.exception("driver BP reconcile degraded")
    return out


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


def _reprice_open_pnl(trades) -> None:
    """Attach a live ``unrealized_pnl`` (total $, = per-spread × qty) to each OPEN
    trade IN PLACE via ``signal_repricer.reprice_swing``.

    Ledger trades carry exactly the fields ``reprice_swing`` reads
    (strategy/short_strike/long_strike/entry_credit/call_short/call_long/symbol/
    expiration), and it shares a per-(symbol, expiration) chain cache so trades on
    the same chain reuse one fetch. Fully defensive per-trade — a reprice failure
    leaves that trade's P&L blank, never raises. Caller gates on market hours so
    off-hours (no live chain) we skip the proxy churn entirely."""
    import signal_repricer

    try:
        signal_repricer.clear_chain_cache()   # fresh marks each publish
    except Exception:
        log.exception("clear_chain_cache before reprice degraded")
    for t in trades or []:
        if (t.get("status") or "").upper() != "OPEN":
            continue
        try:
            # DEBIT/legs trades (long options + debit verticals) reprice generically off
            # their stored legs; credit spreads keep the tested short/long-strike path.
            if t.get("direction") == "DEBIT":
                rep = signal_repricer.reprice_legs(t, _proxy.schwab_py_client)
            else:
                rep = signal_repricer.reprice_swing(t, _proxy.schwab_py_client)
        except Exception:
            continue
        per = (rep or {}).get("unrealized_pnl")     # per-spread $ P&L
        if per is None:
            continue
        try:
            t["unrealized_pnl"] = round(per * int(t.get("quantity") or 1), 2)
        except (TypeError, ValueError):
            continue


def paper_trades_view(reprice: bool = False) -> dict:
    """Read the paper-trade ledger view: ``{"trades": [...]}``.

    With ``reprice=True`` (and only during market hours), each OPEN trade gets a
    live ``unrealized_pnl`` so the Paper Trades page can show running P&L instead
    of a blank column. Defensively guarded → ``{"trades": []}`` on any failure,
    mirroring the page's per-read try/except. The GUI tier reads this cached view
    directly."""
    import paper_trader

    try:
        trades = paper_trader.get_all_trades()
    except Exception:
        log.exception("paper_trades_view read degraded → empty ledger")
        return {"trades": []}
    if reprice:
        try:
            from . import scheduler
            market_open = scheduler._is_trading_day(scheduler._market_now()) \
                and scheduler._is_market_hours(scheduler._market_now())
        except Exception:
            market_open = True   # if the gate can't be evaluated, attempt it
        if market_open:
            try:
                _reprice_open_pnl(trades)
            except Exception:
                log.exception("paper_trades reprice degraded (P&L left blank)")
    return {"trades": trades}


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


def expire_ledger_trades(now_ct=None) -> int:
    """Auto-settle expired OPEN ledger trades (``trades.db``) at intrinsic value.

    The Paper Trades ledger otherwise NEVER auto-closes on expiration
    (``paper_trader.expire_paper_trade`` had no caller), so expired trades linger
    OPEN indefinitely. This mirrors the account engine's expiration settlement for
    the ledger: for each OPEN trade whose expiration is past — or is today and the
    CT clock is at/after 15:00 (the shared ``paper_engine.should_settle`` gate, so
    a 0-DTE spread is held to the close, not settled at the open) — settle at
    intrinsic value vs a directly-fetched underlying and persist the EXPIRED row.

    Defensive per-trade (a bad trade never aborts the pass); returns the count
    settled. Runs on the 5-min manage tick + the manual "Run manage cycle" button.
    ``now_ct`` defaults to the live CT clock; inject it for deterministic tests."""
    import datetime as _dt
    from zoneinfo import ZoneInfo

    import paper_engine
    import paper_trader

    now_ct = now_ct or _dt.datetime.now(ZoneInfo("America/Chicago"))
    today = now_ct.date().isoformat()
    try:
        trades = paper_trader.get_all_trades()
    except Exception:
        log.exception("expire_ledger_trades read degraded → no settlement")
        return 0

    settled = 0
    for t in trades:
        if (t.get("status") or "").upper() != "OPEN":
            continue
        if not paper_engine.should_settle(t.get("expiration"), today, now_ct):
            continue
        sp = paper_engine.underlying_last(_proxy.schwab_py_client, t.get("symbol"))
        if sp is None:
            log.warning("ledger EXPIRY DEFERRED %s: no underlying quote", t.get("symbol"))
            continue
        try:
            closed = paper_trader.expire_paper_trade(dict(t), sp)
            paper_trader.update_trade(t["trade_id"], closed)
            settled += 1
        except Exception:
            log.exception("expire_ledger_trades: settle failed for %s", t.get("trade_id"))
            continue
    return settled


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
    verdict = (result or {}).get("verdict") or {}
    pos = (result or {}).get("position") or {}
    ptarget = (result or {}).get("profit_target") or {}
    return {
        "trade_id": trade_id,
        "symbol": t.get("symbol"),
        "action": verdict.get("action", "—"),
        # Descriptive fields for the Analyze popup (additive; the page falls back
        # gracefully when they're absent).
        "rationale": verdict.get("rationale"),
        "metrics": {
            "unrealized_pnl": pos.get("unrealized_pnl"),
            "unrealized_pnl_pct": pos.get("unrealized_pnl_pct"),
            "underlying_now": pos.get("underlying_now"),
            "dte_remaining": pos.get("dte_remaining"),
            "target_pct": ptarget.get("target_pct"),
            "breakeven": ptarget.get("breakeven"),
        },
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
        log.exception("captured_view read degraded → empty signals")
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

    # Fresh marks each run: clear the repricer's per-(symbol,expiration) chain
    # cache so captured-signal marks (+ the 3x/day action-alert reprice that
    # reuses this path) aren't priced off another caller's minutes-old chains.
    try:
        signal_repricer.clear_chain_cache()
    except Exception:
        log.exception("clear_chain_cache before reprice_captured degraded")

    try:
        sigs = signal_db.get_open_signals_with_latest_mark()
    except Exception:
        log.exception("reprice_captured signal read degraded → empty set")
        sigs = []

    now = dt.datetime.now(dt.timezone.utc)
    flags = []
    for r in sigs:
        try:
            rep = signal_repricer.reprice_swing(r, _proxy.schwab_py_client)
            mark = signal_recommender.build_mark(r, rep, now,
                                                 be_level=_captured_be_level(r))
        except Exception:
            continue
        if not mark:
            continue
        # Merge the mark's display fields into the row (mirrors the page; not
        # persisted — the GUI reads these off the cached repriced list).
        r["current_value"] = mark.get("current_value")   # current option price (spread mark)
        r["unrealized_pnl"] = mark.get("unrealized_pnl")
        r["current_score"] = mark.get("current_score")
        r["score_drift"] = mark.get("score_drift")
        if mark.get("recommendation") is not None:
            r["recommendation"] = mark.get("recommendation")
        code = (mark.get("recommendation_code") or "").upper()
        if code in _STOP_CODES:
            flags.append({"symbol": r.get("symbol"), "code": code})
        # Attach a rescue assessment so captured CUT signals can surface on the
        # Rescue board. Fully defensive: any failure leaves the row untagged and
        # never breaks the reprice loop.
        try:
            _attach_rescue_assessment(r, rep, mark)
        except Exception:
            log.exception("rescue assessment for captured signal degraded (row untagged)")
    return {"signals": sigs, "flags": flags}


# Loss-stop codes that mark a captured signal as a genuine CUT (a TARGET_HIT is a
# winner → TAKE_PROFIT, NOT a CUT). Kept separate from ``_STOP_CODES`` (which
# includes TARGET_HIT for the flag list above).
_LOSS_STOP_CODES = ("MONEY_STOP", "DELTA_STOP", "TIME_STOP")
_CUT_HEAT_FLOOR = 60.0


def _attach_rescue_assessment(r, rep, mark) -> None:
    """Tag a captured-signal row with ``rescue_state`` + ``heat`` (in place).

    Runs the pure rescue engine over an engine-mark built from the reprice +
    the signal's static fields, then **escalates** a CUT (any of the three loss
    stops) to at least ``tested`` with a sensible heat floor so a CUT always
    lands on the Rescue board regardless of borderline heat math. Captured
    signals carry their type in ``strategy`` (PCS/CCS/IC); fall back to ``type``
    if that's all the row has. Caller wraps this defensively."""
    rep = rep or {}
    engine_mark = {
        "current_underlying": rep.get("current_underlying"),
        "current_value": rep.get("current_value"),
        "unrealized_pnl": (mark or {}).get("unrealized_pnl"),
        "current_short_delta": rep.get("current_short_delta"),
        "dte": _rescue_dte(r.get("expiration")),
    }
    # assess_position_risk reads position.get("strategy"); ensure it's present
    # without destructively renaming an existing ``type`` field on the row.
    pos = r if r.get("strategy") else {**r, "strategy": r.get("type")}
    risk = _assess_position_risk(pos, engine_mark, gex=None, regime=None)
    state = risk.get("state", "ok")
    heat = float(risk.get("heat", 0.0) or 0.0)

    rec = ((mark or {}).get("recommendation") or "").upper()
    code = ((mark or {}).get("recommendation_code") or "").upper()
    if rec == "CUT" or code in _LOSS_STOP_CODES:
        # A CUT always lands on the board: escalate to at least ``tested`` (never
        # downgrade a ``critical``) and floor the heat so borderline math can't
        # hide it.
        if state not in ("tested", "critical"):
            state = "tested"
        heat = max(heat, _CUT_HEAT_FLOOR)

    r["rescue_state"] = state
    r["heat"] = heat
    # Surface the live short-leg delta on the row so the Rescue board's "Δ short"
    # column has data for captured signals (the mark merge above doesn't carry it).
    if rep.get("current_short_delta") is not None:
        r["current_short_delta"] = rep.get("current_short_delta")


def close_captured(signal_id, exit_val: float, reason: str) -> None:
    """Manually close a captured signal at ``exit_val``. Mirrors the page's close."""
    import signal_db

    signal_db.close_signal_manually(signal_id, float(exit_val), reason or "MANUAL_CLOSE")


# ── Captured auto-manage cycle (break-even trailing + auto-close) ─────────────
# The close codes that auto-close an OPEN captured signal. TARGET_HIT is NOT
# here: the recommender no longer emits it (a +50% winner now ARMS break-even
# and rides on toward full credit, protected by the break-even stop).
_CAPTURED_CLOSE_CODES = ("BREAKEVEN_STOP", "MONEY_STOP", "TIME_STOP", "DELTA_STOP")


def _captured_be_level(row) -> float:
    """Break-even close floor ($) for a captured signal = round-trip commissions
    for its structure. Defensive → 0.0 on any failure (break-even stop at pnl<=$0)."""
    try:
        strat = row.get("strategy") or row.get("type")
        return commission.round_trip_commission(strat, row.get("symbol"), 1)
    except Exception:
        return 0.0


def run_captured_manage_cycle() -> dict:
    """Reprice → arm break-even → auto-close the OPEN captured signals (paper-only).

    Mirrors the driver/paper manage pattern; fully defensive (a per-signal failure
    is skipped, never fatal). For each OPEN captured signal:
      1. Reprice via ``signal_repricer.reprice_swing`` (stale/failed reprice is
         skipped — never close on bad data — EXCEPT an expired signal, which
         settles at intrinsic below).
      2. **Expiry:** at ``DTE <= 0`` settle at the repriced intrinsic (OTM → ~0 →
         full credit) with reason ``EXPIRED`` and move on.
      3. Build a lifecycle mark (``build_mark`` threads be_armed/strategy/strikes/
         spot + the be_level break-even floor) and persist it (``insert_mark``).
      4. **Arm** break-even the first time pnl reaches +50% credit (``set_be_armed``).
      5. **Auto-close** when the mark's recommendation code is a close code
         (``_CAPTURED_CLOSE_CODES``) via ``close_signal_manually`` (writes an
         outcome + realized P&L — NEVER a broker order).

    Returns ``{"closed": [{signal_id, symbol, reason, exit_val}, ...],
    "armed": [{signal_id, symbol}, ...]}`` for the handler to log/notify."""
    import datetime as dt

    import signal_db
    import signal_recommender
    import signal_repricer

    try:
        signal_repricer.clear_chain_cache()
    except Exception:
        log.exception("clear_chain_cache before captured manage degraded")

    try:
        sigs = signal_db.get_open_signals_with_latest_mark()
    except Exception:
        log.exception("captured manage signal read degraded → empty set")
        return {"closed": [], "armed": []}

    now = dt.datetime.now(_PROJ_CT_TZ)
    tp_dollars = signal_recommender.TP_FRAC * signal_recommender.MULTIPLIER
    closed, armed = [], []
    for r in sigs:
        sid = r.get("signal_id")
        try:
            rep = signal_repricer.reprice_swing(r, _proxy.schwab_py_client)
            if rep is None:
                continue

            dte = _rescue_dte(r.get("expiration"))
            if dte is not None and dte <= 0:
                # Expiry settlement — settle at the repriced intrinsic; fall back
                # to the engine intrinsic vs the current/entry spot when there is
                # no live chain (an already-expired reprice returns no value).
                exit_val = rep.get("current_value")
                if exit_val is None:
                    spot = rep.get("current_underlying") or r.get("entry_underlying")
                    if spot is not None:
                        try:
                            exit_val, _pnl = signal_repricer.intrinsic_value(r, float(spot))
                        except Exception:
                            exit_val = None
                if exit_val is not None:
                    signal_db.close_signal_manually(sid, exit_val, "EXPIRED")
                    closed.append({"signal_id": sid, "symbol": r.get("symbol"),
                                   "reason": "EXPIRED", "exit_val": exit_val})
                continue

            # Not expired — a stale/failed reprice must NEVER close on bad data.
            if rep.get("error"):
                continue

            mark = signal_recommender.build_mark(r, rep, now,
                                                 be_level=_captured_be_level(r))
            if not mark:
                continue
            signal_db.insert_mark(mark)

            # Arm break-even the first time pnl reaches +50% of the credit.
            pnl = mark.get("unrealized_pnl")
            credit = r.get("entry_credit") or 0
            if (pnl is not None and credit and not r.get("be_armed")
                    and pnl >= tp_dollars * credit):
                signal_db.set_be_armed(sid)
                r["be_armed"] = 1
                armed.append({"signal_id": sid, "symbol": r.get("symbol")})

            code = (mark.get("recommendation_code") or "").upper()
            if code in _CAPTURED_CLOSE_CODES:
                exit_val = rep.get("current_value")
                if exit_val is not None:
                    signal_db.close_signal_manually(sid, exit_val, code)
                    closed.append({"signal_id": sid, "symbol": r.get("symbol"),
                                   "reason": code, "exit_val": exit_val})
        except Exception:
            log.exception("captured manage: signal %s degraded (skipped)", sid)
            continue

    return {"closed": closed, "armed": armed}


def captured_closed_today() -> dict:
    """Today's (CT) closed captured outcomes + a day realized total.

    ``{"closed": [...], "total_realized": <sum realized_pnl>}`` — the Tier-1 EOD
    page's "Captured — closed today" view. Defensive → empty on any failure."""
    import signal_db

    today = _dt.datetime.now(_PROJ_CT_TZ).date().isoformat()
    try:
        closed = signal_db.get_outcomes_for_date(today)
    except Exception:
        log.exception("captured_closed_today read degraded → empty")
        closed = []
    total = round(sum((c.get("realized_pnl") or 0.0) for c in closed), 2)
    return {"closed": closed, "total_realized": total}


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


def apply_live_spots(view, quotes_raw):
    """Overlay the live spot onto a matrix view (mutates + returns it).

    ``day_pct`` and ``gex_regime`` are RECOMPUTED from the live spot against the SAME
    session-open baseline (``_open_spot``) / gamma flip (``flip``) that
    ``matrix.build_rows`` used, so the headline Day% keeps a single "vs session open"
    baseline instead of flickering between it and the schwab ``netPercentChange`` (vs
    prior close) that the 1-min rebuild and this ~30s overlay would otherwise disagree
    on. Defensive per-symbol: a missing/None quote leaves that row's existing values
    untouched (never nulls a value), and one bad row can't sink the overlay."""
    import services.options_svc.matrix as mx  # lazy: pure helper reuse, avoid import cycle

    rows = (view or {}).get("rows") or []
    for r in rows:
        try:
            sym = r.get("symbol")
            if not sym:
                continue
            last = quote_last(quotes_raw, sym)
            if last is not None:
                r["spot"] = round(last, 2)
                open_spot = r.get("_open_spot")
                if open_spot:  # truthy (nonzero) → safe divisor
                    r["day_pct"] = round((last - open_spot) / open_spot * 100.0, 2)
                r["gex_regime"] = mx.gex_regime(last, r.get("flip"))
        except Exception:  # noqa: BLE001
            log.debug("apply_live_spots: skipping symbol", exc_info=True)
            continue
    return view


def matrix_quotes(symbols):
    """One batched /quotes fetch for the matrix live-spot overlay. Defensive → {}.

    Mirrors ``refresh_header``'s batched-quote idiom so the proxy dependency stays
    in the compute layer (the handler only reads cache + calls this)."""
    try:
        return _proxy.schwab_py_client.get_quotes(list(symbols)).json() or {}
    except Exception:  # noqa: BLE001
        log.warning("matrix_quotes fetch degraded → {}", exc_info=True)
        return {}


def refresh_header() -> dict:
    """Compute the compact header view (quotes + VIX regime + sentiment dot).

    Returns ``{"prices": {"$SPX","SPY","QQQ"}, "vix", "vix_regime", "sentiment"}``.
    Defensive throughout: a quotes failure yields blank prices/regime; a sentiment
    failure yields the no-data dot — the view is always a well-formed dict."""
    try:
        raw = _proxy.schwab_py_client.get_quotes(HEADER_SYMBOLS).json() or {}
    except Exception:
        log.warning("header quotes fetch degraded → blank prices", exc_info=True)
        raw = {}

    prices = {s: quote_last(raw, s) for s in ("$SPX", "SPY", "QQQ")}
    vix = quote_last(raw, "$VIX")
    regime = vix_regime(vix) or {} if isinstance(vix, (int, float)) else {}

    try:
        dot_color, dot_label = sentiment_dot(evaluate_regime())
    except Exception:
        log.warning("header sentiment dot degraded → no-data dot", exc_info=True)
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


# Term-structure view wants the next ~5 expirations. The GEX/Charm/DEX/Vanna
# views only need the nearest expiry, so the cheap 7-day _gamma_fetch_chain
# window is right for them — but it misses expirations for weekly/monthly-only
# names. _term_chain widens by EXPIRATION COUNT (not a fixed day window, which
# breaks across daily/weekly/monthly cadences): reuse the base chain when it
# already covers n_exp expirations (indices, with daily expiries), else fetch
# progressively wider windows until enough expirations appear.
_TERM_N_EXP = 5
_TERM_WINDOWS = (45, 160, 300)   # calendar-day windows: ~6 weeklies / ~5 monthlies


def _count_expirations(chain) -> int:
    """Number of distinct expiration dates in a chain's call/put maps."""
    if not chain:
        return 0
    dates = set()
    for mp in ((chain.get("callExpDateMap") or {}), (chain.get("putExpDateMap") or {})):
        for key in mp:
            dates.add(str(key).split(":", 1)[0])
    return len(dates)


def _term_chain(symbol, base_chain, n_exp: int = _TERM_N_EXP):
    """Return a chain covering at least ``n_exp`` expirations for the Term view.

    Reuses ``base_chain`` (the nearest-expiry GEX fetch) when it already covers
    n_exp expirations — true for indices with daily expirations. For weekly/
    monthly-only names it widens the fetch window step by step until n_exp
    expirations are present (or the widest window is reached), so the Term grid
    shows 5 expirations regardless of the symbol's cadence. Defensive: a failed
    widen keeps the best chain so far (never fewer expirations than the base)."""
    import datetime as dt

    best = base_chain
    if _count_expirations(best) >= n_exp:
        return best
    today = dt.date.today()
    for days in _TERM_WINDOWS:
        resp = _proxy.schwab_py_client.get_option_chain(
            symbol, contract_type="ALL", from_date=today,
            to_date=today + dt.timedelta(days=days))
        if getattr(resp, "status_code", None) != 200:
            continue
        chain = resp.json()
        if _count_expirations(chain) > _count_expirations(best):
            best = chain
        if _count_expirations(best) >= n_exp:
            break
    return best


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


# Views with directional walls (mirrors gamma_walls — Charm/Vanna have none).
_WALL_VIEWS = frozenset({"GEX", "DEX"})


def _level_track(rows, vname):
    """Per-snapshot flip / call-wall / put-wall levels, parallel 1:1 with ``rows``.

    Powers the heatmap's optional "level movement" overlay: where the walls and the
    gamma flip sat at every point in the session, not just now. Positional
    alignment matters — the page indexes these by the heatmap's time-category
    index, which is the row index.

    Flip is the STORED column (already the value the summary shows). The walls are
    RECOMPUTED per row from that row's own per-strike grid via the engine's
    directional picker, because the stored ``top_pos_strike``/``top_neg_strike``
    are a DIFFERENT metric — the max/min NET strike anywhere in the chain, vs the
    largest call ABOVE spot / most-negative put BELOW spot that the chart draws.
    Measured on a live session, the two disagreed on 383 of 383 rows, so reusing
    the stored columns would draw tracks that contradict the wall lines beside
    them. Recomputing costs ~11 ms per view per session.

    MUST run BEFORE ``_crop_gamma_views``: a wall can sit outside the ±N-strike
    display window, and the crop would silently truncate the grid it's found in.

    Missing/garbage rows yield ``None`` placeholders rather than dropping out, so
    the arrays stay aligned with the time axis. Never raises."""
    import gamma_tool as gt

    out = {"flip": [], "call_wall": [], "put_wall": []}
    walled = vname in _WALL_VIEWS
    for r in (rows or []):
        flip = call_w = put_w = None
        try:
            flip = r[2] if isinstance(r[2], (int, float)) else None
            spot, grid = r[1], r[6]
            if walled and grid and isinstance(spot, (int, float)) and spot > 0:
                w = gt.get_directional_walls({"gex": grid}, spot) or {}
                call_w, put_w = w.get("call_wall"), w.get("put_wall")
        except Exception:
            log.debug("_level_track row failed", exc_info=True)
        out["flip"].append(flip)
        out["call_wall"].append(call_w)
        out["put_wall"].append(put_w)
    return out


# Strikes shown on each side of spot in the Gamma page's bar/heatmap window
# (webgui/pages/options/gamma.py N_SIDE). The page crops the display to this
# ±N_SIDE window; embedding the FULL per-strike chain history (all four views,
# ~250 strikes × full session) made cache:options:gamma ~14 MB and forced a big
# JSON parse on the GUI event loop every 120 s. We crop each per-strike grid to
# the display window SERVER-SIDE before caching — same key, same structure, far
# fewer strikes. flip/walls are computed on the FULL grid FIRST (they're separate
# fields), so cropping can't change them.
GAMMA_N_SIDE = 20


def _window_around(strikes, spot, n_side=GAMMA_N_SIDE):
    """The nearest ``n_side`` strikes ≤ spot + ``n_side`` strictly above — mirrors
    the page's ``gamma.strikes_around``. Returns a set of floats; an unusable spot
    returns ALL numeric strikes (no crop)."""
    s = sorted({x for x in (strikes or []) if isinstance(x, (int, float))})
    if not isinstance(spot, (int, float)):
        return set(s)
    below = [x for x in s if x < spot][-n_side:]
    above = [x for x in s if x > spot][:n_side]
    at = [x for x in s if x == spot]
    return set(below + at + above)


def _crop_grid(grid, keep):
    """Return ``grid`` restricted to keys in ``keep`` (a set of float strikes).

    ``grid`` maps strike(float) -> cell. Non-dict / empty grids pass through
    unchanged. ``keep=None`` means no crop."""
    if keep is None or not isinstance(grid, dict) or not grid:
        return grid
    return {k: v for k, v in grid.items() if k in keep}


def _crop_gamma_views(views, spot, n_side=GAMMA_N_SIDE):
    """Crop each view's current per-strike ``data`` grid AND its history-row grids
    (tuple index 6) to the display window: the ±``n_side`` band around the CURRENT
    spot, widened to span the intraday spot PATH (min→max of history-row spots).

    This exactly mirrors the page's y-range (``bar_yrange(±n_side)`` then
    ``union_range(spot_path)``) — the heatmap is cropped to that same [lo, hi]
    band client-side, so keeping only strikes in that band is behavior-preserving
    while dropping the ~250-strike full chain down to the visible window.

    Mutates ``views`` in place (the entries are freshly built dicts). flip/walls
    live in separate fields already computed on the FULL grid, so they're untouched.
    Returns ``views``.
    """
    for entry in (views or {}).values():
        data = entry.get("data") or {}
        grid = data.get("gex") or {}
        rows = entry.get("history") or []
        # All strikes present anywhere (current grid + every history-row grid).
        all_strikes = set(grid.keys())
        for r in rows:
            if len(r) > 6 and isinstance(r[6], dict):
                all_strikes.update(r[6].keys())
        path_spots = [r[1] for r in rows
                      if len(r) > 1 and isinstance(r[1], (int, float))]
        if not isinstance(spot, (int, float)) and not path_spots:
            continue  # no usable spot → leave grids untouched (can't window safely)
        # ±n_side band around the current spot (falls back to the path when the
        # current spot is missing — e.g. an off-hours snapshot).
        anchor = spot if isinstance(spot, (int, float)) else path_spots[0]
        keep = _window_around(all_strikes, anchor, n_side)
        # Widen to span the intraday spot path (so the overlaid price line — and any
        # strike between two drifted spots — isn't clipped), matching union_range.
        if path_spots:
            lo, hi = min(path_spots), max(path_spots)
            keep |= {k for k in all_strikes if lo <= k <= hi}
        # Crop the current per-strike grid used for the bars.
        if grid:
            data["gex"] = _crop_grid(grid, keep)
        # Crop each history row's grid (index 6). Rows are tuples → rebuild.
        cropped_rows = []
        for r in rows:
            if len(r) > 6 and isinstance(r[6], dict) and r[6]:
                r = tuple(r[:6]) + (_crop_grid(r[6], keep),) + tuple(r[7:])
            cropped_rows.append(r)
        entry["history"] = cropped_rows
        # Crop the forward-projection grid (GEX view only) to the same window.
        # Its keys are strike STRINGS ("5400.0") vs the float ``keep`` set.
        proj = entry.get("projection")
        if isinstance(proj, dict) and isinstance(proj.get("grid"), dict) and proj["grid"]:
            def _kf(s):
                try:
                    return float(s)
                except (TypeError, ValueError):
                    return None
            proj["grid"] = {k: v for k, v in proj["grid"].items() if _kf(k) in keep}
    return views


# Session-history memo for gamma_snapshot: decoded heatmap rows per
# (symbol, view), valid for ONE session date. gamma_snapshot runs every minute
# during RTH (the 1-min collector branch + the page timer); re-loading and
# json-decoding the WHOLE session's per-strike grids (4 views × ~440 rows by the
# close) each run was the service's largest recurring CPU burn — the memo keeps
# the decoded rows and appends only rows newer than the last seen ts (a sargable
# ``ts > ?`` query). Guarded by a lock (scheduler branch + command handlers can
# snapshot concurrently); rows are never mutated downstream (_crop_gamma_views
# REBUILDS row tuples), so handing out a shallow list copy is safe.
_HIST_MEMO: dict = {"date": None, "data": {}}
_HIST_LOCK = threading.Lock()


def reset_gamma_history_memo():
    """Drop the memoized session history (test helper / manual reset)."""
    with _HIST_LOCK:
        _HIST_MEMO.update(date=None, data={})


# Per-tick chain stash: the 1-min collector branch fetches the viewed symbol's
# chain (poll_once) and then, seconds later, refresh_gamma_current used to fetch
# THE SAME chain again for gamma_snapshot — two chain fetches + two engine passes
# per tick for one symbol. The collector now stashes the fetched chain here and
# gamma_snapshot CONSUMES it once (pop semantics): only the same-tick refresh
# reuses it, every other caller (page-timer refresh, symbol switch) still
# fetches fresh. The TTL guards a crash between stash and take.
TICK_CHAIN_TTL_SEC = 45
_TICK_CHAIN: dict = {"ts": 0.0, "symbol": None, "chain": None}
_TICK_CHAIN_LOCK = threading.Lock()


def reset_tick_chain():
    """Drop any stashed tick chain (test helper)."""
    with _TICK_CHAIN_LOCK:
        _TICK_CHAIN.update(ts=0.0, symbol=None, chain=None)


def _stash_tick_chain(symbol, chain):
    """Stash a just-fetched chain for the same tick's gamma refresh."""
    import time as _time
    with _TICK_CHAIN_LOCK:
        _TICK_CHAIN.update(ts=_time.monotonic(), symbol=symbol, chain=chain)


def _take_tick_chain(symbol):
    """Pop the stashed chain if it matches ``symbol`` and is fresh, else None."""
    import time as _time
    with _TICK_CHAIN_LOCK:
        if (_TICK_CHAIN["chain"] is not None
                and _TICK_CHAIN["symbol"] == symbol
                and _time.monotonic() - _TICK_CHAIN["ts"] < TICK_CHAIN_TTL_SEC):
            chain = _TICK_CHAIN["chain"]
            _TICK_CHAIN.update(ts=0.0, symbol=None, chain=None)
            return chain
        return None


# Contract-level UOA (unusual options activity) computed during the poll's on_chain
# hook (reusing each already-fetched chain — no re-fetch) and consumed ONCE by
# handlers.run_flow_alerts on the same tick. Filled every collect, cleared at the
# start of the next collect (and drained by take_uoa_stash), so it never accumulates.
_UOA_STASH: dict = {}   # {symbol: [uoa contract dicts]} for the current tick


def clear_uoa_stash():
    _UOA_STASH.clear()


def stash_uoa(symbol, contracts):
    if contracts:
        _UOA_STASH[symbol] = contracts


def take_uoa_stash() -> dict:
    """Return + clear the tick's UOA results (consumed once by run_flow_alerts)."""
    out = dict(_UOA_STASH)
    _UOA_STASH.clear()
    return out


# Same pattern as _UOA_STASH, for the big_delta (relative delta-notional) detector —
# also computed during the poll's on_chain hook and consumed once by
# handlers.run_flow_alerts on the same tick.
_BIG_DELTA_STASH: dict = {}   # {symbol: [big_delta contract dicts]} for the current tick


def clear_big_delta_stash():
    _BIG_DELTA_STASH.clear()


def stash_big_delta(symbol, contracts):
    if contracts:
        _BIG_DELTA_STASH[symbol] = contracts


def take_big_delta_stash() -> dict:
    """Return + clear the tick's big_delta results (consumed once by run_flow_alerts)."""
    out = dict(_BIG_DELTA_STASH)
    _BIG_DELTA_STASH.clear()
    return out


def _rth_bounds(session_date):
    """(start_ts, end_ts) unix seconds bounding RTH on ``session_date`` in CT.

    Computed once per snapshot and compared numerically against each row's ts —
    far cheaper than converting every row's timestamp to a local time."""
    import datetime as _dtmod
    start = _dtmod.datetime.combine(
        session_date, _dtmod.time(*_RTH_START), tzinfo=_PROJ_CT_TZ)
    end = _dtmod.datetime.combine(
        session_date, _dtmod.time(*_RTH_END), tzinfo=_PROJ_CT_TZ)
    return start.timestamp(), end.timestamp()


def _rth_only(rows, bounds):
    """Rows whose ts (index 0) falls inside ``bounds``, inclusive of both ends.

    ``bounds`` None → passthrough: if the window can't be computed we show
    everything rather than silently blanking the chart."""
    if not bounds:
        return list(rows or [])
    lo, hi = bounds
    return [r for r in (rows or [])
            if isinstance(r[0], (int, float)) and not isinstance(r[0], bool)
            and lo <= r[0] <= hi]


def _display_session_date(now, session_date):
    """The session whose RTH data the Gamma time-axis charts should show.

    ``scheduler.active_session_date`` flips to today at the 08:00 CT COLLECTION
    start, but these charts display RTH only — so between 08:00 and the 08:30 open
    today has rows yet none that are displayable. Keep showing the prior session
    until RTH actually opens, so the charts are never blank mid-morning. Off-hours
    and weekends already hand us a prior date, which passes through untouched."""
    try:
        if session_date == now.date() and (now.hour, now.minute) < _RTH_START:
            # Lazy, mirroring gamma_snapshot — scheduler is not a module-level import.
            from services.options_svc import scheduler as _sch
            return _sch._prev_trading_day(session_date)
    except Exception:
        log.debug("_display_session_date failed — using the active session", exc_info=True)
    return session_date


def _history_rows_incremental(gh, conn, symbol, vstr, session_date):
    """Memoized, append-only heatmap rows for (symbol, view) on session_date.

    Cold (or on a session-date change): full ``load_date_with_grid``. Warm: load
    only rows with ``ts > last-seen`` and append. Returns a shallow copy of the
    accumulated row list (callers must not receive the memo's own list)."""
    with _HIST_LOCK:
        if _HIST_MEMO["date"] != session_date:
            _HIST_MEMO.update(date=session_date, data={})
        ent = _HIST_MEMO["data"].get((symbol, vstr))
        since = ent["last_ts"] if ent else None
    new_rows = gh.load_date_with_grid(conn, symbol, vstr, date=session_date,
                                      since_ts=since)
    with _HIST_LOCK:
        # Re-check the date under the lock (another thread may have rolled it).
        if _HIST_MEMO["date"] != session_date:
            _HIST_MEMO.update(date=session_date, data={})
        ent = _HIST_MEMO["data"].setdefault(
            (symbol, vstr), {"last_ts": None, "rows": []})
        if since is None and ent["rows"]:
            # A concurrent cold load already populated the memo — keep it and
            # ignore this duplicate full load (identical data).
            pass
        elif new_rows:
            ent["rows"].extend(new_rows)
            ent["last_ts"] = new_rows[-1][0]
        elif since is None:
            ent["rows"] = []
            ent["last_ts"] = None
        return list(ent["rows"])


def gamma_snapshot(symbol: str, chain=None) -> dict | None:
    """Fetch + compute the full Gamma snapshot for ``symbol``.

    ``chain`` — an optional already-fetched chain dict (same tick). When omitted,
    the stashed tick chain (see ``_take_tick_chain``) is consumed if fresh, else
    the chain is fetched via ``_gamma_fetch_chain``.

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

    from services.options_svc import scheduler as _sched

    # Persistence: the Gamma display shows the most-recent-available session 24/7 so
    # the by-strike charts (computed from the live chain, which returns data off-hours)
    # AND the heatmap stay visible PRE- and POST-market. The heatmap loads the ACTIVE
    # SESSION DATE (today once collection starts at 08:00 CT, else the prior session),
    # so premarket it shows yesterday until today's snapshots begin.
    now = _sched._market_now()
    # The time-axis charts (heatmap + Flow) show RTH only, so before the 08:30 open
    # fall back to the prior session — today has collected rows but none displayable.
    session_date = _display_session_date(now, _sched.active_session_date(now))
    rth = _rth_bounds(session_date)

    if chain is None:
        chain = _take_tick_chain(symbol)
    if chain is None:
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

    # ONE read-only connection reused across all four view history loads (was a
    # fresh connect per view — 4 opens per snapshot). None if the open failed →
    # each _history degrades to [].
    try:
        hist_conn = gh.connect(read_only=True)
    except Exception:
        hist_conn = None

    def _history(vstr):
        if hist_conn is None:
            return []
        try:
            # Active session date (today live, or the last trading day off-hours/
            # weekends) so the heatmap persists after close + clears next session.
            # Incremental: memoized decoded rows + append-only ts > last-seen loads
            # (see _history_rows_incremental) instead of a full-session re-decode.
            # The memo holds EVERY collected row; RTH filtering happens after it, so
            # the append-only load still works off the true last-collected ts.
            return _rth_only(
                _history_rows_incremental(gh, hist_conn, symbol, vstr, session_date),
                rth)
        except Exception:
            return []

    flow, hedge_history = [], []
    try:
        views = {}
        for vname, (idx, vstr) in _GAMMA_VIEWS.items():
            data = by_index.get(idx) or {}
            try:
                summary = eng.snapshot_summary(data, vstr)
            except Exception:
                summary = {}
            _rows = _history(vstr)
            entry = {
                "data": data,
                "summary": summary,
                "walls": _walls(vname, data),
                "flip": (summary or {}).get("flip"),
                "history": _rows,
                # Intraday movement of the flip + walls, parallel to `history`.
                # Built HERE, before _crop_gamma_views, so the wall search sees each
                # snapshot's FULL grid (a wall can sit outside the display window).
                "levels": _level_track(_rows, vname),
            }
            if vname == "DEX":
                entry["hedge"] = {
                    "net_delta_0dte": data.get("net_delta_0dte"),
                    "projected_net_delta_close": data.get("projected_net_delta_close"),
                    "hedge_pressure": data.get("hedge_pressure"),
                }
            if vname == "GEX":
                try:
                    marks = _future_marks_ct(now)
                    proj = project_gex_grid(eng, chain, spot, now)
                    proj["cone"] = project_em_cone(spot, atm_iv_from_chain(chain, spot), marks, now)
                    entry["projection"] = proj
                except Exception:
                    log.debug("gamma projection attach failed", exc_info=True)
            views[vname] = entry
        # Intraday options-flow series (spot + daily-cumulative call/put volume +
        # premium) for the Flow view — reuses the SAME read-only connection as the
        # view history loads (one open per snapshot). Same active-session date.
        if hist_conn is not None:
            try:
                _frows = _rth_only(
                    gh.load_flow_series(hist_conn, symbol, session_date), rth)
                flow = [{"ts": r[0], "spot": r[1], "call_vol": r[2], "put_vol": r[3],
                         "call_prem": r[4], "put_prem": r[5]} for r in _frows]
            except Exception:
                flow = []
            # 0-DTE hedge-pressure track — same connection, same RTH window as the
            # heatmap rows so the panel's time axis lines up with it. Empty for any
            # symbol whose nearest expiry isn't today (the column is NULL there).
            try:
                _hrows = _rth_only(
                    gh.load_hedge_series(hist_conn, symbol, session_date), rth)
                hedge_history = [{"ts": r[0], "hedge_pressure": r[1],
                                  "net_delta_0dte": r[2], "projected_flip": r[3]}
                                 for r in _hrows]
            except Exception:
                log.debug("hedge history load failed", exc_info=True)
                hedge_history = []
    finally:
        if hist_conn is not None:
            try:
                hist_conn.close()
            except Exception:
                log.debug("gamma history conn close failed", exc_info=True)

    # Slim the payload: crop each view's per-strike grid + history grids to the
    # ±display-window strikes. flip/walls above were computed on the FULL grid, so
    # they're unaffected. (Cut cache:options:gamma from ~14 MB → well under ~1 MB.)
    _crop_gamma_views(views, spot)

    try:
        # The Term view wants the next 5 expirations regardless of the symbol's
        # expiration cadence; widen the chain beyond the nearest-expiry GEX window
        # only when the base chain doesn't already cover them (see _term_chain).
        term = eng.compute_term_grid(_term_chain(symbol, chain))
    except Exception:
        term = {}

    # Projected EOD delta-flip: where the DEX curve crosses zero once the 0-DTE
    # book's deltas are advanced to the 15:00 CT close by CHARM at flat spot. It's
    # ONE level (a 0-DTE delta concept, not per view), so it's published at snapshot
    # level and the page draws it on every heatmap as a shared reference. None
    # whenever the nearest expiry isn't today — i.e. most symbols, most of the time.
    try:
        projected_flip = gt.compute_projected_flip(by_index.get(2) or {}, spot)
    except Exception:
        log.debug("projected flip failed", exc_info=True)
        projected_flip = None

    return {"symbol": symbol, "spot": spot, "dte": dte,
            "views": views, "term": term, "flow": flow,
            "projected_flip": projected_flip, "hedge_history": hedge_history}


# ── Intraday GEX history collection (Tier-2 owner) ──────────────────────────
# The Gamma page's strike×time heatmap reads gex_history.db. That DB used to be
# written ONLY by the standalone options-scanner/gex_collector.py process,
# launched in its own console window by start_all.bat. When that window died
# (closed, machine sleep, or lock contention from a double launch) collection
# silently stopped and the heatmap froze at the first snapshots — "no data past
# the first hour". The always-on options service now owns collection: the
# scheduler calls this on every 2-min slot within market hours, so history
# accrues for the whole session whenever the service is up.

# Retention on the live collection path: keep the last N distinct session-dates
# so the DB stops growing without bound (it grew ~250 MB/day, unbounded) WHILE
# preserving the Gamma page's off-hours persistence (it shows the last session
# until the next trading day — so at least the most recent session must survive).
# N=5 covers weekends/holidays comfortably. The purge is gated to run at most
# once per local date (NOT every 2-min collect tick — purging every tick wastes a
# scan/DELETE). ``_LAST_PURGE_DATE`` tracks the last date we ran it.
GEX_KEEP_SESSIONS = 5
_LAST_PURGE_DATE = None
_GEX_SCHEMA_READY = False   # init_schema latch (once per process, not per 1-min tick)


def _maybe_purge_gex(gh, conn) -> None:
    """Run the keep-last-N-sessions retention at most once per local date.

    Defensive: any failure (a locked/legacy DB) is swallowed — retention must
    never break the collection round. DELETE reclaims free pages for reuse (bounds
    growth) but does NOT shrink the 3 GB file; see ``purge_keep_sessions`` for the
    documented one-time manual VACUUM."""
    global _LAST_PURGE_DATE
    import datetime as _dt

    today = _dt.date.today()
    if _LAST_PURGE_DATE == today:
        return
    try:
        gh.purge_keep_sessions(conn, keep_sessions=GEX_KEEP_SESSIONS)
        _LAST_PURGE_DATE = today
    except Exception:
        # Leave _LAST_PURGE_DATE unset so the next collect start retries.
        pass


def collect_gex_snapshots(capture_symbols=None) -> int:
    """Fetch + persist one snapshot round (GEX/Charm/DEX/Vanna + term) for the
    tracked symbols. Returns ``len(gex_collector.collection_symbols())`` (the
    dynamic collection universe), or ``0`` when a fresh foreign collector owns
    the advisory lock (we defer).

    ``capture_symbols`` — optional set of symbols whose fetched chains should be
    stashed for the SAME tick's gamma refresh (``_stash_tick_chain`` → consumed
    once by ``gamma_snapshot``), so the currently-viewed symbol's chain isn't
    fetched twice in one tick.

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
        # init_schema is a per-DB-file property (idempotent CREATE/ALTER/DROP), so
        # run it ONCE per process, not on every 1-min collect (a needless
        # executescript + PRAGMA + write-lock + commit each tick).
        global _GEX_SCHEMA_READY
        if not _GEX_SCHEMA_READY:
            gh.init_schema(conn)
            _GEX_SCHEMA_READY = True
        # Once-per-day retention at collection start (keeps growth bounded).
        _maybe_purge_gex(gh, conn)
        gc.log.info("Polling GEX history (options_svc)")
        # Contract-level UOA + big_delta both ride the poll's on_chain hook (reusing
        # each fetched chain — no re-fetch); results are stashed per symbol and
        # consumed once by run_flow_alerts. flow_alerts is PURE (stdlib + repo_paths),
        # imported lazily. ONE load_thresholds() call serves both detectors.
        from services.options_svc import flow_alerts
        clear_uoa_stash()
        clear_big_delta_stash()
        _uoa_cfg = flow_alerts.load_thresholds()
        # Kill-switch: when the feature is disabled, skip the per-symbol UOA compute
        # entirely (nothing computed/published). The chain-capture stash stays on.
        _uoa_on = _uoa_cfg.get("enabled", True)
        # big_delta has its OWN enabled flag (independent of the top-level UOA
        # switch above) — the whole detector is inert when [big_delta].enabled=false.
        _big_delta_on = _uoa_cfg.get("big_delta", {}).get("enabled", True)
        wanted = set(capture_symbols) if capture_symbols else set()

        def on_chain(sym, chain):  # noqa: F811 — the callback poll_once calls
            if sym in wanted:
                _stash_tick_chain(sym, chain)
            if _uoa_on:
                # Best-effort — a UOA detect failure must NEVER break collection.
                try:
                    stash_uoa(sym, flow_alerts.detect_uoa(sym, chain, _uoa_cfg))
                except Exception:
                    gc.log.debug("UOA detect failed for %s", sym, exc_info=True)
            if _big_delta_on:
                # Best-effort — a big_delta detect failure must NEVER break collection.
                try:
                    stash_big_delta(sym, flow_alerts.detect_big_delta(sym, chain, _uoa_cfg))
                except Exception:
                    gc.log.debug("big_delta detect failed for %s", sym, exc_info=True)

        gc.poll_once(_proxy.schwab_py_client, gt.GammaEngine(), conn,
                     on_chain=on_chain)
        gc.touch_lock(gc.LOCK_PATH, source="options_svc", owner=owner,
                      now=int(time.time()))
    finally:
        conn.close()
    return len(gc.collection_symbols())


def _gex_next_scan(now):
    """Next GEX-collection boundary strictly after ``now`` within the
    08:00–15:20 CT window, or None if ``now`` is past the window end.

    Reuses the scheduler's ``_GEX_START``/``_GEX_STOP``/``_GEX_INTERVAL_MIN``
    cadence (08:00–15:20 CT, every 1 min). Returns a CT-aware datetime or None.
    Before 08:00 → the window's first slot (08:00 today). At/after 15:20 → None.

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
                log.debug("gex_status conn close failed", exc_info=True)
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


# Index symbols the flow-skew view reports (the sentiment service reads this as an
# aggression input). Deliberately small + explicit (not the whole GEX universe).
FLOW_SKEW_INDEX_SYMBOLS = ("$SPX", "SPY", "QQQ")


def flow_skew_view() -> dict:
    """Per-index 25-delta skew level + its CHANGE since the prior snapshot.

    Reads the last two GEX snapshots per index symbol from ``gex_history.db``
    (``gex_history_db.latest_skew_by_symbol``) and returns
    ``{symbol: {"rr_25d", "rr_delta", "call_vol", "put_vol", "ts"}}`` where
    ``rr_delta = latest.rr_25d − prior.rr_25d`` (None when either is missing or
    there is only one snapshot). Symbols with no rows are omitted.

    No strict contract (mirrors ``gex_status_view``): a small read-only dict the
    sentiment service consumes defensively. Fully defensive — any failure (DB
    locked/missing, import error) degrades to ``{}`` so the publish never raises.
    ``gex_history_db`` is imported LAZILY per the service's cross-app collision
    discipline (same as ``gex_status_view``/``collect_gex_snapshots``)."""
    try:
        import gex_history_db as gh
    except Exception:
        log.debug("flow_skew_view: gex_history_db import failed", exc_info=True)
        return {}

    try:
        conn = gh.connect(read_only=True)
    except Exception:
        log.debug("flow_skew_view: DB connect failed", exc_info=True)
        return {}

    out: dict = {}
    try:
        for symbol in FLOW_SKEW_INDEX_SYMBOLS:
            try:
                rows = gh.latest_skew_by_symbol(conn, symbol, "gex")
            except Exception:
                log.debug("flow_skew_view: read failed for %s", symbol,
                          exc_info=True)
                continue
            if not rows:
                continue
            ts, rr, call_vol, put_vol = rows[0][0], rows[0][1], rows[0][2], rows[0][3]
            rr_delta = None
            if len(rows) > 1:
                prior_rr = rows[1][1]
                if rr is not None and prior_rr is not None:
                    rr_delta = rr - prior_rr
            out[symbol] = {"rr_25d": rr, "rr_delta": rr_delta,
                           "call_vol": call_vol, "put_vol": put_vol, "ts": ts}
    except Exception:
        log.debug("flow_skew_view: build failed", exc_info=True)
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            log.debug("flow_skew_view conn close failed", exc_info=True)
    return out


# --- Options Matrix ----------------------------------------------------------
# DB-only orchestration for the ``cache:options:matrix`` payload: load each
# watchlist symbol's intraday flow series + latest gamma flip from
# ``gex_history.db``, count signals/alerts from the passed-in payloads, and call
# the PURE ``matrix.build_rows`` to assemble the rows. No proxy calls here (spot
# comes from the gex_history series; a live-quote overlay is a later task).


def _matrix_gh():
    """Loader accessor for ``gex_history_db`` (lazy import, per the cross-app
    collision discipline). Exposed as a module function so tests can monkeypatch
    it with a fake DB without a real store."""
    import gex_history_db as gh
    return gh


def _matrix_symbols():
    """The matrix row universe = collected universe minus ``$VIX`` (mirrors
    ``handlers._flow_alert_symbols``). ``gex_collector`` is imported LAZILY.
    Defensive → ``[]`` on failure."""
    try:
        import gex_collector
        return [s for s in gex_collector.collection_symbols() if s != "$VIX"]
    except Exception:
        log.debug("matrix symbol list degraded", exc_info=True)
        return []


def _count_scan_signals(scan_day, today):
    """``{symbol: count}`` from a ``cache:options:scan_day`` payload. Gated on
    date: a stale (``date != today``) envelope contributes nothing. Counts every
    signal dict across the three lists by its ``symbol`` (skips missing)."""
    if not scan_day or scan_day.get("date") != today:
        return {}
    counts: dict = {}
    for key in ("signals_0dte", "signals_swing", "signals_directional"):
        for sig in scan_day.get(key) or []:
            sym = (sig or {}).get("symbol")
            if sym:
                counts[sym] = counts.get(sym, 0) + 1
    return counts


def _count_flow_alerts(flow_cooldowns, today):
    """``{symbol: count}`` of today's DISTINCT flow-alert events per symbol, from the
    ``cache:options:flow_alert_cooldowns`` seen-map (``{date, map: {cid: ts}}``).

    Each ``cid`` (e.g. ``$SPX|crossover`` or ``QQQ|uoa|put|706|2026-07-21``) is one
    distinct alert event; the prefix before the first ``|`` is the symbol. This
    date-scoped seen-map is UNCAPPED and never pruned, so it is the true daily count —
    unlike ``cache:options:flow_alerts`` (a rolling list capped at 50 total that
    undercounts every symbol once the day fires >50 alerts). Gated on the map's date."""
    if not flow_cooldowns or flow_cooldowns.get("date") != today:
        return {}
    counts: dict = {}
    for cid in flow_cooldowns.get("map") or {}:
        sym = cid.split("|", 1)[0] if isinstance(cid, str) and "|" in cid else None
        if sym:
            counts[sym] = counts.get(sym, 0) + 1
    return counts


def build_matrix(scan_day, flow_cooldowns, today, session_date, now_ts):
    """Assemble the ``cache:options:matrix`` payload.

    Loads each watchlist symbol's intraday flow series + latest gamma flip from
    ``gex_history.db`` (one reused read-only connection, ALWAYS closed), counts
    signals from the ``scan_day`` payload + flow-alerts from the ``flow_cooldowns``
    seen-map (``cache:options:flow_alert_cooldowns`` — the uncapped daily source,
    NOT the 50-capped ``flow_alerts`` list), and hands the raw blobs to the PURE
    ``matrix.build_rows``. No proxy calls. Fully defensive — a DB-connect failure
    degrades to an empty-rows payload with ``error`` set; a per-symbol read failure
    yields an empty blob for that symbol (never sinks the build)."""
    # ``session_date`` arrives as a ``datetime.date`` OBJECT from
    # ``scheduler.active_session_date()`` — the gex_history DB readers NEED that
    # object (``_local_unix_range`` reads ``d.year/.month/.day``). But the count
    # gate compares against the payloads' STRING ``date`` field, and the cached
    # payload's ``session_date`` must be a JSON-clean string (the MatrixSnapshot
    # contract). So normalize to an isoformat string ONLY for the gate + the
    # returned field; keep the original object for the DB reads.
    session_key = (session_date.isoformat()
                   if hasattr(session_date, "isoformat") else session_date)
    # Gate the counts on ``session_date``, NOT ``today``: the flow series/flip are
    # read for ``session_date``, and off-hours those diverge (Saturday: today=Sat,
    # session_date=Fri, and the persisted scan_day/flow_alerts payloads are dated
    # Fri). Gating on session_date keeps the counts aligned with the displayed
    # session (during RTH today == session_date, so nothing changes).
    scan_counts = _count_scan_signals(scan_day, session_key)
    alert_counts = _count_flow_alerts(flow_cooldowns, session_key)
    symbols = _matrix_symbols()

    raw: dict = {}
    try:
        gh = _matrix_gh()
        conn = gh.connect(read_only=True)
    except Exception:
        log.debug("build_matrix: DB unavailable", exc_info=True)
        return {"date": today, "session_date": session_key, "ts": _now_iso(),
                "rows": [], "error": "matrix unavailable"}

    try:
        for sym in symbols:
            try:
                series = gh.load_flow_series(conn, sym, session_date)
                flip = gh.latest_flip(conn, sym, "gex", session_date)
                raw[sym] = {"series": series, "flip": flip}
            except Exception:
                log.debug("build_matrix: read failed for %s", sym, exc_info=True)
                raw[sym] = {"series": [], "flip": None}
    finally:
        try:
            conn.close()
        except Exception:
            log.debug("build_matrix conn close failed", exc_info=True)

    try:
        import services.options_svc.matrix as mx
        rows = mx.build_rows(raw, scan_counts, alert_counts, now_ts)
        rows.sort(key=lambda r: r["hotness"], reverse=True)
        # Dollar-weighted market-wide net call/put PREMIUM skew over the same raw
        # blobs — a market-money read consumed by the sentiment tiles. Additive.
        premium = mx.market_premium_aggregate(raw)
    except Exception:
        log.debug("build_matrix: build failed", exc_info=True)
        return {"date": today, "session_date": session_key, "ts": _now_iso(),
                "rows": [], "error": "matrix build failed"}
    return {"date": today, "session_date": session_key, "ts": _now_iso(),
            "rows": rows, "premium": premium, "error": None}


def build_net_premium(session_date, now=None):
    """Assemble the ``cache:options:net_premium`` payload.

    Intraday net-premium series for every symbol the Dealer Positioning "Net Prem"
    view can plot (``net_premium.source_symbols()``), RTH-cropped to the displayed
    session — the same window the heatmap and Flow views use, so the three time
    axes agree. One reused read-only connection, ALWAYS closed. No proxy calls.

    Rides the 1-min GEX branch, so this reads rows that were just written — it is
    not new data collection. It opens its OWN read-only connection rather than
    reusing the one ``build_matrix`` held moments earlier; see
    ``handlers.publish_net_premium`` for why the two reads stay independent.

    ``session_date`` MUST be a ``datetime.date`` (as ``scheduler.active_session_date``
    returns): both ``_rth_bounds`` and the gex_history readers require the object,
    so a string raises rather than degrading. Deliberately unlike ``build_matrix``,
    which ALSO carries an isoformat string — it needs one because its signal/alert
    count gate compares against the cached payloads' string ``date`` field. There is
    no such gate here, so only the returned field is stringified.

    Fully defensive about DATA, not about caller bugs: a DB-connect failure degrades
    to an empty-series payload with ``error`` set, and a per-symbol read failure
    yields no series for that symbol (the page names it as "no data yet")."""
    from services.options_svc import net_premium as np_mod
    from services.options_svc import scheduler as _sched

    now = now or _sched._market_now()
    # The time-axis charts show RTH only, so before the 08:30 open fall back to the
    # prior session — today has collected rows but none displayable. BOTH the read
    # and the crop must use this date, never the argument: between 08:00 and 08:30
    # they differ, and mixing them would read today's near-empty rows and crop them
    # to yesterday's window — a silently blank chart, no error.
    display_date = _display_session_date(now, session_date)
    bounds = _rth_bounds(display_date)
    # JSON-clean for the NetPremiumSnapshot contract.
    date_key = display_date.isoformat()

    try:
        gh = _matrix_gh()
        conn = gh.connect(read_only=True)
    except Exception:
        log.debug("build_net_premium: DB unavailable", exc_info=True)
        return {"session_date": date_key, "ts": _now_iso(), "series": {},
                "error": "net premium unavailable"}

    flow: dict = {}
    try:
        for sym in np_mod.source_symbols():
            # NOTE: _rth_only indexes r[0] unguarded, so a malformed row drops this
            # symbol's WHOLE series — unlike net_premium._project, which skips rows
            # individually (its "unreadable rows are skipped" promise therefore does
            # not hold for the composed pipeline: a dict-shaped row raises KeyError
            # here, before _project ever sees it; sqlite3.Row survives both). Same
            # behaviour the heatmap/Flow views already have; deliberately not changed
            # here (shared helper, wider blast radius).
            try:
                flow[sym] = _rth_only(
                    gh.load_flow_series(conn, sym, display_date), bounds)
            except Exception:
                log.debug("build_net_premium: read failed for %s", sym,
                          exc_info=True)
                flow[sym] = []
    finally:
        try:
            conn.close()
        except Exception:
            log.debug("build_net_premium conn close failed", exc_info=True)

    try:
        series = np_mod.build_series(flow)
    except Exception:
        log.debug("build_net_premium: build failed", exc_info=True)
        return {"session_date": date_key, "ts": _now_iso(), "series": {},
                "error": "net premium build failed"}
    return {"session_date": date_key, "ts": _now_iso(), "series": series,
            "error": None}


def _hedge_direction(pressure):
    """'buy' / 'sell' / None from a signed 0-DTE hedge pressure.

    Mirrors the engine's own labeling: a positive drift means the projected delta
    RISES, so a dealer short the book must BUY underlying to stay hedged."""
    if not isinstance(pressure, (int, float)) or isinstance(pressure, bool):
        return None
    return "buy" if pressure >= 0 else "sell"


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
        # 0-DTE charm drift — already on the DEX snapshot_summary, so Explain shows
        # the same numbers the chart and the briefings do. All None off a 0-DTE
        # book, and the infographic then omits the sentence entirely.
        hedge_pressure=_num(dx.get("hedge_pressure")),
        hedge_direction=_hedge_direction(dx.get("hedge_pressure")),
        projected_flip=_num(dx.get("projected_flip")),
        delta_flip=_num(dx.get("flip")),
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
    proj_text = ""
    try:
        from services.options_svc import scheduler as _sched
        proj_text = _projection_brief(eng, chain, spot, _sched._market_now())
    except Exception:
        proj_text = ""
    try:
        html = gamma_infographic.render_infographic(read, style=style, projection=proj_text)
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
        log.exception("gamma_symbol_options degraded → index trio fallback")
        return ["$SPX", "SPY", "QQQ"]


def _session_expected_move(chain):
    """A stable **1-day** expected move (``spot · ATM_IV · √(1/365)``) for the Analyze
    briefing — the time-of-day-independent 'expected move' a trader reads.

    The engine's ``calc_expected_move_from_chain`` is a 0-DTE *remaining-hours-to-close*
    EM: off-hours / on weekends ``hours_left`` clamps to 0.1h and near the close it
    decays to ~0, so it collapses to a misleadingly tiny number (e.g. SPX ≈ 3). This
    uses the same nearest-expiry ATM IV but a fixed 1-day horizon so the EM band on
    the ladder + the 'Exp. move' tile stay meaningful all day. Returns float or None
    (fully defensive — reuses the engine's static ATM-IV helpers)."""
    import math
    import datetime as _dt
    import gamma_tool as gt
    try:
        if not chain:
            return None
        spot = chain.get("underlyingPrice", 0) or 0
        if spot <= 0:
            return None
        today = _dt.datetime.now(gt.TZ).strftime("%Y-%m-%d")
        strikes = {}
        for mp in (chain.get("callExpDateMap", {}), chain.get("putExpDateMap", {})):
            key, _ = gt.GammaEngine._find_nearest_exp_key(mp, today)
            if key:
                for sk, contracts in mp[key].items():
                    strikes.setdefault(sk, []).extend(contracts)
        atm_iv = gt.GammaEngine._get_atm_iv(strikes, spot)  # percent, e.g. 12.5
        if not atm_iv or atm_iv <= 0:
            return None
        return round(spot * (atm_iv / 100.0) * math.sqrt(1.0 / 365.0), 2)
    except Exception:  # noqa: BLE001 — EM is best-effort; missing → None → '—'.
        return None


def _future_marks_ct(now):
    """15-min CT marks from the next quarter-hour through 15:00 CT (the close).
    Returns [] once ``now`` is at/after the close (off-hours hides the band)."""
    import datetime as _dt
    ct = _PROJ_CT_TZ
    now = now.astimezone(ct)
    close = now.replace(hour=15, minute=0, second=0, microsecond=0)
    if now >= close:
        return []
    q = (now.minute // 15 + 1) * 15
    mark = now.replace(minute=0, second=0, microsecond=0) + _dt.timedelta(minutes=q)
    out = []
    while mark <= close:
        out.append(mark)
        mark = mark + _dt.timedelta(minutes=15)
    return out


def _T_at(dte, mark_ct):
    """Engine-consistent time-to-expiry (years) at a CT wall-clock ``mark_ct``.
    Mirrors GammaEngine: T = (dte*24 + hours_to_close)/(365*24), floored 1e-6,
    hours measured to 15:00 CT (the 4pm ET cash close)."""
    hours_left = max(0.0, (15 - mark_ct.hour) + (0 - mark_ct.minute) / 60.0)
    return max((dte * 24 + hours_left) / (365 * 24), 1e-6)


def project_gex_grid(eng, chain, spot, now):
    """Flat-spot time-decay projection of net GEX per strike to the 4pm ET close.
    Re-prices standing OI at future 15-min marks with spot held flat: each contract's
    CURRENT GEX contribution (chain gamma) is scaled by bs_gamma(S,K,T',σ)/
    bs_gamma(S,K,T_now,σ) — 1.0 at T'=T_now so the seam is continuous; σ<=0 holds flat.
    ``now`` must be timezone-aware (callers pass an aware datetime); a naive ``now``
    degrades to an empty grid defensively. Pure + defensive: empty grid on any failure.
    Returns {"times":[HH:MM...], "grid":{strike_str:[net_t0...]}, "spot": spot}."""
    empty = {"times": [], "grid": {}, "spot": spot}
    try:
        if not chain or not spot or spot <= 0:
            return empty
        marks = _future_marks_ct(now)
        if not marks:
            return empty
        from options_calculator import bs_gamma
        ct = _PROJ_CT_TZ
        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})
        today = now.astimezone(ct).strftime("%Y-%m-%d")
        ck, cdte = eng._find_nearest_exp_key(call_map, today)
        pk, pdte = eng._find_nearest_exp_key(put_map, today)
        dtes = [d for d in (cdte, pdte) if d is not None]
        dte = min(dtes) if dtes else 0
        r = 0.045
        t_now = _T_at(dte, now.astimezone(ct))
        t_future = [_T_at(dte, m) for m in marks]
        grid = {}

        def _accumulate(exp_key, exp_map, sign):
            if not exp_key:
                return
            for strike_str, contracts in exp_map.get(exp_key, {}).items():
                strike = float(strike_str)
                key = str(strike)
                for c in contracts:
                    oi = c.get("openInterest", 0) or 0
                    gamma = c.get("gamma", 0) or 0
                    if oi <= 0 or gamma == 0:
                        continue
                    base = sign * gamma * oi * 100 * spot * spot * 0.01
                    iv = (c.get("volatility", 0) or 0) / 100.0
                    denom = bs_gamma(spot, strike, t_now, r, iv) if iv > 0 else 0.0
                    row = grid.setdefault(key, [0.0] * len(marks))
                    for i, tf in enumerate(t_future):
                        ratio = (bs_gamma(spot, strike, tf, r, iv) / denom) if (iv > 0 and denom > 0) else 1.0
                        row[i] += base * ratio

        _accumulate(ck, call_map, +1.0)
        _accumulate(pk, put_map, -1.0)
        return {"times": [m.strftime("%H:%M") for m in marks], "grid": grid, "spot": spot}
    except Exception:
        log.debug("project_gex_grid failed", exc_info=True)
        return empty


def project_em_cone(spot, atm_iv, marks, now):
    """Up/mid/down expected-move fan over the future marks (flat-spot midline).
    half_width(τ) = spot*atm_iv*sqrt(τ/365), τ = calendar days from ``now`` to the mark.
    ``now`` (and the marks) must be timezone-aware; a naive ``now`` degrades to empty
    lists defensively. Returns {"mid":[...], "up":[...], "down":[...]} (empty if unusable)."""
    import math
    out = {"mid": [], "up": [], "down": []}
    try:
        if not spot or spot <= 0 or not atm_iv or atm_iv <= 0 or not marks:
            return out
        for m in marks:
            tau_days = max(0.0, (m - now).total_seconds() / 86400.0)
            hw = spot * atm_iv * math.sqrt(tau_days / 365.0)
            out["mid"].append(spot)
            out["up"].append(spot + hw)
            out["down"].append(spot - hw)
        return out
    except Exception:
        log.debug("project_em_cone failed", exc_info=True)
        return {"mid": [], "up": [], "down": []}


def _net_zero_cross(col):
    """Strike where net GEX crosses zero (linear interp between sign-changing
    adjacent strikes). ``col`` maps strike(float)->net. None if no crossing."""
    items = sorted(col.items())
    for (k0, v0), (k1, v1) in zip(items, items[1:]):
        if v0 == 0:
            return k0
        if (v0 < 0) != (v1 < 0) and v1 != v0:
            return k0 + (k1 - k0) * (0 - v0) / (v1 - v0)
    return None


def _projection_brief(eng, chain, spot, now):
    """Compact factual 'into the close' summary of the forward GEX projection for
    ONE symbol — projected flip / call wall / put wall at the close + the EM band —
    used as prompt CONTEXT so the model writes each index's reader-first
    close_outlook. Returns '' when there's no session left / no projection."""
    try:
        proj = project_gex_grid(eng, chain, spot, now)
        if not proj.get("times") or not proj.get("grid"):
            return ""
        col = {}
        for k, vals in proj["grid"].items():
            if vals and isinstance(vals[-1], (int, float)):
                try:
                    col[float(k)] = vals[-1]
                except (TypeError, ValueError):
                    continue
        if not col or not isinstance(spot, (int, float)):
            return ""
        flip = _net_zero_cross(col)
        above = [(k, v) for k, v in col.items() if k > spot]
        below = [(k, v) for k, v in col.items() if k < spot]
        call_wall = max(above, key=lambda kv: kv[1])[0] if above else None
        put_wall = min(below, key=lambda kv: kv[1])[0] if below else None
        cone = project_em_cone(spot, atm_iv_from_chain(chain, spot),
                               _future_marks_ct(now), now)
        em_lo = cone["down"][-1] if cone.get("down") else None
        em_up = cone["up"][-1] if cone.get("up") else None
        close_lbl = proj["times"][-1]
        parts = [f"Into the close ({close_lbl} CT), holding spot flat"]
        if flip is not None:
            parts.append(f"projected gamma flip ~{flip:.0f}")
        if call_wall is not None:
            parts.append(f"call wall firms ~{call_wall:.0f}")
        if put_wall is not None:
            parts.append(f"put wall ~{put_wall:.0f}")
        if isinstance(em_lo, (int, float)) and isinstance(em_up, (int, float)):
            parts.append(f"expected-move band {em_lo:.0f}-{em_up:.0f}")
        return ": ".join([parts[0], "; ".join(parts[1:])]) + "." if len(parts) > 1 else parts[0] + "."
    except Exception:
        log.debug("_projection_brief failed", exc_info=True)
        return ""


# The dashboard's ONE single-name-equity category (see services/market_svc/
# symbols.py CATEGORY_ORDER) — everything else there is an index/ETF/currency/
# basket/macro read. This is an ALLOW-list, deliberately: a tile's category
# must equal this to ever be treated as "a stock". A renamed or newly added
# market_svc category is therefore excluded BY DEFAULT (fails closed) — no
# test needs updating to keep it that way, unlike a skip-list which fails open.
_MOVER_STOCK_CATEGORY = "Top 10"

# The options-matrix universe (gex_collector.collection_symbols() = the index
# base + the watchlist) always includes these regardless of the watchlist —
# they must never appear as "individual stock moves" even when the dashboard
# itself is empty/unreachable (proxy down), so this is a hardcoded backstop
# unioned into ``non_stock`` on top of whatever the dashboard classifies.
# Normalized keys (see ``_mover_key``).
#
# This is NOT a mirror of gex_collector.SYMBOLS and must never be "resynced" to
# it. It is an EXCLUDE-list of names that are never an individual stock move —
# broad index/ETF tickers only. gex_collector.SYMBOLS is a COLLECTION universe
# that has always been a superset ($NDX was in it when this comment still
# claimed a mirror) and since 2026-08-05 also holds 11 SPDR sectors and ten real
# mega-caps for the Net Prem view. Syncing the two would suppress NVDA / AAPL /
# TSLA from the briefing's notable movers — silently, since a suppressed mover
# just doesn't appear. Add a symbol here only when it is genuinely not a stock.
_MOVER_INDEX_FLOOR = frozenset({"SPX", "VIX", "SPY", "QQQ"})

_MOVER_LIMIT = 6


def _mover_key(symbol) -> str:
    """Normalize a display symbol to a dedup/lookup key: strip whitespace
    BEFORE stripping a leading '$' (a '" $SPY"' entry would otherwise keep its
    '$' and fail to match), then uppercase."""
    return str(symbol or "").strip().lstrip("$").upper()


def _notable_movers(dashboard, matrix, flow_alerts, limit: int = _MOVER_LIMIT) -> list:
    """The day's biggest individual-stock moves, for the briefing prompt.

    Merges two sources with DIFFERENT day-% semantics: the market dashboard's
    ``change_pct`` (true change vs the PRIOR CLOSE, from the quote — preferred
    when available) and the options matrix's ``day_pct`` (move since the
    ~08:00 CT session-collection start — an INTRADAY read, used as a fallback
    for the ~45 watchlist names not on the dashboard). Each row carries
    ``basis`` so the prompt can label which kind of move it is, honestly,
    rather than implying every number means the same thing.

    Individual-stock filtering is an ALLOW-list on ``_MOVER_STOCK_CATEGORY``
    (the dashboard's one single-name-equity category — see its comment). Every
    OTHER dashboard-classified symbol (indices, ETFs, currencies, crypto, …) is
    collected into ``non_stock`` and ALSO excludes that symbol from the MATRIX
    fallback below — the matrix carries no category of its own (it's just
    gex_collector's collection universe: the index base + the watchlist), so
    without this, ``$SPX``/``SPY``/``QQQ`` — dashboard-excluded but present in
    the matrix — would sail through unfiltered the moment they're not also a
    dashboard "Top 10" tile (which they never are). Because the dashboard can
    itself be empty/down, ``_MOVER_INDEX_FLOOR`` is unioned in as a hardcoded
    backstop so the matrix path is never left completely unfiltered.

    Flow-alert counts mean ONE thing per row, never mixed: a matrix row's own
    ``n_alerts`` (from the UNCAPPED per-symbol cooldown map — the accurate
    count) is preferred when the row carries it; dashboard-only symbols (which
    have no ``n_alerts``) fall back to counting ``cache:options:flow_alerts``'s
    ``alerts`` list, which is documented elsewhere in this module as 50-capped
    and can undercount on a busy day.

    Returns up to ``limit`` rows sorted by |move| desc. Pure over already-
    fetched cache payloads; fully defensive — any failure/garbage input → []."""
    out = {}
    try:
        limit = limit if isinstance(limit, int) else _MOVER_LIMIT

        # Flow-alert counts per symbol — the capped fallback source (see docstring).
        counts = {}
        for a in ((flow_alerts or {}).get("alerts") or []):
            if not isinstance(a, dict):
                continue
            sym = _mover_key(a.get("symbol"))
            if sym:
                counts[sym] = counts.get(sym, 0) + 1

        non_stock = set(_MOVER_INDEX_FLOOR)
        for cat in ((dashboard or {}).get("categories") or []):
            if not isinstance(cat, dict):
                continue
            cat_name = str(cat.get("category") or "")
            for t in (cat.get("tiles") or []):
                if not isinstance(t, dict):
                    continue
                sym = str(t.get("display") or "").strip()
                if not sym:
                    continue
                key = _mover_key(sym)
                if cat_name != _MOVER_STOCK_CATEGORY:
                    non_stock.add(key)     # index/ETF/macro — also filters the matrix pass below
                    continue
                if t.get("value_only") or t.get("basket"):
                    continue
                pct = t.get("change_pct")
                if not isinstance(pct, (int, float)) or isinstance(pct, bool):
                    continue
                out[key] = {"symbol": sym, "day_pct": round(float(pct), 2),
                            "last": t.get("last"), "basis": "prior_close",
                            "flow_alert_count": counts.get(key, 0)}

        for r in ((matrix or {}).get("rows") or []):
            if not isinstance(r, dict):
                continue
            sym = str(r.get("symbol") or "").strip()
            key = _mover_key(sym)
            if not sym or key in out or key in non_stock:
                continue
            pct = r.get("day_pct")
            if not isinstance(pct, (int, float)) or isinstance(pct, bool):
                continue
            n_alerts = r.get("n_alerts")
            has_n_alerts = isinstance(n_alerts, (int, float)) and not isinstance(n_alerts, bool)
            out[key] = {"symbol": sym, "day_pct": round(float(pct), 2),
                        "last": r.get("spot"), "basis": "session",
                        "flow_alert_count": int(n_alerts) if has_n_alerts else counts.get(key, 0)}

        rows = sorted(out.values(), key=lambda m: abs(m["day_pct"]), reverse=True)
        return rows[:max(0, limit)]
    except Exception:
        log.debug("_notable_movers failed", exc_info=True)
        return []


def _movers_prompt_block(movers) -> str:
    """Render movers for the model prompt, labeling each move's basis honestly
    (prior-close % vs since-the-open %) so the model never conflates the two."""
    if not movers:
        return ""
    try:
        lines = []
        for m in movers:
            basis = "vs prior close" if m.get("basis") == "prior_close" else "since the open"
            bit = f"{m['symbol']} {m['day_pct']:+.2f}% ({basis})"
            n = m.get("flow_alert_count")
            if n:
                bit += f" — {n} unusual-flow alert(s) today"
            lines.append(bit)
        return "NOTABLE INDIVIDUAL STOCK MOVES (code-computed, use verbatim):\n" + \
               "\n".join(f"- {x}" for x in lines)
    except Exception:
        log.debug("_movers_prompt_block failed", exc_info=True)
        return ""


# ── EOD session recap — what the market actually did today ──────────────────
def _session_path(series) -> dict:
    """open/high/low/close + day % from a flow series' spot column. {} if unusable."""
    try:
        spots = [r[1] for r in (series or [])
                 if len(r) > 1 and isinstance(r[1], (int, float))
                 and not isinstance(r[1], bool) and r[1] > 0]
        if not spots:
            return {}
        o, c = float(spots[0]), float(spots[-1])
        return {"open": round(o, 2), "high": round(max(spots), 2),
                "low": round(min(spots), 2), "close": round(c, 2),
                "day_pct": round((c - o) / o * 100.0, 2) if o else None}
    except Exception:
        return {}


def _level_verdict(path, level, name: str) -> str:
    """Plain-English: did price hold / break / reclaim / never test this level?"""
    try:
        if not path or not isinstance(level, (int, float)) or isinstance(level, bool):
            return ""
        hi, lo, close = path.get("high"), path.get("low"), path.get("close")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   for v in (hi, lo, close)):
            return ""
        if lo > level:
            return f"stayed entirely above the {name} ({level:g}) — never tested"
        if hi < level:
            return f"did not reach the {name} ({level:g}) all session"
        if close >= level:
            return f"traded below then reclaimed the {name} ({level:g}), closing above"
        return f"lost the {name} ({level:g}) and closed below it"
    except Exception:
        return ""


def _eod_recap_prompt_block(recap) -> str:
    """Render the per-index session recap for the model prompt. '' when empty."""
    if not recap:
        return ""
    out = []
    for sym, d in (recap or {}).items():
        try:
            p = (d or {}).get("path") or {}
            if not p:
                continue
            bits = [f"{sym}: open {p.get('open')} / high {p.get('high')} / "
                    f"low {p.get('low')} / close {p.get('close')} "
                    f"({(p.get('day_pct') or 0):+.2f}%)"]
            for key, name in (("flip", "gamma flip"), ("call_wall", "call wall"),
                              ("put_wall", "put wall")):
                v = _level_verdict(p, d.get(key), name)
                if v:
                    bits.append(f"  · {v}")
            out.append("\n".join(bits))
        except Exception:
            continue
    if not out:
        return ""
    return ("TODAY'S SESSION PATH + LEVELS (code-computed, use verbatim):\n"
            + "\n".join(out))


def _eod_session_recap(levels_by_sym) -> dict:
    """Per-index session recap: today's spot path + the CLOSING key levels.

    ``levels_by_sym`` = ``{symbol: {"flip", "call_wall", "put_wall"}}`` computed by the
    caller off the live chain (do NOT re-read the grid — the whole-session grid decode is
    a documented hotspot). The spot path comes from the cheap flow series. Defensive → {}.
    """
    try:
        import gex_history_db as gh

        from services.options_svc import scheduler as _sched
        d = _sched.active_session_date()
        out, conn = {}, None
        try:
            conn = gh.connect(read_only=True)
            for sym, lv in (levels_by_sym or {}).items():
                try:
                    series = gh.load_flow_series(conn, sym, d)
                    path = _session_path(series)
                    if not path:
                        continue
                    row = {"path": path}
                    row.update({k: (lv or {}).get(k)
                                for k in ("flip", "call_wall", "put_wall")})
                    if row.get("flip") is None:
                        # NOTE: latest_flip is (conn, symbol, view="gex", date=None) —
                        # the date MUST be a keyword or it silently lands in `view`.
                        row["flip"] = gh.latest_flip(conn, sym, date=d)
                    out[sym] = row
                except Exception:
                    continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        return out
    except Exception:
        log.debug("_eod_session_recap failed", exc_info=True)
        return {}


# ── Live macro news research (Task 2) — phase 1 of a briefing ────────────────
# A SEPARATE Claude call from the forced-tool render phase (gamma_analyze's
# `submit_analysis`): the API cannot fire a server-side web search AND force a
# client tool_choice in the same turn (see docstring below), so news research
# happens first, in its own call, and its output is folded into the render
# phase's prompt as plain text context by the caller (Task 4/6 — NOT this task).
_NEWS_MAX_TOKENS = 700
_NEWS_MAX_LINES = 6
# Web search is GA (no beta header/`betas=[...]` needed). `allowed_callers` is
# set EXPLICITLY to ["direct"] because on tool version 20260209+ it otherwise
# defaults to "code_execution" — we call the tool directly from a plain chat
# turn, not from code-execution, so leaving it unset can mean the search
# silently never fires (the model would then answer from memory instead).
_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search",
                    "max_uses": 3, "allowed_callers": ["direct"]}
# The news phase runs on Sonnet 4.6 — the documented model pairing for the
# web_search_20260209 tool version. Deliberately NOT `_ANALYZE_MODEL`
# (claude-sonnet-5, whose web-search support is undocumented) and NOT an Opus
# model (explicit user directive — ask before changing this).
_NEWS_MODEL = "claude-sonnet-4-6"
# A search that FAILS still returns an HTTP 200 — the error arrives as a block
# of this type inside the response, and the model then goes on to answer from
# its training memory. A bare try/except cannot catch this; it must be
# detected by scanning the response blocks (see `_research_news`).
_NEWS_ERROR_BLOCK = "web_search_tool_result"
# Genuine search-INFRASTRUCTURE failures — nothing useful came back, so the whole
# result is discarded (see `_research_news` docstring). `max_uses_exceeded` is
# deliberately EXCLUDED here: `_WEB_SEARCH_TOOL["max_uses"]` above is OUR OWN
# configured cap, so hitting it means some searches already SUCCEEDED before the
# cap bit — that is not a failure, and discarding the research already gathered
# would be throwing away good news over our own self-imposed budget. Any other/
# unknown error code also passes through un-aborted (this list is exhaustive per
# the reviewed spec, not a "when in doubt" allowlist).
_NEWS_ABORT_ERROR_CODES = frozenset({
    "too_many_requests", "unavailable", "invalid_tool_input",
    "query_too_long", "request_too_large",
})

# Small, deliberately narrow prefix blocklist for the preamble/summary sentences
# the model sometimes emits despite being told not to — checked at the START of a
# line only (see `_is_meta_line`), so a real driver mentioning e.g. "Fed: held
# rates steady" is never dropped just for containing a colon.
_NEWS_META_PREFIXES = ("note:", "summary:", "disclaimer:", "sources:")

_NEWS_SYSTEM = (
    "You are a market-news researcher. Search the web for the concrete macro and "
    "earnings news that actually moved US equities TODAY (Fed/rates, CPI/PPI/jobs, "
    "major earnings, geopolitics). The user message states today's date — treat it "
    "as authoritative and IGNORE any search result that is not from that session. "
    "Reply with a short plain list, one driver per line, prefixed by '- ', each "
    "naming the event and its market effect in ONE clause. Do NOT include a preamble "
    "sentence, a section header, a summary line, markdown emphasis (no **bold** or "
    "__underline__), citations, or disclaimers — ONLY the bulleted driver lines. If "
    "asked for the next session, also list tomorrow's scheduled economic releases and "
    "notable earnings, each as its own bulleted line."
)


def _is_meta_line(line: str) -> bool:
    """True for a preamble/summary sentence or a bare section header — junk that
    sometimes slips past the system prompt and must not reach the briefing prompt
    as if it were a driver (see `_research_news`).

    Two checks, both anchored to the START of the line so a legitimate driver's
    tail (which may itself contain a colon or an acronym) is never mistaken for
    junk: (1) a small explicit prefix blocklist (`_NEWS_META_PREFIXES`,
    case-insensitive), and (2) an ALL-CAPS section-header shape — its first three
    alphabetic words are all uppercase (e.g. "TODAY'S TAPE DRIVERS — Friday July
    25 ..."); a single leading all-caps ticker/acronym in an otherwise normal-case
    sentence does not trip this (fewer than 3 leading uppercase words)."""
    s = line.strip()
    if not s:
        return True
    if s.lower().startswith(_NEWS_META_PREFIXES):
        return True
    lead = []
    for w in s.split():
        letters = "".join(c for c in w if c.isalpha())
        if not letters:
            break
        lead.append(letters)
        if len(lead) >= 3:
            break
    return len(lead) >= 3 and all(w.isupper() for w in lead)


def _strip_markdown_emphasis(s: str) -> str:
    """Drop markdown bold/underline emphasis markers — the model sometimes adds
    them despite the system prompt banning them; only plain text should reach the
    briefing prompt."""
    return s.replace("**", "").replace("__", "")


def _extract_driver_lines(text: str) -> list:
    """One text block → the lines that are ACTUALLY bulleted driver lines.

    A line only counts as a driver if it visibly BEGINS with a bullet marker
    ('-'/'•'/'*') — the model's preamble/summary/header sentences are plain
    paragraph text, not bulleted, so this alone filters most of them;
    `_is_meta_line` is a second, independent pass for the rest (e.g. a
    bulleted header). Markdown emphasis is stripped from what's kept."""
    out = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s or s[0] not in "-•*":
            continue
        s = _strip_markdown_emphasis(s.lstrip("-•* ").strip()).strip()
        if s and not _is_meta_line(s):
            out.append(s)
    return out


def _research_news(label: str, context: str = "", client=None, eod: bool = False,
                   now=None) -> list:
    """Search the web for the day's macro drivers → a short list of driver lines.

    Phase 1 of a briefing: a SEPARATE Claude call carrying the web-search server
    tool. It must be separate from the render phase (``gamma_analyze``'s forced
    ``submit_analysis`` tool_choice) because the API cannot fire a server-side
    search AND force a client tool in the same turn — if the model tries both in
    one parallel group, the API returns ``stop_reason: "tool_use"`` and defers the
    search instead of running it. When ``eod`` is set, also asks for the NEXT
    session's scheduled releases/earnings (for the end-of-day retrospective).

    ``now`` defaults (lazily, same idiom as ``gamma_analyze``'s forward-projection
    context) to ``scheduler._market_now()`` — the CT-aware current time — and is
    stated explicitly in the prompt. This is load-bearing, not decorative: a live
    probe caught the model self-contradicting ("results through Friday July 24"
    while writing "TODAY'S ... Friday July 25") when the date was left implicit,
    which for an end-of-day retrospective can misdate the session or let stale
    news pass as today's. Injectable for deterministic tests.

    Fully guarded — returns ``[]`` on no key / unsupported tool / API error / a
    GENUINE in-response search error (one of ``_NEWS_ABORT_ERROR_CODES`` — a search
    infrastructure failure, meaning nothing useful came back) / an empty reply, so
    a briefing always renders on app data alone. A search that hits our OWN
    ``max_uses`` cap (``error_code == "max_uses_exceeded"``) is NOT one of those —
    prior searches in the same call may have already succeeded, so whatever driver
    lines were gathered are kept rather than discarded over our own budget.

    On a genuinely FAILED search the API still replies 200 and the model answers
    from memory; publishing that text would put fabricated headlines into an
    automated market briefing, which is strictly worse than no news at all — so an
    abort-worthy error block anywhere in the reply discards the WHOLE result
    rather than returning the model's memory-text alongside/instead of it.

    ``client`` is injected in tests; in production it is built from the resolved
    API key via ``_make_analyze_client``."""
    client = client or _make_analyze_client()
    if client is None:
        return []
    if now is None:
        try:
            from services.options_svc import scheduler as _sched
            now = _sched._market_now()
        except Exception:
            now = None
    ask = ("Today's US session just closed. " if eod else "The US session is in progress. ")
    if now is not None:
        try:
            ask += f"Today is {now:%A, %B %d, %Y} (US Central Time). "
        except Exception:
            log.debug("news research (%s): could not format `now`", label, exc_info=True)
    ask += "What news drove the tape today?"
    if eod:
        ask += (" Also list the scheduled economic releases and notable earnings for the "
                "NEXT trading session.")
    if context:
        ask += f"\n\nMarket context (already computed, do not re-derive):\n{context}"
    try:
        _count_anthropic_call()
        resp = client.messages.create(
            model=_NEWS_MODEL,
            max_tokens=_NEWS_MAX_TOKENS,
            system=_NEWS_SYSTEM,
            tools=[_WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": ask}],
        )
    except Exception:
        log.warning("news research (%s) failed — briefing degrades to app data only",
                    label, exc_info=True)
        return []
    lines = []
    try:
        blocks = getattr(resp, "content", None) or []
        # A GENUINELY failed search still returns HTTP 200: an error block, then
        # the model answering from memory. Publishing that text would put
        # fabricated headlines in the briefing — strictly worse than no news — so
        # abort the WHOLE result. `max_uses_exceeded` (hitting our own cap after
        # some searches already succeeded) is deliberately NOT one of these; see
        # `_NEWS_ABORT_ERROR_CODES`.
        for b in blocks:
            if getattr(b, "type", None) != _NEWS_ERROR_BLOCK:
                continue
            c = getattr(b, "content", None)
            code = c.get("error_code") if isinstance(c, dict) else None
            if code in _NEWS_ABORT_ERROR_CODES:
                log.warning("news research (%s): web search errored (%s) — no news",
                            label, code)
                return []
            if code:
                log.debug("news research (%s): non-fatal search status (%s) — "
                         "keeping whatever else was gathered", label, code)
        for b in blocks:
            if getattr(b, "type", None) != "text":
                continue          # skip server_tool_use / web_search_tool_result blocks
            lines.extend(_extract_driver_lines(getattr(b, "text", "") or ""))
    except Exception:
        log.debug("news parse failed", exc_info=True)
        return []
    return lines[:_NEWS_MAX_LINES]


def _news_prompt_block(news) -> str:
    """Render researched news for the render-phase prompt, or '' when there's none
    (a briefing must still render cleanly on app data alone)."""
    if not news:
        return ""
    return ("MACRO DRIVERS / NEWS (searched live — attribute the tape to these):\n"
            + "\n".join(f"- {n}" for n in news))


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
    # 1-day expected move (not the engine's 0-DTE remaining-hours EM, which collapses
    # off-hours / at the close) so the briefing's EM-derived fields stay meaningful.
    em = _session_expected_move(chain)
    dte = eng._last_dte

    def bd(snap, view):
        if not snap:
            return None
        return gt.build_analysis_dict(snap, view, symbol, dte,
                                      expected_move=em, grouping=1, chain=chain)

    return {"gex": bd(gex, "gex"), "charm": bd(charm, "charm"),
            "dex": bd(dex, "dex"), "vanna": bd(vanna, "vanna")}


# ── Gamma Analyze (Claude-written intraday briefing → standalone HTML tab) ───
# The Analyze button bundles the live $SPX/SPY/QQQ GEX/Charm/DEX/Vanna data into a
# prompt, runs it through Claude (Sonnet 5), and renders the model's analysis as a
# self-contained dark HTML document the GUI serves in a NEW browser tab — mirroring
# the Explain flow. The Anthropic call is cost-bounded on purpose: Sonnet 5 with
# thinking DISABLED + a modest max_tokens, so each click costs ~the "typical" 1-page
# summary the user signed off on (no runaway thinking tokens). The ``anthropic``
# import is LAZY (only when a key resolves). EVERY failure surface (no chains / no key
# / API error / empty reply) degrades to a readable HTML page so the tab always shows
# something — never a silent no-op.
_ANALYZE_MODEL = "claude-sonnet-5"
# Raised 1500 -> 2600 when macro_drivers + movers were added to _ANALYZE_TOOL.
# A live probe showed the enriched reply hitting the 1500 cap EXACTLY and
# truncating `indices` away entirely (n=0) — the briefing silently lost every
# ladder, tile, what_if and close_outlook, i.e. all of its per-index content.
# This is a CAP, not a spend: billing is on actual output tokens, and a good run
# measures ~1500-1800. Do not trim it back without re-running a live probe.
_ANALYZE_MAX_TOKENS = 2600
_ANALYZE_SYSTEM = (
    "You are an options-market analyst. From the structured GEX / Charm / DEX / Vanna "
    "data provided for $SPX, SPY and QQQ, call the submit_analysis tool exactly once. "
    "Copy the EXACT computed levels from the data into each index entry — gamma flip, "
    "call wall, put wall, max pain, expected move (in points), put/call ratio — do not "
    "estimate or invent numbers, and omit a field only if it isn't present in the data. "
    "Set bias from -100 (max bearish) to +100 (max bullish), 0 = neutral. For EACH "
    "index also fill 'what_if' with three short plain-English scenarios for the rest "
    "of the session, specific to that index: 'rally' (an upside path), 'selloff' (a "
    "downside path) and 'chop' (a sideways/range path) — one sentence each. Fill 'why' "
    "with 1-2 plain sentences on why the tape is acting this way (macro context + the "
    "session's path so far). "
    "When MACRO DRIVERS are supplied below, use them to ground 'why' in what is "
    "actually moving the tape today (they are researched facts — prefer them over "
    "your own recollection), and list them in 'macro_drivers'. When NOTABLE "
    "INDIVIDUAL STOCK MOVES are supplied, surface them in 'movers', copying the "
    "computed percentages exactly — never invent a move or a number. "
    "FRAME EVERYTHING FROM THE TRADER'S PERSPECTIVE — write what the READER should DO "
    "and expect, NOT what dealers are doing. Prefer 'you' and concrete actions ('fade "
    "the call wall', 'buy dips toward the put wall', 'lean long / stay long above the "
    "flip', 'respect the supply', 'trade with the trend', 'tighten stops') over "
    "describing dealer hedging mechanics; you may mention the mechanism briefly, but "
    "LEAD with the action. Keep the headline and per-index notes terse and the "
    "narrative to 2-3 sentences. Do NOT include any disclaimers, risk warnings, 'not "
    "financial advice' notes, or boilerplate — only the analysis."
)

# Forced tool the model fills with the structured analysis (the infographic's data
# source). tool_choice (in gamma_analyze) forces this single tool call, so the reply
# is one ``submit_analysis`` tool_use block we render — the model never free-writes.
_ANALYZE_TOOL = {
    "name": "submit_analysis",
    "description": ("Return the structured intraday options-flow analysis + trader "
                    "playbook for $SPX, SPY and QQQ. The app renders it as an "
                    "infographic, so copy the exact computed levels from the provided "
                    "data rather than estimating. Frame the prose as what the reader "
                    "should DO, not what dealers are doing."),
    "input_schema": {
        "type": "object",
        "properties": {
            "regime": {"type": "string",
                       "description": "Short overall regime label framed for the trader, "
                                      "e.g. 'Trend day — stay long above the flip' or "
                                      "'Pinned / range — fade the walls'."},
            "bias": {"type": "number",
                     "description": "Net directional bias, -100 (bearish) to +100 (bullish)."},
            "bias_label": {"type": "string",
                           "description": "Two-or-three-word bias label, e.g. 'Mildly bearish'."},
            "headline": {"type": "string",
                         "description": "One-sentence headline telling the reader what to "
                                        "do / expect over the next few hours (an action, "
                                        "not a description of dealer hedging)."},
            "narrative": {"type": "string",
                          "description": "2-3 sentence plain read of what the reader should "
                                         "do and expect, and why — lead with the action "
                                         "('lean long above the flip', 'fade the call "
                                         "wall'), mention the mechanism only briefly."},
            "why": {"type": "string",
                    "description": "1-2 plain sentences: why the tape is acting this way "
                                   "(macro context + the session's path so far)."},
            # Optional enrichment — deliberately NOT in `required`, so a terser or
            # older model reply still parses and renders exactly as before.
            "macro_drivers": {
                "type": "array", "items": {"type": "string"},
                "description": "The day's macro drivers, one short line each, taken "
                               "from the supplied research. Omit if none supplied.",
            },
            "movers": {
                "type": "array",
                "description": "Notable individual stock moves, from the supplied "
                               "list. Omit if none supplied.",
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "move": {"type": "string",
                                 "description": "The move, e.g. '+6.5%'. Copy exactly."},
                        "note": {"type": "string",
                                 "description": "A few words on why it moved."},
                    },
                    "required": ["symbol"],
                },
            },
            "indices": {
                "type": "array",
                "description": "One entry per index, in order $SPX, SPY, QQQ.",
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "spot": {"type": "number"},
                        "gamma_flip": {"type": "number"},
                        "call_wall": {"type": "number"},
                        "put_wall": {"type": "number"},
                        "max_pain": {"type": "number"},
                        "expected_move": {"type": "number",
                                          "description": "Expected move in points (1 std dev), not percent."},
                        "pc_ratio": {"type": "number", "description": "Put/call ratio."},
                        "note": {"type": "string",
                                 "description": "Terse one-line read for this index, framed "
                                                "as what to do (e.g. 'buy dips toward 5900, "
                                                "trim into the 5950 call wall')."},
                        "close_outlook": {"type": "string",
                            "description": "One line on what to DO as the day decays "
                                "into the close, using the provided FORWARD PROJECTION "
                                "(e.g. 'into the close the call wall firms at 5950 — trim "
                                "rallies into it; stay defensive below the flip'). "
                                "Reader-first action, NOT dealer mechanics."},
                        "what_if": {
                            "type": "object",
                            "description": "Three short plain-English scenarios for this "
                                           "index for the rest of the session, each telling "
                                           "the reader how to play it.",
                            "properties": {
                                "rally": {"type": "string",
                                          "description": "Upside path — what to do if it "
                                                         "rallies (e.g. 'ride it, trim into "
                                                         "the call wall')."},
                                "selloff": {"type": "string",
                                            "description": "Downside path — what to do if it "
                                                           "sells off (e.g. 'buy the dip at "
                                                           "the put wall, stops below')."},
                                "chop": {"type": "string",
                                         "description": "Sideways/range path — what to do if "
                                                        "it chops (e.g. 'fade the edges, "
                                                        "avoid the middle')."},
                            },
                        },
                    },
                    "required": ["symbol"],
                },
            },
        },
        "required": ["regime", "bias", "headline", "narrative", "why", "indices"],
    },
}

_ANALYZE_CSS = """
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; background:#0c0f15; color:#e6e6e6;
    font-family:"Segoe UI",system-ui,-apple-system,sans-serif; line-height:1.55; }
  .ga { max-width:860px; margin:0 auto; padding:34px 30px 60px; }
  .ga-title { font-size:1.6rem; font-weight:700; color:#90caf9; letter-spacing:.3px; }
  .ga-sub { color:#8a93a3; font-size:.92rem; margin:4px 0 22px; }
  .ga-body h1 { font-size:1.35rem; color:#90caf9; margin:1.4em 0 .4em; }
  .ga-body h2 { font-size:1.12rem; color:#ffd54f; margin:1.3em 0 .4em;
    border-bottom:1px solid #222a36; padding-bottom:4px; }
  .ga-body h3 { font-size:1.0rem; color:#ffb74d; margin:1.1em 0 .3em; }
  .ga-body p { margin:.5em 0; }
  .ga-body ul, .ga-body ol { margin:.4em 0 .8em; padding-left:1.4em; }
  .ga-body li { margin:.25em 0; }
  .ga-body strong { color:#fff; }
  .ga-body code { background:#1b222e; color:#9ad0ff; padding:1px 5px;
    border-radius:4px; font-size:.9em; }
  .ga-body em { color:#c7cdd6; }
  .ga-body hr { border:0; border-top:1px solid #222a36; margin:1.4em 0; }
  .ga-body table { border-collapse:collapse; margin:.6em 0; }
  .ga-body th, .ga-body td { border:1px solid #222a36; padding:5px 10px; }
  .ga-banner { display:flex; align-items:center; justify-content:space-between;
    gap:16px; flex-wrap:wrap; background:#101a30; border:1px solid #213152;
    border-radius:12px; padding:12px 16px; margin:6px 0 14px; }
  .ga-regime { color:#eaf0fb; font-size:1.05rem; font-weight:600; }
  .bias-wrap { display:flex; align-items:center; gap:8px; font-size:.78rem; color:#8a93a3; }
  .bias-track { position:relative; width:160px; height:8px; border-radius:4px; background:#243353; }
  .bias-mid { position:absolute; left:50%; top:0; width:1px; height:8px; background:#3b4a6b; }
  .bias-marker { position:absolute; top:-3px; width:4px; height:14px; border-radius:2px; transform:translateX(-50%); }
  .bias-lab { color:#cdd8ee; font-size:.8rem; margin-left:2px; }
  .ga-headline { color:#dce6f7; font-size:1rem; margin:0 0 14px; line-height:1.5; }
  .idx-card { background:#101a30; border:1px solid #213152; border-radius:12px;
    padding:12px 14px; margin:0 0 12px; }
  .idx-head { color:#ffd54f; font-size:1rem; font-weight:600; margin-bottom:6px; }
  .idx-body { display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  .idx-right { flex:1; min-width:240px; }
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(80px,1fr)); gap:8px; }
  .tile { background:#0c1426; border-radius:8px; padding:6px 9px; }
  .tile-l { color:#8a93a3; font-size:.72rem; }
  .tile-v { color:#eaf0fb; font-size:1.05rem; font-weight:600; }
  .idx-note { color:#9fb0d0; font-size:.85rem; margin-top:8px; line-height:1.45; }
  .ladder { flex:0 0 auto; }
  .ladder-empty { color:#8a93a3; font-size:.8rem; }
  .ga-narr { background:#101a30; border-left:3px solid #3b82f6; padding:10px 12px;
    color:#dce6f7; font-size:.95rem; line-height:1.55; margin:0 0 14px; }
  .ga-narr p { margin:.3em 0; }
  .idx-whatif { margin-top:10px; padding-top:10px; border-top:1px solid #1c2842; }
  .wf-head { color:#8a93a3; font-size:.72rem; text-transform:uppercase;
    letter-spacing:.04em; margin-bottom:5px; }
  .wf-row { display:flex; gap:8px; align-items:baseline; margin:3px 0; }
  .wf-tag { flex:0 0 64px; font-size:.8rem; font-weight:600; }
  .wf-txt { color:#cdd8ee; font-size:.88rem; line-height:1.45; }
  .ga-why { background:#101a30; border:1px solid #213152; border-radius:12px;
    padding:12px 14px; margin-top:14px; }
  .ga-why-h { color:#90caf9; font-size:1rem; font-weight:600; margin-bottom:6px; }
  .ga-why-b { color:#dce6f7; font-size:.95rem; line-height:1.55; }
  .ga-why-b p { margin:.3em 0; }
  .idx-close { color:#c7d2e8; font-size:.86rem; margin-top:8px; padding-top:8px;
    border-top:1px solid #1c2842; line-height:1.45; }
  .idx-close-h { color:#8a93a3; text-transform:uppercase; font-size:.72rem;
    letter-spacing:.04em; margin-right:6px; }
"""


def _anthropic_api_key():
    """Anthropic API key, or ``None`` (never raises). ``ANTHROPIC_API_KEY`` env →
    gitignored ``shared/anthropic_key.txt`` — same resolution order driver_svc uses
    (kept local so options_svc doesn't import driver_svc)."""
    import os
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    try:
        from repo_paths import SHARED_DIR
        p = SHARED_DIR / "anthropic_key.txt"
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001 — a missing/unreadable file is non-fatal.
        log.debug("reading anthropic_key.txt failed", exc_info=True)
    return None


def _make_analyze_client():
    """A real ``anthropic.Anthropic`` client, or ``None`` if no key / SDK (never
    raises). LAZY import so the test suite + service import without the SDK.

    Also ``None`` in an environment whose profile clears ``allow_claude`` (dev) —
    checked FIRST, so a suppressed environment never even reads the key. That is
    deliberately not a new code path: every caller already handles ``None``
    because it is what a box with no configured key returns, so dev lands on the
    same explanatory "no API key" briefing page production already renders."""
    if not ENV_FLAGS.get("allow_claude", True):
        return None
    key = _anthropic_api_key()
    if not key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except Exception:  # noqa: BLE001
        return None


def _analyze_md_to_html(text: str) -> str:
    """Markdown analysis → HTML fragment (defensive — escaped ``<pre>`` fallback)."""
    try:
        import markdown as _md
        return _md.markdown(text or "", extensions=["extra", "sane_lists"])
    except Exception:  # noqa: BLE001
        import html as _html
        return f"<pre>{_html.escape(text or '')}</pre>"


def _analyze_doc(body_html: str,
                 subtitle: str = "Dealer-positioning briefing · $SPX / SPY / QQQ",
                 title: str = "Gamma Analysis") -> str:
    """Wrap an HTML body fragment in a standalone dark document (Explain aesthetic).

    ``title`` defaults to the intraday briefing's name so the three intraday slots
    are unchanged; the EOD retrospective passes its own, because a document headed
    "Gamma Analysis" misnames what the reader is looking at (the same mistake the
    Market Snapshot push had to correct)."""
    import html as _h
    t = _h.escape(title)
    return ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{t} — $SPX / SPY / QQQ</title>"
            f"<style>{_ANALYZE_CSS}</style></head><body><div class=\"ga\">"
            f"<div class=\"ga-title\">{t}</div>"
            f"<div class=\"ga-sub\">{subtitle}</div>"
            f"<div class=\"ga-body\">{body_html}</div></div></body></html>")


def _num(x):
    """``float(x)`` if finite, else None (never raises)."""
    import math
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _fmt_num(val, dec=0) -> str:
    """Thousands-grouped number to ``dec`` places, or '—' when missing."""
    v = _num(val)
    if v is None:
        return "—"
    return f"{v:,.{dec}f}"


def _parse_analysis(inp) -> dict | None:
    """Normalize the model's ``submit_analysis`` tool input → render-ready dict.

    Total over adversarial input (mirrors decider.parse_decision): numbers coerced
    via ``_num`` (bad/missing → None → rendered '—'), strings stripped. Returns None
    only when there's nothing renderable (so the caller degrades to a message)."""
    if not isinstance(inp, dict):
        return None
    out = {
        "regime": str(inp.get("regime") or "").strip(),
        "bias": _num(inp.get("bias")),
        "bias_label": str(inp.get("bias_label") or "").strip(),
        "headline": str(inp.get("headline") or "").strip(),
        "narrative": str(inp.get("narrative") or "").strip(),
        "why": str(inp.get("why") or "").strip(),
        # Additive enrichment; absent on an older reply → empty, rendered as nothing.
        "macro_drivers": _coerce_drivers(inp.get("macro_drivers")),
        "movers": _coerce_movers(inp.get("movers")),
        "indices": [],
    }
    for it in (inp.get("indices") or []):
        if not isinstance(it, dict):
            continue
        wf = it.get("what_if") if isinstance(it.get("what_if"), dict) else {}
        out["indices"].append({
            "symbol": str(it.get("symbol") or "").strip(),
            "spot": _num(it.get("spot")),
            "gamma_flip": _num(it.get("gamma_flip")),
            "call_wall": _num(it.get("call_wall")),
            "put_wall": _num(it.get("put_wall")),
            "max_pain": _num(it.get("max_pain")),
            "expected_move": _num(it.get("expected_move")),
            "pc_ratio": _num(it.get("pc_ratio")),
            "note": str(it.get("note") or "").strip(),
            "close_outlook": str(it.get("close_outlook") or "").strip(),
            "what_if": {
                "rally": str(wf.get("rally") or "").strip(),
                "selloff": str(wf.get("selloff") or "").strip(),
                "chop": str(wf.get("chop") or "").strip(),
            },
        })
    if not out["indices"] and not out["headline"] and not out["narrative"]:
        return None
    return out


def _bias_meter_html(bias, label) -> str:
    """Horizontal bias meter (−100…+100) with a sign-colored marker + label."""
    import html as _h
    b = _num(bias)
    pct = 50.0 if b is None else max(0.0, min(100.0, (b + 100.0) / 2.0))
    color = "#9aa3bd" if (b is None or abs(b) <= 5) else ("#34d399" if b > 0 else "#f87171")
    lab = label or ("Neutral" if b is None else
                    ("Bullish" if b > 5 else "Bearish" if b < -5 else "Neutral"))
    return ('<div class="bias-wrap"><span>Bearish</span>'
            '<div class="bias-track"><div class="bias-mid"></div>'
            f'<div class="bias-marker" style="left:{pct:.0f}%;background:{color}"></div></div>'
            f'<span>Bullish</span><span class="bias-lab">{_h.escape(lab)}</span></div>')


def _metric_tiles_html(idx) -> str:
    """Per-index metric tiles (spot / flip / walls / max pain / EM / P-C)."""
    rows = [
        ("Spot", idx.get("spot"), 0, None),
        ("Gamma flip", idx.get("gamma_flip"), 0, "#ffd54f"),
        ("Call wall", idx.get("call_wall"), 0, "#34d399"),
        ("Put wall", idx.get("put_wall"), 0, "#f87171"),
        ("Max pain", idx.get("max_pain"), 0, None),
        ("Exp. move", idx.get("expected_move"), 1, None),
        ("Put/Call", idx.get("pc_ratio"), 2, None),
    ]
    cells = []
    for lbl, val, dec, color in rows:
        v = _fmt_num(val, dec)
        style = f' style="color:{color}"' if (color and v != "—") else ""
        cells.append(f'<div class="tile"><div class="tile-l">{lbl}</div>'
                     f'<div class="tile-v"{style}>{v}</div></div>')
    return f'<div class="tiles">{"".join(cells)}</div>'


def _ladder_svg(idx) -> str:
    """Vertical price-level ladder: spot vs flip / call+put walls / expected-move band."""
    import html as _h
    spot = _num(idx.get("spot"))
    flip = _num(idx.get("gamma_flip"))
    cw, pw = _num(idx.get("call_wall")), _num(idx.get("put_wall"))
    em = _num(idx.get("expected_move"))
    emu = (spot + em) if (spot is not None and em) else None
    eml = (spot - em) if (spot is not None and em) else None
    pts = []  # (price, label, color, kind) — kind: dot|dash|tick|faint
    if cw is not None:
        pts.append((cw, "Call wall", "#34d399", "tick"))
    if emu is not None:
        pts.append((emu, "EM upper", "#7f8db0", "faint"))
    if spot is not None:
        pts.append((spot, "Spot", "#f5f5f5", "dot"))
    if flip is not None:
        pts.append((flip, "Flip", "#ffd54f", "dash"))
    if eml is not None:
        pts.append((eml, "EM lower", "#7f8db0", "faint"))
    if pw is not None:
        pts.append((pw, "Put wall", "#f87171", "tick"))
    if not pts:
        return '<div class="ladder-empty">no levels</div>'
    prices = [p for p, _, _, _ in pts]
    pmin, pmax = min(prices), max(prices)
    if pmax <= pmin:
        pmax = pmin + 1.0
    W, H, top, bot, ax = 250, 190, 20, 170, 30

    def yof(p):
        return bot - (p - pmin) / (pmax - pmin) * (bot - top)

    svg = [f'<svg viewBox="0 0 {W} {H}" width="250" class="ladder" role="img" '
           'aria-label="price-level ladder">']
    if emu is not None and eml is not None:
        yt, yb = yof(emu), yof(eml)
        svg.append(f'<rect x="{ax - 4}" y="{yt:.0f}" width="8" height="{(yb - yt):.0f}" '
                   'fill="#1f3a5f" opacity="0.45"/>')
    svg.append(f'<line x1="{ax}" y1="{top}" x2="{ax}" y2="{bot}" stroke="#2b3b5a" stroke-width="2"/>')
    # Label de-collision: markers stay at their true price y, but the text labels are
    # pushed apart top→bottom to a minimum gap so clustered levels (spot/flip/walls
    # within a few points) don't overlap into an unreadable smear; a thin connector
    # links a nudged label back to its true marker.
    min_gap, label_y, prev = 15, {}, None
    for i in sorted(range(len(pts)), key=lambda j: yof(pts[j][0])):
        my = yof(pts[i][0])
        ly = my if prev is None else max(my, prev + min_gap)
        label_y[i] = min(ly, H - 6)
        prev = label_y[i]
    for i, (price, lbl, color, kind) in enumerate(pts):
        y, ly = yof(price), label_y[i]
        if kind == "dot":
            svg.append(f'<circle cx="{ax}" cy="{y:.0f}" r="5" fill="{color}"/>')
        elif kind == "dash":
            svg.append(f'<line x1="{ax - 8}" y1="{y:.0f}" x2="{ax + 8}" y2="{y:.0f}" '
                       f'stroke="{color}" stroke-width="2" stroke-dasharray="4 3"/>')
        elif kind == "faint":
            svg.append(f'<line x1="{ax - 5}" y1="{y:.0f}" x2="{ax + 5}" y2="{y:.0f}" '
                       f'stroke="{color}" stroke-width="1" stroke-dasharray="2 2"/>')
        else:
            svg.append(f'<circle cx="{ax}" cy="{y:.0f}" r="3.5" fill="{color}"/>')
        if abs(ly - y) > 3:
            svg.append(f'<line x1="{ax + 8}" y1="{y:.0f}" x2="{ax + 11}" y2="{ly:.0f}" '
                       'stroke="#3b4a6b" stroke-width="1"/>')
        dec = 1 if lbl.startswith("EM") else 0
        lc = "#8a93a3" if kind == "faint" else color
        svg.append(f'<text x="{ax + 13}" y="{ly + 4:.0f}" fill="{lc}" font-size="12" '
                   f'font-family="inherit">{_h.escape(lbl)} {price:,.{dec}f}</text>')
    svg.append('</svg>')
    return "".join(svg)


def _whatif_html(idx) -> str:
    """Per-index 'what if' scenarios: rally (up) / sell-off (down) / chop (range)."""
    import html as _h
    wf = idx.get("what_if") or {}
    rows = [("Rally", wf.get("rally"), "#34d399", "▲"),
            ("Sell-off", wf.get("selloff"), "#f87171", "▼"),
            ("Chop", wf.get("chop"), "#ffd54f", "▬")]
    items = []
    for lbl, txt, color, arrow in rows:
        txt = (txt or "").strip()
        if not txt:
            continue
        items.append(f'<div class="wf-row"><span class="wf-tag" style="color:{color}">'
                     f'{arrow} {lbl}</span><span class="wf-txt">{_h.escape(txt)}</span></div>')
    if not items:
        return ""
    return f'<div class="idx-whatif"><div class="wf-head">What if</div>{"".join(items)}</div>'


def _index_card_html(idx) -> str:
    import html as _h
    sym = _h.escape(idx.get("symbol") or "")
    note = _h.escape(idx.get("note") or "")
    note_html = f'<div class="idx-note">{note}</div>' if note else ""
    close_outlook = _h.escape(idx.get("close_outlook") or "")
    co_html = (f'<div class="idx-close"><span class="idx-close-h">Into the close</span> '
               f'{close_outlook}</div>') if close_outlook else ""
    return (f'<div class="idx-card"><div class="idx-head">{sym}</div>'
            f'<div class="idx-body">{_ladder_svg(idx)}'
            f'<div class="idx-right">{_metric_tiles_html(idx)}{note_html}</div></div>'
            f'{co_html}{_whatif_html(idx)}</div>')


def analyze_infographic_html(data, subtitle=None) -> str:
    """Render the parsed analysis into the all-in-one dashboard infographic BODY
    fragment: a regime banner + bias meter, a card per index (price-level ladder +
    metric tiles + note), and a narrative footer. Wrapped by ``_analyze_doc``."""
    import html as _h
    d = data or {}
    parts = ['<div class="ga-banner">'
             f'<div class="ga-regime">{_h.escape(d.get("regime") or "—")}</div>'
             f'{_bias_meter_html(d.get("bias"), d.get("bias_label"))}</div>']
    headline = _h.escape(d.get("headline") or "")
    if headline:
        parts.append(f'<div class="ga-headline">{headline}</div>')
    narrative = (d.get("narrative") or "").strip()
    if narrative:
        parts.append(f'<div class="ga-narr">{_analyze_md_to_html(narrative)}</div>')
    for idx in d.get("indices") or []:
        parts.append(_index_card_html(idx))
    # Additive enrichment — absent on an older/terser reply, so these render
    # nothing and the intraday briefing is byte-identical to before.
    parts.append(_movers_html(d.get("movers")))
    parts.append(_macro_html(d.get("macro_drivers")))
    why = (d.get("why") or "").strip()
    if why:
        parts.append('<div class="ga-why"><div class="ga-why-h">Why is this happening</div>'
                     f'<div class="ga-why-b">{_analyze_md_to_html(why)}</div></div>')
    return "".join(parts)


def _movers_html(movers) -> str:
    """Notable-individual-stock-moves chip strip, green/red by sign. '' when empty.

    Tolerates BOTH shapes: the code-computed producer (`_notable_movers` →
    ``day_pct``/``basis``/``flow_alert_count``) and the model's tool reply
    (``move`` string + ``note``). This is a raw-HTML document (the documented
    out-of-scope path for the Tailwind-first rule), so inline styles are correct
    here — matching ``_metric_tiles_html``. Every string is escaped."""
    import html as _h
    if not movers:
        return ""
    chips = []
    for m in movers:
        try:
            if not isinstance(m, dict):
                continue
            sym = _h.escape(str(m.get("symbol") or "").strip())
            if not sym:
                continue
            pct = _num(m.get("day_pct"))
            if pct is not None:
                move_txt = f"{pct:+.2f}%"
                color = "#34d399" if pct > 0 else "#f87171" if pct < 0 else "#9aa3bd"
            else:
                move_txt = str(m.get("move") or "").strip()
                color = ("#34d399" if move_txt.startswith("+")
                         else "#f87171" if move_txt.startswith("-") else "#9aa3bd")
            extra = []
            basis = m.get("basis")
            if basis:
                extra.append("vs prior close" if basis == "prior_close" else "since open")
            n = m.get("flow_alert_count", m.get("flow_alerts"))
            if isinstance(n, (int, float)) and not isinstance(n, bool) and n:
                extra.append(f"{int(n)} flow alert{'s' if int(n) != 1 else ''}")
            note = str(m.get("note") or "").strip()
            if note:
                extra.append(note)
            sub = (f'<div style="color:#8a93a3;font-size:.76rem">'
                   f'{_h.escape(" · ".join(extra))}</div>') if extra else ""
            chips.append(
                '<div style="background:#101a30;border:1px solid #213152;border-radius:8px;'
                'padding:8px 12px;min-width:104px">'
                f'<div style="color:#cdd8ee;font-weight:600;font-size:.85rem">{sym}</div>'
                f'<div style="color:{color};font-weight:700">{_h.escape(move_txt) or "—"}</div>'
                f'{sub}</div>')
        except Exception:
            continue
    if not chips:
        return ""
    return ('<div class="ga-why"><div class="ga-why-h">Notable moves</div>'
            '<div style="display:flex;flex-wrap:wrap;gap:8px">'
            + "".join(chips) + "</div></div>")


def _macro_html(drivers) -> str:
    """'What drove the tape' bullet list. '' when empty."""
    import html as _h
    lines = [str(x).strip() for x in (drivers or [])
             if isinstance(x, str) and str(x).strip()]
    if not lines:
        return ""
    items = "".join(f"<li>{_h.escape(x)}</li>" for x in lines)
    return ('<div class="ga-why"><div class="ga-why-h">What drove the tape</div>'
            '<ul style="margin:6px 0 0;padding-left:20px;color:#cdd8ee">'
            f'{items}</ul></div>')


def _eod_index_card_html(idx) -> str:
    """Per-index EOD card — the ladder + tiles, with the past-tense ``recap`` where
    the intraday card shows ``note``/``close_outlook``/``what_if``."""
    import html as _h
    sym = _h.escape(idx.get("symbol") or "")
    recap = _h.escape(idx.get("recap") or "")
    recap_html = f'<div class="idx-note">{recap}</div>' if recap else ""
    return (f'<div class="idx-card"><div class="idx-head">{sym}</div>'
            f'<div class="idx-body">{_ladder_svg(idx)}'
            f'<div class="idx-right">{_metric_tiles_html(idx)}{recap_html}</div>'
            f'</div></div>')


def _next_session_html(ns) -> str:
    """The 'Prepare for the next session' block. '' when there's nothing to say."""
    import html as _h
    if not isinstance(ns, dict):
        return ""
    rows = [("Levels", ns.get("levels")), ("Expected move", ns.get("expected_move_note")),
            ("Catalysts", ns.get("catalysts")), ("Posture", ns.get("posture"))]
    body = "".join(
        f'<div class="ga-why-b"><b>{lbl}:</b> {_h.escape(str(val).strip())}</div>'
        for lbl, val in rows if isinstance(val, str) and val.strip())
    if not body:
        return ""
    return ('<div class="ga-why"><div class="ga-why-h">Prepare for the next session'
            f'</div>{body}</div>')


def eod_infographic_html(data, subtitle=None) -> str:
    """Render the parsed EOD retrospective into the infographic BODY fragment:
    regime banner + bias meter, headline/narrative, a per-index RECAP card, the
    day's notable movers + macro drivers, a 'next session' block, then Why."""
    import html as _h
    d = data or {}
    parts = ['<div class="ga-banner">'
             f'<div class="ga-regime">{_h.escape(d.get("regime") or "—")}</div>'
             f'{_bias_meter_html(d.get("bias"), d.get("bias_label"))}</div>']
    headline = _h.escape(d.get("headline") or "")
    if headline:
        parts.append(f'<div class="ga-headline">{headline}</div>')
    narrative = (d.get("narrative") or "").strip()
    if narrative:
        parts.append(f'<div class="ga-narr">{_analyze_md_to_html(narrative)}</div>')
    for idx in d.get("indices") or []:
        parts.append(_eod_index_card_html(idx))
    parts.append(_movers_html(d.get("movers")))
    parts.append(_macro_html(d.get("macro_drivers")))
    parts.append(_next_session_html(d.get("next_session")))
    why = (d.get("why") or "").strip()
    if why:
        parts.append('<div class="ga-why"><div class="ga-why-h">Why this happened</div>'
                     f'<div class="ga-why-b">{_analyze_md_to_html(why)}</div></div>')
    return "".join(parts)


_HISTORY_CSS = """
  .ga-sec { border-top:1px solid #213152; margin-top:26px; padding-top:10px; }
  .ga-sec:first-of-type { border-top:0; margin-top:0; }
  .ga-sec-h { color:#90caf9; font-size:.95rem; font-weight:600; margin:0 0 10px;
    text-transform:uppercase; letter-spacing:.04em; }
"""


def analyze_history_doc(briefings, title="Gamma Briefings") -> str:
    """Standalone HTML report combining several STORED briefings into one document.

    Each ``briefings`` item is a row dict from ``gamma_briefing_history_db`` (an
    ``analysis`` payload + ``date``/``slot``/``generated_at`` metadata); each is
    re-rendered via ``analyze_infographic_html`` under a date/slot header. PURE +
    deterministic (the report is regenerated from the stored data, never frozen), so
    the utility + any future in-app viewer share one renderer. Rows without a usable
    ``analysis`` are skipped."""
    import html as _h
    parts = []
    for b in (briefings or []):
        analysis = (b or {}).get("analysis")
        if not analysis:
            continue
        hdr = _h.escape(f"{b.get('date', '')} · {b.get('slot', '')} · "
                        f"{b.get('generated_at', '')}")
        parts.append(f'<div class="ga-sec"><div class="ga-sec-h">{hdr}</div>'
                     f'<div class="ga-body">{analyze_infographic_html(analysis)}</div></div>')
    body = "".join(parts) or "<p>No briefings found for this selection.</p>"
    return ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{_h.escape(title)}</title>"
            f"<style>{_ANALYZE_CSS}{_HISTORY_CSS}</style></head><body><div class=\"ga\">"
            f"<div class=\"ga-title\">{_h.escape(title)}</div>"
            f"<div class=\"ga-sub\">{len(parts)} briefing(s)</div>"
            f"{body}</div></body></html>")



def _count_anthropic_call():
    """Best-effort per-day Claude-call counter (Settings -> API usage).

    Recorded immediately before each ``messages.create`` so every real attempt
    counts and no-key/stand-down paths (which never reach the API) do not.
    Never raises — counting must not break a Claude call."""
    try:
        from shared import anthropic_counter
        anthropic_counter.record()
    except Exception:  # noqa: BLE001
        pass

def gamma_analyze(client=None, label: str | None = None) -> dict:
    """Run the bundled SPX/SPY/QQQ briefing through Claude → ``{"html", "prompt"}``.

    Fetch each of $SPX/SPY/QQQ, build its analysis blocks (defensive per-symbol →
    None), bundle them via ``build_summary_prompt_bundled``, then call Claude
    (Sonnet 5, thinking disabled) forcing the ``submit_analysis`` tool, and render
    the structured reply as the all-in-one dashboard infographic (regime banner +
    bias meter, a price-level ladder + metric tiles per index, narrative footer) in a
    standalone dark HTML document the GUI serves in a new tab (mirrors
    ``gamma_explain``). ``client`` is injected in tests; in production it is built
    from the resolved API key. ``label`` (e.g. ``"Auto · Premarket · Jun 28 8:01 AM
    CT"``) is appended to the doc subtitle so a scheduled run shows which slot + when.

    Returns ``{"html", "prompt", "analysis"}`` (``analysis`` = the parsed structured
    payload). Every failure surface degrades to a readable HTML page (so the tab
    always opens): no live chains (market closed) · no API key · API/network error ·
    no/empty tool reply. The ``html`` carries NO disclaimers (the system prompt
    forbids them)."""
    import gamma_tool as gt

    subtitle = "Dealer-positioning briefing · $SPX / SPY / QQQ"
    if label:
        subtitle = f"{subtitle} · {label}"

    blocks, em_by_sym, chains = {}, {}, {}
    for key, sym in (("spx", "$SPX"), ("spy", "SPY"), ("qqq", "QQQ")):
        try:
            chain = _gamma_fetch_chain(sym)
            if chain:
                chains[sym] = chain
                blocks[key] = _gamma_blocks_for(sym, chain)
                # Authoritative 1-day EM per symbol — overrides the model's copied
                # value below so the displayed 'Exp. move' is code-computed, not AI-echoed.
                em_by_sym[sym.lstrip("$").upper()] = _session_expected_move(chain)
            else:
                blocks[key] = None
        except Exception:
            blocks[key] = None
    try:
        prompt = gt.build_summary_prompt_bundled(blocks["spx"], blocks["spy"], blocks["qqq"])
    except Exception:
        # build_summary_prompt_bundled raises when ALL three bundles are None — no
        # live option chain for $SPX/SPY/QQQ (market closed / weekend / proxy down).
        return {"html": _analyze_doc(
            "<p>No GEX analysis available right now — could not fetch live option "
            "chains for $SPX, SPY or QQQ. The market may be closed (Analyze needs "
            "live chain data), or the data service is unavailable. Try again during "
            "market hours.</p>", subtitle)}

    # Forward-projection context: a code-computed 'into the close' brief per index
    # (projected flip/walls + EM band under flat-spot time-decay) so the model can
    # author each index's reader-first close_outlook. Never breaks the briefing.
    try:
        from services.options_svc import scheduler as _sched
        _now = _sched._market_now()
        _eng = gt.GammaEngine()
        _briefs = []
        for _sym in ("$SPX", "SPY", "QQQ"):
            _ch = chains.get(_sym)
            if not _ch:
                continue
            _b = _projection_brief(_eng, _ch, _ch.get("underlyingPrice"), _now)
            if _b:
                _briefs.append(f"{_sym}: {_b}")
        if _briefs:
            prompt = (prompt + "\n\nFORWARD PROJECTION (flat-spot time-decay to the "
                      "close, code-computed — use it to write each index's "
                      "close_outlook as a reader-first action):\n" + "\n".join(_briefs))
    except Exception:
        log.debug("analyze projection context failed", exc_info=True)

    # Enrichment context, shared with the EOD briefing. Each source is in its OWN
    # try/except: a cache read or a web-search failure must never cost us the
    # briefing (pinned by test_gamma_analyze_survives_news_and_movers_failure).
    movers = []
    try:
        _dash, _mtx, _fa = _eod_cache_reads()
        movers = _notable_movers(_dash, _mtx, _fa) or []
        _blk = _movers_prompt_block(movers)
        if _blk:
            prompt = f"{prompt}\n\n{_blk}"
    except Exception:
        log.debug("analyze movers context failed", exc_info=True)
        movers = []
    news = []
    try:
        news = _research_news(label or "intraday") or []
        _blk = _news_prompt_block(news)
        if _blk:
            prompt = f"{prompt}\n\n{_blk}"
    except Exception:
        log.debug("analyze news context failed", exc_info=True)
        news = []

    client = client or _make_analyze_client()
    if client is None:
        return {"html": _analyze_doc(
            "<p>AI analysis is not configured. Set the <code>ANTHROPIC_API_KEY</code> "
            "environment variable (or place the key in <code>shared/anthropic_key.txt</code>) "
            "on the options service, then click Analyze again.</p>", subtitle),
            "prompt": prompt}

    try:
        _count_anthropic_call()
        resp = client.messages.create(
            model=_ANALYZE_MODEL,
            max_tokens=_ANALYZE_MAX_TOKENS,
            thinking={"type": "disabled"},
            system=_ANALYZE_SYSTEM,
            tools=[_ANALYZE_TOOL],
            tool_choice={"type": "tool", "name": "submit_analysis"},
            messages=[{"role": "user", "content": prompt}],
        )
        # See eod_briefing: a max_tokens stop truncates the tool input and drops
        # trailing fields. Here that silently emptied `indices` — the whole
        # per-index briefing — so this must be loud.
        if getattr(resp, "stop_reason", None) == "max_tokens":
            log.warning("gamma_analyze hit max_tokens (%s) — the reply was truncated "
                        "and trailing fields (e.g. indices) may be missing",
                        _ANALYZE_MAX_TOKENS)
        tool_input = None
        for b in (getattr(resp, "content", None) or []):
            if (getattr(b, "type", None) == "tool_use"
                    and getattr(b, "name", "") == "submit_analysis"):
                tool_input = getattr(b, "input", None)
                break
    except Exception as exc:  # noqa: BLE001 — surface the failure in the tab.
        return {"html": _analyze_doc(
            f"<p>AI analysis failed: <code>{exc}</code></p>"
            "<p>Try again in a moment — if it persists, check the API key and the "
            "service log.</p>", subtitle), "prompt": prompt}

    data = _parse_analysis(tool_input)
    if not data:
        return {"html": _analyze_doc(
            "<p>The model returned no usable analysis. Try Analyze again.</p>",
            subtitle), "prompt": prompt}
    # Code-authoritative expected move: replace the model's copied value with the
    # 1-day EM computed above (matched by symbol), so the tile/ladder never show the
    # engine's collapsed 0-DTE remaining-hours figure.
    for idx in data.get("indices") or []:
        k = (idx.get("symbol") or "").lstrip("$").upper()
        if em_by_sym.get(k) is not None:
            idx["expected_move"] = em_by_sym[k]
    # Same code-authoritative rule for the enrichment: where the app computed it,
    # the app's value wins over the model's transcription.
    if movers:
        data["movers"] = movers
    if news:
        data["macro_drivers"] = news
    return {"html": _analyze_doc(analyze_infographic_html(data, subtitle), subtitle),
            "prompt": prompt, "analysis": data}


# ── EOD retrospective briefing (the 15:15 CT `close` slot) ──────────────────
# The cash session is OVER by the time this runs, so it is deliberately NOT the
# intraday playbook: no `what_if`/`close_outlook` (advice for a session that has
# ended is worse than useless). Instead it reports what the market DID, WHY (live
# macro drivers), which levels held or broke (code-computed session path), which
# individual names moved, and what to carry into the NEXT session. Same failure
# discipline as `gamma_analyze`: every surface degrades to a readable page.
# Comfortably above the intraday brief: the EOD reply carries strictly more —
# per-index recap AND movers AND macro_drivers AND next_session. A live probe at
# 1800 measured 1455 output tokens on a good run and intermittently truncated,
# which silently dropped `next_session` (it is emitted LAST) and rendered the
# briefing without its prepare-for-tomorrow block. Headroom is far cheaper than
# a briefing that loses its point.
_EOD_MAX_TOKENS = 2600
_EOD_TITLE = "End-of-Day Recap"
_EOD_SYSTEM = (
    "You are an options-market analyst writing an END-OF-DAY RETROSPECTIVE. The US "
    "cash session has CLOSED. Call the submit_eod tool exactly once.\n"
    "MANDATORY FIELDS — your single tool call MUST include EVERY ONE of: regime, "
    "bias, bias_label, headline, narrative, why, indices, next_session. 'indices' "
    "MUST contain exactly three entries — $SPX, SPY and QQQ, in that order — each "
    "with its levels and its 'recap'. 'next_session' MUST have all four of levels, "
    "expected_move_note, catalysts and posture. Omitting any of these makes the "
    "briefing unusable. Do not skip a field to save space: keep individual prose "
    "SHORT if you need to, but emit them all.\n"
    "WRITE A RETROSPECTIVE, NOT A FORWARD INTRADAY PLAYBOOK. Never advise intraday "
    "entries, exits or management for the session that just ended — it is over. Use "
    "the PAST TENSE for everything about today.\n"
    "Say what the market DID today, WHY it did it (use the supplied MACRO DRIVERS — "
    "these are researched facts, prefer them over your own recollection), which key "
    "levels held or broke (use the supplied SESSION PATH + LEVELS verbatim — it is "
    "code-computed truth), and which individual names moved (use the supplied "
    "NOTABLE INDIVIDUAL STOCK MOVES; copy the percentages exactly, never invent one).\n"
    "Then fill 'next_session' with what to carry into TOMORROW: the levels that "
    "matter (today's closing walls and gamma flip persist overnight because open "
    "interest does), the expected-move band, tomorrow's scheduled catalysts, and the "
    "posture to bring in. This is the only forward-looking part of the briefing, and "
    "it is MANDATORY — fill all four of levels / expected_move_note / catalysts / "
    "posture with substantive content; never leave one blank.\n"
    "Per index, 'recap' is what THAT index did today and where it closed relative to "
    "its levels — one or two terse sentences, past tense.\n"
    "Copy the EXACT computed levels from the data into each index entry — gamma flip, "
    "call wall, put wall, max pain, expected move (in points), put/call ratio — do not "
    "estimate or invent numbers, and omit a field only if it isn't present in the data. "
    "Set bias from -100 (max bearish) to +100 (max bullish), 0 = neutral, describing "
    "how the day RESOLVED.\n"
    "FRAME EVERYTHING FROM THE TRADER'S PERSPECTIVE — prefer 'you' and concrete "
    "observations over dealer-hedging mechanics; you may mention the mechanism "
    "briefly, but lead with what it meant for the reader. Keep the headline terse and "
    "the narrative to 2-3 sentences. Do NOT include any disclaimers, risk warnings, "
    "'not financial advice' notes, or boilerplate — only the analysis."
)

_EOD_TOOL = {
    "name": "submit_eod",
    "description": ("Return the structured END-OF-DAY retrospective for $SPX, SPY and "
                    "QQQ: what the market did today, why, which levels held or broke, "
                    "which individual names moved, and what to carry into the next "
                    "session. The app renders it as an infographic, so copy the exact "
                    "computed levels from the provided data rather than estimating."),
    "input_schema": {
        "type": "object",
        "properties": {
            "regime": {"type": "string",
                       "description": "Short label for how the day RESOLVED, e.g. "
                                      "'Risk-off unwind' or 'Grind-up trend day'."},
            "bias": {"type": "number",
                     "description": "How the day resolved, -100 (bearish) to +100 (bullish)."},
            "bias_label": {"type": "string",
                           "description": "Two-or-three-word label, e.g. 'Clearly bearish'."},
            "headline": {"type": "string",
                         "description": "One-sentence, PAST-TENSE headline of what "
                                        "happened today."},
            "narrative": {"type": "string",
                          "description": "2-3 past-tense sentences on how the session "
                                         "actually played out."},
            "why": {"type": "string",
                    "description": "1-2 plain sentences on WHY the tape did what it did, "
                                   "grounded in the supplied macro drivers."},
            "macro_drivers": {
                "type": "array", "items": {"type": "string"},
                "description": "The day's actual macro drivers, one short line each, "
                               "taken from the supplied research.",
            },
            "movers": {
                "type": "array",
                "description": "Notable individual stock moves, from the supplied list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "move": {"type": "string",
                                 "description": "The move, e.g. '+6.5%'. Copy exactly."},
                        "note": {"type": "string",
                                 "description": "A few words on why it moved."},
                    },
                    "required": ["symbol"],
                },
            },
            "indices": {
                "type": "array",
                "description": "One entry per index, in order $SPX, SPY, QQQ.",
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "spot": {"type": "number",
                                 "description": "The CLOSING spot."},
                        "gamma_flip": {"type": "number"},
                        "call_wall": {"type": "number"},
                        "put_wall": {"type": "number"},
                        "max_pain": {"type": "number"},
                        "expected_move": {"type": "number",
                                          "description": "Expected move in points (1 std dev)."},
                        "pc_ratio": {"type": "number", "description": "Put/call ratio."},
                        "recap": {"type": "string",
                                  "description": "PAST TENSE: what this index did today "
                                                 "and where it closed versus its gamma "
                                                 "flip and walls."},
                    },
                    "required": ["symbol"],
                },
            },
            "next_session": {
                "type": "object",
                "description": "What to carry into the NEXT session — the only "
                               "forward-looking part of this briefing.",
                "properties": {
                    "levels": {"type": "string",
                               "description": "The levels that matter tomorrow (today's "
                                              "closing walls/flip persist overnight)."},
                    "expected_move_note": {"type": "string",
                                           "description": "The expected-move band to expect."},
                    "catalysts": {"type": "string",
                                  "description": "Tomorrow's scheduled catalysts "
                                                 "(data, earnings, events)."},
                    "posture": {"type": "string",
                                "description": "The posture to bring into tomorrow."},
                },
                # All four REQUIRED. A live probe with these merely optional came
                # back with next_session entirely absent, so the "prepare for the
                # next session" block -- the reason this briefing exists -- silently
                # rendered as nothing.
                "required": ["levels", "expected_move_note", "catalysts", "posture"],
            },
        },
        "required": ["regime", "bias", "headline", "narrative", "why", "indices",
                     "next_session"],
    },
}


def _coerce_drivers(v) -> list:
    """Model-supplied macro drivers → a clean list of non-empty strings."""
    out = []
    for x in (v or []) if isinstance(v, (list, tuple)) else []:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def _coerce_movers(v) -> list:
    """Model-supplied movers → clean ``{symbol, move, note}`` dicts (junk dropped)."""
    out = []
    for x in (v or []) if isinstance(v, (list, tuple)) else []:
        if not isinstance(x, dict):
            continue
        sym = str(x.get("symbol") or "").strip()
        if not sym:
            continue
        out.append({"symbol": sym,
                    "move": str(x.get("move") or "").strip(),
                    "note": str(x.get("note") or "").strip()})
    return out


def _parse_eod(inp) -> dict | None:
    """Normalize the model's ``submit_eod`` tool input → render-ready dict.

    Total over adversarial input (mirrors ``_parse_analysis``). Returns None only
    when there's nothing renderable, so the caller degrades to a message."""
    if not isinstance(inp, dict):
        return None
    ns = inp.get("next_session") if isinstance(inp.get("next_session"), dict) else {}
    out = {
        "regime": str(inp.get("regime") or "").strip(),
        "bias": _num(inp.get("bias")),
        "bias_label": str(inp.get("bias_label") or "").strip(),
        "headline": str(inp.get("headline") or "").strip(),
        "narrative": str(inp.get("narrative") or "").strip(),
        "why": str(inp.get("why") or "").strip(),
        "macro_drivers": _coerce_drivers(inp.get("macro_drivers")),
        "movers": _coerce_movers(inp.get("movers")),
        "indices": [],
        "next_session": {
            "levels": str(ns.get("levels") or "").strip(),
            "expected_move_note": str(ns.get("expected_move_note") or "").strip(),
            "catalysts": str(ns.get("catalysts") or "").strip(),
            "posture": str(ns.get("posture") or "").strip(),
        },
    }
    for it in (inp.get("indices") or []):
        if not isinstance(it, dict):
            continue
        out["indices"].append({
            "symbol": str(it.get("symbol") or "").strip(),
            "spot": _num(it.get("spot")),
            "gamma_flip": _num(it.get("gamma_flip")),
            "call_wall": _num(it.get("call_wall")),
            "put_wall": _num(it.get("put_wall")),
            "max_pain": _num(it.get("max_pain")),
            "expected_move": _num(it.get("expected_move")),
            "pc_ratio": _num(it.get("pc_ratio")),
            "recap": str(it.get("recap") or "").strip(),
        })
    if not out["indices"] and not out["headline"] and not out["narrative"]:
        return None
    return out


def _backfill_indices(data, levels_by_sym, em_by_sym, recap) -> dict:
    """Guarantee the EOD briefing has per-index cards.

    The model intermittently omits ``indices`` even though the tool marks it
    required — measured at roughly one live run in three, and the API does not
    hard-enforce required on tool input. Losing it costs the briefing every ladder
    and tile, which is most of its value.

    Every number on those cards is already code-computed (closing levels off the
    chain, the session path off the flow series, the authoritative EM), so when the
    model drops the array we rebuild it deterministically and synthesize a factual
    recap sentence from the path + level verdicts. Only the model's prose is lost.
    A populated reply is left untouched. Never raises."""
    try:
        if not isinstance(data, dict):
            return {"indices": []}
        if data.get("indices"):
            return data
        out = []
        for sym, lv in (levels_by_sym or {}).items():
            try:
                r = (recap or {}).get(sym) or {}
                path = r.get("path") or {}
                lv = lv or {}
                bits = []
                if path:
                    bits.append(
                        f"Opened {path.get('open')}, high {path.get('high')}, "
                        f"low {path.get('low')}, closed {path.get('close')} "
                        f"({(path.get('day_pct') or 0):+.2f}%).")
                    for key, name in (("flip", "gamma flip"), ("call_wall", "call wall"),
                                      ("put_wall", "put wall")):
                        v = _level_verdict(path, lv.get(key) if lv.get(key) is not None
                                           else r.get(key), name)
                        if v:
                            bits.append(v[0].upper() + v[1:] + ".")
                out.append({
                    "symbol": sym,
                    "spot": path.get("close"),
                    "gamma_flip": lv.get("flip", r.get("flip")),
                    "call_wall": lv.get("call_wall", r.get("call_wall")),
                    "put_wall": lv.get("put_wall", r.get("put_wall")),
                    "max_pain": None,
                    "expected_move": (em_by_sym or {}).get(sym.lstrip("$").upper()),
                    "pc_ratio": None,
                    "recap": " ".join(bits),
                })
            except Exception:
                continue
        data["indices"] = out
        if out:
            log.warning("eod_briefing: model omitted `indices` — backfilled %d card(s) "
                        "from code-computed levels", len(out))
        return data
    except Exception:
        log.debug("_backfill_indices failed", exc_info=True)
        try:
            data.setdefault("indices", [])
        except Exception:
            return {"indices": []}
        return data


def _levels_from_blocks(block) -> dict:
    """Closing flip + call/put walls out of one symbol's GEX analysis block.

    Reads ``gex.flip_point`` and ``gex.walls.gex.{call_wall,put_wall}`` (see
    ``gamma_tool.build_analysis_dict``). A wall may be a bare strike or a dict
    carrying one. Defensive → all-None (never raises)."""
    def _strike(v):
        if isinstance(v, dict):
            v = v.get("strike")
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    try:
        g = ((block or {}).get("gex")) or {}
        walls = ((g.get("walls") or {}).get("gex")) or {}
        return {"flip": _strike(g.get("flip_point")),
                "call_wall": _strike(walls.get("call_wall")),
                "put_wall": _strike(walls.get("put_wall"))}
    except Exception:
        return {"flip": None, "call_wall": None, "put_wall": None}


_BRIEFING_BUS = None


def _briefing_bus():
    """Lazily-created module-level Bus for the briefings' cache reads.

    A handle, NOT a per-call construction: every ``Bus()`` opens its own
    connection (and, under pytest, spins up a whole fresh in-memory fakeredis
    server — measured at ~1.2 s per call, which showed up as an 8x slowdown of
    the briefing tests). Reused across briefings; ``None`` if the bus is
    unavailable, so the caller degrades to app-data-only."""
    global _BRIEFING_BUS
    if _BRIEFING_BUS is None:
        try:
            from shared.bus import Bus
            _BRIEFING_BUS = Bus()
        except Exception:
            log.debug("_briefing_bus unavailable", exc_info=True)
            return None
    return _BRIEFING_BUS


def _eod_cache_reads() -> tuple:
    """The three caches `_notable_movers` consumes. Defensive → ({}, {}, {}).

    NOTE: ``bus.cache_get`` returns a ``CacheEnvelope``, NOT a dict — always take
    ``.payload`` (a bare ``.get()`` on the envelope once made a scheduled push
    silently never fire)."""
    def _payload(bus, key):
        try:
            env = bus.cache_get(key)
            return (getattr(env, "payload", None) or {}) if env is not None else {}
        except Exception:
            return {}

    try:
        bus = _briefing_bus()
        if bus is None:
            return {}, {}, {}
        return (_payload(bus, "cache:market:dashboard"),
                _payload(bus, "cache:options:matrix"),
                _payload(bus, "cache:options:flow_alerts"))
    except Exception:
        log.debug("_eod_cache_reads failed", exc_info=True)
        return {}, {}, {}


def eod_briefing(client=None, label: str | None = None) -> dict:
    """End-of-day RETROSPECTIVE briefing → ``{"html", "prompt", "analysis"}``.

    Serves the 15:15 CT ``close`` slot. Same shape and failure discipline as
    ``gamma_analyze`` (so the existing PNG push path works unchanged), but a
    retrospective: the forced tool is ``submit_eod``, the prompt carries the
    code-computed session path + notable movers + researched macro drivers instead
    of the forward projection, and the renderer is ``eod_infographic_html``.
    Every enrichment source is best-effort — the briefing always renders."""
    import gamma_tool as gt

    subtitle = "End-of-day recap · $SPX / SPY / QQQ"
    if label:
        subtitle = f"{subtitle} · {label}"

    blocks, em_by_sym, chains = {}, {}, {}
    for key, sym in (("spx", "$SPX"), ("spy", "SPY"), ("qqq", "QQQ")):
        try:
            chain = _gamma_fetch_chain(sym)
            if chain:
                chains[sym] = chain
                blocks[key] = _gamma_blocks_for(sym, chain)
                em_by_sym[sym.lstrip("$").upper()] = _session_expected_move(chain)
            else:
                blocks[key] = None
        except Exception:
            blocks[key] = None
    try:
        prompt = gt.build_summary_prompt_bundled(blocks["spx"], blocks["spy"], blocks["qqq"])
    except Exception:
        return {"html": _analyze_doc(
            "<p>No end-of-day recap available — could not fetch option chains for "
            "$SPX, SPY or QQQ. The data service may be unavailable. Try again "
            "later.</p>", subtitle, title=_EOD_TITLE)}

    # Session path vs the CLOSING levels (code-computed truth the model must copy,
    # and the source the index cards are rebuilt from if the model omits them).
    levels_by_sym, recap = {}, {}
    try:
        levels_by_sym = {sym: _levels_from_blocks(blocks.get(key))
                         for key, sym in (("spx", "$SPX"), ("spy", "SPY"), ("qqq", "QQQ"))
                         if blocks.get(key)}
        recap = _eod_session_recap(levels_by_sym)
        block = _eod_recap_prompt_block(recap)
        if block:
            prompt = f"{prompt}\n\n{block}"
    except Exception:
        log.debug("eod recap context failed", exc_info=True)
        levels_by_sym, recap = levels_by_sym or {}, {}

    # Notable individual stock moves (code-computed from the app's own caches).
    movers = []
    try:
        dashboard, matrix, flow_alerts = _eod_cache_reads()
        movers = _notable_movers(dashboard, matrix, flow_alerts) or []
        block = _movers_prompt_block(movers)
        if block:
            prompt = f"{prompt}\n\n{block}"
    except Exception:
        log.debug("eod movers context failed", exc_info=True)
        movers = []

    # The day's macro drivers (phase-1 web-search call).
    news = []
    try:
        ctx = ", ".join(f"{s} {(chains.get(s) or {}).get('underlyingPrice')}"
                        for s in ("$SPX", "SPY", "QQQ") if chains.get(s))
        news = _research_news(label or "close", context=ctx, eod=True) or []
        block = _news_prompt_block(news)
        if block:
            prompt = f"{prompt}\n\n{block}"
    except Exception:
        log.debug("eod news context failed", exc_info=True)
        news = []

    client = client or _make_analyze_client()
    if client is None:
        return {"html": _analyze_doc(
            "<p>AI analysis is not configured. Set the <code>ANTHROPIC_API_KEY</code> "
            "environment variable (or place the key in <code>shared/anthropic_key.txt</code>) "
            "on the options service.</p>", subtitle, title=_EOD_TITLE), "prompt": prompt}

    try:
        _count_anthropic_call()
        resp = client.messages.create(
            model=_ANALYZE_MODEL,
            max_tokens=_EOD_MAX_TOKENS,
            thinking={"type": "disabled"},
            system=_EOD_SYSTEM,
            tools=[_EOD_TOOL],
            tool_choice={"type": "tool", "name": "submit_eod"},
            messages=[{"role": "user", "content": prompt}],
        )
        # A max_tokens stop truncates the tool input, silently dropping whatever
        # the model had not emitted yet (next_session is last). Log it — this
        # exact failure looked identical to "the model chose to omit it".
        if getattr(resp, "stop_reason", None) == "max_tokens":
            log.warning("eod_briefing hit max_tokens (%s) — the reply was truncated "
                        "and trailing fields (e.g. next_session) may be missing",
                        _EOD_MAX_TOKENS)
        tool_input = None
        for b in (getattr(resp, "content", None) or []):
            if (getattr(b, "type", None) == "tool_use"
                    and getattr(b, "name", "") == "submit_eod"):
                tool_input = getattr(b, "input", None)
                break
    except Exception as exc:  # noqa: BLE001 — surface the failure in the tab.
        return {"html": _analyze_doc(
            f"<p>End-of-day recap failed: <code>{exc}</code></p>"
            "<p>Check the API key and the service log.</p>", subtitle, title=_EOD_TITLE),
            "prompt": prompt}

    data = _parse_eod(tool_input)
    if not data:
        return {"html": _analyze_doc(
            "<p>The model returned no usable end-of-day recap.</p>", subtitle, title=_EOD_TITLE),
            "prompt": prompt}

    # The model sometimes drops `indices` entirely — rebuild the cards from the
    # code-computed levels so the briefing can never lose them (see the docstring).
    data = _backfill_indices(data, levels_by_sym, em_by_sym, recap)

    # Code-authoritative overrides — same principle as the EM override in
    # gamma_analyze: where the app computed it, the app's number wins.
    for idx in data.get("indices") or []:
        k = (idx.get("symbol") or "").lstrip("$").upper()
        if em_by_sym.get(k) is not None:
            idx["expected_move"] = em_by_sym[k]
    if movers:
        data["movers"] = movers
    if news:
        data["macro_drivers"] = news

    return {"html": _analyze_doc(eod_infographic_html(data, subtitle), subtitle, title=_EOD_TITLE),
            "prompt": prompt, "analysis": data}


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


# Strategy codes the analytic ``calc_summary`` handles exactly; everything else
# (butterfly/condor/calendar/diagonal/CUSTOM) uses the numeric generic summary.
_CALC_ANALYTIC_CODES = {"PCS", "CCS", "IC",
                        "LONG_CALL", "LONG_PUT", "NAKED_CALL", "NAKED_PUT"}


# ── intraday time-to-expiry (the 0DTE fix) ───────────────────────────────────
# Options stop trading at the 4:00pm ET close (QQQ/SPY/equities and PM-settled
# 0DTE index weeklys). Time-to-expiry is the CALENDAR span from now to that close
# in years (/365 — the same convention bs_price/calc_summary already use, and the
# one ThinkorSwim implies IV under). Using calendar ``.days`` instead collapses to
# 0 on expiration day (intrinsic-only) or a bogus full day — the calculator bug.
from zoneinfo import ZoneInfo as _ZoneInfo  # noqa: E402

_MARKET_TZ = _ZoneInfo("America/New_York")
_EXPIRY_CLOSE_HOUR = 16  # 4:00pm ET
_YEAR_SECONDS = 365.0 * 24.0 * 3600.0


def _expiry_settlement(expiry_date):
    """The 4:00pm ET settlement datetime (tz-aware) for an expiry date."""
    import datetime as dt

    return dt.datetime(expiry_date.year, expiry_date.month, expiry_date.day,
                       _EXPIRY_CLOSE_HOUR, 0, 0, tzinfo=_MARKET_TZ)


def _year_fraction(start_dt, end_dt):
    """Calendar years from start to end (never negative), /365."""
    return max((end_dt - start_dt).total_seconds(), 0.0) / _YEAR_SECONDS


def time_to_expiry_years(now_dt, expiry_date):
    """Years from ``now_dt`` (tz-aware) to the expiry's 4:00pm ET close, /365.

    Sub-day resolution: 3 hours before the close on expiry day → ~3/24/365, not 0;
    after the close → 0. Multi-day → calendar days + today's fraction."""
    return _year_fraction(now_dt, _expiry_settlement(expiry_date))


def calc_iv(spot, strike, option_type, mark, expiry, rate=None, now=None) -> dict:
    """Imply IV (percent) from an option ``mark`` at the intraday time-to-expiry.

    ToS-style: solve Black-Scholes for sigma given the contract's current mark and
    the calendar time from *now* to the 4:00pm ET close on ``expiry``. This is what
    reproduces ThinkorSwim's per-contract IV at 0DTE (where IV is acutely sensitive
    to the time basis). Returns ``{"iv", "strike", "option_type", "mark", "T",
    "error"}``; ``iv`` is None with a reason when it can't be solved (expired, mark
    at/below intrinsic, missing inputs). Fully defensive — never raises.

    ``rate`` defaults to the shared ``options_calculator.RISK_FREE_RATE`` (0.045,
    static) so the calculator, simulator, and this IV solver all use one r."""
    import datetime as dt

    import options_calculator as oc

    if rate is None:
        rate = oc.RISK_FREE_RATE

    try:
        expiry_date = dt.date.fromisoformat(str(expiry))
    except Exception:
        return {"iv": None, "strike": strike, "option_type": option_type,
                "mark": mark, "T": None, "error": "bad expiry"}
    if now is None:
        now = dt.datetime.now(_MARKET_TZ)
    T = time_to_expiry_years(now, expiry_date)
    iv = None
    if spot and strike and mark and option_type:
        try:
            iv = oc.implied_vol(float(mark), float(spot), float(strike), T,
                                float(rate), str(option_type))
        except Exception:
            iv = None
    return {"iv": round(iv * 100.0, 2) if iv is not None else None,
            "strike": strike, "option_type": option_type, "mark": mark, "T": T,
            "error": None if iv is not None else "could not imply IV from mark"}


def calc_compute(strategy, spot, iv, rate, ivadj, qty, expiry, legs,
                 num_strikes=24, price_rows=None, now=None) -> dict:
    """Run the calculator math → ``{"summary", "eval_labels", "pnl_data"}``.

    Time-to-expiry is INTRADAY (calendar seconds to the 4:00pm ET close on the
    expiry date, /365 — see ``time_to_expiry_years``), not calendar ``.days``. The
    P&L grid's FIRST column is **"Now"** (priced at that live intraday T → current
    value, the user-visible fix), each subsequent ``generate_eval_dates`` column is
    that future date's close, and the last column is the **expiration payoff**
    (T=0; labeled ``"Exp"`` for a 0DTE where now and expiry share the date). The
    ``calc_summary`` tiles (incl. PoP) also use the intraday "Now" T rather than the
    old ``or 1/365`` clamp, which over-priced 0DTE ~20×.

    The grid's price rows are the page's explicit ``price_rows`` (the ±N real chain
    strikes around spot from the Number-of-strikes control). When absent, fall back
    to the engine's even-step heuristic over ±``num_strikes`` rows. ``expiry`` arrives
    as an ISO string. Labels are pre-formatted strings so the page's grid header needs
    no date objects. ``summary``/``pnl_data`` are JSON-safe."""
    import datetime as dt

    import options_calculator as oc

    expiry_date = dt.date.fromisoformat(str(expiry))
    # Calendars/diagonals carry per-leg expiries; the grid horizon + columns use
    # the FRONT (nearest) leg expiry. Same-expiry strategies are unchanged
    # (min == the page expiry), and legs without an 'expiry' leave it untouched.
    leg_exps = []
    for _l in (legs or []):
        _e = _l.get("expiry")
        if _e:
            try:
                leg_exps.append(dt.date.fromisoformat(str(_e)))
            except (TypeError, ValueError):
                pass
    if leg_exps:
        expiry_date = min(leg_exps)
    today = dt.date.today()
    if now is None:
        now = dt.datetime.now(_MARKET_TZ)

    settlement = _expiry_settlement(expiry_date)
    t_now = time_to_expiry_years(now, expiry_date)
    code = strategy if strategy in _CALC_ANALYTIC_CODES else "CUSTOM"
    if code == "CUSTOM":
        summary = oc.calc_summary_generic(legs, spot, r=rate, iv=iv, T=t_now)
    else:
        summary = oc.calc_summary(legs, code, spot, r=rate, iv=iv, T=t_now)

    # Columns: intraday "Now" (current value) + each future eval date at its close
    # + the expiration payoff. ``generate_eval_dates`` returns [today, …, expiry];
    # "Now" replaces the today slot, the rest keep MM/DD labels (expiry → T=0). For
    # a 0DTE that loop is empty, so append an explicit "Exp" (T=0) payoff column.
    columns = [("Now", t_now)]
    for d in oc.generate_eval_dates(today, expiry_date)[1:]:
        columns.append((d.strftime("%m/%d"), _year_fraction(_expiry_settlement(d),
                                                             settlement)))
    if expiry_date == today:
        columns.append(("Exp", 0.0))

    eval_labels = [lab for lab, _ in columns]
    eval_times = [t for _, t in columns]
    # Grid rows: the page's explicit ±num_strikes real chain strikes when available;
    # else the engine's even-step heuristic over ±num_strikes rows (a wide-open
    # price_range so rows_per_side governs the extent).
    rows = [float(p) for p in (price_rows or []) if isinstance(p, (int, float))]
    pnl_data = oc.calc_spread_pnl(legs, spot, iv, rate, [None] * len(columns),
                                  (0.0, 1e12), expiry_date, iv_adjustment=ivadj,
                                  eval_times=eval_times, per_leg_expiry=True,
                                  rows_per_side=int(num_strikes or 24),
                                  price_rows=(rows or None))
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

# Equity/index option contract multiplier (shares per contract). The simulator
# engine prices in per-share × qty units; ×100 converts the What-if curve to a
# DOLLAR position value so it matches the Calculator (options_calculator.py scales
# by the same literal 100). The old What-if path dropped this entirely.
_CONTRACT_MULT = 100


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


def sim_run(symbol, expiry=None, kind=None, strike=None, direction=None,
            dt=5.0, mult=1.5, legs=None) -> dict:
    """Compute What-if + IV-shock for a position (single OR multi-leg) → JSON-safe.

    ``legs`` (preferred) is a list of {kind, strike, expiry, side, qty}; when
    omitted the legacy single-contract args (expiry/kind/strike/direction) build a
    one-leg list (back-compat). The What-if sweep advances each leg by ``dt``
    ELAPSED days from now (per-leg decay → calendars are correct); IV-shock + the
    engine already price each leg at its own expiry. Missing snapshot/contract →
    {} (page prompts a re-fetch / selection)."""
    from options_simulator import engine as seng
    import numpy as np
    import datetime as _dt

    snap = _SIM_SNAPSHOTS.get(symbol)
    if snap is None:
        return {}

    if legs is None:
        if expiry is None or kind is None or strike is None:
            return {}
        legs = [{"kind": kind, "strike": strike, "expiry": expiry,
                 "side": "long" if (direction or "buy") == "buy" else "short",
                 "qty": 1}]
    if not legs:
        return {}

    resolved = []  # (ContractRow, sign, ratio)
    for leg in legs:
        c = find_contract(snap, leg.get("expiry"), leg.get("kind"), leg.get("strike"))
        if c is None:
            return {}
        sign = +1 if leg.get("side", "long") == "long" else -1
        resolved.append((c, sign, int(leg.get("qty", 1))))
    pos = seng.Position.from_legs(resolved, label=f"{snap.symbol} {len(resolved)}-leg")

    today = _dt.date.today()

    def _days_after(c, elapsed):
        """Each leg's days-to-expiry after ``elapsed`` days from now (>=0.01).
        Tolerates a string or date ``expiry`` (test doubles use strings)."""
        exp = c.expiry
        if isinstance(exp, str):
            try:
                exp = _dt.date.fromisoformat(exp[:10])
            except ValueError:
                return max(float(elapsed), 0.01)
        dte_now = max((exp - today).days, 0)
        return max(dte_now - float(elapsed), 0.01)

    s_range = np.linspace(snap.spot * 0.8, snap.spot * 1.2, 81)
    whatif_eng = seng.WhatIfEngine(snap)
    wdf = seng.aggregate_position(
        pos, lambda c: whatif_eng.sweep(c, s_range, _days_after(c, float(dt))))
    whatif_rows = _sim_records(wdf)
    # ×100 → DOLLAR position value (see _CONTRACT_MULT). Without this a 10-lot
    # spread's What-if read 100× too small (−$200 instead of −$20,000).
    for _r in whatif_rows:
        _tp = _r.get("theo_price")
        if isinstance(_tp, (int, float)):
            _r["theo_price"] = _tp * _CONTRACT_MULT
    # Entry baseline: the position's $ value at the CURRENT spot and CURRENT time
    # (Δt=0, full per-leg DTE) — the mark you'd enter at. The page measures What-if
    # P/L from here (value(S,t) − baseline), i.e. the Calculator's
    # ``entry_credit + value(S,t)`` — NOT the old forward-time "zero at spot"
    # subtraction (which hid theta and was off by the credit).
    bdf = seng.aggregate_position(
        pos, lambda c: whatif_eng.sweep(c, [snap.spot], _days_after(c, 0.0)))
    brows = _sim_records(bdf)
    whatif_baseline = (float(brows[0]["theo_price"]) * _CONTRACT_MULT
                       if brows and isinstance(brows[0].get("theo_price"), (int, float))
                       else 0.0)

    shock_eng = seng.IVShockEngine(snap)
    sdf = seng.aggregate_position(pos, lambda c: shock_eng.sweep(c, [1.0, float(mult)]))
    rows = sdf.to_dict("records") if hasattr(sdf, "to_dict") else list(sdf or [])
    ivshock = {"base": rows[0], "shock": rows[1]} if len(rows) >= 2 else None

    return {"spot": snap.spot, "whatif_rows": whatif_rows,
            "whatif_baseline": whatif_baseline, "ivshock": ivshock}


_REPLAY_OVERRIDES = {
    "1m_1d":   {"freq_type": "minute", "minutes": 1,  "days": 1,  "label": "1-minute bars, 1 day"},
    "5m_3d":   {"freq_type": "minute", "minutes": 5,  "days": 3,  "label": "5-minute bars, 3 days"},
    "5m_5d":   {"freq_type": "minute", "minutes": 5,  "days": 5,  "label": "5-minute bars, 5 days"},
    "15m_10d": {"freq_type": "minute", "minutes": 15, "days": 10, "label": "15-minute bars, 10 days"},
    "1d_20d":  {"freq_type": "daily",  "months": 1,   "bars": 20, "label": "daily bars, 20 days"},
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
        return {"freq_type": "minute", "minutes": 1, "days": 1, "label": "1-minute bars, 1 day"}
    if dte <= 5:
        return {"freq_type": "minute", "minutes": 5, "days": 3, "label": "5-minute bars, 3 days"}
    if dte <= 15:
        return {"freq_type": "minute", "minutes": 5, "days": 5, "label": "5-minute bars, 5 days"}
    bars = math.ceil(dte / 2)
    months = max(1, math.ceil(bars / 21))
    return {"freq_type": "daily", "months": months, "bars": bars,
            "label": f"daily bars, {bars} days"}


def sim_replay(symbol, expiry=None, kind=None, strike=None, direction=None,
               lookback="auto", legs=None) -> dict:
    """Re-price a position (single OR multi-leg) along the underlying's recent path.

    Ports the legacy Tk Replay tab: ``ReplayEngine.full_trace`` over a price
    path, plus the gap-compression / session-boundary layout the page needs to
    draw a clean integer x-axis (overnight/weekend breaks collapsed onto
    consecutive indices). ``legs`` (preferred) is a list of
    {kind, strike, expiry, side, qty}; when omitted the legacy single-contract
    args (expiry/kind/strike/direction) build a one-leg list (back-compat). The
    legs are netted by ``aggregate_position`` into one trace. The path is a
    **DTE-aware** window fetched here (``replay_lookback_spec`` →
    ``_fetch_replay_history``), NOT the snapshot's fixed 2-day history — the
    expiry/DTE is only known at replay time, and ``lookback`` ('auto' or an
    override key) lets the page widen/narrow it (the window is sized to the
    NEAREST leg expiry). Returns a JSON-safe dict; ``{}`` if the snapshot/contract
    is missing (page prompts a re-fetch / selection), or ``{"error": ...}`` if IV
    is unavailable or there's no price history. Replay depends ONLY on the
    contract selector + look-back — not the dt/mult sliders — so it is its own
    command/cache view, separate from ``sim_run`` (keeps slider-driven sweeps
    cheap)."""
    from options_simulator import engine as seng
    import numpy as np
    import dataclasses
    import datetime as dt

    snap = _SIM_SNAPSHOTS.get(symbol)
    if snap is None:
        return {}

    if legs is None:
        if expiry is None or kind is None or strike is None:
            return {}
        legs = [{"kind": kind, "strike": strike, "expiry": expiry,
                 "side": "long" if (direction or "buy") == "buy" else "short",
                 "qty": 1}]
    if not legs:
        return {}

    resolved = []  # (ContractRow, sign, ratio)
    for leg in legs:
        c = find_contract(snap, leg.get("expiry"), leg.get("kind"), leg.get("strike"))
        if c is None:
            return {}
        sign = +1 if leg.get("side", "long") == "long" else -1
        resolved.append((c, sign, int(leg.get("qty", 1))))
    if any(c.iv <= 0 for c, _, _ in resolved):
        return {"error": "IV unavailable - cannot simulate"}

    # DTE-aware history window (fetched here, not the snapshot's fixed 2-day path),
    # sized to the NEAREST leg expiry.
    def _dte(c):
        exp = c.expiry
        if isinstance(exp, str):
            try:
                exp = dt.date.fromisoformat(exp[:10])
            except ValueError:
                return 15
        return (exp - dt.date.today()).days
    dte = min(_dte(c) for c, _, _ in resolved)
    spec = replay_lookback_spec(dte, lookback)
    hist = _fetch_replay_history(snap.symbol, spec)
    if hist is None or hist.empty:
        return {"error": "Replay unavailable - no price history"}

    # Re-price along the fetched path (shallow-copy the snapshot's history so the
    # cached snapshot is untouched).
    snap_path = dataclasses.replace(snap, price_history=hist)
    pos = seng.Position.from_legs(resolved, label=f"{snap.symbol} {len(resolved)}-leg")
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
            resolution = f"{len(hist)} bars, 1-minute, {sessions_n} sessions"
        elif median_delta_s < 3600:
            resolution = (f"{len(hist)} bars, {int(round(median_delta_s/60))}-minute, "
                          f"{sessions_n} sessions")
        else:
            span_days = (hist.index[-1] - hist.index[0]).days or 1
            resolution = f"{len(hist)} bars, daily, ~{span_days} days"
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


def em_cone(spot, atm_iv, dte, start_ts_ms, holidays=None, trading_days_only=False):
    """Forward expected-move cone points anchored at ``spot`` on ``start_ts_ms``.

    Returns {"upper": [[ts_ms, v], ...], "lower": [...]} with one point per day
    t = 0..dte. width(t) = spot * atm_iv * sqrt(t/365) (calendar-day √-time, so
    the envelope is correct). When ``trading_days_only`` is set, **non-trading
    days (weekends + any dates in ``holidays``) are omitted** so the cone lines up
    with the candles on an ordinal (gap-collapsed) axis — the anchor t=0 is always
    kept. Empty dict values on non-positive dte or missing spot/iv (defensive —
    never raises)."""
    import math
    import datetime as _dt
    if not isinstance(spot, (int, float)) or not isinstance(atm_iv, (int, float)):
        return {"upper": [], "lower": []}
    try:
        dte = int(dte)
    except (TypeError, ValueError):
        return {"upper": [], "lower": []}
    if dte <= 0 or atm_iv < 0:
        return {"upper": [], "lower": []}
    holidays = holidays or set()
    start_date = _dt.datetime.fromtimestamp(int(start_ts_ms) / 1000).date()
    upper, lower = [], []
    for t in range(dte + 1):
        if trading_days_only and t > 0:
            d = start_date + _dt.timedelta(days=t)
            if d.weekday() >= 5 or d in holidays:
                continue
        ts = int(start_ts_ms) + t * _DAY_MS
        width = spot * atm_iv * math.sqrt(t / 365.0)
        upper.append([ts, round(spot + width, 2)])
        lower.append([ts, round(spot - width, 2)])
    return {"upper": upper, "lower": lower}


# The daily-history endpoint (periodType=year&period=1) ends at the PREVIOUS
# trading day — Schwab never returns the forming bar — so the current session is
# missing from the Expected Move chart. The live quote does carry it, so
# synthesize it. Gated at/after the open because premarket ``openPrice`` is still
# the PRIOR session's open and would draw a false bar. Reuses ``_RTH_START``
# (defined above) rather than a second 08:30 constant.


def today_candle(quote, last_ts_ms, now=None, holidays=None):
    """``[ts_ms, o, h, l, c]`` for today's forming session bar, or ``None``.

    ``quote`` is a RAW Schwab quote dict (``openPrice``/``highPrice``/
    ``lowPrice``/``lastPrice``) — the normalized ``SchwabProxyClient.get_quote``
    drops ``openPrice``. Returns None unless today is a trading day, local time
    is at/after the 08:30 CT open (``_RTH_START``), every OHLC field is numeric
    and > 0, and the history's last candle predates today (so this is a no-op
    should Schwab ever start returning the forming bar). Schwab's daily-candle
    epoch is **midnight CT** (verified live), so both the history comparison and
    the returned timestamp are computed on the ``_PROJ_CT_TZ`` basis — not host-
    local time — to line up with the real candles on any host. A caller-supplied
    ``now`` is used as-is (naive or aware); only the default reaches for CT.
    Never raises."""
    import datetime as _dt

    if not isinstance(quote, dict):
        return None
    now = now or _dt.datetime.now(_PROJ_CT_TZ)
    holidays = holidays or set()
    today = now.date()
    if now.weekday() >= 5 or today in holidays:
        return None
    if now.time() < _dt.time(*_RTH_START):
        return None
    try:
        last_date = _dt.datetime.fromtimestamp(int(last_ts_ms) / 1000, _PROJ_CT_TZ).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    if last_date >= today:
        return None
    vals = []
    for key in ("openPrice", "highPrice", "lowPrice", "lastPrice"):
        v = quote.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            return None
        vals.append(float(v))
    ts = int(_dt.datetime(today.year, today.month, today.day, tzinfo=_PROJ_CT_TZ).timestamp() * 1000)
    return [ts, *vals]


def _now_iso():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat()


_EM_HISTORY_BARS = 130  # ~6 months of trading days (legacy default / "6mo" override)

_EM_OVERRIDES = {
    "1mo": {"mode": "daily", "months": 1,  "bars": 21,  "label": "daily · 1mo"},
    "3mo": {"mode": "daily", "months": 3,  "bars": 63,  "label": "daily · 3mo"},
    "6mo": {"mode": "daily", "months": 6,  "bars": 130, "label": "daily · 6mo"},
    "1y":  {"mode": "daily", "months": 12, "bars": 252, "label": "daily · 1y"},
}


def em_lookback_spec(dte, override="auto") -> dict:
    """Map ``(dte, override)`` → a trailing-history spec for Expected Move.

    ``override`` of ``"auto"`` (or unknown) gives a window ≈ **3× DTE** trading
    days behind the forward cone: very short DTE (≤2) switches to intraday
    candles, otherwise daily clamped to [20, 252] bars. Known override keys force
    a fixed daily window (1mo/3mo/6mo/1y). Returns a dict with ``mode``
    ('daily'|'intraday') plus the params the fetch needs and a ``label``."""
    import math
    if override and override != "auto" and override in _EM_OVERRIDES:
        return dict(_EM_OVERRIDES[override])
    try:
        dte = int(dte)
    except (TypeError, ValueError):
        dte = 15
    if dte <= 2:
        return {"mode": "intraday", "minutes": 30, "days": 3, "label": "30-min · 3d"}
    bars = min(252, max(20, 3 * dte))
    return {"mode": "daily", "months": max(1, math.ceil(bars / 21)), "bars": bars,
            "label": f"daily · {bars}d"}


def _fetch_em_candles(symbol, spec):
    """Fetch OHLC candles ([[ts_ms, o, h, l, c], …]) for an EM ``spec``.

    Intraday specs use the flexible ``get_intraday_history``; daily specs reuse
    ``get_price_history_every_day`` (1-yr daily) sliced to the last ``bars`` rows
    (same partial-bar filtering as before). Defensive: returns ``[]`` on failure."""
    try:
        if spec.get("mode") == "intraday":
            import pandas as pd
            df = _proxy.schwab_client.get_intraday_history(
                symbol, minutes=spec["minutes"], days=spec["days"])
            if df is None or len(df) == 0:
                return []
            out = []
            for _, row in df.iterrows():
                ts = int(pd.Timestamp(row["datetime"]).timestamp() * 1000)
                if None in (row.get("open"), row.get("high"), row.get("low"), row.get("close")):
                    continue
                out.append([ts, row["open"], row["high"], row["low"], row["close"]])
            out.sort(key=lambda r: r[0])
            return out
        cresp = _proxy.schwab_py_client.get_price_history_every_day(symbol)
        raw = cresp.json().get("candles", []) if getattr(cresp, "status_code", None) == 200 else []
        candles = [[int(c["datetime"]), c["open"], c["high"], c["low"], c["close"]]
                   for c in raw
                   if c.get("datetime") is not None
                   and c.get("open") is not None and c.get("high") is not None
                   and c.get("low") is not None and c.get("close") is not None]
        candles.sort(key=lambda r: r[0])
        return candles[-int(spec.get("bars", _EM_HISTORY_BARS)):]
    except Exception:
        return []


def compute_expected_move(symbol, expiry, legs, lookback="auto") -> dict:
    """Build the Expected Move payload for a symbol/expiry/legs (defensive).

    Fetches ~6mo daily candles + the option chain, derives ATM IV for ``expiry``
    and the spot, and builds the forward cone. Always returns a JSON-safe dict;
    on any failure ``error`` is set and the data fields are empty."""
    import datetime as dt

    base = {"symbol": symbol, "expiry": expiry, "spot": None, "atm_iv": None,
            "dte": None, "candles": [], "em_upper": [], "em_lower": [],
            "legs": legs or [], "generated_at": _now_iso(),
            "lookback": {"label": "", "key": lookback or "auto"}, "error": None}
    try:
        api = "$SPX" if (symbol or "").upper() == "SPX" else (symbol or "").upper()
        if not api:
            base["error"] = "No symbol."
            return base
        today = dt.date.today()

        # Parse the expiry first — the trailing-history window is DTE-aware.
        try:
            exp_date = dt.date.fromisoformat(str(expiry))
        except Exception:
            base["error"] = f"Bad expiry: {expiry!r}."
            return base
        dte = (exp_date - today).days
        base["dte"] = dte
        spec = em_lookback_spec(dte, lookback)
        base["lookback"] = {"label": spec.get("label", ""), "key": lookback or "auto"}

        candles = _fetch_em_candles(api, spec)
        if not candles:
            base["error"] = f"No price history for {api}."
            return base
        base["candles"] = candles

        oresp = _proxy.schwab_py_client.get_option_chain(
            api, contract_type="ALL", from_date=today, to_date=exp_date)
        chain = oresp.json() if getattr(oresp, "status_code", None) == 200 else None

        # Prefer the RAW quote: the normalized schwab_client.get_quote drops
        # openPrice, which today's synthetic candle needs. Fall back to the
        # normalized client when the raw one yields nothing, so the spot path
        # degrades exactly as it did before.
        raw_q = {}
        try:
            qresp = _proxy.schwab_py_client.get_quotes([api])
            if getattr(qresp, "status_code", None) == 200:
                info = (qresp.json() or {}).get(api) or {}
                raw_q = info.get("quote", info.get("reference", info)) or {}
        except Exception:
            raw_q = {}
        spot = raw_q.get("lastPrice") if isinstance(raw_q, dict) else None
        if not spot:
            q = _proxy.schwab_client.get_quote(api) or {}
            if isinstance(q, dict):
                spot = q.get("last")
        if not spot:
            spot = candles[-1][4]
        base["spot"] = spot

        # Schwab's daily history stops at the previous trading day; append the
        # forming bar so the chart shows today AND the cone anchors on it (it is
        # sized from today's spot, so anchoring at yesterday overshot the expiry
        # by a day).
        if spec.get("mode") != "intraday":
            try:
                from services.options_svc.scheduler import _HOLIDAYS as _mkt_hols
            except Exception:
                _mkt_hols = set()
            bar = today_candle(raw_q, candles[-1][0], holidays=_mkt_hols)
            if bar:
                candles = candles + [bar]
                base["candles"] = candles

        atm_iv = atm_iv_from_chain(chain or {}, spot, expiry=str(expiry))
        base["atm_iv"] = atm_iv

        if atm_iv is None:
            base["error"] = f"No ATM IV for {api} {expiry}."
            return base

        # Trading-day-only cone (skip weekends/holidays) so it lines up with the
        # candles on the page's ordinal axis — no blank non-trading gaps.
        try:
            from services.options_svc.scheduler import _HOLIDAYS as _mkt_holidays
        except Exception:
            _mkt_holidays = set()
        cone = em_cone(spot, atm_iv, dte, candles[-1][0],
                       holidays=_mkt_holidays, trading_days_only=True)
        base["em_upper"] = cone["upper"]
        base["em_lower"] = cone["lower"]
        return base
    except Exception as exc:
        base["error"] = f"{type(exc).__name__}: {exc}"
        return base


# ── Rescue advisory integration (Task 6.1) ──────────────────────────────────
# Wires the pure ``rescue`` engine (services/options_svc/rescue.py) to the live
# data path: load a paper position, reprice it to a mark (reusing the same
# ``signal_repricer.reprice_swing`` the manage cycle / captured view use), pull
# gamma + regime context, then call the engine's ``rescue_candidates`` +
# ``assess_position_risk``. compute_rescue + assess_open_positions are FULLY
# DEFENSIVE — they never raise (return {"error": ...} / empty aggregates).
#
# ``reprice_swing`` is imported as a MODULE-LEVEL alias (not lazy) so it is a
# monkeypatch target in tests; it lives in options-scanner which is already on
# sys.path (module top). It pulls ``fill_model`` transitively but NOT
# ``scoring``, so binding it at import is safe in this isolated service process.
from services.options_svc import rescue as _rescue  # noqa: E402
_rescue_candidates = _rescue.rescue_candidates
_assess_position_risk = _rescue.assess_position_risk
_single_candidates = _rescue.single_candidates
_assess_single_risk = _rescue.assess_single_risk
_debit_candidates = _rescue.debit_candidates
_assess_debit_risk = _rescue.assess_debit_risk
_range_candidates = _rescue.range_candidates
_assess_range_risk = _rescue.assess_range_risk
_SINGLE_STRATEGIES = ("LONG_CALL", "LONG_PUT", "NAKED_CALL", "NAKED_PUT")
_DEBIT_STRATEGIES = ("VERT_CALL_DEBIT", "VERT_PUT_DEBIT")
_RANGE_STRATEGIES = ("CONDOR_CALL", "CONDOR_PUT", "BUTTERFLY_CALL", "BUTTERFLY_PUT")
from shared.contracts.options import (  # noqa: E402
    RescueAdvisory, RescueCandidate, RescueMark)

import datetime as _rescue_dt  # noqa: E402

# Aliased at module level so it stays a monkeypatch seam. (signal_repricer pulls
# fill_model only; binding here does not touch options-scanner ``scoring``.)
import signal_repricer as _signal_repricer  # noqa: E402
reprice_swing = _signal_repricer.reprice_swing


def _fetch_chain_for_expiry(symbol, expiry):
    """Fetch the option chain for ONE expiry (from_date == to_date == expiry).

    Mirrors ``signal_repricer._fetch_chain`` (a single-expiry pull) using the
    shared proxy client so the leg pricer can price both the position's own
    expiry AND a forward expiry (current+30d) the roll builders request.
    Defensive: returns None on any failure / non-200."""
    try:
        exp_date = (_rescue_dt.date.fromisoformat(str(expiry)[:10])
                    if expiry is not None else None)
        if exp_date is None:
            return None
        resp = _proxy.schwab_py_client.get_option_chain(
            symbol, contract_type="ALL", from_date=exp_date, to_date=exp_date)
        return resp.json() if getattr(resp, "status_code", None) == 200 else None
    except Exception:
        return None


def _leg_mid_from_chain(chain, right, strike):
    """Per-contract mid (bid+ask)/2 for one leg in a chain payload, else None.

    ``right`` is "PUT"|"CALL". Scans the matching exp-date map (any expiry in the
    single-expiry chain) for the strike key (Schwab keys strikes as "500.0").
    Returns None if missing / one-sided (bid<=0 or ask<=0)."""
    if not isinstance(chain, dict) or strike is None:
        return None
    map_key = "putExpDateMap" if str(right).upper() == "PUT" else "callExpDateMap"
    key = f"{float(strike):.1f}"
    for _exp_key, strikes in (chain.get(map_key) or {}).items():
        contracts = (strikes or {}).get(key)
        if contracts:
            ctr = contracts[0]
            bid = ctr.get("bid", 0) or 0
            ask = ctr.get("ask", 0) or 0
            if bid <= 0 or ask <= 0:
                return None
            return (bid + ask) / 2
    return None


def _make_leg_pricer(symbol):
    """Return ``price_leg(symbol, expiry, right, strike) -> float | None``.

    Backed by the per-expiry chain fetch. The chain for each requested expiry is
    fetched once and cached inside the closure, so repeated leg lookups (and the
    roll builders' forward expiry = current+30d) refetch at most once per expiry.
    Fully defensive — None on any miss / fetch failure."""
    cache: dict[str, dict | None] = {}

    def price_leg(_sym, expiry, right, strike):
        ek = str(expiry)[:10]
        if ek not in cache:
            cache[ek] = _fetch_chain_for_expiry(symbol, expiry)
        chain = cache[ek]
        if chain is None:
            return None
        return _leg_mid_from_chain(chain, right, strike)

    return price_leg


def _load_position(position_id):
    """Load ONE paper position (or captured-signal-as-position) by id, or None.

    paper_account_db has no get-by-id, so we filter ``fetch_all_positions``.
    Lazy import (paper_account_db pulls no ``scoring``, but keep the house lazy
    pattern). Defensive: None on any failure / not found."""
    try:
        import paper_account_db
        for r in paper_account_db.fetch_all_positions(None):
            if r.get("position_id") == position_id:
                return r
    except Exception:
        return None
    return None


def _load_open_positions():
    """All OPEN paper positions (list of dicts). [] on failure."""
    try:
        import paper_account_db
        return paper_account_db.fetch_open_positions(None)
    except Exception:
        return []


def _rescue_regime():
    """Build a {trend_state, trend_confidence} dict from the sentiment bridge.

    Reuses ``regime_filter.evaluate_regime`` (eagerly imported at module top),
    which reads the sentiment bridge file and exposes ``trend_state`` +
    ``trend_confidence``. Returns None if unavailable — rescue handles None."""
    try:
        reg = evaluate_regime() or {}
        ts = reg.get("trend_state")
        if ts is None:
            return None
        return {"trend_state": ts, "trend_confidence": reg.get("trend_confidence") or 0.0}
    except Exception:
        return None


def _light_gex_context(symbol):
    """A LIGHT gamma context for rescue detection — spot + GEX flip/walls ONLY.

    A single chain fetch + ``calc_all_from_chain`` (GEX view), NOT the full
    ``gamma_snapshot`` (which also builds the forward projection band, the term
    grid, the flow series and decodes the whole session's history — all discarded
    here, since rescue only reads flip/walls/spot via ``_gex_from_snapshot``).
    Shaped exactly like a gamma_snapshot GEX view so ``_gex_from_snapshot`` + the
    spot fallback consume it unchanged. Defensive → None."""
    import gamma_tool as gt

    try:
        chain = _gamma_fetch_chain(symbol)
        if not chain:
            return None
        eng = gt.GammaEngine()
        res = eng.calc_all_from_chain(chain)
        if not res:
            return None
        gex = res[0] or {}
        spot = gex.get("spot")
        try:
            summary = eng.snapshot_summary(gex, "gex")
        except Exception:
            summary = {}
        return {"spot": spot,
                "views": {"GEX": {"flip": (summary or {}).get("flip"),
                                  "walls": gamma_walls("GEX", gex, spot)}}}
    except Exception:
        return None


def _gex_from_snapshot(snap):
    """Extract {flip, put_wall, call_wall} from a gamma_snapshot() GEX view.

    The snapshot's ``views["GEX"]`` carries ``flip`` and ``walls`` =
    [put_wall, call_wall] (one per side). Returns None if unavailable."""
    if not isinstance(snap, dict):
        return None
    gex = (snap.get("views") or {}).get("GEX") or {}
    walls = gex.get("walls") or []
    put_wall = walls[0] if len(walls) >= 1 else None
    call_wall = walls[1] if len(walls) >= 2 else None
    if gex.get("flip") is None and put_wall is None and call_wall is None:
        return None
    return {"flip": gex.get("flip"), "put_wall": put_wall, "call_wall": call_wall}


def _rescue_dte(expiration):
    try:
        exp = _rescue_dt.date.fromisoformat(str(expiration)[:10])
        return (exp - _rescue_dt.date.today()).days
    except Exception:
        return None


def _load_captured_as_position(signal_id):
    """Load a captured signal by id and shape it like a paper position, or None.

    Captured signals live in ``signal_db`` (a SELECT * row), not
    ``paper_account_db``. We map the relevant fields onto the position-like dict
    the rescue engine expects: ``strategy`` from the signal ``strategy`` (else
    ``type``); short/long + call legs; expiration; entry_credit; quantity
    (default 1); and a ``max_loss_total`` derived from the spread width when the
    signal doesn't carry one. Lazy import (mirrors the house pattern). Defensive:
    None on any failure / not found."""
    try:
        import signal_db
        sig = signal_db.get_signal(signal_id)
    except Exception:
        return None
    if not sig:
        return None
    strategy = sig.get("strategy") or sig.get("type") or ""
    short = sig.get("short_strike")
    long = sig.get("long_strike")
    qty = sig.get("quantity") or 1
    max_loss = sig.get("max_loss_total")
    if max_loss is None:
        width = sig.get("width")
        if width is None and short is not None and long is not None:
            try:
                width = abs(float(short) - float(long))
            except Exception:
                width = None
        if width is not None:
            try:
                max_loss = abs(float(width)) * 100 * qty
            except Exception:
                max_loss = None
    return {
        "position_id": signal_id,
        "symbol": sig.get("symbol"),
        "strategy": strategy,
        "short_strike": short,
        "long_strike": long,
        "call_short": sig.get("call_short"),
        "call_long": sig.get("call_long"),
        "expiration": sig.get("expiration"),
        "entry_credit": sig.get("entry_credit") or 0.0,
        "quantity": qty,
        "max_loss_total": max_loss,
    }


def _advisory_from_position(pos, *, source: str, force_advisory: bool,
                            position_id) -> dict:
    """Shared rescue-advisory core: reprice ``pos`` → mark → gamma/regime context
    → rank candidates → ``RescueAdvisory`` dict.

    Common to all three callers (paper / captured / ad-hoc): builds the ``trade``
    dict, reprices via ``reprice_swing`` (falling back to stored fields + the
    gamma snapshot's live ``spot`` for the underlying), fetches gamma + regime
    context, runs the pure rescue engine, and — when ``force_advisory`` — forces
    every candidate to ``apply_kind="advisory"`` (no executable paper position).
    ``position_id`` / ``source`` are stamped onto the returned advisory verbatim.
    Fully defensive → ``{"error": "..."}``; never raises."""
    try:
        symbol = pos.get("symbol")
        strategy = pos.get("strategy") or ""

        # 1. mark — live reprice (defensive), else stored fields.
        trade = {
            "symbol": symbol,
            "strategy": strategy,
            "short_strike": pos.get("short_strike"),
            "long_strike": pos.get("long_strike"),
            "call_short": pos.get("call_short"),
            "call_long": pos.get("call_long"),
            "entry_credit": pos.get("entry_credit") or 0.0,
            "expiration": pos.get("expiration"),
        }
        rep = None
        try:
            rep = reprice_swing(trade, _proxy.schwab_py_client)
        except Exception:
            rep = None
        if rep and not rep.get("error"):
            current_value = rep.get("current_value")
            unrealized_pnl = rep.get("unrealized_pnl")
            underlying = rep.get("current_underlying")
            short_delta = rep.get("current_short_delta")
        else:
            # fall back to the position's already-stored marks
            current_value = pos.get("current_value")
            unrealized_pnl = pos.get("unrealized_pnl")
            underlying = None
            short_delta = pos.get("current_short_delta")

        # 2. gamma context (defensive → None). Fetched BEFORE building the mark
        # so its live ``spot`` can stand in as the underlying when reprice could
        # not supply one (off-hours / a momentary quote gap) — the rescue
        # engine's PRIMARY trigger is underlying-vs-short-strike proximity, so a
        # missing underlying would silently degrade detection.
        try:
            snap = _light_gex_context(symbol)
        except Exception:
            snap = None
        if not underlying and isinstance(snap, dict) and (snap.get("spot") or 0) > 0:
            underlying = snap["spot"]
        gex = _gex_from_snapshot(snap)

        dte = _rescue_dte(pos.get("expiration"))
        mark = {
            "underlying": underlying,
            "current_value": current_value,
            "unrealized_pnl": unrealized_pnl,
            "short_delta": short_delta,
            "dte": dte,
        }
        # rescue engine uses ``current_underlying`` / ``current_short_delta`` keys.
        engine_mark = {
            "current_underlying": underlying,
            "current_value": current_value,
            "unrealized_pnl": unrealized_pnl,
            "current_short_delta": short_delta,
            "dte": dte,
        }

        # 3. regime context (defensive → None).
        regime = _rescue_regime()

        # 4. engine.
        price_leg = _make_leg_pricer(symbol)
        candidates = _rescue_candidates(pos, engine_mark, price_leg, gex, regime,
                                        underlying=underlying)
        risk = _assess_position_risk(pos, engine_mark, gex, regime)

        # context = the engine notes (carried on candidates[0].context), else [].
        context = []
        if candidates and candidates[0].get("context"):
            context = list(candidates[0]["context"])

        # Construct candidates per-item so ONE malformed candidate (e.g. a None
        # leg price slipping past a builder) can't sink the whole advisory.
        valid = []
        for c in candidates:
            try:
                if force_advisory:
                    # No executable paper position — every candidate is
                    # advisory-only (the GUI shows no Apply button).
                    c = {**c, "apply_kind": "advisory", "applies": False}
                valid.append(RescueCandidate(**c))
            except Exception:
                continue   # drop a malformed candidate rather than losing the rest

        adv = RescueAdvisory(
            position_id=position_id,
            source=source,
            symbol=symbol,
            strategy=strategy,
            state=risk.get("state", "ok"),
            heat=risk.get("heat", 0.0),
            mark=RescueMark(**mark),
            context=context,
            candidates=valid,
            ts=_rescue_dt.datetime.now(_rescue_dt.timezone.utc).isoformat(),
        )
        return adv.model_dump()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def compute_rescue(position_id, source: str = "paper") -> dict:
    """Build the ranked rescue advisory for ONE position (paper or captured).

    ``source="paper"`` (default): loads a real paper position from
    ``paper_account_db`` and returns an advisory whose execute candidates have a
    one-click Apply. ``source="captured"``: loads the captured signal by id
    (``signal_db.get_signal``), shapes it like a position, and forces EVERY
    candidate to ``apply_kind="advisory"`` (a captured signal has no executable
    paper position). Both delegate to ``_advisory_from_position`` (reprice → mark
    → gamma/regime → rank). Returns a dict shaped like (and validated by) the
    ``RescueAdvisory`` contract; on ANY failure returns ``{"error": "..."}``.
    Never raises."""
    try:
        captured = (source == "captured")
        if captured:
            pos = _load_captured_as_position(position_id)
            if not pos:
                return {"error": "signal not found"}
        else:
            pos = _load_position(position_id)
            if not pos:
                return {"error": "position not found"}
        return _advisory_from_position(pos, source=source,
                                       force_advisory=captured,
                                       position_id=position_id)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _advisory_from_single(pos, *, source: str = "adhoc",
                          position_id="adhoc") -> dict:
    """Rescue-advisory core for a SINGLE-option position (long / naked call/put).

    ``reprice_swing`` only handles PCS/CCS/IC (it raises on any other strategy),
    so a single leg is priced DIRECTLY via ``_make_leg_pricer`` (per-share chain
    mid) and the underlying comes from the gamma snapshot's live ``spot``.
    Unrealized P&L is derived from the signed ``entry_credit``: a LONG's P&L =
    (value + entry_credit)·100·qty (entry_credit is a negative debit), a NAKED
    short's P&L = (entry_credit − value)·100·qty. Advisory-only. Fully defensive
    → ``{"error": "..."}``; never raises."""
    try:
        symbol = pos.get("symbol")
        strategy = (pos.get("strategy") or "").upper()
        strike = pos.get("short_strike")
        expiry = pos.get("expiration")
        qty = int(pos.get("quantity") or 1)
        entry_credit = pos.get("entry_credit") or 0.0
        right = "CALL" if strategy in ("LONG_CALL", "NAKED_CALL") else "PUT"

        # 1. mark — price the single leg directly (reprice_swing can't do singles).
        price_leg = _make_leg_pricer(symbol)
        try:
            current_value = price_leg(symbol, expiry, right, strike)
        except Exception:
            current_value = None

        # 2. gamma context (defensive → None); its live spot supplies the underlying.
        try:
            snap = _light_gex_context(symbol)
        except Exception:
            snap = None
        underlying = None
        if isinstance(snap, dict) and (snap.get("spot") or 0) > 0:
            underlying = snap["spot"]
        gex = _gex_from_snapshot(snap)

        unrealized_pnl = None
        if current_value is not None:
            if strategy in ("LONG_CALL", "LONG_PUT"):
                unrealized_pnl = round((current_value + entry_credit) * 100 * qty, 2)
            else:   # naked short
                unrealized_pnl = round((entry_credit - current_value) * 100 * qty, 2)

        dte = _rescue_dte(expiry)
        mark = {
            "underlying": underlying,
            "current_value": current_value,
            "unrealized_pnl": unrealized_pnl,
            "short_delta": None,
            "dte": dte,
        }
        engine_mark = {
            "current_underlying": underlying,
            "current_value": current_value,
            "unrealized_pnl": unrealized_pnl,
            "current_short_delta": None,
            "dte": dte,
        }

        # 3. regime context (defensive → None).
        regime = _rescue_regime()

        # 4. engine.
        cands = _single_candidates(pos, engine_mark, price_leg, gex, regime)
        risk = _assess_single_risk(pos, engine_mark, gex, regime)

        context = []
        if cands and cands[0].get("context"):
            context = list(cands[0]["context"])

        valid = []
        for c in cands:
            try:
                c = {**c, "apply_kind": "advisory", "applies": False}
                valid.append(RescueCandidate(**c))
            except Exception:
                continue

        adv = RescueAdvisory(
            position_id=position_id,
            source=source,
            symbol=symbol,
            strategy=strategy,
            state=risk.get("state", "ok"),
            heat=risk.get("heat", 0.0),
            mark=RescueMark(**mark),
            context=context,
            candidates=valid,
            ts=_rescue_dt.datetime.now(_rescue_dt.timezone.utc).isoformat(),
        )
        return adv.model_dump()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _adhoc_single(spec) -> dict:
    """Validate + build a single-option ad-hoc rescue advisory. Defensive."""
    try:
        symbol = spec.get("symbol")
        strategy = (spec.get("strategy") or "").upper()
        expiration = spec.get("expiration")
        if not symbol:
            return {"error": "symbol is required"}
        if strategy not in _SINGLE_STRATEGIES:
            return {"error": "unsupported single strategy"}
        if not expiration:
            return {"error": "expiration is required"}

        def _flt(key, required):
            raw = spec.get(key)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                return (None, f"{key} is required") if required else (None, None)
            try:
                return float(raw), None
            except (TypeError, ValueError):
                return None, f"{key} must be a number"

        strike, err = _flt("short_strike", True)
        if err:
            return {"error": err}
        entry_credit, err = _flt("entry_credit", False)
        if err:
            return {"error": err}
        if entry_credit is None:
            entry_credit = 0.0

        qty_raw = spec.get("quantity")
        if qty_raw is None or (isinstance(qty_raw, str) and not qty_raw.strip()):
            quantity = 1
        else:
            try:
                quantity = int(float(qty_raw))
            except (TypeError, ValueError):
                return {"error": "quantity must be an integer"}
        if quantity < 1:
            quantity = 1

        # nominal max_loss_total (LONG = debit; NAKED_PUT = strike notional;
        # NAKED_CALL = unbounded → None). single_candidates doesn't use it, but
        # keep it populated for parity with the spread path.
        if strategy in ("LONG_CALL", "LONG_PUT"):
            max_loss = abs(entry_credit) * 100 * quantity
        elif strategy == "NAKED_PUT":
            max_loss = strike * 100 * quantity
        else:   # NAKED_CALL
            max_loss = None

        pos = {
            "position_id": "adhoc",
            "symbol": symbol,
            "strategy": strategy,
            "short_strike": strike,
            "long_strike": None,
            "call_short": None,
            "call_long": None,
            "expiration": expiration,
            "entry_credit": entry_credit,
            "quantity": quantity,
            "max_loss_total": max_loss,
        }
        return _advisory_from_single(pos, source="adhoc", position_id="adhoc")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _advisory_from_debit(pos, *, source: str = "adhoc",
                         position_id="adhoc") -> dict:
    """Rescue-advisory core for a DEBIT vertical (bull call / bear put).

    ``reprice_swing`` only handles PCS/CCS/IC, so the two legs are priced DIRECTLY
    via ``_make_leg_pricer`` (per-share chain mids) and the current spread value
    ``cv`` = long-leg mid − short-leg mid (falling back to the entered debit when a
    leg is unpriceable). The underlying comes from the gamma snapshot's live
    ``spot``. Unrealized P&L = (cv − |entry_credit|)·100·qty (entry_credit is a
    negative debit → |·| is the per-share debit paid). Advisory-only. Fully
    defensive → ``{"error": "..."}``; never raises."""
    try:
        symbol = pos.get("symbol")
        strategy = (pos.get("strategy") or "").upper()
        long_strike = pos.get("long_strike")
        short_strike = pos.get("short_strike")
        expiry = pos.get("expiration")
        qty = int(pos.get("quantity") or 1)
        entry_credit = pos.get("entry_credit") or 0.0
        right = "CALL" if strategy == "VERT_CALL_DEBIT" else "PUT"

        # 1. mark — price the two legs directly; cv = long mid − short mid.
        price_leg = _make_leg_pricer(symbol)
        try:
            long_mid = price_leg(symbol, expiry, right, long_strike)
            short_mid = price_leg(symbol, expiry, right, short_strike)
        except Exception:
            long_mid = short_mid = None
        if long_mid is not None and short_mid is not None:
            cv = long_mid - short_mid
        else:
            cv = abs(entry_credit)     # degraded fallback = the debit paid

        # 2. gamma context (defensive → None); its live spot supplies the underlying.
        try:
            snap = _light_gex_context(symbol)
        except Exception:
            snap = None
        underlying = None
        if isinstance(snap, dict) and (snap.get("spot") or 0) > 0:
            underlying = snap["spot"]
        gex = _gex_from_snapshot(snap)

        unrealized_pnl = round((cv - abs(entry_credit)) * 100 * qty, 2)
        dte = _rescue_dte(expiry)
        mark = {
            "underlying": underlying,
            "current_value": cv,
            "unrealized_pnl": unrealized_pnl,
            "short_delta": None,
            "dte": dte,
        }
        engine_mark = {
            "current_underlying": underlying,
            "current_value": cv,
            "unrealized_pnl": unrealized_pnl,
            "current_short_delta": None,
            "dte": dte,
        }

        # 3. regime context (defensive → None).
        regime = _rescue_regime()

        # 4. engine.
        cands = _debit_candidates(pos, engine_mark, price_leg, gex, regime)
        risk = _assess_debit_risk(pos, engine_mark, gex, regime)

        context = []
        if cands and cands[0].get("context"):
            context = list(cands[0]["context"])

        valid = []
        for c in cands:
            try:
                c = {**c, "apply_kind": "advisory", "applies": False}
                valid.append(RescueCandidate(**c))
            except Exception:
                continue

        adv = RescueAdvisory(
            position_id=position_id,
            source=source,
            symbol=symbol,
            strategy=strategy,
            state=risk.get("state", "ok"),
            heat=risk.get("heat", 0.0),
            mark=RescueMark(**mark),
            context=context,
            candidates=valid,
            ts=_rescue_dt.datetime.now(_rescue_dt.timezone.utc).isoformat(),
        )
        return adv.model_dump()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _adhoc_debit(spec) -> dict:
    """Validate + build a debit-vertical ad-hoc rescue advisory. Defensive."""
    try:
        symbol = spec.get("symbol")
        strategy = (spec.get("strategy") or "").upper()
        expiration = spec.get("expiration")
        if not symbol:
            return {"error": "symbol is required"}
        if strategy not in _DEBIT_STRATEGIES:
            return {"error": "unsupported debit strategy"}
        if not expiration:
            return {"error": "expiration is required"}

        def _flt(key, required):
            raw = spec.get(key)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                return (None, f"{key} is required") if required else (None, None)
            try:
                return float(raw), None
            except (TypeError, ValueError):
                return None, f"{key} must be a number"

        long_strike, err = _flt("long_strike", True)
        if err:
            return {"error": err}
        short_strike, err = _flt("short_strike", True)
        if err:
            return {"error": err}
        # entry_credit is SIGNED (NEGATIVE = debit paid); do NOT reject a negative.
        entry_credit, err = _flt("entry_credit", False)
        if err:
            return {"error": err}
        if entry_credit is None:
            entry_credit = 0.0

        qty_raw = spec.get("quantity")
        if qty_raw is None or (isinstance(qty_raw, str) and not qty_raw.strip()):
            quantity = 1
        else:
            try:
                quantity = int(float(qty_raw))
            except (TypeError, ValueError):
                return {"error": "quantity must be an integer"}
        if quantity < 1:
            quantity = 1

        # Defined max loss = the debit paid.
        max_loss = abs(entry_credit) * 100 * quantity

        pos = {
            "position_id": "adhoc",
            "symbol": symbol,
            "strategy": strategy,
            "long_strike": long_strike,
            "short_strike": short_strike,
            "call_short": None,
            "call_long": None,
            "expiration": expiration,
            "entry_credit": entry_credit,
            "quantity": quantity,
            "max_loss_total": max_loss,
        }
        return _advisory_from_debit(pos, source="adhoc", position_id="adhoc")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _advisory_from_range(pos, *, source: str = "adhoc",
                         position_id="adhoc") -> dict:
    """Rescue-advisory core for a single-type RANGE structure (condor / butterfly).

    ``reprice_swing`` only handles PCS/CCS/IC, so every leg is priced DIRECTLY via
    ``_make_leg_pricer`` (per-share chain mids) and the current structure value
    ``cv`` = Σ (sign · mid · per-unit qty) — sign +1 short (receive) / −1 long (pay)
    — falling back to the entered debit when any leg is unpriceable. The underlying
    comes from the gamma snapshot's live ``spot``. Unrealized P&L =
    (cv − |entry_credit|)·100·qty. Advisory-only. Fully defensive → ``{"error":
    "..."}``; never raises."""
    try:
        symbol = pos.get("symbol")
        strategy = (pos.get("strategy") or "").upper()
        legs = pos.get("legs") or []
        expiry = pos.get("expiration")
        qty = int(pos.get("quantity") or 1)
        entry_credit = pos.get("entry_credit") or 0.0

        # 1. mark — price every leg directly; cv = Σ sign·mid·per-unit-qty.
        price_leg = _make_leg_pricer(symbol)
        mids = []
        for leg in legs:
            try:
                mids.append(price_leg(symbol, expiry, leg.get("right"),
                                      leg.get("strike")))
            except Exception:
                mids.append(None)
        if legs and all(m is not None for m in mids):
            # structure value to the holder = +long −short (a long condor/fly you
            # own is POSITIVE, ~ the debit paid). Same convention as the debit path.
            cv = sum((1.0 if leg.get("side") == "long" else -1.0)
                     * m * int(leg.get("qty") or 1)
                     for leg, m in zip(legs, mids))
        else:
            cv = abs(entry_credit)     # degraded fallback = the debit paid

        # 2. gamma context (defensive → None); its live spot supplies the underlying.
        try:
            snap = _light_gex_context(symbol)
        except Exception:
            snap = None
        underlying = None
        if isinstance(snap, dict) and (snap.get("spot") or 0) > 0:
            underlying = snap["spot"]
        gex = _gex_from_snapshot(snap)

        unrealized_pnl = round((cv - abs(entry_credit)) * 100 * qty, 2)
        dte = _rescue_dte(expiry)
        mark = {
            "underlying": underlying,
            "current_value": cv,
            "unrealized_pnl": unrealized_pnl,
            "short_delta": None,
            "dte": dte,
        }
        engine_mark = {
            "current_underlying": underlying,
            "current_value": cv,
            "unrealized_pnl": unrealized_pnl,
            "current_short_delta": None,
            "dte": dte,
        }

        # 3. regime context (defensive → None).
        regime = _rescue_regime()

        # 4. engine.
        cands = _range_candidates(pos, engine_mark, price_leg, gex, regime)
        risk = _assess_range_risk(pos, engine_mark, gex, regime)

        context = []
        if cands and cands[0].get("context"):
            context = list(cands[0]["context"])

        valid = []
        for c in cands:
            try:
                c = {**c, "apply_kind": "advisory", "applies": False}
                valid.append(RescueCandidate(**c))
            except Exception:
                continue

        adv = RescueAdvisory(
            position_id=position_id,
            source=source,
            symbol=symbol,
            strategy=strategy,
            state=risk.get("state", "ok"),
            heat=risk.get("heat", 0.0),
            mark=RescueMark(**mark),
            context=context,
            candidates=valid,
            ts=_rescue_dt.datetime.now(_rescue_dt.timezone.utc).isoformat(),
        )
        return adv.model_dump()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _adhoc_range(spec) -> dict:
    """Validate + build a single-type range (condor/butterfly) ad-hoc rescue
    advisory. ``spec`` carries ``legs`` (per-unit [{right, side, strike, qty}]),
    ``quantity`` (units), and a SIGNED ``entry_credit`` (NEGATIVE = debit). Defensive."""
    try:
        symbol = spec.get("symbol")
        strategy = (spec.get("strategy") or "").upper()
        expiration = spec.get("expiration")
        if not symbol:
            return {"error": "symbol is required"}
        if strategy not in _RANGE_STRATEGIES:
            return {"error": "unsupported range strategy"}
        if not expiration:
            return {"error": "expiration is required"}

        raw_legs = spec.get("legs") or []
        legs = []
        for leg in raw_legs:
            strike = leg.get("strike")
            try:
                strike = float(strike)
            except (TypeError, ValueError):
                return {"error": "every leg needs a numeric strike"}
            side = str(leg.get("side") or "").lower()
            if side not in ("long", "short"):
                return {"error": "every leg needs a side (long/short)"}
            right = str(leg.get("right") or "").upper()
            if right not in ("CALL", "PUT"):
                return {"error": "every leg needs a right (CALL/PUT)"}
            try:
                lqty = int(leg.get("qty") or 1)
            except (TypeError, ValueError):
                return {"error": "leg qty must be an integer"}
            legs.append({"right": right, "side": side, "strike": strike,
                         "qty": max(1, lqty)})
        if not legs:
            return {"error": "range structure needs legs"}

        raw = spec.get("entry_credit")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            entry_credit = 0.0
        else:
            try:
                entry_credit = float(raw)
            except (TypeError, ValueError):
                return {"error": "entry_credit must be a number"}

        qty_raw = spec.get("quantity")
        if qty_raw is None or (isinstance(qty_raw, str) and not qty_raw.strip()):
            quantity = 1
        else:
            try:
                quantity = int(float(qty_raw))
            except (TypeError, ValueError):
                return {"error": "quantity must be an integer"}
        if quantity < 1:
            quantity = 1

        pos = {
            "position_id": "adhoc",
            "symbol": symbol,
            "strategy": strategy,
            "legs": legs,
            "expiration": expiration,
            "entry_credit": entry_credit,
            "quantity": quantity,
            "max_loss_total": abs(entry_credit) * 100 * quantity,
        }
        return _advisory_from_range(pos, source="adhoc", position_id="adhoc")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def compute_rescue_adhoc(spec) -> dict:
    """Build the ranked rescue advisory for a USER-DEFINED ad-hoc trade.

    ``spec`` = ``{symbol, strategy, short_strike, long_strike, call_short,
    call_long, expiration, quantity, entry_credit}`` — a trade the user holds
    elsewhere, entered via the GUI form (so numeric fields may arrive as strings).
    Supports credit spreads (PCS/CCS/IC — the spread path) AND the SINGLE-option
    family (LONG_CALL/LONG_PUT/NAKED_CALL/NAKED_PUT — routed to ``_adhoc_single``;
    ``short_strike`` = the single strike, ``entry_credit`` SIGNED). Validates the
    minimum, coerces numerics defensively, builds a position-like dict, and
    delegates to ``_advisory_from_position(source="adhoc", force_advisory=True,
    position_id="adhoc")`` — advisory-only (no executable paper position). Fully
    defensive → ``{"error": "..."}``; never raises."""
    try:
        spec = spec or {}
        symbol = spec.get("symbol")
        strategy = spec.get("strategy")
        expiration = spec.get("expiration")
        if not symbol:
            return {"error": "symbol is required"}
        # Single-option strategies route to the dedicated single path.
        if strategy in _SINGLE_STRATEGIES:
            return _adhoc_single(spec)
        # Debit verticals (bull call / bear put) route to the debit path.
        if strategy in _DEBIT_STRATEGIES:
            return _adhoc_debit(spec)
        # Single-type range structures (condors / butterflies) route to the range path.
        if strategy in _RANGE_STRATEGIES:
            return _adhoc_range(spec)
        if strategy not in ("PCS", "CCS", "IC"):
            return {"error": "strategy must be one of PCS, CCS, IC, "
                    "LONG_CALL, LONG_PUT, NAKED_CALL, NAKED_PUT, "
                    "VERT_CALL_DEBIT, VERT_PUT_DEBIT, "
                    "CONDOR_CALL, CONDOR_PUT, BUTTERFLY_CALL, BUTTERFLY_PUT"}
        if not expiration:
            return {"error": "expiration is required"}

        def _flt(key, required):
            raw = spec.get(key)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                return (None, f"{key} is required") if required else (None, None)
            try:
                return float(raw), None
            except (TypeError, ValueError):
                return None, f"{key} must be a number"

        short_strike, err = _flt("short_strike", True)
        if err:
            return {"error": err}
        long_strike, err = _flt("long_strike", True)
        if err:
            return {"error": err}
        call_short, err = _flt("call_short", strategy == "IC")
        if err:
            return {"error": err}
        call_long, err = _flt("call_long", strategy == "IC")
        if err:
            return {"error": err}
        entry_credit, err = _flt("entry_credit", False)
        if err:
            return {"error": err}
        if entry_credit is None:
            entry_credit = 0.0

        qty_raw = spec.get("quantity")
        if qty_raw is None or (isinstance(qty_raw, str) and not qty_raw.strip()):
            quantity = 1
        else:
            try:
                quantity = int(float(qty_raw))
            except (TypeError, ValueError):
                return {"error": "quantity must be an integer"}
        if quantity < 1:
            quantity = 1

        # max_loss_total from the spread width when not otherwise derivable.
        max_loss = None
        if short_strike is not None and long_strike is not None:
            try:
                max_loss = abs(short_strike - long_strike) * 100 * quantity
            except Exception:
                max_loss = None

        pos = {
            "position_id": "adhoc",
            "symbol": symbol,
            "strategy": strategy,
            "short_strike": short_strike,
            "long_strike": long_strike,
            "call_short": call_short,
            "call_long": call_long,
            "expiration": expiration,
            "entry_credit": entry_credit,
            "quantity": quantity,
            "max_loss_total": max_loss,
        }
        return _advisory_from_position(pos, source="adhoc",
                                       force_advisory=True, position_id="adhoc")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def assess_open_positions() -> dict:
    """Cheap risk pass over all OPEN paper positions using STORED marks only.

    No chain / gamma fetch — uses each position's already-stored
    current_underlying-equivalent (``current_value``/``unrealized_pnl``/
    ``current_short_delta``) fields. Returns
    ``{"per_position": {id: {state, heat}}, "summary": {n_tested, n_critical,
    position_ids}}``. Never raises."""
    per_position: dict = {}
    tested_ids: list = []
    n_critical = 0
    try:
        positions = _load_open_positions()
    except Exception:
        positions = []
    for pos in positions or []:
        try:
            pid = pos.get("position_id")
            mark = {
                "current_underlying": pos.get("current_underlying"),
                "current_value": pos.get("current_value"),
                "unrealized_pnl": pos.get("unrealized_pnl"),
                "current_short_delta": pos.get("current_short_delta"),
                "dte": _rescue_dte(pos.get("expiration")),
            }
            risk = _assess_position_risk(pos, mark, gex=None, regime=None)
            state = risk.get("state", "ok")
            per_position[pid] = {"state": state, "heat": risk.get("heat", 0.0)}
            if state in ("tested", "critical"):
                tested_ids.append(pid)
            if state == "critical":
                n_critical += 1
        except Exception:
            continue
    return {
        "per_position": per_position,
        "summary": {
            "n_tested": len(tested_ids),
            "n_critical": n_critical,
            "position_ids": tested_ids,
        },
    }


# ── Scheduled "trades needing action" digest (10:00 / 13:00 / 15:00 CT) ───────
# The manage cycle auto-closes at TAKE_PROFIT (>=50% of max profit captured) /
# MONEY_STOP (loss <= 200% of credit); these thresholds flag positions
# APPROACHING those bars so the thrice-daily push gives a heads-up FIRST.
_NEAR_TARGET_PCT = 40.0    # 40–50% of max profit captured
_NEAR_STOP_PCT = -150.0    # loss between 150% and 200% of credit (position-level)


def _capture_pct(pos):
    """Position-level P&L as a % of total entry credit, or None."""
    try:
        credit_total = float(pos.get("entry_credit") or 0) * float(pos.get("quantity") or 1) * 100.0
        upnl = pos.get("unrealized_pnl")
        if credit_total <= 0 or upnl is None:
            return None
        return float(upnl) / credit_total * 100.0
    except (TypeError, ValueError):
        return None


def collect_action_items(now_ct=None) -> dict:
    """Gather trades needing a human decision for the scheduled 10/1/3 CT push.

    Four categories (each independently guarded — a category failure never aborts
    the others; never raises):

    * ``captured_action`` — open captured signals whose live recommendation is
      CUT or TAKE_PROFIT (a fresh ``reprice_captured`` so the recs are current);
    * ``expiring_today`` — open ledger AND account trades expiring today (0-DTE);
    * ``at_risk`` — account positions the rescue engine tags tested/critical
      (short strike breached or near);
    * ``account_near`` — account positions approaching (not yet at) their
      auto-close stop or profit target.

    Returns ``{section: [ {symbol, strategy, ...}, ... ]}``. ``now_ct`` defaults to
    the live CT clock; inject for deterministic tests."""
    import datetime as _dt
    from zoneinfo import ZoneInfo

    now_ct = now_ct or _dt.datetime.now(ZoneInfo("America/Chicago"))
    today = now_ct.date().isoformat()
    out = {"captured_action": [], "expiring_today": [], "at_risk": [], "account_near": []}

    # 1. Captured signals recommending action (fresh reprice for current recs).
    try:
        rep = reprice_captured()
        for s in rep.get("signals", []) or []:
            rec = (s.get("recommendation") or "").upper()
            if rec in ("CUT", "TAKE_PROFIT"):
                out["captured_action"].append({
                    "symbol": s.get("symbol"), "strategy": s.get("strategy"),
                    "recommendation": rec, "expiration": s.get("expiration"),
                    "unrealized_pnl": s.get("unrealized_pnl"),
                    "reason": s.get("recommendation_reason")})
    except Exception:
        log.exception("collect_action_items: captured pass degraded")

    positions = _load_open_positions()   # account book, read once, reused below

    # 2. Expiring today (0-DTE) still open — ledger book …
    try:
        import paper_trader
        for t in paper_trader.get_all_trades():
            if (t.get("status") or "").upper() == "OPEN" and str(t.get("expiration"))[:10] == today:
                out["expiring_today"].append({
                    "book": "ledger", "symbol": t.get("symbol"),
                    "strategy": t.get("strategy"), "expiration": t.get("expiration"),
                    "unrealized_pnl": t.get("unrealized_pnl")})
    except Exception:
        log.exception("collect_action_items: ledger-expiring pass degraded")
    # … and account book.
    for p in positions or []:
        try:
            if str(p.get("expiration"))[:10] == today:
                out["expiring_today"].append({
                    "book": "account", "symbol": p.get("symbol"),
                    "strategy": p.get("strategy"), "expiration": p.get("expiration"),
                    "unrealized_pnl": p.get("unrealized_pnl")})
        except Exception:
            continue

    # 3 & 4. At-risk (rescue tested/critical) + near stop/target — account book.
    for p in positions or []:
        try:
            mark = {"current_underlying": p.get("current_underlying"),
                    "current_value": p.get("current_value"),
                    "unrealized_pnl": p.get("unrealized_pnl"),
                    "current_short_delta": p.get("current_short_delta"),
                    "dte": _rescue_dte(p.get("expiration"))}
            risk = _assess_position_risk(p, mark, gex=None, regime=None) or {}
            if (risk.get("state") or "").lower() in ("tested", "critical"):
                out["at_risk"].append({
                    "symbol": p.get("symbol"), "strategy": p.get("strategy"),
                    "rescue_state": (risk.get("state") or "").lower(),
                    "heat": risk.get("heat"), "unrealized_pnl": p.get("unrealized_pnl")})
            cap = _capture_pct(p)
            if cap is not None:
                if _NEAR_TARGET_PCT <= cap < 50.0:
                    out["account_near"].append({
                        "symbol": p.get("symbol"), "strategy": p.get("strategy"),
                        "note": f"{cap:.0f}% of target"})
                elif -200.0 < cap <= _NEAR_STOP_PCT:
                    out["account_near"].append({
                        "symbol": p.get("symbol"), "strategy": p.get("strategy"),
                        "note": f"loss {abs(cap):.0f}% of credit (near stop)"})
        except Exception:
            continue

    return out
