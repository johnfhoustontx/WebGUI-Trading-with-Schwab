"""``tee``'s shadow check: derive each stored raw minute again and compare it
with what the live collector wrote for that minute.

The raw feed's promise is that a minute derived later from the store is the
minute derived live (docs/plans/2026-10-14-raw-feed-and-catch-up-design.md §7).
This is the proof, run on prod before anything relies on it, and the
measurement of what one minute costs to derive (the gate for ``mode = "on"``:
no differences, and 95% of minutes under 2 s).

* It derives into a fresh in-memory database with ``gex_collector.derive_minute``
  and NO ``on_chain`` hooks: the hooks keep state (the bought/sold tally,
  traded premium, the detectors) and push alerts, and a second pass would
  double them. So it compares what ``derive_minute`` writes -- the five views and
  the term rows -- and nothing written beside the poll (``tprem``, HIRO).
* It runs on ONE worker thread of its own and never queues: a minute that
  arrives while the last is still being compared is skipped and counted, so
  the shadow can never delay the live collector.
* It writes nothing to ``gex_history.db`` or Redis but its own stats view
  (``handlers`` publishes ``stats()`` to ``cache:feed:shadow``), and sends no push.

Imports nothing from ``compute``.
"""
from __future__ import annotations

import collections
import logging
import math
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from services import _degrade
from services.options_svc import raw_tee
from shared import raw_store

log = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")
#: The views ``gex_collector.derive_minute`` writes; the only ones compared.
DERIVED_VIEWS = ("gex", "charm", "dex", "vanna", "prem")
#: Floats are compared this closely. Both sides run the same code on the same
#: chain, so anything wider than rounding noise is a real difference.
_REL_TOL, _ABS_TOL = 1e-9, 1e-12
_TERM_SYMBOL = "SPX"            # the symbol derive_term writes its rows under
_MS_KEEP = 2000                 # per-minute timings kept for the percentiles
_LOG_EVERY = 15                 # minutes between INFO summaries
#: The other per-minute costs timed beside the derive. hooks + alerts are what
#: the deriver adds to the derive for every minute it works through; tee is the
#: write the collector makes on its own thread, reported apart.
COSTS = ("hooks", "alerts", "tee")

_EXEC = ThreadPoolExecutor(max_workers=1, thread_name_prefix="raw-shadow")
_LOCK = threading.Lock()
_STATE: dict = {}


def reset_stats() -> None:
    """Start the counters again (a test hook; also what a new process starts with)."""
    with _LOCK:
        _STATE.clear()
        _STATE.update(future=None, last=None, minutes=0, minutes_with_diffs=0,
                      skipped=0, missing_raw=0, last_diff=None,
                      ms=collections.deque(maxlen=_MS_KEEP),
                      costs={k: collections.deque(maxlen=_MS_KEEP) for k in COSTS})


reset_stats()


def _engines():
    """The options-scanner modules, imported lazily (they live in a hyphenated
    folder the service puts on ``sys.path``)."""
    import repo_paths
    path = str(repo_paths.OPTIONS_SCANNER)
    if path not in sys.path:
        sys.path.insert(0, path)
    import gex_collector
    import gex_history_db
    from gamma_tool import GammaEngine
    return gex_collector, gex_history_db, GammaEngine


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(a) or math.isnan(b):
            # A grid cell can hold NaN; NaN on both sides is the same reading.
            return math.isnan(a) and math.isnan(b)
        return math.isclose(a, b, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)
    return a == b


def _grid_diffs(name, live, shadow) -> list:
    out = []
    live, shadow = live or {}, shadow or {}
    for strike in sorted(set(live) | set(shadow), key=float):
        a, b = live.get(strike), shadow.get(strike)
        if a is None or b is None:
            out.append(f"{name} grid {strike}: {'missing from live' if a is None else 'missing from shadow'}")
            continue
        for k in sorted(set(a) | set(b)):
            if not _same(a.get(k), b.get(k)):
                out.append(f"{name} grid {strike} {k}: live {a.get(k)!r} shadow {b.get(k)!r}")
    return out


def _row_diffs(live: dict, shadow: dict) -> list:
    out = []
    for key in sorted(set(live) | set(shadow)):
        name = f"{key[0]}/{key[1]}"
        a, b = live.get(key), shadow.get(key)
        if a is None or b is None:
            out.append(f"{name} {'missing from live' if a is None else 'missing from shadow'}")
            continue
        for col in sorted(set(a) | set(b)):
            if col == "grid":
                out += _grid_diffs(name, a.get("grid"), b.get("grid"))
            elif not _same(a.get(col), b.get(col)):
                out.append(f"{name} {col}: live {a.get(col)!r} shadow {b.get(col)!r}")
    return out


def _term_diffs(live: list, shadow: list) -> list:
    key = lambda r: (r["expiration_date"], r["strike"])  # noqa: E731
    a, b = {key(r): r for r in live}, {key(r): r for r in shadow}
    out = []
    for k in sorted(set(a) | set(b)):
        if k not in a or k not in b:
            out.append(f"term {k}: {'missing from live' if k not in a else 'missing from shadow'}")
            continue
        for col in ("call_gex_usd", "put_gex_usd", "net_gex_usd", "underlying_price"):
            if not _same(a[k][col], b[k][col]):
                out.append(f"term {k} {col}: live {a[k][col]!r} shadow {b[k][col]!r}")
    return out


def compare_minute(date_iso: str, ts: int, live_conn=None, root=None) -> dict:
    """Derive raw minute ``ts`` of session ``date_iso`` again and compare it with
    the live rows. ``{"ts", "rows", "diffs": [str], "ms", "missing_raw"}``;
    ``ms`` is the time the derive took. ``live_conn`` None opens
    ``gex_history.db`` read-only."""
    started = time.perf_counter()
    minute = raw_store.read_minute(date_iso, ts, root=root)
    if minute is None:
        return {"ts": int(ts), "rows": 0, "diffs": [], "ms": 0.0, "missing_raw": True}
    gc, gh, GammaEngine = _engines()
    shadow = sqlite3.connect(":memory:")
    try:
        gh.init_schema(shadow)
        gh.init_term_schema(shadow)
        gc.derive_minute(GammaEngine(), shadow, minute)
        ms = (time.perf_counter() - started) * 1000.0
        own = live_conn is None
        live = gh.connect(read_only=True) if own else live_conn
        try:
            shadow_rows = gh.rows_at(shadow, ts, DERIVED_VIEWS)
            diffs = _row_diffs(gh.rows_at(live, ts, DERIVED_VIEWS), shadow_rows)
            if minute.term is not None:
                ts_iso = datetime.fromtimestamp(int(ts), CT).isoformat()
                diffs += _term_diffs(gh.load_term_snapshot(live, ts_iso, _TERM_SYMBOL),
                                     gh.load_term_snapshot(shadow, ts_iso, _TERM_SYMBOL))
        finally:
            if own:
                live.close()
    finally:
        shadow.close()
    return {"ts": int(ts), "rows": len(shadow_rows), "diffs": diffs, "ms": ms,
            "missing_raw": False}


def _percentile(sorted_ms, q):
    """Nearest-rank percentile of an ascending list, or None when empty."""
    if not sorted_ms:
        return None
    return sorted_ms[max(0, math.ceil(q * len(sorted_ms)) - 1)]


def record(out: dict) -> None:
    """Fold one comparison into the counters."""
    with _LOCK:
        if out.get("missing_raw"):
            _STATE["missing_raw"] += 1
            return
        _STATE["minutes"] += 1
        _STATE["ms"].append(float(out.get("ms") or 0.0))
        if out.get("diffs"):
            _STATE["minutes_with_diffs"] += 1
            _STATE["last_diff"] = out["diffs"][0]
        n = _STATE["minutes"]
    if out.get("diffs"):
        log.warning("raw shadow: minute %s differs in %d place(s): %s", out.get("ts"),
                    len(out["diffs"]), "; ".join(out["diffs"][:5]))
    if n % _LOG_EVERY == 0:
        log.info("raw shadow: %s", stats())


def record_cost(kind: str, ms) -> None:
    """Fold one minute's ``kind`` cost (one of ``COSTS``) into the counters."""
    if kind not in COSTS:
        return
    with _LOCK:
        _STATE["costs"][kind].append(float(ms))


def stats() -> dict:
    """``{"minutes", "minutes_with_diffs", "skipped", "missing_raw", "p50_ms",
    "p95_ms", "<cost>_p50_ms", "<cost>_p95_ms" for each of ``COSTS``,
    "gate_p95_ms", "last_diff"}`` since the process started.

    ``p50_ms`` / ``p95_ms`` are the derive alone (reading the raw minute and
    deriving it). ``gate_p95_ms`` is derive + hooks + alerts at the 95th
    percentile each -- an upper bound on what the deriver spends per minute,
    and the figure the "on" gate (under 2000 ms) is read against."""
    with _LOCK:
        ordered = sorted(_STATE["ms"])
        out = {"minutes": _STATE["minutes"],
               "minutes_with_diffs": _STATE["minutes_with_diffs"],
               "skipped": _STATE["skipped"], "missing_raw": _STATE["missing_raw"],
               "p50_ms": _percentile(ordered, 0.50), "p95_ms": _percentile(ordered, 0.95),
               "last_diff": _STATE["last_diff"]}
        for kind in COSTS:
            cost = sorted(_STATE["costs"][kind])
            out[f"{kind}_p50_ms"] = _percentile(cost, 0.50)
            out[f"{kind}_p95_ms"] = _percentile(cost, 0.95)
    parts = (out["p95_ms"], out["hooks_p95_ms"], out["alerts_p95_ms"])
    out["gate_p95_ms"] = (None if out["p95_ms"] is None
                          else sum(p for p in parts if p is not None))
    return out


def _busy() -> bool:
    future = _STATE.get("future")
    return future is not None and not future.done()


def _run(date_iso, ts) -> None:
    try:
        record(compare_minute(date_iso, ts))
    except Exception:
        _degrade.degraded("options.raw_shadow")


def submit_latest(wait: bool = False) -> bool:
    """Compare the last minute ``raw_tee`` wrote, on the shadow's own thread.
    False when there is nothing new, or the previous comparison is still running
    (that minute is skipped and counted). ``wait`` blocks until it is done (tests)."""
    latest = raw_tee.last_written()
    if latest is None or latest == _STATE.get("last"):
        return False
    if _busy():
        with _LOCK:
            _STATE["skipped"] += 1
        return False
    _STATE["last"] = latest
    _STATE["future"] = future = _EXEC.submit(_run, *latest)
    if wait:
        future.result()
    return True
