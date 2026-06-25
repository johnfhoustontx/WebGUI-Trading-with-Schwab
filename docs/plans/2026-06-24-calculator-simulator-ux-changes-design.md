# Calculator & Simulator UX changes — design

**Date:** 2026-06-24
**Branch:** `Using_Highcharts`
**Pages:** `/options/calculator`, `/options/simulator` (+ shared `leg_editor.py`, `theme.py`)

## Problem

Four UX requests against the Options **Calculator** and **Simulator**:

1. **Symbol input** — after typing a symbol and **tabbing out or pressing Enter**, fire the
   **Load** button (Calculator) / **Fetch snapshot** (Simulator) automatically. While loading,
   show a **wait animation in the middle of the screen**.
2. **Expiry selection** — once the top-level Expiry is picked, **propagate it to all legs**.
3. **Type field truncation** — `call` is clipped to `c…` in the leg-table Type dropdown; make the
   leg cells more compact (less top/bottom padding) **and** fix the horizontal clipping.
4. **"Actions" header** — drop the word; the trashcan icon is adequate.

## Decisions (clarified with the user)

- **Expiry propagation is literal/unconditional** — every leg gets the selected expiry, **including**
  calendar/diagonal templates (which collapses a calendar to a same-expiry spread; accepted).
- **Type field — fix both** — reduce top/bottom padding for compact rows **and** trim side
  padding / widen the cell so `call`/`put` show in full.

## Scope per page

| Change | Calculator | Simulator | Where |
|--------|------------|-----------|-------|
| 1. Symbol → Load on tab/Enter + wait overlay | ✔ `load_symbol()` | ✔ `_request_fetch()` | each page + shared `overlay.py` |
| 2. Expiry → all legs | ✔ (`expiry_sel`) | — (no global expiry; per-leg only) | `calculator.py` + `leg_editor.py` |
| 3. Leg-cell padding / truncation | ✔ | ✔ | shared `leg_editor.py` + `theme.py` |
| 4. Drop "Actions" header | ✔ | ✔ | shared `leg_editor.py` |

Changes 3 & 4 live in the **shared** `leg_editor.py` (both pages mount it with `header=True`), so they
apply to both pages from one edit.

## 1. Symbol → Load on tab-out / Enter + centered wait animation

**Trigger.** Wire `keydown.enter` **and** `focusout` on `symbol_in` to a `_symbol_submit()` helper.
Use `focusout` (not `blur`) — NiceGUI binds the listener to the q-input ROOT and native `blur`
doesn't bubble there (same reason `select_all_on_focus` uses `focusin`, and the Trade page's tab-out
uses `focusout`). Keep the existing `select_all_on_focus`.

- Calculator `_symbol_submit()` → `load_symbol(show_wait=True)`.
- Simulator `_symbol_submit()` → `_request_fetch(show_wait=True)`.

**Dedup (pure).** `should_load(current, last_loaded) -> bool` — tab-out/Enter loads only when the
symbol changed since the last load (collapses the Enter-then-focusout double fire and avoids a
re-fetch when tabbing through unchanged). The **Load / Fetch button always reloads** (force) — even
for the same symbol (e.g. to refresh price). `last_loaded` is tracked in page state and reset on the
safety timeout so a retry after a failure works.

**Wait overlay.** New shared `webgui/pages/options/overlay.py` → `build_loading_overlay()` returns a
small handle (`.show(msg)` / `.hide()`). It is a `position:fixed` full-screen dimmed backdrop
(`rgba(6,12,24,.55)`), high z-index, `pointer-events` blocking, with a centered `ui.spinner(size="lg")`
+ a "Loading SYM…" label. Built once per page render.

- Shown by **user-initiated** loads only (`show_wait=True`: button + tab/Enter).
- Hidden when fresh data lands — Calculator `_apply_chain`, Simulator `_apply_meta` — and on a **~15 s
  safety timeout** so a down service can't leave the spinner hanging (the timeout also clears
  `last_loaded` so a retry re-triggers).
- **Mount-time auto-loads** (persisted-state restore, cross-page handoff) call with `show_wait=False`,
  so navigating to the page doesn't flash the overlay on every visit.

## 2. Expiry propagates to all legs (Calculator only)

On `expiry_sel.on_value_change` (skipped while `state["restoring"]`), call a new editor method
`editor.apply_expiry(value)`:

- Pure core `set_legs_expiry(legs, expiry)` in `leg_editor.py` returns the legs with every
  `leg["expiry"]` set to `expiry` (unit-tested).
- The method writes that onto `state["legs"]`, **re-renders** (so each leg's strike select re-syncs to
  the new expiry's strikes via the existing `_render` coercion), and fires `on_change()` (→ persist).
- The editor `dirty` flag is **preserved** (not forced), so an untouched single-expiry template still
  routes through the analytic summary in `calc_compute`.

No auto-recalculate on expiry change (consistent with the other inputs; the user clicks **Calculate**).

## 3. Leg-cell padding + truncation (shared)

- `leg_editor._render` adds a `leg-row` class to each leg `ui.row(...)`.
- `theme.py` adds scoped rules under `.calc-v2 .leg-row`: reduce the field `min-height` (40 → ~32px)
  and **top/bottom** padding (compact rows), and trim horizontal padding so `call`/`put` fit. If still
  tight after the padding trim, bump the Type select `w-20` → `w-24` **and** the matching header label
  `ui.label("Type").classes("w-20")` → `w-24` to keep header/body aligned.
- Verify in the browser preview that `call` renders in full.

## 4. Drop "Actions" header (shared)

In `leg_editor._render` header block, replace `ui.label("Actions").classes("w-10 text-right")` with an
equal-width empty spacer (`ui.label("").classes("w-10")`) so the trashcan column stays aligned but no
text shows.

## Testing

- **Pure unit tests** (`webgui/tests/`): `should_load`, `set_legs_expiry`.
- **Grep first** for any existing test asserting the "Actions" header / leg-table labels before
  editing (update if present).
- **Browser preview (Calculator):** tab-out + Enter both Load; spinner appears then dismisses;
  expiry propagates to both legs; `call` shows in full; no "Actions" header. **Smoke-check the
  Simulator** Fetch-on-tab + spinner.
- Re-run `cd webgui && ..\.venv\Scripts\python -m pytest -q`.

## Out of scope (YAGNI)

- No global Expiry select added to the Simulator.
- No auto-recalc on expiry change.
- No change to the load/compute service contracts (Tier-2 untouched — this is all Tier-1 UI wiring).
