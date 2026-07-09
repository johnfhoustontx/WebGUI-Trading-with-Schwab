# Driver directional gate + cumulative MTD target — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: build with superpowers:test-driven-development,
> task-by-task. The gate ships **behind a default-OFF flag** and may only be switched ON
> after the backtest (Task 8) passes against the driver's real closed trades.

**Goal:** (A) Hard-block the wrong-side credit spread by regime in `guardrails.py`
(backtested first); (B) make the $500/day banking target cumulative month-to-date
(carry deficit/excess, capped).

**Architecture:** Both are additive to the existing driver pipeline. B: the handler
computes a dynamic `effective_target` (from the driver book's MTD realized P&L + a
trading-day count) and threads it into `build_packet` + `halt_state`. A: a pure
`_directional_posture(market_read)` (computed in `run_cycle` from the already-present
market_read) feeds a new per-trade gate in `apply_guardrails`, **inert until
`settings.DIRECTIONAL_GATE_ENABLED` is flipped after validation**. `guardrails.py` stays
pure. Design: `docs/plans/2026-07-09-driver-directional-gate-cumulative-target-design.md`.

**Conventions (every task):** run `.venv\Scripts\python -m pytest services\driver_svc -q`;
no live Claude/proxy in tests; every helper defensive (degrades, never raises); commit per
task ending with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; PAPER ONLY;
the −$1,500 loss halt + per-trade caps are UNCHANGED.

---

## PART B — Cumulative MTD target

### Task 1: settings `TARGET_CAP` + `TARGET_FLOOR`

**Files:** `services/driver_svc/settings.py`; test `tests/test_settings.py`.

**Step 1 — failing test:**
```python
def test_target_cap_and_floor_defaults():
    from services.driver_svc import settings as s
    assert s.TARGET_CAP == 1000.0 and s.TARGET_FLOOR == 250.0
    lim = s.limits()
    assert lim["target_cap"] == 1000.0 and lim["target_floor"] == 250.0
    assert lim["daily_target"] == 500.0        # base unchanged
```
**Step 2:** run `-k target_cap` → fails (AttributeError).
**Step 3 — implement:** after `DAILY_TARGET`, add:
```python
TARGET_CAP = 1000.0    # cumulative daily target can ratchet to 2x base (recover over days)
TARGET_FLOOR = 250.0   # ...and eases to this when ahead of the MTD pace (keep a light day)
```
and in `limits()` add `"target_cap": TARGET_CAP, "target_floor": TARGET_FLOOR,`.
**Step 4:** run → pass. **Step 5:** commit `feat(driver): TARGET_CAP + TARGET_FLOOR settings`.

---

### Task 2: pure `compute.effective_target`

**Files:** `services/driver_svc/compute.py`; test `tests/test_compute_target.py` (new).

**Step 1 — failing test:**
```python
from services.driver_svc import compute

def test_effective_target_on_pace_is_base():
    # day 1, nothing banked yet -> base 500
    assert compute.effective_target(500, 1, 0, cap=1000, floor=250) == 500

def test_effective_target_behind_ratchets_to_cap():
    # day 5, only $1000 banked of the $2500 pace -> need 1500 today, capped 1000
    assert compute.effective_target(500, 5, 1000, cap=1000, floor=250) == 1000

def test_effective_target_ahead_eases_to_floor():
    # day 5, $3000 banked (ahead of $2500 pace) -> raw negative -> floored 250
    assert compute.effective_target(500, 5, 3000, cap=1000, floor=250) == 250

def test_effective_target_mid_range():
    # day 3, $700 banked of $1500 pace -> need 800 today (within band)
    assert compute.effective_target(500, 3, 700, cap=1000, floor=250) == 800

def test_effective_target_defensive_on_junk():
    assert compute.effective_target(500, None, None, cap=1000, floor=250) == 500
```
**Step 2:** run → fail. **Step 3 — implement:**
```python
def effective_target(base, n_trading_days, mtd_before_today, *, cap, floor) -> float:
    """The cumulative MTD banking target (clamped). Carries the $500/day deficit/excess.

    ``N*base − MTD_realized_before_today`` = what today must bank to be back on the
    N-days x base pace; clamped to ``[floor, cap]`` so a behind month ratchets up to the
    cap (recover over days, never one shot) and an ahead month eases to the floor. Any
    unparseable input → ``base`` (safe fallback). Never raises.
    """
    try:
        raw = float(n_trading_days) * float(base) - float(mtd_before_today)
    except (TypeError, ValueError):
        return float(base)
    return max(float(floor), min(float(cap), raw))
```
**Step 4:** pass. **Step 5:** commit `feat(driver): pure effective_target (cumulative MTD)`.

---

### Task 3: `compute.mtd_realized_before_today` + `_mtd_trading_days`

**Files:** `compute.py`; test `tests/test_compute_target.py`.

**Step 1 — failing test:**
```python
import datetime as dt

def _cp(pnl, exit_ts):
    return {"realized_pnl": pnl, "exit_ts": exit_ts}

def test_mtd_realized_sums_this_month_before_today():
    today = dt.date(2026, 7, 9)
    closed = [_cp(100, "2026-07-01T15:00:00-05:00"),   # this month, before today -> counts
              _cp(-40, "2026-07-08T13:00:00-05:00"),   # counts
              _cp(999, "2026-07-09T10:00:00-05:00"),   # TODAY -> excluded
              _cp(500, "2026-06-30T13:00:00-05:00"),   # last month -> excluded
              None, {}, {"realized_pnl": "x", "exit_ts": "2026-07-02T10:00:00-05:00"}]
    assert compute.mtd_realized_before_today(closed, today) == 60.0   # 100 - 40

def test_mtd_trading_days_counts_weekdays_minus_holidays():
    # Jul 2026: 1st is Wed; through Thu Jul 9 = 7 weekdays (4,5 = Sat/Sun excluded)
    assert compute._mtd_trading_days(dt.date(2026, 7, 9)) == 7
```
**Step 2:** run → fail. **Step 3 — implement:**
```python
import datetime as _dt

def _iso_date(ts):
    try:
        return _dt.date.fromisoformat(str(ts)[:10])
    except (TypeError, ValueError):
        return None

def mtd_realized_before_today(closed_positions, today_ct) -> float:
    """Σ realized_pnl of driver closed positions whose exit date is in the current
    month AND strictly before today. Junk-tolerant (bad rows skipped); never raises."""
    ym, total = (today_ct.year, today_ct.month), 0.0
    for p in closed_positions or []:
        if not isinstance(p, dict):
            continue
        d = _iso_date(p.get("exit_ts") or p.get("exit_time"))
        if d is None or (d.year, d.month) != ym or d >= today_ct:
            continue
        try:
            total += float(p.get("realized_pnl") or 0.0)
        except (TypeError, ValueError):
            pass
    return total

def _mtd_trading_days(today_ct) -> int:
    """Trading days from the 1st of today's month through today inclusive (weekdays −
    NYSE holidays). ``_HOLIDAYS`` imported lazily to avoid a compute<->scheduler cycle."""
    try:
        from services.driver_svc.scheduler import _HOLIDAYS
    except Exception:  # noqa: BLE001
        _HOLIDAYS = set()
    d, n = today_ct.replace(day=1), 0
    while d <= today_ct:
        if d.weekday() < 5 and d not in _HOLIDAYS:
            n += 1
        d += _dt.timedelta(days=1)
    return n
```
**Step 4:** pass. **Step 5:** commit `feat(driver): MTD realized + trading-day count helpers`.

---

### Task 4: handler wires the effective target into the cycle + surfaces it

**Files:** `handlers.py`; `shared/contracts/driver.py` (additive field);
test `tests/test_handlers_autonomous.py`.

**Step 1 — failing test:** (the handler derives the dynamic target from the driver book's
MTD realized and threads it into `run_cycle`)
```python
def test_cycle_uses_cumulative_mtd_target(fake_bus, monkeypatch):
    import datetime as dt
    from services.driver_svc import handlers
    handlers.set_control(fake_bus, enabled=True)
    fake_bus.cache_set("cache:options:scan", {"signals_0dte": [], "signals_swing": []})
    # Driver book: behind the MTD pace -> today's target should ratchet toward the cap.
    m = dt.date.today().strftime("%Y-%m")
    fake_bus.cache_set("cache:options:driver_paper_account", {
        "snapshot": {"session_pnl": 0.0}, "positions": [],
        "closed_positions": [{"realized_pnl": -300.0, "exit_ts": f"{m}-01T15:00:00-05:00"}]})
    seen = {}
    def _capture(scan, paper, *, target, limits, market, **k):
        seen["target"] = target; seen["limit_target"] = limits["daily_target"]
        return {"decision": {"stand_down": True, "trades": []}, "executable": [],
                "rejected": [], "halted": False, "halt_reason": None,
                "day_pnl": 0.0, "open_positions": []}
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    monkeypatch.setattr(handlers.compute, "run_cycle", _capture)
    handlers.run_autonomous_cycle(fake_bus)
    # target > base (behind pace) and clamped <= cap; halt uses the SAME value.
    assert 500 < seen["target"] <= 1000
    assert seen["limit_target"] == seen["target"]

def test_cycle_target_falls_back_to_base_without_book(fake_bus, monkeypatch):
    from services.driver_svc import handlers
    handlers.set_control(fake_bus, enabled=True)
    fake_bus.cache_set("cache:options:scan", {"signals_0dte": [], "signals_swing": []})
    fake_bus.cache_set("cache:options:driver_paper_account", {"snapshot": {}, "positions": []})
    seen = {}
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {})
    monkeypatch.setattr(handlers.compute, "run_cycle",
        lambda *a, target=None, **k: seen.update(target=target) or {
            "decision": {}, "executable": [], "rejected": [], "halted": False,
            "halt_reason": None, "day_pnl": None, "open_positions": []})
    handlers.run_autonomous_cycle(fake_bus)
    assert seen["target"] == 500.0
```
**Step 2:** run → fail. **Step 3 — implement:** in `run_autonomous_cycle`, after reading
`paper` and before `compute.run_cycle`, compute the effective target and thread it:
```python
    import datetime as _date  # (module already imports date; reuse it)
    today = date.today()
    closed = paper.get("closed_positions") or []
    eff_target = compute.effective_target(
        settings.DAILY_TARGET, compute._mtd_trading_days(today),
        compute.mtd_realized_before_today(closed, today),
        cap=settings.TARGET_CAP, floor=settings.TARGET_FLOOR)
    lim = settings.limits()
    lim["daily_target"] = eff_target          # halt_state banks at the dynamic target
    out = compute.run_cycle(scan, paper, target=eff_target, limits=lim, market=market)
```
(Replace the existing `limits=settings.limits()` / `target=settings.DAILY_TARGET` call.)
Wrap the target computation defensively so any failure falls back to `settings.DAILY_TARGET`.
Then surface it: pass `effective_target=eff_target` into `_publish_autonomous`, which sets
`AutonomousState.target = eff_target` (the contract field already exists) and adds a
one-line `"target_note"` to the decision row (e.g. `"target $800 · MTD −$300"`).
**Step 4:** pass + full `test_handlers_autonomous.py`. **Step 5:** commit
`feat(driver): cumulative MTD banking target wired into the cycle`.

---

## PART A — Directional gate (behind a default-OFF flag)

### Task 5: `settings.DIRECTIONAL_GATE_ENABLED` flag (default False)

**Files:** `settings.py`; test `tests/test_settings.py`.
```python
def test_directional_gate_flag_default_off():
    from services.driver_svc import settings as s
    assert s.DIRECTIONAL_GATE_ENABLED is False   # ships inert until backtested
```
Implement `DIRECTIONAL_GATE_ENABLED = False` with a comment pointing at the backtest.
Commit `feat(driver): DIRECTIONAL_GATE_ENABLED flag (default off)`.

---

### Task 6: guardrails `_side_blocked` + `WRONG_SIDE_REGIME` + `apply_guardrails(posture=)`

**Files:** `guardrails.py`; test `tests/test_guardrails.py`.

**Step 1 — failing test:**
```python
from services.driver_svc import guardrails as g

def _lim():
    return {"daily_target": 500.0, "per_trade_max_risk": 3000.0, "daily_risk_budget": 12000.0,
            "max_concurrent": 10, "max_trades_per_cycle": 5, "vix_max": 35.0}

def _dec(mid): return {"stand_down": False, "trades": [{"id": mid, "quantity": 1}]}

def test_side_blocked_matrix():
    ccs = {"type": "CCS", "max_loss": 2.0}
    pcs = {"type": "PCS", "max_loss": 2.0}
    ic  = {"type": "IC",  "max_loss": 2.0}
    assert g._side_blocked(ccs, "up") and not g._side_blocked(ccs, "down")
    assert g._side_blocked(pcs, "down") and not g._side_blocked(pcs, "up")
    assert not g._side_blocked(ic, "up") and not g._side_blocked(ic, "down")   # IC exempt
    for s in (ccs, pcs, ic):
        assert not g._side_blocked(s, "neutral")                              # neutral -> nothing

def test_apply_guardrails_blocks_wrong_side_ccs_when_up():
    menu = {"m0": {"type": "CCS", "max_loss": 2.0}}
    out = g.apply_guardrails(_dec("m0"), menu, _lim(), open_count=0, day_pnl=0.0,
                             vix=14, posture="up")
    assert out["executable"] == []
    assert out["rejected"][0]["reason"] == g.WRONG_SIDE_REGIME

def test_apply_guardrails_allows_right_side_pcs_when_up():
    menu = {"m0": {"type": "PCS", "max_loss": 2.0}}
    out = g.apply_guardrails(_dec("m0"), menu, _lim(), open_count=0, day_pnl=0.0,
                             vix=14, posture="up")
    assert len(out["executable"]) == 1 and out["executable"][0]["qty"] == 1

def test_apply_guardrails_neutral_is_backcompat():
    menu = {"m0": {"type": "CCS", "max_loss": 2.0}}
    out = g.apply_guardrails(_dec("m0"), menu, _lim(), open_count=0, day_pnl=0.0, vix=14)
    assert len(out["executable"]) == 1     # default posture 'neutral' -> no gate (as today)
```
**Step 2:** run → fail. **Step 3 — implement:** add the constant + helper, and the check
+ kwarg:
```python
WRONG_SIDE_REGIME = "wrong-side for the current regime (directional gate)"

def _side_blocked(signal, posture) -> bool:
    """True iff this defined-risk spread's directional side is wrong for ``posture``.

    A CCS (short calls) is hurt by an UP tape; a PCS (short puts) by a DOWN tape. IC is
    neutral -> never blocked. ``posture`` other than 'up'/'down' (incl. 'neutral') -> no
    block. Pure; never raises.
    """
    struct = signal_structure(signal)
    if posture == "up" and struct == "CCS":
        return True
    if posture == "down" and struct == "PCS":
        return True
    return False
```
In `apply_guardrails`, add `posture="neutral"` to the signature, and inside the loop
**immediately after the `is_allowed` reject**:
```python
        if _side_blocked(sig, posture):
            rejected.append({"id": mid, "reason": WRONG_SIDE_REGIME})
            continue
```
(A blocked trade must NOT consume a slot/budget — placing it before the capacity check
ensures that.)
**Step 4:** pass + full `test_guardrails.py`. **Step 5:** commit
`feat(driver): directional wrong-side gate in guardrails (posture-driven)`.

---

### Task 7: `compute._directional_posture` + market_read change_pct + `run_cycle` wiring

**Files:** `compute.py`; test `tests/test_compute_packet.py`.

**Step 1 — failing test:**
```python
def test_directional_posture_up_down_neutral():
    up = {"breadth_spread": 500, "indices": [
        {"symbol": "$SPX", "change_pct": 0.6}, {"symbol": "QQQ", "change_pct": 0.4}]}
    down = {"breadth_spread": -500, "indices": [
        {"symbol": "$SPX", "change_pct": -0.6}, {"symbol": "QQQ", "change_pct": -0.4}]}
    mixed = {"breadth_spread": 500, "indices": [
        {"symbol": "$SPX", "change_pct": -0.6}, {"symbol": "QQQ", "change_pct": 0.4}]}
    assert compute._directional_posture(up) == "up"
    assert compute._directional_posture(down) == "down"
    assert compute._directional_posture(mixed) == "neutral"   # index/breadth disagree
    for bad in (None, {}, {"breadth_spread": 500}, "junk"):
        assert compute._directional_posture(bad) == "neutral"

def test_run_cycle_gate_inert_when_flag_off(monkeypatch):
    # flag OFF (default) -> posture forced neutral -> a CCS in an up tape still executes.
    monkeypatch.setattr("services.driver_svc.decider.decide",
        lambda p, **k: {"stand_down": False, "trades": [{"id": "m0", "quantity": 1}]})
    scan = {"signals_0dte": [{"symbol": "SPY", "type": "CCS", "max_loss": 2.0,
                              "composite_score": 80}], "signals_swing": []}
    out = compute.run_cycle(scan, {"snapshot": {}}, target=500.0, limits=_lim(),
                            market=_market_ctx())   # _market_ctx breadth is risk_off but flag off
    assert len(out["executable"]) == 1     # gate inert

def test_run_cycle_gate_blocks_when_flag_on(monkeypatch):
    monkeypatch.setattr("services.driver_svc.settings.DIRECTIONAL_GATE_ENABLED", True)
    monkeypatch.setattr("services.driver_svc.decider.decide",
        lambda p, **k: {"stand_down": False, "trades": [{"id": "m0", "quantity": 1}]})
    up_ctx = {"breadth_spread": 500, "indices": [
        {"symbol": "$SPX", "change_pct": 0.6}, {"symbol": "QQQ", "change_pct": 0.5}]}
    scan = {"signals_0dte": [{"symbol": "SPY", "type": "CCS", "max_loss": 2.0,
                              "composite_score": 80}], "signals_swing": []}
    out = compute.run_cycle(scan, {"snapshot": {}}, target=500.0, limits=_lim(), market=up_ctx)
    assert out["executable"] == [] and out["rejected"][0]["reason"]  # blocked wrong-side
```
**Step 2:** run → fail. **Step 3 — implement:**
- Enrich `_market_read`: when building each index entry, attach `change_pct` from the
  dashboard tile (map `$SPX→"SPX"`, `SPY→"SPY"`, `QQQ→"QQQ"`), so the posture has an
  index-direction input. (Add a `{display: change_pct}` lookup from `market["dashboard"]`.)
- Add the posture helper:
```python
def _directional_posture(market_read) -> str:
    """Broad-tape direction from the market_read — 'up' / 'down' / 'neutral'.

    Keys on PRICE TRUTH (broad-index change + breadth), deliberately NOT on sentiment/bias
    (which were inverted during the loss period) nor the gamma flip (a volatility regime).
    Decisive only when the $SPX/QQQ change and the $ADVN-$DECN breadth AGREE; else neutral.
    Missing/partial data -> neutral. Never raises. (Threshold tuned by the backtest.)
    """
    try:
        mr = market_read or {}
        breadth = mr.get("breadth_spread")
        ups = downs = 0
        for i in (mr.get("indices") or []):
            if i.get("symbol") in ("$SPX", "QQQ") and i.get("change_pct") is not None:
                c = float(i["change_pct"])
                ups += c > 0; downs += c < 0
        b_up = breadth is not None and float(breadth) > 0
        b_down = breadth is not None and float(breadth) < 0
        if ups > downs and b_up:
            return "up"
        if downs > ups and b_down:
            return "down"
        return "neutral"
    except Exception:  # noqa: BLE001
        return "neutral"
```
- In `run_cycle`, compute posture (flag-gated) and pass it:
```python
        from services.driver_svc import settings as _st2
        posture = (_directional_posture(packet.get("market_read"))
                   if _st2.DIRECTIONAL_GATE_ENABLED else "neutral")
        guarded = _g.apply_guardrails(decision, packet["menu_by_id"], limits,
                    open_count=packet["open_count"], day_pnl=packet["day_pnl"],
                    vix=packet["vix"], daily_max_loss=_daily_max_loss(), posture=posture)
```
**Step 4:** pass + full compute suite. **Step 5:** commit
`feat(driver): _directional_posture + flag-gated run_cycle wiring`.

---

### Task 8: backtest harness — validate, tune, and (only if it passes) enable

**Files:** `services/driver_svc/validate_directional_gate.py` (NEW, offline — NEVER a
request path); optionally flip `settings.DIRECTIONAL_GATE_ENABLED`.

This task is **iterative and data-driven** — not a fixed edit. Steps:
1. Write the harness: read closed positions from `paper_account_driver.db`
   (`symbol, strategy, entry_ts, exit_reason, realized_pnl`); for each, reconstruct the
   broad-index (SPX) **spot trend** at `entry_ts` from `gex_history_db` (spot at entry vs
   spot ~1–2 trading days earlier) → an `up`/`down`/`neutral` posture proxy; apply
   `_side_blocked` (block CCS on up, PCS on down); tally **$ of the CCS loss bucket
   blocked (saved)** vs **$ of winners blocked (forgone)** and the surviving book's win
   rate / realized P&L.
2. Unit-test the harness's pure pieces (trend→posture, tally) on a synthetic set.
3. **RUN it** against the real DB and record the result in the commit + the CLAUDE.md
   changelog (honest numbers, small-sample caveat).
4. **Acceptance:** it blocks a **majority of the −$706 CCS loss bucket** while sparing
   **most winners** (net-positive on the realized book). If the first-cut trend threshold
   over/under-blocks, tune it (lookback / magnitude) and re-run.
5. **Only if acceptance holds:** flip `settings.DIRECTIONAL_GATE_ENABLED = True` (its own
   commit, citing the backtest numbers). If it does NOT hold, leave the flag **OFF**,
   commit the harness + findings, and report — the gate stays inert pending a better signal.

Commit(s): `feat(driver): directional-gate backtest harness + result` and, if it passes,
`feat(driver): enable directional gate (backtest: blocked $X of the CCS loss bucket)`.

---

### Task 9: docs + memory + full suite

- Root `CLAUDE.md`: changelog entry (both features; the gate's backtest result + whether it
  shipped enabled; the cumulative-target formula; restart note) + a `/driver` route note.
- Update the memory `[[driver-market-context-block]]` (or a new note) with the gate outcome.
- Green: `.venv\Scripts\python -m pytest services\driver_svc -q` +
  `.venv\Scripts\python -m pytest shared\contracts -q`.
- Commit `docs(driver): document the directional gate + cumulative MTD target`.

## Final review
Dispatch a final code-reviewer over the whole change (spec + quality), confirm suites
green, then superpowers:finishing-a-development-branch (this stays on `Using_Highcharts`).

**Restart note (user):** restart `driver_svc`. The cumulative target is live immediately;
the gate is live only if Task 8 flipped the flag. PAPER ONLY.
