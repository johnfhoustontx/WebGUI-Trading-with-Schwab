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


DAILY_TARGET = 500.0          # bank-the-day threshold ($ net day P&L)
PER_TRADE_MAX_RISK = 300.0    # max $ loss per single spread position
DAILY_RISK_BUDGET = 900.0     # cap on Σ open driver max-loss
MAX_CONCURRENT = 6            # max open driver positions
MAX_TRADES_PER_CYCLE = 3      # max new trades per checkpoint
VIX_MAX = 25.0               # no new entries above this (mirrors config.VIX_MAX_TRADE)
MENU_TOP_N = 12              # how many top-scored signals Claude sees
# Decision model (committed build default: Opus 4.8). Override per-deployment via the
# DRIVER_MODEL env var OR a gitignored shared/driver_model.txt file (see
# _resolve_model) — e.g. put "claude-sonnet-4-6" in shared/driver_model.txt to run cheaper.
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
    }
