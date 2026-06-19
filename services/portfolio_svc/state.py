"""Shared in-memory portfolio state (scheduler ⇄ command-handler).

Unlike the other domains, portfolio keeps a **live model in memory**: the
scheduler's SSE stream thread applies each quote tick to ``raw_model``, and the
publish loop re-formats + re-caches it. Because the scaffold calls the scheduler
and the command handler independently (no shared argument), that state lives here
as a module-level singleton both import.

The ``lock`` guards ``raw_model``/``baselines`` against the stream thread mutating
them while the publish loop reads them. ``dirty`` marks ticks-since-last-publish
(throttled republish); ``rebuild_requested`` is the flag the GUI "Refresh" command
sets for the scheduler to act on (it owns the stream restart).
"""
import threading


class PortfolioState:
    def __init__(self):
        self.raw_model = {"holdings": [], "sectors": []}
        self.baselines = {}
        self.baseline_sig = None        # signature of the inputs baselines were built from
        self.trades = []
        self.proxy_up = False
        self.streaming = False
        self.errors = []
        self.dirty = False              # ticks applied since the last publish
        self.rebuild_requested = False  # set by the "refresh" command
        self.lock = threading.Lock()


# Process-wide singleton (single-user app).
STATE = PortfolioState()
