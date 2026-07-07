"""Streaming aggressor order-flow consumer (aggression axis input).

A sentiment-service-owned background SSE consumer: it subscribes to the proxy's
``GET /stream/quotes?symbols=SPY,QQQ`` equity stream, classifies each trade
buyer/seller-initiated via the PURE ``scoring/order_flow.py`` (Lee-Ready quote
rule + tick test), rolls a per-symbol time window, and publishes
``cache:sentiment:order_flow`` for the five-state classifier's *aggression* axis
(``AGG_WEIGHTS["order_flow"]``). Positive aggressor ratio = net buying = aligned
with the aggression axis (NO sign flip).

Split like ``portfolio_svc``: the PURE helpers (``parse_sse_line`` / ``add_tick``
/ ``prune`` / ``build_order_flow_view``) are unit-tested; the BLOCKING
``_stream_worker`` (reconnect loop, capped backoff, never raises out) mirrors
``portfolio_svc/scheduler.py:_stream_worker`` and is verified live, not unit-tested.

The SSE tick shape from the proxy is ``{"symbol", "last", "net_change", "bid",
"ask", "bid_size", "ask_size", "last_size", "total_volume"}`` (each a float or
None) — see ``schwab_proxy._normalize_level1_equity``. ``build_order_flow_view``
maps ``last_size`` → the per-trade ``size`` the pure ``aggregate_flow`` reads.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

import requests

from repo_paths import PROXY_URL, SENTIMENT

# ``scoring`` lives under the sentiment-dashboard dir. ``compute`` already puts it
# on ``sys.path`` at import, but replicate the minimal glue so this module is
# importable standalone (e.g. a test importing only the consumer) — process is
# isolated so there is no options ``scoring.py`` collision (root CLAUDE.md).
if str(SENTIMENT) not in sys.path:
    sys.path.insert(0, str(SENTIMENT))

from scoring import order_flow as order_flow_mod  # noqa: E402

log = logging.getLogger(__name__)

# Symbols to subscribe to (SPY drives the aggression axis; QQQ collected too so a
# later phase can broaden). Widened from the sentiment-only set at the proxy.
STREAM_SYMBOLS = ("SPY", "QQQ")

# Index underlyings whose near-ATM options feed the OPTION aggressor-flow signal.
OPTION_STREAM_SYMBOLS = ("SPY", "QQQ")
OPTION_N_SIDE = 3               # calls + puts nearest spot per symbol at the front expiry
OPTION_OSI_REFRESH_SEC = 300    # recompute the near-ATM OSI set every 5 min (spot drifts)

WINDOW_SEC = 300           # rolling aggregation window (5 min of trades)
PRUNE_INTERVAL_SEC = 30    # prune the windows at most this often inside the read loop
RECONNECT_WAIT_SEC = 3.0   # base pause before reconnecting a dropped stream
RECONNECT_WAIT_MAX_SEC = 60.0  # cap on the exponential reconnect backoff

CACHE_ORDER_FLOW = "cache:sentiment:order_flow"
EVENT_ORDER_FLOW = "events:sentiment:order_flow"

# Shared rolling windows: {symbol: deque[(monotonic_ts, tick)]}. Touched by the
# blocking worker thread (append) and the publish path (prune + read), so every
# access is serialized by ``_WINDOWS_LOCK``.
_WINDOWS: dict = {}
_WINDOWS_LOCK = threading.Lock()

# A SINGLE rolling window across all subscribed index-option OSIs (each stored
# tick carries its own ``symbol`` = OSI, so put/call is recovered at aggregation
# time). Touched by the option worker (append) + the publish path (prune + read),
# so every access is serialized by ``_OPTION_LOCK``.
_OPTION_WINDOW: deque = deque()
_OPTION_LOCK = threading.Lock()


# ── PURE helpers (unit-tested) ───────────────────────────────────────────────
def parse_sse_line(line):
    """Parse one SSE line into a tick dict, or ``None`` if it carries no data.

    Mirrors ``portfolio-analyzer/src/live.py:parse_sse_line`` (can't import it —
    cross-app). Returns the decoded JSON object for a ``data: {...}`` line; None
    for blank lines, comment lines (``: ...``), non-``data:`` fields, or a
    ``data:`` payload that is not valid JSON.
    """
    if line is None:
        return None
    line = line.strip()
    if not line or line.startswith(":"):
        return None
    if not line.startswith("data:"):
        return None
    payload = line[len("data:"):].strip()
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def add_tick(windows, symbol, tick, now) -> None:
    """Append ``(now, tick)`` to ``windows[symbol]`` (a deque). Operates on the
    passed dict so it is testable. No-op on a falsy symbol/tick."""
    if not symbol or not isinstance(tick, dict):
        return
    windows.setdefault(symbol, deque()).append((now, tick))


def prune(windows, now, window_sec=WINDOW_SEC) -> None:
    """Drop entries older than ``window_sec`` from every symbol's deque (in place)."""
    cutoff = now - window_sec
    for dq in windows.values():
        while dq and dq[0][0] < cutoff:
            dq.popleft()


def _of_tick(t):
    """Map a stored proxy tick to the ``{last, size, bid, ask}`` shape the pure
    ``aggregate_flow`` reads — ``size`` from ``last_size`` (per-trade size),
    falling back to a ``size`` key if a test/caller already normalized it."""
    size = t.get("size")
    if size is None:
        size = t.get("last_size")
    return {"last": t.get("last"), "size": size,
            "bid": t.get("bid"), "ask": t.get("ask")}


def build_order_flow_view(windows, now) -> dict:
    """Aggregate every symbol's window into a JSON-safe order-flow view.

    Returns ``{symbol: {aggressor_ratio, cvd, buy_vol, sell_vol, n, ts}}`` — one
    entry per symbol that has ticks (empty deques are skipped). ``ts`` is a
    wall-clock UTC iso stamp. Defensive: any failure → ``{}`` (never raises)."""
    try:
        ts = datetime.now(timezone.utc).isoformat()
        out = {}
        for symbol, dq in windows.items():
            if not dq:
                continue
            agg = order_flow_mod.aggregate_flow([_of_tick(t) for (_, t) in dq])
            out[symbol] = {
                "aggressor_ratio": agg.get("aggressor_ratio"),
                "cvd": agg.get("cvd"),
                "buy_vol": agg.get("buy_vol"),
                "sell_vol": agg.get("sell_vol"),
                "n": agg.get("n"),
                "ts": ts,
            }
        return out
    except Exception:  # noqa: BLE001 — degrade to empty, never raise.
        log.exception("build_order_flow_view failed")
        return {}


# ── OPTION near-ATM selection + rolling window (PURE / pure-ish, unit-tested) ─
def _exp_date(exp_key):
    """The date portion of a Schwab ``"<expiration>:<dte>"`` map key (sortable)."""
    return str(exp_key).split(":", 1)[0]


def _front_osis(strike_map_by_exp, spot, n_side):
    """The OSIs of the ``n_side`` strikes nearest ``spot`` at the FRONT expiration
    of one side's ``{expKey: {strikeStr: [contract, ...]}}`` map. Defensive → []."""
    if not isinstance(strike_map_by_exp, dict) or not strike_map_by_exp:
        return []
    try:
        front = min(strike_map_by_exp, key=_exp_date)
    except (ValueError, TypeError):
        return []
    strikes = strike_map_by_exp.get(front) or {}
    if not isinstance(strikes, dict):
        return []
    picks = []
    for strike_str, contracts in strikes.items():
        try:
            k = float(strike_str)
        except (TypeError, ValueError):
            continue
        if not isinstance(contracts, (list, tuple)) or not contracts:
            continue
        c = contracts[0]
        osi = c.get("symbol") if isinstance(c, dict) else None
        if osi:
            picks.append((abs(k - spot), osi))
    picks.sort(key=lambda p: p[0])
    return [osi for _, osi in picks[:max(0, int(n_side))]]


def near_atm_osis(chain, spot, n_side=OPTION_N_SIDE) -> list:
    """From a Schwab chain dict, the OSIs of the ``n_side`` calls + ``n_side`` puts
    nearest ``spot`` at the FRONT (earliest) expiration of each side.

    Reads each contract's ``symbol`` (OSI). Defensive → ``[]`` on a bad chain /
    non-numeric spot / missing maps. Never raises."""
    if not isinstance(chain, dict):
        return []
    try:
        spot = float(spot)
    except (TypeError, ValueError):
        return []
    calls = _front_osis(chain.get("callExpDateMap"), spot, n_side)
    puts = _front_osis(chain.get("putExpDateMap"), spot, n_side)
    return calls + puts


def add_option_tick(window, tick, now) -> None:
    """Append ``(now, tick)`` to the option ``window`` deque. No-op on a tick with
    no ``symbol`` (OSI). Operates on the passed deque so it is testable."""
    if not isinstance(tick, dict) or not tick.get("symbol"):
        return
    window.append((now, tick))


def prune_option(window, now, window_sec=WINDOW_SEC) -> None:
    """Drop entries older than ``window_sec`` from the single option deque (in place)."""
    cutoff = now - window_sec
    while window and window[0][0] < cutoff:
        window.popleft()


def _of_option_tick(t):
    """Map a stored proxy option tick to the ``{osi, last, size, bid, ask}`` shape
    ``aggregate_option_flow`` reads — ``osi`` from the proxy ``symbol`` key (the
    subscribed OSI), ``size`` from ``last_size`` (per-trade size), each falling
    back to an already-normalized key if a test/caller supplied one."""
    size = t.get("size")
    if size is None:
        size = t.get("last_size")
    return {"osi": t.get("osi") or t.get("symbol"), "last": t.get("last"),
            "size": size, "bid": t.get("bid"), "ask": t.get("ask")}


def build_option_flow_view(window, now) -> dict:
    """Aggregate the single option window into a JSON-safe put/call-pressure view:
    ``{call_buy, call_sell, put_buy, put_sell, n, signal, ts}``. Empty window → ``{}``.
    Defensive: any failure → ``{}`` (never raises)."""
    try:
        if not window:
            return {}
        ts = datetime.now(timezone.utc).isoformat()
        agg = order_flow_mod.aggregate_option_flow(
            [_of_option_tick(t) for (_, t) in window])
        return {
            "call_buy": agg.get("call_buy"),
            "call_sell": agg.get("call_sell"),
            "put_buy": agg.get("put_buy"),
            "put_sell": agg.get("put_sell"),
            "n": agg.get("n"),
            "signal": agg.get("signal"),
            "ts": ts,
        }
    except Exception:  # noqa: BLE001 — degrade to empty, never raise.
        log.exception("build_option_flow_view failed")
        return {}


def reconnect_delay(consecutive_failures, *, base=RECONNECT_WAIT_SEC,
                    cap=RECONNECT_WAIT_MAX_SEC):
    """Capped exponential backoff for stream reconnects (pure) — mirrors
    ``portfolio_svc/scheduler.py:reconnect_delay``. 0 = first reconnect."""
    n = max(0, int(consecutive_failures))
    return min(base * (2 ** n), cap)


# ── BLOCKING worker (live-verified, NOT unit-tested) ─────────────────────────
def _stream_worker(bus, stop) -> None:
    """Consume the proxy SSE equity stream, rolling ticks into ``_WINDOWS``.

    Mirrors ``portfolio_svc/scheduler.py:_stream_worker``: reconnects until
    ``stop`` is set with CAPPED EXPONENTIAL BACKOFF + a per-failure warning, so a
    permanently-broken stream backs off to a slow poll and is diagnosable instead
    of hammering the proxy. Off-hours the stream is simply quiet (no ticks) — no
    special gate needed. NEVER raises out: a connection/parse error is logged,
    then it pauses and reconnects.
    """
    session = requests.Session()
    syms = ",".join(STREAM_SYMBOLS)
    failures = 0  # consecutive failed connect attempts (for the backoff)
    while not stop.is_set():
        connected_ok = True
        last_prune = time.monotonic()
        try:
            with session.get(f"{PROXY_URL}/stream/quotes",
                             params={"symbols": syms},
                             stream=True,
                             timeout=(10, None)) as resp:  # connect only; SSE = no read timeout
                resp.raise_for_status()
                for raw in resp.iter_lines(decode_unicode=True):
                    if stop.is_set():
                        break
                    tick = parse_sse_line(raw)
                    if tick is None:
                        continue
                    symbol = tick.get("symbol")
                    now = time.monotonic()
                    with _WINDOWS_LOCK:
                        add_tick(_WINDOWS, symbol, tick, now)
                        if now - last_prune >= PRUNE_INTERVAL_SEC:
                            prune(_WINDOWS, now)
                            last_prune = now
        except Exception as e:  # noqa: BLE001 — never let the stream loop die.
            connected_ok = False
            log.warning("order-flow stream disconnected (attempt %d): %s",
                        failures + 1, e)
        if stop.is_set():
            break
        # A session that connected cleanly (incl. off-hours quiet with no ticks)
        # resets the backoff; a connection/HTTP error backs off exponentially.
        if connected_ok:
            failures = 0
            stop.wait(reconnect_delay(0))
        else:
            failures += 1
            stop.wait(reconnect_delay(failures - 1))


def _fetch_near_atm_osis():
    """Fetch the SPY+QQQ chains + spot via the proxy → the combined near-ATM OSI
    list (both underlyings' ``n_side`` calls + puts at each front expiration).

    Impure (proxy calls) — kept out of the pure ``near_atm_osis`` so that stays
    unit-testable. Fully defensive: a per-symbol failure is skipped and any
    catastrophic failure returns ``[]`` (never raises). Off-hours the chain still
    returns the last session's contracts, so the OSI set stays warm."""
    from services import _proxy
    from datetime import date, timedelta
    today_iso = date.today().isoformat()
    to_iso = (date.today() + timedelta(days=8)).isoformat()
    out = []
    for sym in OPTION_STREAM_SYMBOLS:
        try:
            chain = _proxy.schwab_client._request("/chains", params={
                "symbol": sym, "contractType": "ALL", "range": "NTM",
                "strikeCount": 40, "fromDate": today_iso, "toDate": to_iso})
            spot = _chain_spot(chain, sym)
            if spot is None:
                continue
            out.extend(near_atm_osis(chain, spot, n_side=OPTION_N_SIDE))
        except Exception:  # noqa: BLE001 — one symbol's failure must not sink the set.
            log.debug("near-ATM OSI fetch failed for %s", sym, exc_info=True)
            continue
    return out


def _chain_spot(chain, symbol):
    """Spot for ``symbol``: the chain's ``underlyingPrice``, else a live quote,
    else None. Defensive — never raises."""
    if isinstance(chain, dict):
        up = chain.get("underlyingPrice")
        try:
            if up is not None:
                return float(up)
        except (TypeError, ValueError):
            pass
    try:
        from services import _proxy
        q = _proxy.schwab_client.get_quote(symbol) or {}
        last = q.get("last") if isinstance(q, dict) else None
        return float(last) if last is not None else None
    except Exception:  # noqa: BLE001
        return None


def _option_stream_worker(bus, stop) -> None:
    """Consume the proxy SSE OPTION stream for the near-ATM index OSIs, rolling
    ticks into ``_OPTION_WINDOW``.

    Mirrors ``_stream_worker`` but recomputes the near-ATM OSI set every
    ``OPTION_OSI_REFRESH_SEC`` (spot drifts): it reconnects with the fresh set
    when it CHANGES. Off-hours the stream is simply quiet; if no chain/OSI set is
    available it backs off and retries the OSI fetch. Reconnects with CAPPED
    EXPONENTIAL BACKOFF until ``stop``. NEVER raises out — a connection/parse error
    is logged, then it pauses and reconnects."""
    session = requests.Session()
    failures = 0            # consecutive failed connect/fetch attempts (backoff)
    current_osis = []
    osi_deadline = 0.0      # monotonic time at which to recompute the OSI set
    while not stop.is_set():
        now = time.monotonic()
        if now >= osi_deadline:
            fresh = _fetch_near_atm_osis()
            osi_deadline = now + OPTION_OSI_REFRESH_SEC
            if fresh:
                current_osis = fresh
        if not current_osis:
            # No chain / OSI set (off-hours or proxy hiccup) — back off and retry.
            failures += 1
            stop.wait(reconnect_delay(min(failures - 1, 5)))
            continue
        connected_ok = True
        last_prune = time.monotonic()
        try:
            with session.get(f"{PROXY_URL}/stream/options",
                             params={"symbols": ",".join(current_osis)},
                             stream=True,
                             timeout=(10, None)) as resp:  # connect only; SSE = no read timeout
                resp.raise_for_status()
                for raw in resp.iter_lines(decode_unicode=True):
                    if stop.is_set():
                        break
                    now = time.monotonic()
                    # OSI refresh due? recompute; reconnect only if the set CHANGED.
                    if now >= osi_deadline:
                        fresh = _fetch_near_atm_osis()
                        osi_deadline = now + OPTION_OSI_REFRESH_SEC
                        if fresh and set(fresh) != set(current_osis):
                            current_osis = fresh
                            break  # reconnect with the new OSI set
                    tick = parse_sse_line(raw)
                    if tick is None:
                        continue
                    with _OPTION_LOCK:
                        add_option_tick(_OPTION_WINDOW, tick, now)
                        if now - last_prune >= PRUNE_INTERVAL_SEC:
                            prune_option(_OPTION_WINDOW, now)
                            last_prune = now
        except Exception as e:  # noqa: BLE001 — never let the stream loop die.
            connected_ok = False
            log.warning("option order-flow stream disconnected (attempt %d): %s",
                        failures + 1, e)
        if stop.is_set():
            break
        if connected_ok:
            failures = 0
            stop.wait(reconnect_delay(0))
        else:
            failures += 1
            stop.wait(reconnect_delay(failures - 1))


def publish(bus) -> None:
    """Prune + build the equity AND option order-flow views and publish them.

    ``cache:sentiment:order_flow`` = ``{<symbol>: {equity flow}, ..., "options":
    {option flow}}`` — the per-symbol equity keys are unchanged; the ``options``
    key is ADDED when there is option flow. Defensive — a failure is logged and
    swallowed so the caller's refresh loop is never broken."""
    try:
        now = time.monotonic()
        with _WINDOWS_LOCK:
            prune(_WINDOWS, now)
            view = build_order_flow_view(_WINDOWS, now)
        with _OPTION_LOCK:
            prune_option(_OPTION_WINDOW, now)
            opt_view = build_option_flow_view(_OPTION_WINDOW, now)
        if opt_view:
            view = dict(view)
            view["options"] = opt_view
        version = bus.cache_set(CACHE_ORDER_FLOW, view, event=EVENT_ORDER_FLOW)
        return version
    except Exception:  # noqa: BLE001
        log.exception("order-flow publish failed")
        return None


def start_consumer(bus):
    """Launch the equity SSE consumer on a daemon thread; return its ``stop`` Event.

    Mirrors ``portfolio_svc/scheduler.py:_start_stream``. The caller sets the
    returned Event to stop the worker on shutdown."""
    stop = threading.Event()
    thread = threading.Thread(target=_stream_worker, args=(bus, stop),
                              daemon=True, name="sentiment-order-flow")
    thread.start()
    return stop


def start_option_consumer(bus):
    """Launch the OPTION SSE consumer on a daemon thread; return its ``stop`` Event.
    Mirrors ``start_consumer`` for the near-ATM index-option aggressor stream."""
    stop = threading.Event()
    thread = threading.Thread(target=_option_stream_worker, args=(bus, stop),
                              daemon=True, name="sentiment-option-flow")
    thread.start()
    return stop
