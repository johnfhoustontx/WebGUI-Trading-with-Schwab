"""Portfolio service handlers (Tier-2 → Tier-3 write path).

Three entry points:

* ``rebuild`` — full model build (proxy health → daily trade sync → sector
  breakdown / vs-sector perf via ``build_portfolio`` → slow per-symbol
  baselines) into the shared :data:`state.STATE`, then publish. Fired at startup,
  on a periodic refresh, and when a ``refresh`` command is pending.
* ``publish_current`` — re-format the *current* state into display rows and
  cache+publish. Cheap; the scheduler calls it on each throttled stream tick.
* ``handle_command`` — maps ``{"type":"refresh"}`` to a deferred rebuild (it sets
  the ``rebuild_requested`` flag the scheduler acts on, since the scheduler owns
  the SSE stream restart).

Each publish validates the payload against ``PortfolioModel`` as a gate against
gross drift BEFORE caching, then publishes a change event so the GUI version-poll
repaints. Kept synchronous — the scaffold's consumer/scheduler loops drive them.
"""
from services.portfolio_svc import compute
from services.portfolio_svc.state import STATE
from shared.contracts.portfolio import PortfolioModel

CACHE = "cache:portfolio:positions"
EVENT = "events:portfolio:positions"


def publish_current(bus, state) -> int:
    """Format the current state into display rows, cache it, publish an event."""
    with state.lock:
        model = state.raw_model
        baselines = state.baselines
        proxy_up = state.proxy_up
        streaming = state.streaming
        errors = list(state.errors)
    payload = compute.format_payload(model, baselines, proxy_up=proxy_up,
                                     streaming=streaming, errors=errors)
    pm = PortfolioModel(**payload)
    version = bus.cache_set(CACHE, pm.model_dump())
    bus.publish(EVENT, {"version": version})
    return version


def rebuild(bus, state) -> None:
    """Full model rebuild into ``state`` (defensive), then publish.

    A proxy-down state publishes an empty model carrying a "Proxy not reachable"
    note rather than failing — the page shows the waiting/down banner.
    """
    data = compute.make_data()
    if not compute.proxy_up():
        with state.lock:
            state.raw_model = {"holdings": [], "sectors": []}
            state.baselines = {}
            state.proxy_up = False
            state.errors = ["Proxy not reachable"]
        publish_current(bus, state)
        return

    trades = compute.load_trades(data)
    model, errs = compute.build_raw_model(data, trades)
    # Baselines are the slow per-symbol history pass (~2N proxy calls). Recompute
    # them only when the holdings/trades/day signature changed — otherwise reuse
    # the cached ones, so the periodic 10-min rebuild stops re-fetching unchanged
    # history every cycle.
    sig = compute.baseline_signature(model, trades)
    with state.lock:
        prev_sig = state.baseline_sig
        prev_baselines = state.baselines
    if sig == prev_sig and prev_baselines:
        baselines = prev_baselines
    else:
        baselines = compute.compute_baselines(data, model, trades)
    with state.lock:
        state.raw_model = model
        state.baselines = baselines
        state.baseline_sig = sig
        state.trades = trades
        state.proxy_up = True
        state.errors = errs
    publish_current(bus, state)


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:portfolio`` command; unknown types are a no-op.

    ``refresh`` flags a rebuild for the scheduler loop (which owns the SSE stream
    restart) rather than rebuilding inline on the consumer coroutine.
    """
    if command.type == "refresh":
        STATE.rebuild_requested = True
