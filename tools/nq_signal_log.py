"""
nq_signal_log.py - Dealer-positioning HUD verdict-transition log
Version: 2.0.0
Last Updated: 2026-07-30

Append-only CSV of the HUD's verdict TRANSITIONS — the validation substrate for
the questions the design leaves open. Nothing reads this at runtime; it exists
so decisions can be made on measured data instead of argument.

Version 2.0.0 Changes:
- Multi-instrument. Each row carries an ``instrument`` column and both NQ and ES
  append to the SAME file, because the interesting offline question is how the
  two regimes RELATE — whether a wall break on one leads the other, whether they
  ever disagree about the gamma sign — and that is a single-file query.
- Price columns renamed from NQ-specific (nq_spot / ndx / flip_nq) to generic
  (fut_spot / cash_spot / flip_fut). The file therefore MOVED to
  ``dealer_signals.csv``: appending a differently-shaped row to the old
  ``nq_signals.csv`` would leave one file carrying two incompatible headers,
  which no CSV reader handles. The old file is left in place, intact.

WHY CSV, NOT SQLITE: single writer, append-only, never queried in a request
path. The consumer is an offline pandas/Excel pass over 20-30 sessions. CSV
needs no schema, no migration, no connection handling, and can be eyeballed
mid-session — and this module must never be able to break the HUD, so the
simplest possible write path is the right one.

WHY TRANSITIONS, NOT POLLS: the HUD polls every 2s. Logging every poll would
produce ~12k near-identical rows per session per instrument and bury the handful
of moments that matter. A row is written when that instrument's (regime, action)
CHANGES — which is exactly when a decision would have been made. Each instrument
tracks its own previous state, so one pane's transition never suppresses the
other's.

TWO PIN CANDIDATES ARE RECORDED, deliberately. Design §6 leaves open whether the
mean-reversion target should be max(|net|) (what the HUD uses) or the stored
top_pos_strike (= max(net) over POSITIVE strikes only, and free — no grid
decode). Pinning is caused by POSITIVE dealer gamma, so a large negative-net
strike is an amplifier rather than an attractor, which argues for
top_pos_strike. Both are logged so the question can be settled by seeing which
one price actually gravitates to. ``flip_stored_fut`` is there for the same
reason — the other flip definition, recorded beside the one in use.

The log lives under options-scanner/data/, which is gitignored — session data
never lands in the repo.
"""

from __future__ import annotations

import csv
import logging
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import OPTIONS_SCANNER  # noqa: E402

log = logging.getLogger("nq_hud")

# Derived from repo_paths rather than added AS a repo_paths constant: the
# constants there are for stores several modules share, whereas this file has
# exactly one writer and one ad-hoc offline reader.
LOG_PATH = OPTIONS_SCANNER / "data" / "dealer_signals.csv"

# Column order is the file format — APPEND new fields at the end so an existing
# log stays readable. A RENAME or a reorder needs a new filename (see the module
# docstring).
FIELDS = [
    "ts_ct",            # local (Central) timestamp of the transition
    "instrument",       # NQ / ES — which pane this row describes
    "session_date",     # the gamma session the levels came from
    "source_symbol",    # $NDX / $SPX, or the ETF proxy when degraded
    "phase",            # session phase (morning/pin/afternoon/...)
    "regime",           # positive / negative / flip_zone / unknown
    "action",           # LONG / SHORT / WAIT / STAND DOWN
    "dist_pts",         # cash spot minus the flip (frame-independent)
    "fut_spot",         # the futures contract's last
    "fut_day_pct",
    "cash_spot",        # the cash index behind it
    "vix",
    "basis",            # future - cash, measured
    "scale",            # source -> cash-index multiplier (1.0 for a $ index)
    "flip_fut",         # the levels below are in FUTURES points
    "call_wall_fut",
    "put_wall_fut",
    "pin_fut",          # max(|net|)      <- the HUD's choice
    "pin_top_pos_fut",  # top_pos_strike  <- the free alternative
    "flip_stored_fut",  # the nearest-to-spot flip rule, for comparison
    "entry",
    "stop",
    "target",
    # Reward:risk, recorded on REFUSED setups as well as taken ones — the whole
    # question the gate raises is whether 1.5 is the right cut, and that can
    # only be answered from the rows it turned away.
    "rr",
    "atr_pts",          # session spot range, in futures points
    "snap_age_s",       # collector staleness at decision time
]


def transition_key(regime, action):
    """The identity of a HUD state for change-detection purposes.

    (regime, action) rather than action alone: a regime flip that leaves the
    action unchanged (WAIT under positive gamma -> WAIT under negative) is a
    real change of the world and is exactly what the regime-accuracy question
    needs. Keying on action alone would drop it.
    """
    return (regime, action)


def should_log(prev_key, new_key) -> bool:
    """True when the state changed. A first observation (prev_key None) counts —
    it is the transition from "nothing known" and carries the session's opening
    read.
    """
    return prev_key != new_key


def build_row(state, key) -> dict:
    """Flatten ONE pane of a HUD state into a CSV row. PURE — no I/O, no clock.

    ``state`` is what Hud._collect() returns; ``key`` selects the instrument
    ("nq"/"es"). Every lookup is defensive: a partially-degraded state (no tape,
    no gamma) must still produce a row, because the fact that the HUD was blind
    at that moment is itself the datum worth having.
    """
    pane = (state.get("panes") or {}).get(key) or {}
    spec = pane.get("spec")
    tape = pane.get("tape") or {}
    gamma = pane.get("gamma") or {}
    lv = pane.get("levels") or {}
    v = pane.get("verdict") or {}
    now = state.get("now")

    session = gamma.get("session_date")
    return {
        "ts_ct": now.strftime("%Y-%m-%d %H:%M:%S") if now is not None else "",
        # From the pane's own spec, so the label and the numbers beside it can
        # never describe different instruments.
        "instrument": getattr(spec, "label", "") or str(key).upper(),
        "session_date": (session.isoformat()
                         if hasattr(session, "isoformat") else (session or "")),
        "source_symbol": gamma.get("symbol") or "",
        "phase": state.get("phase") or "",
        "regime": pane.get("regime") or "",
        "action": v.get("action") or "",
        "dist_pts": _r(pane.get("dist"), 1),
        "fut_spot": _r(tape.get("fut"), 2),
        "fut_day_pct": _r(tape.get("fut_pct"), 3),
        "cash_spot": _r(tape.get("cash"), 2),
        # VIX is shared, so it lives at the top of the state rather than on the
        # pane; recorded on every row anyway so each row stands alone.
        "vix": _r((state.get("tape") or {}).get("vix"), 2),
        "basis": _r(pane.get("basis"), 2),
        "scale": _r(pane.get("scale"), 6),
        "flip_fut": _r(lv.get("flip"), 1),
        "call_wall_fut": _r(lv.get("call_wall"), 1),
        "put_wall_fut": _r(lv.get("put_wall"), 1),
        "pin_fut": _r(lv.get("pin"), 1),
        "pin_top_pos_fut": _r(lv.get("pin_top_pos"), 1),
        "flip_stored_fut": _r(lv.get("flip_stored"), 1),
        "entry": _r(v.get("entry"), 1),
        "stop": _r(v.get("stop"), 1),
        "target": _r(v.get("target"), 1),
        "rr": _r(v.get("rr"), 3),
        "atr_pts": _r(pane.get("atr_pts"), 1),
        "snap_age_s": _r(gamma.get("snap_age_s"), 0),
    }


def _r(value, places):
    """Round for a readable log; None -> "" so the CSV cell is genuinely empty
    rather than the string "None"."""
    if value is None:
        return ""
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return ""


def append_row(row, path=None) -> bool:
    """Append one row, writing the header if the file is new. Never raises.

    Returns True on a successful write, False otherwise — the caller treats a
    logging failure as nothing more than a missing row.
    """
    path = pathlib.Path(path) if path is not None else LOG_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            if new_file:
                writer.writeheader()
            writer.writerow(row)
        return True
    except Exception:
        log.debug("signal log append failed", exc_info=True)
        return False


class SignalLogger:
    """Stateful transition detector wrapping the pure helpers.

    ONE INSTANCE PER INSTRUMENT. Instances share a path — they append to the
    same file — but each keeps its own ``_prev_key``, so NQ flipping regime
    does not consume ES's transition.

    ``maybe_log(state, key)`` is safe to call on every poll; it writes only on a
    change.
    """

    def __init__(self, path=None, instrument=None):
        self.path = path
        # Carried for call-site readability and logging context only; the row's
        # instrument value comes from the pane's own spec, so a mislabelled
        # logger cannot produce a mislabelled row.
        self.instrument = instrument
        self._prev_key = None

    def maybe_log(self, state, key) -> bool:
        """Log ``state``'s ``key`` pane if it is a transition. True if written.

        Guarded end to end: this sits in the HUD's 2s poll loop and must never
        be able to take it down.
        """
        try:
            pane = (state.get("panes") or {}).get(key) or {}
            new_key = transition_key(pane.get("regime"),
                                     (pane.get("verdict") or {}).get("action"))
            if not should_log(self._prev_key, new_key):
                return False
            # Advance BEFORE writing: a failing write must not leave the logger
            # retrying the same transition on every subsequent poll.
            self._prev_key = new_key
            return append_row(build_row(state, key), self.path)
        except Exception:
            log.debug("signal log failed", exc_info=True)
            return False
