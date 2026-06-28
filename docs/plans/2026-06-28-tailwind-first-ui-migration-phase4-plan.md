# Tailwind-first UI migration — Implementation Plan (Phase 4: Trade + legacy cleanup)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert the **Trade** page (`webgui/pages/trade.py`) to Tailwind-only — the LAST
`DASHBOARD_CSS` consumer — then perform the **legacy cleanup**: delete `DASHBOARD_CSS` entirely
so `theme.py` = tokens + `QUASAR_INTERNAL_CSS` only.

**Architecture:**
- **Token swaps:** `.calc-v2` wrapper keeps its class as a scope hook + adds `PAGE` (trade.py:330);
  `.calc-card`→`CARD` (8 sites: 351/352/353/365/444/456/464/474, keeping the `flex-1 min-w-[…]`
  utilities); `.calc-eyebrow`→`EYEBROW` (7 sites: 337/355/421/445/457/465/475);
  `.cv2-btn-primary`→`BTN_PRIMARY` (336, keep `color=None`); inject `QUASAR_INTERNAL_CSS` instead
  of `DASHBOARD_CSS` (317).
- **Dynamic colors → palette-map (LOCAL maps — these hexes differ from `TXT_*`):**
  - **Verdict/bias 3-set** (`BUY_COLOR #2e7d32` / `HOLD_COLOR #f9a825` / `SELL_COLOR #c62828`):
    add `verdict_text_class(v)` + `bias_text_class(b)` → `text-[#2e7d32]`/`text-[#f9a825]`/
    `text-[#c62828]` (keep the existing `verdict_color`/`bias_color` hex fns — the Markov chart
    or other code may still read them). Sites: 377/390/425/448/453/482 (some are ternaries over a
    score → map the resulting state). Statics 403/484 (`SELL_COLOR`) → `text-[#c62828]`; 331
    (`#eaf0fb`) → `LABEL`.
  - **Markov band chip 5-set** (`_MK_BAND_COLORS` = #c0392b/#e67e22/#7f8c8d/#27ae60/#1e8449,
    used as the chip BACKGROUND at 537): add a parallel `_MK_BAND_BG` (or have `markov_band_chip`
    return a `class` key) → `bg-[#c0392b]` … and apply via `.classes()`. **`_MK_BAND_COLORS` STAYS**
    (the Highcharts Markov chart reads it — out of scope).
- **Reactive vs build-once:** the verdict cards are **refilled in place** (`_fill_verdict_card`),
  so any label whose color is set on each fill is REACTIVE → use `.classes(remove=<the local
  color-class set>, add=…)`. Build-once labels (header) use `add=`. CHECK each site.
- **Out of scope:** the Markov/MTF/gauge **Highcharts** figures (`markov_forecast_figure`,
  `_MK_AREA_COLORS`, etc.) — chart config, untouched. No `ui.html()` inline-style fragments and
  no `:style=` table slots in trade.py (verified).

**LEGACY CLEANUP (the payoff):** after Trade flips, `DASHBOARD_CSS` has ZERO consumers (Calc/Sim
already inject `QUASAR_INTERNAL_CSS`). DELETE the `DASHBOARD_CSS` constant from `theme.py`; remove
the Phase-0 `test_legacy_dashboard_css_still_present` test (+ any other `DASHBOARD_CSS` assertion);
rewrite the `CLAUDE.md` "App theme — dark-navy 'dashboard'" section to describe the **token**
reality (no more `DASHBOARD_CSS`).

**Tech Stack:** NiceGUI 3.13 (Tailwind JIT), pytest from `webgui/`. Baseline **584**.

**Standing rules:** TDD; reactive recolors via `.classes(remove=…, add=…)`; preserve exact look;
do NOT touch Highcharts builders, `QUASAR_INTERNAL_CSS`, or other pages. Run
`..\.venv\Scripts\python -m pytest -q` after each task.

---

### Task 4.1: `trade.py` — token swaps + color palette-maps

**Files:** Modify `webgui/pages/trade.py`; Test `webgui/tests/test_trade.py`.

**Step 1 — failing tests.** Add mapper tests:
```python
def test_verdict_text_class_maps_states():
    from pages import trade as t
    assert t.verdict_text_class("BUY") == "text-[#2e7d32]"
    assert t.verdict_text_class("SELL") == "text-[#c62828]"
    assert t.verdict_text_class("HOLD") == "text-[#f9a825]"
def test_markov_band_bg_class_maps_5_bands():
    from pages import trade as t
    # one assertion per band edge — exact hexes from _MK_BAND_COLORS
    assert t.markov_band_bg_class(0) == "bg-[#c0392b]"
    assert t.markov_band_bg_class(4) == "bg-[#1e8449]"
```
(Adjust to the actual `markov_band_chip` shape — if it returns an index/band, map that; preserve
the exact `_MK_BAND_COLORS` hexes.)

**Step 2 — run, expect fail.**

**Step 3 — implement.**
- Imports: `from pages.options.theme import QUASAR_INTERNAL_CSS, PAGE, CARD, EYEBROW, BTN_PRIMARY, LABEL`.
- Line 317: `ui.add_css(DASHBOARD_CSS)` → `ui.add_css(QUASAR_INTERNAL_CSS)`; drop the
  `from … import DASHBOARD_CSS`.
- Line 330 wrapper: `"calc-v2 w-full gap-3"` → `f"calc-v2 {PAGE} w-full gap-3"`.
- 8 `.calc-card …` → `f"{CARD} …"` (keep `flex-1 min-w-[…]`/`w-full`). 7 `.calc-eyebrow` → `EYEBROW`.
- Line 336 `.classes("cv2-btn-primary")` → `.classes(BTN_PRIMARY)` (keep `color=None`).
- Add `verdict_text_class`/`bias_text_class` + a `_VERDICT_TEXT` local set (the 3 `text-[…]`
  values, for the reactive `remove=`). Replace each color `.style(f"color:{verdict_color(...)}")`
  with `.classes(...)`: REACTIVE verdict-card sites → `.classes(remove=" ".join(_VERDICT_TEXT),
  add=verdict_text_class(state))`; build-once → `add=`. Statics (331/403/484) → `.classes(LABEL)` /
  `.classes("text-[#c62828]")`.
- Markov chip (537): replace `.style(f"background:{chip['color']}")` with `.classes(...)` using a
  `markov_band_bg_class` (5-band → `bg-[…]`). If the chip is in the refilled Markov card, treat as
  reactive (remove the 5-bg set, add the new) — CHECK; else `add=`.
- Verify `_MK_BAND_COLORS`/`_MK_AREA_COLORS` (chart) are UNTOUCHED.

**Step 4 — run** (trade test + full suite) → green.

**Step 5 — commit:** `refactor(webgui): trade page Tailwind tokens + verdict/markov palette-maps (Phase 4)`

---

### Task 4.2: Legacy cleanup — delete `DASHBOARD_CSS`

**Files:** Modify `webgui/pages/options/theme.py`, `webgui/tests/test_theme.py`, `CLAUDE.md`.

**Step 0 — confirm zero consumers.** grep `DASHBOARD_CSS` across `webgui/` — it must appear ONLY
in `theme.py` (its own definition) + `test_theme.py` (the Phase-0 guard). If ANY page still imports
it, STOP (Trade wasn't fully flipped). Also grep `.calc-card`/`.cv2-btn`/`.cv2-btn-primary`/
`.calc-eyebrow` as bare class strings — should be ZERO outside theme.py/QUASAR_INTERNAL_CSS.

**Step 1 — update the guard test.** Replace `test_legacy_dashboard_css_still_present` (which
asserts `DASHBOARD_CSS` contains `.calc-card`/`.cv2-btn-primary`) with
`test_dashboard_css_removed`:
```python
def test_dashboard_css_removed():
    assert not hasattr(theme, "DASHBOARD_CSS")          # deleted after the last consumer flipped
    # the Quasar-internal rules now live ONLY in QUASAR_INTERNAL_CSS
    assert ".q-field__control" in theme.QUASAR_INTERNAL_CSS
    assert ".strat-menu-navy" in theme.QUASAR_INTERNAL_CSS
```
Run → fail (DASHBOARD_CSS still exists).

**Step 2 — delete `DASHBOARD_CSS`** from `theme.py` (the whole constant, lines ~39-108) + its
module docstring references. Keep `QUASAR_INTERNAL_CSS` + all tokens. Run → the new test passes +
full suite green.

**Step 3 — rewrite the CLAUDE.md "App theme — dark-navy 'dashboard'" section** to describe the
TOKEN reality: `theme.py` now exposes the token vocabulary (`PAGE`/`CARD`/`EYEBROW`/`LABEL`/`MUTED`/
`BTN`/`BTN_PRIMARY`/`STRATEGY_BTN`/`TXT_*`/`BTN_3D*`) + `QUASAR_INTERNAL_CSS` (the field/tab/menu
internals scoped under `.calc-v2`/`.strategy-menu-btn`/`.leg-*` hooks). Update the "Apply to a new
page" example to `ui.add_css(QUASAR_INTERNAL_CSS)` + `.classes(f"calc-v2 {PAGE} …")` + `.classes(CARD)`
+ `.classes(BTN_PRIMARY)`. Remove the ⚠️ "migrating" banner (it's done). Keep the palette table.

**Step 4 — commit:** `refactor(webgui): delete dead DASHBOARD_CSS — theme is tokens + QUASAR_INTERNAL_CSS (Phase 4)`

---

### Task 4.3: Guard + browser gate + CLAUDE.md status

**Step 1 — guard.** Add `trade.py` to the `test_no_inline_style.py` `.style(`/`:style=`-free list.
Run → green.

**Step 2 — browser gate.** Restart the preview (stop+start). On **Trade**: Analyze a symbol (e.g.
AAPL). Verify the navy gradient page (PAGE), the three verdict cards (Position/Investor/Markov) as
`CARD`s, the **verdict labels colored** (BUY green `#2e7d32` / HOLD amber / SELL red — Analyze a few
symbols to see different verdicts), the **Markov band chip background** colored, the eyebrow headers,
the `BTN_PRIMARY` Analyze button, and that the **Markov/MTF Highcharts still render** (out of scope —
must be visually intact). Confirm the input field box still styled (QUASAR_INTERNAL_CSS via `.calc-v2`
hook). Screenshot; confirm **no console errors**.
**Also smoke a Calculator + Simulator page** (they share `theme.py`) to confirm deleting
`DASHBOARD_CSS` didn't regress them (they already inject `QUASAR_INTERNAL_CSS`, so they should be
fine — but verify the navy theme still renders).

**Step 3 — fix any regressions**, re-screenshot.

**Step 4 — CLAUDE.md status** snapshot + Last-updated: **Phase 4 DONE — `DASHBOARD_CSS` deleted;
`theme.py` = tokens + `QUASAR_INTERNAL_CSS`.** The entire **Options section + Trade** are Tailwind-
only. Next: Phase 5 (Sentiment + Rotation — the highest `.style()` count, ~58). Commit.

---

## Per-task done checklist
suite green · trade free of `.style(`/`:style=` · verdict/bias/markov colors palette-mapped
(reactive via remove/add where the card refills) · `DASHBOARD_CSS` DELETED (zero consumers, theme =
tokens + QUASAR_INTERNAL_CSS) · Phase-0 dashboard-present test replaced · CLAUDE.md "App theme"
section rewritten + status updated · screenshot parity (Trade + Calc + Sim) + no console errors.

## Notes / risks
- **The verdict palette is DARKER than `TXT_*`** (`#2e7d32` vs `#66bb6a`) — keep a LOCAL map, do
  NOT reuse `TXT_POS`/`TXT_NEG`/`TXT_NEUTRAL` (would change the look).
- **`_MK_BAND_COLORS` is dual-use** — the chip (in scope → derive a `bg-[…]` class map) AND the
  Highcharts chart (out of scope → leave the hex list intact). Don't delete it.
- **Deleting `DASHBOARD_CSS` is safe ONLY after Step 0 confirms zero consumers** — if Calc/Sim/Trade
  still reference it, the field/tab styling vanishes app-wide. Grep first.
- **Reactive verdict labels:** the verdict cards refill in place, so a plain `add=` would accumulate
  `text-[…]` classes across re-analyses. Use `remove=` the local 3-class set.
