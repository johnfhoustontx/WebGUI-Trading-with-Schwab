# Tailwind-first UI migration — Implementation Plan (Phase 2: shared Options helpers)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert the shared `pages/options/*` helpers to Tailwind-only styling — remove
all `.style()` from `detail.py`, `header.py`, `overlay.py` — using **strict
palette-mapping** for dynamic data-driven colors (map a finite state → a fixed semantic
Tailwind class), so the conversion propagates Tailwind into every Options screen that
mounts these helpers.

**Architecture:** Dynamic colors come from small **finite sets**, so we define **semantic
text/bg color tokens** in `theme.py` (exact hex preserved as arbitrary values, so the look
is unchanged — convert + light polish) and map each dynamic value's state → token
page-side. Static inline styles (overlay backdrop, fixed widths) become plain Tailwind
utilities. `leg_editor.py`/`strategy_menu.py` already have no `.style()` — they only use
the escape-hatch semantic classes (`.leg-row`/`.strategy-menu-btn`) still styled by the
pages' intact `DASHBOARD_CSS`, so they need only a guard, not a rewrite.

**Critical gotcha — dynamic class swap:** `.style("color:x")` OVERWRITES; `.classes("text-[#x]")`
ACCUMULATES. Labels whose color is set on every repaint (detail tiles, header dot) must use
`el.classes(remove=<all-state-classes>, add=<new-class>)` or they stack conflicting
`text-[...]` classes (equal specificity → unpredictable winner). Provide one helper and use
it everywhere a color class is set reactively.

**Tech Stack:** NiceGUI 3.13 (Tailwind browser JIT — runtime arbitrary values DO generate,
but we still map to fixed semantic tokens per the standard), pytest from `webgui/`.

**Reference:** design doc `...-design.md`; standard in `CLAUDE.md` → "UI styling standard"
(esp. the **Dynamic / data-driven values → MAP TO FIXED PALETTE CLASSES** rule). Phase 0
tokens live in `webgui/pages/options/theme.py`.

**Run tests** (from `webgui/`): `..\.venv\Scripts\python -m pytest -q` (baseline **558**).

---

### Task 2.1: Add semantic state-color tokens to `theme.py`

**Files:** Modify `webgui/pages/options/theme.py`; Test `webgui/tests/test_theme.py`.

**Step 1 — failing test.** Append to `test_theme.py`:
```python
STATE_TOKENS = ["TXT_POS", "TXT_WARN", "TXT_NEG", "TXT_NEUTRAL"]

def test_state_color_tokens_exist_and_are_text_classes():
    for name in STATE_TOKENS:
        val = getattr(theme, name)
        assert isinstance(val, str) and val.startswith("text-["), f"{name} not a text-[] class"
        assert "{" not in val and ";" not in val

def test_state_color_tokens_preserve_exact_hex():
    # convert + light polish: exact colors preserved as arbitrary values.
    assert theme.TXT_POS == "text-[#66bb6a]"
    assert theme.TXT_WARN == "text-[#ffa726]"
    assert theme.TXT_NEG == "text-[#ef5350]"
    assert theme.TXT_NEUTRAL == "text-[#bdbdbd]"
```

**Step 2 — run, expect fail** (`AttributeError: TXT_POS`).
Run: `..\.venv\Scripts\python -m pytest tests/test_theme.py -v`

**Step 3 — implement.** Append to `theme.py` (after the Phase 0 tokens):
```python
# Semantic STATE colors (positive / caution / negative / neutral) — the finite
# palette behind data-driven label colors in detail.py/header.py. Exact hexes from
# detail.py's GREEN/AMBER/RED/NEUTRAL constants, preserved as arbitrary-value classes
# so the look is unchanged. Set reactively via .classes(remove=STATE_TEXT, add=TXT_*)
# so repeated repaints don't stack conflicting text-[...] classes.
TXT_POS = "text-[#66bb6a]"
TXT_WARN = "text-[#ffa726]"
TXT_NEG = "text-[#ef5350]"
TXT_NEUTRAL = "text-[#bdbdbd]"
# Space-joined set for the remove= arg of a reactive color swap.
STATE_TEXT_CLASSES = "text-[#66bb6a] text-[#ffa726] text-[#ef5350] text-[#bdbdbd]"
```

**Step 4 — run, expect pass.** Full suite too: `..\.venv\Scripts\python -m pytest -q` (560).

**Step 5 — commit:** `git add webgui/pages/options/theme.py webgui/tests/test_theme.py && git commit -m "feat(webgui): semantic state-color tokens (Phase 2)"`

---

### Task 2.2: `overlay.py` → Tailwind (static, simplest)

**Files:** Modify `webgui/pages/options/overlay.py`; Test: existing render-smoke covers it (no new behavior). Optionally add a tiny guard in `webgui/tests/test_options_*`—but the file isn't directly unit-tested today; rely on the grep guard in Task 2.5 + the smoke import. Keep it minimal.

**Step 1 — implement.** Replace the two `.style()` calls:
- The overlay element: `_OVERLAY_STYLE` (`position:fixed;inset:0;z-index:9999;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;background:rgba(6,12,24,0.55);backdrop-filter:blur(1px)`) →
  ```python
  overlay = ui.element("div").classes(
      "fixed inset-0 z-[9999] flex flex-col items-center justify-center gap-[14px] "
      "bg-[rgba(6,12,24,0.55)] backdrop-blur-[1px]")
  ```
  Delete the `_OVERLAY_STYLE` constant.
- The label: `.style("color:#e7edf8;font-weight:600;letter-spacing:.02em;")` →
  `.classes("text-[#e7edf8] font-semibold tracking-[.02em]")`.

**Step 2 — run** `..\.venv\Scripts\python -m pytest -q` → 560 green (no regressions; overlay is imported by calculator/simulator smoke).

**Step 3 — verify the overlay still covers the viewport** in the browser (Task 2.6 does this for the Calculator). For now confirm import + suite green.

**Step 4 — commit:** `git commit -am "refactor(webgui): wait overlay Tailwind-only (Phase 2)"`

---

### Task 2.3: `detail.py` → Tailwind (palette-mapped colors + static widths)

**Files:** Modify `webgui/pages/options/detail.py`; Test `webgui/tests/test_options_detail.py`.

The 4 color constants and 7 `.style()` sites:
- `GREEN/AMBER/RED/NEUTRAL` (lines 21-24) are used ONLY as the color passed to `_kv`/`_greek`
  and returned by `pop_color`/`_TILES`. Repoint them to the theme tokens and apply via
  `.classes()`. Keep the names (many refs) but make their VALUES the token classes.
- Static widths: `col ... width:{width}px` (226/257), `width: 44px` (260), gauge
  `width:160px;height:104px` (240).

**Step 1 — failing test.** `pop_color` currently returns a hex; after refactor it returns a
class token. Add to `test_options_detail.py`:
```python
from pages.options import detail, theme

def test_pop_color_returns_state_class_tokens():
    assert detail.pop_color(75) == theme.TXT_POS
    assert detail.pop_color(60) == theme.TXT_WARN
    assert detail.pop_color(40) == theme.TXT_NEG
    assert detail.pop_color("n/a") == theme.TXT_NEUTRAL
```
(Update any existing test that asserted `pop_color` returns a hex — change the expectation
to the token. Read the current `test_options_detail.py` first and adjust those assertions.)

**Step 2 — run, expect fail.**
Run: `..\.venv\Scripts\python -m pytest tests/test_options_detail.py -v`

**Step 3 — implement.**
- Replace the constants with imports/aliases to tokens:
  ```python
  from .theme import TXT_POS, TXT_WARN, TXT_NEG, TXT_NEUTRAL, STATE_TEXT_CLASSES
  GREEN, AMBER, RED, NEUTRAL = TXT_POS, TXT_WARN, TXT_NEG, TXT_NEUTRAL  # now class strings
  ```
- Add a reactive color-swap helper (handles the accumulation gotcha):
  ```python
  def _set_color(lbl, cls):
      lbl.classes(remove=STATE_TEXT_CLASSES, add=cls)
  ```
- `_kv` (line 92-93): `if color: lbl.classes(add=color)` (build-once label — no prior state
  class, so `add=` is fine; or use `_set_color` for uniformity).
- `_greek` (175-176): same — `lbl.classes(add=color)` (built once per render).
- Header tiles update (214): **reactive, repeated** → `_set_color(lbl, color_fn(s))`.
- Static widths → Tailwind. `width` is the caller-passed panel width (default 360):
  - `col = ui.column().classes("shrink-0 gap-1").classes(f"w-[{width}px]")` (226) — `width`
    is a build-time int, so `w-[{width}px]` is a literal per render (JIT generates it). Keep
    a single source: store `expanded_w = f"w-[{width}px]"`.
  - toggle expand (257): `col.classes(remove="w-11", add=expanded_w)`.
  - toggle collapse (260): `col.classes(remove=expanded_w, add="w-11")` (44px = `w-11`).
  - gauge (240): `.classes("shrink-0 w-[160px] h-[104px]")` (drop the `.style`).

**Step 4 — run, expect pass.** `..\.venv\Scripts\python -m pytest tests/test_options_detail.py -v`
then full suite `..\.venv\Scripts\python -m pytest -q` → green.

**Step 5 — commit:** `git commit -am "refactor(webgui): detail panel Tailwind-only, palette-mapped colors (Phase 2)"`

---

### Task 2.4: `header.py` → Tailwind (label→class mapping)

**Files:** Modify `webgui/pages/options/header.py`; Test `webgui/tests/test_options_header.py`.

Two reactive `.style()` sites in `_paint`:
- `regime_badge.style(f"background-color:{reg['color']}")` (47) — map `reg["label"]` → a bg class.
- `dot.style(f"color:{sent.get('color')}")` (49) — map `sent["label"]` → a text class.

**Finite sets (page-side mapping, NO Tier-2 change — labels already in payload):**
- Sentiment dot labels → tokens: `Bullish`→`#1D9E75`, `Bearish`→`#E24B4A`, `Neutral`→`#EFC347`,
  `No data`/unknown→`#666666`. (Note these hexes differ from detail's 4-set, so use a LOCAL
  map with the exact header hexes as `text-[...]` arbitrary classes — do NOT reuse TXT_POS etc.)
- VIX regime labels → bg classes: **TRACE the finite label set** from `scanner_engine.vix_regime`
  (imported in `services/options_svc/compute.py:25`; the engine is in `options-scanner/scanner_engine.py`).
  Build a `_REGIME_BG` map from each label → `bg-[<that label's hex>]` (preserve the engine's
  exact colors). Add a neutral fallback (`bg-[#666666]` or the engine's neutral) for an unknown/empty label.

**Step 1 — failing tests.** Add pure mapping helpers + tests:
```python
# header.py
def sentiment_dot_class(label):
    return _DOT_CLASS.get(label, "text-[#666666]")

def regime_badge_class(label):
    return _REGIME_BG.get(label, "bg-[#666666]")
```
```python
# test_options_header.py
from pages.options import header
def test_sentiment_dot_class_maps_known_labels():
    assert header.sentiment_dot_class("Bullish") == "text-[#1D9E75]"
    assert header.sentiment_dot_class("Bearish") == "text-[#E24B4A]"
    assert header.sentiment_dot_class("Neutral") == "text-[#EFC347]"
    assert header.sentiment_dot_class("No data") == "text-[#666666]"
    assert header.sentiment_dot_class("???") == "text-[#666666]"  # fallback
def test_regime_badge_class_known_and_fallback():
    # assert at least one real regime label maps to a bg-[...] class and unknown falls back
    assert header.regime_badge_class("???") == "bg-[#666666]"
```
(After tracing the real regime labels, add an assertion for one concrete label.)

**Step 2 — run, expect fail.**

**Step 3 — implement.** Add `_DOT_CLASS` / `_REGIME_BG` maps + the two helpers; rewrite `_paint`:
```python
regime_badge.classes(remove=_ALL_REGIME_BG, add=regime_badge_class(reg.get("label", "")))
dot.classes(remove=_ALL_DOT, add=sentiment_dot_class(sent.get("label", "")))
```
where `_ALL_REGIME_BG`/`_ALL_DOT` are the space-joined value sets (for the reactive swap).
Keep `reg.get("label")`/`sent.get("label")` as the state source. Do NOT read `["color"]` for
styling anymore (the payload still carries it; it's just unused by the page now — fine, additive).

**Step 4 — run, expect pass** (header test + full suite).

**Step 5 — commit:** `git commit -am "refactor(webgui): header strip Tailwind-only, label-mapped colors (Phase 2)"`

---

### Task 2.5: Guard `leg_editor.py` / `strategy_menu.py` + grep clean

**Files:** Test `webgui/tests/test_shell.py` (or a new `test_no_inline_style.py`).

**Step 1 — failing guard test.** Add a test asserting the converted helpers are `.style()`-free:
```python
import pathlib
def test_options_helpers_have_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages" / "options"
    for fn in ["detail.py", "header.py", "overlay.py", "leg_editor.py", "strategy_menu.py"]:
        src = (base / fn).read_text(encoding="utf-8")
        assert ".style(" not in src, f"{fn} still uses .style()"
```

**Step 2 — run.** If `leg_editor.py`/`strategy_menu.py` are already clean (expected), this passes
once 2.2-2.4 land. If it fails for them, convert the offending site the same way (they should
have none — verify by reading; do NOT touch their `.leg-row`/`.strategy-menu-btn` escape-hatch
classes, which the pages' `DASHBOARD_CSS` still styles).

**Step 3 — run full suite** → green.

**Step 4 — commit:** `git commit -am "test(webgui): guard Options helpers are .style()-free (Phase 2)"`

---

### Task 2.6: Browser verification + CLAUDE.md status (gate)

**Step 1 — browser.** Restart the preview webgui (it must reload to pick up changes) and verify
on a page that mounts these helpers — the **Scanner** (detail panel + header strip) and the
**Calculator** (overlay). Check: header VIX-regime badge background + sentiment dot color render
(select a regime/sentiment state if data is live; off-hours they may be neutral/no-data — confirm
the neutral classes apply, no uncolored/black text), the detail panel's colored values + collapse
(44px strip ↔ full width) work, and the Calculator's Load overlay still covers the viewport.
Screenshot. If the preview is blocked by a running :8500, ask the user (as in Phase 1).

**Step 2 — fix any regressions**, re-screenshot.

**Step 3 — CLAUDE.md.** Update the "UI styling standard" status snapshot line + the top
"Last updated" note: Phase 2 done (shared Options helpers Tailwind-only; semantic state-color
tokens added; next Phase 3a). Commit `docs: Tailwind migration Phase 2 done`.

---

## Per-task done checklist
`..\.venv\Scripts\python -m pytest -q` green · converted file `grep`-clean of `.style(` ·
dynamic colors mapped to fixed tokens (no runtime hex via `.style()`) · reactive color sets use
`.classes(remove=…, add=…)` (no class accumulation) · screenshot parity · CLAUDE.md updated.

## Notes for the implementer
- **Do NOT** convert dynamic colors by `.classes(f"text-[{hex}]")` from a payload value — the
  standard mandates mapping a finite STATE → a fixed token. (The JIT would render it, but it
  scatters magic hexes and defeats the deduped vocabulary.)
- **Do NOT** touch `DASHBOARD_CSS` / `QUASAR_INTERNAL_CSS` or the pages that still consume them
  (calculator/simulator/trade) — Phase 2 is helpers only.
- The header's regime/sentiment payload still carries `color` — leave it (unused-by-page is fine;
  removing it is a Tier-2 change out of this phase's scope).
