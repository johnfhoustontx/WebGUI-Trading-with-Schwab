"""Autonomous-driver tunables (v1 defaults; tune on paper).

These are the static knobs for the autonomous decision layer: the daily target,
the risk envelope the guardrails enforce, the model/token budget for the Claude
call, and the intraday checkpoint cadence. Kept deliberately separate from the
legacy claude-driver ``config`` module (which driver_svc also imports) to avoid
confusion between "the old rule-tree config" and "the new autonomous tunables".

The **runtime-mutable** bits — whether autonomous mode is enabled and whether
it has halted for the day — live in ``cache:driver:control`` (the
``DriverControl`` contract), NOT here.

**The risk envelope itself is now ``config/driver.toml``**, read through
``shared.driver_limits`` — edit the TOML and restart, no code change. It is read
through the shared module rather than defined here because
``services/options_svc/compute.py`` needs the same ``per_trade_max_risk`` and the
two services cannot import each other; they used to hold the number twice.
"""
import os
import pathlib
import sys

# Repo root on sys.path so ``shared`` (PEP 420 namespace pkg) resolves when this
# module is imported from a service started as a script.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared import driver_limits as _driver_limits  # noqa: E402


def _resolve_model() -> str:
    """The decision model: DRIVER_MODEL env var → gitignored shared/driver_model.txt
    → the build default (Sonnet 5). The file fallback mirrors the API-key resolver
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
    # The standing directive is "current Sonnet tier at every call site; never an
    # Opus model without asking". This default used to be claude-opus-4-8, and the
    # ONLY thing keeping the 30-minute autonomous checkpoints off Opus was
    # shared/driver_model.txt — which is gitignored and untracked, so a fresh
    # clone, a wiped shared/ or a new machine silently put them on Opus. The
    # committed default now matches the directive; the overrides above still win.
    return "claude-sonnet-5"


# ── The risk envelope now lives in config/driver.toml ────────────────────────
# Read through shared.driver_limits so options_svc resolves the SAME per-trade cap
# (the two services cannot import each other, and they previously held 3000.0
# twice with a comment asking future editors to keep them in step). These stay
# module CONSTANTS resolved at import: the house contract for config is "edit the
# TOML, restart the service", and a great deal of code reads settings.X directly.
_T = _driver_limits.targets()
_R = _driver_limits.risk()
_D = _driver_limits.decision()

DAILY_TARGET = float(_T["daily_target"])      # base bank-the-day threshold ($ net day P&L)
TARGET_CAP = float(_T["target_cap"])          # max ratcheted daily target
TARGET_FLOOR = float(_T["target_floor"])      # min daily target when ahead of MTD pace

PER_TRADE_MAX_RISK = float(_R["per_trade_max_risk"])   # also read by options_svc
DAILY_RISK_BUDGET = float(_R["daily_risk_budget"])
MAX_CONCURRENT = int(_R["max_concurrent"])
MAX_TRADES_PER_CYCLE = int(_R["max_trades_per_cycle"])
VIX_MAX = float(_R["vix_max"])
DAILY_LOSS_HALT = float(_R["daily_loss_halt"])

MENU_TOP_N = int(_D["menu_top_n"])            # how many top-scored signals Claude sees
CHECKPOINT_MIN = int(_D["checkpoint_min"])    # intraday re-evaluation cadence (minutes)
MAX_TOKENS = int(_D["max_tokens"])

# Directional gate (2026-07-09): hard-block the wrong-side credit spread (a CCS in an up
# tape / a PCS in a down tape) in guardrails, keyed on the market_read's price-truth
# posture. Ships INERT (False) — run_cycle forces posture "neutral" until this is flipped
# after the offline backtest (validate_directional_gate.py) shows it would have blocked the
# CCS loss bucket without nuking winners. See the design/plan 2026-07-09.
# Deliberately NOT in driver.toml: a kill switch for unvalidated behaviour should
# take a code change, not a config edit.
DIRECTIONAL_GATE_ENABLED = False
# Decision model (committed build default: Sonnet 5). Override per-deployment via
# the DRIVER_MODEL env var OR a gitignored shared/driver_model.txt file (see
# _resolve_model) — e.g. put "claude-opus-4-8" there to run one deployment richer.
MODEL = _resolve_model()


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
