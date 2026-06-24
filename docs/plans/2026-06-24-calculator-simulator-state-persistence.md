# Calculator + Simulator State Persistence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `/options/calculator` and `/options/simulator` restore their full UI
state (symbol, strategy, legs, fields, sliders, active tab) when you navigate back,
then auto-refresh the result against current market data.

**Architecture:** A single-user module-level snapshot per page (`_LAST_CALC` /
`_LAST_SIM`), updated on every input change and restored on `render()`. Restore runs
under a `restoring` guard (so wiring fires no stray commands), reuses each page's
existing `pending_legs` hook to push the restored legs through the chain/meta load
(it already beats the template re-seed), then triggers the existing fetch→apply→run
cascade so the re-run uses the restored inputs. Pure precedence/merge helpers live in
a new `page_state.py`. GUI-tier only — no service/contract/engine changes.

**Tech Stack:** NiceGUI, Redis (Memurai) via `bus_client`, pytest.

**Design doc:** [`docs/plans/2026-06-24-calculator-simulator-state-persistence-design.md`](2026-06-24-calculator-simulator-state-persistence-design.md)

---

## Conventions for every task

- TDD pure helpers first (@superpowers:test-driven-development). UI wiring has no
  render-harness here (render is screenshot-verified per `webgui/CLAUDE.md`), so wiring
  steps end in a **smoke/Redis/browser** verification, not a unit test.
- Run webgui tests from inside `webgui/`: `cd webgui && ..\.venv\Scripts\python -m pytest -q`.
- End every commit message with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.
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
    # A present key wins; a missing key falls back to the default.
    assert ps.merge_restore({"symbol": "AAPL", "dt": 12.0}, defaults) == {
        "symbol": "AAPL", "strategy": "PCS", "dt": 12.0}


def test_merge_restore_none_or_empty_is_defaults():
    defaults = {"symbol": "SPY", "dt": 5.0}
    assert ps.merge_restore(None, defaults) == defaults
    assert ps.merge_restore({}, defaults) == defaults
    assert ps.merge_restore(None, defaults) is not defaults     # fresh copy


def test_merge_restore_ignores_stale_unknown_keys():
    # A snapshot from an older build with a removed field doesn't leak through.
    assert ps.merge_restore({"gone": 1, "symbol": "QQQ"}, {"symbol": "SPY"}) == {"symbol": "QQQ"}


def test_snapshot_whitelists_keys():
    vals = {"symbol": "SPY", "dt": 5.0, "_widget": object()}
    assert ps.snapshot(vals, ("symbol", "dt")) == {"symbol": "SPY", "dt": 5.0}
    # Missing keys are simply omitted (no KeyError).
    assert ps.snapshot({"symbol": "SPY"}, ("symbol", "dt")) == {"symbol": "SPY"}


def test_pick_seed_precedence():
    assert ps.pick_seed(handoff={"x": 1}, last={"y": 2}) == "handoff"
    assert ps.pick_seed(handoff=None, last={"y": 2}) == "restore"
    assert ps.pick_seed(handoff=None, last=None) == "default"
    assert ps.pick_seed(handoff={}, last={}) == "default"     # empty == absent
```

**Step 2: Run it to verify it fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_page_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pages.options.page_state'`.

**Step 3: Write the minimal implementation**

```python
# webgui/pages/options/page_state.py
"""Pure helpers for per-page UI-state persistence across navigation (Tier-1).

The Calculator + Simulator keep a single-user module-level snapshot of their inputs
and restore it on render (see the page modules). These helpers are the pure,
unit-tested core: whitelist a snapshot, overlay it on defaults, and resolve the seed
precedence (an explicit cross-page handoff copy beats the persisted snapshot, which
beats the cold defaults)."""


def snapshot(values: dict, keys) -> dict:
    """Pick exactly ``keys`` from ``values`` into a fresh dict (the persisted state).

    Missing keys are omitted (not an error); junk/widget refs in ``values`` are
    dropped by virtue of the whitelist."""
    return {k: values[k] for k in keys if k in values}


def merge_restore(snap: dict | None, defaults: dict) -> dict:
    """Overlay a (possibly partial / stale) ``snap`` on ``defaults``.

    Every default key is present in the result; only keys that exist in ``defaults``
    are taken from ``snap`` (a stale snapshot from an older build can't leak a removed
    field). Returns a fresh dict (never the ``defaults`` object)."""
    out = dict(defaults)
    if snap:
        out.update({k: v for k, v in snap.items() if k in defaults})
    return out


def pick_seed(handoff, last) -> str:
    """Seed precedence → 'handoff' | 'restore' | 'default'.

    An explicit Copy-to-Calculator/Simulator handoff is a fresh intent and wins over
    the persisted snapshot; the snapshot wins over cold defaults. Empty == absent."""
    if handoff:
        return "handoff"
    if last:
        return "restore"
    return "default"
```

**Step 4: Run the tests to verify they pass**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_page_state.py -q`
Expected: PASS (5 tests).

**Step 5: Commit**

```bash
git add webgui/pages/options/page_state.py webgui/tests/test_page_state.py
git commit -m "feat(options): pure helpers for page-state persistence (snapshot/merge/precedence)"
```

---

## Task 2: Simulator persistence + auto-refresh

**Files:**
- Modify: `webgui/pages/options/simulator.py` (render: `state` dict ~272, `_apply_meta`
  ~533, initial paint ~582-606, handler wiring ~518-530)
- Test: `webgui/tests/test_options_simulator.py`

The Simulator already auto-fetches + auto-runs on meta arrival, and `_apply_meta`
already has a `pending_legs` branch that beats the template re-seed. So persistence =
(a) snapshot the inputs on change, (b) on render restore the widgets + stash legs as
`pending_legs`, then re-fetch (auto-refresh), (c) suppress handlers during restore.

**Step 1: Write the failing test (capture-key coverage)**

```python
# add to webgui/tests/test_options_simulator.py
from pages.options import page_state as ps


def test_sim_capture_keys_cover_inputs():
    # Guard against forgetting to persist a Simulator input.
    assert set(sim._SIM_KEYS) == {
        "symbol", "strategy", "legs", "dt", "mult", "lookback", "ds", "active_tab"}


def test_sim_snapshot_roundtrips_via_page_state():
    vals = {"symbol": "AAPL", "strategy": "IC", "legs": [{"option_type": "put"}],
            "dt": 7.0, "mult": 2.0, "lookback": "5m_3d", "ds": -3.0, "active_tab": "What-if",
            "junk": 1}
    snap = ps.snapshot(vals, sim._SIM_KEYS)
    assert "junk" not in snap and snap["dt"] == 7.0
    assert ps.merge_restore(snap, sim._SIM_DEFAULTS)["symbol"] == "AAPL"
```

**Step 2: Run it to verify it fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_options_simulator.py -k sim_capture -q`
Expected: FAIL — `AttributeError: module 'pages.options.simulator' has no attribute '_SIM_KEYS'`.

**Step 3: Implement — module state + capture/restore wiring**

3a. Add module-level constants + state near the top of `simulator.py` (after the
imports / color constants, module scope — NOT inside `render`):

```python
from . import page_state as _ps   # pure snapshot/merge/precedence helpers

# Persisted (single-user) Simulator input snapshot — survives navigation + browser
# reload, resets on a webgui restart (same as the other persisting pages).
_SIM_KEYS = ("symbol", "strategy", "legs", "dt", "mult", "lookback", "ds", "active_tab")
_SIM_DEFAULTS = {"symbol": "SPY", "strategy": "PCS", "legs": [], "dt": 5.0,
                 "mult": 1.5, "lookback": "auto", "ds": 0.0, "active_tab": "Replay"}
_LAST_SIM: dict = {}
```

3b. Inside `render`, add `"restoring": False` to the `state` dict (~272).

3c. After the editor is built and all widgets exist (just before the `# ── render
figures` block, ~373), add capture + restore helpers:

```python
    def _capture():
        if state.get("restoring"):
            return
        _LAST_SIM.clear()
        _LAST_SIM.update(_ps.snapshot({
            "symbol": (symbol_in.value or "").strip().upper(),
            "strategy": strategy_sel.value,
            "legs": editor.get_legs(),
            "dt": float(dt_slider.value), "mult": float(mult_slider.value),
            "lookback": lookback_sel.value, "ds": float(ds_slider.value),
            "active_tab": tabs.value,
        }, _SIM_KEYS))

    def _restore(snap):
        """Apply a persisted snapshot to the widgets under the restoring guard, then
        re-fetch (auto-refresh). Legs ride ``pending_legs`` so ``_apply_meta`` applies
        them (beating the template) and runs with the restored sliders."""
        s = _ps.merge_restore(snap, _SIM_DEFAULTS)
        state["restoring"] = True
        try:
            symbol_in.value = s["symbol"]
            strategy_sel.value = s["strategy"]
            dt_slider.value = s["dt"]; mult_slider.value = s["mult"]
            lookback_sel.value = s["lookback"]; ds_slider.value = s["ds"]
            tabs.value = s["active_tab"]
            state["pending_legs"] = s["legs"] or None
        finally:
            state["restoring"] = False
```

3d. Guard the command-firing handlers so a restore (which assigns widget values)
doesn't enqueue. In `_on_legs_changed`, `_enqueue_run`, `_enqueue_replay`,
`_slider_changed` add an early return at the very top:

```python
        if state.get("restoring"):
            return
```

(The strategy `on_value_change` calls `editor.apply_template(...)` + `_on_legs_changed`
— guard it where wired, see 3e.)

3e. Update the handler wiring (~518-530) to ALSO capture, and to no-op the template
re-seed while restoring:

```python
    fetch_btn.on_click(_request_fetch)
    strategy_sel.on_value_change(lambda e: None if state.get("restoring")
                                 else (editor.apply_template(strategy_sel.value),
                                       _on_legs_changed(), _capture()))
    ds_slider.on_value_change(lambda e: (_render_figures(), _capture()))
    dt_slider.on_value_change(lambda e: (_slider_changed(), _capture()))
    mult_slider.on_value_change(lambda e: (_slider_changed(), _capture()))
    scrub_slider.on_value_change(lambda e: _render_replay())
    lookback_sel.on_value_change(lambda e: (_enqueue_replay(), _capture()))
    symbol_in.on_value_change(lambda e: _capture())
    tabs.on_value_change(lambda e: (ui.timer(0.05, _reflow_charts, once=True), _capture()))
```

Also pass `_capture` into the leg editor's `on_change` so leg edits persist. Change the
`build_leg_editor(... on_change=lambda: _on_legs_changed() ...)` call (~362) to:

```python
        show_premium=False, on_change=lambda: (_on_legs_changed(), _capture()), header=True,
```

3f. Replace the initial-paint + handoff block (~582-606) so restore wins over stale
meta but loses to an explicit handoff copy:

```python
    # Initial paint: paint the cached result instantly (no empty flash), then restore.
    state["result_ver"] = bus_client.read_version("options:sim_result")
    state["result"] = bus_client.read("options:sim_result") or None
    _render_figures()
    state["replay_ver"] = bus_client.read_version("options:sim_replay")
    state["replay"] = bus_client.read("options:sim_replay") or None
    _render_replay()
    state["meta_ver"] = bus_client.read_version("options:sim_meta")

    ui.timer(2.0, _poll_meta)
    ui.timer(2.0, _poll_result)
    ui.timer(2.0, _poll_replay)
    ui.timer(0.4, _flush_pending)

    # Seed precedence: handoff copy > persisted snapshot > SPY/PCS defaults.
    p = handoff.take_pending_simulator()
    seed = _ps.pick_seed(p, _LAST_SIM)
    if seed == "handoff":
        symbol_in.value = p.get("symbol") or symbol_in.value
        state["pending_legs"] = p.get("legs") or []
        _request_fetch()
    elif seed == "restore":
        _restore(_LAST_SIM)
        _request_fetch()        # auto-refresh: re-fetch + re-run with restored legs/sliders
    # else: cold defaults — leave the seeded PCS template; user clicks Fetch.
```

(Delete the old lines that read+applied the stale `sim_meta` on mount — restore +
re-fetch replaces them. Keep `meta_ver` tracking so `_poll_meta` still works.)

**Step 4: Run the tests**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_options_simulator.py -q`
Expected: PASS (existing + 2 new).

**Step 5: Commit**

```bash
git add webgui/pages/options/simulator.py webgui/tests/test_options_simulator.py
git commit -m "feat(simulator): persist + restore full UI state across nav, auto-refresh on return"
```

---

## Task 3: Calculator persistence + auto-refresh

**Files:**
- Modify: `webgui/pages/options/calculator.py` (render `state` ~367, `_seed_template`
  wiring ~485, `_apply_chain` ~664, initial paint/handoff ~744-823)
- Test: `webgui/tests/test_calculator.py` (or the existing calculator test module)

Same shape as Task 2. The extra wrinkle: `_apply_chain` overwrites price (KEEP — that's
the current-market refresh) and range min/max (SKIP during restore so the user's range
survives). Calculator legs carry premiums, so the pending path must NOT re-fetch
premiums when the restored legs already have them.

**Step 1: Write the failing test (capture-key coverage)**

```python
# add to webgui/tests/test_calculator.py
from pages.options import calculator as calc
from pages.options import page_state as ps


def test_calc_capture_keys_cover_inputs():
    assert set(calc._CALC_KEYS) == {
        "symbol", "strategy", "legs", "iv", "rate", "ivadj", "contracts",
        "price", "range_min", "range_max", "range_pct", "expiry"}


def test_calc_snapshot_roundtrips_via_page_state():
    snap = ps.snapshot({"symbol": "MSFT", "strategy": "CCS", "legs": [], "iv": 25.0,
                        "rate": 4.5, "ivadj": 0.0, "contracts": 3, "price": 410.0,
                        "range_min": 0.0, "range_max": 0.0, "range_pct": 8.0,
                        "expiry": "2026-07-17", "junk": 1}, calc._CALC_KEYS)
    assert "junk" not in snap
    assert ps.merge_restore(snap, calc._CALC_DEFAULTS)["contracts"] == 3
```

**Step 2: Run it to verify it fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_calculator.py -k calc_capture -q`
Expected: FAIL — `AttributeError: ... has no attribute '_CALC_KEYS'`.

**Step 3: Implement**

3a. Module scope (top of `calculator.py`):

```python
from . import page_state as _ps

_CALC_KEYS = ("symbol", "strategy", "legs", "iv", "rate", "ivadj", "contracts",
              "price", "range_min", "range_max", "range_pct", "expiry")
_CALC_DEFAULTS = {"symbol": "SPY", "strategy": "PCS", "legs": [], "iv": 20.0,
                  "rate": 4.5, "ivadj": 0.0, "contracts": 1, "price": 100.0,
                  "range_min": 0.0, "range_max": 0.0, "range_pct": 5.0,
                  "expiry": None}
_LAST_CALC: dict = {}
```

3b. Add `"restoring": False` and keep `"restore_pending": False` in the render `state`
dict (~367).

3c. After all widgets + `editor` exist (~462), add:

```python
    def _capture():
        if state.get("restoring"):
            return
        _LAST_CALC.clear()
        _LAST_CALC.update(_ps.snapshot({
            "symbol": (symbol_in.value or "").strip().upper(),
            "strategy": strategy_sel.value, "legs": editor.get_legs(),
            "iv": iv_in.value, "rate": rate_in.value, "ivadj": ivchg_in.value,
            "contracts": int(contracts_in.value or 1), "price": price_in.value,
            "range_min": rmin_in.value, "range_max": rmax_in.value,
            "range_pct": rpct_in.value, "expiry": expiry_sel.value,
        }, _CALC_KEYS))

    def _restore(snap):
        s = _ps.merge_restore(snap, _CALC_DEFAULTS)
        state["restoring"] = True
        try:
            symbol_in.value = s["symbol"]; strategy_sel.value = s["strategy"]
            iv_in.value = s["iv"]; rate_in.value = s["rate"]; ivchg_in.value = s["ivadj"]
            contracts_in.value = s["contracts"]; state["contracts"] = s["contracts"]
            price_in.value = s["price"]
            rmin_in.value = s["range_min"]; rmax_in.value = s["range_max"]
            rpct_in.value = s["range_pct"]; rpct_val.set_text(f"{float(s['range_pct'] or 0):g}%")
            if s["expiry"]:
                expiry_sel.options = [s["expiry"]]; expiry_sel.value = s["expiry"]; expiry_sel.update()
            state["pending_legs"] = s["legs"] or None
            state["restore_pending"] = True     # _apply_chain keeps this range, applies legs, recomputes
        finally:
            state["restoring"] = False
```

3d. Guard command-firing handlers. `_seed_template` and `_on_contracts_change` already
exist — add at the top of each:

```python
        if state.get("restoring"):
            return
```

3e. Wire capture onto the inputs (extend existing handlers; add new ones):

```python
    strategy_sel.on_value_change(lambda e: (_seed_template(), _capture()))
    contracts_in.on_value_change(lambda e: (_on_contracts_change(), _capture()))
    for _w in (symbol_in, iv_in, rate_in, ivchg_in, price_in, rmin_in, rmax_in, expiry_sel):
        _w.on_value_change(lambda e: _capture())
    rpct_in.on_value_change(lambda e: _capture())   # in addition to the existing label updater
```

Pass `_capture` to the leg editor `on_change` (~461): `on_change=lambda: _capture()`.

3f. In `_apply_chain` (~664), keep the price refresh, gate the range overwrite on
restore, and skip premium-refetch when restored legs already carry premiums:

```python
    def _apply_chain(cc):
        cc = cc or {}
        state["chain"] = cc.get("chain")
        if cc.get("price"):
            price_in.value = round(cc["price"], 2)            # always refresh (current market)
        if (cc.get("range_lo") or cc.get("range_hi")) and not state.get("restore_pending"):
            rmin_in.value = round(cc.get("range_lo") or 0, 2)  # skip during restore — keep user's range
            rmax_in.value = round(cc.get("range_hi") or 0, 2)
        exps = chain_expiries(state["chain"] or {})
        expiry_sel.options = exps
        if exps and expiry_sel.value not in exps:
            expiry_sel.value = exps[0]
        expiry_sel.update()
        pending = state.pop("pending_legs", None)
        if pending:
            editor.set_legs(pending)
            if any(l.get("premium") in (None, 0) for l in pending):
                fetch_premiums()        # copy-from-Simulator legs have no premium; restore keeps theirs
            do_calc()
        elif not editor.is_dirty():
            _seed_template()
        else:
            editor.refresh_options()
        state["restore_pending"] = False
        if cc.get("symbol") is not None:
            price = cc.get("price")
            msg = f"{cc['symbol']}: {len(exps)} expiries" + (f", {price:.2f}" if price else "")
            ui.notify(msg, type="positive" if exps else "warning")
```

3g. After the existing handoff blocks (~813-823), add the restore fallback (it must run
ONLY when no handoff consumed the seed):

```python
    if not _pending and not _legs_in and _LAST_CALC:
        _restore(_LAST_CALC)
        load_symbol()       # auto-refresh: reload chain (fresh price) → _apply_chain applies legs + recomputes
```

**Step 4: Run the tests**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_calculator.py -q`
Expected: PASS (existing + 2 new).

**Step 5: Commit**

```bash
git add webgui/pages/options/calculator.py webgui/tests/test_calculator.py
git commit -m "feat(calculator): persist + restore full UI state across nav, auto-refresh on return"
```

---

## Task 4: Full-suite + end-to-end verification

@superpowers:verification-before-completion

**Step 1: Full webgui suite**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest -q`
Expected: all green (prior 441 + the new tests).

**Step 2: Restart the webgui** (the running one is stale; `reload=False`)

```powershell
Start-Process cmd -ArgumentList '/k call "D:\WebGUI Trading with Schwab\tools\restart_one.bat" 8500 0 "webgui\main.py"' -WorkingDirectory 'D:\WebGUI Trading with Schwab'
```
Confirm `GET http://127.0.0.1:8500/options/simulator` → 200.

**Step 3: Browser navigate-away/back check** (Claude Preview)

- Simulator: Fetch a non-default symbol (e.g. `AAPL`), pick a non-default strategy
  (e.g. `IC`), edit a leg qty, switch to the What-if tab, move Δt. Navigate to another
  page, come back. Assert: symbol=AAPL, strategy=IC, the edited legs, the What-if tab,
  and the Δt position are all restored, and a fresh sweep ran (chart populated).
- Calculator: Load a non-default symbol, change Contracts + range %, Calculate.
  Navigate away + back. Assert: symbol/strategy/legs/contracts/range % restored and the
  grid recomputed.
- Cross-check precedence: from the Calculator click **Copy to Simulator** → the
  Simulator shows the copied legs (handoff beats the persisted snapshot).

**Step 4: Redis spot-check** (bypasses the browser)

After a browser restore, read `Bus().cache_get("cache:options:sim_result")` /
`"cache:options:calc_result"` and confirm a fresh version landed (auto-refresh ran).

**Step 5: Commit (if any verification fixups were needed)**

```bash
git add -A && git commit -m "test: verify Calculator/Simulator state persistence end-to-end"
```

---

## Task 5: Update the architecture record (CLAUDE.md)

**Files:** Modify: `CLAUDE.md` (the `/options/calculator` + `/options/simulator`
route-table rows; prepend a new `**Last updated:**` entry).

**Step 1:** Add a `**Last updated:** 2026-06-24 (**Calculator + Simulator state
persistence**: ...)` entry summarizing the module-level `_LAST_CALC`/`_LAST_SIM`
snapshot + restore-on-render + auto-refresh + the `page_state.py` pure helpers; demote
the current top entry to `Prior —`.

**Step 2:** Append to both route-table rows: "persists full UI state across navigation
(symbol/strategy/legs/fields/sliders[/tab]) via a single-user module snapshot +
auto-refreshes on return".

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: note Calculator/Simulator state persistence"
```

---

## Done criteria

- `page_state.py` pure helpers unit-tested; full webgui suite green.
- Both pages restore symbol/strategy/legs/fields/sliders (+ Simulator tab) on
  navigate-back and auto-refresh the result; handoff copy still wins over the snapshot.
- Verified live in the browser + via Redis; CLAUDE.md updated.
