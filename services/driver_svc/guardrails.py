"""Code-authoritative guardrails for the autonomous driver (PURE, no I/O).

Claude *proposes*; THIS module *decides*. The decision layer (``decider.py``) is
free to suggest trades and quantities, but it is never trusted with risk: every
proposed trade is validated against an allowlist, resized to the configured risk
budget, or rejected, and the daily halt conditions (banked target / loss cap /
VIX ceiling) are computed here too. The model's quantities are treated as
*ceilings*, never commands.

Nothing in this module performs I/O, reads the clock, or touches the network — it
is a pure transform of ``(decision, menu, limits, account-state) -> executable
trades`` so it can be exhaustively unit-tested and reasoned about as the single
safety boundary in front of paper execution.

Structure-field tolerance (important): a real ``cache:options:scan`` signal names
the spread structure in its ``type`` field (``"PCS"`` / ``"CCS"`` / ``"IC"``) and
uses ``trade_type`` for the DTE bucket (``"0-DTE"`` / ``"SWING"``); the model-facing
menu projection (``compute.build_packet``) instead emits a normalized
``structure`` key. ``is_allowed`` therefore reads ``structure`` → ``type`` →
``trade_type`` so the SAME function classifies both the raw scanner signal and the
projected menu item correctly. ``_max_loss`` is tolerant of ``None``/strings so a
sparse or malformed signal can never raise here — it simply fails the allowlist.
"""

import math

# Map of free-form structure spellings → the canonical code. Keys are matched
# case-insensitively (the lookup lowercases first).
_STRUCT_MAP = {
    "put_credit_spread": "PCS", "pcs": "PCS",
    "call_credit_spread": "CCS", "ccs": "CCS",
    "iron_condor": "IC", "ic": "IC",
}

# The ONLY structures the autonomous driver may execute: defined-risk credit
# spreads. Anything else (naked, debit, single-leg, futures, equities) is rejected
# by ``is_allowed`` regardless of what the model proposed.
ALLOWED = {"PCS", "CCS", "IC"}


def normalize_structure(s) -> str:
    """Canonicalize a structure label to ``PCS`` / ``CCS`` / ``IC`` (else upper).

    Falsy / ``None`` → ``""``. An unrecognized non-empty value is upper-cased and
    returned as-is so callers can decide (it simply won't be in ``ALLOWED``).
    """
    if not s:
        return ""
    key = str(s).strip().lower()
    return _STRUCT_MAP.get(key, str(s).strip().upper())


def _signal_structure(signal) -> str:
    """Pull the structure code from a signal, tolerating either key family.

    Reads ``structure`` (the projected menu item) first, then ``type`` (the raw
    scanner signal's structure field), then ``trade_type`` (legacy/fallback), and
    normalizes whichever is present.
    """
    raw = signal.get("structure") or signal.get("type") or signal.get("trade_type")
    return normalize_structure(raw)


def _max_loss(signal) -> float | None:
    """The signal's max loss as a float, or ``None`` if missing/unparseable.

    Never raises: a ``None`` or non-numeric ``max_loss`` (sparse/bad signal)
    returns ``None``, which the callers treat as "no defined risk → reject".
    """
    ml = signal.get("max_loss")
    try:
        return float(ml) if ml is not None else None
    except (TypeError, ValueError):
        return None


def is_allowed(signal) -> bool:
    """True iff ``signal`` is a defined-risk credit spread with real risk.

    Two gates: the structure must be in the allowlist (PCS/CCS/IC), AND the max
    loss must be a positive number (``max_loss <= 0`` or ``None`` means no
    defined risk / no real position, so it is rejected).
    """
    if _signal_structure(signal) not in ALLOWED:
        return False
    ml = _max_loss(signal)
    return ml is not None and ml > 0


def clamp_quantity(signal, requested_qty, per_trade_max_risk, remaining_budget) -> int:
    """Resize a requested quantity down to the risk budget; 0 when unaffordable.

    The model's ``requested_qty`` is a CEILING, not a command. The executed
    quantity is the smallest of three caps, floored to whole spreads:

    * the request itself (``requested_qty``, coerced to a non-negative int),
    * the per-trade cap ``floor(per_trade_max_risk / max_loss)`` (no single
      position may risk more than ``per_trade_max_risk``), and
    * the remaining-budget cap ``floor(remaining_budget / max_loss)`` (the sum
      of open driver max-loss may not exceed the daily risk budget).

    Returns ``0`` — i.e. "do not trade this" — when the signal has no usable
    ``max_loss`` (``None`` / non-numeric / ``<= 0``), when ``requested_qty`` is
    ``None`` / non-numeric / negative, or when even one spread can't fit under
    the per-trade cap or the remaining budget. Never raises.
    """
    ml = _max_loss(signal)
    if not ml or ml <= 0:
        return 0
    try:
        req = max(0, int(requested_qty))
    except (TypeError, ValueError):
        return 0
    per_trade_cap = math.floor(per_trade_max_risk / ml)
    budget_cap = math.floor(remaining_budget / ml)
    return max(0, min(req, per_trade_cap, budget_cap))


def halt_state(day_pnl, target, daily_max_loss, vix, vix_max=25.0):
    """Whether new entries are halted for the day, and a human-readable reason.

    Returns ``(halted, reason)`` — ``reason`` is ``None`` when not halted. The
    three conditions are checked in priority order so the reason reflects the
    dominant cause:

    1. **Banked the target** — ``day_pnl >= target``: the day is done, bank it.
    2. **Daily loss cap** — ``day_pnl <= -abs(daily_max_loss)``: stop the bleed.
    3. **VIX ceiling** — ``vix > vix_max``: no new entries in a vol spike.

    A ``None`` ``day_pnl`` (unknown P&L) skips the P&L gates rather than
    assuming the worst; a ``None`` ``vix`` skips the VIX gate. This only governs
    *new entries* — existing positions are managed by the options service's
    paper auto-manage cycle, not here.
    """
    if day_pnl is not None and day_pnl >= target:
        return (True, f"Target reached: ${day_pnl:.0f} ≥ ${target:.0f} — banked for the day.")
    if day_pnl is not None and day_pnl <= -abs(daily_max_loss):
        return (True, f"Daily loss cap: ${day_pnl:.0f} ≤ -${abs(daily_max_loss):.0f}.")
    if vix is not None and vix > vix_max:
        return (True, f"VIX {vix:.1f} > {vix_max:.0f} — no new entries.")
    return (False, None)
