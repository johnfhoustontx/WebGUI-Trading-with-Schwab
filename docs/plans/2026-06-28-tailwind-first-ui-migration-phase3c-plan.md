# Tailwind-first UI migration — Implementation Plan (Phase 3c: chart-heavy screens)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert the **Gamma** and **Expected-Move** pages to Tailwind-only. `expected_move.py`
is already clean (no `.style()`/`ui.add_css`) — it just joins the guard. `gamma.py` has 8
`.style()`: 6 dynamic **panel-flex** layout values + 2 dynamic **colors**. Highcharts option
dicts and the standalone Explain/Analyze HTML documents stay **out of scope** (per the standard).

**Architecture:**
- **Colors (palette-map, as in 3a/3b):** the hedge-pressure tile (gamma:762, finite
  `{#66bb6a,#ef5350,#bdbdbd}`) → `TXT_POS/TXT_NEG/TXT_NEUTRAL`; the collector status bar
  (gamma:816, finite `{green,red,gray,#c48b00}` from `gex_status.classify_collector_status`)
  → a local `status_color_class` map (exact values preserved as `text-[…]` arbitrary classes),
  reactive → `.classes(remove=…, add=…)`.
- **Panel flex (continuous → runtime arbitrary class):** the bar/heatmap column flex ratio
  (gamma:615/627/641/642/647/648) is set from `panel_flex(n_cols)` — a **genuinely continuous**
  value (~82 distinct ratios over a session), so there is NO finite palette to map to. This is
  the one legitimate case for a **runtime arbitrary-value class** (`flex-[{w}_1_0%]`, JIT-
  generated). Because it changes every repaint, reset it via `.classes(remove=prev, add=new)`
  (track the previous flex class per box). A new one-line clause is added to the standard for
  this continuous-value case.
- **`EXPLAIN_CSS` stays:** it styles the Explain dialog's `ui.html()` fragment (raw HTML, not
  NiceGUI components) — out of scope, like the EOD/Analyze docs.

**Tech Stack:** NiceGUI 3.13 (Tailwind JIT), pytest from `webgui/`. Baseline **581**.

**Standing rules:** TDD; preserve exact look; reactive color/flex resets use
`.classes(remove=…, add=…)`; DO NOT touch Highcharts builders, the Explain/Analyze HTML
(Tier-2 `compute.py`), `EXPLAIN_CSS`, `DASHBOARD_CSS`/`QUASAR_INTERNAL_CSS`, or any other page.
Run `..\.venv\Scripts\python -m pytest -q` after each task.

---

### Task 3c.1: Standard clause for continuous values

**Files:** `CLAUDE.md` (the "UI styling standard" → "Dynamic / data-driven values" bullet).

**Step 1 — implement.** Append one sentence to that bullet: a **genuinely continuous** value with
no finite set (e.g. a computed `flex-grow` ratio) may use a **runtime arbitrary-value class**
(`flex-[{w}_1_0%]`, JIT-generated) reset via `.classes(remove=prev, add=new)` — this is distinct
from data-driven COLORS, which always map to a fixed finite palette.

**Step 2 — commit:** `docs: standard clause for continuous-value runtime arbitrary classes (Phase 3c)`

---

### Task 3c.2: `gamma.py` — palette-map the 2 dynamic colors

**Files:** Modify `webgui/pages/options/gamma.py`; Test `webgui/tests/test_options_gamma.py`.

**Step 1 — failing test.** Add a `status_color_class(color)` mapper test:
```python
def test_status_color_class_maps_collector_states():
    from pages.options import gamma as g
    assert g.status_color_class("green") == "text-[green]"
    assert g.status_color_class("red") == "text-[red]"
    assert g.status_color_class("gray") == "text-[gray]"
    assert g.status_color_class("#c48b00") == "text-[#c48b00]"
    assert g.status_color_class("") == "text-[#666666]"     # fallback (also the compute default)
```

**Step 2 — run, expect fail.**

**Step 3 — implement.**
- **Hedge tile (759-762):** the `tile(label, val, color=…)` closure — change the signature to take
  a CLASS (default `TXT_NEUTRAL`), pass `TXT_POS`/`TXT_NEG` at the call site (765:
  `TXT_POS if hp >= 0 else TXT_NEG`), and apply `.classes(f"text-base font-bold {cls}")` instead
  of `.style(f"color:{color}")`. Import `from .theme import TXT_POS, TXT_NEG, TXT_NEUTRAL`. The
  tiles are rebuilt each render → `add=` (no remove needed).
- **Status bar (810-816):** add `_STATUS_CLASS = {"green":"text-[green]","red":"text-[red]",
  "gray":"text-[gray]","#c48b00":"text-[#c48b00]"}` + `status_color_class(color)` (→ class or
  `text-[#666666]` fallback) + `_ALL_STATUS = sorted(set(_STATUS_CLASS.values()) | {"text-[#666666]"})`
  joined. In `_paint_status`, replace `status_lbl.style(f"color:{color}")` with
  `status_lbl.classes(remove=" ".join(_ALL_STATUS), add=status_color_class(color))` (REACTIVE — the
  status bar repaints, so the remove-set prevents accumulation). Keep reading `st["status_color"]`
  as the map key (it's the finite value from `classify_collector_status`).

**Step 4 — run** (gamma test + full suite) → green.

**Step 5 — commit:** `refactor(webgui): gamma status + hedge-tile colors palette-mapped (Phase 3c)`

---

### Task 3c.3: `gamma.py` — panel flex → runtime arbitrary class

**Files:** Modify `webgui/pages/options/gamma.py`; Test `webgui/tests/test_options_gamma.py`.

The 6 flex `.style()` calls: init `chart_box`/`heatmap_box` at `flex: 0.5 1 0%` (615/627); term
`flex: 1 1 0%` / `flex: 0 0 0px` (641/642); proportional `flex: {bar_w} 1 0%` / `flex: {heat_w} 1
0%` (647/648).

**Step 1 — failing test.** Add a pure `flex_class(grow)` helper test:
```python
def test_flex_class_builds_arbitrary_value():
    from pages.options import gamma as g
    assert g.flex_class(0.5) == "flex-[0.5_1_0%]"
    assert g.flex_class(1) == "flex-[1_1_0%]"
    assert g.flex_class(0, basis="0px") == "flex-[0_0_0px]"
```

**Step 2 — run, expect fail.**

**Step 3 — implement.**
- Add `flex_class(grow, grow2=1, basis="0%")` → `f"flex-[{grow}_{grow2}_{basis}]"` (Tailwind
  arbitrary; `_` = space). For the term hidden case use `flex_class(0, grow2=0, basis="0px")`.
- Track the current flex class per box (a closure dict `flex_cur = {"chart": "", "heat": ""}`).
- Add `_set_flex_class(box, key, cls)`: `box.classes(remove=flex_cur[key], add=cls); flex_cur[key]=cls`
  (the `remove=""` on first call is a harmless no-op).
- Replace the init `.style("flex: 0.5 1 0%")` (615/627) — either set the initial class inline via
  `.classes(flex_class(0.5))` at creation (then seed `flex_cur`), or call `_set_flex_class` once.
- Replace `_apply_flex` (641/642/647/648): term → `_set_flex_class(chart_box,"chart",flex_class(1))`
  + `_set_flex_class(heatmap_box,"heat",flex_class(0,grow2=0,basis="0px"))`; proportional →
  `_set_flex_class(chart_box,"chart",flex_class(bar_w))` + `_set_flex_class(heatmap_box,"heat",
  flex_class(heat_w))`. Keep the `set_visibility` calls unchanged.

**Step 4 — run** (gamma test + full suite) → green.

**Step 5 — commit:** `refactor(webgui): gamma panel flex via runtime arbitrary Tailwind class (Phase 3c)`

---

### Task 3c.4: Guard + browser gate + CLAUDE.md status

**Step 1 — guard.** Extend `test_no_inline_style.py` to include `gamma.py` + `expected_move.py`
in the `.style(`/`:style=`-free list. (gamma's `EXPLAIN_CSS` is a `ui.add_css` of a CSS STRING,
not `.style(`/`:style=`, so it passes; confirm the guard doesn't false-match it.)

**Step 2 — run** → green.

**Step 3 — browser gate.** Restart the preview (stop+start). On **Gamma**: verify the
bar/heatmap **panel split resizes** (the flex classes apply — load a symbol; off-hours the
panels still render at the init 0.5/0.5 or persisted ratio), the **DEX view hedge-pressure
tiles** show green/red/neutral values (switch to DEX), and the **collector status bar** shows
its colored label (off-hours likely gray "idle" — confirm the gray class applies, not black/
unstyled). On **Expected-Move**: it's unchanged (already Tailwind) — smoke that it renders.
Screenshot Gamma; confirm **no console errors**. If the `flex-[…]` arbitrary class doesn't
resize the panels, STOP and flag.

**Step 4 — fix any regressions**, re-screenshot.

**Step 5 — CLAUDE.md status** snapshot + Last-updated: Phase 3c done (Gamma + Expected-Move;
gamma colors palette-mapped, panel flex via runtime arbitrary class; Highcharts dicts + Explain/
Analyze HTML out of scope). **Phase 3 (all Options screens) COMPLETE.** Next: Phase 4 (Trade —
flips `DASHBOARD_CSS`, then the legacy cleanup). Commit.

---

## Per-task done checklist
suite green · gamma + expected_move free of `.style(`/`:style=` (`EXPLAIN_CSS`/Highcharts/Explain-
HTML exempt) · dynamic colors palette-mapped (reactive status via remove/add) · panel flex via
runtime arbitrary class with tracked-previous removal · screenshot parity + no console errors ·
CLAUDE.md updated.

## Notes / risks
- **The status bar is REACTIVE** — use `remove=` (the full status-class set) so colors don't
  accumulate across repaints. The hedge tiles are REBUILT per render — `add=` is fine.
- **Panel flex tracked-previous:** if you forget to seed `flex_cur` with the init class, the first
  `_apply_flex` won't remove the init flex and you'll stack two `flex-[…]` classes (the later one
  may not win). Seed it.
- **Out of scope (do NOT convert):** Highcharts option dicts (chart colors), the Explain/Analyze
  HTML built in Tier-2 `compute.py`, and `EXPLAIN_CSS` (styles the `ui.html()` Explain fragment).
- Expected-Move needs no code change — only the guard addition.
