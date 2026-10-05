"""Two helpers for a service that CONSUMES one of the proxy's SSE streams
(``/stream/quotes``, ``/stream/options``). Pure; stdlib only.

``sentiment_svc/order_flow_consumer.py`` and ``portfolio_svc/scheduler.py`` each
hold an older private copy of both. They are left as they are; a new consumer
imports these instead of adding a third copy.
"""
import json


def parse_sse_line(line):
    """One SSE line as a tick dict, or None when it carries no data: a blank
    line, a comment (``: keepalive``), a non-``data:`` field, or a ``data:``
    payload that is not a JSON object. Never raises."""
    if not isinstance(line, str):
        return None
    line = line.strip()
    if not line or line.startswith(":") or not line.startswith("data:"):
        return None
    try:
        obj = json.loads(line[len("data:"):].strip())
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def reconnect_delay(consecutive_failures, *, base=3.0, cap=60.0):
    """Capped exponential backoff, in seconds: ``base`` on the first reconnect
    (0 failures), doubling per consecutive failure, never above ``cap``."""
    n = max(0, int(consecutive_failures))
    # 2 ** n overflows a float long before a stream has failed n times in a
    # row for real, and min() would then raise instead of capping.
    return cap if n >= 64 else min(base * (2 ** n), cap)
