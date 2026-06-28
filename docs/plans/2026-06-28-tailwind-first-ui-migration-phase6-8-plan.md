# Tailwind-first UI migration — Implementation Plan (Phases 6–8: Portfolio / Driver / utility pages)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Finish the migration — convert the last pages with `.style()`/`:style=`: `portfolio.py`
(Phase 6), `driver.py` (Phase 7), `status.py` (Phase 8). Confirm `settings.py`/`terminate.py`/
`manuals.py` are already clean and `eod.py`'s `EOD_CSS` is out of scope (styles a `ui.html()`
fragment). These pages keep their OWN look (no `PAGE`/`CARD` token imposition).

**Tech Stack:** NiceGUI 3.13 (Tailwind JIT), pytest from `webgui/`. Baseline **599**.

**Standing rules:** TDD the color mappers; reactive in-place recolors via `.classes(remove=…, add=…)`;
rebuilt-in-`.clear()`-loop elements via `add=`; preserve exact look; out of scope = Highcharts,
`ui.html()` fragments + their CSS, Quasar `color=` props. Run `..\.venv\Scripts\python -m pytest -q`
after each task.

---

### Task 6.1: `portfolio.py` — status colors + static layout

**Files:** Modify `webgui/pages/portfolio.py`; Test `webgui/tests/test_portfolio.py`.

Local palette: `UP_COLOR=#2e9e6b`, `DOWN_COLOR=#e24b4a`, `MUTED_COLOR=#888888` (NOT theme `TXT_*`).
`proxy_status`(66) → `(text, UP|DOWN)`; `stream_status`(72) → `(text, UP|MUTED)`. Both applied to
PERSISTENT labels updated in-place (156 `proxy_lbl`, 159 `stream_lbl`) → REACTIVE.

**Step 1 — failing test.** Add local text-class constants + class-returning helpers:
```python
TXT_UP="text-[#2e9e6b]"; TXT_DOWN="text-[#e24b4a]"; TXT_MUTED="text-[#888888]"
STATUS_TEXT_CLASSES="text-[#2e9e6b] text-[#e24b4a] text-[#888888]"  # remove-set
def test_proxy_stream_status_classes():
    from pages import portfolio as p
    # whatever shape you choose — assert proxy up → TXT_UP, down → TXT_DOWN; stream up → TXT_UP, else TXT_MUTED
```
(Add `proxy_status_class`/`stream_status_class`, OR have `proxy_status`/`stream_status` return a
`class` instead of a hex `color`. Keep the text strings.)

**Step 2 — run, expect fail.**

**Step 3 — implement.**
- Line 131 static `.style("white-space:pre-wrap")` → `.classes("whitespace-pre-wrap")`.
- Lines 156/159 → `proxy_lbl.classes(remove=STATUS_TEXT_CLASSES, add=<class>)` /
  `stream_lbl.classes(remove=STATUS_TEXT_CLASSES, add=<class>)` (REACTIVE — version-poll repaints).

**Step 4 — run** (portfolio test + full suite) → green; grep portfolio.py `.style(` → ZERO.

**Step 5 — commit:** `refactor(webgui): portfolio status colors + layout → Tailwind (Phase 6)`

---

### Task 7.1: `driver.py` — badge colors + P&L `:style=` slot → `:class`

**Files:** Modify `webgui/pages/driver.py`; Test `webgui/tests/test_driver.py` (+ `test_driver_monitor.py`).

Pieces:
- `grade_color`(70, 5-set `#1D9E75`/`#185FA5`/`#BA7517`/`#E24B4A`/`#888888`) → grade badge bg(811);
  `control_state_color`(288, 3-set `#888888`/`#1D9E75`/`#BA7517`) → control badge bg(659). BOTH badges
  are REBUILT per repaint (`_render_approval`/`_render_monitor` `.clear()`) → `add=`.
- `pnl_color`(93, 3-set = EXACT theme `TXT_*` hexes) drives the Vue `:style=` slot `_PNL_CELL_SLOT`
  (537-543) via the stamped row field `_pnl_color` (set in `_breakdown_rows`/perf rows). Convert the
  slot `:style="color:${props.row._pnl_color}..."` → `:class` with a stamped `_pnl_class` field.
- `DRIVER_CSS`(529, sticky `thead`/`.q-table__middle`) is **Quasar-internal → KEEP** (the per-page
  escape hatch, like SCAN_CSS/PAPER_CSS table internals).

**Step 1 — failing tests.** Add class maps:
```python
def test_grade_bg_class_and_control_bg_class():
    from pages import driver as d
    assert d.grade_bg_class("A") == "bg-[#1D9E75]"
    assert d.grade_bg_class("X") == "bg-[#E24B4A]"
    assert d.grade_bg_class("?") == "bg-[#888888]"        # fallback
    assert d.control_bg_class("active") == "bg-[#1D9E75]" # adapt to control_state input
def test_pnl_class_maps_sign():
    from pages import driver as d
    assert d.pnl_class(5) == "text-[#66bb6a]"
    assert d.pnl_class(-5) == "text-[#ef5350]"
    assert d.pnl_class(0) == "text-[#bdbdbd]"
```

**Step 2 — run, expect fail.**

**Step 3 — implement.**
- `grade_bg_class`/`control_bg_class` (mirror the dict/fallback → `bg-[#hex]`). Replace the badge
  `.style(f"background:{...}")` (659/811) with `.classes(f"text-weight-bold text-white px-3 py-1 rounded {…bg class}")`
  (rebuilt → `add=`).
- `pnl_class` (mirror `pnl_color` → `text-[#hex]`). In the row builders that stamp `_pnl_color`,
  ALSO stamp `_pnl_class = pnl_class(v)`. Rewrite `_PNL_CELL_SLOT` to bind `:class` instead of
  `:style`: `<span :class="(props.row._pnl_class || 'text-[#bdbdbd]') + ' font-semibold'">{{props.value}}</span>`
  (drop the `:style`). Keep `_pnl_color` only if still read elsewhere; else replace it.

**Step 4 — run** (driver tests + full suite) → green; grep driver.py for `.style(` AND `:style=` → ZERO.

**Step 5 — commit:** `refactor(webgui): driver badge colors + P&L cell :class → Tailwind (Phase 7)`

---

### Task 8.1: `status.py` static widths + guard the clean utility pages

**Files:** Modify `webgui/pages/status.py`; Test `webgui/tests/test_no_inline_style.py`.

- `status.py` 3 static `.style("min-width:Npx")` (401/403/406) → `.classes("min-w-[220px]")` /
  `min-w-[200px]` / `min-w-[60px]`. (The `ui.icon().props(f"color={...}")` Quasar color props are
  OUT OF SCOPE — leave.)
- Extend `test_no_inline_style.py` to add `status.py`, `settings.py`, `terminate.py`, `manuals.py`
  (all `.style()`-free). For `eod.py`: its `EOD_CSS` styles a `ui.html()` fragment (out of scope) and
  it has no `.style(`/`:style=` method/binding — add it to the guard ONLY if the guard greps `.style(`/
  `:style=` (not bare `style=`); if its HTML-string `style=` would trip the guard, SKIP eod.py and note
  it's out of scope.

**Step 1 — implement** status.py widths + run → green; grep status.py `.style(` → ZERO.

**Step 2 — extend the guard** + run → green.

**Step 3 — commit:** `refactor(webgui): status widths Tailwind + guard utility pages .style()-free (Phase 8)`

---

### Task 8.2: Browser gate + final docs

**Step 1 — browser gate.** Restart the preview (stop+start). On **/portfolio**: proxy/stream status
bar colors (up green / down red / muted) — check no class stacking after a poll tick; tabs render.
On **/driver**: the control-state badge bg (Enable/Disable state) + (if a morning run is cached) the
grade badge + the perf P&L cell colors (green/red) in the breakdown tables. On **/status**: the
freshness table widths + the up/down component badges (Quasar props — unchanged). Screenshot
portfolio + driver; confirm **no console errors**.

**Step 2 — fix any regressions**, re-screenshot.

**Step 3 — FINAL DOCS (the migration is COMPLETE).**
- `CLAUDE.md`: update the status snapshot + the top "Last updated" note → **Phases 0–8 DONE — the
  ENTIRE webgui is Tailwind-only** (every `.style()`/`:style=` removed except the documented out-of-
  scope set: Highcharts dicts, `ui.html()` fragments + their CSS [EOD/Gamma Explain/Analyze], Quasar
  `color=` props; the ONE escape hatch is per-page Quasar-internal `ui.add_css` [field/tab/menu/table
  internals]). State the final green count.
- Update the design doc `docs/plans/2026-06-28-tailwind-first-ui-migration-design.md` Status line →
  all phases complete.
- Verify the "App theme — dark-navy" section + "UI styling standard" section in CLAUDE.md read
  correctly post-migration (they were rewritten in P4 — confirm no stale `DASHBOARD_CSS` refs).

**Step 4 — commit** `docs: Tailwind migration COMPLETE (Phases 0–8) — entire webgui Tailwind-only`.

---

## Per-task done checklist
suite green · each converted page free of `.style(`/`:style=` · dynamic colors via local class maps
(reactive in-place via remove/add; rebuilt via add=; the driver P&L slot via `:class`) · `DRIVER_CSS`
kept (Quasar-internal) · `EOD_CSS`/Highcharts/Quasar-props untouched · screenshot parity + no console
errors · all docs updated.

## Notes / risks
- **Portfolio status labels are PERSISTENT** (version-poll updates in place) → `remove=` the local
  3-class set or colors stack. (Same gotcha as Phase 2/3a/5.)
- **Driver P&L slot:** the `:style=`→`:class` change mirrors Phase 3a — stamp a `_pnl_class` row field
  and bind `:class`; the JIT generates the `text-[#hex]`.
- **Driver badges are REBUILT** (the `_render_*` clear+rebuild) → `add=` is correct (no remove needed).
- **`DRIVER_CSS` + `EOD_CSS` are NOT deleted** — DRIVER_CSS is the Quasar-internal table escape hatch;
  EOD_CSS styles an out-of-scope `ui.html()` fragment.
- Do NOT touch Highcharts, Quasar `color=` props, or the `ui.html()` report fragments.
