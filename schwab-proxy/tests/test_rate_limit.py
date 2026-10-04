"""The marketdata rate limiter must SPACE concurrent callers, not let them burst.

Unsynchronized, the 8-thread ``parallel_map`` fan-outs could all read the same
last-request time and fire together (a 429 risk). The gate ``_rate_limit`` goes
through (``rate_gate.RateGate``, built by ``_new_gate``) serializes the spacing;
its ordering is tested in test_rate_gate.py.
"""
import threading
import time
import types

import schwab_proxy as sp


def test_rate_limit_serializes_concurrent_callers(monkeypatch):
    monkeypatch.setattr(sp, "MIN_REQUEST_INTERVAL", 0.02)
    monkeypatch.setattr(sp.api_call_counter, "record", lambda *a, **k: None)
    obj = types.SimpleNamespace(_gate=sp._new_gate())

    start = time.monotonic()
    threads = [threading.Thread(target=lambda: sp.TokenManager._rate_limit(obj))
               for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - start

    # 5 calls, spaced 0.02s apart => at least 4 gaps (allow scheduling slack).
    assert elapsed >= 0.02 * 4 * 0.7
