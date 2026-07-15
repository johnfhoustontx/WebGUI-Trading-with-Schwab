# Nav Icon Rail Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the webgui's always-open nav drawer into a 64px icon rail that expands on hover, with curated per-item icons and the hamburger repurposed as pin/unpin.

**Architecture:** The drawer renders at Quasar `width=64`, so the page's left offset is permanently 64px. One `_NAV_CSS` rule (`.nav-drawer:hover { width: 248px !important }`) widens it on hover — author `!important` beats Quasar's inline `style="width:64px"`, and because Quasar's *layout* still believes the drawer is 64px, the page never reflows and the expanded menu floats over the content. No Quasar mini-mode, no JS, no hover round-trips. Icons replace the dead `nav-dot`; badges become Quasar `floating` badges on the icon corner. Pin state persists in `app_settings`.

**Tech Stack:** NiceGUI 2.x (Quasar QDrawer/QBadge/QIcon), Tailwind utility classes, pytest.

**Design doc:** `docs/plans/2026-07-15-nav-icon-rail-design.md` (committed, `58020ba`).

---

## Context you need before starting

Everything below lives in **`webgui/main.py`** unless stated. Run tests with:

```bash
cd "D:/WebGUI Trading with Schwab/webgui" && ../.venv/Scripts/python -m pytest -q
```

Baseline is **723 passing**. Never let that number drop.

**Three landmines specific to this codebase — read these or you will lose an afternoon:**

1. **`config/theme.toml` `[menu].text` is set (`#98a1c0`)**, so `theme.build_nav_css()` emits
   `.nav-drawer .q-icon{color:#98a1c0!important;}` and `_layout` injects it **after**
   `_NAV_CSS`. That rule hits every `ui.icon` in the drawer, so a Tailwind
   `text-[#6b86ff]` class on the active icon **will silently lose**. The active-icon rule must
   therefore be `!important` **and** out-specify it: `.nav-drawer .nav-active .nav-icon`
   (3 classes) beats `.nav-drawer .q-icon` (2 classes), so ours wins despite being injected
   earlier. This is why the active-icon color is CSS and not a Tailwind class.

2. **`test_nav_css_has_no_reachable_rules`** (`tests/test_shell.py:56`) guards `_NAV_CSS`
   against re-growing link/title styling. Our additions (`.nav-drawer:hover`, `.nav-icon`,
   `.nav-label` visibility) are not on its banned list, and state-dependent `.nav-label` rules
   already live there (line 498) — so this precedent is fine. Do not touch that test.

3. **`ui.left_drawer` has no `width` kwarg** (verified against the installed NiceGUI —
   its `__init__` takes only `value/fixed/bordered/elevated/top_corner/bottom_corner`).
   Width must go through Quasar's own prop: `.props("width=64")`.

**The Tailwind-first standard applies** (see CLAUDE.md): prefer `.classes()`, never `.style()`.
`_NAV_CSS` is the documented Quasar-internal escape hatch — legitimate here because we are
overriding a Quasar **inline** width, which no Tailwind class can do.

---

### Task 1: Curate the icons + a distinctness guard

The seven drawer icons are the only affordance in a collapsed rail, so they must be distinct.
Two are being changed (see the design doc's rationale table): Sentiment `insights` → `speed`,
Trade Analyzer `analytics` → `query_stats`.

**Files:**
- Modify: `webgui/main.py:229` (`SENTIMENT_CHILDREN`), `webgui/main.py:239` (`FLAT_NAV`), `webgui/main.py:265` (`_NAV_GROUPS`)
- Test: `webgui/tests/test_shell.py`

**Step 1: Write the failing test**

Append to `webgui/tests/test_shell.py`:

```python
def test_drawer_icons_are_present_and_distinct():
    """The collapsed rail shows ONLY icons, so every drawer item needs a
    non-empty icon and no two may collide (an ambiguous rail is unusable).
    The drawer is the 3 groups + the flat pages — child pages live in the tab
    strip and are not covered here."""
    import main
    icons = [g[1] for g in main._NAV_GROUPS] + [i for _p, _l, i in main.FLAT_NAV]
    assert len(icons) == 7
    assert all(icons), "every drawer item needs an icon"
    assert len(set(icons)) == len(icons), f"duplicate drawer icons: {icons}"
    # The two curated changes (design doc 2026-07-15).
    by_label = {g[0]: g[1] for g in main._NAV_GROUPS}
    assert by_label["Market Trend & Sentiment"] == "speed"
    assert dict((l, i) for _p, l, i in main.FLAT_NAV)["Trade Analyzer"] == "query_stats"
```

**Step 2: Run it and watch it fail**

```bash
cd "D:/WebGUI Trading with Schwab/webgui" && ../.venv/Scripts/python -m pytest tests/test_shell.py::test_drawer_icons_are_present_and_distinct -q
```
Expected: FAIL — `assert 'insights' == 'speed'`.

**Step 3: Make the change**

`main.py:239` — in `FLAT_NAV`, Trade Analyzer:
```python
    ("/trade", "Trade Analyzer", "query_stats"),
```

`main.py:265` — in `_NAV_GROUPS`, the Sentiment group:
```python
    ("Market Trend & Sentiment", "speed", SENTIMENT_CHILDREN),
```

Leave `SENTIMENT_CHILDREN[0]`'s own `insights` icon alone — that is the *child page's* icon
for the tab strip, a different list from the group icon.

**Step 4: Verify**

Run the same command. Expected: PASS. Then the full suite:
```bash
../.venv/Scripts/python -m pytest -q
```
Expected: 724 passed.

**Step 5: Commit**

```bash
git add webgui/main.py webgui/tests/test_shell.py
git commit -m "feat(nav): curate drawer icons + guard their distinctness"
```

---

### Task 2: `drawer_width(pinned)` pure helper

**Files:**
- Modify: `webgui/main.py` (add next to `breadcrumb_parts`, ~line 288)
- Test: `webgui/tests/test_shell.py`

**Step 1: Write the failing test**

```python
def test_drawer_width_pinned_vs_rail():
    """Pinned = the full menu (Quasar offsets content to match). Unpinned = the
    64px icon rail; the CSS :hover rule widens it WITHOUT changing this number,
    which is exactly why hovering overlays instead of reflowing the page."""
    import main
    assert main.drawer_width(True) == main.NAV_WIDTH_OPEN == 248
    assert main.drawer_width(False) == main.NAV_WIDTH_RAIL == 64
```

**Step 2: Run it and watch it fail**

```bash
../.venv/Scripts/python -m pytest tests/test_shell.py::test_drawer_width_pinned_vs_rail -q
```
Expected: FAIL — `AttributeError: module 'main' has no attribute 'drawer_width'`.

**Step 3: Implement**

Add after `breadcrumb_parts` (~line 288):

```python
# ── Nav rail geometry (2026-07-15) ──────────────────────────────────────────
# The drawer is an ICON RAIL that expands on hover. NAV_WIDTH_RAIL is the width
# Quasar lays out with (so the page's left offset is always the rail width);
# NAV_WIDTH_OPEN is what the _NAV_CSS :hover rule widens the drawer to. Keep the
# two in lockstep with the .nav-drawer:hover rule in _NAV_CSS.
NAV_WIDTH_RAIL = 64
NAV_WIDTH_OPEN = 248


def drawer_width(pinned: bool) -> int:
    """Quasar ``width`` prop for the nav drawer: the full menu when pinned, else
    the icon rail (hover widens it via CSS only — the layout offset stays here)."""
    return NAV_WIDTH_OPEN if pinned else NAV_WIDTH_RAIL
```

**Step 4: Verify** — same command, expected PASS.

**Step 5: Commit**

```bash
git add webgui/main.py webgui/tests/test_shell.py
git commit -m "feat(nav): drawer_width helper for the rail/pinned geometry"
```

---

### Task 3: `nav_pinned` setting

**Files:**
- Modify: `webgui/app_settings.py:18`
- Test: `webgui/tests/test_app_settings.py` (create the test if the file lacks a DEFAULTS test)

**Step 1: Write the failing test**

Append to `webgui/tests/test_app_settings.py`:

```python
def test_nav_pinned_defaults_to_the_hover_rail():
    """Fresh installs get the icon rail; pinning is an opt-in preference."""
    import app_settings
    assert app_settings.DEFAULTS["nav_pinned"] is False
```

**Step 2: Run it and watch it fail**

```bash
../.venv/Scripts/python -m pytest tests/test_app_settings.py::test_nav_pinned_defaults_to_the_hover_rail -q
```
Expected: FAIL — `KeyError: 'nav_pinned'`.

**Step 3: Implement**

`app_settings.py`, inside `DEFAULTS` (after `ticker_speed`):

```python
    "nav_pinned": False,             # nav drawer locked open (else a hover icon rail)
```

Note `_load_from_disk` merges `{**DEFAULTS, **raw}`, so an existing `settings.json` on disk
picks the new key up automatically — no migration needed.

**Step 4: Verify** — same command, expected PASS.

**Step 5: Commit**

```bash
git add webgui/app_settings.py webgui/tests/test_app_settings.py
git commit -m "feat(nav): nav_pinned setting (default off = hover rail)"
```

---

### Task 4: Render the icon + corner badge

Replaces the dead `nav-dot`. The badge moves onto the icon so a *collapsed* rail still
reports "3 new signals". Quasar's `floating` badge prop does the corner positioning for us
against a `relative` wrapper — no custom CSS.

The 2s watcher (`main.py:905-912`) only does `badge.text = ...` and `badge.set_visibility(...)`
on the refs, so re-parenting the badge is transparent to it. **Do not touch the watcher.**

**Files:**
- Modify: `webgui/main.py:713-756` (`_nav_link`, `_nav_group_link`)
- Test: `webgui/tests/test_shell.py`

**Step 1: Write the failing test**

```python
def test_nav_link_renders_the_icon_and_registers_its_badge(monkeypatch):
    """The icon (not the retired dot) is the rail's affordance, and the badge ref
    the 2s watcher writes to must still be registered per route."""
    import inspect
    import main
    src = inspect.getsource(main._nav_link) + inspect.getsource(main._nav_group_link)
    assert "nav-dot" not in src, "the dot is retired — the icon carries active state"
    assert "_nav_icon(" in src, "both link builders go through the shared icon+badge helper"
    # The shared helper renders a Quasar floating badge on a relative wrapper.
    helper = inspect.getsource(main._nav_icon)
    assert "floating" in helper
    assert "relative" in helper
```

**Step 2: Run it and watch it fail**

```bash
../.venv/Scripts/python -m pytest tests/test_shell.py::test_nav_link_renders_the_icon_and_registers_its_badge -q
```
Expected: FAIL — `AttributeError: module 'main' has no attribute '_nav_icon'`.

**Step 3: Implement**

Add `_nav_icon` above `_nav_link` (~line 713):

```python
def _nav_icon(icon: str, is_active: bool, count: int):
    """The rail's icon plus its corner count badge.

    The icon is the ONLY thing visible when the rail is collapsed, so it carries
    the active state (via .nav-icon-active — see _NAV_CSS, which must out-specify
    the [menu].text override) and the badge rides its top-right corner in BOTH
    states. Returns the badge so the caller can register it for the 2s watcher."""
    with ui.element("div").classes(
            "relative flex items-center justify-center flex-none w-[22px] h-[22px]"):
        ui.icon(icon).classes(
            "nav-icon text-[21px]" + (" nav-icon-active" if is_active else ""))
        badge = ui.badge(str(count) if count else "").props(
            "color=red rounded floating")
        badge.set_visibility(bool(count))
    return badge
```

Rewrite `_nav_link`'s body (replacing the `nav-dot` element + trailing badge):

```python
def _nav_link(path: str, label: str, icon: str, active: str) -> None:
    base = ("w-full no-underline items-center rounded-[10px] px-3 py-1 "
            "transition-colors hover:bg-white/[0.06]")
    # nav-active is a plain CSS rule in _NAV_CSS — NOT a Tailwind arbitrary class:
    # the bundled Tailwind JIT does not reliably generate arbitrary values
    # containing var(...), so bg-[var(--q-primary)] silently produced no rule.
    is_active = path == active
    state = " nav-active" if is_active else ""
    with ui.link(target=path).classes(base + state):
        _help_tooltip(path)   # rest the mouse 2 s for this page's guide
        with ui.row().classes("items-center gap-3 w-full no-wrap"):
            _badge_refs[path] = _nav_icon(icon, is_active, _NAV_BADGES.get(path, 0))
            ui.label(label).classes("nav-label")
```

And `_nav_group_link`'s body:

```python
    paths = [p for p, _l, _i in children]
    base = ("w-full no-underline items-center rounded-[10px] px-3 py-1 "
            "transition-colors hover:bg-white/[0.06]")
    is_active = active in paths
    state = " nav-active" if is_active else ""
    with ui.link(target=paths[0]).classes(base + state):
        _help_tooltip(paths[0])   # the group's landing page's guide (2 s rest)
        with ui.row().classes("items-center gap-3 w-full no-wrap"):
            n = sum(_NAV_BADGES.get(p, 0) for p in paths)
            _group_badge_refs[label] = (_nav_icon(icon, is_active, n), paths)
            ui.label(label).classes("nav-label")
```

Update `_nav_group_link`'s docstring line about the dot if it mentions one. Delete the now-dead
`.nav-dot` / `.nav-dot-active` / `.nav-dot-idle` rules from `_NAV_CSS` (`main.py:558-560`).

**Step 4: Verify**

```bash
../.venv/Scripts/python -m pytest tests/test_shell.py -q && ../.venv/Scripts/python -m pytest -q
```
Expected: all pass, 726 total.

**Step 5: Commit**

```bash
git add webgui/main.py webgui/tests/test_shell.py
git commit -m "feat(nav): render per-item icons with a corner badge, retire the dot"
```

---

### Task 5: The rail CSS

**Files:**
- Modify: `webgui/main.py:491` (`_NAV_CSS`)
- Test: `webgui/tests/test_shell.py`

**Step 1: Write the failing test**

```python
def test_nav_rail_css_overrides_the_quasar_inline_width():
    """The rail hinges on beating Quasar's inline style='width:64px' — only an
    author !important rule can. And .nav-drawer .nav-active .nav-icon must
    out-specify theme.build_nav_css's '.nav-drawer .q-icon{...!important}'
    ([menu].text), which _layout injects AFTER _NAV_CSS."""
    import main
    css = main._NAV_CSS
    assert ".nav-drawer:hover" in css
    assert f"width: {main.NAV_WIDTH_OPEN}px !important" in css
    assert ".nav-drawer .nav-active .nav-icon" in css   # 3 classes > theme's 2
    assert ".nav-dot" not in css                        # retired in Task 4
```

**Step 2: Run it and watch it fail**

```bash
../.venv/Scripts/python -m pytest tests/test_shell.py::test_nav_rail_css_overrides_the_quasar_inline_width -q
```
Expected: FAIL — `.nav-drawer:hover` not in css.

**Step 3: Implement**

Add to `_NAV_CSS`, right after the existing `.nav-drawer .q-item { border-radius: 10px; }`
line (~499):

```css
/* ── Icon rail (2026-07-15) ────────────────────────────────────────────────
   The drawer lays out at NAV_WIDTH_RAIL (drawer_width) and CSS widens it on
   hover. `!important` is REQUIRED: Quasar writes the width as an INLINE style
   on <aside class="q-drawer">, and only an author !important rule beats an
   inline declaration. Quasar's LAYOUT still uses the rail width, so
   .q-page-container's padding never changes — the expanded menu overlays the
   content instead of reflowing it (this app's Highcharts have no
   ResizeObserver, so a reflow on every hover would leave charts mis-sized).
   .nav-pinned opts out: the drawer is already laid out at the open width. */
.nav-drawer { overflow-x: hidden; transition: width .18s ease; }
.nav-drawer:not(.nav-pinned):hover { width: 248px !important; }
.nav-drawer:not(.nav-pinned) { box-shadow: none; }
.nav-drawer:not(.nav-pinned):hover { box-shadow: 0 12px 40px rgba(0,0,0,.5); }
/* Labels clip (not wrap) in the rail and fade in as it opens. */
.nav-drawer .nav-label { white-space: nowrap; opacity: 0; transition: opacity .14s ease; }
.nav-drawer.nav-pinned .nav-label, .nav-drawer:hover .nav-label { opacity: 1; }
.nav-drawer .nav-title { opacity: 0; transition: opacity .14s ease; }
.nav-drawer.nav-pinned .nav-title, .nav-drawer:hover .nav-title { opacity: 1; }
/* Active icon accent. MUST be !important AND 3 classes: theme.build_nav_css
   emits `.nav-drawer .q-icon{color:<[menu].text>!important}` (2 classes) and is
   injected AFTER this block, so equal-specificity would lose. */
.nav-drawer .nav-active .nav-icon { color: #6b86ff !important; }
```

Then **delete** the retired dot rules at `main.py:558-560`:
```css
.nav-dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.nav-dot-active { background: #6b86ff; }
.nav-dot-idle { background: #3c4560; }
```

Note the literal `248px` must match `NAV_WIDTH_OPEN` — the test above pins them together.

**Step 4: Verify**

```bash
../.venv/Scripts/python -m pytest tests/test_shell.py -q
```
Expected: PASS, including the untouched `test_nav_css_has_no_reachable_rules`.

**Step 5: Commit**

```bash
git add webgui/main.py webgui/tests/test_shell.py
git commit -m "feat(nav): hover-expand rail CSS over Quasar's inline drawer width"
```

---

### Task 6: Wire the drawer width + the pin toggle

**Files:**
- Modify: `webgui/main.py:793` (drawer construction) and `main.py:814` (hamburger)

**Step 1: Write the failing test**

```python
def test_hamburger_pins_instead_of_toggling_the_drawer(monkeypatch):
    """The rail is always visible, so the hamburger's job changed from show/hide
    to pin/unpin — and the pin must PERSIST (it's a preference, not per-page)."""
    import inspect
    import main
    src = inspect.getsource(main._layout)
    assert "drawer.toggle" not in src, "hamburger now pins, it does not hide the rail"
    assert "_toggle_pin" in src
    assert "drawer_width(" in src, "the drawer's width comes from the pin state"
    assert 'app_settings.set("nav_pinned"' in inspect.getsource(main._toggle_pin)
```

**Step 2: Run it and watch it fail**

```bash
../.venv/Scripts/python -m pytest tests/test_shell.py::test_hamburger_pins_instead_of_toggling_the_drawer -q
```
Expected: FAIL — no attribute `_toggle_pin`.

**Step 3: Implement**

Add above `_layout` (~line 759):

```python
def _toggle_pin(drawer) -> None:
    """Pin/unpin the nav rail (the header hamburger). Pinned = laid out at the
    open width with the hover rule disabled; unpinned = the icon rail. Quasar
    reacts to the width prop, so no page reload. Persisted so the drawer opens
    the way it was left."""
    pinned = not app_settings.get("nav_pinned")
    app_settings.set("nav_pinned", pinned)
    drawer.props(f"width={drawer_width(pinned)}")
    drawer.classes(add="nav-pinned") if pinned else drawer.classes(remove="nav-pinned")
```

Replace the drawer construction at `main.py:793`:

```python
    # Icon rail: laid out at the rail width (or the open width when pinned); the
    # _NAV_CSS :hover rule expands it over the content. behavior=desktop keeps
    # Quasar from flipping it to a mobile overlay at narrow viewports.
    _pinned = bool(app_settings.get("nav_pinned"))
    drawer = (ui.left_drawer(value=True, bordered=True)
              .classes("nav-drawer" + (" nav-pinned" if _pinned else ""))
              .props(f"behavior=desktop width={drawer_width(_pinned)}"))
```

Replace the hamburger at `main.py:814`:

```python
            ui.button(icon="menu", on_click=lambda: _toggle_pin(drawer)).props(
                "flat round dense color=white size=sm").tooltip("Pin / unpin the menu")
```

Confirm `app_settings` is already imported at the top of `main.py`; it is (the ticker/alert
settings use it).

**Step 4: Verify**

```bash
../.venv/Scripts/python -m pytest -q
```
Expected: 728 passed.

**Step 5: Commit**

```bash
git add webgui/main.py webgui/tests/test_shell.py
git commit -m "feat(nav): hamburger pins/unpins the rail, persisted in app_settings"
```

---

### Task 7: Live browser verification

Unit tests cannot prove a CSS `!important` actually beat a Quasar inline style, or that the
page does not reflow on hover. **This task is the real acceptance gate** — @superpowers:verification-before-completion.

**Step 1: Start the app**

Use the preview tool: `preview_start` with `{name: "webgui"}` (`.claude/launch.json`, port
8500, `autoPort:false`). If :8500 is already held by a running webgui, restart it — the
running one is stale and will not have your changes.

**Step 2: Verify the collapsed rail**

```js
// javascript_tool
const d = document.querySelector('aside.q-drawer');
JSON.stringify({
  drawer: getComputedStyle(d).width,                                  // expect "64px"
  offset: getComputedStyle(document.querySelector('.q-page-container')).paddingLeft,
  labelOpacity: getComputedStyle(document.querySelector('.nav-label')).opacity, // "0"
  icons: document.querySelectorAll('.nav-icon').length,               // 7
})
```
Expect `drawer: "64px"`, `offset: "64px"`, `labelOpacity: "0"`, `icons: 7`.

**Step 3: Verify hover expands WITHOUT reflowing**

Hover the rail (`computer` → `hover` at ~`[30, 300]`), wait ~300ms, then re-run the snippet.
Expect `drawer: "248px"` and **`offset` still `"64px"`** — the offset staying put is the whole
point (it proves overlay, not push). `labelOpacity` should now be `"1"`.

If `drawer` is still `64px`, the `!important` did not win: check that `_NAV_CSS` is actually
injected and that `.nav-pinned` is not on the element.

**Step 4: Verify the active icon survives the theme override**

```js
getComputedStyle(document.querySelector('.nav-active .nav-icon')).color
```
Expect `rgb(107, 134, 255)` (the accent) — **not** `rgb(152, 161, 192)` (`[menu].text`).
If you get the latter, the specificity fight was lost — re-read landmine #1.

**Step 5: Verify the badge on a collapsed icon**

Badges only appear when there are new signals. Force one:
```js
// in the page console via javascript_tool — just confirm the DOM shape
document.querySelectorAll('.nav-drawer .q-badge--floating').length  // expect 7
```
Then screenshot the collapsed rail and confirm no badge is clipped by `overflow-x: hidden`.
**If a badge is clipped**, the `overflow-x: hidden` on `.nav-drawer` is cutting the floating
badge; fix by moving the clip to the label row instead:
`.nav-drawer .nav-label { overflow: hidden; }` and drop it from `.nav-drawer`.

**Step 6: Verify pin**

Click the hamburger. Expect `drawer: "248px"` AND `offset: "248px"` (pinned reflows — that is
correct, it is an explicit choice). Reload the page: it must come back pinned. Click again:
back to a 64px rail; reload: still a rail.

**Step 7: Screenshot all four states** and share them: collapsed, hover-expanded, pinned,
badge-on-rail.

**Step 8: Commit any fixes**

```bash
git add webgui/main.py
git commit -m "fix(nav): <whatever the browser check turned up>"
```

---

### Task 8: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` ("webgui structure" section + the `Last updated:` line)

The nav shell's description in "webgui structure" currently says the drawer is a "FLAT main
menu" of items; it must now describe the icon rail, the hover-expand-overlay mechanism, the
pin, and the `[menu].text` specificity trap (a future session WILL hit it). Follow the file's
existing convention: prepend a new dated entry to `Last updated:` and demote the current one
to `Prior —`.

Keep it to a few sentences. Do not restate the plan.

```bash
git add CLAUDE.md
git commit -m "docs: record the nav icon rail in CLAUDE.md"
```

---

## Done when

- 728 webgui tests green.
- The browser check in Task 7 passes all six probes, screenshots shared.
- `git log --oneline` shows one commit per task.
