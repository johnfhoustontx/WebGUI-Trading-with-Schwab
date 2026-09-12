"""The autonomous driver's risk envelope, from ``config/driver.toml``.

**Why this is config and not code.** It is the most personally-retuned block in
the repo - the values carry a dated record of the operator choosing a "Very
Aggressive" profile (2026-07-02) and re-tuning the loss halt off the legacy $250.
Changing a risk cap should not need a code edit.

**Why it lives in ``shared/`` rather than beside either consumer.** Two Tier-2
services read it and **they cannot import each other**:

* ``services/driver_svc/settings.py`` - the whole envelope, for the guardrails
  and the decision packet.
* ``services/options_svc/compute.py`` - ``per_trade_max_risk`` only, as the paper
  sizer's cap on the open path.

Those two carried the same 3000.0 twice, kept together by a comment saying "must
stay in sync". When they disagree the failure is quiet and confusing: the driver
approves a quantity the sizer then zeroes to RISK_TOO_HIGH, and the log says
"Executed" while nothing opened. One file read by both removes the possibility.

Missing file / bad TOML / missing key -> the built-in defaults, never a raise.
"""
import math

from repo_paths import DRIVER_TOML
from shared.config_toml import toml_loader

# The shipped envelope. These ARE the values - the TOML only overrides.
DEFAULTS = {
    "targets": {
        # Base bank-the-day threshold ($ net day P&L). The cumulative MTD band
        # carries the daily deficit/excess, clamped to [floor, cap]: behind the
        # pace it ratchets to the cap (recover over days, never one reckless
        # shot), ahead it eases to the floor (keep a light day). This moves only
        # WHEN the day banks, never how big a single trade can be.
        "daily_target": 500.0,
        "target_cap": 1000.0,        # max ratcheted daily target (2x base)
        "target_floor": 250.0,       # min daily target when ahead of MTD pace
    },
    "risk": {
        # Per-trade cap funds the widest liquid $SPX (~$1,833/contract) with room
        # to size up on smaller names. Read by options_svc too - see the module
        # docstring for what happens when the two sides disagree.
        "per_trade_max_risk": 3000.0,
        "daily_risk_budget": 12000.0,   # cap on the sum of open driver max-loss
        # ...and the SAME two caps as a fraction of live equity (gap assessment
        # B8), enforced as min(dollars, pct x equity) by scale_to_equity below.
        # These are not a new appetite: 0.12 and 0.48 are what the two comments
        # above have always CLAIMED, and at $25,000 they reproduce the dollar
        # figures exactly. They exist because a fixed dollar cap stops meaning
        # what it says once the book moves - measured on the live driver book at
        # $13,347 after a 46.6% drawdown, "~12% of the book" was 22.5% and
        # "~half the book" was 89.9%. 0 turns either off.
        "per_trade_max_risk_pct": 0.12,
        "daily_risk_budget_pct": 0.48,
        "max_concurrent": 10,
        "max_trades_per_cycle": 5,      # per 30-min checkpoint
        "vix_max": 35.0,                # no NEW entries above this VIX
        # Stop opening new trades once the day is down this much; management and
        # exits are unaffected. 3x the daily target so the driver absorbs losers
        # and keeps pressing, rather than halting on one losing $SPX.
        "daily_loss_halt": 1500.0,
    },
    "decision": {
        "menu_top_n": 15,            # how many top-scored signals Claude sees
        "checkpoint_min": 30,        # intraday re-evaluation cadence (minutes)
        "max_tokens": 2000,
    },
}

load, reset_cache = toml_loader(DRIVER_TOML, DEFAULTS, label="driver.toml")


def _section(name):
    cfg = load()
    sec = cfg.get(name)
    return sec if isinstance(sec, dict) else DEFAULTS[name]


def targets() -> dict:
    return _section("targets")


def risk() -> dict:
    return _section("risk")


def decision() -> dict:
    return _section("decision")


def _finite_positive(value):
    """A usable positive number, or ``None``. Rejects bool and non-finites."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) and v > 0 else None


def scale_to_equity(limits, equity):
    """``limits`` with the two risk caps tightened to ``pct x equity`` (B8).

    ``min(dollar cap, pct x equity)`` — so this can only ever **tighten**. A book
    that has GROWN must not silently authorise a bigger trade: raising appetite is
    a decision, and restoring a stale percentage is not allowed to make it. That
    asymmetry is the whole safety argument for shipping this without asking.

    ⚠ **Every absence leaves the dollar cap exactly as it is.** No equity, a zero
    or non-finite one, a missing or zero percentage, a non-numeric ceiling — each
    means "cannot scale", never "scale to zero". A fraction of an unknown cannot
    be enforced and a zero denominator would refuse every trade forever, which
    reads as a broken driver rather than as a cap. Same rule as the paper engine's
    ``max_deployed_risk_pct``. A cap absent from ``limits`` is likewise not
    invented: scaling tightens a ceiling that exists.

    Returns a COPY — the caller hands in ``settings.limits()``, and mutating it
    would leak one cycle's tightened cap into the next at a different equity.

    ⚠ Note the denominator differs from B3's deliberately. The paper engine's
    deployment cap uses ``session_start_equity`` because it is a standing ceiling
    on the whole book and must not drift intraday; these are per-trade and
    per-cycle caps re-derived at each 30-minute checkpoint, and B8's own wording
    is "a percent of CURRENT equity" — so live equity is the right basis here.
    """
    if not isinstance(limits, dict):
        return {}
    out = dict(limits)
    eq = _finite_positive(equity)
    if eq is None:
        return out
    for cap_key, pct_key in (("per_trade_max_risk", "per_trade_max_risk_pct"),
                             ("daily_risk_budget", "daily_risk_budget_pct")):
        if cap_key not in out:
            continue
        pct = _finite_positive(out.get(pct_key))
        cap = _finite_positive(out.get(cap_key))
        if pct is None or cap is None:
            continue
        out[cap_key] = min(cap, pct * eq)
    return out


def per_trade_max_risk() -> float:
    """The single value options_svc needs - kept as a named accessor so the
    cross-service coupling is greppable from both ends."""
    try:
        return float(risk()["per_trade_max_risk"])
    except Exception:
        return float(DEFAULTS["risk"]["per_trade_max_risk"])
