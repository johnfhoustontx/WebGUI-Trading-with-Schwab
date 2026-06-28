# Tailwind-first UI migration — Implementation Plan (Phase 3a: signal-table screens)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert the six signal-table Options screens — **Scanner, Swing, Captured, Paper,
Paper Portfolio, Rescue** — to Tailwind-only styling: remove all `.style()` and all Vue
`:style=` slot bindings, map dynamic table-cell colors to fixed palette classes, move the
3D gradient buttons to shared theme tokens, and slim each page's `ui.add_css` block to
Quasar-table-internal rules only.

**Architecture (three conversion patterns):**
1. **Dynamic cell colors** are currently stamped as a hex field on each row and bound via a
   Quasar table slot `:style="background:${props.row._x_color};…"` (an inline style — banned).
   Convert: the page's color function returns a **Tailwind class** (from its finite set,
   exact hex preserved), stamp a `_x_class` field, and bind `:class="props.row._x_class"`
   (the Tailwind JIT generates it — verified in Phase 2). Static parts of the slot
   (border-radius/padding) become static Tailwind utilities in the same `:class`.
2. **3D gradient buttons** (`scan-btn`, `pt-btn`, `pt-danger`) → shared `theme.BTN_3D` /
   `theme.BTN_3D_DANGER` tokens (Tailwind arbitrary gradient + shadow + `hover:`/`active:`
   variants), applied with `color=None` so Quasar's `bg-primary` doesn't compete.
3. **Quasar-table-internal rules** (cell `td/th` padding, sticky `thead tr th`, scrollable
   `.q-table__middle` max-height, `.q-tab__indicator`/`.q-tab--active`) STAY in a slimmed
   `ui.add_css` block — `.classes()` can't reach Quasar-generated cells/tab internals.

**Tech Stack:** NiceGUI 3.13 (Tailwind browser JIT), pytest from `webgui/`. Baseline **565**.

**Reference:** design doc `...-design.md`; `CLAUDE.md` "UI styling standard" (esp. the
dynamic-value rule); inventory in this session. Tokens in `webgui/pages/options/theme.py`.

**Standing rules for every task:**
- TDD: failing test → run (fail) → implement → run (pass) → commit. Use `.classes(remove=…,
  add=…)` for any reactively-reset class (avoid accumulation). DO NOT touch `DASHBOARD_CSS` /
  `QUASAR_INTERNAL_CSS` or the not-yet-converted pages (calculator/simulator/trade/gamma/etc).
- A converted page must be free of `.style(` AND of `:style=` in its slot templates. A
  surviving `ui.add_css` block must contain ONLY Quasar-internal selectors.
- Preserve exact colors (convert + light polish). Run `..\.venv\Scripts\python -m pytest -q`
  after each task.

---

### Task 3a.1: Shared 3D-button tokens in `theme.py`

**Files:** Modify `webgui/pages/options/theme.py`; Test `webgui/tests/test_theme.py`.

`scan-btn` and `pt-btn` are byte-identical 3D gradients; `pt-danger` is the red variant.
Make them shared tokens.

**Step 1 — failing test.** Append to `test_theme.py`:
```python
BTN3D = ["BTN_3D", "BTN_3D_DANGER"]
def test_btn3d_tokens_are_class_strings():
    for n in BTN3D:
        v = getattr(theme, n)
        assert isinstance(v, str) and v.strip() and "{" not in v and ";" not in v
def test_btn3d_encodes_gradient_and_shadow():
    assert "linear-gradient(180deg" in theme.BTN_3D
    assert theme.BTN_3D.count("shadow-[") >= 1 and "active:" in theme.BTN_3D
    assert "#d33f3f" in theme.BTN_3D_DANGER  # red variant mid-stop
```

**Step 2 — run, expect fail.**

**Step 3 — implement.** Append to `theme.py` (Tailwind arbitrary values; `_` = space):
```python
# Shared 3D gradient buttons (the Scanner "Run scan" + Paper action buttons). Apply
# with color=None so Quasar's bg-primary doesn't compete. Replaces SCAN_CSS .scan-btn
# and PAPER_CSS .pt-btn / .pt-danger (byte-identical gradients).
BTN_3D = (
    "bg-[linear-gradient(180deg,#5aa0e6_0%,#3a7bc0_55%,#316eac_100%)] text-white "
    "rounded-[7px] font-semibold min-h-[34px] "
    "shadow-[0_4px_0_0_#244e78,0_6px_10px_rgba(0,0,0,.4)] "
    "hover:brightness-110 active:translate-y-[4px] "
    "active:shadow-[0_1px_0_0_#244e78,0_2px_4px_rgba(0,0,0,.4)] "
    "transition-[transform,box-shadow,filter] duration-100"
)
BTN_3D_DANGER = (
    "bg-[linear-gradient(180deg,#ef6b6b_0%,#d33f3f_55%,#b53030_100%)] text-white "
    "rounded-[7px] font-semibold min-h-[34px] "
    "shadow-[0_4px_0_0_#7a1f1f,0_6px_10px_rgba(0,0,0,.4)] "
    "hover:brightness-110 active:translate-y-[4px] "
    "active:shadow-[0_1px_0_0_#7a1f1f,0_2px_4px_rgba(0,0,0,.4)] "
    "transition-[transform,box-shadow,filter] duration-100"
)
```

**Step 4 — run, expect pass** (theme tests + full suite).

**Step 5 — commit:** `feat(webgui): shared 3D-button tokens (Phase 3a)`

> **Browser-verify these tokens early (in Task 3a.7 gate):** the multi-layer `shadow-[…,…]`
> and `bg-[linear-gradient(…)]` arbitrary classes are the riskiest part of this phase. If the
> JIT does not render a gradient/shadow exactly, STOP and flag — do NOT silently drop it into
> the escape hatch (that block is for unreachable DOM only).

---

### Task 3a.2: `scanner.py` (+ `swing.py` inherits)

**Files:** Modify `webgui/pages/options/scanner.py`; Test `webgui/tests/test_options_scanner.py`.

**The dynamic color:** `score_zone_color(score)` (scanner.py:39-50) returns hex from a finite
5-set (`#666666`/`#ef5350`/`#ffa726`/`#42a5f5`/`#66bb6a`). The score badge slot
`body-cell-composite_score` (≈295-299) binds `:style="background:${props.row._score_color};
border-radius:3px;padding:1px 6px"`.

**Step 1 — failing test.** Add a `score_zone_class(score)` returning bg classes:
```python
def test_score_zone_class_maps_zones():
    from pages.options import scanner as sc
    assert sc.score_zone_class(80) == "bg-[#66bb6a]"
    assert sc.score_zone_class(60) == "bg-[#42a5f5]"
    assert sc.score_zone_class(50) == "bg-[#ffa726]"
    assert sc.score_zone_class(30) == "bg-[#ef5350]"
    assert sc.score_zone_class(None) == "bg-[#666666]"
```

**Step 2 — run, expect fail.**

**Step 3 — implement.**
- Add `score_zone_class(score)` (mirror `score_zone_color`'s thresholds → `bg-[<hex>]`).
  Keep `score_zone_color` if other callers exist (grep); otherwise repoint it.
- In `signal_rows()` stamp `_score_class = score_zone_class(score)` (alongside/instead of
  `_score_color`). Change the slot to:
  `:class="props.row._score_class + ' rounded-[3px] px-1.5 py-px'"` and DROP the `:style`.
  (Quote it as the slot template expects — keep the existing `text-white`/centering if any.)
- **`scan-btn` → token:** the Run-scan button `.classes("scan-btn")` → `.classes(BTN_3D)` with
  `color=None` (import `from .theme import BTN_3D`). Remove the `.scan-btn` rules from SCAN_CSS.
- **Tabs:** `.tab-0dte`/`.tab-swing` text color is reachable — apply `text-[#ffa726]` /
  `text-[#42a5f5]` as a Tailwind class on each `ui.tab` (with the existing tab class) and
  remove the `.tab-0dte/.tab-swing { color }` rules. KEEP in SCAN_CSS (Quasar-internal):
  `.scan-tabs .q-tab` text-transform/weight, `.q-tab__indicator { display:none }`, and the
  `.q-tab--active { box-shadow }` active-underline (internal pseudo-state) — but note its color
  is the literal `TAB_*_COLOR`; keep those two `q-tab--active` rules as-is.
- **Table padding:** `.scan-table td,th { padding }` is Quasar-internal — KEEP.
- The `_new` badge slot (`body-cell-symbol`, ≈301-307) uses a STATIC color `#1565c0` in a
  `:style`. Convert that slot's `:style` to `:class="'bg-[#1565c0] text-white rounded px-1 …'"`
  (static — no dynamic part). Remove inline style.

After: SCAN_CSS should contain only `.scan-table td/th` padding + the `.scan-tabs .q-tab` /
`q-tab__indicator` / `q-tab--active` internal rules.

**swing.py:** reuses `scanner.signal_columns()`/`signal_rows()` + has NO css/.style of its own —
it inherits the change. Confirm it still renders (smoke). Add nothing unless a guard fails.

**Step 4 — run** (scanner test + full suite) → green.
**Step 5 — commit:** `refactor(webgui): scanner/swing Tailwind cell colors + 3D button (Phase 3a)`

---

### Task 3a.3: `captured.py`

**Files:** Modify `webgui/pages/options/captured.py`; Test `webgui/tests/test_options_captured.py`.

Dynamic: `rec_color` (50-52, finite 4-set), `pnl_color` (70-79, green/red/""),
`rescue_highlight` (33-38, → `heat_color` border tint). Slots: `body-cell-recommendation`
(`:style="background:${_rec_color}…"`), `body-cell-unrealized_pnl` (`:style="color:${_pnl_color}…"`),
`body-cell-symbol` (`:style="border-left:4px solid ${_rescue_color}…"`).

**Step 1 — failing tests** for `rec_class(rec)` (→ `bg-[…]`), `pnl_class(v)` (→ `text-[#66bb6a]`/
`text-[#ef5350]`/`""`), and (from rescue.py, see 3a.6) a `heat_border_class`. Assert the maps.

**Step 2 — run, expect fail.**

**Step 3 — implement.** Add `rec_class`/`pnl_class` (mirror the color fns → classes, exact hex).
Stamp `_rec_class`/`_pnl_class`/`_rescue_class` (the last = `heat_border_class(heat)` →
`"border-l-4 border-[<hex>]"` or `""` from rescue.py). Rebind each slot `:style` → `:class`,
folding the static parts (`rounded`/`px`/`text-center`) into the class string. Slim CAPTURED_CSS:
keep `.q-table__middle` max-height + `thead tr th` sticky + `td/th` padding (all Quasar-internal).

**Step 4 — run** → green. **Step 5 — commit:** `refactor(webgui): captured Tailwind cell colors (Phase 3a)`

---

### Task 3a.4: `paper.py`

**Files:** Modify `webgui/pages/options/paper.py`; Test `webgui/tests/test_options_paper.py`.

Dynamic: `pnl_color` (125-129), `verdict_color` (92-95), `rescue_highlight` (57-62). Two direct
`.style()`: verdict popup label (431, `background:{verdict_color};color:#111;width:fit-content`)
+ metric label (443, `color:{color}`). Slots: `body-cell-pnl` (`:style="color:${_pnl_color}"`),
`body-cell-symbol` (rescue border). Buttons: `.pt-btn` / `.pt-btn.pt-danger`.

**Step 1 — failing tests** for `pnl_class`, `verdict_class` (→ `bg-[…]`), reusing/aligning with
captured's `pnl_class` if identical (DRY — consider a shared helper; if you add it to one page
and import, note it).

**Step 2 — run, expect fail.**

**Step 3 — implement.**
- `pnl_class`/`verdict_class` → classes. Stamp `_pnl_class`; rebind `body-cell-pnl` `:style`→`:class`.
- Verdict popup label (431): `.classes(f"{verdict_class(action)} text-[#111] w-fit rounded px-2")` —
  no `.style()`. Metric label (443): `.classes(pnl_class(pnl))` (build-once in the popup → `add=` ok).
- `body-cell-symbol` rescue tint → `:class` with `_rescue_class` (shared `heat_border_class`).
- **Buttons:** `.classes("pt-btn")` → `.classes(BTN_3D)` and `.classes("pt-btn pt-danger")` →
  `.classes(BTN_3D_DANGER)`, all with `color=None` (the danger ones already pass `color=None`).
  Remove ALL `.pt-btn`/`.pt-danger` rules from PAPER_CSS. Slim PAPER_CSS to `.paper-table` td/th
  padding + `.q-table__middle` + sticky `thead tr th` (Quasar-internal).

**Step 4 — run** → green. **Step 5 — commit:** `refactor(webgui): paper Tailwind cell colors + 3D buttons (Phase 3a)`

---

### Task 3a.5: `portfolio.py` (Paper Portfolio)

**Files:** Modify `webgui/pages/options/portfolio.py`; Test `webgui/tests/test_options_paper_portfolio.py`.

Only dynamic: `rescue_highlight` (30-35) via `body-cell-symbol` border tint. No `.style()`, no css.

**Step 1 — failing test** asserting `_rescue_class` stamping uses `heat_border_class` (or assert
the rendered slot has no `:style`). **Step 2 — fail. Step 3:** stamp `_rescue_class`, rebind the
slot `:style`→`:class`. **Step 4 — green. Step 5 — commit:** `refactor(webgui): paper-portfolio Tailwind rescue tint (Phase 3a)`

---

### Task 3a.6: `rescue.py` (shared `heat_color` lives here)

**Files:** Modify `webgui/pages/options/rescue.py`; Test `webgui/tests/test_rescue.py`.

Do this EITHER FIRST (3a.3-3a.5 import `heat_border_class` from here) — reorder so 3a.6 lands
before the consumers, OR add the helper in 3a.3 and have rescue import it. **Recommended: do the
`heat_border_class`/`heat_bg_class` helpers as the FIRST step (before 3a.3).**

Dynamic: `heat_color` (32-46, finite 4-set incl. `#ff7043` orange), `cash_text` (132-142, returns
`{"text","color"}`). Direct `.style()`: two static flex (322 `flex: 3 1 0`, 349 `flex: 2 1 0`) +
cash label (464, `color:{cell['color']}`). Slot: `body-cell-heat` (`:style="background:${_heat_color}"`).

**Step 1 — failing tests:**
```python
def test_heat_bg_and_border_classes():
    from pages.options import rescue as r
    assert r.heat_bg_class(80) == "bg-[#ef5350]"
    assert r.heat_bg_class(60) == "bg-[#ff7043]"
    assert r.heat_bg_class(40) == "bg-[#ffa726]"
    assert r.heat_bg_class(10) == "bg-[#66bb6a]"
    assert r.heat_border_class(80) == "border-l-4 border-[#ef5350]"
    assert r.heat_border_class(None) == ""   # define a sensible empty/neutral for missing
def test_cash_class_maps_sign():
    from pages.options import rescue as r
    assert r.cash_class(5) == "text-[#66bb6a]"
    assert r.cash_class(-5) == "text-[#ef5350]"
    assert r.cash_class(0) == "text-[#9e9e9e]"
```
(Confirm `heat_border_class`'s empty/neutral contract matches how `rescue_highlight` decides
to show NO border for ok/watch — only tested/critical get a border. The class fn returns the
border classes; `rescue_highlight` still gates on state, returning `""` when not at-risk.)

**Step 2 — fail. Step 3 — implement:**
- `heat_bg_class`/`heat_border_class` (mirror `heat_color` buckets), `cash_class` (sign → text class).
- `cash_text` keep returning text; ADD `cash_class(value)` OR have `cash_text` return a `class`
  key instead of `color`. Update the cash label (464): `.classes(cash_class(value))`, no `.style()`.
- Flex columns (322/349): `.style("flex: 3 1 0")` → `.classes("grow-[3] shrink basis-0")` (or
  `flex-[3_1_0]` arbitrary); `flex: 2 1 0` → `grow-[2] shrink basis-0`. Verify the 3:2 split holds
  in the browser.
- `body-cell-heat` slot `:style`→`:class` with stamped `_heat_class = heat_bg_class(heat)`.
- `_RESCUE_CSS` is already Quasar-internal-only (`.q-table__middle` + sticky `thead tr th`) — KEEP as-is.

**Step 4 — run** → green. **Step 5 — commit:** `refactor(webgui): rescue Tailwind heat/cash colors + flex (Phase 3a)`

---

### Task 3a.7: Guard + browser gate + CLAUDE.md

**Step 1 — guard test.** Extend the `.style()`-free guard (or add a `:style=`-free guard) to cover
all six Phase 3a files: assert each page source has no `.style(` AND no `:style=` substring (the
slot templates). Add to `test_no_inline_style.py`.

**Step 2 — browser gate.** Restart the preview (stop+start to reload). Verify on each screen:
- **Scanner:** Run-scan button renders the 3D blue gradient + shadow + press-down (CLICK it to see
  `active:`); tab accent colors + active underline; a signal's composite-score cell shows its zone
  bg color; `_new` badge. (Off-hours: 1 swing signal exists — check its score cell color.)
- **Paper:** the blue + RED 3D buttons; P&L cell colors; Analyze popup verdict chip + metric color.
- **Captured / Paper Portfolio:** rescue border tint (if any at-risk), rec/pnl cell colors.
- **Rescue:** heat cell colors, cash text colors, the 3:2 column split.
Screenshot each; confirm **no console errors**. If a 3D gradient/shadow class doesn't render,
STOP and flag (do not escape-hatch it silently).

**Step 3 — CLAUDE.md** status snapshot + Last-updated note: Phase 3a done (signal-table screens
Tailwind; dynamic cell colors via `:class` bindings + palette maps; 3D buttons → `BTN_3D`/
`BTN_3D_DANGER`; per-page CSS slimmed to Quasar-table-internal). Next Phase 3b. Commit.

---

## Per-task done checklist
suite green · page free of `.style(` AND `:style=` · dynamic cell colors via stamped Tailwind
`:class` from a finite map · 3D buttons via tokens w/ `color=None` · surviving `ui.add_css` is
Quasar-internal only · screenshot parity + no console errors · CLAUDE.md updated.

## Notes / risks
- **`:style=` → `:class=` in slots is the core change.** Read each slot template carefully; keep
  every NON-style attribute and the static style bits (as Tailwind utilities). The stamped row
  field changes from a hex to a class string.
- **DRY:** `pnl_class` appears in captured + paper; `heat_*`/`rescue_highlight` are shared from
  rescue.py. Reuse, don't duplicate — import across pages as the code already imports `heat_color`.
- **3D button arbitrary classes (gradient + multi-layer shadow) are the top visual risk** — the
  gate must click the buttons and confirm gradient/shadow/press render. JIT supports them (Phase 2
  verified arbitrary values), but multi-layer shadow commas are unusual — verify.
- Do NOT alter the not-yet-converted pages or the shared escape-hatch CSS.
