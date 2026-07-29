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

# An option contract is 100 shares. The scanner stores ``max_loss`` PER-SHARE
# (e.g. 7.05 for a $SPX spread = width - credit), but the driver's risk caps
# (``per_trade_max_risk`` / ``daily_risk_budget``) are DOLLARS. Affordability MUST
# be evaluated in per-CONTRACT dollars (``max_loss * 100``) or a $705 position is
# mis-counted as $7 and the guardrail under-enforces its own dollar cap by 100x —
# the bug that let $SPX/MU past the driver only to be rejected ``RISK_TOO_HIGH`` by
# the paper account's per-contract sizer (which correctly used ``(width-credit)*100``).
CONTRACT_MULTIPLIER = 100


def normalize_structure(s) -> str:
    """Canonicalize a structure label to ``PCS`` / ``CCS`` / ``IC`` (else upper).

    Falsy / ``None`` → ``""``. An unrecognized non-empty value is upper-cased and
    returned as-is so callers can decide (it simply won't be in ``ALLOWED``).
    """
    if not s:
        return ""
    key = str(s).strip().lower()
    return _STRUCT_MAP.get(key, str(s).strip().upper())


def signal_structure(signal) -> str:
    """Pull the structure code from a signal, tolerating either key family.

    Reads ``structure`` (the projected menu item) first, then ``type`` (the raw
    scanner signal's structure field — a real ``cache:options:scan`` signal stores
    ``"PCS"``/``"CCS"``/``"IC"`` there), then ``trade_type`` (legacy/fallback), and
    normalizes whichever is present. Public so ``compute.build_packet`` can reuse
    the SAME structure resolution for the model-facing menu projection — the raw
    scanner signal has no ``structure`` key, and ``trade_type`` is the DTE bucket
    (``"0-DTE"``/``"SWING"``), not the structure, so reading the wrong key would
    mislabel every real signal.
    """
    raw = signal.get("structure") or signal.get("type") or signal.get("trade_type")
    return normalize_structure(raw)


# Backward-compatible private alias (the function was promoted to public).
_signal_structure = signal_structure


def _max_loss(signal) -> float | None:
    """The signal's max loss as a float, or ``None`` if missing/unparseable.

    Never raises: a ``None``, non-numeric, or **non-finite** (``NaN`` / ``inf``)
    ``max_loss`` returns ``None``, which the callers treat as "no defined risk →
    reject". The finite check matters because the scanner derives ``max_loss`` by
    rounding option marks (``round(nan, 2)`` is still ``NaN``), and ``NaN`` would
    otherwise slip the ``> 0`` allowlist gate and crash ``math.floor`` downstream.
    """
    ml = signal.get("max_loss")
    try:
        v = float(ml) if ml is not None else None
    except (TypeError, ValueError):
        return None
    return v if (v is not None and math.isfinite(v)) else None


def _max_loss_dollars(signal) -> float | None:
    """The signal's max loss in PER-CONTRACT DOLLARS (per-share ``max_loss`` * 100).

    ``None`` when the per-share max loss is missing/unparseable/non-finite. This is
    the risk unit the dollar caps must be compared against — see ``CONTRACT_MULTIPLIER``.
    """
    ml = _max_loss(signal)
    return ml * CONTRACT_MULTIPLIER if ml is not None else None


# Rejection reason for the directional gate (2026-07-09) — a defined-risk spread whose
# directional side is wrong for the current market posture (a CCS in an up tape / a PCS in
# a down tape). Surfaced on the /driver decision log like the other reject reasons.
WRONG_SIDE_REGIME = "wrong-side for the current regime (directional gate)"

# Rejection reason for the one-position-per-symbol rule (2026-07-16) — the driver may
# hold at most ONE open trade per underlying, so a proposed trade on a symbol that already
# has an open position (or was already taken earlier in THIS cycle) is rejected. Caps the
# per-name concentration that drove the $SPX-heavy losing book.
SYMBOL_ALREADY_OPEN = "symbol already has an open position (one trade per symbol)"


def _side_blocked(signal, posture) -> bool:
    """True iff this defined-risk spread's directional side is wrong for ``posture``.

    A CCS (short calls) is hurt by an UP tape; a PCS (short puts) by a DOWN tape; an IC is
    neutral and never blocked. Any ``posture`` other than ``"up"``/``"down"`` (incl. the
    default ``"neutral"``) blocks nothing — so the gate is inert unless a decisive posture
    is supplied. Pure; never raises (``signal_structure`` tolerates sparse signals).
    """
    struct = signal_structure(signal)
    if posture == "up" and struct == "CCS":
        return True
    if posture == "down" and struct == "PCS":
        return True
    return False


def shadow_gate(executable, posture) -> dict:
    """Evaluate the directional gate in LOG-ONLY mode over the trades that fired.

    Returns ``{"posture", "would_block": [{id, symbol, structure}], "n": int}`` — the
    subset of ``executable`` (each ``{id, signal, qty, ...}``) that a LIVE directional
    gate WOULD have rejected at ``posture`` (a CCS in an up tape / a PCS in a down tape).
    Nothing is actually blocked; this is the evidence-gathering shadow that runs even
    while ``settings.DIRECTIONAL_GATE_ENABLED`` is False, so every live trading day
    accrues real would-have-blocked data before the gate is switched on.

    When the gate is already live (posture passed to ``apply_guardrails``) the wrong-side
    trades are in ``rejected`` rather than ``executable``, so ``would_block`` is naturally
    empty — the shadow only surfaces trades the *inert* gate let through. Pure; never
    raises (``_side_blocked``/``signal_structure`` tolerate sparse rows).
    """
    would = []
    for t in executable or []:
        sig = (t or {}).get("signal") if isinstance(t, dict) else None
        if isinstance(sig, dict) and _side_blocked(sig, posture):
            would.append({"id": t.get("id"), "symbol": sig.get("symbol"),
                          "structure": signal_structure(sig)})
    return {"posture": posture, "would_block": would, "n": len(would)}


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

    ``max_loss`` is converted to PER-CONTRACT DOLLARS (``_max_loss_dollars``) before
    every comparison, because the caps are dollars and one spread risks
    ``max_loss * 100`` (see ``CONTRACT_MULTIPLIER``).
    """
    ml = _max_loss_dollars(signal)
    if not ml or ml <= 0:
        return 0
    try:
        # ``ml`` is finite (``_max_loss`` guarantees it); guard the budget args so
        # a NaN/inf/non-numeric cap can't crash the floor division either.
        if not (math.isfinite(float(per_trade_max_risk))
                and math.isfinite(float(remaining_budget))):
            return 0
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


def apply_guardrails(decision, menu_by_id, limits, *, open_count, day_pnl, vix=None,
                     daily_max_loss=250.0, posture="neutral", open_symbols=frozenset()):
    """Turn a model decision into an executable, risk-clamped trade list.

    This is the single authority over what the autonomous driver executes. The
    model's ``decision`` (a parsed ``{stand_down, trades:[{id, quantity, ...}]}``)
    is run through, in order:

    1. **Halt check** — ``halt_state``. If halted (banked / loss cap / VIX),
       NOTHING executes and the reason is returned.
    2. **Stand-down** — if the model chose to stand down, nothing executes.
    3. **Per-trade resolution**, tracking the remaining risk budget and the
       remaining concurrent slots ACROSS trades, in order:
       * the menu id must resolve to a known signal (else reject "off-menu"),
       * the signal must be on the allowlist with defined risk (``is_allowed``),
       * the per-cycle cap and the concurrent-slot budget must not be exhausted,
       * ``clamp_quantity`` must yield ``>= 1`` under the *remaining* budget.
       Each survivor consumes one slot and ``qty * max_loss`` of the budget.

    ``menu_by_id`` maps the packet menu id → the FULL scanner signal dict (so the
    resolved ``signal`` can be enqueued verbatim for paper execution). Returns
    ``{executable: [{id, signal, qty, rationale}], rejected: [{id, reason}],
    halted: bool, halt_reason: str|None}``. CODE is authoritative — the model's
    quantities are ceilings, not commands. Never raises on a well-formed decision.
    """
    halted, halt_reason = halt_state(day_pnl, limits["daily_target"],
                                     daily_max_loss, vix, limits["vix_max"])
    if halted:
        return {"executable": [], "rejected": [], "halted": True, "halt_reason": halt_reason}
    if decision.get("stand_down"):
        return {"executable": [], "rejected": [], "halted": False, "halt_reason": None}

    executable, rejected = [], []
    remaining = float(limits["daily_risk_budget"])
    slots = max(0, limits["max_concurrent"] - int(open_count))
    per_cycle = limits["max_trades_per_cycle"]
    # One position per symbol: seed with the symbols already open in the book, then add
    # each symbol taken THIS cycle — so neither the account nor a same-cycle duplicate can
    # stack a second trade on the same underlying.
    taken_symbols = set(open_symbols or ())

    for t in decision.get("trades", []):
        mid = t.get("id")
        sig = menu_by_id.get(mid)
        if sig is None:
            rejected.append({"id": mid, "reason": "off-menu (no matching signal)"})
            continue
        if not is_allowed(sig):
            rejected.append({"id": mid, "reason": "structure not in allowlist / no defined risk"})
            continue
        # Directional gate — before the capacity check so a wrong-side block does NOT
        # consume a concurrent slot/budget (a following right-side trade can still run).
        # ``posture`` defaults to "neutral" (inert); run_cycle supplies a decisive posture
        # only when settings.DIRECTIONAL_GATE_ENABLED.
        if _side_blocked(sig, posture):
            rejected.append({"id": mid, "reason": WRONG_SIDE_REGIME})
            continue
        # One trade per symbol — before the capacity check so a duplicate-symbol block
        # does NOT consume a concurrent slot (a following new-symbol trade can still run).
        sym = sig.get("symbol")
        if sym and sym in taken_symbols:
            rejected.append({"id": mid, "reason": SYMBOL_ALREADY_OPEN})
            continue
        if len(executable) >= per_cycle or slots <= 0:
            rejected.append({"id": mid, "reason": "max trades/concurrent reached"})
            continue
        qty = clamp_quantity(sig, t.get("quantity", 1), limits["per_trade_max_risk"], remaining)
        if qty <= 0:
            rejected.append({"id": mid, "reason": "unaffordable within remaining budget"})
            continue
        executable.append({"id": mid, "signal": sig, "qty": qty,
                           "rationale": t.get("rationale", "")})
        if sym:
            taken_symbols.add(sym)
        remaining -= qty * _max_loss_dollars(sig)   # dollars — keep the budget in per-contract $
        slots -= 1

    return {"executable": executable, "rejected": rejected, "halted": False, "halt_reason": None}
