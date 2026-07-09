"""Autonomous-driver tunables (v1 defaults; tune on paper).

These are the static knobs for the autonomous decision layer: the daily target,
the risk envelope the guardrails enforce, the model/token budget for the Claude
call, and the intraday checkpoint cadence. Kept deliberately separate from the
legacy claude-driver ``config`` module (which driver_svc also imports) to avoid
confusion between "the old rule-tree config" and "the new autonomous tunables".

The **runtime-mutable** bits — whether autonomous mode is enabled and whether
it has halted for the day — live in ``cache:driver:control`` (the
``DriverControl`` contract), NOT here. This module holds only the fixed v1
defaults.
"""
import os


def _resolve_model() -> str:
    """The decision model: DRIVER_MODEL env var → gitignored shared/driver_model.txt
    → the build default (Opus 4.8). The file fallback mirrors the API-key resolver
    (api_keys.py) so a deployment can pin the model WITHOUT fighting env-var
    propagation (Windows ``setx`` only affects NEW windows and is easy to miss). Read
    at import — set the env var or the file before starting driver_svc; a restart
    picks up a change. Never raises (a missing/unreadable override → the default)."""
    env = os.environ.get("DRIVER_MODEL")
    if env and env.strip():
        return env.strip()
    try:
        from repo_paths import SHARED_DIR
        p = SHARED_DIR / "driver_model.txt"
        if p.exists():
            picked = p.read_text(encoding="utf-8").strip()
            if picked:
                return picked
    except Exception:  # noqa: BLE001 — a missing/unreadable override is non-fatal.
        pass
    return "claude-opus-4-8"


DAILY_TARGET = 500.0          # base bank-the-day threshold ($ net day P&L)
# Cumulative MTD target band (2026-07-09): the banking target carries the $500/day
# deficit/excess month-to-date, clamped to [floor, cap] — behind the pace it ratchets to
# the cap (recover over days, never one reckless shot), ahead it eases to the floor (keep
# a light day). The −$1,500 DAILY_LOSS_HALT + the per-trade caps are UNCHANGED, so this
# only moves WHEN the day banks/stops, not how big any single trade can be.
TARGET_CAP = 1000.0           # max ratcheted daily target (2x base)
TARGET_FLOOR = 250.0          # min daily target when ahead of the MTD pace
# ── AGGRESSIVE risk envelope ("Very Aggressive" profile, 2026-07-02, user choice) ──
# Tuned to PRESS toward the $500/day target and tolerate real drawdown. Per-trade cap
# funds the widest liquid $SPX (~$1,833/contract) with room to size up on smaller names;
# must stay in sync with options_svc compute._DRIVER_MAX_RISK_PER_TRADE (the paper
# sizer's cap on the open path). All values are for the $25k paper book.
PER_TRADE_MAX_RISK = 3000.0   # max $ loss per single spread position (~12% of the book)
DAILY_RISK_BUDGET = 12000.0   # cap on Σ open driver max-loss (~half the book deployable)
MAX_CONCURRENT = 10           # max open driver positions
MAX_TRADES_PER_CYCLE = 5      # max new trades per 30-min checkpoint
VIX_MAX = 35.0               # no new entries above this VIX (spreads collect more premium in vol)
# Daily-loss HALT: stop opening NEW trades once the day is down this much (management +
# exits are unaffected). Raised from the legacy $250 — which halted after a single losing
# $SPX — to 3x the $500 target so the driver absorbs losers and keeps pressing. This is
# now the driver's OWN knob: compute._daily_max_loss() reads it here (legacy
# config.RISK_LIMITS is only a fallback).
DAILY_LOSS_HALT = 1500.0     # $ daily loss that halts new entries
MENU_TOP_N = 15             # how many top-scored signals Claude sees
# Decision model (committed build default: Opus 4.8). Override per-deployment via the
# DRIVER_MODEL env var OR a gitignored shared/driver_model.txt file (see
# _resolve_model) — e.g. put "claude-sonnet-5" in shared/driver_model.txt to run cheaper.
MODEL = _resolve_model()
MAX_TOKENS = 2000
CHECKPOINT_MIN = 30          # intraday re-evaluation cadence (minutes)


def limits() -> dict:
    """The risk envelope the guardrails enforce (a plain dict for the packet)."""
    return {
        "daily_target": DAILY_TARGET,
        "per_trade_max_risk": PER_TRADE_MAX_RISK,
        "daily_risk_budget": DAILY_RISK_BUDGET,
        "max_concurrent": MAX_CONCURRENT,
        "max_trades_per_cycle": MAX_TRADES_PER_CYCLE,
        "vix_max": VIX_MAX,
        "daily_loss_halt": DAILY_LOSS_HALT,   # informs the model's loss budget in the packet
        "target_cap": TARGET_CAP,             # cumulative MTD target band (see above)
        "target_floor": TARGET_FLOOR,
    }
