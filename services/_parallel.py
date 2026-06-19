"""Tiny ThreadPoolExecutor helper for parallelizing I/O-bound proxy calls.

Several Tier-2 compute paths fan out dozens of schwab-proxy HTTP calls. They are
I/O-bound (a network round-trip to Schwab), and the proxy's rate limiter only
*spaces* upstream calls ~0.2 s apart without holding a lock across the request —
so concurrent calls overlap and cut wall-clock latency several-fold. Keep the
pool modest (the proxy still serializes the dispatch at 0.2 s).

``parallel_map`` mirrors a serial ``[fn(x) for x in items]`` loop, in input
order. ``fn`` must be self-contained and defensive (catch its own per-item
errors and return a sentinel), exactly like the per-item ``try/except`` bodies it
replaces — a raising ``fn`` propagates on result iteration, just as it would in
the serial loop.
"""
from concurrent.futures import ThreadPoolExecutor

DEFAULT_WORKERS = 6


def parallel_map(fn, items, workers: int = DEFAULT_WORKERS):
    """Apply ``fn`` to each item concurrently; return results in input order.

    The pool is sized to ``min(workers, len(items))``. An empty ``items`` returns
    ``[]`` without spinning up a pool.
    """
    items = list(items)
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))
