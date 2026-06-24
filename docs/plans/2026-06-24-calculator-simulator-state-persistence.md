# Calculator + Simulator State Persistence (+ Calc "Number of strikes") Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** (a) Replace the Calculator's Range min/max/% controls with a single
**Number of strikes** input (default 24) that draws the P&L grid at ±24 real chain
strikes around spot; (b) make `/options/calculator` and `/options/simulator` restore
their full UI state on navigate-back and auto-refresh against current market data.

**Architecture:** Persistence = a single-user module-level snapshot per page
(`_LAST_CALC` / `_LAST_SIM`), updated on every input change and restored on `render()`
under a `restoring` guard, reusing each page's existing `pending_legs` hook so the
re-run uses the restored inputs. The strikes change is additive: the engine
`calc_spread_pnl` gains an explicit `price_rows` row list; the page computes ±N real
strikes from the cached chain and passes them. Pure helpers (`snapshot`/`merge_restore`/
`pick_seed`/`strikes_window`) are unit-tested. GUI/engine-tier only — no contract changes.

**Tech Stack:** NiceGUI, Redis (Memurai) via `bus_client`, Black-Scholes calc engine, pytest.

**Design doc:** [`2026-06-24-calculator-simulator-state-persistence-design.md`](2026-06-24-calculator-simulator-state-persistence-design.md)

---

## Conventions for every task

- TDD pure helpers first (@superpowers:test-driven-development). UI wiring has no
  render-harness here (render is screenshot-verified per `webgui/CLAUDE.md`), so wiring
  steps end in a **smoke/Redis/browser** verification, not a unit test.
- Test commands:
  - webgui: `cd webgui && ..\.venv\Scripts\python -m pytest -q`
  - options_svc: `.venv\Scripts\python -m pytest services\options_svc -q` (from repo root)
  - options-scanner: `cd options-scanner && ..\.venv\Scripts\python -m pytest tests -q`
    (baseline has 2 known pre-existing failures — do not "fix" them).
- End every commit message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Branch: `Using_Highcharts` (current). Do not branch off.

---

## Task 1: Pure persistence helpers (`page_state.py`)

**Files:**
- Create: `webgui/pages/options/page_state.py`
- Test: `webgui/tests/test_page_state.py`

**Step 1: Write the failing test**

```python
# webgui/tests/test_page_state.py
from pages.options import page_state as ps


def test_merge_restore_overlays_snapshot_on_defaults():
    defaults = {"symbol": "SPY", "strategy": "PCS", "dt": 5.0}
    assert ps.merge_restore({"symbol": "AAPL", "dt": 12.0}, defaults) == {
        "symbol": "AAPL", "strategy": "PCS", "dt": 12.0}


def test_merge_restore_none_or_empty_is_defaults():
    defaults = {"symbol": "SPY", "dt": 5.0}
    assert ps.merge_restore(None, defaults) == defaults
    assert ps.merge_restore({}, defaults) == defaults
    assert ps.merge_restore(None, defaults) is not defaults


def test_merge_restore_ignores_stale_unknown_keys():
    assert ps.merge_restore({"gone": 1, "symbol": "QQQ"}, {"symbol": "SPY"}) == {"symbol": "QQQ"}


def test_snapshot_whitelists_keys():
    vals = {"symbol": "SPY", "dt": 5.0, "_widget": object()}
    assert ps.snapshot(vals, ("symbol", "dt")) == {"symbol": "SPY", "dt": 5.0}
    assert ps.snapshot({"symbol": "SPY"}, ("symbol", "dt")) == {"symbol": "SPY"}


def test_pick_seed_precedence():
    assert ps.pick_seed(handoff={"x": 1}, last={"y": 2}) == "handoff"
    assert ps.pick_seed(handoff=None, last={"y": 2}) == "restore"
    assert ps.pick_seed(handoff=None, last=None) == "default"
    assert ps.pick_seed(handoff={}, last={}) == "default"
```

**Step 2: Run it to verify it fails** — `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_page_state.py -q`
Expected: FAIL — `ModuleNotFoundError: pages.options.page_state`.

**Step 3: Implement**

```python
# webgui/pages/options/page_state.py
"""Pure helpers for per-page UI-state persistence across navigation (Tier-1).

The Calculator + Simulator keep a single-user module-level snapshot of their inputs
and restore it on render. These are the pure, unit-tested core: whitelist a snapshot,
overlay it on defaults, and resolve the seed precedence (a cross-page handoff copy
beats the persisted snapshot, which beats cold defaults)."""


def snapshot(values: dict, keys) -> dict:
    """Pick exactly ``keys`` from ``values`` (missing keys omitted; junk dropped)."""
    return {k: values[k] for k in keys if k in values}


def merge_restore(snap: dict | None, defaults: dict) -> dict:
    """Overlay a (possibly partial/stale) ``snap`` on ``defaults``; only keys present
    in ``defaults`` are taken from ``snap``. Returns a fresh dict."""
    out = dict(defaults)
    if snap:
        out.update({k: v for k, v in snap.items() if k in defaults})
    return out


def pick_seed(handoff, last) -> str:
    """Seed precedence → 'handoff' | 'restore' | 'default' (empty == absent)."""
    if handoff:
        return "handoff"
    if last:
        return "restore"
    return "default"
```

**Step 4: Run** — same command. Expected: PASS (5).

**Step 5: Commit**

```bash
git add webgui/pages/options/page_state.py webgui/tests/test_page_state.py
git commit -m "feat(options): pure helpers for page-state persistence (snapshot/merge/precedence)"
```

---

## Task 2: Calculator "Number of strikes" (range → ±N chain strikes)

Replace Range min/max/% with a Number-of-strikes control; the P&L grid rows become the
±N real chain strikes around spot. Done in three commits: engine → service → page.

### Task 2a: Engine — `calc_spread_pnl` explicit `price_rows`

**Files:**
- Modify: `options-scanner/options_calculator.py:calc_spread_pnl` (signature ~840; row-gen ~895-915)
- Test: `options-scanner/tests/test_options_calculator.py` (create if absent)

**Step 1: Failing test**

```python
from datetime import date
import options_calculator as oc

def test_calc_spread_pnl_uses_explicit_price_rows():
    legs = [{"strike": 100, "option_type": "call", "side": "long", "premium": 2.0, "qty": 1}]
    rows = oc.calc_spread_pnl(legs, 100, 0.2, 0.045, [None], (0, 1e9),
                              date(2026, 7, 17), eval_times=[0.05],
                              price_rows=[90, 95, 100, 105, 110])
    assert [r["price"] for r in rows] == [90.0, 95.0, 100.0, 105.0, 110.0]

def test_calc_spread_pnl_price_rows_none_keeps_step_gen():
    legs = [{"strike": 100, "option_type": "call", "side": "long", "premium": 2.0, "qty": 1}]
    rows = oc.calc_spread_pnl(legs, 100, 0.2, 0.045, [None], (95, 105),
                              date(2026, 7, 17), eval_times=[0.05])
    assert len(rows) >= 3 and all("price" in r for r in rows)   # unchanged fallback
```

**Step 2: Run to fail** — `cd options-scanner && ..\.venv\Scripts\python -m pytest tests/test_options_calculator.py -q`
Expected: FAIL — `TypeError: calc_spread_pnl() got an unexpected keyword argument 'price_rows'`.

**Step 3: Implement** — add `price_rows=None` to the signature, and short-circuit the
row-gen:

```python
def calc_spread_pnl(legs, spot, iv, r, eval_dates, price_range,
                    expiry_date, iv_adjustment=0.0, rows_per_side=30,
                    eval_times=None, per_leg_expiry=False, price_rows=None):
    ...
    # (after `adjusted_iv` + total_premium_received, REPLACE the step-gen block:)
    if price_rows:
        # Explicit grid rows (the page's ±N real chain strikes). Used verbatim.
        price_steps = sorted({round(float(p), 2) for p in price_rows})
    else:
        import math
        if spot > 5000: step = 5.00
        elif spot < 1000: step = 1.00
        else: step = 2.50
        inv = 1.0 / step
        low_limit = math.floor((spot - rows_per_side * step) * inv) / inv
        high_limit = math.ceil((spot + rows_per_side * step) * inv) / inv
        low = max(price_range[0], low_limit); high = min(price_range[1], high_limit)
        low_r = math.floor(low * inv) / inv; high_r = math.ceil(high * inv) / inv
        if high_r <= low_r:
            price_steps = [low_r]
        else:
            price_steps = []
            p = low_r
            while p <= high_r + 0.01:
                price_steps.append(round(p, 2)); p += step
```

**Step 4: Run** — Expected: PASS (2 new). **Step 5: Commit**

```bash
git add options-scanner/options_calculator.py options-scanner/tests/test_options_calculator.py
git commit -m "feat(calc): calc_spread_pnl accepts explicit price_rows for the grid"
```

### Task 2b: Service — `calc_compute` takes `num_strikes`/`price_rows`

**Files:**
- Modify: `services/options_svc/compute.py:calc_compute` (signature ~1169; price_range block ~1227-1240)
- Test: `services/options_svc/tests/test_compute.py`

**Step 1: Failing test**

```python
def test_calc_compute_uses_explicit_price_rows():
    out = compute.calc_compute(
        "CUSTOM", 100.0, 0.2, 0.045, 0.0, 1, "2026-07-17",
        [{"strike": 100, "premium": 2.0, "option_type": "call", "side": "long", "qty": 1,
          "expiry": "2026-07-17"}],
        num_strikes=24, price_rows=[95.0, 100.0, 105.0])
    assert [row["price"] for row in out["pnl_data"]] == [95.0, 100.0, 105.0]
```

**Step 2: Run to fail** — `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k price_rows -q`
Expected: FAIL — `calc_compute() got an unexpected keyword argument 'num_strikes'` (and a stale
test that still passes `range_min` — update those existing calc_compute tests in this step too).

**Step 3: Implement** — change the signature + the range block:

```python
def calc_compute(strategy, spot, iv, rate, ivadj, qty, expiry, legs,
                 num_strikes=24, price_rows=None, now=None) -> dict:
    ...
    # (REPLACE the `if range_min and range_max ...` / symmetric_price_range block with:)
    # Grid rows: the page's explicit ±num_strikes real chain strikes when available;
    # otherwise fall back to the engine's even-step heuristic over ±num_strikes rows
    # (wide-open price_range so rows_per_side governs).
    rows = [float(p) for p in (price_rows or []) if isinstance(p, (int, float))]
    pnl_data = oc.calc_spread_pnl(legs, spot, iv, rate, [None] * len(columns),
                                  (0.0, 1e12), expiry_date, iv_adjustment=ivadj,
                                  eval_times=eval_times, per_leg_expiry=True,
                                  rows_per_side=int(num_strikes or 24),
                                  price_rows=(rows or None))
    return {"summary": summary, "eval_labels": eval_labels, "pnl_data": pnl_data}
```

Remove the now-unused `range_min`/`range_max`/`range_pct` params + `symmetric_price_range`
call. (`symmetric_price_range` may stay in the module if other callers use it — grep first;
if unused, delete it + its test.) Update any existing `test_calc_compute*` that passed
`range_*` to pass `num_strikes`/`price_rows`.

NOTE: `handlers.calc_compute` is `compute.calc_compute(**args)` (generic splat) — **no
handler change**, but the page (Task 2c) must stop sending `range_*` and start sending
`num_strikes`/`price_rows` in lockstep with this signature.

**Step 4: Run** — `.venv\Scripts\python -m pytest services\options_svc -q`. Expected: PASS.
**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(calc): calc_compute draws ±num_strikes chain strikes (replaces range_*)"
```

### Task 2c: Page — Number-of-strikes control + `strikes_window`

**Files:**
- Modify: `webgui/pages/options/calculator.py` (range widgets ~401-412; `do_calc` params
  ~641-643; `_apply_chain` range overwrite ~669-671)
- Test: `webgui/tests/test_calculator.py`

**Step 1: Failing test (pure `strikes_window`)**

```python
from pages.options import calculator as calc

def test_strikes_window_n_either_side_of_spot():
    xs = list(range(1, 101))          # 1..100
    assert calc.strikes_window(xs, 50.4, 3) == [48, 49, 50, 51, 52, 53]   # 3 ≤spot + 3 >spot
    assert calc.strikes_window(xs, 50.0, 2) == [49, 50, 51, 52]
    assert calc.strikes_window([], 50, 5) == []
    assert calc.strikes_window(xs, 0, 3) == [1, 2, 3]      # spot below all → first n above
    assert calc.strikes_window(xs, 999, 2) == [99, 100]    # spot above all → last n below
```

**Step 2: Run to fail** — `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_calculator.py -k strikes_window -q`
Expected: FAIL — `AttributeError: ... has no attribute 'strikes_window'`.

**Step 3: Implement**

3a. Module-scope pure helper in `calculator.py`:

```python
def strikes_window(strikes, spot, n):
    """Grid price rows: the ``n`` strikes ≤ spot plus the ``n`` strikes > spot
    (strictly ±n around spot — far-OTM legs beyond it fall off the grid)."""
    xs = sorted({float(s) for s in (strikes or []) if isinstance(s, (int, float))})
    if not xs or spot is None:
        return []
    below = [s for s in xs if s <= spot][-n:]
    above = [s for s in xs if s > spot][:n]
    return below + above
```

3b. Replace the Range min/max/% UI block (~401-412) with a single Number-of-strikes input:

```python
                        with ui.row().classes("items-end gap-4 flex-wrap"):
                            nstrikes_in = ui.number("Number of strikes", value=24, min=1,
                                                    max=200, format="%.0f").classes("w-40") \
                                .tooltip("Strikes shown either side of spot in the P&L grid")
```

Delete `rmin_in`, `rmax_in`, `rpct_in`, `rpct_val` and their `on_value_change` label updater.

3c. In `do_calc`, replace the `range_*` params with `num_strikes` + `price_rows`:

```python
                "num_strikes": int(nstrikes_in.value or 24),
                "price_rows": strikes_window(
                    sorted(set(_strikes_for(expiry_sel.value, "call"))
                           | set(_strikes_for(expiry_sel.value, "put"))),
                    spot, int(nstrikes_in.value or 24)) or None,
```

(Remove the three `range_min`/`range_max`/`range_pct` lines.)

3d. In `_apply_chain`, delete the range-overwrite block (the `if cc.get("range_lo") ...`
lines) — there is no range widget to set anymore. Keep the price refresh + the
pending-legs path.

**Step 4: Run** — `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_calculator.py -q`.
Expected: PASS. Grep `rmin_in|rmax_in|rpct_in|rpct_val|range_min|range_max|range_pct` in
`calculator.py` → zero hits remain.

**Step 5: Commit**

```bash
git add webgui/pages/options/calculator.py webgui/tests/test_calculator.py
git commit -m "feat(calculator): Number-of-strikes control draws ±N chain strikes (drops range_*)"
```

---

## Task 3: Simulator persistence + auto-refresh

**Files:**
- Modify: `webgui/pages/options/simulator.py` (`state` ~272, `_apply_meta` ~533, initial
  paint ~582-606, wiring ~518-530, editor `on_change` ~362)
- Test: `webgui/tests/test_options_simulator.py`

The Simulator already auto-fetches + auto-runs on meta arrival, and `_apply_meta` already
has a `pending_legs` branch that beats the template re-seed. Persistence = snapshot on
change + restore widgets/legs on render + re-fetch.

**Step 1: Failing test (capture-key coverage)**

```python
from pages.options import page_state as ps

def test_sim_capture_keys_cover_inputs():
    assert set(sim._SIM_KEYS) == {
        "symbol", "strategy", "legs", "dt", "mult", "lookback", "ds", "active_tab"}

def test_sim_snapshot_roundtrips_via_page_state():
    vals = {"symbol": "AAPL", "strategy": "IC", "legs": [{"option_type": "put"}],
            "dt": 7.0, "mult": 2.0, "lookback": "5m_3d", "ds": -3.0,
            "active_tab": "What-if", "junk": 1}
    snap = ps.snapshot(vals, sim._SIM_KEYS)
    assert "junk" not in snap and snap["dt"] == 7.0
    assert ps.merge_restore(snap, sim._SIM_DEFAULTS)["symbol"] == "AAPL"
```

**Step 2: Run to fail** — `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_options_simulator.py -k sim_capture -q`
Expected: FAIL — `AttributeError: ... has no attribute '_SIM_KEYS'`.

**Step 3: Implement**

3a. Module scope (top of `simulator.py`):

```python
from . import page_state as _ps

_SIM_KEYS = ("symbol", "strategy", "legs", "dt", "mult", "lookback", "ds", "active_tab")
_SIM_DEFAULTS = {"symbol": "SPY", "strategy": "PCS", "legs": [], "dt": 5.0,
                 "mult": 1.5, "lookback": "auto", "ds": 0.0, "active_tab": "Replay"}
_LAST_SIM: dict = {}
```

3b. Add `"restoring": False` to the render `state` dict.

3c. After the editor + all widgets exist (~373), add `_capture` + `_restore` (see design;
capture reads symbol_in/strategy_sel/editor.get_legs()/dt_slider/mult_slider/lookback_sel/
ds_slider/tabs.value → `_LAST_SIM`; restore sets them under `state["restoring"]=True`,
stashing legs in `state["pending_legs"]`).

3d. Add `if state.get("restoring"): return` at the top of `_on_legs_changed`, `_enqueue_run`,
`_enqueue_replay`, `_slider_changed`.

3e. Wire `_capture` onto every input (symbol/strategy/dt/mult/lookback/ds/tabs) + the leg
editor `on_change`; guard the strategy template re-seed while restoring (see design §3e/3f).

3f. Replace the initial-paint + handoff block (~582-606): paint cached result instantly,
then `pick_seed(handoff, _LAST_SIM)` → handoff path (existing) / restore path (`_restore`
+ `_request_fetch`) / default (leave PCS template). Remove the old stale-meta-on-mount apply.

**Step 4: Run** — `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_options_simulator.py -q`.
Expected: PASS. **Step 5: Commit**

```bash
git add webgui/pages/options/simulator.py webgui/tests/test_options_simulator.py
git commit -m "feat(simulator): persist + restore full UI state across nav, auto-refresh on return"
```

---

## Task 4: Calculator persistence + auto-refresh

**Files:**
- Modify: `webgui/pages/options/calculator.py` (`state` ~367, `_seed_template`/`_on_contracts_change`
  guards, `_apply_chain` pending path ~681-689, handoff block ~813-823, editor `on_change` ~461)
- Test: `webgui/tests/test_calculator.py`

Mirrors Task 3 with the post-Task-2 control set (`num_strikes`, no `range_*`). The only
wrinkle: Calculator legs carry premiums, so the pending path must not re-fetch premiums
when the restored legs already have them.

**Step 1: Failing test (capture-key coverage)**

```python
from pages.options import calculator as calc
from pages.options import page_state as ps

def test_calc_capture_keys_cover_inputs():
    assert set(calc._CALC_KEYS) == {
        "symbol", "strategy", "legs", "iv", "rate", "ivadj", "contracts",
        "price", "num_strikes", "expiry"}

def test_calc_snapshot_roundtrips_via_page_state():
    snap = ps.snapshot({"symbol": "MSFT", "strategy": "CCS", "legs": [], "iv": 25.0,
                        "rate": 4.5, "ivadj": 0.0, "contracts": 3, "price": 410.0,
                        "num_strikes": 30, "expiry": "2026-07-17", "junk": 1}, calc._CALC_KEYS)
    assert "junk" not in snap
    assert ps.merge_restore(snap, calc._CALC_DEFAULTS)["num_strikes"] == 30
```

**Step 2: Run to fail** — `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_calculator.py -k calc_capture -q`
Expected: FAIL — `AttributeError: ... '_CALC_KEYS'`.

**Step 3: Implement**

3a. Module scope:

```python
from . import page_state as _ps

_CALC_KEYS = ("symbol", "strategy", "legs", "iv", "rate", "ivadj", "contracts",
              "price", "num_strikes", "expiry")
_CALC_DEFAULTS = {"symbol": "SPY", "strategy": "PCS", "legs": [], "iv": 20.0,
                  "rate": 4.5, "ivadj": 0.0, "contracts": 1, "price": 100.0,
                  "num_strikes": 24, "expiry": None}
_LAST_CALC: dict = {}
```

3b. `state` gets `"restoring": False`.

3c. `_capture` (reads symbol/strategy/legs/iv/rate/ivadj/contracts/price/`nstrikes_in.value`/
expiry → `_LAST_CALC`) + `_restore` (sets those widgets incl. `nstrikes_in`, stashes legs in
`state["pending_legs"]`, `state["contracts"]`) under the `restoring` guard. **No range gate** —
the strikes change removed the range overwrite from `_apply_chain`.

3d. Add `if state.get("restoring"): return` at the top of `_seed_template` and
`_on_contracts_change`. Wire `_capture` onto symbol/strategy/iv/rate/ivchg/contracts/price/
`nstrikes_in`/expiry + the editor `on_change`.

3e. In `_apply_chain`'s pending path, skip the premium re-fetch when restored legs already
carry premiums:

```python
        if pending:
            editor.set_legs(pending)
            if any(l.get("premium") in (None, 0) for l in pending):
                fetch_premiums()      # copy-from-Simulator legs have no premium; restore keeps theirs
            do_calc()
```

3f. After the handoff blocks (~813-823), add the restore fallback (only when no handoff
consumed the seed):

```python
    if not _pending and not _legs_in and _LAST_CALC:
        _restore(_LAST_CALC)
        load_symbol()       # auto-refresh: reload chain (fresh price) → _apply_chain applies legs + recomputes
```

**Step 4: Run** — `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_calculator.py -q`.
Expected: PASS. **Step 5: Commit**

```bash
git add webgui/pages/options/calculator.py webgui/tests/test_calculator.py
git commit -m "feat(calculator): persist + restore full UI state across nav, auto-refresh on return"
```

---

## Task 5: Full-suite + end-to-end verification

@superpowers:verification-before-completion

**Step 1: Full suites** — webgui, options_svc, options-scanner (note the 2 pre-existing
options-scanner failures are expected). All otherwise green.

**Step 2: Restart the stale services** (`reload=False`): restart **options_svc** (:8211)
AND **webgui** (:8500) via `tools\restart_one.bat` (the strikes change touches both).
Confirm `GET :8500/options/calculator` and `/options/simulator` → 200.

**Step 3: Browser checks** (Claude Preview):
- Calculator: Load `SNDK`, set Number of strikes = 10, Calculate → grid spans ~10 real
  strikes either side of spot. Change to 30 → grid grows. Navigate away + back → symbol/
  strategy/legs/contracts/**num_strikes**/the grid all restored + recomputed.
- Simulator: Fetch `AAPL`, strategy `IC`, edit a leg, switch to What-if, move Δt; navigate
  away + back → all restored, fresh sweep ran.
- Precedence: Calculator → **Copy to Simulator** → Simulator shows the copied legs (handoff
  beats the snapshot).

**Step 4: Redis spot-check** — after a restore, confirm a fresh `cache:options:calc_result`
/ `sim_result` version landed, and the calc grid's row prices equal the ±N strikes.

**Step 5: Commit** any verification fixups.

---

## Task 6: Update the architecture record (CLAUDE.md)

**Files:** Modify `CLAUDE.md` (route-table rows for `/options/calculator` + `/options/simulator`;
prepend a new `**Last updated:**` entry).

- New `**Last updated:** 2026-06-24 (**Calc Number-of-strikes + Calc/Sim state persistence**:
  ...)` entry; demote the current top to `Prior —`.
- Calculator row: note the **Number of strikes** control (±N real chain strikes, default 24,
  strictly around spot) replacing Range min/max/%; both rows: "persists full UI state across
  navigation + auto-refreshes on return (module snapshot)".
- Commit: `docs: note Calc Number-of-strikes + Calc/Sim state persistence`.

---

## Done criteria

- Calculator grid draws ±N real chain strikes around spot via the Number-of-strikes control;
  far-OTM legs may fall off (strictly ±N).
- Both pages restore full UI state on navigate-back and auto-refresh; handoff copy still wins.
- `page_state.py` + `strikes_window` + engine `price_rows` unit-tested; all suites green;
  verified live; CLAUDE.md updated.
