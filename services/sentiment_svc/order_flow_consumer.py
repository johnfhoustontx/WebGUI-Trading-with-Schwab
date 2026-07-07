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


def publish(bus) -> None:
    """Prune + build the order-flow view and publish it. Defensive — a failure is
    logged and swallowed so the caller's refresh loop is never broken."""
    try:
        now = time.monotonic()
        with _WINDOWS_LOCK:
            prune(_WINDOWS, now)
            view = build_order_flow_view(_WINDOWS, now)
        version = bus.cache_set(CACHE_ORDER_FLOW, view, event=EVENT_ORDER_FLOW)
        return version
    except Exception:  # noqa: BLE001
        log.exception("order-flow publish failed")
        return None


def start_consumer(bus):
    """Launch the SSE consumer on a daemon thread; return its ``stop`` Event.

    Mirrors ``portfolio_svc/scheduler.py:_start_stream``. The caller sets the
    returned Event to stop the worker on shutdown."""
    stop = threading.Event()
    thread = threading.Thread(target=_stream_worker, args=(bus, stop),
                              daemon=True, name="sentiment-order-flow")
    thread.start()
    return stop
