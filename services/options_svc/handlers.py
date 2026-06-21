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
from services.options_svc import compute
from shared.contracts.options import ScanResult

CACHE_SCAN = "cache:options:scan"
EVENT_SCAN = "events:options:scan"

CACHE_HEADER = "cache:options:header"
EVENT_HEADER = "events:options:header"

CACHE_SWING = "cache:options:swing"
EVENT_SWING = "events:options:swing"

CACHE_PAPER = "cache:options:paper_account"
EVENT_PAPER = "events:options:paper_account"

CACHE_PAPER_TRADES = "cache:options:paper_trades"
EVENT_PAPER_TRADES = "events:options:paper_trades"

CACHE_PAPER_ANALYZE = "cache:options:paper_analyze"
EVENT_PAPER_ANALYZE = "events:options:paper_analyze"

CACHE_CAPTURED = "cache:options:captured"
EVENT_CAPTURED = "events:options:captured"

CACHE_CAPTURED_FLAGS = "cache:options:captured_flags"
EVENT_CAPTURED_FLAGS = "events:options:captured_flags"

CACHE_GAMMA = "cache:options:gamma"
EVENT_GAMMA = "events:options:gamma"

CACHE_GAMMA_EXPLAIN = "cache:options:gamma_explain"
EVENT_GAMMA_EXPLAIN = "events:options:gamma_explain"

CACHE_GAMMA_ANALYZE = "cache:options:gamma_analyze"
EVENT_GAMMA_ANALYZE = "events:options:gamma_analyze"

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

CACHE_GEX_STATUS = "cache:options:gex_status"
EVENT_GEX_STATUS = "events:options:gex_status"

CACHE_EXPECTED_MOVE = "cache:options:expected_move"
EVENT_EXPECTED_MOVE = "events:options:expected_move"

CACHE_RESCUE = "cache:options:rescue"            # per-id: f"{CACHE_RESCUE}:{position_id}"
CACHE_RESCUE_SUMMARY = "cache:options:rescue_summary"
EVENT_RESCUE = "events:options:rescue"
EVENT_RESCUE_SUMMARY = "events:options:rescue_summary"

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
}

# The six fields ScanResult validates — we project the engine dict onto exactly
# these (dropping the extra keys the GUI ignores). ``.get`` with a default of
# the field's container type keeps missing optional keys from crashing, while
# the ScanResult construction validates the *types* of whatever is present.
_SCAN_DEFAULTS = {
    "signals_0dte": [],
    "signals_swing": [],
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


def swing_scan(bus, args: dict) -> None:
    """Run a user-parameterized swing scan, cache the result, publish an event.

    On-demand only (not scheduled): the GUI Swing page enqueues a ``swing_scan``
    command with the user's inputs in ``args``; we extract them (falling back to
    the page-default for any missing key), call ``compute.swing_scan`` with the
    raw client objects, and cache the signal list (plus the symbol + original
    args, for the page to display/debug) under ``cache:options:swing``.

    No ScanResult gate: this is a flat signal list (not the dual-list scan
    contract), and the page reads ``payload["signals"]`` directly."""
    args = args or {}
    params = {k: args.get(k, default) for k, default in _SWING_DEFAULTS.items()}
    signals = compute.swing_scan(**params)
    payload = {"signals": signals, "symbol": params["symbol"], "params": args}
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
        pass


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
    target/stop hits) then republish the paper account view.

    Guarded on an existing account — with no account it just refreshes so the
    page shows the no-account state. Shared by the ``paper_manage`` command
    (manual button) and the scheduler's auto-manage tick (``scheduler.manage_due``)
    so both run identical logic."""
    if compute.has_paper_account():
        compute.run_manage_cycle()
    refresh_paper_account(bus)
    # Piggyback the manage tick (no new cadence): publish the rescue summary for
    # the nav badge. Defensive so it never blocks the core paper refresh.
    try:
        publish_rescue_summary(bus)
    except Exception:
        pass


def refresh_paper_trades(bus) -> None:
    """Read the paper-trade ledger view and publish it to the bus.

    No strict contract: the view is a loosely-shaped read-only dict
    (``{"trades": [...]}``) that only the Paper Trades page consumes, and
    ``compute.paper_trades_view`` is already fully defensive."""
    data = compute.paper_trades_view()
    version = bus.cache_set(CACHE_PAPER_TRADES, data)
    bus.publish(EVENT_PAPER_TRADES, {"version": version})


def refresh_captured(bus) -> None:
    """Read the open-signals view and publish it to the bus.

    No strict contract: the view is a loosely-shaped read-only dict
    (``{"signals": [...]}``) that only the Captured Signals page consumes, and
    ``compute.captured_view`` is already fully defensive."""
    data = compute.captured_view()
    version = bus.cache_set(CACHE_CAPTURED, data)
    bus.publish(EVENT_CAPTURED, {"version": version})


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
    itself defensive (per-symbol failures are logged, not raised)."""
    compute.collect_gex_snapshots()


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


def publish_gamma_symbols(bus) -> None:
    """Compute the Gamma dropdown universe (collected symbols minus $VIX) and
    publish it to the bus so the Tier-3 Gamma page can populate its symbol
    dropdown without importing any engine. No strict contract: a small read-only
    ``{"symbols":[...]}`` dict; ``compute.gamma_symbol_options`` is defensive."""
    data = {"symbols": compute.gamma_symbol_options()}
    version = bus.cache_set(CACHE_GAMMA_SYMBOLS, data)
    bus.publish(EVENT_GAMMA_SYMBOLS, {"version": version})


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:options`` command. ``rescan`` → full rescan;
    ``swing_scan`` → on-demand parameterized swing scan; ``refresh_paper`` →
    re-read the paper account; ``paper_entry``/``paper_manage`` → run the cycle
    (guarded on an existing account) then refresh; ``paper_reset`` → reset the
    account then refresh; ``paper_create`` (args signal, qty) → create + persist a
    paper trade from a signal then refresh the ledger; ``paper_reload`` → re-read
    the trade ledger;
    ``paper_close``/``paper_delete``/``paper_delete_closed`` → run the lifecycle
    action then refresh the ledger; ``paper_analyze`` → analyze the selected
    trade, cache the result + publish; ``captured_reload`` → re-read open signals;
    ``captured_reprice`` → reprice all open signals, cache the repriced list +
    flags (two views) + publish both; ``captured_close`` → manually close a signal
    then refresh; ``gamma_refresh`` (args symbol, default ``$SPX``) → recompute the
    Gamma snapshot; ``gamma_explain`` (args symbol) → build the Explain body, cache
    + publish; ``gamma_analyze`` → build the bundled SPX/SPY/QQQ prompt, cache +
    publish; ``sim_fetch`` (args symbol) → fetch the simulator ChainSnapshot
    (stashed in-process), cache the selector meta + publish; ``sim_run`` (args
    symbol/expiry/kind/strike/direction/dt/mult) → compute both sweeps, cache the
    result + publish; ``calc_load`` (args symbol) → fetch the quote + option chain,
    cache the loader payload (chain dict + price + range) + publish; ``calc_compute``
    (args = the calc params dict) → run the summary + P&L grid math, cache the
    result + publish; ``expected_move`` (args symbol/expiry/legs) → build the
    expected-move cone payload, cache the result + publish; else no-op."""
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
    elif command.type == "paper_create":
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
        ver = bus.cache_set(CACHE_CAPTURED, {"signals": res["signals"]})
        bus.publish(EVENT_CAPTURED, {"version": ver})
        fver = bus.cache_set(CACHE_CAPTURED_FLAGS, {"flags": res["flags"]})
        bus.publish(EVENT_CAPTURED_FLAGS, {"version": fver})
    elif command.type == "captured_close":
        compute.close_captured(command.args.get("signal_id"),
                               command.args.get("exit_val", 0.0),
                               command.args.get("reason", "MANUAL_CLOSE"))
        refresh_captured(bus)
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
    elif command.type == "sim_fetch":
        meta = compute.sim_fetch(command.args.get("symbol", "SPY"))
        version = bus.cache_set(CACHE_SIM_META, meta)
        bus.publish(EVENT_SIM_META, {"version": version})
    elif command.type == "sim_run":
        a = command.args or {}
        result = compute.sim_run(
            a.get("symbol"), a.get("expiry"), a.get("kind"), a.get("strike"),
            a.get("direction"), a.get("dt"), a.get("mult"))
        version = bus.cache_set(CACHE_SIM_RESULT, result)
        bus.publish(EVENT_SIM_RESULT, {"version": version})
    elif command.type == "sim_replay":
        a = command.args or {}
        res = compute.sim_replay(
            a.get("symbol"), a.get("expiry"), a.get("kind"),
            a.get("strike"), a.get("direction"), a.get("lookback", "auto"))
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
    elif command.type == "expected_move":
        a = command.args or {}
        res = compute.compute_expected_move(
            a.get("symbol"), a.get("expiry"), a.get("legs") or [],
            a.get("lookback", "auto"))
        version = bus.cache_set(CACHE_EXPECTED_MOVE, res)
        bus.publish(EVENT_EXPECTED_MOVE, {"version": version})
