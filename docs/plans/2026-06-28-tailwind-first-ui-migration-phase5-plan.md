# Tailwind-first UI migration — Implementation Plan (Phase 5: Sentiment + Sector Rotation)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert `webgui/pages/sentiment.py` (~45 `.style()`) and
`webgui/pages/sentiment_rotation.py` (~13 `.style()`) to Tailwind-only — the heaviest phase.
These are TOP-LEVEL pages with their OWN look (NOT the `.calc-v2` dashboard theme), so
**preserve their current appearance** (convert + light polish) — do NOT impose `PAGE`/`CARD`
tokens. Just turn every `.style()` into a Tailwind `.classes()`.

**Architecture (three mechanical patterns):**
1. **Static layout (`width`/`min-width`/`flex`/`padding`/`opacity`/overflow):** → arbitrary
   Tailwind (`w-[170px]`, `min-w-[210px]`, `flex-[1.4]`, `pl-[14px]`, `opacity-85`,
   `overflow-hidden text-ellipsis whitespace-nowrap`). ~30 sites.
2. **Dynamic colors from finite sets → LOCAL class maps.** Both files share a local 5-color
   palette (`#66bb6a`/`#ef5350`/`#ffd54f`/`#9e9e9e`/`#3fb6c7`) that does NOT fully match `TXT_*`
   (yellow/cyan have no token; flat differs `#9e9e9e` vs `#bdbdbd`), so define LOCAL class
   constants per file (text + bg variants) and convert each color helper to return a class:
   `traffic_color`(bg 3-set)/`bias_color`/`pct_color`/`pcr_color`/`rrg_color`(4)/`sc_color`/trend/
   rotation in sentiment.py; `quadrant_color`(4)/`_regime_color`/tcolor in rotation.py.
   - **Rebuilt-per-repaint elements** (component/sector/industry/rotation/quadrant table rows —
     built in a `.clear()`+loop) → `.classes(add=…)`.
   - **Persistent, updated-in-place elements** → `.classes(remove=<the local set>, add=…)`:
     sentiment.py `bias_lbl`(644), the 4 `tile_cards` bg(661), `regime_badge`(683),
     `rotation_lbl`(813); rotation.py `headline_lbl`(207).
3. **The `.sent-sectors` `ui.add_css` block (sentiment.py:448):** reachable — replace with
   Tailwind on the row elements (`border-b border-white/5`, `hover:bg-white/[0.04]`, per-cell
   `border-r border-white/[0.04]`, `bg-white/[0.02]` for industry rows). Delete the block.

**Out of scope:** all Highcharts figures (the 4 gauges via `gauge.py`, the 30d history line, the
RRG spline/quadrant charts) — chart config; and any `ui.html()` inline-style fragment (none found).

**Tech Stack:** NiceGUI 3.13 (Tailwind JIT), pytest from `webgui/`. Baseline **587**.

**Standing rules:** TDD the color-class mappers; reactive in-place recolors via
`.classes(remove=…, add=…)`; preserve exact look (these pages keep their own styling — no token
imposition); do NOT touch the gauges/charts or `theme.py`. Run `..\.venv\Scripts\python -m pytest
-q` after each task.

---

### Task 5.1: `sentiment.py` — local color-class maps + dynamic-color conversion

**Files:** Modify `webgui/pages/sentiment.py`; Test `webgui/tests/test_sentiment.py`.

**Step 1 — local class constants + mappers.** Add module-level LOCAL Tailwind class constants
(text + bg) mirroring the existing `CLR_*` hexes EXACTLY, and convert each color helper to return
a class. Add tests:
```python
# constants (exact hexes preserved)
TXT_G="text-[#66bb6a]"; TXT_R="text-[#ef5350]"; TXT_Y="text-[#ffd54f]"; TXT_FLAT="text-[#9e9e9e]"; TXT_CY="text-[#3fb6c7]"
BG_G="bg-[#66bb6a]"; BG_R="bg-[#ef5350]"; BG_Y="bg-[#ffd54f]"
TRAFFIC_BG_CLASSES = "bg-[#66bb6a] bg-[#ef5350] bg-[#ffd54f]"   # remove-set for the tile bg swap
SENT_TEXT_CLASSES = "text-[#66bb6a] text-[#ef5350] text-[#ffd54f] text-[#9e9e9e] text-[#3fb6c7]"  # remove-set for in-place text recolors
```
```python
def test_traffic_bg_class_maps_bands():
    from pages import sentiment as s
    assert s.traffic_bg_class(7) == "bg-[#66bb6a]"
    assert s.traffic_bg_class(4) == "bg-[#ef5350]"
    assert s.traffic_bg_class(5.5) == "bg-[#ffd54f]"
def test_pcr_text_class_and_rrg_text_class():
    from pages import sentiment as s
    assert s.pcr_text_class(0.9) == "text-[#66bb6a]"
    assert s.pcr_text_class(1.1) == "text-[#ef5350]"
    assert s.pcr_text_class(1.0) == "text-[#9e9e9e]"
    assert s.rrg_text_class("Improving") == "text-[#3fb6c7]"
    assert s.rrg_text_class("Lagging") == "text-[#ef5350]"
```
(Add equivalents for `bias`/`pct`/`sc`/trend/rotation — keep the existing hex `*_color` fns OR
repoint them; whichever keeps callers cleanest. Mirror the exact band thresholds.)

**Step 2 — run, expect fail.**

**Step 3 — implement the color conversions.** Replace every dynamic color `.style()` with `.classes()`:
- **Rebuilt rows** (component 603-604; sector 736/737/738-739/743/746/748; industry 763-768/772/775/777)
  → `.classes(add=<width classes> + " " + <color class>)` (these labels are freshly built in the loop;
  fold the static width in here too OR leave width for Task 5.2 — your call, but do the COLOR now).
- **In-place** → remove/add: `bias_lbl`(644) `.classes(remove=SENT_TEXT_CLASSES, add=bias_text_class(...))`;
  the 4 `tile_cards`(661) `.classes(remove=TRAFFIC_BG_CLASSES, add=traffic_bg_class(total))`;
  `regime_badge`(683) `remove=SENT_TEXT_CLASSES`; `rotation_lbl`(813) `remove=SENT_TEXT_CLASSES`.
- The signal-tile labels `color:#111`(553/554) are STATIC dark text on the colored tile bg →
  `.classes("text-[#111]")` (do here or in 5.2). The `text-negative`/`text-warning`(563/564) Quasar
  classes are already non-`.style()` — leave or normalize to `text-[#ef5350]`/`text-[#ffd54f]`
  (light polish, optional).

**Step 4 — run** (sentiment test + full suite) → green.

**Step 5 — commit:** `refactor(webgui): sentiment dynamic colors → local Tailwind class maps (Phase 5)`

---

### Task 5.2: `sentiment.py` — static layout + `.sent-sectors` CSS block

**Files:** Modify `webgui/pages/sentiment.py`; Test: existing render-smoke + the guard (5.4).

**Step 1 — convert static `.style()`** (if not already folded in 5.1): all `width:Npx` →
`w-[Npx]`; `min-width:Npx` → `min-w-[Npx]`; `flex:1;min-width:0` → `flex-1 min-w-0`;
`flex:1;min-width:160px` → `flex-1 min-w-[160px]`; `padding-left:14px` → `pl-[14px]`;
`opacity:0.85/0.8` → `opacity-85/opacity-80`; the overflow combo →
`overflow-hidden text-ellipsis whitespace-nowrap`; gauge boxes `width:170px;height:120px` →
`w-[170px] h-[120px]`. The `q-pa-md`/`q-pa-sm`/`q-mt-xs` Quasar spacing classes are NOT `.style()` —
leave them (or normalize to Tailwind `p-*` if trivial — optional light polish).

**Step 2 — replace the `.sent-sectors` `ui.add_css` block** (448) with Tailwind on the row
elements: the sector/industry row `ui.row()`s get `border-b border-white/5 hover:bg-white/[0.04]`
(sector) / `bg-white/[0.02]` (industry); each cell `> div` gets `border-r border-white/[0.04]`
(drop on last). Delete the `ui.add_css('''…''')` call + the CSS string. (Apply the per-cell
border via a small helper or on each cell's `.classes()`.) Verify the `.sent-sectors` scope class
is no longer needed (remove it from `sector_box` if nothing else uses it).

**Step 3 — run** full suite → green; grep sentiment.py for `.style(` → ZERO.

**Step 4 — commit:** `refactor(webgui): sentiment static layout + sector-table CSS → Tailwind (Phase 5)`

---

### Task 5.3: `sentiment_rotation.py` — colors + layout

**Files:** Modify `webgui/pages/sentiment_rotation.py`; Test `webgui/tests/test_sentiment_rotation.py`.

**Step 1 — failing test** for the local mappers `quadrant_text_class` (4-set) + `regime_text_class`:
```python
def test_quadrant_text_class():
    from pages import sentiment_rotation as r
    assert r.quadrant_text_class("Leading") == "text-[#66bb6a]"
    assert r.quadrant_text_class("Improving") == "text-[#3fb6c7]"
    assert r.quadrant_text_class("Weakening") == "text-[#ffd54f]"
    assert r.quadrant_text_class("Lagging") == "text-[#ef5350]"
```

**Step 2 — run, expect fail.**

**Step 3 — implement.** Same local class constants as sentiment.py (text set). Convert:
- Static: `flex:1.4;min-width:0`→`flex-[1.4] min-w-0`; `flex:1;min-width:0`→`flex-1 min-w-0`;
  all `width:Npx`→`w-[Npx]` (150/55/90/90/110/60 + header widths).
- Colors: rebuilt rows (216/220-221/230 row + the row-level `style(f"color:{r['color']}")` on the
  `ui.row()` at 230) → `.classes(add=quadrant_text_class(...))`; the in-place `headline_lbl`(207) →
  `.classes(remove=<local text set>, add=regime_text_class(...))`.
- The row-level color on `ui.row()`(230) sets the default text color for all cells — keep that
  behavior by putting the color class on the row's `.classes()`.

**Step 4 — run** (rotation test + full suite) → green; grep rotation.py for `.style(` → ZERO.

**Step 5 — commit:** `refactor(webgui): sector-rotation Tailwind colors + layout (Phase 5)`

---

### Task 5.4: Guard + browser gate + CLAUDE.md status

**Step 1 — guard.** Add `sentiment.py` + `sentiment_rotation.py` to `test_no_inline_style.py`. Run → green.

**Step 2 — browser gate.** Restart the preview (stop+start). On **/sentiment**: verify the two gauge
columns render (gauges are Highcharts — out of scope, must be intact), the **traffic-light Signals
tiles** show their background color (green/amber/red by composite), the component table + the
**sector & industry table** render with per-cell colored %/P-C/RRG values + the row borders/hover
(the converted `.sent-sectors`), expand a sector to see industry rows. On **/sentiment/rotation**:
the headline color, the rotation-from/into columns, and the quadrant table colored rows + RRG chart.
Screenshot both; confirm **no console errors**. (These pages auto/refresh — if a tile recolor stacks
classes after a refresh, the `remove=` was incomplete; fix it.)

**Step 3 — fix any regressions**, re-screenshot.

**Step 4 — CLAUDE.md status** snapshot + Last-updated: Phase 5 done (Sentiment + Rotation Tailwind;
local color-class maps; sector-table CSS → Tailwind; gauges/charts out of scope). Next: Phase 6
(Portfolio). Commit.

---

## Per-task done checklist
suite green · both files free of `.style(`/`:style=` · dynamic colors via LOCAL class maps (reactive
in-place via remove/add; rebuilt rows via add=) · `.sent-sectors` CSS block → Tailwind on rows ·
gauges/charts untouched · pages keep their OWN look (no token imposition) · screenshot parity +
no console errors · CLAUDE.md updated.

## Notes / risks
- **HUGE site count (~58)** — go methodically; don't miss a `.style()`. The guard test is the
  backstop. Keep static-width conversions exact (`width:170px`→`w-[170px]`, not a scale guess).
- **In-place recolors stack without `remove=`** — these pages auto-refresh every 2 s; the 4 traffic
  tiles + bias/regime/rotation/headline labels are persistent. Their `remove=` set MUST cover every
  class they can apply (the full local text set, or the 3 traffic bg classes). A miss = colors stack
  and the wrong one wins after the first refresh. (Same gotcha as Phase 2/3a.)
- **Preserve the local palette EXACTLY** (`#ffd54f` yellow, `#3fb6c7` cyan, `#9e9e9e` flat are
  page-specific — do NOT substitute `TXT_WARN`/`TXT_NEUTRAL`; the look would shift).
- **Do NOT adopt `PAGE`/`CARD` tokens** — these pages are intentionally NOT the navy dashboard theme.
- Highcharts gauges/charts: untouched (out of scope).
