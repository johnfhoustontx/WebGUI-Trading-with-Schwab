"""
nq_state.py - NQ HUD current-state export for NinjaTrader
Version: 1.0.0
Last Updated: 2026-07-30

Writes the HUD's CURRENT state to a small JSON file so a NinjaTrader 8 indicator
can render the same read beside a live chart. Write-only; nothing in the HUD ever
reads it back.

Distinct from nq_signal_log.py, which appends one row per verdict TRANSITION for
offline validation. This is the opposite shape: one file, overwritten every poll,
always describing now.

TWO DESIGN CHOICES THE CONSUMER FORCES:

1. THE DOCUMENT IS FLAT. NinjaScript has no bundled JSON parser, and the house
   convention (StrategyLibrary/ML/ModelConfigLoader.cs) is regex extraction by
   key. Nested objects would make "flip" ambiguous between the cash and NQ
   blocks, so every key is unique and top-level: cash_flip, nq_flip, and so on.

2. BOTH FRAMES ARE EXPORTED, plus cash_spot. The nq_* values are computed against
   whatever contract market_svc quotes (NQ_CONTRACT). A chart on a different
   expiry — or on a back-adjusted continuous contract — would place them at the
   wrong prices. Shipping cash_spot lets the indicator recompute its own basis
   from its own Close[0], which is correct for ANY contract:

       level_on_chart = cash_level + (Close[0] - cash_spot)

Writes are ATOMIC (temp file + os.replace) because the reader polls on its own
timer and must never observe a half-written document.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import OPTIONS_SCANNER  # noqa: E402

log = logging.getLogger("nq_hud")

# Sits beside the transition log, under the gitignored data dir.
STATE_PATH = OPTIONS_SCANNER / "data" / "nq_state.json"

# Bump when a key is REMOVED or its meaning changes; the indicator refuses a
# schema it does not know rather than silently misreading it. Adding keys is
# backward-compatible and does NOT need a bump.
SCHEMA_VERSION = 1


def build_state(state, *, nq_contract, stale_after_sec):
    """Flatten one HUD state dict into the export payload. PURE — no I/O.

    ``state`` is what NQHud._collect() returns. Every lookup is defensive: a
    degraded state still yields a well-formed document, because the indicator
    needs to distinguish "the HUD says it cannot tell" from "the HUD is gone",
    and only a written file can express the first.
    """
    tape = state.get("tape") or {}
    gamma = state.get("gamma") or {}
    cash = state.get("levels_cash") or {}
    nq = state.get("levels") or {}
    verdict = state.get("verdict") or {}
    cash_verdict = state.get("verdict_cash") or {}
    now = state.get("now")
    snap_age = gamma.get("snap_age_s")

    session = gamma.get("session_date")
    out = {
        "schema": SCHEMA_VERSION,
        "ts": now.isoformat() if now is not None else None,
        "ts_epoch": now.timestamp() if now is not None else None,
        "source_symbol": gamma.get("symbol"),
        "session_date": (session.isoformat()
                         if hasattr(session, "isoformat") else session),
        "phase": state.get("phase"),
        "regime": state.get("regime"),
        # Present when the HUD is deliberately withholding the regime; the
        # indicator greys its readout rather than drawing confident levels.
        "regime_stale": state.get("regime_stale"),
        "action": verdict.get("action"),
        "reason": verdict.get("reason"),
        "dist_pts": _num(state.get("dist")),
        # --- cash frame: what decisions are made in, and what the indicator
        # --- should rebase onto its own contract.
        "cash_spot": _num(tape.get("ndx")),
        "cash_flip": _num(cash.get("flip")),
        "cash_call_wall": _num(cash.get("call_wall")),
        "cash_put_wall": _num(cash.get("put_wall")),
        "cash_pin": _num(cash.get("pin")),
        # --- NQ frame: correct only for nq_contract.
        "nq_contract": nq_contract,
        "nq_spot": _num(tape.get("nq")),
        "nq_day_pct": _num(tape.get("nq_pct")),
        "nq_flip": _num(nq.get("flip")),
        "nq_call_wall": _num(nq.get("call_wall")),
        "nq_put_wall": _num(nq.get("put_wall")),
        "nq_pin": _num(nq.get("pin")),
        "basis": _num(state.get("basis")),
        # --- risk in the NQ frame (what the HUD panel shows).
        "entry": _num(verdict.get("entry")),
        "stop": _num(verdict.get("stop")),
        "target": _num(verdict.get("target")),
        # --- risk in the CASH frame, so a consumer that rebases the LEVELS
        # --- onto its own contract can rebase these identically. Without them
        # --- a back-adjusted continuous chart would show levels in one frame
        # --- and entry/stop/target in another, differing by the adjustment.
        "cash_entry": _num(cash_verdict.get("entry")),
        "cash_stop": _num(cash_verdict.get("stop")),
        "cash_target": _num(cash_verdict.get("target")),
        "atr_pts": _num(state.get("atr_nq")),
        # --- health.
        "vix": _num(tape.get("vix")),
        "tape_ok": bool(tape.get("ok")),
        "snap_age_s": _num(snap_age, 0),
        "snapshot_stale": bool(snap_age is not None and snap_age > stale_after_sec),
    }
    return out


def _num(value, places=4):
    """Round for a compact document; None stays None (JSON null), never a string.

    The indicator treats null as "unknown" and paints an em dash, so a numeric
    field must never arrive as the text "None".
    """
    if value is None:
        return None
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return None


def write_state(payload, path=None) -> bool:
    """Write the payload atomically. Never raises; returns True on success.

    Temp file + os.replace, because the NinjaTrader indicator polls on its own
    timer with no coordination — a partial document would surface as a parse
    failure, or worse as a plausible-looking wrong level.
    """
    path = pathlib.Path(path) if path is not None else STATE_PATH
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, allow_nan=False)
            fh.write("\n")
        os.replace(tmp, path)
        return True
    except Exception:
        log.debug("state write failed", exc_info=True)
        try:
            tmp.unlink()
        except Exception:
            pass
        return False


class StateWriter:
    """Writes the state file on every poll. One instance per HUD.

    Every poll rather than on-change, deliberately: the timestamp IS the
    heartbeat. An indicator that sees ts stop advancing knows the HUD died,
    which it could not infer from a file that only changes when the verdict does.
    """

    def __init__(self, path=None, *, nq_contract, stale_after_sec):
        self.path = path
        self.nq_contract = nq_contract
        self.stale_after_sec = stale_after_sec

    def write(self, state) -> bool:
        """Export ``state``. Guarded end to end — this sits in the 2s poll loop
        and must never be able to take the HUD down."""
        try:
            payload = build_state(state, nq_contract=self.nq_contract,
                                  stale_after_sec=self.stale_after_sec)
            return write_state(payload, self.path)
        except Exception:
            log.debug("state export failed", exc_info=True)
            return False
