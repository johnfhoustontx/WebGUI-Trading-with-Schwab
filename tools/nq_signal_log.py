"""
nq_signal_log.py - NQ HUD verdict-transition log
Version: 1.0.0
Last Updated: 2026-07-29

Append-only CSV of the NQ HUD's verdict TRANSITIONS — the validation substrate
for the questions the design leaves open. Nothing reads this at runtime; it
exists so decisions can be made on measured data instead of argument.

WHY CSV, NOT SQLITE: single writer, append-only, never queried in a request
path. The consumer is an offline pandas/Excel pass over 20-30 sessions. CSV
needs no schema, no migration, no connection handling, and can be eyeballed
mid-session — and this module must never be able to break the HUD, so the
simplest possible write path is the right one.

WHY TRANSITIONS, NOT POLLS: the HUD polls every 2s. Logging every poll would
produce ~12k near-identical rows per session and bury the handful of moments
that matter. A row is written when (regime, action) CHANGES — which is exactly
when a decision would have been made.

TWO PIN CANDIDATES ARE RECORDED, deliberately. Design §6 leaves open whether
the mean-reversion target should be max(|net|) (what the HUD uses) or the
stored top_pos_strike (= max(net) over POSITIVE strikes only, and free — no
grid decode). Pinning is caused by POSITIVE dealer gamma, so a large
negative-net strike is an amplifier rather than an attractor, which argues for
top_pos_strike. Both are logged in NQ points so the question can be settled by
seeing which one price actually gravitates to.

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
LOG_PATH = OPTIONS_SCANNER / "data" / "nq_signals.csv"

# Column order is the file format — APPEND new fields at the end so an existing
# log stays readable.
FIELDS = [
    "ts_ct",            # local (Central) timestamp of the transition
    "session_date",     # the gamma session the levels came from
    "source_symbol",    # $NDX, or QQQ when degraded
    "phase",            # session phase (morning/pin/afternoon/...)
    "regime",           # positive / negative / flip_zone / unknown
    "action",           # LONG / SHORT / WAIT / STAND DOWN
    "dist_pts",         # NQ spot minus the converted flip
    "nq_spot",
    "nq_day_pct",
    "ndx",
    "vix",
    "basis",            # NQ - NDX, measured
    "scale",            # source -> NDX multiplier (1.0 for $NDX)
    "flip_nq",
    "call_wall_nq",
    "put_wall_nq",
    "pin_nq",           # max(|net|)      <- the HUD's choice
    "pin_top_pos_nq",   # top_pos_strike  <- the free alternative
    "entry",
    "stop",
    "target",
    "atr_nq",           # session spot range, in NQ points
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


def build_row(state) -> dict:
    """Flatten one HUD state dict into a CSV row. PURE — no I/O, no clock.

    ``state`` is what NQHud._collect() returns. Every lookup is defensive: a
    partially-degraded state (no tape, no gamma) must still produce a row,
    because the fact that the HUD was blind at that moment is itself the
    datum worth having.
    """
    tape = state.get("tape") or {}
    gamma = state.get("gamma") or {}
    lv = state.get("levels") or {}
    v = state.get("verdict") or {}
    now = state.get("now")

    session = gamma.get("session_date")
    return {
        "ts_ct": now.strftime("%Y-%m-%d %H:%M:%S") if now is not None else "",
        "session_date": session.isoformat() if hasattr(session, "isoformat") else (session or ""),
        "source_symbol": gamma.get("symbol") or "",
        "phase": state.get("phase") or "",
        "regime": state.get("regime") or "",
        "action": v.get("action") or "",
        "dist_pts": _r(state.get("dist"), 1),
        "nq_spot": _r(tape.get("nq"), 2),
        "nq_day_pct": _r(tape.get("nq_pct"), 3),
        "ndx": _r(tape.get("ndx"), 2),
        "vix": _r(tape.get("vix"), 2),
        "basis": _r(state.get("basis"), 2),
        "scale": _r(state.get("scale"), 6),
        "flip_nq": _r(lv.get("flip"), 1),
        "call_wall_nq": _r(lv.get("call_wall"), 1),
        "put_wall_nq": _r(lv.get("put_wall"), 1),
        "pin_nq": _r(lv.get("pin"), 1),
        "pin_top_pos_nq": _r(lv.get("pin_top_pos"), 1),
        "entry": _r(v.get("entry"), 1),
        "stop": _r(v.get("stop"), 1),
        "target": _r(v.get("target"), 1),
        "atr_nq": _r(state.get("atr_nq"), 1),
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

    One instance per HUD. ``maybe_log(state)`` is safe to call on every poll;
    it writes only on a change.
    """

    def __init__(self, path=None):
        self.path = path
        self._prev_key = None

    def maybe_log(self, state) -> bool:
        """Log ``state`` if it is a transition. Returns True if a row was written.

        Guarded end to end: this sits in the HUD's 2s poll loop and must never
        be able to take it down.
        """
        try:
            key = transition_key(state.get("regime"), (state.get("verdict") or {}).get("action"))
            if not should_log(self._prev_key, key):
                return False
            # Advance BEFORE writing: a failing write must not leave the logger
            # retrying the same transition on every subsequent poll.
            self._prev_key = key
            return append_row(build_row(state), self.path)
        except Exception:
            log.debug("signal log failed", exc_info=True)
            return False
