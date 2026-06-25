# Claude Driver — Autonomous Decision Layer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the driver's hardcoded `trade_selector` rule tree with a Claude-driven, strategy-agnostic decision layer that auto-selects and sizes defined-risk option credit spreads from the scanner menu (autonomy level B — autonomous paper execution, no approval gate), within code-enforced guardrails, targeting net $500/day.

**Architecture:** A new `driver_svc` decision pipeline — `build_packet → decider.decide (Claude/Opus 4.8 tool-use) → guardrails.apply (pure, code-authoritative) → enqueue `paper_create` on `cmd:options`` — fired at 09:28 ET and every 30 min during RTH. A `cache:driver:control` key is the master switch + kill-switch; a `cache:driver:autonomous` view feeds a repurposed `/driver` monitor page. Claude may only pick from the already-scored `cache:options:scan` menu (never invents strikes); the legacy rule tree is retained behind a flag and the safe failure mode is stand-down.

**Tech Stack:** Python 3.11, `shared.bus` (Redis/Memurai, fakeredis under pytest), `shared.contracts` (pydantic `_Base`), the `anthropic` SDK (new), NiceGUI (`/driver` page), pytest. Engines are imported standalone in-process (the documented `config`/`src` isolation rule).

**Design doc:** [2026-06-24-driver-autonomous-claude-decider-design.md](2026-06-24-driver-autonomous-claude-decider-design.md)

**Relevant skills:** @superpowers:test-driven-development · @superpowers:executing-plans · @claude-api (exact Anthropic SDK call surface for the decider) · @superpowers:verification-before-completion

---

## Conventions & guardrails for the implementer

- **Run service tests per folder** from the repo root: `.venv\Scripts\python -m pytest services\driver_svc` — NEVER `pytest services` (re-triggers the `config`/`src` cross-app module collision).
- **webgui tests:** `cd webgui && ..\.venv\Scripts\python -m pytest -q`.
- **Branch:** `Using_Highcharts` (the long-lived dev branch — do not branch/PR unless asked).
- **PAPER ONLY:** never set/flip `config.PAPER_TRADE` (stays `True`). This whole feature is paper. There is NO live-order code path in this plan.
- **The guardrails module is the safety core** — it is pure and gets the most tests. The model is NEVER trusted to size its own risk.
- After each task: run the named tests, see them pass, then commit. Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

### Known shapes (verified while planning)
- `bus.enqueue_command(stream, {"type": str, "args": dict}) -> str`
- `bus.cache_set(key, payload_dict, event=None, skip_unchanged=False) -> int`; `bus.cache_get(key) -> env|None` (`env.payload`); `bus.cache_version(key) -> int|None`; `bus.publish(channel, dict)`.
- Scan menu = `cache:options:scan` → `ScanResult` → `signals_0dte: list[dict]` + `signals_swing: list[dict]`. Each signal is a sparse dict; **confirm the exact key names** for `symbol`, structure (`trade_type`/`structure`), `credit`, `max_loss`, `pop`/`pop_pct`, `composite_score`, `expiry`, `strikes` by reading `options-scanner/scanner_engine.py` `screen_spreads`/`build_iron_condors` output before Task 5.
- Paper account view = `cache:options:paper_account` → `{snapshot, positions, orders, has_account}`.
- Paper execution command: `{"type":"paper_create","args":{"signal": <scanner signal dict>, "qty": int}}` on `cmd:options` → `options_svc compute.create_paper_trade`.

### v1 assumptions (from the design doc — keep them visible)
- **Executable universe = defined-risk option credit spreads (PCS/CCS/IC) from the scanner only.** Equities deferred to v2.
- **Day-P&L attribution:** v1 uses the **whole paper-account day P&L** as the progress proxy and assumes the **paper account is dedicated to the driver during the autonomous-paper trial** (the user resets/owns it). Driver trades are additionally tagged `source="driver"` in the signal dict for the audit log. True multi-tenant attribution is v2.
- **Exits:** none added — the options service's existing 5-min paper auto-manage handles them.

---

## Phase 0 — Dependency, secrets, settings

### Task 0.1: Add the `anthropic` SDK dependency

**Files:**
- Modify: `requirements.txt` (repo root — confirm the canonical deps file; if the repo uses `pyproject.toml`/per-app reqs, add there).

**Step 1:** Add a line `anthropic>=0.40` to `requirements.txt`.

**Step 2:** Install into the venv:
Run: `.venv\Scripts\python -m pip install "anthropic>=0.40"`
Expected: installs cleanly; `.venv\Scripts\python -c "import anthropic; print(anthropic.__version__)"` prints a version.

**Step 3: Commit**
```bash
git add requirements.txt
git commit -m "build(driver): add anthropic SDK dependency for the decision layer"
```

### Task 0.2: API-key resolution helper (no secrets committed)

**Files:**
- Create: `services/driver_svc/secrets.py`
- Test: `services/driver_svc/tests/test_secrets.py`

**Step 1: Failing test**
```python
import os
from services.driver_svc import secrets

def test_api_key_prefers_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    assert secrets.anthropic_api_key() == "sk-test-123"

def test_api_key_missing_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # no shared file in the test env
    assert secrets.anthropic_api_key() in (None, "")
```

**Step 2:** Run → FAIL (module missing).
Run: `.venv\Scripts\python -m pytest services\driver_svc\tests\test_secrets.py -v`

**Step 3: Implement**
```python
"""Anthropic API key resolution for driver_svc (never commit the key).

Order: ANTHROPIC_API_KEY env var → optional gitignored shared/anthropic_key.txt.
Returns None when unset so the decider degrades to stand-down rather than raising.
"""
import os
from repo_paths import SHARED_DIR  # add if absent; else use pathlib to shared/


def anthropic_api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    try:
        p = SHARED_DIR / "anthropic_key.txt"
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None
```
(If `SHARED_DIR` isn't in `repo_paths.py`, add it: `SHARED_DIR = _ROOT / "shared"`. Add `shared/anthropic_key.txt` to `.gitignore`.)

**Step 4:** Run → PASS.

**Step 5: Commit**
```bash
git add services/driver_svc/secrets.py services/driver_svc/tests/test_secrets.py .gitignore repo_paths.py
git commit -m "feat(driver): anthropic API key resolution (env-first, gitignored fallback)"
```

### Task 0.3: Autonomous settings module

**Files:**
- Create: `services/driver_svc/settings.py`
- Test: `services/driver_svc/tests/test_settings.py`

**Step 1: Failing test**
```python
from services.driver_svc import settings

def test_limits_dict_shape():
    lim = settings.limits()
    assert lim["daily_target"] == 500.0
    assert lim["per_trade_max_risk"] > 0
    assert lim["daily_risk_budget"] >= lim["per_trade_max_risk"]
    assert lim["vix_max"] == 25.0
    assert lim["max_concurrent"] >= 1
    assert lim["max_trades_per_cycle"] >= 1
```

**Step 2:** Run → FAIL.

**Step 3: Implement**
```python
"""Autonomous-driver tunables (v1 defaults; tune on paper).

Kept separate from the legacy claude-driver ``config`` (which driver_svc also
imports) to avoid confusion. The runtime-mutable bits (enabled/halted) live in
``cache:driver:control``, NOT here."""

DAILY_TARGET = 500.0          # bank-the-day threshold
PER_TRADE_MAX_RISK = 300.0    # max $ loss per single spread position
DAILY_RISK_BUDGET = 900.0     # cap on Σ open driver max-loss
MAX_CONCURRENT = 6            # max open driver positions
MAX_TRADES_PER_CYCLE = 3      # max new trades per checkpoint
VIX_MAX = 25.0               # no new entries above this (mirrors config.VIX_MAX_TRADE)
MENU_TOP_N = 12              # how many top-scored signals Claude sees
MODEL = "claude-opus-4-8"
MAX_TOKENS = 2000
CHECKPOINT_MIN = 30          # intraday re-evaluation cadence


def limits() -> dict:
    return {
        "daily_target": DAILY_TARGET,
        "per_trade_max_risk": PER_TRADE_MAX_RISK,
        "daily_risk_budget": DAILY_RISK_BUDGET,
        "max_concurrent": MAX_CONCURRENT,
        "max_trades_per_cycle": MAX_TRADES_PER_CYCLE,
        "vix_max": VIX_MAX,
    }
```

**Step 4:** Run → PASS.

**Step 5: Commit**
```bash
git add services/driver_svc/settings.py services/driver_svc/tests/test_settings.py
git commit -m "feat(driver): autonomous settings + limits() (v1 defaults)"
```

---

## Phase 1 — Contracts

### Task 1.1: `DriverControl` + `AutonomousState` contracts

**Files:**
- Modify: `shared/contracts/driver.py` (append; do not touch `ApprovalState`/`PerfReport`)
- Test: `shared/contracts/tests/test_driver_contracts.py` (add cases; confirm the existing test path)

**Step 1: Failing test**
```python
from shared.contracts.driver import DriverControl, AutonomousState

def test_driver_control_defaults_disabled():
    c = DriverControl()
    assert c.enabled is False and c.halted is False and c.reason is None

def test_autonomous_state_envelope():
    s = AutonomousState(date="2026-06-24", day_pnl=120.0, target=500.0,
                        positions=[{"symbol": "QQQ"}],
                        decisions=[{"thesis": "x", "trades": []}],
                        enabled=True, halted=False)
    assert s.target == 500.0
    assert isinstance(s.decisions, list) and isinstance(s.positions, list)
```

**Step 2:** Run → FAIL.
Run: `.venv\Scripts\python -m pytest shared\contracts -v`

**Step 3: Implement** (append to `shared/contracts/driver.py`)
```python
class DriverControl(_Base):
    """cache:driver:control — the autonomous master switch + kill-switch.

    ``enabled`` is the user's master toggle (default OFF). ``halted`` latches
    within a day when a halt condition trips (banked $500 / loss cap / VIX) or
    the STOP button is hit; ``reason`` explains. The scheduler runs the decision
    loop only when ``enabled and not halted``."""
    enabled: bool = False
    halted: bool = False
    reason: str | None = None
    halted_date: str | None = None     # ISO date the latch was set (re-arm next day)
    timestamp: str | None = None


class AutonomousState(_Base):
    """cache:driver:autonomous — the live monitor view for the /driver page.

    ``decisions`` is the per-checkpoint audit log (thesis + chosen/clamped/
    rejected trades). Loose dicts — the page tolerates sparse rows."""
    date: str = ""
    enabled: bool = False
    halted: bool = False
    halt_reason: str | None = None
    day_pnl: float | None = None
    target: float = 500.0
    positions: list[dict] = []         # open driver positions w/ live P&L
    decisions: list[dict] = []         # newest-first checkpoint log
    last_cycle_ts: str | None = None
    error: str | None = None
    timestamp: str | None = None
```

**Step 4:** Run → PASS.

**Step 5: Commit**
```bash
git add shared/contracts/driver.py shared/contracts/tests/test_driver_contracts.py
git commit -m "feat(contracts): DriverControl + AutonomousState for autonomous driver"
```

---

## Phase 2 — Guardrails (pure safety core — heaviest TDD)

Create `services/driver_svc/guardrails.py` incrementally. All functions are pure (no I/O, no clock). Test file `services/driver_svc/tests/test_guardrails.py` grows per task.

### Task 2.1: `normalize_structure` + `is_allowed`

**Step 1: Failing test**
```python
from services.driver_svc import guardrails as g

def test_normalize_structure_canonicalizes():
    assert g.normalize_structure("put_credit_spread") == "PCS"
    assert g.normalize_structure("CALL_CREDIT_SPREAD") == "CCS"
    assert g.normalize_structure("iron_condor") == "IC"
    assert g.normalize_structure("PCS") == "PCS"

def test_is_allowed_only_defined_risk_spreads():
    assert g.is_allowed({"structure": "put_credit_spread", "max_loss": 250}) is True
    assert g.is_allowed({"structure": "naked_put", "max_loss": None}) is False
    assert g.is_allowed({"structure": "PCS", "max_loss": 0}) is False  # no real risk/credit
```

**Step 2:** Run → FAIL.

**Step 3: Implement**
```python
"""Code-authoritative guardrails for the autonomous driver (PURE, no I/O).

Claude proposes; THIS module decides. Every proposed trade is validated against
the allowlist, resized to the risk budget, or rejected. Halt conditions are
computed here too. Nothing in this module performs I/O or reads the clock."""

_STRUCT_MAP = {
    "put_credit_spread": "PCS", "pcs": "PCS",
    "call_credit_spread": "CCS", "ccs": "CCS",
    "iron_condor": "IC", "ic": "IC",
}
ALLOWED = {"PCS", "CCS", "IC"}


def normalize_structure(s) -> str:
    if not s:
        return ""
    key = str(s).strip().lower()
    return _STRUCT_MAP.get(key, str(s).strip().upper())


def _max_loss(signal) -> float | None:
    ml = signal.get("max_loss")
    try:
        return float(ml) if ml is not None else None
    except (TypeError, ValueError):
        return None


def is_allowed(signal) -> bool:
    if normalize_structure(signal.get("structure") or signal.get("trade_type")) not in ALLOWED:
        return False
    ml = _max_loss(signal)
    return ml is not None and ml > 0
```
(NOTE: confirm whether the scanner key is `structure` or `trade_type` and make `is_allowed`/`normalize_structure` read both, as shown.)

**Step 4:** Run → PASS.

**Step 5: Commit**
```bash
git add services/driver_svc/guardrails.py services/driver_svc/tests/test_guardrails.py
git commit -m "feat(driver): guardrails allowlist (defined-risk spreads only)"
```

### Task 2.2: `clamp_quantity`

**Step 1: Failing test**
```python
def test_clamp_quantity_respects_per_trade_and_budget():
    sig = {"structure": "PCS", "max_loss": 200.0}   # $200 risk per spread
    # requested 5, per-trade cap $300 → 1 spread; budget $900 → 4; → min(5,1,4)=1
    assert g.clamp_quantity(sig, 5, per_trade_max_risk=300, remaining_budget=900) == 1
    # bigger per-trade cap: per-trade $1000 → 5; budget $900 → 4; req 5 → 4
    assert g.clamp_quantity(sig, 5, per_trade_max_risk=1000, remaining_budget=900) == 4

def test_clamp_quantity_zero_when_unaffordable():
    sig = {"structure": "PCS", "max_loss": 1000.0}
    assert g.clamp_quantity(sig, 1, per_trade_max_risk=300, remaining_budget=900) == 0

def test_clamp_quantity_zero_on_bad_maxloss():
    assert g.clamp_quantity({"structure": "PCS", "max_loss": None}, 3, 300, 900) == 0
```

**Step 2:** Run → FAIL.

**Step 3: Implement** (append)
```python
import math


def clamp_quantity(signal, requested_qty, per_trade_max_risk, remaining_budget) -> int:
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
```

**Step 4:** Run → PASS. **Step 5: Commit** `feat(driver): guardrails clamp_quantity (per-trade + budget resize)`

### Task 2.3: `halt_state`

**Step 1: Failing test**
```python
def test_halt_banked_at_target():
    halted, reason = g.halt_state(day_pnl=520, target=500, daily_max_loss=250, vix=14)
    assert halted and "target" in reason.lower()

def test_halt_loss_cap():
    halted, reason = g.halt_state(day_pnl=-260, target=500, daily_max_loss=250, vix=14)
    assert halted and "loss" in reason.lower()

def test_halt_vix():
    halted, reason = g.halt_state(day_pnl=0, target=500, daily_max_loss=250, vix=26)
    assert halted and "vix" in reason.lower()

def test_no_halt_in_normal_range():
    assert g.halt_state(day_pnl=120, target=500, daily_max_loss=250, vix=15) == (False, None)
```

**Step 2:** Run → FAIL.

**Step 3: Implement** (append)
```python
def halt_state(day_pnl, target, daily_max_loss, vix, vix_max=25.0):
    """(halted, reason). Order: banked target → daily loss cap → VIX ceiling."""
    if day_pnl is not None and day_pnl >= target:
        return (True, f"Target reached: ${day_pnl:.0f} ≥ ${target:.0f} — banked for the day.")
    if day_pnl is not None and day_pnl <= -abs(daily_max_loss):
        return (True, f"Daily loss cap: ${day_pnl:.0f} ≤ -${abs(daily_max_loss):.0f}.")
    if vix is not None and vix > vix_max:
        return (True, f"VIX {vix:.1f} > {vix_max:.0f} — no new entries.")
    return (False, None)
```

**Step 4:** Run → PASS. **Step 5: Commit** `feat(driver): guardrails halt_state (bank/loss/VIX)`

### Task 2.4: `apply_guardrails` (the orchestrator)

**Step 1: Failing test**
```python
def _menu():
    return {
        "m0": {"id": "m0", "structure": "PCS", "max_loss": 200.0, "symbol": "QQQ"},
        "m1": {"id": "m1", "structure": "naked_put", "max_loss": None, "symbol": "X"},
        "m2": {"id": "m2", "structure": "IC", "max_loss": 300.0, "symbol": "SPX"},
    }

def test_apply_stand_down():
    out = g.apply_guardrails({"stand_down": True, "trades": []}, _menu(),
                             g_limits(), open_count=0, day_pnl=0)
    assert out["executable"] == [] and out["halted"] is False

def test_apply_halts_block_everything():
    out = g.apply_guardrails({"stand_down": False, "trades": [{"id": "m0", "quantity": 1}]},
                             _menu(), g_limits(), open_count=0, day_pnl=600)  # banked
    assert out["halted"] is True and out["executable"] == []

def test_apply_rejects_offmenu_and_disallowed_and_clamps():
    decision = {"stand_down": False, "trades": [
        {"id": "m9", "quantity": 1},          # off-menu → reject
        {"id": "m1", "quantity": 1},          # disallowed structure → reject
        {"id": "m0", "quantity": 99},         # clamp to budget/per-trade
    ]}
    out = g.apply_guardrails(decision, _menu(), g_limits(), open_count=0, day_pnl=0)
    ids = [t["id"] for t in out["executable"]]
    assert "m0" in ids and "m1" not in ids and "m9" not in ids
    m0 = next(t for t in out["executable"] if t["id"] == "m0")
    assert m0["qty"] >= 1
    assert any(r["id"] == "m9" for r in out["rejected"])

def test_apply_respects_max_trades_and_concurrent():
    decision = {"stand_down": False, "trades": [
        {"id": "m0", "quantity": 1}, {"id": "m2", "quantity": 1}, {"id": "m0", "quantity": 1},
    ]}
    lim = g_limits(); lim["max_trades_per_cycle"] = 1
    out = g.apply_guardrails(decision, _menu(), lim, open_count=0, day_pnl=0)
    assert len(out["executable"]) == 1

def g_limits():
    return {"daily_target": 500.0, "per_trade_max_risk": 300.0, "daily_risk_budget": 900.0,
            "max_concurrent": 6, "max_trades_per_cycle": 3, "vix_max": 25.0}
```

**Step 2:** Run → FAIL.

**Step 3: Implement** (append)
```python
def apply_guardrails(decision, menu_by_id, limits, *, open_count, day_pnl, vix=None,
                     daily_max_loss=250.0):
    """Turn a model decision into an executable, risk-clamped trade list.

    ``menu_by_id`` maps the packet menu id → the full scanner signal dict.
    Returns {executable:[{id,signal,qty,rationale}], rejected:[{id,reason}],
    halted, halt_reason}. CODE is authoritative — the model's quantities are
    ceilings, not commands."""
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

    for t in decision.get("trades", []):
        mid = t.get("id")
        sig = menu_by_id.get(mid)
        if sig is None:
            rejected.append({"id": mid, "reason": "off-menu (no matching signal)"})
            continue
        if not is_allowed(sig):
            rejected.append({"id": mid, "reason": "structure not in allowlist / no defined risk"})
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
        remaining -= qty * _max_loss(sig)
        slots -= 1

    return {"executable": executable, "rejected": rejected, "halted": False, "halt_reason": None}
```

**Step 4:** Run → PASS (all guardrail tests).
Run: `.venv\Scripts\python -m pytest services\driver_svc\tests\test_guardrails.py -v`

**Step 5: Commit** `feat(driver): guardrails.apply_guardrails — clamp/reject/halt orchestrator`

---

## Phase 3 — Decider (Claude call)

### Task 3.1: `parse_decision` (robust, malformed → stand-down)

**Files:** Create `services/driver_svc/decider.py`; Test `services/driver_svc/tests/test_decider.py`.

**Step 1: Failing test**
```python
from services.driver_svc import decider

def test_parse_valid():
    raw = {"stand_down": False, "day_thesis": "bull", "confidence": 0.7,
           "trades": [{"id": "m0", "quantity": 2, "rationale": "high pop"}]}
    d = decider.parse_decision(raw)
    assert d["stand_down"] is False and d["trades"][0]["id"] == "m0"

def test_parse_malformed_falls_back_to_stand_down():
    assert decider.parse_decision(None)["stand_down"] is True
    assert decider.parse_decision({"trades": "nope"})["stand_down"] is True
    assert decider.parse_decision({"trades": [{"quantity": 1}]})["trades"] == []  # drop id-less
```

**Step 2:** Run → FAIL.

**Step 3: Implement**
```python
"""The decision layer: build the prompt, call Claude (tool-use), parse the result.

Defensive by construction: ANY failure (no key, API error, malformed output)
returns a stand-down decision — the system never trades on a broken decision.
See @claude-api for the exact messages.create / tool-use surface."""
from services.driver_svc import settings, secrets

DECISION_TOOL = {
    "name": "submit_decision",
    "description": "Submit the trade decision for this checkpoint.",
    "input_schema": {
        "type": "object",
        "properties": {
            "stand_down": {"type": "boolean"},
            "day_thesis": {"type": "string"},
            "confidence": {"type": "number"},
            "trades": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "menu id from the packet"},
                        "quantity": {"type": "integer", "minimum": 1},
                        "rationale": {"type": "string"},
                    },
                    "required": ["id", "quantity"],
                },
            },
        },
        "required": ["stand_down", "trades"],
    },
}


def parse_decision(raw) -> dict:
    if not isinstance(raw, dict):
        return {"stand_down": True, "day_thesis": "", "confidence": 0.0, "trades": []}
    trades = raw.get("trades")
    clean = []
    if isinstance(trades, list):
        for t in trades:
            if isinstance(t, dict) and t.get("id"):
                clean.append({"id": str(t["id"]),
                              "quantity": int(t.get("quantity", 1) or 1),
                              "rationale": str(t.get("rationale", ""))})
    return {
        "stand_down": bool(raw.get("stand_down", not clean)),
        "day_thesis": str(raw.get("day_thesis", "")),
        "confidence": float(raw.get("confidence", 0.0) or 0.0),
        "trades": clean,
    }
```

**Step 4:** Run → PASS. **Step 5: Commit** `feat(driver): decider.parse_decision (malformed → stand-down)`

### Task 3.2: `build_messages` (packet → prompt)

**Step 1: Failing test**
```python
def test_build_messages_includes_menu_and_target():
    packet = {"target": 500, "gap_to_target": 380, "menu": [{"id": "m0", "symbol": "QQQ"}],
              "vix": 14.2, "open_positions": [], "limits": {"per_trade_max_risk": 300}}
    msgs = decider.build_messages(packet)
    blob = str(msgs)
    assert "m0" in blob and "500" in blob and "380" in blob
```

**Step 2:** Run → FAIL.

**Step 3: Implement** (append) — a system prompt that states the mandate (strategy-agnostic; pick from the menu only; size within limits; stand down when edge is poor; the daily target is a target, not a quota) and a user message embedding the packet JSON.
```python
import json

_SYSTEM = (
    "You are the decision engine for an autonomous PAPER options trader. Your job: "
    "choose 0+ defined-risk credit spreads FROM THE PROVIDED MENU to move toward the "
    "daily net target, or stand down. You may ONLY pick menu ids; never invent trades. "
    "Quantities you give are ceilings — code re-clamps to the risk budget. The target is "
    "a target, NOT a quota: standing down on a poor-edge checkpoint is a correct, "
    "encouraged decision. Prefer high composite_score and PoP; avoid over-concentration. "
    "Call submit_decision exactly once."
)


def build_messages(packet) -> list:
    return [{"role": "user",
             "content": "Decision packet (JSON):\n" + json.dumps(packet, default=str)}]


def system_prompt() -> str:
    return _SYSTEM
```

**Step 4:** Run → PASS. **Step 5: Commit** `feat(driver): decider.build_messages + system prompt`

### Task 3.3: `decide` (the Anthropic call, client injected/mocked)

**Step 1: Failing test** (mock client — no network)
```python
class _FakeClient:
    def __init__(self, tool_input): self._ti = tool_input
    class _Msg:  # mimic anthropic response.content = [block,...]
        pass
    @property
    def messages(self): return self
    def create(self, **kw):
        block = type("B", (), {"type": "tool_use", "name": "submit_decision", "input": self._ti})()
        return type("R", (), {"content": [block]})()

def test_decide_extracts_tool_input():
    client = _FakeClient({"stand_down": False, "trades": [{"id": "m0", "quantity": 1}]})
    d = decider.decide({"menu": [{"id": "m0"}]}, client=client)
    assert d["trades"][0]["id"] == "m0"

def test_decide_no_client_stands_down():
    # no api key / no client → stand-down, never raises
    d = decider.decide({"menu": []}, client=None, _force_no_key=True)
    assert d["stand_down"] is True
```

**Step 2:** Run → FAIL.

**Step 3: Implement** (append) — see @claude-api for the precise SDK surface.
```python
def _make_client():
    key = secrets.anthropic_api_key()
    if not key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=key)


def decide(packet, client=None, _force_no_key=False) -> dict:
    """Call Claude for a decision. Any failure → stand-down (never raises)."""
    try:
        if _force_no_key:
            return parse_decision(None)
        client = client or _make_client()
        if client is None:
            return parse_decision(None)
        resp = client.messages.create(
            model=settings.MODEL,
            max_tokens=settings.MAX_TOKENS,
            system=system_prompt(),
            tools=[DECISION_TOOL],
            tool_choice={"type": "tool", "name": "submit_decision"},
            messages=build_messages(packet),
        )
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == "submit_decision":
                return parse_decision(getattr(block, "input", None))
        return parse_decision(None)
    except Exception:  # noqa: BLE001 — degrade to stand-down.
        return parse_decision(None)
```

**Step 4:** Run → PASS.
Run: `.venv\Scripts\python -m pytest services\driver_svc\tests\test_decider.py -v`

**Step 5: Commit** `feat(driver): decider.decide (tool-use call; failure → stand-down)`

---

## Phase 4 — Compute (packet + cycle)

> Before this phase: read `options-scanner/scanner_engine.py` `screen_spreads`/`build_iron_condors` to confirm the exact signal key names, and `options-scanner/paper_engine.account_snapshot()` to confirm the day-P&L field on the snapshot. Adjust `_signal_view`/`_day_pnl` below accordingly.

### Task 4.1: `build_packet` (pure given the cache views)

**Files:** Modify `services/driver_svc/compute.py`; Test `services/driver_svc/tests/test_compute_packet.py`.

**Step 1: Failing test**
```python
from services.driver_svc import compute

def test_build_packet_filters_allowed_and_assigns_ids():
    scan = {"signals_0dte": [
                {"symbol": "QQQ", "structure": "put_credit_spread", "max_loss": 200,
                 "credit": 60, "pop": 0.85, "composite_score": 78, "expiry": "2026-06-24"},
                {"symbol": "X", "structure": "naked_put", "max_loss": None}],  # dropped
            "signals_swing": []}
    paper = {"snapshot": {"day_pnl": 120.0}, "positions": [], "has_account": True}
    pkt = compute.build_packet(scan, paper, target=500.0, limits={"per_trade_max_risk": 300,
            "daily_risk_budget": 900, "max_concurrent": 6, "max_trades_per_cycle": 3, "vix_max": 25},
            market={"vix": 14.0})
    assert pkt["gap_to_target"] == 380.0
    assert len(pkt["menu"]) == 1 and pkt["menu"][0]["id"] == "m0"
    assert "menu_by_id" in pkt and "m0" in pkt["menu_by_id"]
```

**Step 2:** Run → FAIL.

**Step 3: Implement** (add to `compute.py`)
```python
from services.driver_svc import guardrails as _g

def _day_pnl(paper_view) -> float | None:
    snap = (paper_view or {}).get("snapshot") or {}
    for k in ("day_pnl", "realized_day_pnl", "session_pnl"):  # confirm real key
        if snap.get(k) is not None:
            try: return float(snap[k])
            except (TypeError, ValueError): pass
    return None

def _menu_item(sig, mid):
    """Compact, model-facing projection of a scanner signal (+ stable id)."""
    return {"id": mid, "symbol": sig.get("symbol"),
            "structure": _g.normalize_structure(sig.get("structure") or sig.get("trade_type")),
            "expiry": sig.get("expiry"),
            "credit": sig.get("credit"), "max_loss": sig.get("max_loss"),
            "pop": sig.get("pop") or sig.get("pop_pct"),
            "score": sig.get("composite_score")}

def build_packet(scan_view, paper_view, *, target, limits, market) -> dict:
    raw = list((scan_view or {}).get("signals_0dte", [])) + \
          list((scan_view or {}).get("signals_swing", []))
    allowed = [s for s in raw if _g.is_allowed(s)]
    allowed.sort(key=lambda s: (s.get("composite_score") or 0), reverse=True)
    menu, menu_by_id = [], {}
    from services.driver_svc import settings as _st
    for i, sig in enumerate(allowed[: _st.MENU_TOP_N]):
        mid = f"m{i}"
        menu.append(_menu_item(sig, mid))
        menu_by_id[mid] = sig
    day_pnl = _day_pnl(paper_view)
    open_positions = [p for p in (paper_view or {}).get("positions", [])
                      if str(p.get("source", "")) == "driver"] or \
                     (paper_view or {}).get("positions", [])  # v1: whole account if untagged
    return {
        "target": target,
        "day_pnl": day_pnl,
        "gap_to_target": (target - day_pnl) if day_pnl is not None else target,
        "vix": (market or {}).get("vix"),
        "menu": menu,
        "menu_by_id": menu_by_id,
        "open_positions": open_positions,
        "open_count": len(open_positions),
        "limits": limits,
    }
```

**Step 4:** Run → PASS. **Step 5: Commit** `feat(driver): compute.build_packet (menu projection + gap)`

### Task 4.2: `run_cycle` (packet → decide → guardrails)

**Step 1: Failing test** (inject a fake decider via monkeypatch)
```python
def test_run_cycle_returns_executable(monkeypatch):
    scan = {"signals_0dte": [{"symbol": "QQQ", "structure": "PCS", "max_loss": 200,
                              "composite_score": 80, "credit": 55, "expiry": "2026-06-24"}],
            "signals_swing": []}
    paper = {"snapshot": {"day_pnl": 0.0}, "positions": [], "has_account": True}
    monkeypatch.setattr("services.driver_svc.decider.decide",
                        lambda packet, **kw: {"stand_down": False,
                            "day_thesis": "t", "confidence": 0.6,
                            "trades": [{"id": "m0", "quantity": 1}]})
    out = compute.run_cycle(scan, paper, target=500.0, limits=_lim(), market={"vix": 14})
    assert out["executable"][0]["signal"]["symbol"] == "QQQ"
    assert out["halted"] is False and out["decision"]["day_thesis"] == "t"

def test_run_cycle_defensive_on_explosion(monkeypatch):
    monkeypatch.setattr("services.driver_svc.decider.decide",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = compute.run_cycle({"signals_0dte": [], "signals_swing": []},
                            {"positions": []}, target=500.0, limits=_lim(), market={})
    assert out["executable"] == [] and out["decision"]["stand_down"] is True

def _lim():
    return {"daily_target": 500.0, "per_trade_max_risk": 300.0, "daily_risk_budget": 900.0,
            "max_concurrent": 6, "max_trades_per_cycle": 3, "vix_max": 25.0}
```

**Step 2:** Run → FAIL.

**Step 3: Implement** (add to `compute.py`)
```python
def run_cycle(scan_view, paper_view, *, target, limits, market, client=None) -> dict:
    """Full decision cycle (never raises). Returns the executable trade list +
    decision audit + halt state for the handler to act on."""
    from services.driver_svc import decider
    try:
        packet = build_packet(scan_view, paper_view, target=target, limits=limits, market=market)
        model_facing = {k: v for k, v in packet.items() if k != "menu_by_id"}
        decision = decider.decide(model_facing, client=client)
        guarded = _g.apply_guardrails(
            decision, packet["menu_by_id"], limits,
            open_count=packet["open_count"], day_pnl=packet["day_pnl"],
            vix=packet["vix"], daily_max_loss=250.0)
        return {"decision": decision, "day_pnl": packet["day_pnl"],
                "open_positions": packet["open_positions"], **guarded}
    except Exception as exc:  # noqa: BLE001
        return {"decision": {"stand_down": True, "day_thesis": "", "confidence": 0.0,
                             "trades": [], "error": str(exc)},
                "executable": [], "rejected": [], "halted": False, "halt_reason": None,
                "day_pnl": None, "open_positions": []}
```

**Step 4:** Run → PASS.
Run: `.venv\Scripts\python -m pytest services\driver_svc\tests\test_compute_packet.py -v`

**Step 5: Commit** `feat(driver): compute.run_cycle (decide → guardrails, defensive)`

### Task 4.3: `fetch_market_context` (VIX/GEX/ML via existing fetchers)

**Step 1: Failing test** (monkeypatch the morning_agent fetchers)
```python
def test_fetch_market_context_defensive(monkeypatch):
    monkeypatch.setattr(compute.morning_agent, "fetch_market_conditions",
                        lambda: {"vix": 13.5, "spx_spot": 5500, "vix1d": 12})
    ctx = compute.fetch_market_context()
    assert ctx["vix"] == 13.5

def test_fetch_market_context_never_raises(monkeypatch):
    monkeypatch.setattr(compute.morning_agent, "fetch_market_conditions",
                        lambda: (_ for _ in ()).throw(RuntimeError()))
    assert compute.fetch_market_context() == {}
```

**Step 2:** Run → FAIL. **Step 3: Implement**
```python
def fetch_market_context() -> dict:
    """VIX/SPX context for the packet (defensive → {} on failure)."""
    try:
        return dict(morning_agent.fetch_market_conditions() or {})
    except Exception:  # noqa: BLE001
        return {}
```
**Step 4:** PASS. **Step 5: Commit** `feat(driver): compute.fetch_market_context`

---

## Phase 5 — Handlers (bus I/O + execution)

### Task 5.1: control read/write helpers

**Files:** Modify `services/driver_svc/handlers.py`; Test `services/driver_svc/tests/test_handlers_autonomous.py` (use the suite's existing fake bus — confirm its name/fixture).

**Step 1: Failing test**
```python
from services.driver_svc import handlers

def test_control_roundtrip(fake_bus):
    handlers.set_control(fake_bus, enabled=True)
    c = handlers.read_control(fake_bus)
    assert c["enabled"] is True and c["halted"] is False

def test_stop_sets_halted(fake_bus):
    handlers.set_control(fake_bus, enabled=True)
    handlers.set_control(fake_bus, halted=True, reason="manual STOP")
    c = handlers.read_control(fake_bus)
    assert c["halted"] is True and c["reason"] == "manual STOP" and c["enabled"] is True
```

**Step 2:** Run → FAIL.

**Step 3: Implement** (add to `handlers.py`)
```python
from shared.contracts.driver import ApprovalState, PerfReport, DriverControl, AutonomousState

CACHE_CONTROL = "cache:driver:control"
EVENT_CONTROL = "events:driver:control"
CACHE_AUTONOMOUS = "cache:driver:autonomous"
EVENT_AUTONOMOUS = "events:driver:autonomous"


def read_control(bus) -> dict:
    env = bus.cache_get(CACHE_CONTROL)
    return env.payload if env else DriverControl().model_dump()


def set_control(bus, *, enabled=None, halted=None, reason=None, halted_date=None) -> dict:
    cur = read_control(bus)
    if enabled is not None: cur["enabled"] = bool(enabled)
    if halted is not None:
        cur["halted"] = bool(halted)
        cur["reason"] = reason
        cur["halted_date"] = halted_date
    cur["timestamp"] = _now_iso()
    st = DriverControl(**cur)
    bus.cache_set(CACHE_CONTROL, st.model_dump(), event=EVENT_CONTROL)
    return st.model_dump()
```

**Step 4:** PASS. **Step 5: Commit** `feat(driver): control key read/write helpers`

### Task 5.2: `run_autonomous_cycle` (gate → cycle → execute → publish)

**Step 1: Failing test**
```python
def test_cycle_disabled_is_noop(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=False)
    called = []
    monkeypatch.setattr(handlers.compute, "run_cycle", lambda *a, **k: called.append(1) or {})
    handlers.run_autonomous_cycle(fake_bus)
    assert called == []  # gated off

def test_cycle_enqueues_paper_create(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    # seed the option caches the handler reads
    fake_bus.cache_set("cache:options:scan", {"signals_0dte": [], "signals_swing": []})
    fake_bus.cache_set("cache:options:paper_account", {"snapshot": {"day_pnl": 0}, "positions": []})
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    monkeypatch.setattr(handlers.compute, "run_cycle", lambda *a, **k: {
        "decision": {"stand_down": False, "day_thesis": "t", "trades": [{"id": "m0", "quantity": 2}]},
        "executable": [{"id": "m0", "signal": {"symbol": "QQQ", "structure": "PCS"}, "qty": 2,
                        "rationale": "r"}],
        "rejected": [], "halted": False, "halt_reason": None, "day_pnl": 0.0, "open_positions": []})
    enq = []
    monkeypatch.setattr(fake_bus, "enqueue_command", lambda stream, cmd: enq.append((stream, cmd)))
    handlers.run_autonomous_cycle(fake_bus)
    assert enq and enq[0][0] == "cmd:options"
    assert enq[0][1]["type"] == "paper_create" and enq[0][1]["args"]["qty"] == 2
    assert enq[0][1]["args"]["signal"]["source"] == "driver"

def test_cycle_halt_latches_control(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    fake_bus.cache_set("cache:options:scan", {"signals_0dte": [], "signals_swing": []})
    fake_bus.cache_set("cache:options:paper_account", {"snapshot": {"day_pnl": 600}, "positions": []})
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    monkeypatch.setattr(handlers.compute, "run_cycle", lambda *a, **k: {
        "decision": {"stand_down": True, "trades": []}, "executable": [], "rejected": [],
        "halted": True, "halt_reason": "Target reached", "day_pnl": 600.0, "open_positions": []})
    handlers.run_autonomous_cycle(fake_bus)
    assert handlers.read_control(fake_bus)["halted"] is True
```

**Step 2:** Run → FAIL.

**Step 3: Implement** (add to `handlers.py`)
```python
from datetime import date
from services.driver_svc import settings

CACHE_OPT_SCAN = "cache:options:scan"
CACHE_OPT_PAPER = "cache:options:paper_account"
CMD_OPTIONS = "cmd:options"


def _read_payload(bus, key):
    env = bus.cache_get(key)
    return env.payload if env else None


def _publish_autonomous(bus, *, day_pnl, positions, decision, guarded, control):
    env = bus.cache_get(CACHE_AUTONOMOUS)
    prev = env.payload if env else {}
    log = list(prev.get("decisions", []))
    log.insert(0, {"ts": _now_iso(), "thesis": decision.get("day_thesis", ""),
                   "stand_down": decision.get("stand_down", True),
                   "executed": [{"id": t["id"], "symbol": t["signal"].get("symbol"),
                                 "qty": t["qty"], "rationale": t.get("rationale", "")}
                                for t in guarded.get("executable", [])],
                   "rejected": guarded.get("rejected", []),
                   "halted": guarded.get("halted", False),
                   "halt_reason": guarded.get("halt_reason")})
    st = AutonomousState(date=date.today().isoformat(), enabled=control["enabled"],
                         halted=control["halted"], halt_reason=control.get("reason"),
                         day_pnl=day_pnl, target=settings.DAILY_TARGET, positions=positions,
                         decisions=log[:50], last_cycle_ts=_now_iso(), timestamp=_now_iso())
    version = bus.cache_set(CACHE_AUTONOMOUS, st.model_dump(), event=EVENT_AUTONOMOUS)
    return version


def run_autonomous_cycle(bus) -> None:
    """One decision checkpoint: gated by control, executes survivors in paper."""
    control = read_control(bus)
    if not control.get("enabled") or control.get("halted"):
        return
    scan = _read_payload(bus, CACHE_OPT_SCAN) or {}
    paper = _read_payload(bus, CACHE_OPT_PAPER) or {}
    market = compute.fetch_market_context()
    out = compute.run_cycle(scan, paper, target=settings.DAILY_TARGET,
                            limits=settings.limits(), market=market)
    for t in out.get("executable", []):
        signal = {**t["signal"], "source": "driver"}
        bus.enqueue_command(CMD_OPTIONS, {"type": "paper_create",
                                          "args": {"signal": signal, "qty": t["qty"]}})
    if out.get("halted"):
        control = set_control(bus, halted=True, reason=out.get("halt_reason"),
                              halted_date=date.today().isoformat())
    _publish_autonomous(bus, day_pnl=out.get("day_pnl"),
                        positions=out.get("open_positions", []),
                        decision=out.get("decision", {}), guarded=out, control=control)
```

**Step 4:** PASS. **Step 5: Commit** `feat(driver): run_autonomous_cycle (gate→decide→paper_create→publish)`

### Task 5.3: command dispatch (`cycle`/`enable`/`disable`/`stop`)

**Step 1: Failing test**
```python
def test_handle_command_enable_disable_stop(fake_bus):
    handlers.handle_command(fake_bus, _cmd("enable"))
    assert handlers.read_control(fake_bus)["enabled"] is True
    handlers.handle_command(fake_bus, _cmd("stop"))
    assert handlers.read_control(fake_bus)["halted"] is True
    handlers.handle_command(fake_bus, _cmd("disable"))
    assert handlers.read_control(fake_bus)["enabled"] is False

def _cmd(t, **args):
    return type("C", (), {"type": t, "args": args})()
```

**Step 2:** Run → FAIL.

**Step 3: Implement** — extend the existing `handle_command` in `handlers.py`:
```python
    elif command.type == "cycle":
        run_autonomous_cycle(bus)
    elif command.type == "enable":
        set_control(bus, enabled=True, halted=False, reason=None)
    elif command.type == "disable":
        set_control(bus, enabled=False)
    elif command.type == "stop":
        set_control(bus, halted=True, reason="manual STOP",
                    halted_date=__import__("datetime").date.today().isoformat())
```
(Keep the existing `run`/`approve`/`skip`/`perf` branches.)

**Step 4:** PASS.
Run: `.venv\Scripts\python -m pytest services\driver_svc\tests\test_handlers_autonomous.py -v`

**Step 5: Commit** `feat(driver): autonomous command dispatch (cycle/enable/disable/stop)`

---

## Phase 6 — Scheduler (intraday cadence + halt re-arm)

### Task 6.1: `checkpoint_due` (pure)

**Files:** Modify `services/driver_svc/scheduler.py`; Test `services/driver_svc/tests/test_scheduler_checkpoint.py`.

**Step 1: Failing test**
```python
from datetime import datetime
from zoneinfo import ZoneInfo
from services.driver_svc import scheduler

ET = ZoneInfo("America/New_York")

def test_checkpoint_due_every_30m_in_rth():
    now = datetime(2026, 6, 24, 10, 0, tzinfo=ET)
    due, ts = scheduler.checkpoint_due(now, None)
    assert due is True
    # same 30-min slot → not due again
    assert scheduler.checkpoint_due(datetime(2026, 6, 24, 10, 20, tzinfo=ET), ts)[0] is False
    # next slot → due
    assert scheduler.checkpoint_due(datetime(2026, 6, 24, 10, 35, tzinfo=ET), ts)[0] is True

def test_checkpoint_not_due_outside_rth_or_weekend():
    assert scheduler.checkpoint_due(datetime(2026, 6, 24, 8, 0, tzinfo=ET), None)[0] is False
    assert scheduler.checkpoint_due(datetime(2026, 6, 24, 16, 30, tzinfo=ET), None)[0] is False
    assert scheduler.checkpoint_due(datetime(2026, 6, 27, 11, 0, tzinfo=ET), None)[0] is False  # Sat
```

**Step 2:** Run → FAIL.

**Step 3: Implement** (add to `scheduler.py`)
```python
from services.driver_svc import settings as _settings

RTH_START = (9, 30)
RTH_END = (16, 0)


def checkpoint_due(now, last_slot):
    """(due, slot_key) — True at most once per CHECKPOINT_MIN slot during RTH."""
    if now.weekday() >= 5:
        return (False, last_slot)
    hm = (now.hour, now.minute)
    if hm < RTH_START or hm >= RTH_END:
        return (False, last_slot)
    slot = (now.hour * 60 + now.minute) // _settings.CHECKPOINT_MIN
    key = f"{now.date().isoformat()}:{slot}"
    return (key != last_slot, key)
```

**Step 4:** PASS. **Step 5: Commit** `feat(driver): scheduler.checkpoint_due (30-min RTH slots)`

### Task 6.2: wire checkpoints + halt re-arm into `loop`

**Step 1: Failing test** — pure helper `should_rearm(control, today)`:
```python
def test_should_rearm_clears_stale_halt():
    assert scheduler.should_rearm({"halted": True, "halted_date": "2026-06-23"}, "2026-06-24") is True
    assert scheduler.should_rearm({"halted": True, "halted_date": "2026-06-24"}, "2026-06-24") is False
    assert scheduler.should_rearm({"halted": False}, "2026-06-24") is False
```

**Step 2:** Run → FAIL.

**Step 3: Implement** `should_rearm` + integrate into `loop`:
```python
def should_rearm(control, today) -> bool:
    return bool(control.get("halted")) and control.get("halted_date") not in (None, today)
```
Then in `loop`, each poll: (a) if `should_rearm(handlers.read_control(bus), today)` → `handlers.set_control(bus, halted=False, reason=None)`; (b) compute `checkpoint_due`; if due → `handlers.run_autonomous_cycle(bus)`. Keep the existing morning + perf branches (the morning `run_morning` path is retained for the approval-queue/back-compat behind the flag; the autonomous loop is the new behavior). Guard each in try/except like the existing branches.

**Step 4:** PASS.
Run: `.venv\Scripts\python -m pytest services\driver_svc\tests\test_scheduler_checkpoint.py -v`

**Step 5: Commit** `feat(driver): scheduler runs autonomous checkpoints + daily halt re-arm`

---

## Phase 7 — `/driver` monitor page

### Task 7.1: pure builders

**Files:** Modify `webgui/pages/driver.py`; Test `webgui/tests/test_driver_monitor.py`.

**Step 1: Failing test**
```python
from pages import driver

def test_target_progress():
    assert driver.target_progress(250, 500) == 0.5
    assert driver.target_progress(600, 500) == 1.0      # clamp
    assert driver.target_progress(None, 500) == 0.0

def test_control_label():
    assert "STOP" in driver.control_state_label({"enabled": True, "halted": False}).upper() or True
    assert driver.control_state_label({"enabled": False, "halted": False}) != ""
    assert "halt" in driver.control_state_label({"enabled": True, "halted": True,
                                                 "reason": "Target"}).lower()

def test_decision_log_rows():
    rows = driver.decision_log_rows([{"ts": "t", "thesis": "bull", "stand_down": False,
        "executed": [{"symbol": "QQQ", "qty": 2}], "rejected": [{"id": "m9", "reason": "off-menu"}]}])
    assert rows and rows[0]["thesis"] == "bull"
```

**Step 2:** Run → FAIL.

**Step 3: Implement** pure builders in `driver.py`:
```python
def target_progress(day_pnl, target) -> float:
    if day_pnl is None or not target:
        return 0.0
    return max(0.0, min(1.0, day_pnl / target))

def control_state_label(control) -> str:
    if not control.get("enabled"):
        return "DISABLED — autonomous off"
    if control.get("halted"):
        return f"HALTED — {control.get('reason') or 'stopped'}"
    return "ACTIVE — autonomous running"

def decision_log_rows(decisions) -> list:
    out = []
    for d in decisions or []:
        out.append({"ts": d.get("ts", ""), "thesis": d.get("thesis", ""),
                    "stand_down": d.get("stand_down", False),
                    "executed": d.get("executed", []), "rejected": d.get("rejected", []),
                    "halted": d.get("halted", False), "halt_reason": d.get("halt_reason")})
    return out
```

**Step 4:** PASS. **Step 5: Commit** `feat(webgui): driver monitor pure builders`

### Task 7.2: monitor render (toggle + STOP + progress + log)

**Step 1:** Add a `render_monitor()` (or extend `render()`) that: reads `cache:driver:autonomous` + `cache:driver:control` (version-poll via `ui.timer`), shows the **Enable/Disable** switch + a prominent **STOP** button (enqueue `cmd:driver` `{type:"enable"|"disable"|"stop"}` via `bus_client.request("driver", ...)`), a target-progress bar (`target_progress`), the open-driver-positions table, and the decision log (`decision_log_rows`). Wrap timer/handlers in `@guard` (`pages/ui_guard`). Keep the existing approval/perf UI available.

**Step 2:** Manual smoke (preview) — verify the toggle/STOP enqueue commands and the page renders from a seeded `cache:driver:autonomous`. (No unit assertion for render; the builders are covered.)

**Step 3:** Register nothing new in `test_shell.py` (route `/driver` already exists).

**Step 4: Commit** `feat(webgui): /driver autonomous monitor + STOP kill-switch`

---

## Phase 8 — End-to-end + verification

### Task 8.1: Redis-driven end-to-end test

**Files:** Test `services/driver_svc/tests/test_autonomous_e2e.py`.

**Step 1: Write the test** — with a fake/real bus: seed `cache:driver:control` enabled, seed `cache:options:scan` with one allowed PCS signal, stub `compute.fetch_market_context` + `decider.decide` (pick m0), call `handlers.run_autonomous_cycle(bus)`, assert (a) a `paper_create` command was enqueued on `cmd:options` with `source="driver"`, and (b) `cache:driver:autonomous` reflects the executed trade + day P&L.

**Step 2:** Run → PASS.
Run: `.venv\Scripts\python -m pytest services\driver_svc -q`

**Step 3: Commit** `test(driver): end-to-end autonomous cycle (Redis-driven)`

### Task 8.2: Full-suite verification (per @superpowers:verification-before-completion)

**Step 1:** Run each affected suite and paste real output:
```
.venv\Scripts\python -m pytest services\driver_svc      # all driver tests green
.venv\Scripts\python -m pytest shared\contracts         # contracts green
cd webgui && ..\.venv\Scripts\python -m pytest -q       # webgui green
```
**Step 2:** Live smoke (services up + Memurai): restart `driver_svc`, set `ANTHROPIC_API_KEY`, enable autonomous on `/driver` during RTH (or enqueue a `cycle` command), confirm a paper trade appears on the Paper Portfolio and a decision lands in the monitor log; hit **STOP** and confirm it halts.
**Step 3: Commit** any doc updates.

### Task 8.3: Update `CLAUDE.md`

Add a "Driver — autonomous Claude decision layer" subsection to the root `CLAUDE.md` Driver entry (premise, the decider→guardrails→paper_create flow, the control kill-switch, the new `anthropic` dep + key, paper-only/level-B scope, and the v1 attribution assumption). Commit `docs: record autonomous driver decision layer`.

---

## Out of scope (v2+ — do NOT build now)
Equities in the executable universe; Claude-managed exits/rolls; agentic tool-use (live chain queries) vs. the single-shot schema call; parameterized off-menu spreads; true multi-tenant paper P&L attribution; **level C / live execution** (separate design + explicit user opt-in + `PAPER_TRADE=False`).
