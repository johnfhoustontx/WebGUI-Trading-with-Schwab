"""Stream a flagged flow-alert contract AFTER its alert has fired.

One daemon thread holds one SSE connection to the proxy's ``/stream/options``
for the day's flagged contracts (``flow_sides_tick.wanted_osis``) and hands each
level-one tick to ``flow_sides_tick.stream_tick``. The minute poll already
reports the whole session, including the volume that caused the alert; this is
the finer reading of what trades afterwards. Level-one conflates rapid ticks, so
it is a finer SAMPLE, not a tape.

The proxy keeps paper-trade leg tracking safe on its own: a flow subscription
can never drop a tracked leg (schwab-proxy/CLAUDE.md). The worker never raises
out, and a dead stream leaves the poll tally intact.

Split like ``sentiment_svc/order_flow_consumer``: the decisions (``handle``,
``set_changed``, ``_consume`` over an injected session, ``_worker`` over an
injected ``_consume``) are unit-tested; the real socket is verified live.
Design: docs/plans/2026-10-04-flow-alert-sides-design.md.
"""
import logging
import threading
import time

import requests

from repo_paths import PROXY_URL

from services import _sse
from services.options_svc import flow_sides_tick

log = logging.getLogger(__name__)

THREAD_NAME = "options-flow-stream"

# These follow the proxy and the poll, not an operator's choice, so they are
# constants rather than config:
IDLE_POLL_SEC = 5.0         # an idle worker looks for its first contract this often
RECHECK_SEC = 5.0           # a connected one looks for a changed set this often
CONNECT_TIMEOUT_SEC = 10
# The proxy sends a keepalive comment every 15 s on a quiet stream. Three missed
# in a row is a dead proxy, not a quiet one: time out and reconnect, rather than
# block for ever on a socket nobody will write to again.
READ_TIMEOUT_SEC = 45
RECONNECT_WAIT_SEC = 3.0
RECONNECT_WAIT_MAX_SEC = 60.0


def stream_params(osis) -> dict:
    """The query for ``/stream/options``. Contract symbols go as they are:
    the padding inside one (``"SPY   261009C00770000"``) is part of it."""
    return {"symbols": ",".join(osis)}


def set_changed(current, wanted) -> bool:
    """Whether the worker must reconnect: the wanted set differs from the one
    it is connected with. Within a session the set only grows (a new alert); a
    new session date empties it."""
    return set(current) != set(wanted)


def handle(tick, seen) -> None:
    """Book one tick. ``seen`` holds the contracts already heard from on THIS
    connection: a contract's first tick after a connect is passed unlabelled,
    because whatever printed while the stream was down cannot take the label of
    the first quote seen after it."""
    osi = tick.get("symbol") if isinstance(tick, dict) else None
    if not osi:
        return
    flow_sides_tick.stream_tick(tick, label=osi in seen)
    # Only a tick that CARRIED a volume uses up the unlabelled first step: a
    # quote-only tick arriving first would otherwise let the next one, which
    # brings everything printed while the stream was down, take a label.
    if tick.get("total_volume") is not None:
        seen.add(osi)


def _consume(session, osis, stop) -> bool:
    """One connection, until it ends. True when it was left because the wanted
    set changed (reconnect at once); False when the stream ended or ``stop``
    was set. Raises on a connection or HTTP error."""
    seen: set = set()
    next_check = time.monotonic() + RECHECK_SEC
    with session.get(f"{PROXY_URL}/stream/options", params=stream_params(osis),
                     stream=True,
                     timeout=(CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC)) as resp:
        resp.raise_for_status()
        # A quiet stream still yields a keepalive line every 15 s, so the two
        # checks below run at least that often.
        for raw in resp.iter_lines(decode_unicode=True):
            if stop.is_set():
                return False
            now = time.monotonic()
            if now >= next_check:
                next_check = now + RECHECK_SEC
                if set_changed(osis, flow_sides_tick.wanted_osis()):
                    return True
            tick = _sse.parse_sse_line(raw)
            if tick is not None:
                handle(tick, seen)
    return False


def _pause(stop, seconds) -> None:
    stop.wait(seconds)


def _worker(stop) -> None:
    """Hold the stream until ``stop`` is set. Capped exponential backoff on a
    failed connection, one WARNING per failure. NEVER raises out."""
    session = requests.Session()
    failures = 0
    while not stop.is_set():
        osis = flow_sides_tick.wanted_osis()
        if not osis:
            _pause(stop, IDLE_POLL_SEC)
            continue
        try:
            changed = _consume(session, osis, stop)
        except Exception as e:  # noqa: BLE001 - the loop must outlive any one stream
            log.warning("flow stream disconnected (attempt %d): %s", failures + 1, e)
            if stop.is_set():
                break
            _pause(stop, _sse.reconnect_delay(
                failures, base=RECONNECT_WAIT_SEC, cap=RECONNECT_WAIT_MAX_SEC))
            failures += 1
            continue
        failures = 0
        if not changed and not stop.is_set():
            # The proxy closed the stream (a restart): give it a moment.
            _pause(stop, _sse.reconnect_delay(
                0, base=RECONNECT_WAIT_SEC, cap=RECONNECT_WAIT_MAX_SEC))


_RUNNING: dict = {"thread": None, "stop": None}


def start() -> threading.Event:
    """Launch the worker on a daemon thread; return its ``stop`` Event.

    ONE worker per process: the scheduler loop that calls this is restarted by
    its supervisor after a crash, and each call would otherwise add a thread
    and a second proxy subscription. While a worker is alive its own stop
    event is returned."""
    thread = _RUNNING["thread"]
    if thread is not None and thread.is_alive():
        return _RUNNING["stop"]
    stop = threading.Event()
    thread = threading.Thread(target=_worker, args=(stop,), daemon=True,
                              name=THREAD_NAME)
    _RUNNING.update(thread=thread, stop=stop)
    thread.start()
    return stop
