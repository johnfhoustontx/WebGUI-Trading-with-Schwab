# Scanner: directional trades + day-persistent signals — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add single-leg directional trades (`LONG_CALL`/`LONG_PUT`/`SHORT_CALL`/`SHORT_PUT`) to the Options Scanner in their own sub-tab, and make the day's signals persist until end of day with a **New** marker on signals you haven't seen.

**Architecture:** The directional pass runs inside `scanner_engine.run_full_scan` (where the chains, technicals and IV data already live) reusing the **already-built** `strategy_scanner.build_directional` + `strategy_scoring.score_all`, and lands on a **new `signals_directional` list** that the autonomous driver is structurally blind to. Day-persistence publishes a **second cache key** (`cache:options:scan_day`) so `cache:options:scan` keeps its live-only semantics and no stale signal can ever reach the driver.

**Tech Stack:** Python 3.11, pytest, NiceGUI + Quasar tables, Redis (Memurai) via `shared/bus`, pydantic contracts.

**Design doc:** [2026-07-16-scanner-directional-day-persistence-design.md](2026-07-16-scanner-directional-day-persistence-design.md)

---

## Before you start — read these

- Root `CLAUDE.md` §"webgui structure", §"UI styling standard — Tailwind-first" (`.style()` is **banned**; a guard test enforces it), §"NiceGUI gotchas".
- `options-scanner/CLAUDE.md` §"Critical operational rules".
- **Test baselines — anything beyond these is YOUR regression:**

| Suite | Command (from repo root unless noted) | Baseline |
|---|---|---|
| options-scanner | `cd options-scanner; ..\.venv\Scripts\python -m pytest tests -q -p no:randomly` | **1260 passed, 15 failed, 1 skipped** (measured 2026-07-16). All 15 are **pre-existing** (`TestEarningsAvoidance`, Tk `test_dashboard_*`, `test_gex_collector*`, `test_key_levels_doc`). Do not fix them. **Count varies 14–16** under `pytest-randomly` — compare the failing *set*, not the count. |
| options_svc | `.venv\Scripts\python -m pytest services\options_svc -q -p no:randomly` | **537 passed, 2 failed** (measured 2026-07-16). The 2 (`test_expected_move.py::test_compute_expected_move_builds_payload`, `::test_compute_expected_move_skips_partial_candle`) are **pre-existing** and date-relative — confirmed by running them at the pre-plan commit `a0c1976`. Do not fix them. Full run takes >2 min; scope to a test file when iterating. |
| webgui | `cd webgui; ..\.venv\Scripts\python -m pytest . -q` | **772** green |
| contracts | `.venv\Scripts\python -m pytest shared\contracts -q` | ~37 green |
| driver_svc | `.venv\Scripts\python -m pytest services\driver_svc -q` | ~203 green |

> **Never run `pytest services`** across all services — it puts several hyphenated app dirs on `sys.path` at once and re-triggers the documented `config`/`scoring`/`src` module-name collisions. One service folder at a time.

---

## Task 1: Fix the unbounded profit/loss conflation (prerequisite bugfix)

**Why first:** `payoff_metrics` sets `unbounded = (call_coeff != 0)` — **True for both** unbounded *profit* (long call) and unbounded *loss* (naked short call). `strategy_table._fmt_max_profit` then renders `∞` whenever `unbounded` is set, so a **naked short call displays `Max P = ∞`** when its profit is actually capped at the credit, while its genuinely unlimited loss shows as a finite margin proxy. Exactly inverted.

**This bug is live on `/options/swing` today** (verified against the real engine — a short 460c at 2.00 on a 450 spot returns `max_profit=198.7, unbounded=True` → renders `∞`). Task 2 would surface it on the Scanner, and it *is* the "undefined-risk marker" the design requires. Fix it before building on top.

**Files:**
- Modify: `options-scanner/strategy_scanner.py:82-149` (`payoff_metrics`)
- Modify: `webgui/pages/options/strategy_table.py:143-148` (`_fmt_max_profit`), `159-192` (`strategy_rows`), `91-117` (`strategy_columns`)
- Test: `options-scanner/tests/test_strategy_scanner.py`, `webgui/tests/test_strategy_table.py`

### Step 1: Write the failing engine test

Add to `options-scanner/tests/test_strategy_scanner.py`:

```python
def _dleg(kind, side, strike, mark):
    return {"kind": kind, "side": side, "strike": strike, "qty": 1, "mark": mark,
            "delta": 0.3, "theta": -0.05, "vega": 0.1, "gamma": 0.01, "iv": 0.2,
            "expiration": "2026-07-24", "bid": mark - 0.05, "ask": mark + 0.05,
            "volume": 100, "oi": 500}


def test_payoff_metrics_distinguishes_unbounded_profit_from_unbounded_loss():
    """`unbounded` alone conflates a long call with a naked short call.

    A long call has unlimited PROFIT and a capped loss (the debit); a naked
    short call has a capped profit (the credit) and unlimited LOSS. Both set
    call_coeff != 0, so the display cannot tell them apart from `unbounded`.
    """
    spot = 450.0

    long_call = strategy_scanner.payoff_metrics([_dleg("call", "long", 460.0, 2.00)], spot, "SPY")
    assert long_call["unbounded_profit"] is True
    assert long_call["unbounded_loss"] is False
    assert long_call["max_profit"] is None          # genuinely unlimited

    short_call = strategy_scanner.payoff_metrics([_dleg("call", "short", 460.0, 2.00)], spot, "SPY")
    assert short_call["unbounded_profit"] is False
    assert short_call["unbounded_loss"] is True
    assert short_call["max_profit"] is not None     # capped at the credit
    assert short_call["max_profit"] > 0


def test_payoff_metrics_bounded_structure_flags_neither():
    spot = 450.0
    legs = [_dleg("call", "long", 460.0, 2.00), _dleg("call", "short", 465.0, 1.00)]
    m = strategy_scanner.payoff_metrics(legs, spot, "SPY")
    assert m["unbounded_profit"] is False
    assert m["unbounded_loss"] is False
    assert m["unbounded"] is False


def test_payoff_metrics_keeps_legacy_unbounded_flag():
    """`unbounded` stays as the OR of both, for back-compat with paper_trader."""
    spot = 450.0
    for side in ("long", "short"):
        m = strategy_scanner.payoff_metrics([_dleg("call", side, 460.0, 2.00)], spot, "SPY")
        assert m["unbounded"] is True
```

### Step 2: Run to verify it fails

```powershell
cd options-scanner; ..\.venv\Scripts\python -m pytest tests/test_strategy_scanner.py -q -k unbounded
```
Expected: FAIL — `KeyError: 'unbounded_profit'`.

### Step 3: Implement

In `options-scanner/strategy_scanner.py`, at line 91-92 replace:

```python
    call_coeff = sum(_sign(l) * l.get("qty", 1) for l in legs if l["kind"] == "call")
    unbounded = (call_coeff != 0)
```

with:

```python
    call_coeff = sum(_sign(l) * l.get("qty", 1) for l in legs if l["kind"] == "call")
    # `unbounded` alone cannot tell a long call (unlimited PROFIT) from a naked
    # short call (unlimited LOSS) — both set call_coeff != 0. Callers that render
    # a max-profit/max-loss cell need the SIDE, so emit it explicitly. `unbounded`
    # is kept as the OR of the two for back-compat (paper_trader reads it).
    unbounded_profit = (call_coeff > 0)
    unbounded_loss = (call_coeff < 0)
    unbounded = (call_coeff != 0)
```

Then in the returned dict (line ~141) replace:

```python
        "breakevens": breakevens, "unbounded": unbounded,
```

with:

```python
        "breakevens": breakevens, "unbounded": unbounded,
        "unbounded_profit": unbounded_profit, "unbounded_loss": unbounded_loss,
```

### Step 4: Run to verify it passes

```powershell
cd options-scanner; ..\.venv\Scripts\python -m pytest tests/test_strategy_scanner.py -q
```
Expected: PASS.

### Step 5: Write the failing display test

Add to `webgui/tests/test_strategy_table.py`:

```python
def test_fmt_max_profit_naked_short_shows_the_capped_credit_not_infinity():
    """A naked short's profit is capped at the credit — never render it as ∞."""
    short_call = {"type": "SHORT_CALL", "max_profit": 198.7, "max_loss": 9001.3,
                  "unbounded": True, "unbounded_profit": False, "unbounded_loss": True}
    assert strategy_table._fmt_max_profit(short_call) == "198.70"


def test_fmt_max_loss_naked_short_shows_infinity():
    short_call = {"type": "SHORT_CALL", "max_profit": 198.7, "max_loss": 9001.3,
                  "unbounded": True, "unbounded_profit": False, "unbounded_loss": True}
    assert strategy_table._fmt_max_loss(short_call) == "∞"


def test_fmt_long_call_profit_infinite_loss_capped():
    long_call = {"type": "LONG_CALL", "max_profit": None, "max_loss": 201.3,
                 "unbounded": True, "unbounded_profit": True, "unbounded_loss": False}
    assert strategy_table._fmt_max_profit(long_call) == "∞"
    assert strategy_table._fmt_max_loss(long_call) == "201.30"


def test_strategy_rows_marks_undefined_risk_only_on_unbounded_loss():
    rows = strategy_table.strategy_rows([
        {"id": "a", "type": "SHORT_CALL", "max_profit": 198.7, "max_loss": 9001.3,
         "unbounded": True, "unbounded_profit": False, "unbounded_loss": True},
        {"id": "b", "type": "LONG_CALL", "max_profit": None, "max_loss": 201.3,
         "unbounded": True, "unbounded_profit": True, "unbounded_loss": False},
    ])
    by_id = {r["id"]: r for r in rows}
    assert by_id["a"]["_undefined_risk"] is True    # naked short
    assert by_id["b"]["_undefined_risk"] is False   # long call: risk IS defined
    assert by_id["a"]["_allow_paper"] is False      # already gated, pin it
```

### Step 6: Run to verify it fails

```powershell
cd webgui; ..\.venv\Scripts\python -m pytest tests/test_strategy_table.py -q -k "max_profit or max_loss or undefined"
```
Expected: FAIL — `_fmt_max_loss` does not exist.

### Step 7: Implement the display fix

In `webgui/pages/options/strategy_table.py` replace `_fmt_max_profit` (line 143-148):

```python
def _fmt_max_profit(signal):
    """Max profit cell: '∞' only when the PROFIT side is unbounded, else 2dp.

    Gates on ``unbounded_profit``, not ``unbounded`` — the latter is also True
    for a naked short, whose profit is capped at the credit. Falls back to the
    legacy ``unbounded`` only when the profit value is genuinely absent, so a
    pre-fix cached signal still renders sanely.
    """
    mp = signal.get("max_profit")
    if mp is None:
        return "∞"
    return f"{mp:.2f}" if isinstance(mp, (int, float)) else "—"


def _fmt_max_loss(signal):
    """Max loss cell: '∞' when the LOSS side is unbounded (a naked short).

    The engine substitutes a margin proxy for an unbounded max_loss, which would
    otherwise render as a finite figure and read as a risk cap. It is not one.
    """
    if signal.get("unbounded_loss"):
        return "∞"
    return _fmt_2(signal.get("max_loss"))
```

In `strategy_rows` (line ~178) replace `"max_loss": _fmt_2(s.get("max_loss")),` with:

```python
            "max_loss": _fmt_max_loss(s),
```

and add to the same row dict (next to `_allow_paper`):

```python
            "_undefined_risk": bool(s.get("unbounded_loss")),
```

### Step 8: Render the undefined-risk marker

In `webgui/pages/options/swing.py`, find the `body-cell-max_loss` slot (add one if absent) on the results table:

```python
        table.add_slot('body-cell-max_loss', r'''
          <q-td :props="props">
            {{ props.value }}
            <q-badge v-if="props.row._undefined_risk" label="undefined risk"
                     class="q-ml-xs text-[9px] px-1 py-0 bg-[#b71c1c] text-white"/>
          </q-td>
        ''')
```

> Tailwind-first: the badge colors are **arbitrary-value classes**, not `.style()`. `tests/test_no_inline_style.py` will fail the build otherwise.

### Step 9: Run both suites

```powershell
cd options-scanner; ..\.venv\Scripts\python -m pytest tests -q
cd ..\webgui; ..\.venv\Scripts\python -m pytest . -q
```
Expected: options-scanner **1260 passed / 15 failed** (the pre-existing set, unchanged — verify the failing *set* matches, not just the count); webgui **≥786** green.

### Step 10: Commit

```bash
git add options-scanner/strategy_scanner.py options-scanner/tests/test_strategy_scanner.py \
        webgui/pages/options/strategy_table.py webgui/pages/options/swing.py \
        webgui/tests/test_strategy_table.py
git commit -m "fix(swing): stop rendering a naked short's capped profit as unlimited

payoff_metrics set unbounded=True for BOTH an unbounded profit (long call)
and an unbounded loss (naked short call), so the Max P cell rendered a short
call's credit-capped profit as INF while its genuinely unlimited loss showed
as a finite margin proxy -- exactly inverted. Emit the side explicitly.
"
```

---

## Task 2: Build directional signals in the engine

**Files:**
- Modify: `options-scanner/scanner_engine.py:1158-1168` (results dict), `1273-1347` (per-symbol loop)
- Test: `options-scanner/tests/test_scanner_engine.py`

**Context you need:**
- The per-symbol serial loop (line 1273) already has `price`, `tech`, `iv_data`, `daily_em`, `chain_0`, `chain_s` in scope. **Build here** — anywhere else means re-fetching chains.
- Copy the call pattern from `services/options_svc/compute.py:154-176` (`swing_scan`), which already does exactly this:
  `ssc.infer_market_view(tech, iv)` → `ssn.build_directional(chain, symbol, spot, atm_iv, dte_min, dte_max)` → `ssc.score_all(signals, view, atm_iv, em_1sd)`.
- **`atm_iv` must be a DECIMAL fraction.** `run_iv_analysis`'s `current_iv` is a **PERCENT** — the documented trap. Derive it from the dollar daily EM exactly as `swing_scan` does: `atm_iv = dem * sqrt(365) / spot`.
- **Naming trap:** `run_full_scan` already has a "directional pass" at line 1349 that stamps `mode="DIRECTIONAL"` on **credit spreads**. Unrelated. Do not touch it.

### Step 1: Write the failing test

Add to `options-scanner/tests/test_scanner_engine.py` (follow the existing fake-client fixtures in that file):

```python
class TestDirectionalSignals:
    def test_run_full_scan_emits_signals_directional(self, fake_client):
        results = scanner_engine.run_full_scan(fake_client, symbols=["SPY"])
        assert "signals_directional" in results
        assert isinstance(results["signals_directional"], list)

    def test_directional_signals_carry_strategy_shape(self, fake_client):
        results = scanner_engine.run_full_scan(fake_client, symbols=["SPY"])
        sigs = results["signals_directional"]
        assert sigs, "expected directional candidates from the fake chain"
        for s in sigs:
            assert s["type"] in {"LONG_CALL", "LONG_PUT", "SHORT_CALL", "SHORT_PUT"}
            assert s["family"] == "DIRECTIONAL"
            assert isinstance(s["legs"], list) and len(s["legs"]) == 1
            assert s.get("id")
            assert s.get("composite_score") is not None

    def test_directional_sorted_by_score_desc(self, fake_client):
        sigs = scanner_engine.run_full_scan(fake_client, symbols=["SPY"])["signals_directional"]
        scores = [s.get("composite_score") or 0 for s in sigs]
        assert scores == sorted(scores, reverse=True)

    def test_directional_never_leaks_into_the_credit_lists(self, fake_client):
        """The driver reads signals_0dte + signals_swing. Directional must not be there."""
        results = scanner_engine.run_full_scan(fake_client, symbols=["SPY"])
        for s in results["signals_0dte"] + results["signals_swing"]:
            assert s["type"] in {"PCS", "CCS", "IC"}

    def test_directional_degrades_when_builder_raises(self, fake_client, monkeypatch):
        """A directional failure must never break the credit-spread scan."""
        import strategy_scanner
        monkeypatch.setattr(strategy_scanner, "build_directional",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        results = scanner_engine.run_full_scan(fake_client, symbols=["SPY"])
        assert results["signals_directional"] == []
        assert isinstance(results["signals_0dte"], list)   # scan survived
```

### Step 2: Run to verify it fails

```powershell
cd options-scanner; ..\.venv\Scripts\python -m pytest tests/test_scanner_engine.py -q -k Directional
```
Expected: FAIL — `KeyError: 'signals_directional'`.

### Step 3: Implement

In `scanner_engine.py`, add to the `results` dict (line ~1165, after `"signals_swing": []`):

```python
        "signals_directional": [],
```

Add a module-level constant near the other scan tunables:

```python
# Single-leg directional candidates (LONG/SHORT calls & puts) are built per
# symbol per DTE window and scored on strategy_scoring's Fit+Quality model --
# options-scanner's own `scoring.py` is a premium-seller's model that cannot
# score a long call (it rewards positive theta and penalizes long vega).
DIRECTIONAL_SINGLE_MAX_PER_SYMBOL = 8
```

Inside the per-symbol serial loop, **after** the Swing block (after line 1347, still inside `for symbol in symbols:`), add:

```python
        # --- Single-leg directional candidates (own tab, own scorer) ---
        # Lazy import: strategy_scoring lazy-imports options-scanner's `scoring`
        # for its liquidity normalizer; keep the binding local to the call.
        try:
            import strategy_scanner as _ssn
            import strategy_scoring as _ssc

            # ATM IV as a DECIMAL fraction, derived from the engine's
            # authoritative dollar daily EM (dem = spot*iv_dec*sqrt(1/365)).
            # run_iv_analysis's `current_iv` is a PERCENT -- the documented trap.
            atm_iv = None
            if daily_em and price and price > 0:
                atm_iv = (daily_em * math.sqrt(365.0)) / price
            if not atm_iv:
                civ = iv_data.get("current_iv")
                atm_iv = (civ / 100.0) if (civ and civ > 1.5) else (civ or 0.20)

            view = _ssc.infer_market_view(tech or {}, iv_data or {})
            dir_sigs = []
            for _chain, _lo, _hi in (
                (chain_0, zerodte_min_dte, zerodte_max_dte),
                (chain_s, swing_min_dte, swing_max_dte),
            ):
                if not _chain or _chain.get("status") == "FAILED":
                    continue
                dir_sigs += _ssn.build_directional(_chain, symbol, price, atm_iv, _lo, _hi)

            if dir_sigs:
                em_1sd = (daily_em or 0.0) * math.sqrt(max(zerodte_min_dte, 1))
                dir_sigs = _ssc.score_all(dir_sigs, view, atm_iv, em_1sd)
                results["signals_directional"].extend(
                    dir_sigs[:DIRECTIONAL_SINGLE_MAX_PER_SYMBOL])
        except Exception as e:  # noqa: BLE001
            # Directional is additive -- never let it break the credit-spread scan.
            log.warning(f"  directional build for {symbol} failed: {e}")
```

> `chain_0` / `chain_s` are bound inside the two `if` blocks above. Hoist them: `chain_0 = data["chain_0"]` is at line 1293 and `chain_s = data["chain_s"]` at 1334 — both already execute unconditionally within the loop body, so both names are in scope here. Verify before relying on it.

Add `import math` at the top of `scanner_engine.py` if not already present (check first — it likely is).

Finally, sort the accumulated list **after** the symbol loop (near the existing re-sort at line 1517):

```python
    results["signals_directional"].sort(
        key=lambda x: (x.get("composite_score") or 0), reverse=True)
```

### Step 4: Run to verify it passes

```powershell
cd options-scanner; ..\.venv\Scripts\python -m pytest tests/test_scanner_engine.py -q
```
Expected: PASS (the pre-existing failing set — `TestEarningsAvoidance` et al — **unchanged**).

### Step 5: Commit

```bash
git add options-scanner/scanner_engine.py options-scanner/tests/test_scanner_engine.py
git commit -m "feat(scanner): build single-leg directional candidates in run_full_scan

Reuses strategy_scanner.build_directional + strategy_scoring (already proven
on the Swing page) against the chains the scan has already fetched. Lands on
a NEW signals_directional list, deliberately separate from the credit-spread
lists the autonomous driver reads.
"
```

---

## Task 3: Carry `signals_directional` through the contract + handler

**Files:**
- Modify: `shared/contracts/options.py:4-21` (`ScanResult`)
- Modify: `services/options_svc/handlers.py:254-261` (`_SCAN_DEFAULTS`)
- Test: `shared/contracts/tests/test_options.py`, `services/options_svc/tests/test_handlers.py`

### Step 1: Write the failing tests

`shared/contracts/tests/test_options.py`:

```python
def test_scan_result_accepts_signals_directional():
    r = ScanResult(signals_0dte=[], signals_swing=[],
                   signals_directional=[{"id": "x", "type": "LONG_CALL"}])
    assert r.signals_directional[0]["type"] == "LONG_CALL"


def test_scan_result_back_compat_without_signals_directional():
    """A payload cached before this field existed must still validate."""
    r = ScanResult(signals_0dte=[], signals_swing=[])
    assert r.signals_directional == []
```

`services/options_svc/tests/test_handlers.py`:

```python
def test_rescan_publishes_signals_directional(bus, monkeypatch):
    monkeypatch.setattr(compute, "run_scan", lambda: {
        "signals_0dte": [], "signals_swing": [],
        "signals_directional": [{"id": "d1", "type": "LONG_CALL", "symbol": "SPY"}],
        "timestamp": "2026-07-16T10:00:00", "errors": [], "warnings": [],
        "vix_term_structure": {},
    })
    handlers.rescan(bus)
    cached = bus.cache_get(handlers.CACHE_SCAN)
    assert cached["signals_directional"][0]["id"] == "d1"
```

### Step 2: Run to verify they fail

```powershell
.venv\Scripts\python -m pytest shared\contracts\tests\test_options.py -q -k directional
.venv\Scripts\python -m pytest services\options_svc\tests\test_handlers.py -q -k directional
```
Expected: FAIL — unexpected/absent field.

### Step 3: Implement

`shared/contracts/options.py`, in `ScanResult` after `signals_swing`:

```python
    signals_directional: list[dict] = []
```

Update its docstring to mention directional candidates carry the `strategy_scanner` shape (a `legs` list, per-contract dollars) rather than the flat PCS/CCS shape.

`services/options_svc/handlers.py`, add to `_SCAN_DEFAULTS`:

```python
    "signals_directional": [],
```

and fix the stale comment above it (`# The six fields ScanResult validates` → seven).

### Step 4: Run to verify they pass

```powershell
.venv\Scripts\python -m pytest shared\contracts -q
.venv\Scripts\python -m pytest services\options_svc\tests\test_handlers.py -q
```
Expected: PASS.

### Step 5: Commit

```bash
git add shared/contracts/options.py shared/contracts/tests/test_options.py \
        services/options_svc/handlers.py services/options_svc/tests/test_handlers.py
git commit -m "feat(contracts): carry signals_directional on ScanResult

Additive with a default, so payloads cached before this field still validate."
```

---

## Task 4: Pin the driver's isolation from directional signals

**No production code changes.** This task exists to make an implicit safety property explicit and permanent. `driver_svc/compute.build_packet` merges `signals_0dte + signals_swing` only, so a third list is invisible to it *by construction* — and `guardrails.ALLOWED = {"PCS","CCS","IC"}` is the belt to that suspenders. Both facts are load-bearing and neither is currently tested against directional input.

**Files:**
- Test: `services/driver_svc/tests/test_compute_packet.py`, `services/driver_svc/tests/test_guardrails.py`

### Step 1: Write the tests

`test_compute_packet.py`:

```python
def test_build_packet_ignores_signals_directional():
    """The driver must never be offered a single-leg directional trade.

    Naked shorts are undefined-risk and long options are not the driver's
    mandate. build_packet reads only the two credit-spread lists; a directional
    list on the same scan view must not reach the menu.
    """
    scan = {
        "signals_0dte": [{"id": "s1", "symbol": "SPY", "type": "PCS",
                          "max_loss": 4.0, "credit": 1.0, "composite_score": 70}],
        "signals_swing": [],
        "signals_directional": [{"id": "d1", "symbol": "SPY", "type": "LONG_CALL",
                                 "max_loss": 200.0, "composite_score": 99}],
    }
    packet = compute.build_packet(scan, market={}, account={})
    ids = {m["id"] for m in packet["menu"]}
    symbols_types = {m.get("structure") for m in packet["menu"]}
    assert "LONG_CALL" not in symbols_types
    assert all(not str(i).startswith("d") for i in packet["menu_by_id"].values()
               if isinstance(i, str))
    assert len(packet["menu"]) == 1        # only the PCS survived
```

> Adapt the `build_packet(...)` call + assertions to the real signature in `services/driver_svc/compute.py:426` — read it first; the packet keys are `menu` / `menu_by_id`.

`test_guardrails.py`:

```python
import pytest

@pytest.mark.parametrize("stype", ["LONG_CALL", "LONG_PUT", "SHORT_CALL", "SHORT_PUT"])
def test_directional_types_are_not_allowed(stype):
    """Defense-in-depth: even if a directional signal reached the guardrail."""
    assert guardrails.is_allowed({"type": stype, "max_loss": 200.0}) is False
```

### Step 2: Run — these should PASS immediately

```powershell
.venv\Scripts\python -m pytest services\driver_svc -q
```
Expected: PASS. **If any fails, STOP** — the isolation the design depends on is not real, and the design needs revisiting before proceeding.

### Step 3: Commit

```bash
git add services/driver_svc/tests/
git commit -m "test(driver): pin that directional signals never reach the menu

Guards two load-bearing properties the scanner's new directional list relies
on: build_packet reads only the credit-spread lists, and the guardrail
allowlist rejects single-leg types. Neither was covered against this input."
```

---

## Task 5: The day-union merge (pure)

**Files:**
- Modify: `services/options_svc/compute.py`
- Test: `services/options_svc/tests/test_compute.py`

**Key facts:**
- Key each signal by its **`id`** — the engine already guarantees uniqueness (`{symbol}_{side}_{exp}_{short}_{long}`; `_assemble` sets one too). Do **not** rebuild a composite key.
- Date-scoped envelope that resets wholesale on a date change — the pattern `push_notify.new_keys` (`push_notify.py:342`) already proves.

### Step 1: Write the failing tests

```python
_LISTS = ("signals_0dte", "signals_swing", "signals_directional")


def _sig(sid, credit=1.0):
    return {"id": sid, "symbol": "SPY", "type": "PCS", "credit": credit}


def test_merge_day_signals_seeds_from_empty_prev():
    out = compute.merge_day_signals(None, {"signals_0dte": [_sig("a")]}, "2026-07-16")
    assert out["date"] == "2026-07-16"
    assert [s["id"] for s in out["signals_0dte"]] == ["a"]
    assert out["signals_0dte"][0]["live"] is True


def test_merge_day_signals_keeps_live_signal_fresh():
    """A still-qualifying signal takes the CURRENT scan's numbers, not the old ones."""
    prev = compute.merge_day_signals(None, {"signals_0dte": [_sig("a", credit=1.0)]}, "2026-07-16")
    out = compute.merge_day_signals(prev, {"signals_0dte": [_sig("a", credit=2.5)]}, "2026-07-16")
    assert len(out["signals_0dte"]) == 1
    assert out["signals_0dte"][0]["credit"] == 2.5   # refreshed
    assert out["signals_0dte"][0]["live"] is True


def test_merge_day_signals_freezes_dropped_out_signal():
    prev = compute.merge_day_signals(None, {"signals_0dte": [_sig("a", credit=1.0)]}, "2026-07-16")
    out = compute.merge_day_signals(prev, {"signals_0dte": []}, "2026-07-16")
    assert len(out["signals_0dte"]) == 1
    kept = out["signals_0dte"][0]
    assert kept["id"] == "a"
    assert kept["credit"] == 1.0        # frozen at last-seen
    assert kept["live"] is False
    assert kept["stale_since"]          # stamped


def test_merge_day_signals_accumulates_the_union():
    prev = compute.merge_day_signals(None, {"signals_0dte": [_sig("a")]}, "2026-07-16")
    out = compute.merge_day_signals(prev, {"signals_0dte": [_sig("b")]}, "2026-07-16")
    assert {s["id"] for s in out["signals_0dte"]} == {"a", "b"}


def test_merge_day_signals_reappearing_signal_goes_live_again():
    """Dropped out, then came back -- it must be live with fresh numbers."""
    e = compute.merge_day_signals(None, {"signals_0dte": [_sig("a", 1.0)]}, "2026-07-16")
    e = compute.merge_day_signals(e, {"signals_0dte": []}, "2026-07-16")
    assert e["signals_0dte"][0]["live"] is False
    e = compute.merge_day_signals(e, {"signals_0dte": [_sig("a", 3.0)]}, "2026-07-16")
    assert e["signals_0dte"][0]["live"] is True
    assert e["signals_0dte"][0]["credit"] == 3.0
    assert e["signals_0dte"][0].get("stale_since") in (None, "")


def test_merge_day_signals_resets_on_date_roll():
    prev = compute.merge_day_signals(None, {"signals_0dte": [_sig("a")]}, "2026-07-16")
    out = compute.merge_day_signals(prev, {"signals_0dte": [_sig("b")]}, "2026-07-17")
    assert out["date"] == "2026-07-17"
    assert {s["id"] for s in out["signals_0dte"]} == {"b"}   # yesterday dropped


def test_merge_day_signals_covers_all_three_lists():
    cur = {k: [_sig(f"{k}-1")] for k in _LISTS}
    out = compute.merge_day_signals(None, cur, "2026-07-16")
    for k in _LISTS:
        assert len(out[k]) == 1


def test_merge_day_signals_tolerates_malformed_prev():
    """A corrupt/foreign envelope must degrade to a fresh day, not raise."""
    for bad in ({}, {"date": "2026-07-16"}, {"date": "2026-07-16", "signals_0dte": "nope"},
                {"signals_0dte": [{"no_id": 1}]}):
        out = compute.merge_day_signals(bad, {"signals_0dte": [_sig("a")]}, "2026-07-16")
        assert [s["id"] for s in out["signals_0dte"]] == ["a"]


def test_merge_day_signals_skips_signals_without_an_id():
    out = compute.merge_day_signals(None, {"signals_0dte": [{"symbol": "SPY"}]}, "2026-07-16")
    assert out["signals_0dte"] == []


def test_merge_day_signals_does_not_mutate_inputs():
    cur = {"signals_0dte": [_sig("a")]}
    compute.merge_day_signals(None, cur, "2026-07-16")
    assert "live" not in cur["signals_0dte"][0]
```

### Step 2: Run to verify they fail

```powershell
.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -q -k merge_day
```
Expected: FAIL — `AttributeError: module 'compute' has no attribute 'merge_day_signals'`.

### Step 3: Implement

Add to `services/options_svc/compute.py` (near `run_scan`, module level — **pure, no I/O**):

```python
# ── Day-persistent scan union ───────────────────────────────────────────────
# The Scanner table shows the DAY's signals, not just the last scan's. This is
# published to its own key (cache:options:scan_day); cache:options:scan keeps
# its live-only semantics because the autonomous driver reads it and must never
# be offered a signal that no longer qualifies.

_DAY_LISTS = ("signals_0dte", "signals_swing", "signals_directional")


def merge_day_signals(prev, current, today, now_iso=None):
    """Merge one scan's signals into the day's accumulated union. PURE.

    ``prev``    -- the previous ``{date, signals_*}`` envelope (or None/garbage).
    ``current`` -- the fresh scan dict (the engine result / ScanResult dump).
    ``today``   -- local date string 'YYYY-MM-DD'.

    Per signal, keyed on the engine's unique ``id``:
      * present in ``current``  -> take it FRESH, ``live=True`` (still qualifying,
        and the numbers cost nothing -- the engine just computed them),
      * absent from ``current`` -> carry the last-seen copy forward FROZEN,
        ``live=False`` + ``stale_since`` stamped once.

    A ``date`` mismatch (or an unusable ``prev``) resets the day wholesale --
    the same auto-reset-at-date-roll contract push_notify's seen-set uses.
    Signals with no ``id`` are dropped (they cannot be tracked across scans).
    Never mutates its inputs; never raises.
    """
    import copy
    import datetime as dt

    now_iso = now_iso or dt.datetime.now().isoformat(timespec="seconds")

    if not isinstance(prev, dict) or prev.get("date") != today:
        prev = {}

    out = {"date": today}
    for key in _DAY_LISTS:
        cur_list = current.get(key) if isinstance(current, dict) else None
        cur_list = cur_list if isinstance(cur_list, list) else []
        cur_by_id = {s["id"]: s for s in cur_list
                     if isinstance(s, dict) and s.get("id")}

        prev_list = prev.get(key)
        prev_list = prev_list if isinstance(prev_list, list) else []

        merged = []
        seen = set()
        # Carried-forward first (stable order: oldest first, newcomers appended).
        for s in prev_list:
            if not isinstance(s, dict) or not s.get("id") or s["id"] in seen:
                continue
            sid = s["id"]
            seen.add(sid)
            if sid in cur_by_id:
                fresh = copy.deepcopy(cur_by_id[sid])
                fresh["live"] = True
                fresh["stale_since"] = None
                fresh["first_seen"] = s.get("first_seen") or now_iso
                merged.append(fresh)
            else:
                kept = copy.deepcopy(s)
                kept["live"] = False
                kept.setdefault("first_seen", now_iso)
                if not kept.get("stale_since"):
                    kept["stale_since"] = now_iso
                merged.append(kept)
        for sid, s in cur_by_id.items():
            if sid in seen:
                continue
            fresh = copy.deepcopy(s)
            fresh["live"] = True
            fresh["stale_since"] = None
            fresh["first_seen"] = now_iso
            merged.append(fresh)
        out[key] = merged
    return out
```

### Step 4: Run to verify they pass

```powershell
.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -q -k merge_day
```
Expected: PASS (11 tests).

### Step 5: Commit

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(options): add the pure day-union merge for scanner signals

Live signals take the fresh scan's numbers; dropped-out signals freeze at
last-seen and get stamped stale. Date-scoped, resetting wholesale at the
date roll -- the same contract push_notify's seen-set already uses."
```

---

## Task 6: Publish `cache:options:scan_day`

**Files:**
- Modify: `services/options_svc/handlers.py:112` (cache keys), `264-286` (`rescan`)
- Test: `services/options_svc/tests/test_handlers.py`

### Step 1: Write the failing tests

```python
def test_rescan_publishes_the_day_union(bus, monkeypatch):
    monkeypatch.setattr(compute, "run_scan", lambda: {
        "signals_0dte": [{"id": "a", "type": "PCS", "symbol": "SPY", "credit": 1.0}],
        "signals_swing": [], "signals_directional": [],
        "timestamp": "2026-07-16T10:00:00", "errors": [], "warnings": [],
        "vix_term_structure": {},
    })
    handlers.rescan(bus)
    day = bus.cache_get(handlers.CACHE_SCAN_DAY)
    assert [s["id"] for s in day["signals_0dte"]] == ["a"]
    assert day["signals_0dte"][0]["live"] is True


def test_rescan_day_union_accumulates_across_scans(bus, monkeypatch):
    monkeypatch.setattr(compute, "run_scan", lambda: {
        "signals_0dte": [{"id": "a", "type": "PCS", "symbol": "SPY"}],
        "signals_swing": [], "signals_directional": [],
        "timestamp": "t", "errors": [], "warnings": [], "vix_term_structure": {},
    })
    handlers.rescan(bus)
    monkeypatch.setattr(compute, "run_scan", lambda: {
        "signals_0dte": [{"id": "b", "type": "PCS", "symbol": "QQQ"}],
        "signals_swing": [], "signals_directional": [],
        "timestamp": "t", "errors": [], "warnings": [], "vix_term_structure": {},
    })
    handlers.rescan(bus)

    live = bus.cache_get(handlers.CACHE_SCAN)
    day = bus.cache_get(handlers.CACHE_SCAN_DAY)
    # THE load-bearing assertion: the live key is replaced, the day key accumulates.
    assert [s["id"] for s in live["signals_0dte"]] == ["b"]
    assert {s["id"] for s in day["signals_0dte"]} == {"a", "b"}
    frozen = next(s for s in day["signals_0dte"] if s["id"] == "a")
    assert frozen["live"] is False


def test_rescan_day_union_failure_does_not_break_the_live_publish(bus, monkeypatch):
    monkeypatch.setattr(compute, "run_scan", lambda: {
        "signals_0dte": [{"id": "a", "type": "PCS", "symbol": "SPY"}],
        "signals_swing": [], "signals_directional": [],
        "timestamp": "t", "errors": [], "warnings": [], "vix_term_structure": {},
    })
    monkeypatch.setattr(compute, "merge_day_signals",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    handlers.rescan(bus)
    assert bus.cache_get(handlers.CACHE_SCAN)["signals_0dte"][0]["id"] == "a"
```

### Step 2: Run to verify they fail

```powershell
.venv\Scripts\python -m pytest services\options_svc\tests\test_handlers.py -q -k day_union
```
Expected: FAIL — `CACHE_SCAN_DAY` does not exist.

### Step 3: Implement

Next to `CACHE_SCAN` (line ~112) add:

```python
CACHE_SCAN_DAY = "cache:options:scan_day"
EVENT_SCAN_DAY = "events:options:scan_day"
```

In `rescan`, after the existing live publish (`bus.publish(EVENT_SCAN, ...)`) and **before** the push-notify block:

```python
    # Day-persistent union for the Scanner page. A SEPARATE key on purpose:
    # cache:options:scan stays live-only because the autonomous driver reads it
    # and must never be offered a signal that no longer qualifies. Best-effort --
    # a merge failure must not break the live publish above.
    try:
        import datetime as dt
        today = dt.datetime.now().strftime("%Y-%m-%d")
        day = compute.merge_day_signals(
            bus.cache_get(CACHE_SCAN_DAY), scan.model_dump(), today)
        day_version = bus.cache_set(CACHE_SCAN_DAY, day)
        bus.publish(EVENT_SCAN_DAY, {"version": day_version})
    except Exception:  # noqa: BLE001
        log.exception("scan day-union merge failed (non-fatal)")
```

### Step 4: Run to verify they pass

```powershell
.venv\Scripts\python -m pytest services\options_svc -q
```
Expected: PASS, whole suite green.

### Step 5: Commit

```bash
git add services/options_svc/handlers.py services/options_svc/tests/test_handlers.py
git commit -m "feat(options): publish the day-persistent scan union

Second key, not a change to cache:options:scan -- the driver reads that one
and must keep seeing only currently-qualifying signals."
```

---

## Task 7: Scanner page — Directional tab + day-union read + New rework

This is the largest task; do the steps in order. **Read `webgui/pages/options/scanner.py` end-to-end first.**

**Files:**
- Modify: `webgui/pages/options/scanner.py`
- Test: `webgui/tests/test_options_scanner.py`

**Two behaviors being changed at once (deliberately — they touch the same `_populate`):**
1. Read `options:scan_day` instead of `options:scan`; add the Directional tab.
2. Replace the version-memoized `_NEW` tracker with an **id-keyed, unseen-since-viewed** seen-set — which also **fixes the collapsed-key bug** (`_sig_key` at line 143 is fed display rows from `signal_rows`, which merge both strikes into one `strikes` field, so every key degrades to `SPY|PCS|None|None|07/17`).

### Step 1: Write the failing tests

```python
def test_sig_key_bug_regression_rows_at_different_strikes_are_distinct():
    """REGRESSION: keys must not collapse for same symbol/type/expiry.

    The old _sig_key read short_strike/long_strike off DISPLAY rows, which
    signal_rows merges into one `strikes` field -- so every key became
    'SPY|PCS|None|None|07/17' and a new signal at different strikes went
    unmarked whenever anything on that symbol/type/expiry was already there.
    """
    rows = scanner.signal_rows([
        {"id": "SPY_PCS_2026-07-17_440_435", "symbol": "SPY", "type": "PCS",
         "expiration": "2026-07-17", "short_strike": 440, "long_strike": 435},
        {"id": "SPY_PCS_2026-07-17_430_425", "symbol": "SPY", "type": "PCS",
         "expiration": "2026-07-17", "short_strike": 430, "long_strike": 425},
    ])
    keys = {scanner._sig_key(r) for r in rows}
    assert len(keys) == 2, "distinct signals must not share a key"


def test_unseen_ids_marks_everything_on_a_cold_start():
    scanner._reset_seen_state()
    assert scanner.unseen_ids({"a", "b"}, "2026-07-16") == {"a", "b"}


def test_unseen_ids_clears_after_acknowledge():
    scanner._reset_seen_state()
    scanner.unseen_ids({"a", "b"}, "2026-07-16")
    scanner.acknowledge_ids({"a", "b"}, "2026-07-16")
    assert scanner.unseen_ids({"a", "b"}, "2026-07-16") == set()


def test_unseen_ids_flags_only_the_newcomer():
    scanner._reset_seen_state()
    scanner.acknowledge_ids({"a"}, "2026-07-16")
    assert scanner.unseen_ids({"a", "b"}, "2026-07-16") == {"b"}


def test_unseen_ids_resets_on_date_roll():
    scanner._reset_seen_state()
    scanner.acknowledge_ids({"a"}, "2026-07-16")
    assert scanner.unseen_ids({"a"}, "2026-07-17") == {"a"}


def test_signal_rows_marks_stale_from_the_live_flag():
    rows = scanner.signal_rows([
        {"id": "a", "symbol": "SPY", "type": "PCS", "live": True},
        {"id": "b", "symbol": "QQQ", "type": "CCS", "live": False,
         "stale_since": "2026-07-16T11:00:00"},
    ])
    by_id = {r["id"]: r for r in rows}
    assert by_id["a"]["_stale"] is False
    assert by_id["b"]["_stale"] is True
    assert by_id["b"]["_row_class"]          # dimmed treatment applied


def test_signal_rows_absent_live_flag_is_not_stale():
    """Back-compat: a payload from cache:options:scan carries no `live` key."""
    rows = scanner.signal_rows([{"id": "a", "symbol": "SPY", "type": "PCS"}])
    assert rows[0]["_stale"] is False


def test_stamp_new_keys_off_id():
    rows = [{"id": "a"}, {"id": "b"}]
    scanner.stamp_new(rows, {"a"})
    assert rows[0]["_new"] is True and rows[1]["_new"] is False
```

### Step 2: Run to verify they fail

```powershell
cd webgui; ..\.venv\Scripts\python -m pytest tests/test_options_scanner.py -q -k "unseen or stale or sig_key or stamp_new"
```
Expected: FAIL.

### Step 3: Implement the pure helpers

In `webgui/pages/options/scanner.py`:

Replace `_sig_key` (line 143-145):

```python
def _sig_key(r):
    """Stable identity for a signal across scans.

    The engine already mints a unique ``id`` (``{symbol}_{side}_{exp}_{short}_{long}``),
    so use it. The previous implementation rebuilt a composite key from
    short_strike/long_strike -- fields that ``signal_rows`` merges into a single
    ``strikes`` cell -- so every key collapsed to 'SPY|PCS|None|None|07/17' and
    distinct signals shared one identity.
    """
    return r.get("id")
```

Replace the `_NEW` block (lines 148-175) with:

```python
# Seen-signal tracking. "New" means UNSEEN SINCE YOU LAST VIEWED THIS PAGE --
# so stepping away for hours never loses a marker (the old version-memoized
# tracker cleared on the next scan whether or not anyone looked). Module level
# (single-user, like _NAV_OPEN/_CACHE) so the marks survive navigation. Date
# scoped so the day roll starts clean. NOTE: this is page-side state -- a webgui
# restart re-marks everything New. The day's SIGNALS survive a restart (they are
# in Redis); only the read-marks do not. Accepted: pushing GUI read-state
# server-side is not worth the machinery.
_SEEN = {"date": None, "ids": set()}


def _reset_seen_state():
    """Clear the module-level seen tracker (test seam / fresh session)."""
    _SEEN.update(date=None, ids=set())


def _roll_seen(today):
    if _SEEN["date"] != today:
        _SEEN.update(date=today, ids=set())


def unseen_ids(current_ids, today):
    """Ids not yet seen. Does NOT mark them -- call ``acknowledge_ids`` after."""
    _roll_seen(today)
    return set(current_ids) - _SEEN["ids"]


def acknowledge_ids(current_ids, today):
    """Mark ids as seen. Call AFTER snapshotting ``unseen_ids``."""
    _roll_seen(today)
    _SEEN["ids"] |= set(current_ids)
```

In `signal_rows` (line ~123), add to each row dict:

```python
            "_stale": s.get("live") is False,
            "_row_class": "opacity-50" if s.get("live") is False else "",
            "stale_since": _short_time(s.get("stale_since")),
```

> `live is False` — **not** `not s.get("live")`. A payload from `cache:options:scan` has no `live` key at all and must not read as stale.

Add a `stale_since` column to `signal_columns()` after `grade`:

```python
        ("stale_since", "Dropped"),
```

### Step 4: Wire the Directional tab + day-union read

In `render()`:
- Add a third tab beside `tab_0dte` / `tab_swing`:
  ```python
  tab_directional = ui.tab("Directional")
  ```
  and a matching `ui.tab_panel` holding
  `table_dir = ui.table(columns=strategy_table.strategy_columns(), rows=[], row_key="id")`
  (import `from pages.options import strategy_table`).
- Give `table_dir` the same `rowClick` + `handoff.add_strategy_row_actions` wiring the Swing page uses (**not** `add_row_actions` — directional signals carry the strategy shape), plus the `_score_class` / `_new` slots and the `_undefined_risk` badge slot from Task 1.
- Apply the dimmed treatment via a `:class` binding on each table:
  ```python
  _t.props(':table-row-class-fn', 'row => row._row_class')
  ```
  (verify the exact Quasar prop name against the installed version; the fallback is a `body-cell-symbol` slot class.)
- In `_populate`, read all **three** lists, build `by_id` across all three, and rework the marking:

```python
        import datetime as dt
        today = dt.datetime.now().strftime("%Y-%m-%d")
        rows_dir = strategy_table.strategy_rows(results.get("signals_directional"))
        all_ids = ({r["id"] for r in rows_0dte} | {r["id"] for r in rows_swing}
                   | {r["id"] for r in rows_dir}) - {None}
        # Snapshot BEFORE acknowledging -- acknowledge first and nothing is ever New.
        new_ids = unseen_ids(all_ids, today)
        for rs in (rows_0dte, rows_swing, rows_dir):
            stamp_new(rs, new_ids)
        acknowledge_ids(all_ids, today)
```

- Change the two cache reads from `"options:scan"` to `"options:scan_day"` (initial paint at line 366, and `_maybe_repaint` at line 375).

> **OBLIGATION — gate the render on the envelope's date.** `rescan`'s merge is
> best-effort: on failure it leaves `cache:options:scan_day` **untouched**, which
> is correct (writing an empty envelope would destroy the day's data) — but the
> consequence is **stale, not absent**. If it throws on the first scan of a new
> day, the key still holds *yesterday's* envelope, **including signals stamped
> `live=True` from yesterday's 15:15 scan**. A page that renders it blind will
> present day-old signals as live and tradeable.
>
> So: read `payload["date"]` and **render nothing (or an explicit "waiting for
> today's scan") unless it equals today in CT**. The envelope carries `date`
> precisely so this is checkable. **Pin it with a test** — feed a yesterday-dated
> envelope containing `live=True` signals and assert the table does not show them.

> **Ordering is load-bearing.** `unseen_ids` must be snapshotted before `acknowledge_ids`. A test above pins it.

> The `status_line(results)` bottom bar reads `timestamp`/`errors`/`warnings`, which the day envelope does **not** carry. Either add them to the envelope in `merge_day_signals` (pass through from `current`) or keep a cheap read of `options:scan` for the status line only. **Prefer the former** — one read, one version to poll.

### Step 5: Run the suite

```powershell
cd webgui; ..\.venv\Scripts\python -m pytest . -q
```
Expected: **≥772** green, including `tests/test_no_inline_style.py`.

### Step 6: Commit

```bash
git add webgui/pages/options/scanner.py webgui/tests/test_options_scanner.py
git commit -m "feat(scanner): directional tab + day-persistent signals + fixed New marker

The table now reads the day union, so the day's signals stay visible with
dropped-out ones dimmed and frozen. 'New' now means unseen since you last
viewed the page rather than absent-from-the-last-scan, so stepping away no
longer loses markers.

Also fixes the New marker outright: _sig_key rebuilt a key from
short_strike/long_strike, but signal_rows merges both into one `strikes`
cell -- so every key collapsed to 'SPY|PCS|None|None|07/17' and a new signal
at different strikes went unmarked whenever anything on that symbol/type/
expiry was already present. Key off the engine's unique id instead.
"
```

---

## Task 8: Live verification (Redis-driven, not the browser)

Per the documented practice, the reliable end-to-end check for a 3-tier page is Redis-driven — the browser screenshot tool times out on this app's heavy pages.

**Steps:**

1. Restart the service so it picks up the engine + handler changes (the running one is stale):
   ```powershell
   .venv\Scripts\python services\options_svc\app.py
   ```
2. Drive a real scan and inspect BOTH keys:
   ```powershell
   .venv\Scripts\python -c "
   import sys; sys.path.insert(0, '.')
   from shared.bus import Bus
   b = Bus()
   b.enqueue_command('cmd:options', {'type': 'rescan'})
   "
   ```
   Wait for it to finish, then:
   ```powershell
   .venv\Scripts\python -c "
   import sys; sys.path.insert(0, '.')
   from shared.bus import Bus
   b = Bus()
   live = b.cache_get('cache:options:scan') or {}
   day  = b.cache_get('cache:options:scan_day') or {}
   print('live 0dte/swing/dir:', len(live.get('signals_0dte',[])), len(live.get('signals_swing',[])), len(live.get('signals_directional',[])))
   print('day  0dte/swing/dir:', len(day.get('signals_0dte',[])), len(day.get('signals_swing',[])), len(day.get('signals_directional',[])))
   d = day.get('signals_directional') or []
   print('directional types:', sorted({s.get('type') for s in d}))
   print('sample:', {k: d[0].get(k) for k in ('id','type','max_profit','max_loss','unbounded_loss','composite_score','grade','live')} if d else 'none')
   "
   ```

**Acceptance:**
- `signals_directional` is populated with types drawn from `{LONG_CALL, LONG_PUT, SHORT_CALL, SHORT_PUT}`, each with a `composite_score` and a `grade`.
- Every entry in `live.signals_0dte` / `live.signals_swing` is still `PCS`/`CCS`/`IC`.
- A **second** `rescan` accumulates the day key while the live key is replaced (the key property — compare counts across two runs).
- A `SHORT_CALL`, if present, has `unbounded_loss: True` and a finite `max_profit`.

> **Off-hours caveat:** weekends/off-hours give sparse-to-empty chains, so an empty `signals_directional` is **not** a failure — it is the documented degraded case. Verify during RTH, or accept the shape check alone and say so plainly rather than claiming a pass you did not observe.

3. Restart the webgui and confirm the Scanner page renders three tabs, the Directional table populates, and dropped-out rows dim. Read the page via DOM eval if the screenshot tool hangs.

4. Update the root `CLAUDE.md` "Last updated" entry + the `/` route row in the route table (per the standing maintenance requirement), then commit.

---

## Definition of done

- [ ] All five suites at or above baseline (options-scanner 1260/15 pre-existing set unchanged; webgui ≥786; options_svc, contracts, driver_svc green).
- [ ] `ruff check` clean.
- [ ] Directional signals appear in their own tab, scored by Fit+Quality, never mixed into the credit-spread tables.
- [ ] A naked short renders `Max P` = its credit and `Max L` = `∞` with an undefined-risk badge, and cannot be paper-traded.
- [ ] The driver's menu is provably free of directional signals (Task 4 tests).
- [ ] The day's signals persist across scans; dropped-out ones are dimmed + frozen; live ones show fresh numbers.
- [ ] New marks survive stepping away and clear on page view.
- [ ] Root `CLAUDE.md` updated. `options_svc` + webgui restarted.
