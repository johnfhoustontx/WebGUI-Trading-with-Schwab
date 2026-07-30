"""
nq_state.py - Dealer-positioning HUD current-state export for NinjaTrader
Version: 2.0.0
Last Updated: 2026-07-30

Writes the HUD's CURRENT state to a small JSON file so a NinjaTrader 8 indicator
can render the same read beside a live chart. Write-only; nothing in the HUD
ever reads it back.

Version 2.0.0 Changes (SCHEMA 2 — the indicator refuses schema 1):
- Both instruments in ONE document, each under its own key prefix
  (``nq_cash_flip`` / ``es_cash_flip``). One atomic write means the indicator
  can never paint a half-updated pair, which two separate files could not
  guarantee.
- The futures-frame prefix changed from ``nq_`` to ``{key}_fut_``. Under schema 1
  ``nq_flip`` meant "the flip in NQ futures points"; with two instruments that
  reading collides with "the NQ instrument's flip", so the frame is now named
  explicitly.

Distinct from nq_signal_log.py, which appends one row per verdict TRANSITION for
offline validation. This is the opposite shape: one file, overwritten every
poll, always describing now.

TWO DESIGN CHOICES THE CONSUMER FORCES:

1. THE DOCUMENT IS FLAT. NinjaScript has no bundled JSON parser, and the house
   convention (StrategyLibrary/ML/ModelConfigLoader.cs) is regex extraction by
   key. Nested objects would make "flip" ambiguous between instruments and
   frames, so every key is unique and top-level. The reader's regex anchors on
   the opening quote, so ``"cash_flip"`` does NOT match ``"nq_cash_flip"`` —
   prefixing is safe precisely because of that anchor.

2. BOTH FRAMES ARE EXPORTED, plus cash_spot. The ``*_fut_*`` values are computed
   against whatever contract market_svc quotes (``{key}_contract``). A chart on
   a different expiry — or on a back-adjusted continuous contract — would place
   them at the wrong prices. Shipping cash_spot lets the indicator recompute its
   own basis from its own Close[0], which is correct for ANY contract:

       level_on_chart = cash_level + (Close[0] - cash_spot)

   That only works for the instrument the chart is actually on; the other pane
   is rendered in its own futures frame, which is why both are shipped.

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

# Sits beside the transition log, under the gitignored data dir. The filename is
# unchanged across the schema bump on purpose: the path is configured in the
# NinjaTrader indicator's properties, and moving it would silently orphan every
# existing chart until the user re-pointed it by hand.
STATE_PATH = OPTIONS_SCANNER / "data" / "nq_state.json"

# Bump when a key is REMOVED or its meaning changes; the indicator refuses a
# schema it does not know rather than silently misreading it. Adding keys is
# backward-compatible and does NOT need a bump.
#
# 1 -> 2: NQ-only, un-prefixed keys  ->  per-instrument prefixes.
SCHEMA_VERSION = 2


def build_state(state, *, stale_after_sec):
    """Flatten a multi-pane HUD state into the export payload. PURE — no I/O.

    ``state`` is what Hud._collect() returns. Every lookup is defensive: a
    degraded state still yields a well-formed document, because the indicator
    needs to distinguish "the HUD says it cannot tell" from "the HUD is gone",
    and only a written file can express the first.
    """
    now = state.get("now")
    panes = state.get("panes") or {}
    tape = state.get("tape") or {}

    out = {
        "schema": SCHEMA_VERSION,
        "ts": now.isoformat() if now is not None else None,
        "ts_epoch": now.timestamp() if now is not None else None,
        "phase": state.get("phase"),
        # Comma-separated key list, so the indicator can discover which prefixes
        # are present rather than hardcoding them — adding a third instrument
        # then needs no reader change.
        "instruments": ",".join(panes.keys()),
        "vix": _num(tape.get("vix")),
        "tape_ok": bool(tape.get("ok")),
    }
    for key, pane in panes.items():
        out.update(_pane_keys(key, pane, stale_after_sec))
    return out


def _pane_keys(key, pane, stale_after_sec):
    """One instrument's block of flat, prefixed keys."""
    spec = pane.get("spec")
    tape = pane.get("tape") or {}
    gamma = pane.get("gamma") or {}
    cash = pane.get("levels_cash") or {}
    fut = pane.get("levels") or {}
    verdict = pane.get("verdict") or {}
    cash_verdict = pane.get("verdict_cash") or {}
    snap_age = gamma.get("snap_age_s")
    session = gamma.get("session_date")

    p = key + "_"
    return {
        p + "label": getattr(spec, "label", None) or str(key).upper(),
        p + "contract": getattr(spec, "contract", None),
        p + "source_symbol": gamma.get("symbol"),
        p + "session_date": (session.isoformat()
                             if hasattr(session, "isoformat") else session),
        p + "regime": pane.get("regime"),
        # Present when the HUD is deliberately withholding the regime; the
        # indicator greys that pane rather than drawing confident levels.
        p + "regime_stale": pane.get("regime_stale"),
        p + "action": verdict.get("action"),
        p + "reason": verdict.get("reason"),
        p + "dist_pts": _num(pane.get("dist")),
        # --- cash frame: what decisions are made in, and what the indicator
        # --- rebases onto its own contract.
        p + "cash_spot": _num(tape.get("cash")),
        p + "cash_flip": _num(cash.get("flip")),
        p + "cash_call_wall": _num(cash.get("call_wall")),
        p + "cash_put_wall": _num(cash.get("put_wall")),
        p + "cash_pin": _num(cash.get("pin")),
        # The OTHER flip rule (snapshot_summary's nearest-to-spot column).
        # Exported beside the one actually used so the engine-wide flip
        # question can be settled on data rather than argument.
        p + "cash_flip_stored": _num(cash.get("flip_stored")),
        # --- futures frame: correct only for {key}_contract.
        p + "fut_spot": _num(tape.get("fut")),
        p + "fut_day_pct": _num(tape.get("fut_pct")),
        p + "fut_flip": _num(fut.get("flip")),
        p + "fut_call_wall": _num(fut.get("call_wall")),
        p + "fut_put_wall": _num(fut.get("put_wall")),
        p + "fut_pin": _num(fut.get("pin")),
        p + "basis": _num(pane.get("basis")),
        # --- risk in the futures frame (what the HUD panel shows).
        p + "entry": _num(verdict.get("entry")),
        p + "stop": _num(verdict.get("stop")),
        p + "target": _num(verdict.get("target")),
        # --- risk in the CASH frame, so a consumer that rebases the LEVELS onto
        # --- its own contract can rebase these identically. Without them a
        # --- back-adjusted continuous chart would show levels in one frame and
        # --- entry/stop/target in another, differing by the adjustment.
        p + "cash_entry": _num(cash_verdict.get("entry")),
        p + "cash_stop": _num(cash_verdict.get("stop")),
        p + "cash_target": _num(cash_verdict.get("target")),
        p + "atr_pts": _num(pane.get("atr_pts")),
        # --- health, per instrument: one collector can stall while the other
        # --- keeps publishing.
        p + "snap_age_s": _num(snap_age, 0),
        p + "snapshot_stale": bool(snap_age is not None
                                   and snap_age > stale_after_sec),
    }


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

    ``ensure_ascii=False`` because the reason strings contain em dashes, and the
    default would escape them to ``\\u2014``. The NinjaScript accessor unescapes
    only ``\\"`` and ``\\\\`` (the house ModelConfigLoader convention), so an
    escaped dash reaches the panel as the literal seven characters. Writing real
    UTF-8 avoids the problem at the source; the file is already opened as UTF-8
    and the C# StreamReader decodes UTF-8 by default.
    """
    path = pathlib.Path(path) if path is not None else STATE_PATH
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, allow_nan=False, ensure_ascii=False)
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

    def __init__(self, path=None, *, stale_after_sec):
        self.path = path
        self.stale_after_sec = stale_after_sec

    def write(self, state) -> bool:
        """Export ``state``. Guarded end to end — this sits in the 2s poll loop
        and must never be able to take the HUD down."""
        try:
            payload = build_state(state, stale_after_sec=self.stale_after_sec)
            return write_state(payload, self.path)
        except Exception:
            log.debug("state export failed", exc_info=True)
            return False
