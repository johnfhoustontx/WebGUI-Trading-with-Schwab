# Calculator & Simulator UX changes — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Calculator/Simulator symbol fields Load on tab-out/Enter with a centered wait overlay; the Calculator's top-level Expiry propagates to all legs; leg-table cells are compact and `call`/`put` no longer clip; the "Actions" header is dropped.

**Architecture:** Pure Tier-1 (webgui) UI wiring — no Tier-2/service contract changes. New pure helpers (`should_load`, `set_legs_expiry`) are TDD'd; a new shared `overlay.py` builds the wait animation; the shared `leg_editor.py` + `theme.py` carry the leg-cell + header changes (both pages inherit them). Symbol triggers use `keydown.enter` + `focusout` (not `blur` — NiceGUI binds to the q-input root where `blur` doesn't bubble).

**Tech Stack:** NiceGUI (Python), pytest. Run tests with `cd webgui && ..\.venv\Scripts\python -m pytest -q`.

---

## Design reference

See [`docs/plans/2026-06-24-calculator-simulator-ux-changes-design.md`](2026-06-24-calculator-simulator-ux-changes-design.md).

Key files:
- `webgui/pages/options/inputs.py` — add `should_load`.
- `webgui/pages/options/leg_editor.py` — add `set_legs_expiry` + `apply_expiry`; leg-row class; widen Type; drop "Actions".
- `webgui/pages/options/theme.py` — `.leg-row` compact CSS.
- `webgui/pages/options/overlay.py` — **new** shared wait overlay.
- `webgui/pages/options/calculator.py` — symbol submit, overlay, expiry propagation.
- `webgui/pages/options/simulator.py` — symbol submit, overlay.
- Tests: `webgui/tests/test_inputs.py` (**new**), `webgui/tests/test_leg_editor.py`.

---

## Task 1: `should_load` symbol-load dedup helper (pure)

**Files:**
- Modify: `webgui/pages/options/inputs.py`
- Test: `webgui/tests/test_inputs.py` (create)

**Step 1: Write the failing test**

Create `webgui/tests/test_inputs.py`:

```python
from pages.options.inputs import should_load


def test_should_load_true_for_new_symbol():
    assert should_load("AAPL", None) is True
    assert should_load("MSFT", "AAPL") is True


def test_should_load_false_when_unchanged_or_empty():
    assert should_load("AAPL", "AAPL") is False   # same as already loaded
    assert should_load("", "AAPL") is False        # empty
    assert should_load(None, "AAPL") is False      # None
```

**Step 2: Run test to verify it fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_inputs.py -q`
Expected: FAIL (`ImportError: cannot import name 'should_load'`).

**Step 3: Write minimal implementation**

Append to `webgui/pages/options/inputs.py`:

```python
def should_load(current, last_loaded):
    """True when a tab-out / Enter should (re)trigger Load: a non-empty symbol that
    differs from the one already loaded. The Load / Fetch BUTTON bypasses this and
    always loads (e.g. to refresh price); this only gates the symbol-field triggers
    so tabbing through an unchanged symbol won't re-fetch."""
    return bool(current) and current != last_loaded
```

**Step 4: Run test to verify it passes**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_inputs.py -q`
Expected: PASS (2 tests).

**Step 5: Commit**

```bash
git add webgui/pages/options/inputs.py webgui/tests/test_inputs.py
git commit -m "feat(options): should_load symbol-field dedup helper"
```

---

## Task 2: `set_legs_expiry` + editor `apply_expiry` (expiry → all legs)

**Files:**
- Modify: `webgui/pages/options/leg_editor.py`
- Test: `webgui/tests/test_leg_editor.py`

**Step 1: Write the failing tests**

Append to `webgui/tests/test_leg_editor.py`:

```python
def test_set_legs_expiry_sets_every_leg():
    legs = [{"option_type": "call", "side": "long", "strike": 100,
             "expiry": "2026-07-17", "qty": 1, "premium": 2.5},
            {"option_type": "put", "side": "short", "strike": 95,
             "expiry": "2026-08-21", "qty": 2, "premium": 1.0}]
    out = LE.set_legs_expiry(legs, "2026-09-18")
    assert [l["expiry"] for l in out] == ["2026-09-18", "2026-09-18"]
    assert out[0]["strike"] == 100 and out[1]["qty"] == 2   # other fields preserved


def test_apply_expiry_propagates_to_all_legs():
    from nicegui import ui
    with ui.card() as container:
        ed = LE.build_leg_editor(
            container,
            strikes_for=lambda exp, otype: [735, 736, 737],
            expiries_for=lambda: ["2026-06-23", "2026-06-26"],
            show_premium=True)
        ed.set_legs([
            {"option_type": "call", "side": "long", "strike": 736,
             "expiry": "2026-06-23", "qty": 1, "premium": None},
            {"option_type": "put", "side": "short", "strike": 735,
             "expiry": "2026-06-26", "qty": 1, "premium": None}])
        ed.apply_expiry("2026-06-26")     # propagate to ALL legs (literal, per design)
    legs = ed.get_legs()
    assert all(l["expiry"] == "2026-06-26" for l in legs)
```

**Step 2: Run to verify it fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_leg_editor.py -q`
Expected: FAIL (`AttributeError: ... 'set_legs_expiry'` / `apply_expiry`).

**Step 3: Implement**

In `webgui/pages/options/leg_editor.py`, add a module-level pure helper after `normalize_legs`:

```python
def set_legs_expiry(legs, expiry):
    """Return normalized legs with EVERY leg's expiry set to ``expiry`` (used by the
    Calculator's top-level Expiry → propagate to all legs). Other fields preserved."""
    out = normalize_legs(legs)
    for l in out:
        l["expiry"] = expiry
    return out
```

Inside `build_leg_editor`, add the method (before the `return SimpleNamespace(...)`):

```python
    def apply_expiry(expiry):
        """Set every leg's expiry to ``expiry`` and re-render (strike selects re-sync
        to that expiry's strikes via _render's coercion). Fires on_change. The dirty
        flag is preserved — an untouched single-expiry template still routes analytic."""
        state["legs"] = set_legs_expiry(state["legs"], expiry)
        _render()
        on_change()
```

And add it to the returned namespace:

```python
    return SimpleNamespace(get_legs=get_legs, set_legs=set_legs,
                           apply_template=apply_template, apply_expiry=apply_expiry,
                           refresh_options=refresh_options,
                           is_dirty=lambda: state["dirty"])
```

**Step 4: Run to verify it passes**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_leg_editor.py -q`
Expected: PASS (all, incl. the 2 new).

**Step 5: Commit**

```bash
git add webgui/pages/options/leg_editor.py webgui/tests/test_leg_editor.py
git commit -m "feat(options): leg-editor apply_expiry (propagate expiry to all legs)"
```

---

## Task 3: Leg-table markup — compact row class, wider Type, drop "Actions"

**Files:**
- Modify: `webgui/pages/options/leg_editor.py` (the `_render` function)

No new unit test (markup-only; covered by existing `test_leg_editor.py` render test + browser verification in Task 8). Run the suite after to confirm nothing breaks.

**Step 1: Header row — widen Type, drop "Actions"**

In `_render`, the `if header:` block. Change the Type label width `w-20` → `w-24` and replace the "Actions" label with an equal-width empty spacer:

```python
            if header:
                with ui.row().classes("items-center gap-2 no-wrap leg-head"):
                    ui.label("Type").classes("w-24")
                    ui.label("Side").classes("w-24")
                    ui.label("Expiry").classes("w-40")
                    ui.label("Strike").classes("w-24")
                    ui.label("Qty").classes("w-16")
                    if show_premium:
                        ui.label("Premium").classes("w-20")
                    ui.label("").classes("w-10")     # trashcan column — no header text
```

**Step 2: Leg row — add `leg-row` class + widen the Type select to w-24**

In `_render`, the per-leg `with ui.row(...)` and the Type `ui.select`:

```python
                with ui.row().classes("items-end gap-2 no-wrap leg-row"):
                    ui.select(["call", "put"], value=leg.get("option_type"), label=lab("Type")) \
                        .classes("w-24").on_value_change(lambda e, i=i: _set_field(i, "option_type", e.value))
```

(Leave Side/Expiry/Strike/Qty/Premium/remove as-is.)

**Step 3: Run the suite**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_leg_editor.py -q`
Expected: PASS (markup change doesn't affect the pure/coercion tests).

**Step 4: Commit**

```bash
git add webgui/pages/options/leg_editor.py
git commit -m "feat(options): compact leg rows (leg-row class), widen Type, drop Actions header"
```

---

## Task 4: Theme — compact `.leg-row` cell padding

**Files:**
- Modify: `webgui/pages/options/theme.py`

**Step 1: Add scoped CSS**

In `DASHBOARD_CSS`, after the `.leg-head` rule, add `.leg-row` rules that shrink the
field height + top/bottom padding **and** trim horizontal padding so `call`/`put` fit:

```css
/* Leg table rows — compact cells (less top/bottom padding, shorter height) and
   tighter side padding so "call"/"put" are not horizontally clipped. */
.calc-v2 .leg-row .q-field__control{min-height:32px;padding:0 6px;}
.calc-v2 .leg-row .q-field__control .q-field__native,
.calc-v2 .leg-row .q-field__marginal{min-height:32px;padding-top:0;padding-bottom:0;}
.calc-v2 .leg-row .q-field__append{padding-left:0;}
.calc-v2 .leg-row .q-field__native{font-size:13px;}
```

**Step 2: Verify (browser, in Task 8)**

No unit test for CSS. Confirmed via preview in Task 8 (`call` shows in full; rows visibly shorter).

**Step 3: Commit**

```bash
git add webgui/pages/options/theme.py
git commit -m "feat(options): compact leg-row cell padding (theme)"
```

---

## Task 5: Shared wait-overlay helper

**Files:**
- Create: `webgui/pages/options/overlay.py`
- Test: `webgui/tests/test_inputs.py` (add a light render test)

**Step 1: Write the failing test**

Append to `webgui/tests/test_inputs.py`:

```python
def test_build_loading_overlay_handle_starts_hidden():
    from nicegui import ui
    from pages.options.overlay import build_loading_overlay
    with ui.card():
        ov = build_loading_overlay("Loading…")
    assert hasattr(ov, "show") and hasattr(ov, "hide")
    assert ov.element.visible is False     # starts hidden
    ov.show("Loading AAPL…")
    assert ov.element.visible is True
    ov.hide()
    assert ov.element.visible is False
```

**Step 2: Run to verify it fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_inputs.py -q`
Expected: FAIL (`ModuleNotFoundError: ... overlay`).

**Step 3: Implement**

Create `webgui/pages/options/overlay.py`:

```python
"""Shared full-screen wait overlay for the Options pages (Tier-1).

A ``position:fixed`` dimmed backdrop with a centered spinner shown while a symbol
loads (Calculator Load / Simulator Fetch). Built once per page render; toggled via
the returned ``show(msg)`` / ``hide()`` handle. The overlay covers the viewport
(blocks clicks) and sits above the page (high z-index) regardless of DOM nesting,
so it can be created anywhere in render()."""
from types import SimpleNamespace

from nicegui import ui

_OVERLAY_STYLE = (
    "position:fixed;inset:0;z-index:9999;display:flex;flex-direction:column;"
    "align-items:center;justify-content:center;gap:14px;"
    "background:rgba(6,12,24,0.55);backdrop-filter:blur(1px);"
)


def build_loading_overlay(text="Loading…"):
    """Mount a hidden full-screen wait overlay. Returns a handle with
    ``element`` / ``show(msg=None)`` / ``hide()``."""
    overlay = ui.element("div").style(_OVERLAY_STYLE)
    with overlay:
        ui.spinner(size="lg", color="primary")
        label = ui.label(text).style(
            "color:#e7edf8;font-weight:600;letter-spacing:.02em;")
    overlay.set_visibility(False)

    def show(msg=None):
        if msg is not None:
            label.text = msg
        overlay.set_visibility(True)

    def hide():
        overlay.set_visibility(False)

    return SimpleNamespace(element=overlay, show=show, hide=hide)
```

**Step 4: Run to verify it passes**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_inputs.py -q`
Expected: PASS.

> If `element.visible` is not exposed by the installed NiceGUI version, change the
> assertions to check `ov.element._props` / visibility class instead, or drop the
> visibility asserts and keep only the `hasattr(show/hide)` checks. Do NOT change
> the implementation to satisfy the test.

**Step 5: Commit**

```bash
git add webgui/pages/options/overlay.py webgui/tests/test_inputs.py
git commit -m "feat(options): shared full-screen wait overlay helper"
```

---

## Task 6: Calculator wiring — symbol submit, overlay, expiry propagation

**Files:**
- Modify: `webgui/pages/options/calculator.py`

No unit test (page wiring); verified in Task 8 + existing suite stays green.

**Step 1: Imports + state + overlay**

Top of `render()` — add the imports and overlay; extend `state`:

- Import: `from .inputs import select_all_on_focus, should_load` (replace the existing single import) and `from . import overlay as _overlay`.
- After `ui.add_css(CALC_V2_CSS)`: `wait = _overlay.build_loading_overlay()`.
- In the `state = {...}` dict add: `"last_loaded": None,` and `"loading": False,` and `"applying": False,`.

**Step 2: `load_symbol` — overlay + last_loaded + re-entrancy guard**

Replace `load_symbol` with:

```python
    @guard
    def load_symbol(show_wait=False):
        """Enqueue a ``calc_load`` for the symbol; the version-poll applies it.

        ``show_wait`` (user-initiated: Load button / symbol tab-out / Enter) shows the
        centered wait overlay until the chain arrives (or a ~15s safety timeout).
        Mount-time auto-loads (restore / handoff) pass show_wait=False."""
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        if show_wait and state.get("loading"):
            return  # a user load is already in flight (collapses focusout-then-click)
        state["last_loaded"] = sym
        if show_wait:
            state["loading"] = True
            wait.show(f"Loading {sym}…")
            ui.timer(15.0, _load_timeout, once=True)
        bus_client.request("options", {"type": "calc_load", "args": {"symbol": sym}})
        ui.notify(f"Loading {sym}…", type="info")

    @guard
    def _load_timeout():
        """Safety net: if the chain never arrived, drop the overlay + reset the dedup
        so a retry re-triggers (e.g. the service is down)."""
        if state.get("loading"):
            state["loading"] = False
            wait.hide()
            state["last_loaded"] = None

    @guard
    def _symbol_submit():
        """Tab-out / Enter on the symbol field → Load (deduped: only when changed)."""
        if not should_load((symbol_in.value or "").strip().upper(), state.get("last_loaded")):
            return
        load_symbol(show_wait=True)
```

**Step 3: Wire the Load button + symbol triggers**

- Load button (currently `on_click=lambda: load_symbol()`): change to `on_click=lambda: load_symbol(show_wait=True)`.
- The symbol input persistence loop currently includes `expiry_sel`; **remove `expiry_sel`** from it and give the symbol its triggers. Replace the block:

```python
    contracts_in.on_value_change(lambda e: (_on_contracts_change(), _capture()))
    # Persist the remaining inputs on change (cheap dict write; no command).
    for _w in (iv_in, rate_in, ivchg_in, price_in, nstrikes_in):
        _w.on_value_change(lambda e: _capture())
    # Symbol: tab-out / Enter simulate Load (deduped); value-change still persists.
    symbol_in.on_value_change(lambda e: _capture())
    symbol_in.on("keydown.enter", lambda e: _symbol_submit())
    symbol_in.on("focusout", lambda e: _symbol_submit())

    @guard
    def _on_expiry_change():
        """Top-level Expiry → propagate to ALL legs (re-syncs each leg's strikes).
        Suppressed while restoring or while _apply_chain/_prefill set it programmatically."""
        if state.get("restoring") or state.get("applying"):
            return
        editor.apply_expiry(expiry_sel.value)
        _capture()

    expiry_sel.on_value_change(lambda e: _on_expiry_change())
```

**Step 4: `_apply_chain` — hide overlay + guard the programmatic expiry set**

In `_apply_chain`, at the very top add the overlay dismissal, and wrap the
`expiry_sel.value` assignment in the `applying` guard:

```python
    def _apply_chain(cc):
        cc = cc or {}
        state["loading"] = False
        wait.hide()
        state["chain"] = cc.get("chain")
        if cc.get("price"):
            price_in.value = round(cc["price"], 2)
        exps = chain_expiries(state["chain"] or {})
        state["applying"] = True
        try:
            expiry_sel.options = exps
            if exps and expiry_sel.value not in exps:
                expiry_sel.value = exps[0]
            expiry_sel.update()
        finally:
            state["applying"] = False
        # ... (rest unchanged: pending / seed / refresh / notify)
```

**Step 5: `_prefill` — guard its programmatic expiry set**

In `_prefill`, wrap the `expiry_sel.options/value/update` lines with the `applying` guard so the prefill's expiry assignment doesn't fire `apply_expiry` on stale legs:

```python
        exp = sig.get("expiration")
        if exp:
            state["applying"] = True
            try:
                expiry_sel.options = [exp]
                expiry_sel.value = exp
                expiry_sel.update()
            finally:
                state["applying"] = False
```

**Step 6: Run the webgui suite**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest -q`
Expected: PASS (no regressions; new tests green).

**Step 7: Commit**

```bash
git add webgui/pages/options/calculator.py
git commit -m "feat(calculator): symbol tab/Enter Loads + wait overlay; Expiry propagates to all legs"
```

---

## Task 7: Simulator wiring — symbol submit + overlay

**Files:**
- Modify: `webgui/pages/options/simulator.py`

No unit test (page wiring); verified in Task 8.

**Step 1: Imports + state + overlay**

- Import: `from .inputs import select_all_on_focus, should_load` and `from . import overlay as _overlay`.
- After `ui.add_css(DASHBOARD_CSS)`: `wait = _overlay.build_loading_overlay()`.
- In `state` add: `"last_loaded": None,` and `"loading": False,`.

**Step 2: `_request_fetch` — show_wait + last_loaded + re-entrancy guard, and a submit helper**

Replace `_request_fetch` with:

```python
    @guard
    def _request_fetch(show_wait=False):
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        if show_wait and state.get("loading"):
            return  # a user fetch is already in flight
        state["last_loaded"] = sym
        if show_wait:
            state["loading"] = True
            wait.show(f"Fetching {sym}…")
            ui.timer(15.0, _fetch_timeout, once=True)
        bus_client.request("options", {"type": "sim_fetch", "args": {"symbol": sym}})
        status.text = "Fetching snapshot…"

    @guard
    def _fetch_timeout():
        if state.get("loading"):
            state["loading"] = False
            wait.hide()
            state["last_loaded"] = None

    @guard
    def _symbol_submit():
        """Tab-out / Enter on the symbol field → Fetch (deduped: only when changed)."""
        if not should_load((symbol_in.value or "").strip().upper(), state.get("last_loaded")):
            return
        _request_fetch(show_wait=True)
```

**Step 3: Wire the Fetch button + symbol triggers**

- Change `fetch_btn.on_click(_request_fetch)` → `fetch_btn.on_click(lambda e: _request_fetch(show_wait=True))`.
- Replace `symbol_in.on_value_change(lambda e: _capture())` with:

```python
    symbol_in.on_value_change(lambda e: _capture())
    symbol_in.on("keydown.enter", lambda e: _symbol_submit())
    symbol_in.on("focusout", lambda e: _symbol_submit())
```

**Step 4: `_apply_meta` — hide overlay**

At the top of `_apply_meta`:

```python
    def _apply_meta(meta):
        state["loading"] = False
        wait.hide()
        state["meta"] = meta or None
        # ... (rest unchanged)
```

**Step 5: Run the webgui suite**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest -q`
Expected: PASS.

**Step 6: Commit**

```bash
git add webgui/pages/options/simulator.py
git commit -m "feat(simulator): symbol tab/Enter Fetches + wait overlay"
```

---

## Task 8: Browser verification (preview)

**Files:** none (verification only).

Start the preview dev server (`webgui` on :8500). The options_svc service must be up
for live chain data; if not, the wait overlay + tab-trigger still demonstrate (overlay
shows, then the 15s timeout dismisses it).

**Calculator (`/options/calculator`):**
1. Type a new symbol (e.g. `AAPL`) and **press Tab** → Load fires, the centered wait
   overlay appears, then dismisses when the chain lands. Repeat with **Enter**.
2. Confirm the **Type** dropdown shows `call` in full (not `c…`) and the leg rows are
   visibly more compact (less top/bottom space).
3. Confirm the leg-table header has **no "Actions"** text above the trashcan.
4. Change the top-level **Expiry** → every leg's Expiry select updates to match (and
   strikes re-sync). Use `preview_snapshot` / `preview_eval` to read the leg selects.

**Simulator (`/options/simulator`):**
5. Type a symbol + **Tab** / **Enter** → Fetch fires + overlay shows/dismisses.
6. Confirm the same compact leg rows / full `call` / no "Actions" header (shared editor).

Capture a screenshot of the Calculator showing the fixed Type column + compact rows.
If anything is off, fix the source and re-verify before continuing.

**Step — full suite:**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest -q`
Expected: PASS (count ≥ previous + new tests).

---

## Task 9: Update CLAUDE.md + final commit

**Files:**
- Modify: `CLAUDE.md` (root) — the standing "update after any structural change" requirement.

**Step 1: Update the `**Last updated:**` block** with a concise entry describing:
- Symbol tab-out/Enter → Load (Calculator) / Fetch (Simulator), deduped via
  `inputs.should_load`; centered wait overlay (`overlay.build_loading_overlay`)
  shown on user loads, dismissed on chain/meta arrival or a 15s safety timeout.
- Calculator top-level Expiry → `leg_editor.apply_expiry` propagates to all legs
  (literal, incl. calendars) + re-syncs strikes.
- Shared `leg_editor` compact `leg-row` cells (`theme.py`) + widened Type (fixes
  `call` truncation) + dropped "Actions" header — both pages.
- Note the new files (`overlay.py`) in the `pages/options/` shared-subpackage list
  and the Calculator/Simulator route-table descriptions.
- Reference the design + plan docs.

**Step 2: Update the route table** Calculator + Simulator rows + the
`pages/options/` shared-helper paragraph to mention `overlay.py` and the new
behaviors.

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record Calculator/Simulator UX changes (symbol-load, expiry propagate, leg cells)"
```

---

## Done criteria

- `should_load` + `set_legs_expiry` + `apply_expiry` + overlay-handle tests green.
- Full `webgui` pytest green (no regressions).
- Browser-verified: tab/Enter Loads with overlay (both pages); Expiry propagates to all
  legs (Calculator); `call` shows in full + compact rows + no "Actions" header (both).
- CLAUDE.md updated; all work committed on `Using_Highcharts`.
