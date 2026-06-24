"""Shared, PURE strategy/leg model for the Options Simulator + Calculator.

Tier-1: no nicegui, no engine imports. Defines the normalized leg dict, the
strategy template table, default-leg construction (ATM-centered, strike-snapped,
near/far expiries for calendars), and the analytic-vs-numeric summary routing.
Both pages import this so the templates + copy payload never drift.
"""

# Normalized leg dict keys: option_type, side, strike, expiry, qty, premium.

# Each template leg spec: option_type, side, qty, strike_role, expiry_role.
# strike_role drives default strike placement relative to ATM; expiry_role is
# "near" (front) or "far" (back) for calendars/diagonals (else "near").
def _leg(option_type, side, qty, strike_role, expiry_role="near"):
    return {"option_type": option_type, "side": side, "qty": qty,
            "strike_role": strike_role, "expiry_role": expiry_role}


STRATEGY_TEMPLATES = {
    # singles
    "LONG_CALL":  [_leg("call", "long", 1, "atm")],
    "LONG_PUT":   [_leg("put", "long", 1, "atm")],
    "NAKED_CALL": [_leg("call", "short", 1, "otm_up_1")],
    "NAKED_PUT":  [_leg("put", "short", 1, "otm_dn_1")],
    # verticals — credit (aliases PCS/CCS) + debit
    "PCS": [_leg("put", "short", 1, "otm_dn_1"), _leg("put", "long", 1, "otm_dn_2")],
    "CCS": [_leg("call", "short", 1, "otm_up_1"), _leg("call", "long", 1, "otm_up_2")],
    "VERT_PUT_DEBIT":  [_leg("put", "long", 1, "atm"), _leg("put", "short", 1, "otm_dn_1")],
    "VERT_CALL_DEBIT": [_leg("call", "long", 1, "atm"), _leg("call", "short", 1, "otm_up_1")],
    # condors
    "IC": [_leg("put", "short", 1, "otm_dn_1"), _leg("put", "long", 1, "otm_dn_2"),
           _leg("call", "short", 1, "otm_up_1"), _leg("call", "long", 1, "otm_up_2")],
    "CONDOR_CALL": [_leg("call", "long", 1, "atm"), _leg("call", "short", 1, "otm_up_1"),
                    _leg("call", "short", 1, "otm_up_2"), _leg("call", "long", 1, "otm_up_3")],
    "CONDOR_PUT": [_leg("put", "long", 1, "atm"), _leg("put", "short", 1, "otm_dn_1"),
                   _leg("put", "short", 1, "otm_dn_2"), _leg("put", "long", 1, "otm_dn_3")],
    # butterflies (1-2-1)
    "BUTTERFLY_CALL": [_leg("call", "long", 1, "otm_dn_1"), _leg("call", "short", 2, "atm"),
                       _leg("call", "long", 1, "otm_up_1")],
    "BUTTERFLY_PUT": [_leg("put", "long", 1, "otm_up_1"), _leg("put", "short", 2, "atm"),
                      _leg("put", "long", 1, "otm_dn_1")],
    "IRON_BUTTERFLY": [_leg("put", "short", 1, "atm"), _leg("put", "long", 1, "otm_dn_1"),
                       _leg("call", "short", 1, "atm"), _leg("call", "long", 1, "otm_up_1")],
    # calendars / diagonals (per-leg expiry)
    "CALENDAR_CALL": [_leg("call", "short", 1, "atm", "near"), _leg("call", "long", 1, "atm", "far")],
    "CALENDAR_PUT": [_leg("put", "short", 1, "atm", "near"), _leg("put", "long", 1, "atm", "far")],
    "DIAGONAL_CALL": [_leg("call", "short", 1, "otm_up_1", "near"), _leg("call", "long", 1, "atm", "far")],
    "DIAGONAL_PUT": [_leg("put", "short", 1, "otm_dn_1", "near"), _leg("put", "long", 1, "atm", "far")],
}

# Display groups for the UI dropdown (label -> codes).
STRATEGY_GROUPS = [
    ("Singles", ["LONG_CALL", "LONG_PUT", "NAKED_CALL", "NAKED_PUT"]),
    ("Verticals", ["PCS", "CCS", "VERT_CALL_DEBIT", "VERT_PUT_DEBIT"]),
    ("Condors", ["IC", "CONDOR_CALL", "CONDOR_PUT"]),
    ("Butterflies", ["BUTTERFLY_CALL", "BUTTERFLY_PUT", "IRON_BUTTERFLY"]),
    ("Calendars", ["CALENDAR_CALL", "CALENDAR_PUT", "DIAGONAL_CALL", "DIAGONAL_PUT"]),
]

# Cascading Strategy menu: (family label, [(variant label, code), …]). The display
# hierarchy for the Calculator + Simulator strategy picker — a finer split of
# STRATEGY_GROUPS (verticals → credit/debit, calendars → calendar/diagonal). Single
# source of truth for the menu + strategy_label(); covers every STRATEGY_TEMPLATES code.
STRATEGY_MENU = [
    ("Single", [("Long call", "LONG_CALL"), ("Long put", "LONG_PUT"),
                ("Short call", "NAKED_CALL"), ("Short put", "NAKED_PUT")]),
    ("Credit spread", [("Call", "CCS"), ("Put", "PCS")]),
    ("Debit spread", [("Call", "VERT_CALL_DEBIT"), ("Put", "VERT_PUT_DEBIT")]),
    ("Condor", [("Iron", "IC"), ("Call", "CONDOR_CALL"), ("Put", "CONDOR_PUT")]),
    ("Butterfly", [("Call", "BUTTERFLY_CALL"), ("Put", "BUTTERFLY_PUT"),
                   ("Iron", "IRON_BUTTERFLY")]),
    ("Calendar", [("Call", "CALENDAR_CALL"), ("Put", "CALENDAR_PUT")]),
    ("Diagonal", [("Call", "DIAGONAL_CALL"), ("Put", "DIAGONAL_PUT")]),
]


def strategy_label(code):
    """Display label for a strategy code: 'PCS' → 'Credit spread · put',
    'LONG_CALL' → 'Long call'. Falls back to the code itself."""
    for family, variants in STRATEGY_MENU:
        for vlabel, vcode in variants:
            if vcode == code:
                return vlabel if family == "Single" else f"{family} · {vlabel.lower()}"
    return code


# Strategy codes the calculator can summarize ANALYTICALLY (exact legacy path).
_ANALYTIC_CODES = {"PCS", "CCS", "IC", "LONG_CALL", "LONG_PUT", "NAKED_CALL", "NAKED_PUT"}


def _role_strike(role, spot, strikes):
    """Map a strike_role -> a concrete strike snapped to ``strikes``.

    ATM is nearest-to-spot; otm_up_N / otm_dn_N step N positions out from ATM in
    the sorted strike ladder so wings stay distinct on any strike grid."""
    if not strikes:
        return None
    ladder = sorted(strikes)
    atm_idx = min(range(len(ladder)), key=lambda i: abs(ladder[i] - spot))
    if role == "atm":
        return ladder[atm_idx]
    direction = 1 if "up" in role else -1
    try:
        n = int(role.rsplit("_", 1)[-1])
    except ValueError:
        n = 1
    idx = max(0, min(len(ladder) - 1, atm_idx + direction * n))
    return ladder[idx]


def build_default_legs(template, spot, strikes, expiries):
    """Normalized legs for ``template`` with ATM-centered, snapped strikes.

    ``expiries`` is a sorted list of ISO strings; near = expiries[0],
    far = the next distinct expiry (else near). [] for an unknown template."""
    specs = STRATEGY_TEMPLATES.get(template)
    if not specs:
        return []
    exps = list(expiries or [])
    near = exps[0] if exps else None
    far = next((e for e in exps if e != near), near)
    legs = []
    for spec in specs:
        legs.append({
            "option_type": spec["option_type"],
            "side": spec["side"],
            "qty": spec["qty"],
            "strike": _role_strike(spec["strike_role"], spot, strikes),
            "expiry": far if spec["expiry_role"] == "far" else near,
            "premium": None,
        })
    return legs


def summary_code(template, legs):
    """Analytic code if ``legs`` still match the canonical template for an
    analytic-capable strategy, else ``"CUSTOM"`` (numeric summary).

    'Match' = same leg shape (option_type/side/qty multiset) AND a single shared
    expiry (the analytic formulas assume one expiry). Strike edits do NOT flip
    this; the page tracks a separate 'dirty' flag for that."""
    if template not in _ANALYTIC_CODES:
        return "CUSTOM"
    if len({l.get("expiry") for l in legs}) > 1:
        return "CUSTOM"
    canon = STRATEGY_TEMPLATES[template]
    if len(legs) != len(canon):
        return "CUSTOM"
    canon_shape = sorted((s["option_type"], s["side"], s["qty"]) for s in canon)
    legs_shape = sorted((l["option_type"], l["side"], int(l.get("qty", 1))) for l in legs)
    return template if canon_shape == legs_shape else "CUSTOM"
