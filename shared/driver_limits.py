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


def per_trade_max_risk() -> float:
    """The single value options_svc needs - kept as a named accessor so the
    cross-service coupling is greppable from both ends."""
    try:
        return float(risk()["per_trade_max_risk"])
    except Exception:
        return float(DEFAULTS["risk"]["per_trade_max_risk"])
