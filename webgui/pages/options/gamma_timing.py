"""How long Dealer Positioning makes the reader wait, one log line per wait.

Measured on prod 2026-11-15: the page's HTML arrived at 0.41 s and the charts
had data at about 3.2 s, while the Redis reads behind them took about 0.1 s.
The rest (the socket, building the Highcharts figures, sending them, the
browser drawing them) was not measured, so there was no telling which
prefetch would pay. This module measures it, step by step, on every:

- ``open``   the page built and its first charts drawn;
- ``symbol`` a symbol picked, through the options service's refresh, to drawn;
- ``tab``    a subtab clicked, to drawn.

One INFO line per wait on the ``webgui.gamma_timing`` logger, every figure in
milliseconds from the wait's start unless said otherwise::

    gamma open $SPX GEX: built=380 first_read=1240 snap=34 netprem=17 hist=51
        figures=820 bar=41 heat=702 drawn=310 total=2790 nav=3190

``built``      the page built on the server (``open`` only)
``first_read`` the first data read began (after the socket connected)
``published``  the service published the symbol's snapshot (``symbol`` only)
``snap`` / ``netprem`` / ``hist``  time inside each read (read and parse)
``figures``    time inside the page's paints, ``bar`` and ``heat`` within it
``drawn``      from asking the browser to the browser answering after it drew
``total``      start to drawn
``nav``        the browser's own clock: navigation start to drawn (``open`` only)

A figure that did not happen in the wait is left out. ``config/gamma_heat.toml
[timing]`` turns it off and sets how long to wait for the browser. Pure apart
from the clock and the logger, so a test drives it with a fake clock and a fake
browser.
"""
import datetime as _dt
import logging
import time

log = logging.getLogger("webgui.gamma_timing")

# Resolves after the browser has painted the updates sent before it: one
# animation frame, then the task after it. Its value is the browser's own
# milliseconds since navigation began.
DRAWN_JS = ("new Promise(r => requestAnimationFrame("
            "() => setTimeout(() => r(performance.now()))))")

# Shown in this order; anything else after them, in the order it was recorded.
_ORDER = ("built", "first_read", "published", "snap", "netprem", "hist",
          "figures", "bar", "heat", "drawn", "total", "nav")


class Clock:
    """The timings for one page. ``start`` opens a wait; ``report`` logs it
    once the browser says it has drawn. Until the next ``start`` a report does
    nothing, so the page can call it on every repaint."""

    def __init__(self, who, *, enabled=True, wait_sec=3.0,
                 clock=time.perf_counter, wall=time.time):
        # ``who`` gives ``(symbol, view)`` when the line is written: neither
        # is known when the page starts building.
        self._who, self._clock, self._wall = who, clock, wall
        self.enabled, self.wait_sec = bool(enabled), float(wait_sec)
        self.kind = None
        self._parts, self._marks = {}, {}
        self.start("open")

    def _ms(self, since=None):
        return (self._clock() - (self._t0 if since is None else since)) * 1000.0

    def start(self, kind):
        """Open a wait of ``kind``, dropping whatever the last one recorded."""
        self.kind = kind
        self._t0, self._wall0 = self._clock(), self._wall()
        self._parts, self._marks = {}, {}
        self.pending = self.enabled

    def mark(self, name):
        """Note when ``name`` first happened in this wait."""
        if self.pending:
            self._marks.setdefault(name, self._ms())

    def timed(self, name, fn):
        """``fn``, adding the time spent inside it to ``name``. Off, it is
        ``fn`` itself."""
        if not self.enabled:
            return fn

        def run(*args, **kwargs):
            began = self._clock()
            if name in ("snap", "netprem", "hist"):
                self.mark("first_read")
            try:
                return fn(*args, **kwargs)
            finally:
                self._parts[name] = self._parts.get(name, 0.0) + self._ms(began)
        return run

    def _published(self, published, wall0):
        """Milliseconds from ``wall0`` to an ISO time, or None."""
        try:
            stamp = _dt.datetime.fromisoformat(str(published))
        except (TypeError, ValueError):
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=_dt.timezone.utc)
        return (stamp.timestamp() - wall0) * 1000.0

    def _label(self):
        try:
            return self._who()
        except Exception:  # noqa: BLE001 - a label must never stop the line
            return None, None

    async def report(self, run_js, published=None):
        """Ask the browser to say when it has drawn, then log the wait.

        ``run_js`` is ``ui.run_javascript``; ``published`` a callable giving
        the snapshot's publish time (ISO), read only for a symbol wait.
        Everything but the browser's answer is taken when the browser is ASKED:
        a wait started while this one is still being drawn (a tab clicked
        mid-symbol) must not change what this line says."""
        if not self.pending:
            return
        self.pending = False
        kind, t0 = self.kind, self._t0
        found = {**self._marks, **self._parts}
        symbol, view = self._label()
        if kind == "symbol" and published is not None:
            found["published"] = self._published(published(), self._wall0)
        asked = self._clock()
        try:
            nav = await run_js(DRAWN_JS, timeout=self.wait_sec)
            found["drawn"] = (self._clock() - asked) * 1000.0
        except Exception:  # noqa: BLE001 - a hidden or closed tab never answers
            nav = None
        found["total"] = (self._clock() - t0) * 1000.0
        if kind == "open" and isinstance(nav, (int, float)) and not isinstance(nav, bool):
            found["nav"] = float(nav)
        log.info(line(kind, symbol, view, found))


def line(kind, symbol, view, found):
    """The log line: ``found`` in display order, ``None`` values left out."""
    found = {k: v for k, v in found.items() if v is not None}
    names = [k for k in _ORDER if k in found] + [k for k in found if k not in _ORDER]
    body = " ".join(f"{k}={found[k]:.0f}" for k in names)
    return f"gamma {kind} {symbol or '-'} {view or '-'}: {body}"


def for_page(who):
    """The page's clock, set from ``config/gamma_heat.toml [timing]``."""
    from shared import gamma_heat_config
    cfg = gamma_heat_config.timing()
    return Clock(who, enabled=cfg["enabled"], wait_sec=cfg["browser_wait_sec"])
