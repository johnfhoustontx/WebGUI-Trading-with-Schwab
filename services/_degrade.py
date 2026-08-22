"""Make a swallowed exception audible: log it, and count it for ``/health``.

The recurring bug class in this repo is ``try/except Exception -> return a
plausible default``. A measured example: ``sentiment_svc/compute.py`` wraps 294
lines of trend computation in one guard whose handler returns ``_neutral_trend()``
- so ANY bug inside it renders as a confident neutral reading, with nothing in
``logs/sentiment.log`` to say a degrade happened. The same shape sat over the
five NaN incidents.

Those outer guards are load-bearing (they keep a refresh path alive when one
symbol's data is missing), so the fix is not to delete them. It is to stop them
being SILENT:

    except Exception:
        _degrade.degraded("sentiment.intraday_trend")
        return _neutral_trend()

Two deliberate choices:

* **WARNING, not ERROR.** Most of these fire on real, expected conditions (a
  symbol with no chain off-hours). ERROR should mean "a human should look now".
  The traceback rides along via ``exc_info`` so the line is still diagnosable.
* **The counter is the signal; the log line is the detail.** One degrade is
  noise, 340 in a session is a bug. ``/health`` carries ``degrades`` so the
  Status page can show the count without anyone grepping a log.

Scope note: this is for guards that swallow a whole COMPUTATION. The ~250
one-statement parse guards (``try: return float(x) except: return None``) are
deliberately left alone - a WARNING per row per tick is spam, not observability,
and the missing-value contract there is the point rather than a failure.

No heavy imports on purpose: ``compute`` modules import this, and it must not
drag in FastAPI or the Bus.
"""
import logging
import threading

log = logging.getLogger("services.degrade")

_lock = threading.Lock()
_counts: dict[str, int] = {}


def degraded(area, *, detail=None, exc_info: bool = True) -> None:
    """Record + log one degraded path. NEVER raises.

    ``area`` is a dotted label like ``"sentiment.intraday_trend"``, used both as
    the counter key and in the log line. ``detail`` appends context (a symbol, a
    key) to the message. Call it from INSIDE the ``except`` block so ``exc_info``
    picks up the live exception.
    """
    try:
        key = str(area)
        with _lock:
            _counts[key] = _counts.get(key, 0) + 1
        msg = f"degraded: {key}" + (f" ({detail})" if detail else "")
        log.warning(msg, exc_info=exc_info)
    except Exception:       # pragma: no cover - telemetry must never break a path
        pass


def counts() -> dict[str, int]:
    """Per-area degrade counts since process start (a copy)."""
    with _lock:
        return dict(_counts)


def total() -> int:
    with _lock:
        return sum(_counts.values())


def reset() -> None:
    """Test hook."""
    with _lock:
        _counts.clear()
