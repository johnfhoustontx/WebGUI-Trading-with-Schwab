# Tailwind-first UI migration — Implementation Plan (Phase 3b: builder/analytic screens)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert the **Calculator** and **Simulator** (the heaviest `DASHBOARD_CSS`
consumers) to Tailwind-only: swap their `.calc-v2`/`.calc-card`/`.cv2-btn*`/`.calc-eyebrow`/
`.strategy-menu-btn` semantic classes for the Phase 0/3a tokens, point `ui.add_css` at the
slimmed `QUASAR_INTERNAL_CSS` (keeping `.calc-v2`/`.leg-*`/`.strategy-menu-btn` only as
**scope hooks** for the Quasar-field/tab/menu internals), and remove the dead `CALC_CSS`.

**Architecture / mapping (from the inventory):**
| Legacy class | → | Token | Where applied |
|---|---|---|---|
| `.calc-v2` (wrapper) | keep as hook + add | `PAGE` | calc:414, sim:302 |
| `.calc-card` | | `CARD` | calc:424,447,463; sim:306,324,330 |
| `.calc-eyebrow` | | `EYEBROW` | sim:318 |
| `.cv2-btn` | | `BTN` | calc:441,443,452,454,460; sim:317 |
| `.cv2-btn-primary` | | `BTN_PRIMARY` | calc:445; sim:310 |
| `.strategy-menu-btn` | keep as hook + add | `STRATEGY_BTN` | `strategy_menu.py` (shared) |
| `.leg-head/.leg-row/.leg-strike` | keep as hooks (no change) | — | `leg_editor.py` (shared) |
| `.style("color:#eaf0fb")` title | | `LABEL` (`text-[#eaf0fb]`) | calc:415, sim:303 |
| summary-tile `.style(f"color:{c}")` | | `TXT_POS/NEG/NEUTRAL` (finite) | calc:289 |

**Why `.calc-v2` stays as a class:** `QUASAR_INTERNAL_CSS` scopes its field/tab rules under
`.calc-v2` (e.g. `.calc-v2 .q-field__control`). The wrapper must keep the bare `calc-v2`
class as the scope selector AND add the `PAGE` token for the gradient/border/padding (the
`.calc-v2{background:…}` visual rule was moved to `PAGE` in Phase 0). Same for
`strategy-menu-btn` (hook for `.strategy-menu-btn .q-btn__content`/`.q-icon` internals) and
the `leg-*` classes (hooks for the compact q-field rules) — these classes stay, the box/
visual styling comes from tokens.

**Scope call — the Calculator P&L heatmap is OUT OF SCOPE.** It's a dense computed grid
rendered as a **raw `ui.html()` HTML string** (`_CELL_COLORS`, calc ~337-340 — `<td style=
"background:…">`), not NiceGUI components with `.classes()`. Per the standard's principle
("not NiceGUI components, so the rule doesn't bind"), inline `style=` inside a `ui.html()`
fragment is treated like the EOD/Gamma HTML fragments — left as-is. (The `test_no_inline_style`
guard checks for the `.style(` METHOD and Vue `:style=` bindings, neither of which the grid
uses, so adding calc to the guard is safe.) Convert only the NiceGUI summary **tiles**
(`.style()` method calls). **A separate clause is added to the standard to record this.**

**Tech Stack:** NiceGUI 3.13 (Tailwind JIT), pytest from `webgui/`. Baseline **578**.

**Standing rules:** TDD; `.classes(remove=…, add=…)` for any reactively-reset class; preserve
exact look (convert + light polish); DO NOT touch `DASHBOARD_CSS`/`QUASAR_INTERNAL_CSS` (both
stay — `DASHBOARD_CSS` is still injected by the not-yet-converted **Trade** page) or any other
page. Run `..\.venv\Scripts\python -m pytest -q` after each task.

---

### Task 3b.1: `strategy_menu.py` — STRATEGY_BTN token (shared widget)

**Files:** Modify `webgui/pages/options/strategy_menu.py`; Test `webgui/tests/test_strategy_menu.py`.

The boxed Strategy button currently gets only `strategy-menu-btn` (its box styling comes from
`DASHBOARD_CSS .strategy-menu-btn.q-btn`, which disappears once calc/sim stop injecting
DASHBOARD_CSS). It must also carry the `STRATEGY_BTN` token.

**Step 0 — confirm consumers.** grep `build_strategy_menu` across `webgui/` — confirm ONLY
calculator.py + simulator.py call it (both in this phase). If another page uses it, STOP and report.

**Step 1 — failing test.** Assert the boxed button's class string contains BOTH the hook and the
token (read the current test + how it builds; assert `theme.STRATEGY_BTN` substrings appear and
`strategy-menu-btn` is retained).

**Step 2 — run, expect fail.**

**Step 3 — implement.** Where `boxed=True` applies `"strategy-menu-btn"`, change to
`f"strategy-menu-btn {STRATEGY_BTN}"` (import `from .theme import STRATEGY_BTN`). Keep the
`strat-menu-navy` menu class untouched (teleported escape-hatch). Keep `strategy-menu-btn` as
the hook (QUASAR_INTERNAL_CSS styles its `.q-btn__content`/`.q-icon`).

**Step 4 — run, expect pass** (+ full suite).

**Step 5 — commit:** `refactor(webgui): strategy menu button uses STRATEGY_BTN token (Phase 3b)`

---

### Task 3b.2: `simulator.py` — tokens + QUASAR_INTERNAL_CSS (simpler page first)

**Files:** Modify `webgui/pages/options/simulator.py`; Test `webgui/tests/test_options_simulator.py`.

Sim has 1 `.style()` (title), no CALC_CSS, no dynamic tile colors — do it first as the template.

**Step 1 — failing/guard test.** Add (or extend) a test asserting simulator's render path uses
`QUASAR_INTERNAL_CSS` (not `DASHBOARD_CSS`) and is `.style(`-free. (If render isn't unit-testable
directly, assert at the source level via the no-inline-style guard in 3b.4 + a token-usage smoke.)

**Step 2 — implement.**
- Import `from .theme import QUASAR_INTERNAL_CSS, PAGE, CARD, EYEBROW, BTN, BTN_PRIMARY, LABEL`.
- Line 279: `ui.add_css(DASHBOARD_CSS)` → `ui.add_css(QUASAR_INTERNAL_CSS)`. Update the
  `from .theme import DASHBOARD_CSS` import accordingly.
- Line 302 wrapper: `"calc-v2 w-full gap-4"` → `f"calc-v2 {PAGE} w-full gap-4"`.
- Lines 306/324/330 `"calc-card w-full gap-N"` → `f"{CARD} w-full gap-N"` (drop `calc-card`).
- Line 318 `"calc-eyebrow"` → `EYEBROW`.
- Line 310 `"cv2-btn-primary"` → `BTN_PRIMARY`; line 317 `"cv2-btn"` → `BTN` (keep `color=None`).
- Line 303 title `.style("color:#eaf0fb")` → `.classes(LABEL)` (append to its existing classes).
- Verify the Strategy button (326) + leg editor still render (they get STRATEGY_BTN from 3b.1 +
  the leg-* hooks from QUASAR_INTERNAL_CSS).

**Step 3 — run** (sim test + full suite) → green.
**Step 4 — commit:** `refactor(webgui): simulator Tailwind tokens (Phase 3b)`

---

### Task 3b.3: `calculator.py` — tokens + tile palette-map + drop dead CALC_CSS

**Files:** Modify `webgui/pages/options/calculator.py`; Test `webgui/tests/test_options_calculator.py`.

**Step 1 — failing test.** The summary tile colors (calc:283-289) are a finite set
(`#66bb6a`/`#ef5350`/`#bdbdbd`). Add a mapper test if you introduce a `tile_color_class` helper:
```python
def test_tile_color_class_maps_palette():
    from pages.options import calculator as c, theme
    assert c.tile_color_class("#66bb6a") == theme.TXT_POS   # or by semantic state
    assert c.tile_color_class("#ef5350") == theme.TXT_NEG
    assert c.tile_color_class("#bdbdbd") == theme.TXT_NEUTRAL
```
(If the tile color originates as a SEMANTIC state upstream — profit/loss/neutral — prefer mapping
the state; if it's already a hex from a finite set, map the hex → token. Read `_render_summary`
first and pick the cleaner source.)

**Step 2 — run, expect fail.**

**Step 3 — implement.**
- Imports: `from .theme import QUASAR_INTERNAL_CSS, PAGE, CARD, BTN, BTN_PRIMARY, LABEL,
  TXT_POS, TXT_NEG, TXT_NEUTRAL`.
- **Drop dead CALC_CSS:** remove `ui.add_css(CALC_CSS)` (line 388) AND the `CALC_CSS` constant
  (49-66) — grep first to confirm NO element uses `calc-btn-3d`/`calc-go` (the inventory says none).
  If grep finds a use, STOP and report.
- Line 389: `ui.add_css(CALC_V2_CSS)` → `ui.add_css(QUASAR_INTERNAL_CSS)`; drop the
  `from .theme import DASHBOARD_CSS as CALC_V2_CSS` alias (import QUASAR_INTERNAL_CSS instead).
- Line 414 wrapper → `f"calc-v2 {PAGE} w-full gap-4"`.
- Lines 424/447/463 `calc-card …` → `f"{CARD} …"` (keep the flex/min-w/self-stretch utilities).
- Lines 441/443/452/454/460 `cv2-btn …` → `f"{BTN} …"`; line 445 `cv2-btn-primary w-full` →
  `f"{BTN_PRIMARY} w-full"` (keep `color=None`).
- Line 415 title `.style("color:#eaf0fb")` → `.classes(LABEL)`.
- **Summary tiles (289):** replace `.style(f"color:{color}")` with a Tailwind class. Add
  `tile_color_class(color_or_state)` → `TXT_POS`/`TXT_NEG`/`TXT_NEUTRAL`, and apply via
  `.classes(...)`. If the tile label is REBUILT each calculate (check `_render_summary` — likely
  a `.clear()`+rebuild) use `add=`; if updated in place, use `.classes(remove=STATE_TEXT_CLASSES,
  add=...)`.
- **P&L grid (`_CELL_COLORS`, ~337-340): OUT OF SCOPE — leave the `ui.html()` grid as-is.**

**Step 4 — run** (calc test + full suite) → green.
**Step 5 — commit:** `refactor(webgui): calculator Tailwind tokens + tile palette-map, drop dead CALC_CSS (Phase 3b)`

---

### Task 3b.4: Guard + standard clarification

**Files:** `webgui/tests/test_no_inline_style.py`; `CLAUDE.md`.

**Step 1 — extend the guard** to include `calculator.py` + `simulator.py` in the `.style(`/`:style=`-free
list. (The calc P&L grid uses raw `style=` inside a `ui.html()` STRING — NOT `.style(`/`:style=` —
so this passes. Confirm the guard substring choice doesn't false-match the grid; if your guard
greps bare `style=`, exclude calc's grid or keep the guard to `.style(`/`:style=` only.)

**Step 2 — run** → green.

**Step 3 — standard clarification.** In `CLAUDE.md` → "UI styling standard", extend the **Out of
scope** bullet to explicitly include **raw `ui.html()` HTML-string fragments** (e.g. the Calculator
P&L heatmap grid, Gamma Explain blocks) — not just `HTMLResponse` docs — since they aren't NiceGUI
components with `.classes()`. One sentence.

**Step 4 — commit:** `test+docs(webgui): guard calc/sim inline-style-free + scope ui.html fragments (Phase 3b)`

---

### Task 3b.5: Browser gate + CLAUDE.md status

**Step 1 — browser.** Restart the preview (stop+start to reload). On **Calculator** and
**Simulator** verify: the navy gradient page background (PAGE token), bordered cards (CARD), the
boxed Strategy button (STRATEGY_BTN — gradient/box intact), the filled navy input boxes + tabs
(QUASAR_INTERNAL_CSS field/tab rules still applied via the `.calc-v2` hook), the BTN/BTN_PRIMARY
buttons, the title color, and (Calculator) load a symbol → summary tile colors + the P&L grid still
render. Screenshot both. Confirm **no console errors** and that the look matches pre-conversion
(convert + light polish). If a token doesn't reproduce a look (esp. the Strategy button box or the
field internals via the `.calc-v2` hook), STOP and flag.

**Step 2 — fix any regressions**, re-screenshot.

**Step 3 — CLAUDE.md status** snapshot + Last-updated: Phase 3b done (Calculator + Simulator on
tokens; `DASHBOARD_CSS` now consumed ONLY by Trade — Phase 4 will flip it and trigger the legacy
cleanup). Next Phase 3c. Commit.

---

## Per-task done checklist
suite green · calc/sim free of `.style(`/`:style=` (grid `ui.html` exempt + documented) · semantic
classes → tokens · `.calc-v2`/`.strategy-menu-btn`/`.leg-*` retained ONLY as scope hooks · dead
CALC_CSS removed · `DASHBOARD_CSS`/`QUASAR_INTERNAL_CSS` untouched · screenshot parity + no console
errors · CLAUDE.md updated.

## Notes / risks
- **The `.calc-v2` hook is load-bearing** — removing the class (vs the visual rule) breaks every
  field/tab internal style. Keep the class, add the PAGE token. Same for `strategy-menu-btn`/`leg-*`.
- After 3b, **`DASHBOARD_CSS` is consumed only by `trade.py`** — do NOT delete it yet (Phase 4
  flips Trade, then the cleanup removes the now-dead semantic rules).
- The Strategy button box style depends on 3b.1 landing (STRATEGY_BTN token) AND the page injecting
  QUASAR_INTERNAL_CSS (for its q-btn__content internals) — verify the button looks right on BOTH pages.
- Don't touch `leg_editor.py` (its leg-* classes are unchanged hooks).
