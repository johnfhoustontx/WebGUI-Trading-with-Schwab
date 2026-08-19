"""Options service rescan handler (Tier-2 → Tier-3 write path).

The service-side analog of ``webgui/pages/options/scanner.py``'s scan call.
Instead of holding the result in an in-process page state, it computes via
``compute.run_scan`` (a dict), projects it onto the ``ScanResult`` contract as a
validation gate (so gross shape drift raises loudly BEFORE caching), writes the
full validated payload into the Redis bus (Tier 3) under ONE cache key, and
publishes a change event for the GUI to react to.

Single cache view (``cache:options:scan``): a single scan produces BOTH the
0-DTE and swing signal lists, so one cache view holds the whole result. The
GUI's two tabs (0-DTE / Swing) both read from it — there is no scan/swing split.

Kept synchronous: the scaffold's consumer loop handles sync handlers.
"""
import datetime as _dt
import logging
import time

from services.options_svc import compute
from services.options_svc import flow_alerts
from services.options_svc import push_notify
from shared import market_calendar as mc
from shared.notify.channels import _today_ct
from shared.contracts.options import NetPremiumSnapshot, ScanResult

log = logging.getLogger(__name__)

# ── R5: stale trade-opening command gate ────────────────────────────────────
# A trade-OPENING command (``driver_paper_create`` / ``paper_create``) consumed
# long after it was enqueued (e.g. the service was down for hours and the stream
# replays it on restart) would open on STALE economics — corrupting the measured
# book. We reject any opening command whose enqueue ``ts`` is older than this.
# Idempotent refresh/manage/reset commands are NOT gated (re-running them is safe).
# Missing ts (a legacy command serialized before the field existed) → treated as
# fresh (never reject a legacy command). See shared/contracts/envelope.Command.ts.
STALE_OPEN_MAX_AGE_SEC = 180  # 3 minutes

# ── R1: surfaced per-trade open results ──────────────────────────────────────
# ``compute.open_driver_position`` never raises and returns
# {"status": "opened"|"rejected"|"error", ...}. Historically the handler THREW
# THE RESULT AWAY, so a signal-shape drift / broker rejection / MIN_FILL_CREDIT
# reject showed "executed" in the decision log while the account stayed empty
# (the [[driver-feeds-raw-scanner-signal-shape]] incident). We now capture every
# open outcome into this rolling list (single-user, single-process service) and
# surface it on the driver account view so the /driver page shows opened/rejected/
# error PER trade, not just "enqueued". Also holds R5 stale-reject records.
_LAST_OPEN_RESULTS: list[dict] = []
_MAX_OPEN_RESULTS = 25

# ``paper_adjust`` (the rescue-apply primitives) lives in options-scanner and
# transitively pulls in ``paper_engine`` → ``scoring``. Importing it at module top
# would (a) require options-scanner on ``sys.path`` before ``compute`` adds it, and
# (b) bind the process-wide ``sys.modules['scoring']`` to options-scanner's module
# merely by importing this module — which breaks the sentiment service's ``scoring``
# package in the *combined* pytest run (all services share one process). So it is
# imported LAZILY inside ``run_rescue_apply`` (mirrors ``compute``'s lazy-import
# convention for ``paper_engine``/``paper_account_db``/``signal_db``). ``compute``
# (imported above) has already put OPTIONS_SCANNER on ``sys.path`` by the time the
# handler runs, so the lazy import resolves. The module attribute is created on
# first use so tests can ``monkeypatch.setattr(handlers, "paper_adjust", ...)``.
paper_adjust = None


def _paper_adjust():
    """Lazy-import + cache the ``paper_adjust`` engine module (see note above)."""
    global paper_adjust
    if paper_adjust is None:
        import paper_adjust as _pa
        paper_adjust = _pa
    return paper_adjust

def _record_open_result(result: dict) -> None:
    """Append one open outcome (R1) to the rolling surfaced-results list.

    Stamps a ``ts`` if absent so the /driver page can order/age them. Trims to the
    last ``_MAX_OPEN_RESULTS`` (newest last). Pure list bookkeeping — never raises."""
    try:
        rec = dict(result or {})
        rec.setdefault("ts", _dt.datetime.now(_dt.timezone.utc).isoformat())
        _LAST_OPEN_RESULTS.append(rec)
        if len(_LAST_OPEN_RESULTS) > _MAX_OPEN_RESULTS:
            del _LAST_OPEN_RESULTS[:-_MAX_OPEN_RESULTS]
    except Exception:  # noqa: BLE001 — surfacing must never break the open path.
        log.exception("recording open result degraded")


def _command_age_seconds(command) -> float | None:
    """Age of ``command`` in seconds from its enqueue ``ts``, or None when the ts
    is missing/unparseable (→ caller treats as NOT stale, back-compat).

    ``Command.ts`` is an ISO-8601 UTC stamp set at enqueue time; a legacy command
    serialized before the field existed decodes with a fresh stamp (age ~0), and
    an explicit ``ts=None`` yields None here."""
    ts = getattr(command, "ts", None)
    if not ts:
        return None
    try:
        when = _dt.datetime.fromisoformat(ts)
        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        return (_dt.datetime.now(_dt.timezone.utc) - when).total_seconds()
    except Exception:  # noqa: BLE001 — unparseable ts → treat as not-stale.
        log.warning("command ts unparseable (%r); treating as fresh", ts)
        return None


def _is_stale_open(command) -> bool:
    """True if a trade-OPENING command is older than ``STALE_OPEN_MAX_AGE_SEC``.

    Missing/unparseable ts → False (never reject a legacy command)."""
    age = _command_age_seconds(command)
    return age is not None and age > STALE_OPEN_MAX_AGE_SEC


CACHE_SCAN = "cache:options:scan"
EVENT_SCAN = "events:options:scan"

# The day's accumulated signal union — read by the Scanner page ONLY. Deliberately
# a SEPARATE key from CACHE_SCAN: the autonomous driver reads CACHE_SCAN and must
# never be offered a signal that no longer qualifies, so that key stays live-only.
CACHE_SCAN_DAY = "cache:options:scan_day"
EVENT_SCAN_DAY = "events:options:scan_day"

CACHE_HEADER = "cache:options:header"
EVENT_HEADER = "events:options:header"

CACHE_SWING = "cache:options:swing"
EVENT_SWING = "events:options:swing"

CACHE_PAPER = "cache:options:paper_account"
EVENT_PAPER = "events:options:paper_account"
# Manual (scanner-baseline) book performance analytics — the benchmark to compare the
# driver's Claude-selected book against (equity curve + MAE/MFE; no posture post-mortem).
CACHE_PAPER_ANALYTICS = "cache:options:paper_analytics"
EVENT_PAPER_ANALYTICS = "events:options:paper_analytics"

CACHE_PAPER_TRADES = "cache:options:paper_trades"
EVENT_PAPER_TRADES = "events:options:paper_trades"

CACHE_PAPER_ANALYZE = "cache:options:paper_analyze"
EVENT_PAPER_ANALYZE = "events:options:paper_analyze"

CACHE_CAPTURED = "cache:options:captured"
EVENT_CAPTURED = "events:options:captured"

CACHE_CAPTURED_FLAGS = "cache:options:captured_flags"
EVENT_CAPTURED_FLAGS = "events:options:captured_flags"

# Captured auto-manage: today's closed captured outcomes (EOD page) + the
# Settings auto-close master toggle (default ON; only an explicit False disables).
CACHE_CAPTURED_CLOSED = "cache:options:captured_closed"
EVENT_CAPTURED_CLOSED = "events:options:captured_closed"
CACHE_AUTOCLOSE_ENABLED = "cache:options:autoclose_enabled"

# MANUAL paper account's opt-in break-even lifecycle toggle — the INVERSE
# default of CACHE_AUTOCLOSE_ENABLED (default OFF; only an explicit True
# enables). The DRIVER's isolated account never reads this key.
CACHE_MANUAL_PAPER_LIFECYCLE = "cache:options:manual_paper_lifecycle"

# Date-scoped seen-sets for server-side phone push (Telegram/Discord/Fi-SMS).
# See services/options_svc/push_notify.py.
CACHE_NOTIFIED_SCAN = "cache:options:notified_scan"
CACHE_NOTIFIED_CAPTURED = "cache:options:notified_captured"

CACHE_ACTION_ALERT = "cache:options:action_alert"
EVENT_ACTION_ALERT = "events:options:action_alert"

CACHE_MARKET_SNAPSHOT = "cache:options:market_snapshot"

CACHE_EOD_SUMMARY = "cache:options:eod_summary"
EVENT_EOD_SUMMARY = "events:options:eod_summary"

CACHE_GAMMA = "cache:options:gamma"
EVENT_GAMMA = "events:options:gamma"

CACHE_GAMMA_EXPLAIN = "cache:options:gamma_explain"
EVENT_GAMMA_EXPLAIN = "events:options:gamma_explain"

CACHE_GAMMA_ANALYZE = "cache:options:gamma_analyze"
EVENT_GAMMA_ANALYZE = "events:options:gamma_analyze"

# Scheduled (auto-run) $SPX/SPY/QQQ Gamma Analyze briefings — one cache key per
# daily slot so each is independently viewable and persists in Redis until the next
# day's run overwrites it. Kept SEPARATE from CACHE_GAMMA_ANALYZE (the ad-hoc button
# key) so a scheduled run does NOT trip an open Gamma page's _watch_analyze (which
# would auto-open a browser tab). Driven by scheduler.analyze_slot_due at premarket /
# ~18 min after the open / midday / close on each trading day.
ANALYZE_SLOT_TITLES = {
    "premarket": "Premarket",
    "open": "After open",
    "midday": "Midday",
    "close": "EOD recap",
}
CACHE_GAMMA_ANALYZE_SCHED = {
    slot: f"cache:options:gamma_analyze_{slot}" for slot in ANALYZE_SLOT_TITLES}
EVENT_GAMMA_ANALYZE_SCHED = {
    slot: f"events:options:gamma_analyze_{slot}" for slot in ANALYZE_SLOT_TITLES}

# Gamma briefing HISTORY (the persisted Auto/ad-hoc briefings) — an index of stored
# briefings for the in-app picker, and the latest on-demand regenerated history report
# (HTML rebuilt from the stored structured analysis via the gamma_history command).
CACHE_GAMMA_BRIEFINGS = "cache:options:gamma_briefings"
EVENT_GAMMA_BRIEFINGS = "events:options:gamma_briefings"
CACHE_GAMMA_HISTORY = "cache:options:gamma_history"
EVENT_GAMMA_HISTORY = "events:options:gamma_history"

CACHE_GAMMA_SYMBOLS = "cache:options:gamma_symbols"
EVENT_GAMMA_SYMBOLS = "events:options:gamma_symbols"

CACHE_SIM_META = "cache:options:sim_meta"
EVENT_SIM_META = "events:options:sim_meta"

CACHE_SIM_RESULT = "cache:options:sim_result"
EVENT_SIM_RESULT = "events:options:sim_result"

CACHE_SIM_REPLAY = "cache:options:sim_replay"
EVENT_SIM_REPLAY = "events:options:sim_replay"

CACHE_CALC_CHAIN = "cache:options:calc_chain"
EVENT_CALC_CHAIN = "events:options:calc_chain"

CACHE_CALC_RESULT = "cache:options:calc_result"
EVENT_CALC_RESULT = "events:options:calc_result"

CACHE_CALC_IV = "cache:options:calc_iv"
EVENT_CALC_IV = "events:options:calc_iv"

CACHE_GEX_STATUS = "cache:options:gex_status"
EVENT_GEX_STATUS = "events:options:gex_status"

# Per-index 25-delta options-flow skew (level + change since the prior snapshot).
# No strict contract (mirrors gex_status): a small read-only dict the sentiment
# service reads defensively as an aggression input. Published on each 2-min GEX
# tick right after collection (rides collect_gex_history).
CACHE_FLOW_SKEW = "cache:options:flow_skew"
EVENT_FLOW_SKEW = "events:options:flow_skew"

# Options Matrix — a per-symbol hotness roll-up (signal/alert counts + flip) built
# from the just-written GEX rows. Published on each 1-min GEX tick right after the
# flow-skew + flow-alert publishes (rides collect_gex_history). skip_unchanged so an
# unchanged matrix doesn't wake GUI version-pollers.
CACHE_MATRIX = "cache:options:matrix"
EVENT_MATRIX = "events:options:matrix"

# Net Prem — intraday net options premium per symbol for the Dealer Positioning
# "Net Prem" view. Published on each 1-min GEX tick right after the matrix, over
# the rows collect_gex_snapshots just wrote. Its own read (NOT a slice of the
# matrix's) — see publish_net_premium's docstring for why they cannot share.
CACHE_NET_PREMIUM = "cache:options:net_premium"
EVENT_NET_PREMIUM = "events:options:net_premium"

# Options-flow alerts (premium crossover + unusual call/put activity). Detected on
# each 1-min GEX tick over the collected universe, pushed to the phone (best-effort),
# and published as a day-scoped rolling list for the webgui popup/badge. The cooldown
# map is persisted in its own key so a signal doesn't re-fire every minute.
CACHE_FLOW_ALERTS = "cache:options:flow_alerts"
EVENT_FLOW_ALERTS = "events:options:flow_alerts"
_FLOW_COOLDOWN_KEY = "cache:options:flow_alert_cooldowns"
# Per-symbol last-alerted gamma regime ('positive'/'negative'), date-scoped so the
# first snapshot each day sets the baseline without firing an open-time alert.
_GAMMA_REGIME_KEY = "cache:options:gamma_regime_state"
# Cap on the day's published list. 50 dropped the morning's alerts before anyone
# could read them; ~45 symbols with a 30-min crossover cooldown, a $5M UOA floor
# and 4 gamma-flip names fit comfortably in 300.
_FLOW_ALERTS_MAX = 300
# Trailing flow rows the crossover detector needs (it compares the last two
# premium-bearing rows; a couple extra guard against a forward-only NULL-premium
# row). UOA no longer reads the series — it rides the poll's on_chain stash — so
# this is small (the aggregate volume-spike detector that needed ~22 rows is gone).
_FLOW_CROSSOVER_TAIL = 4

CACHE_EXPECTED_MOVE = "cache:options:expected_move"
EVENT_EXPECTED_MOVE = "events:options:expected_move"

CACHE_EM_CHAIN = "cache:options:em_chain"
EVENT_EM_CHAIN = "events:options:em_chain"

CACHE_RESCUE = "cache:options:rescue"            # per-id: f"{CACHE_RESCUE}:{position_id}"
CACHE_RESCUE_SUMMARY = "cache:options:rescue_summary"
EVENT_RESCUE = "events:options:rescue"
EVENT_RESCUE_SUMMARY = "events:options:rescue_summary"

# Isolated driver paper account (a SEPARATE DB from the manual paper_account.db —
# see compute.DRIVER_PAPER_DB). Two views: the account snapshot + open positions,
# and the standalone performance scorecard. The autonomous Driver enqueues
# ``driver_paper_create`` and the /driver page reads these views.
CACHE_DRIVER_PAPER = "cache:options:driver_paper_account"
EVENT_DRIVER_PAPER = "events:options:driver_paper_account"
CACHE_DRIVER_PERF = "cache:options:driver_paper_perf"
EVENT_DRIVER_PERF = "events:options:driver_paper_perf"
# Driver-book performance ANALYTICS (equity curve + posture post-mortem + MAE/MFE).
CACHE_DRIVER_ANALYTICS = "cache:options:driver_paper_analytics"
EVENT_DRIVER_ANALYTICS = "events:options:driver_paper_analytics"

# Defaults mirror the page's input defaults (symbol SPY, 5-30 DTE, the put/call
# delta gates, min credit 10% -> 0.10 fraction). The page sends the fraction.
_SWING_DEFAULTS = {
    "symbol": "SPY",
    "dte_min": 5,
    "dte_max": 30,
    "put_d_min": -0.20,
    "put_d_max": -0.10,
    "call_d_min": 0.10,
    "call_d_max": 0.20,
    "min_cr_fraction": 0.10,
    "families": None,
}

# The fields ScanResult validates — we project the engine dict onto exactly
# these (dropping the extra keys the GUI ignores). ``.get`` with a default of
# the field's container type keeps missing optional keys from crashing, while
# the ScanResult construction validates the *types* of whatever is present.
_SCAN_DEFAULTS = {
    "signals_0dte": [],
    "signals_swing": [],
    # Single-leg directional candidates ride their own list (own scorer, own
    # signal shape) — see ScanResult's docstring.
    "signals_directional": [],
    "vix_term_structure": {},
    "timestamp": None,
    "errors": [],
    "warnings": [],
}


def rescan(bus) -> None:
    """Run a scan, validate its shape, cache the full result, publish an event."""
    result = compute.run_scan()

    # Validation gate: project onto ScanResult fields and construct the model so
    # a gross shape drift (e.g. signals_0dte not a list) raises BEFORE caching.
    scan = ScanResult(**{k: result.get(k, default)
                         for k, default in _SCAN_DEFAULTS.items()})

    # One cache view holds the whole result (both signal lists + metadata).
    version = bus.cache_set(CACHE_SCAN, scan.model_dump())
    bus.publish(EVENT_SCAN, {"version": version})

    # Day-persistent union for the Scanner page. A SEPARATE key on purpose:
    # cache:options:scan stays live-only because the autonomous driver reads it
    # and must never be offered a signal that no longer qualifies. Best-effort —
    # a merge failure must not break the live publish above.
    try:
        # CT, via the SAME helper push_notify uses below — rescan would otherwise
        # carry three date bases (scheduler: CT, push_notify: CT, this: local).
        # NOT scheduler.active_session_date(): that flips to today at 08:30 CT
        # (it is tuned to the GEX collector's window) while scans start at 08:00,
        # so the 08:00/08:15 scans would read as YESTERDAY and the 08:30 one would
        # look like a date change and wipe the morning's union.
        today = _today_ct()
        prev = bus.cache_get(CACHE_SCAN_DAY)
        day = compute.merge_day_signals(
            prev.payload if prev is not None else None, scan.model_dump(), today)
        day_version = bus.cache_set(CACHE_SCAN_DAY, day)
        bus.publish(EVENT_SCAN_DAY, {"version": day_version})
    except Exception:  # noqa: BLE001
        log.exception("scan day-union merge failed (non-fatal)")

    # Server-side phone push on genuinely-new signals (Telegram/Discord/Fi-SMS).
    # Best-effort; must never break the scan/publish path. First run after start
    # seeds silently so a restart mid-session doesn't blast every open signal.
    try:
        all_sigs = (scan.signals_0dte or []) + (scan.signals_swing or [])
        seed = push_notify.load_seen(bus, CACHE_NOTIFIED_SCAN) is None
        push_notify.notify_signals(bus, all_sigs, kind="scanner",
                                   seen_key=CACHE_NOTIFIED_SCAN, seed=seed)
    except Exception:  # noqa: BLE001
        log.exception("scanner push-notify failed (non-fatal)")


def refresh_header(bus) -> None:
    """Compute the compact header view and publish it to the bus.

    No strict contract: the header view is a small, loosely-shaped read-only dict
    (prices + vix + regime + sentiment dot) that only the header strip consumes,
    so a Pydantic gate would be ceremony with no payoff (YAGNI). ``refresh_header``
    is already fully defensive — it never returns a malformed shape — which is the
    invariant the ScanResult gate exists to enforce for the heavier scan path."""
    data = compute.refresh_header()
    # Per-tick republisher: skip the version bump + publish when quotes/regime are
    # byte-identical to the last write, so an unchanged header doesn't wake the
    # GUI's version-poller into a needless repaint.
    bus.cache_set(CACHE_HEADER, data, event=EVENT_HEADER, skip_unchanged=True)
    # Best-effort ~30s live spot/day% overlay onto the 1-min matrix. Guarded so a
    # matrix/quote failure can NEVER break the header refresh.
    try:
        refresh_matrix_spots(bus)
    except Exception:
        log.exception("refresh_matrix_spots after header degraded")


def refresh_matrix_spots(bus) -> None:
    """Overlay live spot + day% onto the last-published matrix (best-effort).

    Rides the ~30 s header tick: reads the last matrix, does ONE batched quote
    fetch for its symbols, overlays live spot/day% via ``compute.apply_live_spots``,
    and republishes with ``skip_unchanged`` so the Spot/Day% columns feel live
    without re-decoding grids. Fully defensive — no matrix yet / empty rows / quote
    failure / missing symbol → a no-op that never raises. Deep-copies each row
    before overlay so the cached-read payload's dicts are never aliased/mutated."""
    try:
        env = bus.cache_get(CACHE_MATRIX)
        if env is None or not (env.payload or {}).get("rows"):
            return
        payload = dict(env.payload)
        payload["rows"] = [dict(r) for r in payload.get("rows", [])]
        symbols = [r["symbol"] for r in payload["rows"] if r.get("symbol")]
        if not symbols:
            return
        raw = compute.matrix_quotes(symbols)
        view = compute.apply_live_spots(payload, raw)
        bus.cache_set(CACHE_MATRIX, view, event=EVENT_MATRIX, skip_unchanged=True)
    except Exception:
        log.exception("refresh_matrix_spots degraded")


def swing_scan(bus, args: dict) -> None:
    """Run a user-parameterized swing scan, cache the result, publish an event.

    On-demand only (not scheduled): the GUI Swing page enqueues a ``swing_scan``
    command with the user's inputs in ``args``; we extract them (falling back to
    the page-default for any missing key), call ``compute.swing_scan`` with the
    raw client objects, and cache the scored ``signals`` + the inferred market
    ``view`` (plus the symbol + original args, for the page to display/debug)
    under ``cache:options:swing``.

    ``filtered_out`` (how many scored candidates compute's quality cut dropped)
    is carried through so the page can tell "nothing cleared the quality bar"
    apart from "the scan found nothing"; it defaults to 0 rather than being
    omitted, so the page never has to special-case a missing key.

    No ScanResult gate: this is a flat signal list (not the dual-list scan
    contract), and the page reads ``payload["signals"]`` directly.

    Cross-service read: the live committed five-state market classifier lives in
    ``cache:sentiment:composite`` under ``derived.trend.state`` (published by the
    sentiment service). We read it DEFENSIVELY here — every level guarded, any
    missing level / absent composite -> ``None`` -> ``compute.swing_scan`` applies
    no family-ranking tilt — and pass it as ``market_state``. This keeps
    ``compute.swing_scan`` proxy-only (no bus dependency); the handler is the
    natural cross-service reader (mirrors the ``cache_get`` reads in
    ``remove_closed_from_captured`` / ``refresh_gamma_current``)."""
    args = args or {}
    params = {k: args.get(k, default) for k, default in _SWING_DEFAULTS.items()}
    env = bus.cache_get("cache:sentiment:composite")
    payload = env.payload if env is not None else None
    market_state = (((payload or {}).get("derived") or {}).get("trend") or {}).get("state")
    result = compute.swing_scan(**params, market_state=market_state)
    payload = {"signals": result["signals"], "view": result.get("view"),
               "filtered_out": result.get("filtered_out") or 0,
               "symbol": params["symbol"], "params": args}
    version = bus.cache_set(CACHE_SWING, payload)
    bus.publish(EVENT_SWING, {"version": version})


def _coerce_pid(pid):
    """Coerce a position id to int for overlay lookup (rows/keys may be int or
    str). Returns None when it can't be coerced (so the lookup falls back to
    the 'ok' default)."""
    try:
        return int(pid)
    except (TypeError, ValueError):
        return None


def _apply_rescue_overlay(data) -> None:
    """Tag each open-position row in the paper-account view with ``rescue_state``
    + ``heat`` from ``compute.assess_open_positions`` (cheap — stored marks only,
    no chain fetch). Mutates ``data["positions"]`` in place. Fully defensive: any
    failure leaves the view unchanged so the core paper refresh still publishes.

    ``per_position`` is keyed by ``position_id`` (possibly int); the row's
    ``position_id`` field may be int or str — both are coerced to int to match."""
    try:
        per_position = compute.assess_open_positions().get("per_position", {}) or {}
        # Re-key by int so an int-row / str-row id both resolve.
        overlay = {_coerce_pid(k): v for k, v in per_position.items()}
        for row in data.get("positions") or []:
            entry = overlay.get(_coerce_pid(row.get("position_id"))) or {}
            row["rescue_state"] = entry.get("state", "ok")
            row["heat"] = entry.get("heat", 0.0)
    except Exception:
        # Never let the overlay break the core paper-account publish.
        log.exception("rescue overlay degraded (paper-account publish continues)")


def refresh_paper_account(bus) -> None:
    """Read the paper account view, tag rows with the rescue overlay, publish it.

    No strict contract: the view is a loosely-shaped read-only dict (snapshot +
    positions + orders + has_account flag) that only the Paper Portfolio page
    consumes, and ``compute.paper_account_view`` is already fully defensive. The
    overlay ADDS ``rescue_state``/``heat`` per position row (Task 6.2) without
    changing the existing view shape."""
    data = compute.paper_account_view()
    _apply_rescue_overlay(data)
    version = bus.cache_set(CACHE_PAPER, data)
    bus.publish(EVENT_PAPER, {"version": version})
    # Manual-book analytics (equity curve + MAE/MFE) — defensive; can't block the account
    # republish above.
    try:
        analytics = compute.manual_analytics()
        va = bus.cache_set(CACHE_PAPER_ANALYTICS, analytics)
        bus.publish(EVENT_PAPER_ANALYTICS, {"version": va})
    except Exception:
        log.exception("manual analytics publish degraded")


def publish_rescue_summary(bus) -> None:
    """Publish the small rescue summary view for the nav badge (Task 6.2).

    Reuses ``compute.assess_open_positions`` (cheap — stored marks only) and
    caches its ``summary`` (``n_tested``/``n_critical``/``position_ids``) under
    ``cache:options:rescue_summary``, skipping the version bump when unchanged so
    an idle manage tick doesn't wake the GUI's badge poller."""
    res = compute.assess_open_positions()
    summary = res.get("summary", {"n_tested": 0, "n_critical": 0, "position_ids": []})
    bus.cache_set(CACHE_RESCUE_SUMMARY, summary,
                  event=EVENT_RESCUE_SUMMARY, skip_unchanged=True)


def run_manage_and_refresh(bus) -> None:
    """Run the paper auto-manage cycle (reprice open positions + auto-close
    target/stop hits + expiration-settle) then republish the paper views.

    Guarded on an existing account — with no account it just refreshes so the
    page shows the no-account state. Also auto-settles expired **ledger** trades
    (``compute.expire_ledger_trades`` — the Paper Trades tab otherwise never
    closes on expiration) and republishes the ledger view when any settled.
    Shared by the ``paper_manage`` command (manual button) and the scheduler's
    auto-manage tick (``scheduler.manage_due``) so both run identical logic.

    Threads ``manual_paper_lifecycle_enabled(bus)`` (the Settings toggle, default
    OFF) into ``compute.run_manage_cycle(lifecycle=...)`` — this is the ONE
    chokepoint both the manual button and the scheduler pass through, so the
    manual paper account's opt-in break-even lifecycle needs no separate wiring
    at either call site. The DRIVER's isolated account is untouched here (see
    ``run_driver_manage_and_refresh``, which never reads this flag)."""
    if compute.has_paper_account():
        compute.run_manage_cycle(lifecycle=manual_paper_lifecycle_enabled(bus))
    # Auto-settle expired LEDGER trades (the Paper Trades tab otherwise never
    # closes on expiration). Defensive so a settlement hiccup never skips the
    # refreshes below; the piggyback ledger refresh further down republishes the
    # settled rows.
    try:
        compute.expire_ledger_trades()
    except Exception:
        log.exception("expire_ledger_trades degraded")
    refresh_paper_account(bus)
    # Piggyback the manage tick (no new cadence): refresh the paper-trade LEDGER
    # too so its open positions get fresh live P&L every 5 min (and reflect any
    # expiration settlement above; the account view is a separate book).
    # Defensive so a reprice hiccup never blocks the core paper refresh.
    try:
        refresh_paper_trades(bus)
    except Exception:
        log.exception("piggyback refresh_paper_trades degraded")
    # Also publish the rescue summary for the nav badge.
    try:
        publish_rescue_summary(bus)
    except Exception:
        log.exception("publish_rescue_summary degraded")


def run_paper_entry_and_manage(bus) -> None:
    """Hourly MANUAL Paper Portfolio cycle: open new paper trades from the current
    captured signals, then reprice open positions + auto-close hits and republish.

    Entry mirrors the ``paper_entry`` command (guarded on an existing account, in
    its OWN try/except so an entry failure can NEVER skip the manage/refresh);
    manage reuses ``run_manage_and_refresh`` (which itself guards on an account
    and republishes the paper account + ledger + rescue-summary views). Shared by
    the scheduler's hourly ``paper_cycle_due`` tick (09:00–14:00 CT) — this is the
    manual account's cadence; the isolated DRIVER account stays on the 5-min
    ``manage_due`` slot."""
    if compute.has_paper_account():
        try:
            compute.run_entry_cycle()
        except Exception:
            log.exception("hourly paper entry cycle degraded (manage still runs)")
    run_manage_and_refresh(bus)


def refresh_driver_paper(bus) -> None:
    """Publish the isolated driver paper account view + its performance scorecard.

    Two views — the account snapshot/positions (``cache:options:driver_paper_account``)
    and the standalone scorecard (``cache:options:driver_paper_perf``) — each on its
    own change event. The /driver monitor reads them to show the driver's OWN book
    (day-P&L, open positions) and how it's performing.

    Deliberately does NOT apply the rescue overlay (``_apply_rescue_overlay`` /
    ``compute.assess_open_positions``): that overlay reads the MANUAL paper account,
    so tagging the driver's rows with it would attach heat/state from the wrong
    book. Both ``compute.driver_account_view`` and ``compute.driver_account_perf``
    are already fully defensive (degrade, never raise).

    R1: the view carries ``last_open_results`` — the rolling per-trade open
    outcomes (opened/rejected/error, with reason) — so the /driver decision log
    can show WHY a driver open didn't land, instead of a bare "enqueued"."""
    # Read the driver book's full positions + snapshot ONCE, shared by all three
    # views below (was 3× fetch_all_positions + 2× account_snapshot per refresh).
    positions, snapshot = compute.driver_shared_reads()
    acct = compute.driver_account_view(all_positions=positions, snapshot=snapshot)
    if isinstance(acct, dict):
        # Surface a COPY so a later append can't mutate the cached snapshot.
        acct["last_open_results"] = list(_LAST_OPEN_RESULTS)
    va = bus.cache_set(CACHE_DRIVER_PAPER, acct)
    bus.publish(EVENT_DRIVER_PAPER, {"version": va})
    perf = compute.driver_account_perf(positions=positions, snapshot=snapshot)
    vp = bus.cache_set(CACHE_DRIVER_PERF, perf)
    bus.publish(EVENT_DRIVER_PERF, {"version": vp})
    # Performance analytics (equity curve / posture post-mortem / MAE-MFE). Defensive —
    # a bad payload can't block the account/perf republish above.
    try:
        analytics = compute.driver_analytics(positions=positions)
        va2 = bus.cache_set(CACHE_DRIVER_ANALYTICS, analytics)
        bus.publish(EVENT_DRIVER_ANALYTICS, {"version": va2})
    except Exception:
        log.exception("driver analytics publish degraded")


def run_driver_manage_and_refresh(bus) -> None:
    """5-min driver-account manage tick: reprice + auto-close the driver's open
    positions (``compute.run_driver_manage_cycle`` — no-op-safe if the driver
    account doesn't exist yet) then republish both driver views.

    The driver analog of ``run_manage_and_refresh`` — shared by the
    ``driver_paper_manage`` command and the scheduler's 5-min manage tick so both
    run identical logic. No rescue summary piggyback (that is the manual book's
    nav badge)."""
    compute.run_driver_manage_cycle()
    refresh_driver_paper(bus)


def refresh_paper_trades(bus, reprice: bool = True) -> None:
    """Read the paper-trade ledger view and publish it to the bus.

    ``reprice`` (default True) attaches a live ``unrealized_pnl`` to each OPEN
    trade during market hours so the Paper Trades page shows running P&L instead
    of a blank column (the reprice is skipped off-hours inside the view). No strict
    contract: the view is a loosely-shaped read-only dict (``{"trades": [...]}``)
    that only the Paper Trades page consumes, and ``compute.paper_trades_view`` is
    already fully defensive."""
    data = compute.paper_trades_view(reprice=reprice)
    version = bus.cache_set(CACHE_PAPER_TRADES, data)
    bus.publish(EVENT_PAPER_TRADES, {"version": version})


def _publish_captured(bus, signals) -> None:
    """Cache + publish the captured view, ALWAYS carrying the day footer block.

    Every writer of ``CACHE_CAPTURED`` goes through here. Two of the three
    rebuild the payload from scratch (a reprice replaces the signal list, a
    close filters it), so before this helper existed they silently dropped the
    ``day`` block and the page's footer went blank until the next full refresh
    — a failure only visible by opening the page after one of those actions.
    The summary is RE-READ on every publish rather than carried over, because a
    close changes the day's closed count and booked total as it happens.
    """
    version = bus.cache_set(CACHE_CAPTURED,
                            {"signals": signals, "day": compute.captured_day_summary()})
    bus.publish(EVENT_CAPTURED, {"version": version})


def refresh_captured(bus) -> None:
    """Read the open-signals view and publish it to the bus.

    No strict contract: the view is a loosely-shaped read-only dict
    (``{"signals": [...], "day": {...}}``) that only the Captured Signals page
    consumes, and ``compute.captured_view`` is already fully defensive."""
    data = compute.captured_view()
    _publish_captured(bus, (data or {}).get("signals") or [])
    _notify_captured(bus, (data or {}).get("signals"))


def _notify_captured(bus, signals) -> None:
    """Best-effort server-side phone push on new captured signals.

    Shared by ``refresh_captured`` and the ``captured_reprice`` command path so the
    notify logic is DRY. Never fatal — a notify failure must not stop the publish.
    First run after start seeds silently (see ``push_notify.notify_signals``)."""
    try:
        seed = push_notify.load_seen(bus, CACHE_NOTIFIED_CAPTURED) is None
        push_notify.notify_signals(bus, signals or [], kind="captured",
                                   seen_key=CACHE_NOTIFIED_CAPTURED, seed=seed)
    except Exception:  # noqa: BLE001
        log.exception("captured push-notify failed (non-fatal)")


def publish_captured_closed(bus) -> None:
    """Publish today's closed-captured outcomes view (``cache:options:captured_closed``).

    Read-only — published regardless of the auto-close toggle, since MANUAL closes
    land in ``signal_outcomes`` too (the EOD 'Captured — closed today' section reads
    this). ``compute.captured_closed_today`` is fully defensive."""
    data = compute.captured_closed_today()
    version = bus.cache_set(CACHE_CAPTURED_CLOSED, data)
    bus.publish(EVENT_CAPTURED_CLOSED, {"version": version})


def autoclose_enabled(bus) -> bool:
    """Whether captured auto-close is enabled. Defaults **True** on a missing /
    unreadable key — so only an EXPLICIT ``{"enabled": False}`` (from the Settings
    toggle write-through) disables the cycle; a wiped Memurai resumes auto-close."""
    try:
        env = bus.cache_get(CACHE_AUTOCLOSE_ENABLED)
        if env is None:
            return True
        return bool((env.payload or {}).get("enabled", True))
    except Exception:  # noqa: BLE001
        return True


def manual_paper_lifecycle_enabled(bus) -> bool:
    """Whether the MANUAL paper account has opted into the captured-style
    break-even lifecycle (arm at +50% credit, ride under a break-even stop).
    Defaults **False** on a missing / unreadable key — the INVERSE of
    ``autoclose_enabled`` — so only an EXPLICIT ``{"enabled": True}`` (from the
    Settings toggle write-through) opts in; a wiped Memurai reverts to today's
    plain TAKE_PROFIT-at-+50%. The DRIVER's isolated account never reads this
    flag (see ``compute.run_driver_manage_cycle``, which always passes
    ``lifecycle=False``)."""
    try:
        env = bus.cache_get(CACHE_MANUAL_PAPER_LIFECYCLE)
        if env is None:
            return False
        return bool((env.payload or {}).get("enabled", False))
    except Exception:  # noqa: BLE001
        return False


def _notify_captured_closes(bus, closed) -> None:
    """Best-effort record of the cycle's auto-closes (never fatal).

    The refreshed open + closed views already repaint the GUI; this logs each close
    (symbol / reason) so the server trace shows what the unattended cycle did."""
    for c in closed or []:
        log.info("captured auto-close: %s %s @ %s",
                 c.get("symbol"), c.get("reason"), c.get("exit_val"))


def run_captured_manage_and_publish(bus) -> None:
    """Run the captured auto-manage cycle then republish the open + closed views.

    Reprices/arms/auto-closes the OPEN captured signals
    (``compute.run_captured_manage_cycle`` — paper-only, writes outcomes, never a
    broker order), republishes the open-signals view (``refresh_captured`` — which
    also phone-pushes any NEW signal) and the closed-today view
    (``publish_captured_closed``). Shared by the ``captured_manage`` command (manual
    'run now') and the scheduler's 5-min captured-manage tick. Each close is logged
    best-effort; a notify failure never blocks the publishes."""
    res = compute.run_captured_manage_cycle()
    refresh_captured(bus)
    publish_captured_closed(bus)
    try:
        _notify_captured_closes(bus, (res or {}).get("closed"))
    except Exception:  # noqa: BLE001
        log.exception("captured close notify degraded (non-fatal)")


def remove_closed_from_captured(bus, signal_id) -> None:
    """Republish the captured view with one signal removed, PRESERVING live marks.

    Used after a manual close instead of ``refresh_captured``: it drops just the
    closed ``signal_id`` from the CURRENT cached view (which may carry the live
    marks merged by a prior ``captured_reprice`` / "Refresh marks (live)") and
    republishes. This keeps the remaining rows' live marks intact — closing one
    trade no longer reverts the whole table to the persisted (pre-refresh) view.
    Falls back to a full persisted ``refresh_captured`` when no cached view exists
    yet (cold start), so the table is still correct."""
    env = bus.cache_get(CACHE_CAPTURED)
    if env is None or not isinstance(env.payload, dict):
        refresh_captured(bus)
        return
    signals = env.payload.get("signals") or []
    kept = [s for s in signals if s.get("signal_id") != signal_id]
    _publish_captured(bus, kept)


def refresh_gamma(bus, symbol="$SPX") -> None:
    """Compute the Gamma snapshot for ``symbol``, cache it, publish an event.

    No strict contract: the snapshot is a loosely-shaped read-only dict (per-view
    data/summary/walls/flip/history + term grid) that only the Gamma page
    consumes, and ``compute.gamma_snapshot`` is defensive. When the chain fetch
    fails it returns None — we cache a graceful-empty view (``{"symbol", views:{}}``)
    so the page shows a "no data" state instead of staling on a prior symbol's
    snapshot."""
    snap = compute.gamma_snapshot(symbol)
    if snap is None:
        snap = {"symbol": symbol, "spot": None, "dte": None, "views": {}, "term": {}}
    version = bus.cache_set(CACHE_GAMMA, snap)
    bus.publish(EVENT_GAMMA, {"version": version})


def _current_gamma_symbol(bus) -> str:
    """The symbol currently in the cached Gamma snapshot ($SPX when nothing is
    cached / on any read failure). Shared by the same-tick collect→refresh pair
    so both agree on which symbol's chain to capture + refresh."""
    try:
        env = bus.cache_get(CACHE_GAMMA)
        return ((env.payload if env else None) or {}).get("symbol") or "$SPX"
    except Exception:  # noqa: BLE001
        return "$SPX"


def refresh_gamma_current(bus) -> None:
    """Refresh the Gamma snapshot for the symbol CURRENTLY in the cache, so the
    intraday heatmap + candles stay current server-side even when no Gamma page is
    open. Driven on the GEX-collection cadence (every 2 min in-window): without it
    the gamma cache only refreshed while a page was open (its 120 s timer) / at
    startup, so after a gap the heatmap showed a stale, cut-off session on next load.

    Reads the symbol from the cached snapshot so it NEVER forces a fixed $SPX (which
    would reintroduce the symbol-revert bug); falls back to $SPX only when nothing is
    cached. Defensive — ``refresh_gamma`` is itself guarded."""
    refresh_gamma(bus, _current_gamma_symbol(bus))


def collect_gex_history(bus=None) -> None:
    """Write one round of intraday GEX/Charm/DEX/Vanna (+term) snapshots.

    Tier-2 owner of intraday history collection — replaces the standalone
    ``gex_collector.py`` window so collection runs whenever this service is up
    (driven by ``scheduler.gex_due`` on a 5-min cadence within market hours).
    This is a pure write to the on-disk Tier-3 history store
    (``gex_history.db``); the Gamma page reads that history live on its own
    refresh, so there is no Redis cache view to publish here. ``bus`` is accepted
    only for handler-signature uniformity with the other scheduler-invoked
    refreshers. Guarded by the caller; ``compute.collect_gex_snapshots`` is
    itself defensive (per-symbol failures are logged, not raised).

    After collection, publishes the views built FROM the rows just written, so they
    ride the SAME 1-min tick: flow-skew (``cache:options:flow_skew``), the flow
    alerts, the Options Matrix (``cache:options:matrix``) and the Net Prem series
    (``cache:options:net_premium``). Each is its own best-effort block — a failure
    must never affect the collection that already succeeded, nor the sibling
    publishes (and ``bus`` is None for legacy callers, which skip all of them).

    The currently-viewed gamma symbol's chain is CAPTURED during the poll (see
    ``compute.collect_gex_snapshots(capture_symbols=…)``) so the same tick's
    ``refresh_gamma_current`` reuses it instead of refetching it seconds later."""
    capture = {_current_gamma_symbol(bus)} if bus is not None else None
    compute.collect_gex_snapshots(capture_symbols=capture)
    if bus is not None:
        try:
            publish_flow_skew(bus)
        except Exception:
            log.exception("publish_flow_skew after collect degraded")
        try:
            run_flow_alerts(bus)
        except Exception:
            log.exception("run_flow_alerts after collect degraded")
        try:
            publish_matrix(bus)
        except Exception:
            log.exception("publish_matrix after collect degraded")
        try:
            publish_net_premium(bus)
        except Exception:
            log.exception("publish_net_premium after collect degraded")


def _alert_now():
    """Now, in CT, for the flow-window gate. Its own hook so tests can pin it
    (the options_svc conftest pins it suite-wide, so no test depends on the
    wall clock)."""
    return _dt.datetime.now(mc.CT)


def _flow_window_open(now=None) -> bool:
    """Whether flow DETECTION (and the flow-skew publish) may run right now.

    The predicate is ``mc.in_collection_window(now)`` with ``eth_eligible``
    left FALSE — i.e. 08:00–15:20 CT on a trading day, the collection window as
    it stands *without* extended hours. That is exactly the set of moments
    ``run_flow_alerts`` can be reached in today (its only caller is
    ``collect_gex_history`` ← ``scheduler.gex_due`` ← ``in_collection_window(…,
    eth_eligible=True)``), so this gate is **provably inert before the
    activation date** and only ever closes over the newly-added GTH stretch
    (06:30–08:00 CT).

    **Why not the REGULAR session (08:30–15:00), and why not the push window?**
    Both would be a live regression. ``send_flow_alert`` has NO market-hours
    gate — the ``market_hours_only`` / ``_in_market_hours`` check lives in
    ``push_notify.notify_signals`` (scanner + captured signals) and
    ``sentiment_svc.state_alert``, neither of which is on the flow path. So
    flow alerts detect AND push across the whole 08:00–15:20 window today;
    narrowing to 08:30–15:00 would silently delete the first 30 and last 20
    minutes of a working feature.

    **Why gate DETECTION rather than emission.** The cooldown map doubles as a
    day-scoped seen-set and is mutated the moment a signal is *detected*
    (``flow_alerts.detect_flow_alerts`` writes ``cooldowns[key]`` in place; the
    UOA loop writes ``cooldowns[cid]`` before pushing). A signal detected at
    06:45 with only the push suppressed would be marked seen and would never
    fire again — not during GTH, and not at the open. Gating emission alone
    destroys the alert; gating detection defers it.

    ``[alerts].fire_in_extended_hours = true`` (``config/sessions.toml``,
    default false) opts back in for GTH and Curb."""
    now = now if now is not None else _alert_now()
    if mc.in_collection_window(now):
        return True
    return mc.alerts_fire_in_extended_hours() and mc.is_extended_hours(now)


def publish_flow_skew(bus) -> None:
    """Compute the per-index flow-skew view and publish it to the bus.

    No strict contract: the view is a small read-only dict keyed by index symbol
    (``{symbol: {rr_25d, rr_delta, call_vol, put_vol, ts}}``) that the sentiment
    service consumes defensively, and ``compute.flow_skew_view`` is already fully
    defensive (any failure degrades to ``{}``). Guarded so a bus hiccup never
    escapes into the caller.

    Skipped outside ``_flow_window_open`` (i.e. during GTH). The view is built
    from ``$SPX``/``SPY``/``QQQ``, but **only ``$SPX`` is ETH-eligible**, and
    ``gex_history_db.latest_skew_by_symbol`` has no date filter — so a GTH
    publish would emit a MIXED-FRESHNESS payload: a fresh ``rr_delta`` computed
    off thin 06:30 GTH prints for ``$SPX`` beside ``SPY``/``QQQ`` frozen at
    yesterday's 15:19 close, all presented as one current snapshot to the
    five-state classifier's aggression axis. Skipping simply leaves yesterday's
    value cached until the 08:00 tick republishes it fresh — exactly what
    happens overnight today."""
    if not _flow_window_open():
        return
    try:
        view = compute.flow_skew_view()
        bus.cache_set(CACHE_FLOW_SKEW, view, event=EVENT_FLOW_SKEW)
    except Exception:
        log.exception("publish_flow_skew degraded")


def _cache_payload(bus, key):
    """Unwrap ``bus.cache_get(key).payload`` → the raw payload (or None if absent)."""
    env = bus.cache_get(key)
    return env.payload if env is not None else None


def publish_matrix(bus) -> None:
    """Assemble the Options Matrix view and publish it to the bus.

    Rides the 1-min GEX tick right after the flow-skew + flow-alert publishes,
    reusing the rows ``collect_gex_snapshots`` just wrote to ``gex_history.db``.
    Feeds ``compute.build_matrix`` the scan_day day-union + the flow-alert cooldown
    SEEN-MAP (``cache:options:flow_alert_cooldowns`` — the uncapped daily record of
    distinct flow-alert events per symbol, so the Flow column reflects the true daily
    count rather than the 50-capped ``cache:options:flow_alerts`` rolling list).
    That builder is fully defensive (a DB-connect failure degrades to empty rows).
    ``skip_unchanged`` so an unchanged matrix doesn't wake GUI version-pollers.
    Guarded so a matrix failure never escapes into the caller (the 1-min GEX
    collection + the flow-alert publish must be unaffected)."""
    try:
        # scheduler imports handlers at its module top, so import it lazily here to
        # avoid a module-top import cycle.
        from services.options_svc import scheduler
        today = _today_ct()
        session_date = scheduler.active_session_date()
        now_ts = int(time.time())
        scan_day = _cache_payload(bus, CACHE_SCAN_DAY)
        flow_cooldowns = _cache_payload(bus, _FLOW_COOLDOWN_KEY)
        view = compute.build_matrix(
            scan_day, flow_cooldowns, today, session_date, now_ts)
        bus.cache_set(CACHE_MATRIX, view, event=EVENT_MATRIX, skip_unchanged=True)
    except Exception:
        log.exception("publish_matrix degraded")


def publish_net_premium(bus) -> None:
    """Assemble the Net Prem view and publish it to the bus.

    Rides the 1-min GEX tick right after ``publish_matrix``, over the rows
    ``collect_gex_snapshots`` just wrote. ``compute.build_net_premium`` is fully
    defensive about DATA (a DB failure degrades to an empty-series payload with
    ``error`` set). Guarded so a net-premium failure never escapes into the caller
    — the collection and the earlier publishes must be unaffected.

    **Its own DB read, deliberately.** ``build_matrix`` has just read the same six
    flow columns over an overlapping symbol set, so it is tempting to hand those
    rows over instead. Three reasons not to.

    1. **The universes are independently sourced and need not overlap.**
       ``net_premium.GROUPS`` is a static hardcoded tuple, while
       ``compute._matrix_symbols()`` derives from
       ``gex_collector.collection_symbols()`` (the index base ∪ the *gitignored*
       ``Top 20.xlsx``) and degrades to ``[]`` when that import fails. They happen
       to be equal locally (27 each), but on a fresh clone — or any watchlist
       edit — this view's symbols are a strict SUPERSET, so sharing would starve
       it of most of its lines year-round.
    2. **They are not the same rows.** Matrix reads ``session_date`` while net
       premium reads ``_display_session_date(now, …)``, and between the 08:00
       collection start and the 08:30 RTH open those DIFFER by a day — sharing
       would silently plot the wrong session in exactly the window this view is
       most fragile in.
    3. **Independence.** A matrix build failure returns early with ``error`` and
       no rows, which would then take this view down with it.

    The duplicated read is six narrow columns over an indexed range (no grid blob)
    for 27 symbols — cheap enough that correctness and isolation win.

    **No ``skip_unchanged``** (unlike ``publish_matrix``): it could not fire, for two
    independent reasons. The payload carries a fresh ``ts`` from every build, so the
    comparison never matches; and even ignoring ``ts``, the sole caller is
    ``collect_gex_history`` under ``scheduler.gex_due`` (08:00–15:20 CT), so this only
    ever runs inside the window where the series gains a row every minute. There is
    nothing to suppress — the flag would only add a full cache read plus a deep
    compare of a many-thousand-point series on every tick of a branch that has
    historically dropped slots under load.

    A payload that fails the ``NetPremiumSnapshot`` gate is logged as an ERROR
    naming the contract and NOT cached. That loudness is the point: this publish
    sits inside a try/except, so a quiet skip would reach the user as an empty
    chart — indistinguishable from "not collected yet", which is the honest state
    of several of these series for a while after ship."""
    try:
        # scheduler imports handlers at its module top, so import it lazily here to
        # avoid a module-top import cycle.
        from services.options_svc import scheduler
        # ONE clock read, threaded into both consumers (mirrors compute.gamma_snapshot):
        # active_session_date and build_net_premium's RTH crop straddle the 08:00 and
        # 08:30 boundaries, and reasoning about those is only sound if they agree on
        # the instant. active_session_date returns a datetime.date OBJECT — pass it
        # through untouched; build_net_premium raises TypeError on a string by design.
        now = scheduler._market_now()
        view = compute.build_net_premium(scheduler.active_session_date(now), now=now)
        # The constructor IS the gate: every field carries a default (so a missing
        # key validates) and _Base inherits pydantic's extra="ignore" (so an engine
        # extra is dropped rather than cached). A non-dict return raises TypeError at
        # the ** expansion and lands on the same handler. The write hangs off ``else``
        # so "publish only on a clean payload" is structural, not inferred.
        try:
            snap = NetPremiumSnapshot(**view)
        except Exception:
            log.exception("net-premium payload could not be validated against "
                          "NetPremiumSnapshot — NOT publishing (shape regression)")
        else:
            bus.cache_set(CACHE_NET_PREMIUM, snap.model_dump(), event=EVENT_NET_PREMIUM)
    except Exception:
        log.exception("publish_net_premium degraded")


def _flow_alert_symbols():
    """The collected universe to run flow alerts over. Defensive → [].

    ``gex_collector`` (options-scanner) is imported LAZILY — ``compute`` (imported
    at module top) has already put OPTIONS_SCANNER on ``sys.path``, and the lazy
    import avoids binding process-wide module names at handler-module import time
    (the documented cross-app collision discipline).

    This derives from the SAME ``collection_symbols()`` the Net Prem view needs,
    so the 2026-08-05 additions to ``gex_collector.SYMBOLS`` (the 11 SPDR sectors
    + IWM/DIA + the ten mega-caps) also widened the UOA + crossover push to
    Discord/Telegram. The user was asked and CHOSE to keep sector alerts — "a
    large XLE or XLF sweep is a real rotation signal".

    **If they ever prove too noisy, exclude them HERE — do NOT remove them from
    ``gex_collector.SYMBOLS``.** The Net Prem view reads those same collected
    rows, so dropping them from collection would silently empty its entire SPDR
    Sectors group: eleven blank lines, no error, no log. This function is the
    right cut point precisely because it is downstream of collection.
    """
    try:
        import gex_collector
        # $VIX-options premium crossovers are noise (mirrors the gamma symbol
        # dropdown, which also drops $VIX) — exclude it from the flow universe.
        return [s for s in gex_collector.collection_symbols() if s != "$VIX"]
    except Exception:
        log.exception("flow-alert symbol list degraded")
        return []


def _load_flow_series_for(conn, symbol, limit):
    """The trailing ``limit`` flow rows for one symbol over an already-open
    read-only connection (the caller owns it). Bounded — the detectors need only
    the last ~22 rows, not the whole day. Defensive → []."""
    try:
        import gex_history_db as gh
        return gh.load_flow_tail(conn, symbol, limit)
    except Exception:
        log.debug("load_flow_tail degraded for %s", symbol, exc_info=True)
        return []


def _load_spot_flip_for(conn, symbol):
    """The most-recent (ts, spot, flip) for a symbol's gex view over an already-open
    read-only connection. Defensive → None."""
    try:
        import gex_history_db as gh
        return gh.latest_spot_flip(conn, symbol)
    except Exception:
        log.debug("latest_spot_flip degraded for %s", symbol, exc_info=True)
        return None


def _run_gamma_flip(conn, cfg, bus, today, cooldowns, now_ts, universe):
    """Detect dealer gamma-regime flips (spot crossing the flip level) for the
    configured symbols and return a list of fresh alert dicts. Persists the
    per-symbol regime state; honors a per-symbol cooldown. Best-effort → []."""
    gf = cfg.get("gamma_flip", {})
    if not gf.get("enabled", True) or conn is None:
        return []
    gf_symbols = gf.get("symbols") or list(universe)
    band = gf.get("band_pct", 0.0015)
    cd_sec = gf.get("cooldown_min", 60) * 60

    env = bus.cache_get(_GAMMA_REGIME_KEY)
    payload = env.payload if (env and isinstance(env.payload, dict)) else {}
    state = payload.get("map", {}) if payload.get("date") == today else {}

    out = []
    for sym in gf_symbols:
        row = _load_spot_flip_for(conn, sym)
        if not row:
            continue
        ts, spot, flip = row
        prev = state.get(sym)
        alert, new_regime = flow_alerts.detect_gamma_flip(sym, spot, flip, prev, band, ts)
        if alert:
            key = f"{sym}|gamma_flip"
            last = cooldowns.get(key)
            on_cd = isinstance(last, (int, float)) and (now_ts - last) < cd_sec
            if on_cd:
                continue        # suppress; keep prior state so it can fire post-cooldown
            cooldowns[key] = now_ts
            state[sym] = new_regime
            alert["id"] = f"{sym}|gamma_flip|{alert['side']}|{int(ts)}"
            alert["text"] = flow_alerts.alert_text(alert)
            out.append(alert)
        else:
            state[sym] = new_regime

    bus.cache_set(_GAMMA_REGIME_KEY, {"date": today, "map": state}, skip_unchanged=True)
    return out


def _flow_now_ts():
    import time
    return int(time.time())


def run_flow_alerts(bus) -> None:
    """Detect + push + publish options-flow alerts (crossover + unusual activity) for
    the whole collected universe. Rides the 1-min GEX poll; best-effort, never raises.

    The cooldown map is persisted (day-scoped) in its own key so a fired signal
    doesn't re-push every minute; fresh alerts are pushed to the phone (best-effort)
    and appended to a day-scoped rolling list the webgui reads.

    Gated on ``_flow_window_open`` — DETECTION, not just the push. That gate sits
    ahead of the cooldown read for a reason: the cooldown map is the day-scoped
    seen-set and is mutated at detection time, so suppressing only the push would
    burn the signal instead of deferring it (see ``_flow_window_open``)."""
    try:
        cfg = flow_alerts.load_thresholds()
        if not cfg.get("enabled", True):
            return
        # Before ANY detection or cooldown mutation — see the docstring.
        if not _flow_window_open():
            return
        today = _today_ct()
        cd_env = bus.cache_get(_FLOW_COOLDOWN_KEY)
        cd_payload = cd_env.payload if (cd_env and isinstance(cd_env.payload, dict)) else {}
        cooldowns = cd_payload.get("map", {}) if cd_payload.get("date") == today else {}
        now_ts = _flow_now_ts()
        push_cfg = push_notify.load_config()
        # The crossover detector reads only the trailing rows (last-vs-prior premium);
        # UOA now rides the poll's on_chain stash, not the series. Load a small bounded
        # tail (a couple extra rows so a forward-only NULL-premium row can't hide the
        # crossover), never the whole day's ~440 rows/symbol every minute.
        tail_limit = _FLOW_CROSSOVER_TAIL

        # Capture the flow universe ONCE (excludes $VIX) and reuse it for both the
        # crossover loop and the UOA drain so both share the exact same universe.
        symbols = _flow_alert_symbols()
        allowed = set(symbols)

        conn = None
        try:
            import gex_history_db as gh
            conn = gh.connect(read_only=True)
        except Exception:
            log.debug("flow-alert history connect degraded", exc_info=True)
        try:
            fresh = []
            for sym in symbols:
                series = _load_flow_series_for(conn, sym, tail_limit) if conn is not None else []
                for a in flow_alerts.detect_flow_alerts(sym, series, cfg, cooldowns, now_ts):
                    fresh.append(a)
            # Dealer gamma-regime flips (spot crossing the flip level) — reuses the
            # same open read-only connection + cooldown map.
            fresh.extend(_run_gamma_flip(conn, cfg, bus, today, cooldowns, now_ts, allowed))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    log.debug("flow-alert history conn close failed", exc_info=True)

        # Contract-level UOA (from the poll's on_chain stash — no re-fetch). Once per
        # contract per day: the cooldown map doubles as a date-scoped seen-set (vol/OI
        # is monotonic, so a contract crosses K once and stays — alert it a single time).
        for sym, contracts in compute.take_uoa_stash().items():
            if sym not in allowed:      # same universe as crossover (excludes $VIX)
                continue
            for c in contracts:
                cid = f"{sym}|uoa|{c['side']}|{c['strike']:g}|{c['expiry']}"
                if cid in cooldowns:
                    continue
                cooldowns[cid] = now_ts
                a = dict(c)
                a["id"] = cid
                # detect_uoa emits no ts (crossover/gamma_flip do) — stamp the
                # detecting tick so every alert can be placed on a timeline.
                a["ts"] = now_ts
                a["text"] = flow_alerts.alert_text(a)
                fresh.append(a)

        # Contract-level big_delta (relative delta-notional) — same stash pattern,
        # same shared cooldown seen-set (once per contract per day) as UOA above.
        for sym, contracts in compute.take_big_delta_stash().items():
            if sym not in allowed:      # same universe as crossover/UOA (excludes $VIX)
                continue
            for c in contracts:
                cid = f"{sym}|big_delta|{c['side']}|{c['strike']:g}|{c['expiry']}"
                if cid in cooldowns:
                    continue
                cooldowns[cid] = now_ts
                a = dict(c)
                a["id"] = cid
                a["ts"] = now_ts
                a["text"] = flow_alerts.alert_text(a)
                fresh.append(a)

        if fresh:
            # big_delta pushes are gated SEPARATELY from firing: every fire still
            # lands on the screen (cached below), but only a fire clearing
            # [big_delta].push_threshold (with push=true) earns a phone push —
            # flow_alerts.big_delta_should_push. Every other alert type is unaffected.
            for a in fresh:
                if a.get("type") == "big_delta" and not flow_alerts.big_delta_should_push(a, cfg):
                    continue
                try:
                    push_notify.send_flow_alert(a, config=push_cfg)
                except Exception:
                    log.exception("send_flow_alert degraded")
            env = bus.cache_get(CACHE_FLOW_ALERTS)
            prior = (env.payload.get("alerts", []) if (env and isinstance(env.payload, dict)
                     and env.payload.get("date") == today) else [])
            seen_ids = {a.get("id") for a in prior}
            deduped = [a for a in fresh if a.get("id") not in seen_ids]
            if deduped:
                alerts = (prior + deduped)[-_FLOW_ALERTS_MAX:]
                bus.cache_set(CACHE_FLOW_ALERTS, {"date": today, "alerts": alerts},
                              event=EVENT_FLOW_ALERTS)
        # skip_unchanged: a quiet tick leaves the cooldown map identical, so don't
        # bump the key's version (and wake version-pollers) every minute.
        bus.cache_set(_FLOW_COOLDOWN_KEY, {"date": today, "map": cooldowns},
                      skip_unchanged=True)
    except Exception:
        log.exception("run_flow_alerts degraded")


def publish_gex_status(bus) -> None:
    """Compute the GEX-collector status view and publish it to the bus.

    No strict contract: the view is a small read-only dict (collector status
    label/color + last/next scan times) that only the Gamma page's status bar
    consumes, and ``compute.gex_status_view`` is already fully defensive (any
    failure degrades to a safe default dict). Called once at startup and on each
    scheduler tick so the page's status bar tracks collector health live."""
    data = compute.gex_status_view()
    # Per-tick republisher: skip the version bump + publish when the status view
    # is unchanged (e.g. off-hours), so it doesn't wake the GUI poller needlessly.
    bus.cache_set(CACHE_GEX_STATUS, data, event=EVENT_GEX_STATUS, skip_unchanged=True)


def _persist_briefing(res, slot, now) -> None:
    """Append a successful briefing to the on-disk history (gamma_briefing_history_db).

    Stores the STRUCTURED analysis payload (source of truth) per (date, slot) so a
    report can be regenerated on demand — see the gamma_briefing_report.py utility.
    Best-effort: only runs with a real ``analysis`` are recorded (degraded no-chains/
    no-key/error pages have none); any failure is logged, never raised — persistence
    must not break the generation/cache path."""
    analysis = (res or {}).get("analysis")
    if not analysis:
        return
    try:
        import gamma_briefing_history_db as gbh
        conn = gbh.connect()
        try:
            gbh.insert_briefing(
                conn, date=now.date().isoformat(), slot=slot,
                generated_at=now.isoformat(), symbol_scope="$SPX/SPY/QQQ",
                model=getattr(compute, "_ANALYZE_MODEL", None),
                bias=analysis.get("bias"), headline=analysis.get("headline"),
                analysis=analysis)
        finally:
            conn.close()
    except Exception:
        log.exception("gamma briefing history persist degraded")


def publish_gamma_briefing_index(bus) -> None:
    """Publish the stored-briefings index (metadata only) for the in-app history
    picker (``cache:options:gamma_briefings``). Called at startup and after each
    persist. Defensive — a cold/absent DB just yields an empty list."""
    briefings = []
    try:
        import gamma_briefing_history_db as gbh
        conn = gbh.connect()
        try:
            briefings = gbh.list_briefings(conn)  # metadata, newest date first
        finally:
            conn.close()
    except Exception:
        log.exception("gamma briefing index degraded")
    bus.cache_set(CACHE_GAMMA_BRIEFINGS, {"briefings": briefings},
                  event=EVENT_GAMMA_BRIEFINGS, skip_unchanged=True)


def run_gamma_history(bus, date, slot=None) -> None:
    """Regenerate a history report (HTML) from the stored briefings for ``date`` — a
    single slot if given, else all of that day's slots — and cache it for the page to
    open in a new tab (``cache:options:gamma_history``). The HTML is rebuilt from the
    stored structured analysis (compute.analyze_history_doc); a no-match yields a
    readable 'no briefings' page. Defensive throughout."""
    rows, title = [], f"Gamma Briefings — {date}"
    try:
        import gamma_briefing_history_db as gbh
        conn = gbh.connect()
        try:
            if slot:
                r = gbh.get_briefing(conn, date, slot)
                rows = [r] if r else []
                title = f"Gamma Briefing — {date} · {slot}"
            else:
                rows = gbh.briefings_for_date(conn, date)
        finally:
            conn.close()
    except Exception:
        log.exception("gamma history load degraded")
    html = compute.analyze_history_doc(rows, title=title)
    version = bus.cache_set(CACHE_GAMMA_HISTORY,
                            {"html": html, "date": date, "slot": slot})
    bus.publish(EVENT_GAMMA_HISTORY, {"version": version})


def run_scheduled_gamma_analyze(bus, slot) -> None:
    """Auto-run the $SPX/SPY/QQQ Gamma Analyze for a scheduled ``slot`` and cache it
    under that slot's OWN key (NOT the ad-hoc ``gamma_analyze`` key — so an open
    Gamma page's _watch_analyze doesn't auto-open a browser tab for it).

    Driven by ``scheduler.analyze_slot_due`` at premarket / ~18 min after the open /
    midday / close on each trading day, in addition to the ad-hoc Analyze button.
    The ``close`` slot (15:15 CT, after the cash close) is the EOD RETROSPECTIVE —
    ``compute.eod_briefing``, not the intraday playbook. It returns the same
    ``{"html", "analysis"}`` shape, so caching / history / the PNG push below are
    identical for all four slots.
    The result persists in Redis under its slot key until the next day's run
    overwrites it; the Gamma page opens it on demand via ``/options/analyze?slot=``.
    Defensive: ``compute.gamma_analyze`` never raises (every failure → a readable
    HTML page), so this only guards the slot-key lookup. After caching + persisting,
    the briefing is pushed to the phone (Telegram + Discord) as a self-contained HTML
    attachment — last and guarded, so a channel outage can't cost us the briefing."""
    key = CACHE_GAMMA_ANALYZE_SCHED.get(slot)
    if not key:
        return
    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    now = _dt.datetime.now(_ZI("America/Chicago"))
    title = ANALYZE_SLOT_TITLES.get(slot, slot)
    hr = (now.strftime("%I").lstrip("0") or "12")  # portable 12-hour (no %-I on Windows)
    label = f"Auto · {title} · {now.strftime('%b %d')} {hr}:{now.strftime('%M %p')} CT"
    res = (compute.eod_briefing(label=label) if slot == "close"
           else compute.gamma_analyze(label=label))
    res = {**res, "slot": slot, "generated_at": now.isoformat()}
    version = bus.cache_set(CACHE_GAMMA_ANALYZE_SCHED[slot], res)
    bus.publish(EVENT_GAMMA_ANALYZE_SCHED[slot], {"version": version})
    _persist_briefing(res, slot, now)      # record to history (best-effort)
    publish_gamma_briefing_index(bus)      # refresh the in-app history picker
    # Phone push: render the briefing to a PNG and send it inline (Telegram +
    # Discord). An image, not the HTML doc — Discord auto-previews an .html
    # attachment as raw syntax-highlighted source, which no flag suppresses.
    # LAST and guarded — publish/persist first, notify second (the house pattern
    # from rescan/refresh_captured), so a channel outage can never cost us the
    # cached briefing or its history row.
    try:
        push_notify.send_gamma_briefing(res, slot=slot)
    except Exception:
        log.exception("gamma briefing push degraded")


def run_action_alert(bus, slot=None) -> None:
    """Collect trades needing action + push a digest to Telegram/Discord (+ SMS).

    Driven by ``scheduler.action_alert_due`` at 10:00 / 13:00 / 15:00 CT on each
    trading day. Caches the digest under ``cache:options:action_alert`` for
    inspection (the run's items + whether it pushed). Fully defensive — a collect
    or push hiccup never raises into the scheduler; an empty digest pushes nothing
    (see ``push_notify.send_action_digest``)."""
    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI

    slot_label = push_notify.action_slot_label(slot)
    try:
        items = compute.collect_action_items()
    except Exception:
        log.exception("collect_action_items degraded → empty digest")
        items = {}
    sent = False
    try:
        sent = push_notify.send_action_digest(items, slot_label=slot_label)
    except Exception:
        log.exception("send_action_digest degraded")
    payload = {"slot": slot, "slot_label": slot_label, "items": items,
               "total": push_notify.action_total(items), "sent": bool(sent),
               "generated_at": _dt.datetime.now(_ZI("America/Chicago")).isoformat()}
    try:
        version = bus.cache_set(CACHE_ACTION_ALERT, payload)
        bus.publish(EVENT_ACTION_ALERT, {"version": version})
    except Exception:
        log.exception("action_alert cache_set degraded")


def run_market_snapshot(bus, slot):
    """Build + push the 30-min Market Snapshot PNG (Telegram + Discord).

    Reads six caches (market dashboard + sentiment composite/regime + the two
    intraday histories), pushes via ``push_notify.send_market_snapshot``, and caches
    the inputs at ``cache:options:market_snapshot`` for inspection. Best-effort —
    ANY failure logs and returns, never raises into the scheduler."""
    def _snap_payload(env):
        # Bus.cache_get returns a CacheEnvelope (or None), never the bare payload.
        return env.payload if env is not None else None

    try:
        dashboard = _snap_payload(bus.cache_get("cache:market:dashboard")) or {}
        comp = _snap_payload(bus.cache_get("cache:sentiment:composite")) or {}
        trend = (comp.get("derived") or {}).get("trend") or {}
        sentiment = (comp.get("live") or {}).get("composite") or {}
        regime = _snap_payload(bus.cache_get("cache:sentiment:regime")) or {}
        intraday = _snap_payload(bus.cache_get("cache:sentiment:intraday_history")) or {}
        regime_hist = _snap_payload(bus.cache_get("cache:sentiment:regime_history")) or {}
        # The sentiment ring's WEEK (last 5 scored sessions) and MONTH (full
        # history) horizons come from the session snapshots, not the composite —
        # this read exists solely for them.
        hist = _snap_payload(bus.cache_get("cache:sentiment:history")) or {}
    except Exception:  # noqa: BLE001 — a down bus must not break the tick.
        log.exception("market snapshot %s: cache read failed", slot)
        return
    try:
        push_notify.send_market_snapshot(dashboard, trend, sentiment, regime,
                                         intraday, regime_hist, slot=slot,
                                         derived=(comp.get("derived") or {}),
                                         snaps=(hist.get("snaps") or []),
                                         composite_at=comp.get("composite_at"))
    except Exception:  # noqa: BLE001 — the push primitives shouldn't raise, belt+braces.
        log.exception("market snapshot %s: push failed", slot)
    try:
        bus.cache_set(CACHE_MARKET_SNAPSHOT,
                      {"slot": slot, "trend": trend, "sentiment": sentiment,
                       "regime": regime})
    except Exception:  # noqa: BLE001
        log.exception("market snapshot %s: cache_set failed", slot)


def run_eod_summary(bus, slot=None) -> None:
    """Assemble the per-book end-of-day P&L summary + push it (Telegram/Discord/SMS).

    Driven by ``scheduler.eod_summary_due`` at ~15:10 CT on each trading day. Caches the
    summary under ``cache:options:eod_summary`` for inspection (the day's per-book stats +
    whether it pushed). Fully defensive — a collect or push hiccup never raises into the
    scheduler; the push sends nothing when no paper book is seeded (see
    ``push_notify.send_eod_summary``)."""
    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI

    try:
        summary = compute.collect_eod_summary()
    except Exception:
        log.exception("collect_eod_summary degraded → empty summary")
        summary = {}
    sent = False
    try:
        sent = push_notify.send_eod_summary(summary)
    except Exception:
        log.exception("send_eod_summary degraded")
    payload = {"slot": slot, "summary": summary,
               "books": push_notify.eod_book_count(summary), "sent": bool(sent),
               "generated_at": _dt.datetime.now(_ZI("America/Chicago")).isoformat()}
    try:
        version = bus.cache_set(CACHE_EOD_SUMMARY, payload)
        bus.publish(EVENT_EOD_SUMMARY, {"version": version})
    except Exception:
        log.exception("eod_summary cache_set degraded")


def publish_gamma_symbols(bus) -> None:
    """Compute the Gamma dropdown universe (collected symbols minus $VIX) and
    publish it to the bus so the Tier-3 Gamma page can populate its symbol
    dropdown without importing any engine. No strict contract: a small read-only
    ``{"symbols":[...]}`` dict; ``compute.gamma_symbol_options`` is defensive."""
    data = {"symbols": compute.gamma_symbol_options()}
    version = bus.cache_set(CACHE_GAMMA_SYMBOLS, data)
    bus.publish(EVENT_GAMMA_SYMBOLS, {"version": version})


def run_rescue(bus, position_id, source: str = "paper") -> None:
    """Compute the ranked rescue advisory for ONE position and cache it.

    On-demand only (the GUI's per-position Rescue button enqueues a ``rescue``
    command). ``source`` selects the data path: ``"paper"`` (default, int
    position_id) loads a real paper position; ``"captured"`` (string signal_id)
    loads a captured signal and returns advisory-only candidates.
    ``compute.compute_rescue`` is fully defensive — it returns a
    ``RescueAdvisory``-shaped dict, or ``{"error": ...}`` on any failure, never
    raising — so the GUI always sees a payload (an advisory or an error note)
    rather than hanging on a missing cache view. Cached per-id under
    ``cache:options:rescue:<position_id>`` (the string signal_id works as a key)."""
    adv = compute.compute_rescue(position_id, source)
    key = f"{CACHE_RESCUE}:{position_id}"
    version = bus.cache_set(key, adv)
    bus.publish(EVENT_RESCUE, {"version": version, "position_id": position_id})


def run_rescue_adhoc(bus, spec) -> None:
    """Compute the ranked rescue advisory for a user-defined ad-hoc trade + cache it.

    On-demand only (the GUI's "Rescue an ad-hoc trade" card enqueues a
    ``rescue_adhoc`` command with the form ``spec``). ``compute.compute_rescue_adhoc``
    is fully defensive — it returns a ``RescueAdvisory``-shaped dict (advisory-only)
    or ``{"error": ...}`` on any failure, never raising. Cached under the fixed
    ``cache:options:rescue:adhoc`` key (advisory-only, so no Apply flow)."""
    adv = compute.compute_rescue_adhoc(spec)
    key = f"{CACHE_RESCUE}:adhoc"
    version = bus.cache_set(key, adv)
    bus.publish(EVENT_RESCUE, {"version": version, "position_id": "adhoc"})


def run_rescue_apply(bus, position_id, candidate) -> None:
    """Execute an approved rescue candidate against a paper position.

    Flow:
      1. Load the position via the SAME loader ``compute_rescue`` uses
         (``compute._load_position`` → ``paper_account_db``). If it isn't a real
         OPEN paper position (None / not found — covers captured-signal ids and
         bogus ids, which never live in ``paper_account_db``) we cache an advisory
         carrying an error note and return WITHOUT mutating anything.
      2. Reprice-and-dispatch via ``paper_adjust.apply_adjustment`` with a live
         leg pricer (``compute._make_leg_pricer``) — its built-in stale-price
         guard aborts (``stale``) without mutating when prices have drifted.
      3. On a stale/failed apply, re-cache the (unchanged) advisory with the apply
         result attached so the GUI sees *why* it didn't apply. On success, refresh
         the paper view, then re-cache the advisory (now reflecting the adjusted /
         closed position) with the apply result attached.

    Fully defensive: any unexpected failure caches a minimal error advisory so the
    GUI poller isn't left hanging, never raising into the command consumer."""
    key = f"{CACHE_RESCUE}:{position_id}"
    try:
        pos = compute._load_position(position_id)
        if not pos:
            # Not a paper position (captured-signal / bogus id) — refuse cleanly.
            adv = {
                "position_id": position_id,
                "error": "not a paper position (cannot apply rescue)",
                "apply_result": {
                    "ok": False,
                    "error": "position not found — only open paper positions can be rescued",
                    "position_id": position_id,
                },
            }
            version = bus.cache_set(key, adv)
            bus.publish(EVENT_RESCUE, {"version": version, "position_id": position_id})
            return

        symbol = pos.get("symbol")
        price_leg = compute._make_leg_pricer(symbol)
        result = _paper_adjust().apply_adjustment(
            None, pos, candidate, price_leg=price_leg)

        ok = bool(result.get("ok"))
        if ok:
            # The position changed — refresh the paper view so the page + nav badge
            # reflect the adjustment/close, then recompute the advisory.
            refresh_paper_account(bus)

        # Recompute the advisory against the (now current) position. If the apply
        # closed/rolled it, compute_rescue returns position-not-found — fall back
        # to a minimal advisory so the GUI still gets the apply result.
        adv = compute.compute_rescue(position_id)
        if not isinstance(adv, dict) or adv.get("error"):
            adv = {"position_id": position_id, "error": (adv or {}).get("error")
                   if isinstance(adv, dict) else "advisory unavailable"}
        adv["apply_result"] = result

        version = bus.cache_set(key, adv)
        bus.publish(EVENT_RESCUE, {"version": version, "position_id": position_id})
    except Exception as e:
        # Never let a bad apply kill the consumer; surface the error to the GUI.
        adv = {
            "position_id": position_id,
            "error": f"{type(e).__name__}: {e}",
            "apply_result": {"ok": False, "error": str(e), "position_id": position_id},
        }
        version = bus.cache_set(key, adv)
        bus.publish(EVENT_RESCUE, {"version": version, "position_id": position_id})


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:options`` command. ``rescan`` → full rescan;
    ``swing_scan`` → on-demand parameterized swing scan; ``refresh_paper`` →
    re-read the paper account; ``paper_entry``/``paper_manage`` → run the cycle
    (guarded on an existing account) then refresh; ``paper_reset`` → reset the
    account then refresh; ``paper_create`` (args signal, qty) → create + persist a
    paper trade from a signal then refresh the ledger; ``paper_reload`` → re-read
    the trade ledger;
    ``paper_close``/``paper_delete``/``paper_delete_closed`` → run the lifecycle
    action then refresh the ledger; ``driver_paper_create`` (args signal, qty) →
    open ONE guardrail-approved signal into the ISOLATED driver account then
    republish the driver views; ``driver_paper_manage`` → reprice/auto-close the
    driver account then republish; ``driver_paper_reset`` → (re)seed the driver
    account then republish; ``paper_analyze`` → analyze the selected
    trade, cache the result + publish; ``captured_reload`` → re-read open signals;
    ``captured_reprice`` → reprice all open signals, cache the repriced list +
    flags (two views) + publish both; ``captured_close`` → manually close a signal
    then refresh; ``captured_manage`` → run the captured auto-manage cycle (reprice
    → arm break-even → auto-close) then republish the open + closed views;
    ``set_autoclose`` (args enabled) → write the auto-close master toggle;
    ``set_manual_paper_lifecycle`` (args enabled) → write the MANUAL paper
    account's opt-in break-even-lifecycle toggle (default OFF; the DRIVER
    account never reads it);
    ``gamma_refresh`` (args symbol, default ``$SPX``) → recompute the
    Gamma snapshot; ``gamma_explain`` (args symbol) → build the Explain body, cache
    + publish; ``gamma_analyze`` → build the bundled SPX/SPY/QQQ prompt, cache +
    publish; ``sim_fetch`` (args symbol) → fetch the simulator ChainSnapshot
    (stashed in-process), cache the selector meta + publish; ``sim_run`` (args
    symbol/expiry/kind/strike/direction/dt/mult) → compute both sweeps, cache the
    result + publish; ``calc_load`` (args symbol) → fetch the quote + option chain,
    cache the loader payload (chain dict + price + range) + publish; ``calc_compute``
    (args = the calc params dict) → run the summary + P&L grid math, cache the
    result + publish; ``calc_iv`` (args spot/strike/option_type/mark/expiry/rate) →
    imply IV from the option mark at the intraday time-to-expiry, cache + publish;
    ``expected_move`` (args symbol/expiry/legs) → build the
    expected-move cone payload, cache the result + publish; ``em_chain`` (args
    symbol) → expirations + strike ladders for the Expected Move dropdowns;
    ``rescue`` (args
    position_id) → compute the ranked rescue advisory for one paper position, cache
    per-id + publish; ``rescue_apply`` (args position_id, candidate) → execute the
    approved candidate against the paper position (stale-price guarded), refresh the
    paper view + re-cache the advisory; else no-op."""
    if command.type == "rescan":
        rescan(bus)
    elif command.type == "swing_scan":
        swing_scan(bus, command.args)
    elif command.type == "refresh_paper":
        refresh_paper_account(bus)
    elif command.type == "paper_entry":
        # No account -> don't run the cycle; refresh so the page shows the
        # no-account state.
        if compute.has_paper_account():
            compute.run_entry_cycle()
        refresh_paper_account(bus)
    elif command.type == "paper_manage":
        run_manage_and_refresh(bus)
    elif command.type == "paper_reset":
        compute.reset_paper_account(float(command.args.get("starting_balance", 25000.0)))
        refresh_paper_account(bus)
    elif command.type == "driver_paper_create":
        # Open ONE guardrail-approved signal into the ISOLATED driver account
        # (a separate DB from the manual paper account), then republish the driver
        # views. ``open_driver_position`` lazily seeds the account + is defensive.
        signal = command.args.get("signal") or {}
        sym = signal.get("symbol") or signal.get("id")
        # R5: refuse a trade-opening command consumed long after enqueue (stale
        # economics on a service-restart replay). Surfaced via the R1 results list.
        if _is_stale_open(command):
            age = _command_age_seconds(command)
            log.warning(
                "REJECTED stale driver_paper_create for %s: age %.0fs > %ds "
                "(enqueue ts=%s) — not opening on stale economics",
                sym, age or -1, STALE_OPEN_MAX_AGE_SEC, getattr(command, "ts", None))
            _record_open_result({"status": "rejected", "reason": "stale_command",
                                 "symbol": sym, "age_sec": round(age or 0, 1),
                                 "source": "driver"})
            refresh_driver_paper(bus)
        else:
            # R1: capture the outcome — ``open_driver_position`` NEVER raises and
            # returns {"status": "opened"|"rejected"|"error", ...}. Log + surface
            # any non-opened outcome so a silent shape-drift / broker / low-credit
            # reject can't masquerade as an executed trade in the decision log.
            result = compute.open_driver_position(
                signal, int(command.args.get("qty", 1)),
                context=command.args.get("context")) or {}
            result.setdefault("symbol", sym)
            result.setdefault("source", "driver")
            status = result.get("status")
            if status != "opened":
                log.warning(
                    "driver open did NOT land for %s: status=%s reason=%s error=%s",
                    sym, status, result.get("reason"), result.get("error"))
            else:
                log.info("driver opened %s qty=%s credit=%s",
                         sym, result.get("qty"), result.get("entry_credit"))
            _record_open_result(result)
            refresh_driver_paper(bus)
    elif command.type == "driver_paper_manage":
        run_driver_manage_and_refresh(bus)
    elif command.type == "driver_paper_reset":
        compute.ensure_driver_account(
            float(command.args.get("starting_balance", 25000.0)))
        refresh_driver_paper(bus)
    elif command.type == "paper_create":
        # R5: refuse a stale manual paper-open (a restart replay would open on
        # stale economics). Surfaced via the R1 results list + logged; the ledger
        # is still refreshed so the page repaints.
        if _is_stale_open(command):
            age = _command_age_seconds(command)
            sig = command.args.get("signal") or {}
            log.warning(
                "REJECTED stale paper_create for %s: age %.0fs > %ds (enqueue ts=%s)",
                sig.get("symbol"), age or -1, STALE_OPEN_MAX_AGE_SEC,
                getattr(command, "ts", None))
            _record_open_result({"status": "rejected", "reason": "stale_command",
                                 "symbol": sig.get("symbol"),
                                 "age_sec": round(age or 0, 1), "source": "manual"})
        else:
            compute.create_paper_trade(command.args.get("signal"),
                                       command.args.get("qty", 1))
        refresh_paper_trades(bus)
    elif command.type == "paper_reload":
        refresh_paper_trades(bus)
    elif command.type == "paper_close":
        compute.close_paper(command.args.get("trade_id"),
                            command.args.get("debit", 0.0))
        refresh_paper_trades(bus)
    elif command.type == "paper_delete":
        compute.delete_paper(command.args.get("trade_id"))
        refresh_paper_trades(bus)
    elif command.type == "paper_delete_closed":
        compute.delete_closed_paper()
        refresh_paper_trades(bus)
    elif command.type == "paper_analyze":
        res = compute.analyze_paper(command.args.get("trade_id"))
        version = bus.cache_set(CACHE_PAPER_ANALYZE, res)
        bus.publish(EVENT_PAPER_ANALYZE, {"version": version})
    elif command.type == "captured_reload":
        refresh_captured(bus)
    elif command.type == "captured_reprice":
        res = compute.reprice_captured()
        # Cache the repriced signal list (so the table shows fresh marks) +
        # the flags list (so the page can notify) under separate views.
        _publish_captured(bus, res["signals"])
        fver = bus.cache_set(CACHE_CAPTURED_FLAGS, {"flags": res["flags"]})
        bus.publish(EVENT_CAPTURED_FLAGS, {"version": fver})
        _notify_captured(bus, res.get("signals"))
    elif command.type == "captured_close":
        sid = command.args.get("signal_id")
        compute.close_captured(sid,
                               command.args.get("exit_val", 0.0),
                               command.args.get("reason", "MANUAL_CLOSE"))
        # Drop ONLY the closed signal from the cached view, preserving the live
        # marks on the remaining rows (don't revert to the persisted view).
        remove_closed_from_captured(bus, sid)
    elif command.type == "captured_manage":
        # Manual 'run now' of the captured auto-manage cycle (reprice → arm → close);
        # runs regardless of the auto-close toggle (the toggle only gates the
        # scheduled tick — an explicit click should always work).
        run_captured_manage_and_publish(bus)
    elif command.type == "set_autoclose":
        # Settings toggle write-through: gate the SCHEDULED captured-manage cycle.
        enabled = bool((command.args or {}).get("enabled", True))
        bus.cache_set(CACHE_AUTOCLOSE_ENABLED, {"enabled": enabled})
    elif command.type == "set_manual_paper_lifecycle":
        # Settings toggle write-through: gate the MANUAL paper account's opt-in
        # break-even lifecycle. Defaults False — only an explicit enable turns it
        # on; the DRIVER account is never affected.
        enabled = bool((command.args or {}).get("enabled", False))
        bus.cache_set(CACHE_MANUAL_PAPER_LIFECYCLE, {"enabled": enabled})
    elif command.type == "gamma_refresh":
        refresh_gamma(bus, command.args.get("symbol", "$SPX"))
    elif command.type == "gamma_explain":
        res = compute.gamma_explain(command.args.get("symbol", "$SPX"))
        version = bus.cache_set(CACHE_GAMMA_EXPLAIN, res)
        bus.publish(EVENT_GAMMA_EXPLAIN, {"version": version})
    elif command.type == "gamma_analyze":
        res = compute.gamma_analyze()
        version = bus.cache_set(CACHE_GAMMA_ANALYZE, res)
        bus.publish(EVENT_GAMMA_ANALYZE, {"version": version})
        # Record ad-hoc runs to history too, under a time-stamped slot so repeated
        # clicks in a day are each kept (distinct from the scheduled slots).
        import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        _now = _dt.datetime.now(_ZI("America/Chicago"))
        _persist_briefing(res, f"adhoc-{_now.strftime('%H%M')}", _now)
        publish_gamma_briefing_index(bus)
    elif command.type == "gamma_history":
        run_gamma_history(bus, command.args.get("date"), command.args.get("slot"))
    elif command.type == "sim_fetch":
        meta = compute.sim_fetch(command.args.get("symbol", "SPY"))
        version = bus.cache_set(CACHE_SIM_META, meta)
        bus.publish(EVENT_SIM_META, {"version": version})
    elif command.type == "sim_run":
        a = command.args or {}
        result = compute.sim_run(
            a.get("symbol"), a.get("expiry"), a.get("kind"), a.get("strike"),
            a.get("direction"), a.get("dt", 5.0), a.get("mult", 1.5),
            legs=a.get("legs"))
        version = bus.cache_set(CACHE_SIM_RESULT, result)
        bus.publish(EVENT_SIM_RESULT, {"version": version})
    elif command.type == "sim_replay":
        a = command.args or {}
        res = compute.sim_replay(
            a.get("symbol"), a.get("expiry"), a.get("kind"),
            a.get("strike"), a.get("direction"), a.get("lookback", "auto"),
            legs=a.get("legs"))
        version = bus.cache_set(CACHE_SIM_REPLAY, res)
        bus.publish(EVENT_SIM_REPLAY, {"version": version})
    elif command.type == "calc_load":
        cc = compute.calc_load_symbol(command.args.get("symbol", "SPY"))
        version = bus.cache_set(CACHE_CALC_CHAIN, cc)
        bus.publish(EVENT_CALC_CHAIN, {"version": version})
    elif command.type == "calc_compute":
        result = compute.calc_compute(**(command.args or {}))
        version = bus.cache_set(CACHE_CALC_RESULT, result)
        bus.publish(EVENT_CALC_RESULT, {"version": version})
    elif command.type == "calc_iv":
        a = command.args or {}
        res = compute.calc_iv(a.get("spot"), a.get("strike"),
                              a.get("option_type"), a.get("mark"),
                              a.get("expiry"), a.get("rate", 0.045))
        version = bus.cache_set(CACHE_CALC_IV, res)
        bus.publish(EVENT_CALC_IV, {"version": version})
    elif command.type == "expected_move":
        a = command.args or {}
        res = compute.compute_expected_move(
            a.get("symbol"), a.get("expiry"), a.get("legs") or [],
            a.get("lookback", "auto"))
        version = bus.cache_set(CACHE_EXPECTED_MOVE, res)
        bus.publish(EVENT_EXPECTED_MOVE, {"version": version})
    elif command.type == "em_chain":
        res = compute.em_chain_meta((command.args or {}).get("symbol", "SPY"))
        version = bus.cache_set(CACHE_EM_CHAIN, res)
        bus.publish(EVENT_EM_CHAIN, {"version": version})
    elif command.type == "rescue":
        # position_id may be a paper int OR a captured signal_id string. Coerce
        # to int only for the paper path (the paper loader expects an int id); a
        # captured signal_id is passed through as-is.
        source = command.args.get("source", "paper")
        pid = command.args["position_id"]
        if source == "paper":
            pid = int(pid)
        run_rescue(bus, pid, source)
    elif command.type == "rescue_adhoc":
        # The page sends {"type":"rescue_adhoc","args":{"spec": {...}}}; tolerate
        # the spec being the args dict itself as a fallback.
        run_rescue_adhoc(bus, command.args.get("spec") or command.args)
    elif command.type == "rescue_apply":
        run_rescue_apply(bus, int(command.args["position_id"]),
                         command.args["candidate"])
