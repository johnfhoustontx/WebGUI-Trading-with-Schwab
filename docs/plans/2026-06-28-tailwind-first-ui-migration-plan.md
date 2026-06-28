# Tailwind-first UI migration — Implementation Plan (Phase 0 + Phase 1)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stand up the Tailwind design-token vocabulary (Phase 0) and convert the
top-level menu / nav shell to Tailwind-only styling (Phase 1) — the foundation every
later screen-conversion phase builds on.

**Architecture:** The dark-navy theme becomes **Python Tailwind-class-string constants**
in `webgui/pages/options/theme.py` (`PAGE`/`CARD`/`BTN`/…), applied via `.classes(CARD)`.
A slimmed `ui.add_css` block holds **only** Quasar-internal/teleported rules
(`q-field__control`, `q-tab*`, the body-mounted `.strat-menu-navy` popup) that component
classes can't reach. Phase 0 is **purely additive** — the legacy `DASHBOARD_CSS` and its
`.calc-card`/`.cv2-btn*` rules stay intact so existing pages don't regress; they are
deleted only by a final cleanup after the last consumer (Phase 4) flips. The nav shell
(Phase 1) lives entirely in `main.py`, so it converts in one pass: reachable rules move
to `.classes()`, only Quasar-internal rules remain in a slimmed `_NAV_CSS`.

**Tech Stack:** NiceGUI (bundled Tailwind + Quasar), pytest (run from `webgui/`).

**Reference:** design doc `docs/plans/2026-06-28-tailwind-first-ui-migration-design.md`;
standard in `CLAUDE.md` → "UI styling standard — Tailwind-first"; canonical palette in
`CLAUDE.md` → "App theme — dark-navy 'dashboard'".

**Testing reality (read first).** This is a CSS/Tailwind migration; you cannot unit-test
"looks navy." Tests here are **guards**, not behavior specs: (a) token constants exist /
are class strings (no `{`/`;`/`.style`), (b) the slimmed CSS blocks contain only
Quasar-internal selectors, (c) converted files are free of `.style(`. Visual correctness
is verified by the **render-smoke tests** (`test_shell.py` builds every route) plus a
**browser screenshot** (preview tool) compared against the pre-conversion look — convert
+ light polish means same-or-cleaner, never broken. Run the screenshot step where noted;
do not skip it.

**Commands** (always from `webgui/`):
- Single test: `..\.venv\Scripts\python -m pytest tests/test_theme.py -v`
- Full webgui suite: `..\.venv\Scripts\python -m pytest -q`

---

## Phase 0 — Foundation: token vocabulary

### Task 0.1: Add design-token constants to `theme.py`

**Files:**
- Modify: `webgui/pages/options/theme.py` (append constants; do NOT touch `DASHBOARD_CSS`)
- Test: `webgui/tests/test_theme.py` (create)

**Step 1: Write the failing test**

```python
# webgui/tests/test_theme.py
"""Guard tests for the Tailwind design-token vocabulary (Phase 0)."""
from pages.options import theme

TOKENS = ["PAGE", "CARD", "EYEBROW", "LABEL", "MUTED", "BTN", "BTN_PRIMARY", "STRATEGY_BTN"]


def test_tokens_exist_and_are_nonempty_strings():
    for name in TOKENS:
        val = getattr(theme, name)
        assert isinstance(val, str) and val.strip(), f"{name} missing/empty"


def test_tokens_are_class_strings_not_css():
    # A token is a Tailwind utility string applied via .classes() — it must not
    # contain CSS rule syntax (the whole point of the migration).
    for name in TOKENS:
        val = getattr(theme, name)
        assert "{" not in val and ";" not in val and ":hover{" not in val, \
            f"{name} looks like CSS, not a class string"


def test_card_token_encodes_navy_palette():
    # Convert + light polish: tokens encode the canonical hex palette.
    assert "#101a30" in theme.CARD and "#213152" in theme.CARD


def test_legacy_dashboard_css_still_present():
    # Phase 0 is additive — existing consumers (Calculator/Simulator/Trade) still
    # reference .calc-card/.cv2-btn until their phases. Do NOT remove yet.
    assert ".calc-card" in theme.DASHBOARD_CSS
    assert ".cv2-btn-primary" in theme.DASHBOARD_CSS
```

**Step 2: Run test to verify it fails**

Run: `..\.venv\Scripts\python -m pytest tests/test_theme.py -v`
Expected: FAIL — `AttributeError: module 'pages.options.theme' has no attribute 'PAGE'`.

**Step 3: Write minimal implementation**

Append to `webgui/pages/options/theme.py` (palette per the `CLAUDE.md` App-theme section).
Note `hover:`/focus variants and arbitrary values are valid Tailwind class strings:

```python
# ---------------------------------------------------------------------------
# Tailwind design tokens (Phase 0 of the Tailwind-first migration). Each is a
# reusable .classes() utility string encoding the dark-navy palette. Apply with
# `.classes(CARD)` instead of `.classes("calc-card")` + a CSS rule. The legacy
# DASHBOARD_CSS above is retained until its last consumer is converted (Phase 4
# cleanup). Palette source: CLAUDE.md "App theme — dark-navy 'dashboard'".
# ---------------------------------------------------------------------------
PAGE = (
    "rounded-[14px] border border-[#1d2942] p-[18px_20px_22px] text-[#cdd8ee] "
    "bg-[radial-gradient(130%_90%_at_50%_-20%,#16243f_0%,#0c1424_55%,#0a0f1c_100%)]"
)
CARD = "bg-[#101a30] border border-[#213152] rounded-[12px] px-4 py-3.5"
EYEBROW = "text-[#8794b4] text-[12px] tracking-[.02em]"
LABEL = "text-[#eaf0fb]"
MUTED = "text-[#7f8db0]"
BTN = (
    "bg-[#15213b] hover:bg-[#1b2950] text-[#cdd8ee] border border-[#2a3a5c] "
    "rounded-[9px] min-h-[40px] font-medium"
)
BTN_PRIMARY = (
    "bg-[#2563eb] hover:bg-[#1d4fd1] text-white rounded-[9px] min-h-[40px] font-semibold"
)
STRATEGY_BTN = (
    "bg-[#0c1426] hover:border-[#3b82f6] border border-[#243353] text-[#e7edf8] "
    "rounded-[8px] min-h-[40px] font-normal"
)
```

**Step 4: Run test to verify it passes**

Run: `..\.venv\Scripts\python -m pytest tests/test_theme.py -v`
Expected: PASS (5 tests).

**Step 5: Commit**

```bash
git add webgui/pages/options/theme.py webgui/tests/test_theme.py
git commit -m "feat(webgui): add Tailwind design-token vocabulary (Phase 0)"
```

---

### Task 0.2: Extract `QUASAR_INTERNAL_CSS` (the escape-hatch block)

**Files:**
- Modify: `webgui/pages/options/theme.py` (add a new constant; keep `DASHBOARD_CSS`)
- Test: `webgui/tests/test_theme.py`

**Step 1: Add the failing test**

Append to `webgui/tests/test_theme.py`:

```python
def test_quasar_internal_css_is_internal_only():
    css = theme.QUASAR_INTERNAL_CSS
    # MUST contain the Quasar-internal rules component classes can't reach.
    assert ".q-field__control" in css
    assert ".strat-menu-navy" in css
    # MUST NOT contain the now-tokenized semantic rules.
    assert ".calc-card{" not in css.replace(" ", "")
    assert ".cv2-btn" not in css
    assert ".calc-eyebrow" not in css
```

**Step 2: Run to verify fail**

Run: `..\.venv\Scripts\python -m pytest tests/test_theme.py::test_quasar_internal_css_is_internal_only -v`
Expected: FAIL — `AttributeError: ... 'QUASAR_INTERNAL_CSS'`.

**Step 3: Implement**

Add a `QUASAR_INTERNAL_CSS` constant to `theme.py` containing ONLY the Quasar-internal
subset copied from `DASHBOARD_CSS` (the `.q-field__control` boxed-input rules incl. the
`.leg-row`/`.leg-strike`/`.leg-head` variants, `.q-tab*`, and the teleported
`.strat-menu-navy` popup). Omit `.calc-v2` page bg, `.calc-card`, `.calc-eyebrow`,
`.cv2-btn*`, `.strategy-menu-btn` (those become tokens). Keep the field/tab rules scoped
under `.calc-v2` exactly as today so behavior is unchanged for pages still using it.
**Do not delete or edit `DASHBOARD_CSS`.**

**Step 4: Run to verify pass**

Run: `..\.venv\Scripts\python -m pytest tests/test_theme.py -v`
Expected: PASS (6 tests).

**Step 5: Commit**

```bash
git add webgui/pages/options/theme.py webgui/tests/test_theme.py
git commit -m "feat(webgui): extract QUASAR_INTERNAL_CSS escape-hatch block (Phase 0)"
```

> **Phase 0 done.** Run the full suite — `..\.venv\Scripts\python -m pytest -q` — and
> confirm green (nothing consumes the new constants yet, so this only proves no
> accidental edit to `DASHBOARD_CSS`).

---

## Phase 1 — Top-level menu / nav shell (`main.py`)

The nav lives only in `main.py`, so reachable rules move fully to `.classes()` and
`_NAV_CSS` slims to Quasar-internal-only. Reference: `main.py:248-279` (`_NAV_CSS`),
`main.py:352-381` (`_nav_link`/`_settings_group`), `main.py:383-419+` (`_layout`).

**Reachable → Tailwind** (move to `.classes()`): `.nav-link` (radius/padding/transition/
hover/active), `.nav-icon` (size/opacity), `.nav-badge` (`ml-auto`), `.nav-title`,
`.help-fab` (positioning).
**Quasar-internal → stays in slimmed `_NAV_CSS`:** `.nav-drawer .nicegui-expansion-content`
gap, `.nav-drawer .q-item`/`.q-expansion-item .q-item` radius+min-height,
`.nav-subgroup .q-expansion-item__content` padding, `.q-tooltip.help-tip`,
`.help-btn .q-btn__content` padding.

### Task 1.1: Convert `_nav_link` to Tailwind classes

**Files:**
- Modify: `webgui/main.py` (`_nav_link`, ~`352-361`)
- Test: `webgui/tests/test_shell.py` (render smoke already covers routes; add a guard)

**Step 1: Add the failing guard test**

Append to `webgui/tests/test_shell.py` (or create `tests/test_nav_style.py` if cleaner):

```python
def test_nav_css_has_no_reachable_rules():
    """Phase 1: nav-link/nav-title/nav-icon/nav-badge styling moved to .classes();
    _NAV_CSS keeps only Quasar-internal selectors."""
    import main
    css = main._NAV_CSS
    assert "a.nav-link:hover" not in css           # moved to hover:bg-* utility
    assert ".nav-title {" not in css and ".nav-title{" not in css
    assert ".nicegui-expansion-content" in css     # Quasar-internal stays
    assert ".q-tooltip.help-tip" in css            # teleported tooltip stays
```

**Step 2: Run to verify fail**

Run: `..\.venv\Scripts\python -m pytest tests/test_shell.py::test_nav_css_has_no_reachable_rules -v`
Expected: FAIL (the rules are still in `_NAV_CSS`).

**Step 3: Implement — rewrite `_nav_link`**

Replace the `classes` string in `_nav_link` so the visual rules are Tailwind utilities
(keep the `nav-link`/`active` marker classes only if other code keys off them — the active
test below uses the link's own classes, so encode active state directly):

```python
def _nav_link(path: str, label: str, icon: str, active: str) -> None:
    base = ("w-full no-underline items-center rounded-[10px] px-3 py-1 "
            "transition-colors hover:bg-white/[0.06]")
    state = " bg-[var(--q-primary)] text-white" if path == active else ""
    with ui.link(target=path).classes(base + state):
        with ui.row().classes("items-center gap-3 w-full no-wrap"):
            ui.icon(icon).classes("text-xl opacity-90")
            ui.label(label)
            n = _NAV_BADGES.get(path, 0)
            badge = ui.badge(str(n) if n else "").classes("ml-auto").props("color=red rounded")
            badge.set_visibility(bool(n))
            _badge_refs[path] = badge
```

Then delete the now-dead reachable rules from `_NAV_CSS` (`.nav-link` radius/padding/
transition/hover/active, `.nav-icon`, `.nav-badge`). Keep all `.q-*`/`.nicegui-expansion-content`/
`.q-tooltip.help-tip`/`.help-btn .q-btn__content` rules. (The `.nav-drawer .q-item ...
border-radius:10px` rule stays — it styles the Quasar expansion-header item, not the link.)

**Step 4: Run to verify pass**

Run: `..\.venv\Scripts\python -m pytest tests/test_shell.py -v`
Expected: PASS (guard + existing route-render smoke tests all green).

**Step 5: Commit**

```bash
git add webgui/main.py webgui/tests/test_shell.py
git commit -m "refactor(webgui): nav links Tailwind-only (Phase 1)"
```

### Task 1.2: Convert `nav-title` + help-fab positioning

**Files:**
- Modify: `webgui/main.py` (the `ui.label("SCHWAB TRADING").classes("nav-title")` at
  `~397`; the help-fab element wherever `help-fab` is applied) + `_NAV_CSS`.

**Step 1:** (covered by the Task 1.1 guard asserting `.nav-title {` absent — extend it to
also assert `.help-fab {` absent once the fab positioning is moved.)

**Step 2: Run** → FAIL until implemented.

**Step 3: Implement**
- `nav-title` → `ui.label("SCHWAB TRADING").classes("font-bold tracking-[.04em] text-[.8rem] px-3 pt-1 pb-1.5 opacity-55")`.
- help-fab container → `.classes("absolute right-[6px] bottom-[2px] z-[2300]")` (find the
  element; keep the `help-btn`/`q-btn__content` padding rule in `_NAV_CSS` — Quasar-internal).
- Delete `.nav-title` and `.help-fab` position rules from `_NAV_CSS`; keep `.help-fab .help-btn`
  and `.q-btn__content` padding (internal).

**Step 4: Run** `..\.venv\Scripts\python -m pytest -q` → PASS (full suite).

**Step 5: Commit**
```bash
git add webgui/main.py webgui/tests/test_shell.py
git commit -m "refactor(webgui): nav title + help-fab Tailwind-only (Phase 1)"
```

### Task 1.3: Browser verification (no code — required gate)

**Step 1:** Start the preview dev server (`webgui`, :8500) and screenshot the page with the
drawer open.
**Step 2:** Compare against the pre-Phase-1 look: drawer spacing tight, group expand/collapse
works, active item shows the primary-color pill, hover highlights, badges right-aligned, help
"?" in the header corner, tooltip styled. Convert + light polish = same or cleaner.
**Step 3:** If anything regressed, fix the offending `.classes()` string and re-screenshot.
Do not proceed until it matches.
**Step 4: Commit** any fixes:
```bash
git add webgui/main.py
git commit -m "fix(webgui): nav visual parity after Tailwind conversion (Phase 1)"
```

### Task 1.4: Update CLAUDE.md status line

**Files:** Modify `CLAUDE.md` — the "UI styling standard" status snapshot line.

**Step 1:** Change "**Phase 0 (token module) not yet started**" → "**Phase 0 + 1 done**
(token vocabulary in `theme.py`; nav shell converted; `_NAV_CSS` now Quasar-internal only)."
Also update the top "Last updated" Phase-status note.

**Step 2: Commit**
```bash
git add CLAUDE.md
git commit -m "docs: Tailwind migration Phase 0+1 done"
```

---

## After Phase 0 + 1

Phases 2–8 (shared Options helpers → screens by logical group, per the design doc) follow
this same template: introduce no new tokens unless needed; convert one logical group at a
time; guard test that the file is free of `.style(` and that any surviving CSS is
Quasar-internal; render-smoke + screenshot; commit per group. Generate each phase's plan
from the design doc when you reach it. The **final cleanup** (after Phase 4) deletes the
now-dead `DASHBOARD_CSS` semantic rules once no page references `.calc-card`/`.cv2-btn*`,
leaving `theme.py` with tokens + `QUASAR_INTERNAL_CSS` only.

**Per-phase done checklist:** `..\.venv\Scripts\python -m pytest -q` green · converted files
`grep`-clean of `.style(` · screenshot parity · CLAUDE.md status line updated.
