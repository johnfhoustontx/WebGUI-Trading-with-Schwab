# App UI consistency — Phase 0 (foundations) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the shared page kit (`webgui/pages/ui_kit.py`), the app-wide surface and field styling, the theme changes, the guard test, and the new Settings → Appearance tab that every later phase builds on.

**Architecture:** A Tier-1 kit module exposes the standard pieces (header line, control bar, fields, buttons, region, table, empty state, confirm, toast), with every decision a pure, unit-tested function. Both entrypoints (`main._layout`, `live_main._render`) paint the navy page ground and boxed fields app-wide under a new `.ns-app` scope on their content column. A guard test ratchets the pages that still build raw controls. Appearance becomes its own Settings tab whose preview draws from the same tokens.

**Tech Stack:** NiceGUI 3.13 (Quasar + the bundled Tailwind browser JIT), pytest.

**Design:** [`2026-09-19-app-ui-consistency-design.md`](2026-09-19-app-ui-consistency-design.md) — read it first.

---

## Conventions (read before Task 1)

- **Root:** `D:\WebGUI Trading with Schwab\.claude\worktrees\market-summary-social-images-2333b5`, branch `claude/app-ui-consistency-892db8`. Paths below are relative to it.
- **Python:** the worktree has no venv. `$PY` means `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"`. Webgui tests run from `webgui/`:
  `cd webgui && $PY -m pytest tests/test_theme.py -q`
- **Baseline** (measured 2026-09-19 on this branch): `5053 passed, 1 skipped`; the skip is `tests/test_auth_store.py:39` (POSIX mode bits). Compare the failing **set** and the skipped **set**, never the count.
- **Commits:** `git add <exact paths>` only — never `git add -A` / `git add .`, never `--amend` (another session may share this repository). End every message with
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Do not push.
- **House rules that bind every task:** Tailwind classes through `.classes()`, never `.style()`; colours from `theme.py` tokens; `color=None` on every `ui.button` (drops Quasar's `bg-primary`); a callback that touches widgets is wrapped in `pages.ui_guard.guard` / `guard_async`.
- **Do not weaken an existing assertion to make a test pass.** If an old test pins behaviour this plan deliberately changes, change the assertion to the new behaviour and say so in the commit message.

---

### Task 1: Retire `[buttons_3d]`; the solid danger colour becomes `[palette] danger`

Only `red_mid` in `[buttons_3d]` is read by anything (it fills `BTN_DANGER_SOLID`); the other seven keys have been dead since the July flat-button redesign, yet Settings → Appearance still offers them.

**Files:**
- Modify: `webgui/pages/options/theme.py` (`_DEFAULTS`, `load_theme`, `build_tokens`)
- Modify: `config/theme.toml` (`[palette]`, delete `[buttons_3d]`)
- Modify: `webgui/pages/settings.py:91-98` (`_THEME_SECTIONS` — drop the `buttons_3d` row, or the Appearance card raises `KeyError`)
- Test: `webgui/tests/test_theme.py`

**Step 1: Write the failing tests** — append to `webgui/tests/test_theme.py`:

```python
# -- [buttons_3d] retired 2026-09-19: the solid danger fill is [palette].danger --


def test_danger_is_a_palette_colour(tmp_path):
    p = tmp_path / "theme.toml"
    p.write_text('[palette]\ndanger = "#123456"\n', encoding="utf-8")
    toks = theme.build_tokens(theme.load_theme(p))
    assert "bg-[#123456]" in toks["BTN_DANGER_SOLID"]


def test_a_saved_buttons_3d_red_mid_still_sets_danger(tmp_path):
    """An override written before the retirement may still carry red_mid -
    the one key of that section anything read. It keeps its colour."""
    p = tmp_path / "theme.toml"
    p.write_text('[buttons_3d]\nred_mid = "#654321"\n', encoding="utf-8")
    t = theme.load_theme(p)
    assert t["palette"]["danger"] == "#654321"
    assert "buttons_3d" not in t


def test_palette_danger_wins_over_the_retired_key(tmp_path):
    p = tmp_path / "theme.toml"
    p.write_text('[palette]\ndanger = "#111111"\n[buttons_3d]\nred_mid = "#222222"\n',
                 encoding="utf-8")
    assert theme.load_theme(p)["palette"]["danger"] == "#111111"
```

Also edit two existing tests in the same file:
- In `test_buttons_are_flat_deep_slate`, change the comment `# Solid danger (Terminate): full red fill from [buttons_3d].red_mid + glow.` to `# Solid danger (Terminate): full red fill from [palette].danger + glow.` (the asserted `#d33f3f` stays — it is the new default).
- In `test_build_tokens_reflect_theme_values`, delete the line `'[buttons_3d]\nblue_top = "#123456"\n'` and the two comment lines about `blue_top` beneath `assert "bg-[#00aa55]" in toks["BTN_PRIMARY"]` (keep the `BTN_3D` assertion).

**Step 2: Run to verify the new tests fail**

`cd webgui && $PY -m pytest tests/test_theme.py -q -k "danger or retired"`
Expected: FAIL — `KeyError: 'danger'` / `buttons_3d` still present.

**Step 3: Implement**

In `_DEFAULTS["palette"]`, after `"primary": "#2563eb", "primary_hover": "#1d4fd1",` add:

```python
        # The solid danger fill (Stop all services, a destructive confirm). Was
        # [buttons_3d].red_mid - the one key of that retired section anything read.
        "danger": "#d33f3f",
```

Delete the whole `"buttons_3d": {...},` entry from `_DEFAULTS`.

In `load_theme`, inside the `try:` block, directly after the `for sec, vals in data.items():` loop, add:

```python
        # [buttons_3d] retired 2026-09-19. A file that still carries its one live
        # key and no [palette].danger keeps that colour rather than silently
        # falling back to the default red.
        legacy = (data.get("buttons_3d") or {}).get("red_mid")
        own = (data.get("palette") or {}).get("danger")
        if (isinstance(legacy, str) and legacy.strip()
                and not (isinstance(own, str) and own.strip())):
            merged["palette"]["danger"] = legacy.strip()
```

In `build_tokens`, replace

```python
    p, s, b = theme["palette"], theme["semantic"], theme["buttons_3d"]
```
with
```python
    p, s = theme["palette"], theme["semantic"]
```
replace `dr = hex_rgb(b["red_mid"], (229, 89, 91))` with `dr = hex_rgb(p["danger"], (211, 63, 63))`, and in `"BTN_DANGER_SOLID"` replace `bg-[{b['red_mid']}]` with `bg-[{p['danger']}]`.

In `config/theme.toml`, delete the whole `[buttons_3d]` block (the header line and its eight keys, plus the blank line after it) and add to `[palette]`, directly under `primary_hover`:

```toml
danger       = "#e5595b"       # solid danger fill (Stop all services, destructive confirms)
```

In `webgui/pages/settings.py`, delete the line `    ("buttons_3d", "3D buttons", "color"),` from `_THEME_SECTIONS` (Task 17 removes the whole card; this keeps it rendering meanwhile).

**Step 4: Run to verify**

`cd webgui && $PY -m pytest tests/test_theme.py tests/test_settings.py tests/test_theme_console.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/theme.py config/theme.toml webgui/pages/settings.py webgui/tests/test_theme.py
git commit -m "refactor(theme): retire [buttons_3d]; the solid danger fill is [palette].danger

Seven of its eight colours had driven nothing since the flat-button redesign,
yet Settings -> Appearance still offered them. red_mid, the one live key, moves
to [palette].danger; an override that still carries it keeps its colour.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The quiet button token

**Files:**
- Modify: `webgui/pages/options/theme.py` (`build_tokens`, module exports)
- Test: `webgui/tests/test_theme.py`

**Step 1: Write the failing test.** In `webgui/tests/test_theme.py`, add `"BTN_QUIET"` to the `BTN_TOKENS` list, and append:

```python
def test_quiet_button_is_text_only():
    toks = theme.build_tokens(theme.load_theme("Z:/nope.toml"))
    q = toks["BTN_QUIET"]
    assert "bg-transparent" in q and "text-[#7f8db0]" in q     # default muted
    assert "border" not in q
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_theme.py -q -k "quiet or button_tokens"` — Expected: FAIL (`KeyError` / `AttributeError`).

**Step 3: Implement.** In `build_tokens`'s returned dict, directly after the `"BTN_DANGER_SOLID": (...)` entry:

```python
        # Quiet: text only, for a small link beside a control ("Why no trade?",
        # "Change") that must not read as the page's action.
        "BTN_QUIET": (f"bg-transparent text-[{p['muted']}] hover:text-[{p['title']}] "
                      "rounded-[9px] min-h-[34px] px-2 font-medium"),
```

At module level, after `BTN_DANGER_SOLID = _TOKENS["BTN_DANGER_SOLID"]`:

```python
BTN_QUIET = _TOKENS["BTN_QUIET"]
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_theme.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/theme.py webgui/tests/test_theme.py
git commit -m "feat(theme): BTN_QUIET, the text-only button kind

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Boxed-field CSS takes a scope; `APP_FIELD_CSS` for every page

Today a page gets boxed navy fields only if it wraps itself in `.calc-v2` and injects `QUASAR_INTERNAL_CSS`; a dozen pages don't, so their fields are stock Quasar underlines.

**Files:**
- Modify: `webgui/pages/options/theme.py` (`build_quasar_css`, module exports)
- Test: `webgui/tests/test_theme.py`

**Step 1: Write the failing tests** (append):

```python
def test_quasar_css_scope_is_a_parameter():
    t = theme.load_theme("Z:/nope.toml")
    app = theme.build_quasar_css(t, scope=".ns-app")
    assert ".ns-app .q-field__control{" in app
    assert ".calc-v2" not in app
    assert ".calc-v2 .q-field__control{" in theme.build_quasar_css(t)   # default unchanged
    assert ".strat-menu-navy.q-menu{" in app       # the teleported popup stays global


def test_app_field_css_is_the_app_scope():
    assert theme.APP_FIELD_CSS == theme.build_quasar_css(theme.THEME, scope=".ns-app")
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_theme.py -q -k scope` — Expected: FAIL (`TypeError: unexpected keyword 'scope'`).

**Step 3: Implement.** Change the signature to `def build_quasar_css(theme, scope=".calc-v2"):`, change the docstring's last sentence to `Scoped under ``scope`` (``.calc-v2`` for the pages that still wrap themselves; ``.ns-app`` app-wide).`, and inside the returned f-string replace **every** occurrence of the literal text `.calc-v2 ` (with its trailing space) with `{scope} `. The two `.strat-menu-navy` rule groups carry no `.calc-v2` and stay as they are. Then at module level, after `QUASAR_INTERNAL_CSS = build_quasar_css(THEME)`:

```python
# The same rules for EVERY page: both entrypoints put ``ns-app`` on their content
# column and inject this, so a page no longer needs a scope class of its own.
APP_FIELD_CSS = build_quasar_css(THEME, scope=".ns-app")
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_theme.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/theme.py webgui/tests/test_theme.py
git commit -m "feat(theme): scope the boxed-field CSS; APP_FIELD_CSS for every page

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The app surface — page ground, default card, selected row, Quasar colours

Pages with no wrapper sit on Quasar's flat `#121212` and default `#1d1d1d` cards (the grey public Gamma screen). This paints the palette once, for everyone.

**Files:**
- Modify: `webgui/pages/options/theme.py` (two builders + two exports)
- Test: `webgui/tests/test_theme.py`

**Step 1: Write the failing tests** (append):

```python
def test_surface_css_paints_the_ground_and_the_selected_row():
    t = theme.load_theme("Z:/nope.toml")
    css = theme.build_surface_css(t)
    assert "body.body--dark{background:radial-gradient(" in css
    assert "#16243f 0%" in css and "#0c1424 55%" in css
    assert ".kit-row-selected > td{background:rgba(59,130,246,.08);}" in css
    assert ".kit-row-selected > td:first-child{box-shadow:inset 3px 0 0 #3b82f6;}" in css
    assert ':not([class*="border"])' in css      # a page's own border class wins


def test_quasar_colors_follow_the_palette_and_keep_the_accent():
    t = theme.load_theme("Z:/nope.toml")
    assert theme.build_quasar_colors(t) == {"dark": "#101a30", "dark_page": "#0c1424"}
    t["menu"]["accent"] = "#6b86ff"
    assert theme.build_quasar_colors(t)["primary"] == "#6b86ff"


def test_the_live_surface_constants_are_built_from_the_theme():
    assert theme.SURFACE_CSS == theme.build_surface_css(theme.THEME)
    assert theme.QUASAR_COLORS == theme.build_quasar_colors(theme.THEME)
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_theme.py -q -k "surface or quasar_colors"` — Expected: FAIL (`AttributeError`).

**Step 3: Implement.** Add after `build_quasar_css`:

```python
def build_surface_css(theme):
    """The app-wide SURFACE, painted once for every page by both entrypoints.

    Raw CSS for what no page's ``.classes()`` reaches: the ``<body>`` ground
    (Quasar's flat #121212 until 2026-09-19 - what a page with no wrapper sat
    on), the default ``q-card`` frame, and the selected-row accent that
    ``ui_kit.table`` stamps. Every rule yields to a page's own look: the card
    rules skip an element carrying its own ``border`` / ``rounded`` class, and
    a page that paints its own ground simply covers the body's."""
    p = theme["palette"]
    r, g, b = hex_rgb(p["focus"], (107, 134, 255))
    return (
        f"body.body--dark{{background:radial-gradient(130% 90% at 50% -20%,"
        f"{p['page_bg1']} 0%,{p['page_bg2']} 55%,{p['page_bg3']} 100%) fixed;"
        f"color:{p['text']};}}\n"
        f'.ns-app .q-card--dark:not([class*="border"]){{border:1px solid '
        f"{p['card_border']};box-shadow:none;}}\n"
        f'.ns-app .q-card--dark:not([class*="rounded"]){{border-radius:12px;}}\n'
        f".ns-app .kit-row-selected > td{{background:rgba({r},{g},{b},.08);}}\n"
        f".ns-app .kit-row-selected > td:first-child{{box-shadow:inset 3px 0 0 "
        f"{p['focus']};}}\n"
    )


def build_quasar_colors(theme):
    """``ui.colors(**...)`` for both entrypoints.

    ``dark`` is Quasar's own card / menu / dark-table fill (#1d1d1d stock) and
    ``dark_page`` its page ground (#121212 stock); pointing them at the palette
    makes every default Quasar surface the app's card instead of a grey one.
    ``primary`` is ``[menu].accent`` when set, exactly as before."""
    p = theme["palette"]
    out = {"dark": p["card_bg"], "dark_page": p["page_bg2"]}
    accent = str((theme.get("menu") or {}).get("accent") or "").strip()
    if accent:
        out["primary"] = accent
    return out
```

At module level, after the `APP_FIELD_CSS` line from Task 3:

```python
SURFACE_CSS = build_surface_css(THEME)       # injected by BOTH entrypoints
QUASAR_COLORS = build_quasar_colors(THEME)   # ui.colors(**QUASAR_COLORS) in both
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_theme.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/theme.py webgui/tests/test_theme.py
git commit -m "feat(theme): app surface CSS and Quasar colour variables from the palette

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Table and subtab chrome follow the theme; sentence-case table headers

`shell.TABLE_CSS` and `SUBTAB_CSS` hard-code hexes that happen to equal today's palette, so an Appearance edit never reaches them. The standard also drops the uppercase tracked header.

**Files:**
- Modify: `webgui/shell.py` (`TABLE_CSS`, `SUBTAB_CSS`)
- Test: `webgui/tests/test_shell_seam.py`

**Step 1: Write the failing test** (append to `webgui/tests/test_shell_seam.py`):

```python
def test_table_and_subtab_chrome_follow_the_theme():
    import shell
    from pages.options import theme
    p = theme.THEME["palette"]
    assert f"background: {p['input_bg']};" in shell.TABLE_CSS
    assert f"color: {p['icon']};" in shell.TABLE_CSS
    assert "text-transform: uppercase" not in shell.TABLE_CSS   # sentence-case headers
    assert f"background: {p['card_bg']};" in shell.SUBTAB_CSS
    assert f"color: {p['muted']};" in shell.SUBTAB_CSS
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_shell_seam.py -q -k chrome` — Expected: FAIL (uppercase present).

**Step 3: Implement.** In `webgui/shell.py`, directly above `TABLE_CSS`, add `_P = theme.THEME["palette"]` and replace the two constants with:

```python
TABLE_CSS = f"""
.q-table__middle {{ max-height: 65vh; }}
/* Sticky header in the theme's inset tone; column labels in sentence case and
   the eyebrow colour (the 2026-09-19 standard retired the uppercase tracking). */
.q-table thead tr th {{
  position: sticky; top: 0; z-index: 1; background: {_P['input_bg']};
  font-size: 11px; font-weight: 600; color: {_P['icon']};
}}
/* Faint row dividers between body rows. */
.q-table tbody tr:not(:last-child) td {{ border-bottom: 1px solid rgba(255,255,255,.04); }}
"""
```

```python
SUBTAB_CSS = f"""
.compact-subtabs {{
  background: {_P['card_bg']}; border-radius: 10px; padding: 3px 4px; min-height: 0;
}}
.compact-subtabs .q-tab {{
  min-height: 26px; padding: 0 11px; margin-right: 2px;
  border-radius: 7px; background: transparent; color: {_P['muted']};
}}
.compact-subtabs .q-tab--active {{ background: rgba(255,255,255,.08); color: {_P['title']}; }}
.compact-subtabs .q-tab__indicator {{ display: none; }}
.compact-subtabs .q-tab__label {{ font-size: 12px; }}
"""
```

Keep the comment blocks above each constant, updating "uppercase faint column labels (10.5px / 600 / .06em)" to "sentence-case column labels".

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_shell_seam.py tests/test_shell.py tests/test_live_main.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/shell.py webgui/tests/test_shell_seam.py
git commit -m "feat(shell): table and subtab chrome from the theme; sentence-case headers

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The menu accent reaches the nav pill, tab fill and icon

`[menu].accent` has never reached the active nav pill, the tab-strip fill or the active icon — they are hard-coded in `main._NAV_CSS`, and `config/theme.toml` warns the knob "LOOKS like it drives them". Raw CSS can carry any colour (the JIT limit is Tailwind-only), so `build_nav_css` re-emits them.

**Files:**
- Modify: `webgui/pages/options/theme.py` (`build_nav_css`)
- Modify: `webgui/main.py` (the `_NAV_CSS` comment at the `.nav-drawer .nav-active` rule)
- Modify: `config/theme.toml` (`[menu]` comment)
- Test: `webgui/tests/test_theme.py`

**Step 1: Write the failing tests** (append):

```python
def test_menu_accent_reaches_the_nav_pill_tabs_and_icon():
    t = theme.load_theme("Z:/nope.toml")
    t["menu"]["accent"] = "#ff8800"
    css = theme.build_nav_css(t)
    assert ".nav-drawer .nav-active{background:rgba(255,136,0,0.13)!important;}" in css
    assert ".nav-drawer .nav-active .nav-icon{color:#ff8800!important;}" in css
    assert ".compact-tabs .q-tab--active{background:rgba(255,136,0,0.16)!important;}" in css


def test_an_empty_accent_leaves_the_stock_nav_alone():
    assert ".nav-active" not in theme.build_nav_css(theme.load_theme("Z:/nope.toml"))
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_theme.py -q -k accent` — Expected: FAIL.

**Step 3: Implement.** In `build_nav_css`, before `return "\n".join(rules)`:

```python
    if m.get("accent"):
        # The ONE accent reaches the active pill, the tab-strip fill and the
        # active icon too (injected after main._NAV_CSS, whose stock rgba it
        # overrides). Raw CSS may carry any value; the JIT limit that once kept
        # these hard-coded applies to Tailwind classes only.
        r, g, b = hex_rgb(m["accent"], (107, 134, 255))
        rules += [
            f".nav-drawer .nav-active{{background:rgba({r},{g},{b},0.13)!important;}}",
            f".nav-drawer .nav-active .nav-icon{{color:{m['accent']}!important;}}",
            f".compact-tabs .q-tab--active{{background:rgba({r},{g},{b},0.16)!important;}}",
        ]
```

Replace the sentence in `build_nav_css`'s docstring that begins "``accent`` knob is NOT css" through "cannot ride ``--q-primary``)" with: `The ``accent`` knob feeds ``ui.colors(primary=…)`` (via ``build_quasar_colors``) AND, since 2026-09-19, the active nav pill, tab-strip fill and active icon below.`

In `webgui/main.py`, replace the comment above `.nav-drawer .nav-active { background: rgba(107,134,255,0.13); }`:

```
/* Active nav item pill — the "Deep Slate" look: a SUBTLE navy tint (not a solid
   accent fill), paired with the item's own icon, which carries the active state
   (see the .nav-active .nav-icon accent below). The rgba here is the STOCK look;
   when [menu].accent is set, theme.build_nav_css re-emits this wash, the
   .compact-tabs fill and the icon accent in that colour (injected after this
   block, so it wins) — since 2026-09-19. */
```

In `config/theme.toml` `[menu]`, replace the paragraph starting `# READ THIS BEFORE CHANGING \`accent\`` (through `# change it and they silently stay blue. To move them, edit main._NAV_CSS too.`) and the `accent` line with:

```toml
# `accent` is the ONE menu accent: it feeds ui.colors(primary=…) (switches,
# sliders, color=primary buttons) AND, since 2026-09-19, the active nav pill,
# the tab-strip fill and the active nav icon (theme.build_nav_css re-emits them
# in this colour). It does not recolour the header bar — header_bg does.
accent    = "#6b86ff"          # menu accent: Quasar controls + active nav pill/tab/icon
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_theme.py tests/test_shell.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/theme.py webgui/main.py config/theme.toml webgui/tests/test_theme.py
git commit -m "fix(theme): the menu accent reaches the nav pill, tab fill and active icon

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Both entrypoints paint the surface and the fields

**Files:**
- Modify: `webgui/main.py` (`_layout`)
- Modify: `webgui/live_main.py` (`_render`, `_CONTENT`)
- Test: `webgui/tests/test_shell_seam.py`, `webgui/tests/test_live_main.py`

**Step 1: Write the failing tests.** Append to `webgui/tests/test_shell_seam.py` (it already has `_add_css_args(path, func_name)`, which parses a file without importing it):

```python
def test_both_entrypoints_paint_the_app_surface_and_fields():
    """⚠ BOTH, or the public and private screens drift - the published pages
    render the same modules."""
    webgui = pathlib.Path(__file__).resolve().parents[1]
    for path, func in ((webgui / "main.py", "_layout"),
                       (webgui / "live_main.py", "_render")):
        injected = _add_css_args(path, func)
        assert "SURFACE_CSS" in injected, f"{path.name} lost the app surface"
        assert "APP_FIELD_CSS" in injected, f"{path.name} lost the app-wide fields"
        assert "ui.colors(**theme.QUASAR_COLORS)" in path.read_text(encoding="utf-8")


def test_both_content_columns_carry_the_app_scope():
    webgui = pathlib.Path(__file__).resolve().parents[1]
    assert 'classes("ns-app w-full p-4 gap-3 pb-10")' in \
        (webgui / "main.py").read_text(encoding="utf-8")
    assert '_CONTENT = "ns-app w-full p-4 gap-3"' in \
        (webgui / "live_main.py").read_text(encoding="utf-8")
```

In `webgui/tests/test_live_main.py::test_the_public_render_injects_the_page_level_css`, add after the existing three asserts:

```python
    from pages.options import theme
    assert theme.SURFACE_CSS in seen,   "the published screens sit on Quasar's grey"
    assert theme.APP_FIELD_CSS in seen, "the published fields render as stock Quasar"
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_shell_seam.py tests/test_live_main.py -q` — Expected: FAIL on the new assertions.

**Step 3: Implement.**

In `main._layout`, after `ui.add_css(PANEL_SCROLL_CSS)  # a dashboard panel keeps its own overflow` add:

```python
    ui.add_css(theme.SURFACE_CSS)    # page ground + default card frame (both entrypoints)
    ui.add_css(theme.APP_FIELD_CSS)  # boxed fields on every page, under .ns-app
```

Replace the whole `if theme.MENU_ACCENT:` block (its comment and the `ui.colors(primary=theme.MENU_ACCENT)` line) with:

```python
    # Quasar's colour variables: primary = [menu].accent (Quasar controls), and
    # dark / dark_page = the palette's card and page, so a default q-card or
    # q-menu is the app's card rather than Quasar's grey (build_quasar_colors).
    ui.colors(**theme.QUASAR_COLORS)
```

and change the content column to `with ui.column().classes("ns-app w-full p-4 gap-3 pb-10") as content:` (keep its `pb-10` comment).

In `webgui/live_main.py`: set `_CONTENT = "ns-app w-full p-4 gap-3"`; after `ui.add_css(shell.PANEL_SCROLL_CSS)` add the same two `ui.add_css(theme.…)` lines; replace the `if theme.MENU_ACCENT:` block with `ui.colors(**theme.QUASAR_COLORS)` and a one-line comment.

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_shell_seam.py tests/test_live_main.py tests/test_shell.py -q` — Expected: PASS. If an older test asserts the literal `ui.colors(primary=theme.MENU_ACCENT)`, change it to assert `ui.colors(**theme.QUASAR_COLORS)` — the accent still reaches `primary` through `build_quasar_colors` (Task 4 tests it).

**Step 5: Commit**

```bash
git add webgui/main.py webgui/live_main.py webgui/tests/test_shell_seam.py webgui/tests/test_live_main.py
git commit -m "feat(shell): both entrypoints paint the app surface and boxed fields

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: `ui_kit` — module, freshness, toast arguments

**Files:**
- Create: `webgui/pages/ui_kit.py`
- Test: `webgui/tests/test_ui_kit.py`

**Step 1: Write the failing tests** — create `webgui/tests/test_ui_kit.py`:

```python
"""Tests for the page kit (pages/ui_kit.py) - the one look and behaviour."""
import datetime as dt

import pytest
from nicegui import ui

from pages import ui_kit as kit
from pages.options import theme

UTC = dt.timezone.utc


def _utc(day, h, m):
    return dt.datetime(2026, 9, day, h, m, tzinfo=UTC)


# -- freshness: the header's "Updated" stamp ----------------------------------
def test_nothing_published_is_waiting_never_a_time():
    assert kit.freshness(None, _utc(18, 15, 0)) == ("Waiting for data", "waiting")
    assert kit.freshness("not a time", _utc(18, 15, 0)) == ("Waiting for data", "waiting")


def test_freshness_reads_central_time():
    # 15:42 UTC on 2026-09-18 is 10:42 CDT
    assert kit.freshness("2026-09-18T15:42:00+00:00", _utc(18, 15, 50)) == \
        ("Updated 10:42 AM CT", "fresh")


def test_a_naive_stamp_is_utc_not_local():
    assert kit.freshness("2026-09-18T15:42:00", _utc(18, 15, 50))[0] == \
        "Updated 10:42 AM CT"


def test_a_stamp_from_another_day_names_the_day():
    assert kit.freshness("2026-09-17T20:15:00+00:00", _utc(18, 15, 0)) == \
        ("Updated Sep 17 3:15 PM CT", "fresh")


def test_past_the_threshold_is_stale():
    assert kit.freshness("2026-09-18T15:00:00+00:00", _utc(18, 15, 30),
                         stale_after_sec=600) == ("Stale · updated 10:00 AM CT", "stale")


def test_no_threshold_means_never_stale():
    """None = the view is not due to publish now (e.g. the scanner at night),
    so its age says nothing."""
    assert kit.freshness("2026-09-11T15:00:00+00:00", _utc(18, 15, 0))[1] == "fresh"


# -- toasts --------------------------------------------------------------------
def test_toast_args_one_position_and_a_type_always():
    assert kit.toast_args("ok", "Saved") == {
        "message": "Saved", "type": "positive", "position": "bottom",
        "timeout": 4000, "multi_line": False}
    assert kit.toast_args("warn", "x")["timeout"] == 8000
    assert kit.toast_args("error", "x")["type"] == "negative"
    assert kit.toast_args("info", "x" * 81)["multi_line"] is True


def test_an_unknown_toast_kind_is_an_error():
    with pytest.raises(ValueError):
        kit.toast_args("loud", "x")
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_ui_kit.py -q` — Expected: FAIL (`ImportError: cannot import name 'ui_kit'`).

**Step 3: Implement** — create `webgui/pages/ui_kit.py`:

```python
"""The page kit: one look and one behaviour for every screen (Tier-1).

Every page builds its header line, control bar, fields, buttons, loading
region, table, empty state, confirm dialog and toast from here, so two screens
cannot drift apart. The standard - and why each rule is what it is - is
``docs/plans/2026-09-19-app-ui-consistency-design.md``;
``tests/test_ui_kit_guard.py`` fails when a page builds a button, dialog, toast
or table of its own.

Tier-1 safe: imports ``nicegui``, the theme, the busy spinner, the Symbol-field
helpers, ``bus_client`` and ``shell`` - nothing outside the allow-list - so the
public live process can render a page built from it. Decisions are PURE
module-level functions (``freshness``, ``toast_args``, ``button_classes``,
``table_columns``), unit-tested without a browser; the builders stay thin.
"""
import contextlib
import datetime as _dt
import time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from nicegui import run, ui

import bus_client
import shell
from pages import busy as _busy
from pages.options import theme as _t
from pages.options.inputs import (bind_symbol_load, mark_symbol_loaded,
                                  select_all_on_focus)
from pages.ui_guard import guard, guard_async

CT = ZoneInfo("America/Chicago")

# ── freshness: the header's "Updated" stamp ─────────────────────────────────
WAITING_TEXT = "Waiting for data"
FRESHNESS_CLASS = {"waiting": _t.MUTED, "fresh": _t.MUTED, "stale": _t.TXT_WARN}


def _parse_ts(ts):
    """An ISO stamp as an aware datetime, or None. A naive stamp is UTC: the bus
    writes ``datetime.now(timezone.utc).isoformat()``."""
    if not ts:
        return None
    try:
        when = _dt.datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return when


def freshness(ts, now, stale_after_sec=None):
    """``(text, state)`` for a view's last-confirmed stamp. PURE.

    ``state`` is ``"waiting"`` (nothing published - never a made-up time),
    ``"fresh"`` or ``"stale"`` (older than ``stale_after_sec``). A ``None``
    threshold means the view is not due to publish now, so its age says
    nothing and it is never stale. Central time everywhere; the day is named
    when it is not today's."""
    when = _parse_ts(ts)
    if when is None:
        return WAITING_TEXT, "waiting"
    local = when.astimezone(CT)
    clock = local.strftime("%I:%M %p").lstrip("0")
    if local.date() != now.astimezone(CT).date():
        clock = f"{local.strftime('%b')} {local.day} {clock}"
    clock += " CT"
    if stale_after_sec is not None and (now - when).total_seconds() > stale_after_sec:
        return f"Stale · updated {clock}", "stale"
    return f"Updated {clock}", "fresh"


def _stale_after(view, now):
    """The nav badge's own per-view threshold, so the stamp and the badge agree.
    ``alerts`` is imported lazily: it imports the scanner page, which imports
    this kit."""
    import alerts
    if not alerts.expects_updates(view, now):
        return None
    return alerts.stale_after(view, now)


# ── toasts ───────────────────────────────────────────────────────────────────
TOAST_POSITION = "bottom"
_TOAST = {"info": ("info", 4000), "ok": ("positive", 4000),
          "warn": ("warning", 8000), "error": ("negative", 8000)}


def toast_args(kind, text):
    """The ``ui.notify`` arguments for a toast. PURE. One position, a type
    always, and a longer life for anything the reader has to act on."""
    if kind not in _TOAST:
        raise ValueError(f"unknown toast kind {kind!r}; use one of {sorted(_TOAST)}")
    qtype, timeout = _TOAST[kind]
    return {"message": text, "type": qtype, "position": TOAST_POSITION,
            "timeout": timeout, "multi_line": len(text) > 80}


def toast(kind, text):
    """Report the OUTCOME of an action. Validation is shown inline, waiting is
    shown by a spinner - neither is a toast."""
    ui.notify(**toast_args(kind, text))
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_ui_kit.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/ui_kit.py webgui/tests/test_ui_kit.py
git commit -m "feat(ui_kit): the page kit module - freshness stamp and toasts

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: `ui_kit` — buttons and the busy state

**Files:** Modify `webgui/pages/ui_kit.py`; Test `webgui/tests/test_ui_kit.py`

**Step 1: Write the failing tests** (append):

```python
# -- buttons -------------------------------------------------------------------
def test_button_kinds_map_to_the_theme_tokens():
    assert kit.button_classes("primary") == theme.BTN_PRIMARY
    assert kit.button_classes("secondary") == theme.BTN
    assert kit.button_classes("danger") == theme.BTN_DANGER
    assert kit.button_classes("quiet") == theme.BTN_QUIET
    assert kit.button_classes("danger_solid") == theme.BTN_DANGER_SOLID


def test_an_unknown_button_kind_is_an_error_not_a_default():
    with pytest.raises(ValueError):
        kit.button_classes("blue")


def test_preview_tokens_override_the_live_ones():
    toks = dict(theme._TOKENS, BTN_PRIMARY="bg-[#123456]")
    assert kit.button_classes("primary", toks) == "bg-[#123456]"


def test_button_is_sentence_case_and_not_quasar_blue():
    with ui.card():
        b = kit.button("Run scan", kind="primary", icon="play_arrow")
    assert b._props.get("no-caps") is True
    assert "bg-primary" not in b.classes
    assert set(theme.BTN_PRIMARY.split()) <= set(b.classes)


def test_an_icon_button_must_say_what_it_does():
    with pytest.raises(TypeError):
        kit.icon_button("delete")          # tooltip is required


def test_set_busy_spins_and_disables_then_releases():
    with ui.card():
        b = kit.button("Load", kind="primary")
    kit.set_busy(b)
    assert b._props.get("loading") is True and not b.enabled
    kit.set_busy(b, False)
    assert not b._props.get("loading") and b.enabled


def test_the_backstop_releases_a_button_whose_answer_never_came():
    with ui.card():
        b = kit.button("Load")
    kit.set_busy(b, timeout=0)
    assert b._kit_busy["timer"].active is True
    b._kit_busy["tick"]()
    assert b.enabled and not b._props.get("loading")
    assert b._kit_busy["timer"].active is False
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_ui_kit.py -q` — Expected: FAIL (`AttributeError: button_classes`).

**Step 3: Implement** (append to `ui_kit.py`):

```python
# ── buttons ──────────────────────────────────────────────────────────────────
# Four kinds for pages: primary (one per area), secondary (everything else),
# danger (outline), quiet (text only). danger_solid is the confirm dialog's own.
BUTTON_KINDS = ("primary", "secondary", "danger", "quiet", "danger_solid")
_KIND_TOKEN = {"primary": "BTN_PRIMARY", "secondary": "BTN", "danger": "BTN_DANGER",
               "quiet": "BTN_QUIET", "danger_solid": "BTN_DANGER_SOLID"}
BUSY_TIMEOUT_SEC = _busy.BUSY_TIMEOUT_SEC


def button_classes(kind, tokens=None):
    """The class string for a button kind. PURE. ``tokens`` lets the Appearance
    preview draw with unsaved colours; pages never pass it."""
    if kind not in _KIND_TOKEN:
        raise ValueError(f"unknown button kind {kind!r}; use one of {BUTTON_KINDS}")
    return (tokens or _t._TOKENS)[_KIND_TOKEN[kind]]


def button(text, *, kind="secondary", icon=None, on_click=None, tooltip=None,
           tokens=None):
    """A labelled button: sentence case, verb first, one of the four kinds."""
    b = ui.button(text, icon=icon, color=None, on_click=on_click) \
        .props("no-caps unelevated").classes(button_classes(kind, tokens))
    if tooltip:
        with b:
            ui.tooltip(tooltip).props("delay=350 max-width=340px")
    return b


def icon_button(icon, *, tooltip, on_click=None):
    """An icon-only button (per row, per panel). The tooltip is REQUIRED: an
    icon alone does not say what it does."""
    b = ui.button(icon=icon, color=None, on_click=on_click) \
        .props("flat round dense size=sm").classes(_t.MUTED)
    with b:
        ui.tooltip(tooltip).props("delay=350")
    return b


def _busy_state(btn):
    """One backstop timer per button, created on first use and reused, so a
    long session does not accumulate timers (the busy.py reasoning)."""
    st = getattr(btn, "_kit_busy", None)
    if st is None:
        st = {"deadline": None}

        def _tick():
            if st["deadline"] is not None and time.monotonic() >= st["deadline"]:
                set_busy(btn, False)

        st["tick"] = _tick
        with btn.parent_slot:
            st["timer"] = ui.timer(1.0, guard(_tick), active=False)
        btn._kit_busy = st
    return st


def set_busy(btn, busy=True, *, timeout=BUSY_TIMEOUT_SEC):
    """Show a button's own spinner and hold it disabled until the result lands
    (``set_busy(btn, False)``) or ``timeout`` passes - no double submits, and no
    button left spinning when the answer never comes."""
    st = _busy_state(btn)
    if busy:
        btn.props(add="loading")
        btn.disable()
        st["deadline"] = time.monotonic() + timeout
        st["timer"].active = True
    else:
        btn.props(remove="loading")
        btn.enable()
        st["deadline"] = None
        st["timer"].active = False
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_ui_kit.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/ui_kit.py webgui/tests/test_ui_kit.py
git commit -m "feat(ui_kit): the four button kinds and a button's own busy state

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: `ui_kit` — page, header line, status line, notice

**Files:** Modify `webgui/pages/ui_kit.py`; Test `webgui/tests/test_ui_kit.py`

**Step 1: Write the failing tests** (append):

```python
# -- page frame and header line ---------------------------------------------------
def test_a_form_page_caps_its_width():
    with ui.card():
        full, form = kit.page(), kit.page("form")
    assert "max-w-3xl" in form.classes and "max-w-3xl" not in full.classes


def test_header_without_a_view_has_no_stamp():
    with ui.card():
        h = kit.header("Paper Ledger")
    assert h.title.text == "Paper Ledger"
    assert not h.stamp.visible


def test_header_stamp_turns_warning_when_stale_and_back():
    with ui.card():
        h = kit.header("Paper Ledger", view="options:paper_trades")
    now = _utc(18, 15, 30)
    h.set_stamp("2026-09-18T15:00:00+00:00", 600, now)
    assert h.stamp.text.startswith("Stale") and theme.TXT_WARN in h.stamp.classes
    h.set_stamp("2026-09-18T15:29:00+00:00", 600, now)
    assert h.stamp.text.startswith("Updated") and theme.TXT_WARN not in h.stamp.classes


def test_the_public_origin_drops_the_title():
    """live_main draws the screen's name in its own header."""
    import shell
    shell.publish({})
    try:
        with ui.card():
            h = kit.header("Flow Alerts")
        assert h.title is None
    finally:
        shell.unpublish()


def test_header_actions_sit_right_of_the_stamp():
    with ui.card():
        h = kit.header("X", view="v")
    kids = list(h.row.default_slot.children)
    assert kids.index(h.stamp) < kids.index(h.actions)
```

**Step 2: Run** — Expected: FAIL.

**Step 3: Implement** (append):

```python
# ── page frame, header line, status line, notice ────────────────────────────
TITLE = f"text-h6 font-semibold {_t.LABEL}"


def page(width="full"):
    """The page column. ``"form"`` caps a settings-style page at a readable
    width; everything else is full width."""
    return ui.column().classes(
        "w-full gap-4" if width == "full" else "w-full max-w-3xl gap-4")


def header(title, *, view=None, stale=False, poll_sec=5.0):
    """The page's one header line: the title left; the Updated stamp and then
    the page actions right - add the primary action LAST so it sits rightmost.

    ``view`` is the bus view the page's data comes from; its ``:ts`` side key
    (the time the publisher last confirmed it current) drives the stamp, read
    off the loop every ``poll_sec``. No view, no stamp. ``stale=True`` only for
    a view published on a SCHEDULE: it then turns amber past the nav badge's
    own threshold (``alerts.stale_after``). An on-demand or once-a-day view
    leaves it off and is never called stale - its age says nothing. On the
    PUBLIC origin the title is omitted - live_main names the screen itself."""
    with ui.row().classes("w-full items-center gap-3 flex-wrap min-h-[38px]") as row:
        title_lbl = None if shell.is_public() else ui.label(title).classes(TITLE)
        ui.space()
        stamp = ui.label("").classes(f"text-xs {_t.MUTED}")
        stamp.set_visibility(view is not None)
        actions = ui.row().classes("items-center gap-2 no-wrap")
    state = {"cls": _t.MUTED}

    def set_stamp(ts, stale_after_sec=None, now=None):
        now = now or _dt.datetime.now(_dt.timezone.utc)
        text, st = freshness(ts, now, stale_after_sec)
        stamp.text = text
        cls = FRESHNESS_CLASS[st]
        if cls != state["cls"]:
            stamp.classes(remove=state["cls"], add=cls)
            state["cls"] = cls

    if view is not None:
        @guard_async
        async def _poll():
            _ver, ts = await run.io_bound(bus_client.read_meta, view)
            now = _dt.datetime.now(_dt.timezone.utc)
            set_stamp(ts, _stale_after(view, now) if stale else None, now)

        ui.timer(0.1, _poll, once=True)
        ui.timer(poll_sec, _poll)
    return SimpleNamespace(row=row, title=title_lbl, stamp=stamp, actions=actions,
                           set_stamp=set_stamp)


def status_line(text=""):
    """The one line of counts a board shows above its table ("12 trades ·
    3 open"). The time lives in the header stamp, never here."""
    return ui.label(text).classes(_t.EYEBROW)


_S = _t.THEME["semantic"]
NOTICE = (f"w-full items-center gap-3 rounded-[10px] px-3 py-2 "
          f"bg-[{_S['warning']}]/10 border border-[{_S['warning']}]/30")


def notice(text, *, icon="info"):
    """A one-line notice across the page (a pending restart, a service note).
    Returns the row, so a caller may add a button inside ``with notice(...)``."""
    with ui.row().classes(NOTICE) as row:
        ui.icon(icon).classes(_t.TXT_WARN)
        ui.label(text).classes(f"text-sm grow {_t.LABEL}")
    return row
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_ui_kit.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/ui_kit.py webgui/tests/test_ui_kit.py
git commit -m "feat(ui_kit): page frame, header line with the Updated stamp, status line, notice

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: `ui_kit` — control bar and fields

**Files:** Modify `webgui/pages/ui_kit.py`; Test `webgui/tests/test_ui_kit.py`

**Step 1: Write the failing tests** (append):

```python
# -- control bar and fields ---------------------------------------------------------
def test_field_puts_its_label_above():
    with ui.card():
        with kit.field("Expiry") as col:
            s = ui.select(["Oct 17"])
    kids = list(col.default_slot.children)
    assert kids[0].text == "Expiry" and kids[1] is s


def test_symbol_field_is_the_one_symbol_behaviour():
    with ui.card():
        inp = kit.symbol_field(value="SPY", on_load=lambda: None)
    assert "uppercase" in inp.classes                       # select_all_on_focus
    assert inp._symbol_load_last["sym"] == "SPY"            # seeded: tabbing through SPY is no load


def test_symbol_error_shows_under_the_field_and_clears():
    with ui.card():
        inp = kit.symbol_field(on_load=lambda: None)
    kit.symbol_error(inp, "No such ticker")
    assert inp._props["error"] is True and inp._props["error-message"] == "No such ticker"
    kit.symbol_error(inp, None)
    assert not inp._props.get("error")


def test_number_field_checks_on_leaving_not_per_keystroke():
    with ui.card():
        n = kit.number_field("Contracts", value=1, min=1, max=100)
    n.value = 0
    assert n.error is None
    assert n.validate() is False and n.error == "At least 1"
    n.value = 101
    assert n.validate() is False and n.error == "At most 100"
    n.value = None
    assert n.validate() is False and n.error == "Enter a number"


def test_gate_holds_go_while_a_field_is_wrong():
    with ui.card():
        n = kit.number_field("Contracts", value=1, min=1)
        go = kit.button("Load", kind="primary")
    sync = kit.gate(go, n)
    assert go.enabled
    n.value = 0
    sync()
    assert not go.enabled
    n.value = 3
    sync()
    assert go.enabled
```

**Step 2: Run** — Expected: FAIL.

**Step 3: Implement** (append):

```python
# ── control bar and fields ──────────────────────────────────────────────────
FIELD_PROPS = "dense hide-bottom-space"


def control_bar():
    """The card that holds a page's fields: left to right, labels above, and
    the Go button right after the last field (``with control_bar(): ...``)."""
    return ui.row().classes(f"{_t.CARD} w-full items-end gap-x-4 gap-y-2 flex-wrap")


@contextlib.contextmanager
def field(label, *, grow=False):
    """A labelled slot: the label ABOVE (never floating, never placeholder-only),
    then whatever control the caller builds inside it."""
    with ui.column().classes("gap-1 w-full" if grow else "gap-1") as col:
        ui.label(label).classes(_t.EYEBROW)
        yield col


def text_field(label, *, value="", placeholder="", width="w-40", on_change=None):
    """A labelled text input. A placeholder is an example value, never the label."""
    with field(label, grow=width == "w-full"):
        inp = ui.input(value=value, placeholder=placeholder or None,
                       on_change=on_change).props(FIELD_PROPS).classes(width)
    return inp


def select_field(label, options, *, value=None, width="w-40", on_change=None, **kw):
    """A labelled dropdown. A filter on what is on screen applies on change."""
    with field(label, grow=width == "w-full"):
        sel = ui.select(options, value=value, on_change=on_change, **kw) \
            .props(f"{FIELD_PROPS} options-dense").classes(width)
    return sel


def number_field(label, *, value=None, min=None, max=None, step=None,
                 width="w-28", format=None, on_change=None):
    """A labelled number whose range is checked when you LEAVE it, not per
    keystroke; the message shows in red under the field. The range is not
    passed to Quasar, which would silently clamp instead of saying so."""
    checks = {"Enter a number": lambda v: v is not None}
    if min is not None:
        checks[f"At least {min:g}"] = lambda v, lo=min: v is None or v >= lo
    if max is not None:
        checks[f"At most {max:g}"] = lambda v, hi=max: v is None or v <= hi
    with field(label, grow=width == "w-full"):
        n = ui.number(value=value, step=step, format=format, on_change=on_change,
                      validation=checks).props(FIELD_PROPS).classes(width)
    n.without_auto_validation()
    n.on("blur", lambda _e: n.validate(return_result=False))
    return n


def symbol_field(label="Symbol", *, value="", on_load, tab=True, width="w-[110px]"):
    """The one Symbol behaviour: uppercase; the whole ticker selected on click or
    tab-in; Enter loads; tab-out loads only a CHANGED symbol (the dedup is seeded
    from ``value``, so tabbing through the default is no load); an unknown
    ticker is reported under it (``symbol_error``). The page's Go button calls
    ``on_load`` directly, which always reloads."""
    with field(label):
        inp = ui.input(value=value) \
            .props(f"{FIELD_PROPS} spellcheck=false").classes(f"{width} font-semibold")
    select_all_on_focus(inp)
    bind_symbol_load(inp, on_load, tab=tab)
    return inp


# A page that writes the Symbol field from code (a hand-off) marks it loaded.
symbol_loaded = mark_symbol_loaded


def symbol_error(inp, text=None):
    """Show - or, with ``None``, clear - the message under a Symbol field."""
    inp.error = text or None


def gate(go, *fields):
    """Keep ``go`` disabled while any of ``fields`` fails its check. Returns the
    sync function, for a page that sets a value from code."""
    def sync(_e=None):
        ok = [f.validate() for f in fields]      # a list: validate EVERY field
        if all(ok):
            go.enable()
        else:
            go.disable()
    for f in fields:
        f.on("blur", sync)
    sync()
    return sync
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_ui_kit.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/ui_kit.py webgui/tests/test_ui_kit.py
git commit -m "feat(ui_kit): control bar, labelled fields, the one Symbol field, gate

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: `ui_kit` — region, empty state, table

**Files:** Modify `webgui/pages/ui_kit.py`; Test `webgui/tests/test_ui_kit.py`

**Step 1: Write the failing tests** (append):

```python
# -- region, empty state, table ---------------------------------------------------
def test_the_region_spinner_survives_a_repaint():
    """Five pages mounted their spinner on the container their repaint clears,
    so it was deleted on the first paint."""
    with ui.card():
        r = kit.region("Loading…")
    with r.content:
        ui.label("old rows")
    r.content.clear()
    assert r.busy.element in r.outer.default_slot.children
    r.busy.show()
    assert r.busy.element.visible


def test_empty_state_is_one_muted_line():
    with ui.card():
        e = kit.empty("Nothing traded yet today")
    assert e.text == "Nothing traded yet today" and theme.MUTED in e.classes


def test_table_columns_sortable_and_numbers_right():
    cols = [{"name": "symbol", "label": "Symbol", "field": "symbol"},
            {"name": "pnl", "label": "P&L", "field": "pnl"},
            {"name": "actions", "label": "", "field": "actions"}]
    out = kit.table_columns(cols, numeric=("pnl",))
    assert out[0]["sortable"] is True and out[0]["align"] == "left"
    assert out[1]["align"] == "right"
    assert out[2]["sortable"] is False
    assert "sortable" not in cols[0]                  # the input is not mutated


def test_an_explicit_unsortable_column_stays_unsortable():
    out = kit.table_columns([{"name": "checks", "field": "checks", "sortable": False}])
    assert out[0]["sortable"] is False


def test_mark_selected_stamps_exactly_one_row():
    rows = [{"id": 1}, {"id": 2}, {"id": 3}]
    kit.mark_selected(rows, 2)
    assert [r["_selected"] for r in rows] == [False, True, False]
    kit.mark_selected(rows, None)
    assert not any(r["_selected"] for r in rows)


def test_table_is_dense_flat_and_draws_the_selected_row():
    with ui.card():
        t = kit.table([{"name": "symbol", "label": "Symbol", "field": "symbol"}], [])
    assert t._props.get("dense") is True and t._props.get("flat") is True
    assert "kit-row-selected" in t._props[":table-row-class-fn"]
    assert "row._row_class" in t._props[":table-row-class-fn"]     # a page's own class survives
```

**Step 2: Run** — Expected: FAIL.

**Step 3: Implement** (append):

```python
# ── region, empty state, table ──────────────────────────────────────────────
def region(text="Loading…", *, classes="w-full"):
    """A block whose contents a repaint replaces. Repaint ``content`` (clear and
    rebuild it); the spinner lives on ``outer``, so a clear can never delete it
    - the bug five pages had. ``busy.show()`` on first load and every refresh."""
    outer = ui.element("div").classes(classes)
    with outer:
        content = ui.column().classes("w-full gap-3")
    spin = _busy.build_busy(outer, text)
    return SimpleNamespace(outer=outer, content=content, busy=spin)


EMPTY = f"w-full text-center text-[13px] {_t.MUTED} py-6"


def empty(text):
    """The one empty-state line. "Nothing published yet" (copy.WAITING_*) and
    "nothing to report" stay worded differently - the caller picks the words."""
    return ui.label(text).classes(EMPTY)


TABLE_PROPS = "dense flat"
# Composes a page's own ``_row_class`` (e.g. the scanner's stale dimming) with
# the selected-row accent, so neither has to give way.
ROW_CLASS_FN = ("row => [row._row_class, row._selected ? 'kit-row-selected' : '']"
                ".filter(Boolean).join(' ')")


def table_columns(columns, *, numeric=()):
    """Column defaults. PURE - returns new dicts. Every data column sortable
    (``actions`` never; an explicit ``sortable: False`` stays); ``numeric``
    columns right-aligned, the rest left."""
    out = []
    for col in columns:
        c = dict(col)
        if c.get("name") == "actions":
            c["sortable"] = False
        else:
            c.setdefault("sortable", True)
        c["align"] = "right" if c.get("name") in numeric else c.get("align", "left")
        out.append(c)
    return out


def mark_selected(rows, row_id, *, key="id"):
    """Stamp ``_selected`` so exactly the clicked row carries the accent."""
    for r in rows:
        r["_selected"] = row_id is not None and r.get(key) == row_id
    return rows


def table(columns, rows=None, *, row_key="id", numeric=(), rows_per_page=0,
          classes="w-full"):
    """The one table: dense, flat, sticky header (the app-wide ``TABLE_CSS``),
    numbers right-aligned, sortable columns, and the selected row drawn from
    ``_selected`` (``mark_selected``). ``rows_per_page=0`` shows every row."""
    t = ui.table(columns=table_columns(columns, numeric=numeric),
                 rows=list(rows or []), row_key=row_key,
                 pagination={"rowsPerPage": rows_per_page}) \
        .classes(classes).props(TABLE_PROPS)
    # Written to _props directly: a props STRING would be re-parsed and mangle
    # the quotes inside the arrow function (the scanner.py precedent).
    t._props[":table-row-class-fn"] = ROW_CLASS_FN
    return t
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_ui_kit.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/ui_kit.py webgui/tests/test_ui_kit.py
git commit -m "feat(ui_kit): region (spinner survives repaints), empty state, the one table

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: `ui_kit` — the one confirm dialog

**Files:** Modify `webgui/pages/ui_kit.py`; Test `webgui/tests/test_ui_kit.py`

**Step 1: Write the failing tests** (append):

```python
# -- confirm dialog ---------------------------------------------------------------
def _confirm(**kw):
    fired = []
    with ui.card():
        d = kit.confirm("Delete the NVDA call credit?", "It leaves the ledger for good.",
                        confirm_text="Delete", on_confirm=lambda: fired.append(1), **kw)
    return d, fired


def test_cancel_comes_first_and_the_row_is_right_aligned():
    d, _ = _confirm(danger=True)
    kids = list(d.actions.default_slot.children)
    assert kids.index(d.cancel) < kids.index(d.confirm)
    assert "justify-end" in d.actions.classes


def test_a_destructive_confirm_is_solid_red_and_a_plain_one_primary():
    d, _ = _confirm(danger=True)
    assert set(theme.BTN_DANGER_SOLID.split()) <= set(d.confirm.classes)
    d2, _ = _confirm()
    assert set(theme.BTN_PRIMARY.split()) <= set(d2.confirm.classes)


def test_confirm_runs_once_then_closes():
    d, fired = _confirm()
    d.open()
    d.run()
    d.run()                       # a queued Enter after the click
    assert fired == [1] and d.dialog.value is False


def test_returning_false_keeps_the_dialog_open():
    fired = []
    with ui.card():
        d = kit.confirm("Open?", confirm_text="Open",
                        on_confirm=lambda: fired.append(1) or False)
    d.open()
    d.run()
    assert fired == [1] and d.dialog.value is True


def test_a_disabled_confirm_does_nothing():
    d, fired = _confirm()
    d.open()
    d.confirm.disable()
    d.run()
    assert fired == []
```

**Step 2: Run** — Expected: FAIL.

**Step 3: Implement** (append):

```python
# ── confirm dialog ──────────────────────────────────────────────────────────
CONFIRM_CARD = f"{_t.CARD} min-w-[360px] max-w-[520px] gap-3"


def confirm(title, body="", *, confirm_text, on_confirm, danger=False):
    """The one confirm dialog: a title, one sentence, then Cancel and the
    confirm, right-aligned in that order. Enter confirms, Esc cancels. A
    destructive action passes ``danger=True`` - the only place solid red appears.

    Add any inputs to ``handle.content`` before ``open()``. ``on_confirm`` runs
    first; returning ``False`` keeps the dialog open (a check that failed), and
    anything else closes it. It runs at most once per ``open()``, so a click
    followed by a queued Enter cannot act twice. Build the dialog at the page's
    own level, never inside a container a repaint clears - a dialog deletes
    itself with its slot (the swing.py precedent). Build it ONCE and retitle
    it per use (``handle.title.text``, ``handle.body.text``) rather than a new
    dialog per click, which would leave one behind in the page each time."""
    with ui.dialog() as dlg, ui.card().classes(CONFIRM_CARD) as card:
        title_lbl = ui.label(title).classes(f"text-subtitle1 font-semibold {_t.LABEL}")
        body_lbl = ui.label(body).classes(f"text-sm {_t.MUTED}")
        body_lbl.set_visibility(bool(body))
        content = ui.column().classes("w-full gap-2")
        with ui.row().classes("w-full justify-end gap-2 pt-1") as actions:
            cancel = button("Cancel", kind="secondary", on_click=dlg.close)
            ok = button(confirm_text, kind="danger_solid" if danger else "primary")
    st = {"done": False}

    @guard
    def run_(_e=None):
        if st["done"] or not ok.enabled:
            return
        if on_confirm() is False:
            return
        st["done"] = True
        dlg.close()

    def open_():
        st["done"] = False
        dlg.open()

    ok.on_click(run_)
    card.on("keydown.enter", run_)
    return SimpleNamespace(dialog=dlg, title=title_lbl, content=content, body=body_lbl,
                           actions=actions, cancel=cancel, confirm=ok, run=run_,
                           open=open_, close=dlg.close)
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_ui_kit.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/ui_kit.py webgui/tests/test_ui_kit.py
git commit -m "feat(ui_kit): the one confirm dialog - Cancel first, danger solid only here

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 14: The guard test and the inline-style guard

**Files:**
- Create: `webgui/tests/test_ui_kit_guard.py`
- Modify: `webgui/tests/test_no_inline_style.py`

**Step 1: Write the guard** — create `webgui/tests/test_ui_kit_guard.py`. The `ALLOWED` counts below were measured on this branch on 2026-09-19 before any page migrated:

```python
"""Guard: pages build buttons, dialogs, toasts and tables through pages/ui_kit.py.

The consistency standard (docs/plans/2026-09-19-app-ui-consistency-design.md)
holds only if a page cannot quietly build its own button, dialog, toast or
table, or load a web font of its own. ALLOWED is the ratchet: every page that
still does, by file and count. A migrated page DELETES its entry; a count may
only fall; and the list must match the code EXACTLY, so a page that drops a raw
call lowers its entry in the same commit - otherwise the list stops describing
the code. An entry may outlive the migration only with a written reason (a
control that is not an action button: a segmented picker, a leg-table toggle).
"""
import ast
import collections
import pathlib

GUARDED = ("button", "dialog", "notify", "table", "add_head_html")
PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"
KIT = "ui_kit.py"

ALLOWED = {
    "config_editor.py": {"button": 12, "dialog": 2, "notify": 10},
    "desk.py": {"add_head_html": 3, "button": 1},
    "driver.py": {"button": 8, "dialog": 2, "notify": 1, "table": 5},
    "eod.py": {"button": 3, "notify": 2},
    "manuals.py": {"button": 1},
    "market.py": {"add_head_html": 1, "button": 2},
    "options/calculator.py": {"add_head_html": 1, "button": 3, "dialog": 1, "notify": 3},
    "options/captured.py": {"button": 5, "dialog": 1, "notify": 5, "table": 1},
    "options/detail.py": {"button": 1},
    "options/entry_panel.py": {"button": 3},
    "options/expected_move.py": {"button": 1, "notify": 1},
    "options/flow.py": {"table": 1},
    "options/gamma.py": {"button": 8, "notify": 7},
    "options/handoff.py": {"button": 4, "dialog": 2, "notify": 12},
    "options/income.py": {"notify": 1, "table": 1},
    "options/leg_editor.py": {"button": 10},
    "options/matrix.py": {"table": 1},
    "options/paper.py": {"button": 9, "dialog": 2, "notify": 6, "table": 1},
    "options/portfolio.py": {"button": 6, "dialog": 1, "notify": 3, "table": 2},
    "options/rescue.py": {"button": 5, "dialog": 1, "notify": 7, "table": 1},
    "options/scanner.py": {"button": 3, "dialog": 1, "notify": 2, "table": 1},
    "options/shares.py": {"table": 1},
    "options/simulator.py": {"button": 1, "notify": 1},
    "options/strategy_menu.py": {"button": 2},
    "options/swing.py": {"button": 7, "table": 1},
    "portfolio.py": {"button": 1, "table": 3},
    "sentiment.py": {"add_head_html": 1, "button": 3, "notify": 1},
    "sentiment_bullbear.py": {"add_head_html": 1, "button": 1, "notify": 2},
    "sentiment_momentum.py": {"add_head_html": 1, "button": 3, "notify": 1, "table": 1},
    "sentiment_rotation.py": {"add_head_html": 1, "button": 1, "notify": 1},
    "sentiment_rrg.py": {"add_head_html": 1, "button": 1, "notify": 1},
    "sentiment_sectors.py": {"add_head_html": 1, "button": 3, "notify": 1},
    "settings.py": {"button": 12, "dialog": 2, "notify": 4},
    "status.py": {"button": 3, "notify": 4},
    "symbol.py": {"add_head_html": 1, "button": 2},
    "terminate.py": {"button": 3, "dialog": 1, "notify": 1},
    "trade.py": {"button": 3, "table": 2},
    "trade_board.py": {"button": 2},
    "trade_plan_screen.py": {"button": 2, "notify": 1},
    "trade_shell.py": {"add_head_html": 1, "button": 1},
}


def raw_calls():
    """``{page file: {ui.<guarded>: count}}`` for every page but the kit."""
    out = {}
    for f in sorted(PAGES.rglob("*.py")):
        rel = f.relative_to(PAGES).as_posix()
        if rel == KIT:
            continue
        counts = collections.Counter()
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "ui"
                    and n.func.attr in GUARDED):
                counts[n.func.attr] += 1
        if counts:
            out[rel] = dict(sorted(counts.items()))
    return out


def test_no_new_page_builds_its_own_controls():
    now = raw_calls()
    new = {f: now[f] for f in sorted(set(now) - set(ALLOWED))}
    assert not new, f"build these through pages/ui_kit.py instead: {new}"


def test_allowed_matches_the_code_exactly():
    now = raw_calls()
    drift = {f: {"allowed": ALLOWED[f], "actual": now.get(f, {})}
             for f in ALLOWED if now.get(f, {}) != ALLOWED[f]}
    assert not drift, (
        f"ALLOWED no longer describes the pages: {drift}. A count that FELL: lower "
        "the entry (delete it at zero). A count that ROSE: use pages/ui_kit.py.")


def test_the_kit_really_builds_what_it_guards():
    """Non-vacuity: the guard exempts ui_kit.py because the kit is where these
    calls live. If the kit stopped making them, the exemption would hide a gap."""
    src = (PAGES / KIT).read_text(encoding="utf-8")
    for attr in ("button", "dialog", "notify", "table"):
        assert f"ui.{attr}(" in src, f"ui_kit no longer builds ui.{attr}"
```

In `webgui/tests/test_no_inline_style.py`, append:

```python
# The page kit and the Appearance tab (2026-09-19 consistency work): the kit is
# what every page's controls are built from, so a .style() there would spread
# to every screen at once.
def test_ui_kit_and_appearance_have_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages"
    for fn in ("ui_kit.py", "appearance.py"):
        path = base / fn
        if not path.exists():          # appearance.py lands in Task 16
            continue
        src = path.read_text(encoding="utf-8")
        assert ".style(" not in src, f"{fn} still uses .style()"
        assert ":style=" not in src, f"{fn} still uses a Vue :style= slot binding"
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_ui_kit_guard.py tests/test_no_inline_style.py -q`
Expected: PASS. If `test_allowed_matches_the_code_exactly` fails, a page changed since the counts were measured — do **not** edit the page; set that entry to the "actual" value the message prints.

**Step 3: Commit**

```bash
git add webgui/tests/test_ui_kit_guard.py webgui/tests/test_no_inline_style.py
git commit -m "test: guard - pages build buttons, dialogs, toasts and tables via ui_kit

Starts as a ratchet naming every current offender by file and count; each
migration phase deletes its pages' entries.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 15: Appearance — the pure parts

**Files:**
- Create: `webgui/pages/appearance.py`
- Test: `webgui/tests/test_appearance.py`

**Step 1: Write the failing tests** — create `webgui/tests/test_appearance.py`:

```python
"""Tests for Settings -> Appearance (pages/appearance.py)."""
from pages import appearance
from pages.options import theme


def _keys():
    return [(s, k) for _label, _kind, keys in appearance.GROUPS for s, k in keys]


def test_every_editable_key_is_on_the_screen_exactly_once():
    seen = _keys()
    assert len(seen) == len(set(seen)), "a key is on the screen twice"
    want = {(s, k) for s in appearance.EDITABLE_SECTIONS for k in theme._DEFAULTS[s]}
    assert set(seen) == want, (f"missing {sorted(want - set(seen))}, "
                               f"extra {sorted(set(seen) - want)}")


def test_no_retired_or_page_scoped_section_is_editable():
    sections = {s for s, _k in _keys()}
    assert sections.isdisjoint({"buttons_3d", "brand", "console", "macro",
                                "sectors", "rotation", "calc", "flow"})


def test_group_kinds_are_known():
    assert {kind for _l, kind, _k in appearance.GROUPS} <= {"color", "text", "menu"}


def test_size_error():
    for ok in ("14", "14px", "1.1rem", " 16 "):
        assert appearance.size_error(ok) is None, ok
    assert appearance.size_error("") == "Enter a size, like 14"
    assert appearance.size_error("big") == "A number of pixels, like 14 or 14px"


def test_edited_theme_overlays_without_mutating_the_base():
    base = theme.load_theme("Z:/nope.toml")
    out = appearance.edited_theme(base, {("palette", "card_bg"): "#123456"})
    assert out["palette"]["card_bg"] == "#123456"
    assert base["palette"]["card_bg"] == "#101a30"


def test_updates_from_groups_by_section():
    assert appearance.updates_from({("palette", "card_bg"): "#111111",
                                    ("semantic", "positive"): "#222222"}) == {
        "palette": {"card_bg": "#111111"}, "semantic": {"positive": "#222222"}}
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_appearance.py -q` — Expected: FAIL (`ImportError`).

**Step 3: Implement** — create `webgui/pages/appearance.py`:

```python
"""Settings -> Appearance: every colour and font in the app, with a live preview.

Since 2026-09-19 every screen is built from ``pages/ui_kit.py`` on one palette,
so this editor restyles the WHOLE app - which is also what makes its preview
honest: it draws the kit's pieces from the same tokens, with the unsaved colours
laid over the saved ones.

The groups follow the app's standard (surfaces, text, fields, buttons, status
colours, charts, type, menu), not ``config/theme.toml``'s sections: a group is
a DISPLAY mapping over existing keys, so the file never has to move for the
screen to make sense. Saving writes the operator's override
(``config/local/theme.toml`` via ``theme.save_theme_values``). The theme loads
once at startup, so a saved change shows after a web GUI restart, and this
preview is the only place an unsaved colour can be seen.
"""
import re

from nicegui import ui

import config_schema as _cs
from pages import ui_kit as kit
from pages.options import theme
from pages.ui_guard import guard

# (group, editor kind, [(section, key), ...]). Every key of EDITABLE_SECTIONS
# appears exactly once - test_appearance pins it, so a new theme key cannot
# ship without a place on this screen.
GROUPS = [
    ("Surfaces", "color", [("palette", k) for k in (
        "page_bg1", "page_bg2", "page_bg3", "page_border", "card_bg", "card_border")]),
    ("Text", "color", [("palette", k) for k in ("title", "text", "muted", "icon")]),
    ("Fields", "color", [("palette", k) for k in (
        "input_bg", "input_border", "input_text", "focus")]),
    ("Buttons", "color", [("palette", k) for k in (
        "primary", "primary_hover", "btn_bg", "btn_hover", "btn_border", "danger")]),
    ("Status colours", "color", [("semantic", k) for k in (
        "positive", "warning", "negative", "neutral")]),
    ("Charts", "color", [("charts", k) for k in ("green", "red", "yellow", "flat", "cyan")]),
    ("Type", "text", [("typography", k) for k in (
        "family", "font_url", "numeric", "titles", "subtitles", "sections", "body", "small")]),
    ("Menu", "menu", [("menu", k) for k in (
        "accent", "header_bg", "drawer_bg", "text", "hover_bg", "title")]),
]
EDITABLE_SECTIONS = ("palette", "semantic", "charts", "typography", "menu")
_SIZE_KEYS = ("titles", "subtitles", "sections", "body", "small")
_SIZE_RE = re.compile(r"^\d+(\.\d+)?(px|rem|em)?$")


def size_error(value):
    """The message for a text size that is not a size, else ``None``. PURE."""
    v = str(value or "").strip()
    if not v:
        return "Enter a size, like 14"
    return None if _SIZE_RE.match(v) else "A number of pixels, like 14 or 14px"


def edited_theme(base, edits):
    """``base`` (a ``load_theme`` dict) with ``edits`` ``{(section, key): value}``
    laid over it - what the preview draws. PURE; ``base`` is not mutated."""
    out = {sec: dict(vals) for sec, vals in base.items()}
    for (sec, key), val in edits.items():
        if sec in out:
            out[sec][key] = val
    return out


def updates_from(edits):
    """``{section: {key: value}}`` for ``theme.save_theme_values``. PURE."""
    out = {}
    for (sec, key), val in edits.items():
        out.setdefault(sec, {})[key] = val
    return out
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_appearance.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/appearance.py webgui/tests/test_appearance.py
git commit -m "feat(appearance): groups over the theme keys, size checks, preview overlay

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 16: Appearance — the editor, preview, footer and restart banner

**Files:** Modify `webgui/pages/appearance.py`; Test `webgui/tests/test_appearance.py`

**Step 1: Write the failing test** (append):

```python
def test_render_builds_every_group():
    from nicegui import ui
    with ui.card() as host:
        appearance.render()
    texts = {getattr(e, "text", None) for e in host.descendants()}
    for label, _kind, _keys in appearance.GROUPS:
        assert label in texts, f"group {label!r} did not render"
    assert "Save changes" in texts and "Discard" in texts
```

**Step 2: Run** — Expected: FAIL (`AttributeError: render`).

**Step 3: Implement** (append to `appearance.py`):

```python
def render():
    """The Appearance tab: grouped editors left, the live preview right, a
    sticky Discard / Save footer, and the shared pending-restart banner."""
    from pages import config_editor      # its pending-restart set is the app's one
    P = theme.THEME["palette"]           # the editor itself wears the RUNNING theme
    state = {"base": theme.load_theme(), "edits": {}, "errors": {}}
    tiles, inputs = {}, {}

    def effective(sec, key):
        return state["edits"].get((sec, key), state["base"][sec][key])

    with kit.page():
        kit.header("Appearance")
        banner = ui.row().classes("w-full")
        with ui.row().classes("w-full items-start gap-4 no-wrap"):
            editor = ui.column().classes("grow min-w-0 gap-4")
            with ui.column().classes("w-[380px] shrink-0 gap-2 sticky top-2"):
                ui.label("Preview — unsaved colours show here first").classes(theme.EYEBROW)
                preview = ui.column().classes("w-full gap-3")
        footer = ui.row().classes(
            f"w-full items-center gap-3 sticky bottom-0 z-10 bg-[{P['page_bg2']}] "
            f"border-t border-[{P['card_border']}] px-4 py-2 rounded-t-lg")

    # ── editing ────────────────────────────────────────────────────────────
    def _set(sec, key, value):
        if value == state["base"][sec][key]:
            state["edits"].pop((sec, key), None)
        else:
            state["edits"][(sec, key)] = value
        _paint_preview()
        _paint_footer()

    def _show_tile(sec, key):
        t = tiles[(sec, key)]
        val = effective(sec, key)
        cls = f"bg-[{val}]"                 # continuous value: runtime class, remove/add
        if cls != t["cls"]:
            t["swatch"].classes(remove=t["cls"], add=cls)
            t["cls"] = cls
        t["hex"].text = val

    @guard
    def _pick(sec, key, value):
        _set(sec, key, value)
        _show_tile(sec, key)

    def _tile(sec, key):
        val = effective(sec, key)
        with ui.column().classes(
                f"w-[118px] gap-0 cursor-pointer overflow-hidden rounded-[10px] "
                f"border border-[{P['card_border']}] bg-[{P['input_bg']}]"):
            swatch = ui.element("div").classes(f"w-full h-12 bg-[{val}]")
            with ui.column().classes("px-2 py-1 gap-0"):
                ui.label(theme.knob_label(key)).classes(
                    f"text-[11px] font-semibold leading-tight {theme.LABEL}")
                hex_lbl = ui.label(val).classes(f"text-[10px] {theme.MUTED}")
            ui.color_picker(on_pick=lambda e, s=sec, k=key: _pick(s, k, e.color))
        tiles[(sec, key)] = {"swatch": swatch, "hex": hex_lbl, "cls": f"bg-[{val}]"}

    def _text(sec, key, kind):
        placeholder = ("Leave empty for the stock look" if kind == "menu"
                       else "14" if key in _SIZE_KEYS else "")
        inp = kit.text_field(theme.knob_label(key), value=effective(sec, key),
                             placeholder=placeholder, width="w-full")

        @guard
        def _commit(_e=None, s=sec, k=key, el=inp):
            v = (el.value or "").strip()
            err = size_error(v) if (s == "typography" and k in _SIZE_KEYS) else None
            el.error = err
            if err:
                state["errors"][(s, k)] = err
                _paint_footer()
                return
            state["errors"].pop((s, k), None)
            _set(s, k, v)

        inp.on("blur", _commit)
        inp.on("keydown.enter", _commit)
        inputs[(sec, key)] = inp

    with editor:
        for label, kind, keys in GROUPS:
            with ui.column().classes(f"{theme.CARD} w-full gap-2"):
                ui.label(label).classes(f"text-subtitle2 font-semibold {theme.LABEL}")
                if kind == "color":
                    with ui.row().classes("gap-2 flex-wrap"):
                        for sec, key in keys:
                            _tile(sec, key)
                    continue
                if kind == "menu":
                    ui.label("Leave a field empty to keep the stock look.") \
                        .classes(f"text-xs {theme.MUTED}")
                with ui.grid(columns=2).classes("w-full gap-x-4 gap-y-2"):
                    for sec, key in keys:
                        _text(sec, key, kind)

    # ── preview: the kit's pieces, drawn from the EDITED tokens ─────────────
    def _paint_preview():
        t = edited_theme(state["base"], state["edits"])
        tok = theme.build_tokens(t)
        p, ty, size = t["palette"], t["typography"], theme.normalize_size
        preview.clear()
        with preview:
            with ui.column().classes(
                    f"w-full gap-3 rounded-[12px] p-3 border border-[{p['page_border']}] "
                    f"bg-[{p['page_bg2']}] text-[{p['text']}]"):
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    ui.label("Strategy Finder").classes(
                        f"font-semibold text-[{p['title']}] text-[{size(ty['titles'])}]")
                    ui.space()
                    ui.label("Updated 10:42 AM CT").classes(
                        f"text-[{p['muted']}] text-[{size(ty['small'])}]")
                    kit.button("Refresh", kind="secondary", icon="refresh", tokens=tok)
                with ui.row().classes(f"{tok['CARD']} w-full items-end gap-3 flex-wrap"):
                    for label, value, focused in (("Symbol", "SPY", False),
                                                  ("Expiry", "Oct 17", True)):
                        with ui.column().classes("gap-1"):
                            ui.label(label).classes(tok["EYEBROW"])
                            edge = p["focus"] if focused else p["input_border"]
                            ui.label(value).classes(
                                f"min-w-[84px] rounded-[8px] px-[10px] py-[7px] "
                                f"bg-[{p['input_bg']}] border border-[{edge}] "
                                f"text-[{p['input_text']}]")
                    kit.button("Load", kind="primary", icon="search", tokens=tok)
                with ui.row().classes("w-full gap-2 flex-wrap"):
                    for kind, text in (("primary", "Primary"), ("secondary", "Secondary"),
                                       ("danger", "Delete"), ("quiet", "Quiet")):
                        kit.button(text, kind=kind, tokens=tok)
                grid = "grid grid-cols-[1fr_1fr_1fr] w-full px-3 py-[6px]"
                with ui.column().classes(f"{tok['CARD']} w-full gap-0 p-0 overflow-hidden"):
                    with ui.element("div").classes(
                            f"{grid} bg-[{p['input_bg']}] text-[{p['icon']}] "
                            "text-[11px] font-semibold"):
                        for head in ("Symbol", "Strategy", "P&L"):
                            ui.label(head)
                    for sym, strat, pnl, key, selected in (
                            ("SPY", "Put credit", "+42.00", "TXT_POS", False),
                            ("NVDA", "Call credit", "-18.50", "TXT_NEG", True)):
                        mark = (f" bg-[{p['focus']}]/[.08] border-l-[3px] border-[{p['focus']}]"
                                if selected else "")
                        with ui.element("div").classes(f"{grid}{mark}"):
                            ui.label(sym)
                            ui.label(strat)
                            ui.label(pnl).classes(tok[key])
                with ui.row().classes("w-full gap-3"):
                    for text, key in (("Profit", "TXT_POS"), ("Warning", "TXT_WARN"),
                                      ("Loss", "TXT_NEG"), ("Neutral", "TXT_NEUTRAL")):
                        ui.label(text).classes(f"{tok[key]} font-semibold "
                                               f"text-[{size(ty['small'])}]")
                ui.label("Nothing to report yet").classes(
                    f"w-full text-center text-[13px] text-[{p['muted']}] py-1")

    # ── actions, footer, restart banner ─────────────────────────────────────
    def _repaint_values():
        for sec, key in tiles:
            _show_tile(sec, key)
        for (sec, key), el in inputs.items():
            el.value = effective(sec, key)
            el.error = None
        _paint_preview()
        _paint_footer()

    def _paint_banner():
        banner.clear()
        if _cs.WEBGUI not in config_editor._PENDING:
            return
        with banner:
            with kit.notice("Saved appearance is waiting for a web GUI restart.",
                            icon="restart_alt"):
                kit.button("Restart now", kind="primary", icon="restart_alt",
                           on_click=restart_dlg.open)

    def _mark_restart():
        config_editor._PENDING.add(_cs.WEBGUI)
        _paint_banner()

    @guard
    def _discard():
        state["edits"].clear()
        state["errors"].clear()
        _repaint_values()
        kit.toast("info", "Unsaved changes discarded.")

    @guard
    def _save():
        if state["errors"]:
            kit.toast("warn", "Fix the highlighted values first.")
            return
        state["base"] = theme.save_theme_values(updates_from(state["edits"]))
        state["edits"].clear()
        _repaint_values()
        _mark_restart()
        kit.toast("ok", "Saved. Restart the web GUI to see it on every screen.")

    def _reset():
        state["base"] = theme.reset_theme()
        state["edits"].clear()
        state["errors"].clear()
        _repaint_values()
        _mark_restart()
        kit.toast("ok", "Back to the shipped values. Restart the web GUI to see it.")

    def _restart():
        config_editor._PENDING.discard(_cs.WEBGUI)
        kit.toast("warn", "Restarting the web GUI. This page reloads in a few seconds.")
        config_editor._restart_webgui()

    reset_dlg = kit.confirm(
        "Reset every colour and font to the shipped values?",
        "Your saved appearance is removed. It shows after a web GUI restart.",
        confirm_text="Reset", danger=True, on_confirm=_reset)
    restart_dlg = kit.confirm(
        "Restart the web GUI?", "Every open page reloads in a few seconds.",
        confirm_text="Restart now", on_confirm=_restart)

    def _paint_footer():
        footer.clear()
        n, errs = len(state["edits"]), len(state["errors"])
        with footer:
            kit.button("Reset to shipped values", kind="danger", on_click=reset_dlg.open)
            if errs:
                text = f"{errs} value{'s' if errs != 1 else ''} to fix before saving"
                cls = theme.TXT_NEG
            elif n:
                text, cls = f"{n} unsaved change{'s' if n != 1 else ''}", theme.TXT_WARN
            else:
                text, cls = "All changes saved", theme.MUTED
            ui.label(text).classes(f"text-sm grow {cls}")
            discard = kit.button("Discard", kind="secondary", on_click=_discard)
            save = kit.button("Save changes", kind="primary", icon="save", on_click=_save)
            if not (n or errs):
                discard.disable()
            if not n or errs:
                save.disable()

    _paint_preview()
    _paint_footer()
    _paint_banner()
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_appearance.py tests/test_no_inline_style.py tests/test_ui_kit_guard.py -q` — Expected: PASS (`appearance.py` has no raw guarded calls — every button, dialog and toast goes through the kit).

**Step 5: Commit**

```bash
git add webgui/pages/appearance.py webgui/tests/test_appearance.py
git commit -m "feat(appearance): editor with a live preview, staged save and restart banner

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 17: Appearance becomes a Settings tab; help text and pointers follow

**Files:**
- Modify: `webgui/pages/settings.py` (`SUBTABS`, `render`, delete `_THEME_SECTIONS` and the Appearance card, drop the now-unused `BTN_3D_DANGER` import)
- Modify: `webgui/page_help.py` (`/settings` help and `SUBTAB_HELP`)
- Modify: `webgui/config_schema.py` (`NOT_HERE`)
- Modify: `webgui/tests/test_page_help.py`, `webgui/tests/test_theme_console.py`, `webgui/tests/test_settings.py`, `webgui/tests/test_ui_kit_guard.py`

**Step 1: Write / update the tests.**

Append to `webgui/tests/test_settings.py`:

```python
def test_settings_has_three_tabs_in_order():
    from pages import settings
    assert settings.SUBTABS == ("General", "Appearance", "Configuration")


def test_the_general_tab_no_longer_carries_the_appearance_card():
    import inspect
    from pages import settings
    assert "Appearance" not in inspect.getsource(settings._render_general)
```

In `webgui/tests/test_page_help.py::test_subtab_help_covers_every_sub_tab`, change `"/settings": {"General", "Configuration"},` to `"/settings": {"General", "Appearance", "Configuration"},`.

In `webgui/tests/test_theme_console.py`, replace `test_console_is_not_in_the_settings_appearance_editor`'s body with:

```python
    from pages import appearance
    sections = {s for _label, _kind, keys in appearance.GROUPS for s, _k in keys}
    assert "console" not in sections
    assert "brand" not in sections
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_settings.py tests/test_page_help.py tests/test_theme_console.py -q` — Expected: FAIL (two SUBTABS / help failures).

**Step 3: Implement.**

`webgui/pages/settings.py`:
- `SUBTABS = ("General", "Appearance", "Configuration")`
- In `render()`, add `from pages import appearance` beside `from pages import config_editor`, and between the General and Configuration panels:

```python
        with ui.tab_panel("Appearance"):
            appearance.render()
```
- Update `render()`'s docstring: "Three sub-tabs … General (the app preferences below), Appearance (every colour and font, `pages/appearance.py`) and Configuration (every config/*.toml setting)."
- Delete `_THEME_SECTIONS` (with its comment) and the whole `# ── Appearance — every configurable GUI component` card in `_render_general` (from that comment through the row holding Save / Save & restart web GUI / Reset to defaults).
- Change `from pages.options.theme import BTN_3D, BTN_3D_DANGER` to `from pages.options.theme import BTN_3D` if `BTN_3D_DANGER` is no longer used in the file (grep first).
- In the module docstring, replace "the Appearance section saves to the override file config/local/theme.toml (``theme.save_theme_values``) and applies on a web-GUI restart — the theme loads once at startup." with "Appearance is its own tab (``pages/appearance.py``)."

`webgui/page_help.py`:
- In the `/settings` help, replace `Two tabs. **General** holds the app's own preferences (below). **Configuration**` with `Three tabs. **General** holds the app's own preferences (below). **Appearance** sets every colour and font, with a live preview; a saved change shows after a web GUI restart. **Configuration**`.
- Replace `The General tab controls the alert chimes, notifications, the ticker, and the\napp's look.` with `The General tab controls the alert chimes, notifications and the ticker.`
- Delete the two-line bullet beginning `- **Appearance** — every color, font, and menu style`.
- In `SUBTAB_HELP["/settings"]`, set `"General"` to `"App preferences: alert sounds, spoken alerts, the ticker, API usage and maintenance."` and add:

```python
        "Appearance": "Every colour and font in the app, with a live preview. "
                      "Saved changes show after a web GUI restart.",
```

`webgui/config_schema.py`: change `"theme.toml": "Settings → General → Appearance",` to `"theme.toml": "Settings → Appearance",`.

`webgui/tests/test_ui_kit_guard.py`: run the guard; `settings.py`'s counts fell (the Appearance card's buttons, dialog and toasts left). Set its `ALLOWED` entry to the "actual" value the failure prints.

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_settings.py tests/test_page_help.py tests/test_theme_console.py tests/test_config_schema.py tests/test_config_editor.py tests/test_ui_kit_guard.py tests/test_appearance.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/settings.py webgui/page_help.py webgui/config_schema.py webgui/tests/test_settings.py webgui/tests/test_page_help.py webgui/tests/test_theme_console.py webgui/tests/test_ui_kit_guard.py
git commit -m "feat(settings): Appearance is its own tab, with the preview

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 18: Full suite

**Step 1:** `cd webgui && $PY -m pytest -q -p no:randomly -rfs`
Expected: every test passes except the baseline skip `tests/test_auth_store.py:39`. Diff the failing and skipped **sets** against the baseline. For each new failure, decide: a test pinning behaviour this plan deliberately changed (uppercase table headers, `ui.colors(primary=…)`, the Appearance card inside General) → update the assertion to the new behaviour, naming it in the commit; anything else is a regression → fix the code.

**Step 2:** `cd .. && $PY -m pytest shared/tests -q` and `$PY -m pytest tools/tests -q` — Expected: unchanged from before this branch (theme.toml moved a key; nothing outside webgui reads `[buttons_3d]`).

**Step 3: Commit** any test updates with an explicit list of files.

---

### Task 19: See it — the local page harness

There is no dev environment and the app is behind a login Claude does not enter. This harness renders one page module on its own server with the app's page CSS and a fake Bus.

**Files:**
- Create: `tools/ui_harness.py`
- Test: `tools/tests/test_ui_harness.py`

**Step 1: Write the failing test** — create `tools/tests/test_ui_harness.py`:

```python
"""The harness must import without starting a server (tests import it)."""
import importlib.util
import pathlib


def test_harness_imports_without_starting_a_server():
    path = pathlib.Path(__file__).resolve().parents[1] / "ui_harness.py"
    spec = importlib.util.spec_from_file_location("ui_harness", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)            # __name__ != "__main__": no ui.run
    assert callable(mod.main)
```

**Step 2: Run** `$PY -m pytest tools/tests/test_ui_harness.py -q` — Expected: FAIL (file missing).

**Step 3: Implement** — create `tools/ui_harness.py`:

```python
"""Render ONE page module on a local NiceGUI server, with the app's page CSS.

For checking a page in a real browser without the login or a dev stack (there
is no dev environment - CLAUDE.md "Environments"). A fake Bus stands in for
Redis; ``--seed file.json`` fills it with ``{view: payload}`` first. The page
is drawn in the same ``ns-app`` content column, with the same CSS, that both
entrypoints give it - but with no nav rail or header.

    python tools/ui_harness.py settings --port 9591
    python tools/ui_harness.py options.paper --seed seed.json

Not a test and never for prod: with a fake Bus no command is executed.
"""
import argparse
import importlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "webgui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("page", help="module under webgui/pages, e.g. settings or options.paper")
    ap.add_argument("--port", type=int, default=9591)
    ap.add_argument("--seed", help="JSON file of {view: payload} for the fake bus")
    args = ap.parse_args(argv)

    import bus_client
    from shared.bus import Bus
    bus_client._bus = Bus(fake=True)
    if args.seed:
        seed = json.loads(pathlib.Path(args.seed).read_text(encoding="utf-8"))
        for view, payload in seed.items():
            bus_client.bus().cache_set(f"cache:{view}", payload)

    from nicegui import ui

    import shell
    from pages.options import theme

    @ui.page("/")
    def _index():
        for css in (shell.TABLE_CSS, shell.SUBTAB_CSS, shell.PANEL_SCROLL_CSS,
                    theme.SURFACE_CSS, theme.APP_FIELD_CSS, theme.TYPOGRAPHY_CSS):
            if css:
                ui.add_css(css)
        if theme.FONT_HEAD_HTML:
            ui.add_head_html(theme.FONT_HEAD_HTML)
        ui.colors(**theme.QUASAR_COLORS)
        with ui.column().classes("ns-app w-full p-4 gap-3 pb-10"):
            importlib.import_module(f"pages.{args.page}").render()

    ui.run(port=args.port, dark=True, reload=False, show=False, title="ui harness")


if __name__ == "__main__":
    main()
```

**Step 4: Run** `$PY -m pytest tools/tests/test_ui_harness.py -q` — Expected: PASS. Commit:

```bash
git add tools/ui_harness.py tools/tests/test_ui_harness.py
git commit -m "tools: ui_harness - render one page locally with the app CSS and a fake bus

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

**Step 5: Look at it.** Temporarily add to `.claude/launch.json` (it is tracked — **revert it afterwards**, do not commit it):

```json
    {
      "name": "ui-harness",
      "runtimeExecutable": "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe",
      "runtimeArgs": ["tools/ui_harness.py", "settings", "--port", "9591"],
      "port": 9591,
      "autoPort": false
    }
```

`preview_start {name: "ui-harness"}`, then check, with DOM reads (`javascript_tool`) where the screenshot tool struggles:
1. **Ground:** `getComputedStyle(document.body).backgroundImage` contains `radial-gradient` (not a flat `rgb(18, 18, 18)`).
2. **Appearance tab:** eight group cards; the preview column on the right. Type `18` into **Titles** and press Tab → the preview title's computed `font-size` is `18px`, and the footer says "1 unsaved change". Type `big` and press Tab → the field shows "A number of pixels, like 14 or 14px" under it and **Save changes** is disabled.
3. **Reset to shipped values** opens a dialog whose buttons read, left to right, `Cancel`, `Reset`, with Reset solid red. Press Esc — it closes.
4. Switch the harness page (edit `runtimeArgs`, restart the preview) to `options.calculator`: its fields still wear the `[calc]` near-black box — `getComputedStyle(document.querySelector('.calc-v3 .q-field__control')).backgroundColor` is `rgb(10, 18, 25)` (`#0a1219`), proving the app-wide fields do not override a page's own.
5. `options.expected_move` (a page with no wrapper today): its Symbol / Expiry fields are now boxed navy.
6. `sentiment_rotation`: unchanged look (it paints its own ground).

Take one screenshot of the Appearance tab (resize the window to 1600×1000 first if the screenshot times out). Then `preview_stop` and `git checkout -- .claude/launch.json`. Reverse any Appearance edit you saved while testing: delete `config/local/theme.toml` if the harness created it (`git status` must show it untracked-and-gone, never committed).

---

### Task 20: Documentation

**Files:** `CLAUDE.md`, `docs/CHANGELOG.md`, `docs/manuals/user-guide/user-guide.md`, `docs/manuals/reference-guide/reference-guide.md`, `webgui/pages/options/theme.py` (module docstring)

**Step 1: CLAUDE.md** — correct in place (the file's own rule 2: edit the sentence, never append a correction):
- In **App theme — dark-navy**, the `[menu]` sentence: replace "**NOT** the active nav pill / tab fills / icon accent (hardcoded rgba in `main._NAV_CSS` — see the JIT gotcha below)" with "and, since 2026-09-19, the active nav pill, tab-strip fill and active icon (`build_nav_css` re-emits them in the accent)". In the JIT gotcha, replace "so it does **not** follow the `accent` knob (nor do the tab-strip fills or the active icon accent). Changing `accent` moves the Quasar controls only; to move the nav accents, edit `main._NAV_CSS` as well." with "which stays the stock look; `build_nav_css` re-emits it in the accent as raw CSS, which the JIT limit does not bind."
- Replace "secondary+primary buttons, the **3D gradient buttons**," with "secondary, primary and danger buttons,".
- Replace the **Apply to a new page** snippet and the paragraph under it with:

```markdown
- **Apply to a new page — use the kit (2026-09-19): `webgui/pages/ui_kit.py`.**
  ```python
  from pages import ui_kit as kit
  with kit.page():
      head = kit.header("Title", view="options:matrix")   # title · Updated stamp · actions
      with head.actions:
          kit.button("Refresh", kind="secondary", icon="refresh", on_click=_refresh)
      with kit.control_bar():
          sym = kit.symbol_field(value="SPY", on_load=_load)
          kit.button("Load", kind="primary", icon="search", on_click=_load)
      region = kit.region("Loading…")
      with region.content:
          table = kit.table(columns, rows, numeric=("pnl",))
  ```
  Fields and tabs are boxed app-wide: both entrypoints put `ns-app` on the content
  column and inject `theme.APP_FIELD_CSS` + `theme.SURFACE_CSS`, so a page adds no
  scope class. `tests/test_ui_kit_guard.py` fails when a page builds a raw
  `ui.button` / `ui.dialog` / `ui.notify` / `ui.table` or loads its own font; its
  `ALLOWED` ratchet names the pages not yet migrated. The standard is
  [the design doc](docs/plans/2026-09-19-app-ui-consistency-design.md).
```

- In **Token vocabulary**, replace "`BTN_3D` / `BTN_3D_DANGER` 3D gradient buttons." with "`BTN_QUIET` text-only button · `BTN_3D` / `BTN_3D_DANGER` legacy aliases of `BTN_PRIMARY` / `BTN_DANGER`, removed once no page uses them."
- Near "`config/theme.toml` is the single source of truth for the **webgui styling palette**", replace "(surfaces/cards/text, buttons incl. the 3D gradients," with "(surfaces/cards/text, buttons,". In the same paragraph, where it says the page-scoped sections are "NOT surfaced in Settings → Appearance", leave it — still true.

**Step 2: docs/CHANGELOG.md** — add at the top, above the current **Last updated** entry, and turn that entry's heading into **Prior —**:

```markdown
**Last updated:** 2026-09-19 (**One look and one behaviour — Phase 0: the page kit.**)

- **`webgui/pages/ui_kit.py`**, the pieces every screen will be built from: the
  header line (title, an Updated stamp in CT from the view's `:ts` key, page
  actions), control bar, labelled fields, the one Symbol field, four button
  kinds with their own busy state, a region whose spinner survives repaints, the
  one table (sortable, numbers right, selected row drawn), empty state, the one
  confirm dialog (Cancel first, solid red only there) and toasts.
- **App-wide surface:** both entrypoints paint the navy ground, default cards and
  boxed fields (`.ns-app`, `theme.SURFACE_CSS` / `APP_FIELD_CSS`,
  `ui.colors(**theme.QUASAR_COLORS)`), so a page with no wrapper no longer sits on
  Quasar's grey. Table and subtab chrome follow the theme; table headers are
  sentence case.
- **The menu accent now reaches** the active nav pill, tab fill and icon.
- **`[buttons_3d]` retired** — seven of its eight colours drove nothing;
  `red_mid` is now `[palette].danger`.
- **Settings → Appearance** is its own tab: groups that match the standard, a
  live preview drawn from the same tokens, a staged Discard / Save footer and the
  shared restart banner.
- **Guard:** `tests/test_ui_kit_guard.py` (ratchet of pages still building raw
  controls). **Harness:** `tools/ui_harness.py`.
- Design: `docs/plans/2026-09-19-app-ui-consistency-design.md`; plan:
  `docs/plans/2026-09-19-app-ui-consistency-phase0-plan.md`.
```

**Step 3: Manuals.** In `docs/manuals/user-guide/user-guide.md`, replace the bullet

```
- **Appearance** — every colour, font and menu style, in six tabs. **Save &
  restart web GUI** applies the change; **Reset** (confirm-gated) returns to the
  shipped theme.
```
with
```
- **Appearance** is its own tab now (**Settings → Appearance**): every colour and
  font, grouped as surfaces, text, fields, buttons, status colours, charts, type
  and menu, with a live preview. **Save changes**, then **Restart now**, applies
  it to every screen; **Reset to shipped values** (confirm-gated) goes back.
```
In `docs/manuals/reference-guide/reference-guide.md`, replace the paragraph beginning `**Appearance.** Every colour, font and menu style, in six tabs` with

```
**Appearance tab.** Every colour and font in the app, grouped as surfaces, text,
fields, buttons, status colours, charts, type and menu, with a live preview that
shows an unsaved colour before anything else does. **Save changes** writes an
override in `config/local/theme.toml` (the shipped `config/theme.toml` is never
edited) and **Restart now** applies it to every screen; **Reset to shipped values**
(confirm-gated) removes the override.
```
and the table row `| **Settings → Appearance** | Surfaces · State colors · 3D buttons · Gauges · Charts · Text · Menu |` with `| **Settings** | **General** · **Appearance** (Surfaces · Text · Fields · Buttons · Status colours · Charts · Type · Menu) · **Configuration** |`.

Rebuild: `cd docs/manuals && $PY build_docs.py user-guide` then `$PY build_docs.py reference-guide`.

**Step 4: theme.py docstring** — in the module docstring, replace "``BTN_3D*`` (3D gradient buttons), ``TILE_3D`` (raised metric tiles)" with "``BTN_QUIET`` (text-only), ``BTN_3D*`` (legacy aliases), ``TILE_3D`` (metric tiles)", and add one sentence at the end: "Since 2026-09-19 pages build their controls through ``pages/ui_kit.py``, and the boxed-field rules also ship app-wide as ``APP_FIELD_CSS`` under ``.ns-app``."

**Step 5: Commit**

```bash
git add CLAUDE.md docs/CHANGELOG.md docs/manuals/user-guide docs/manuals/reference-guide webgui/pages/options/theme.py
git commit -m "docs: the page kit, the app-wide surface and the Appearance tab

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Done when

- `cd webgui && $PY -m pytest -q -p no:randomly -rfs` matches the baseline sets (plus the new tests).
- The harness shows the Appearance tab working as in Task 19, and the Calculator's own field look unchanged.
- Nothing is promoted by this plan. Promoting is the operator's call (15:25–16:15 CT, `ssh vps2 'cd /home/administrator/dev && tools/promote.sh'` after `main` is pushed). Phase 0 changes every page's ground and field boxes, so after a promote the operator clicks through the private pages; Claude can check the 14 public screens at `https://live.neuralstrike.co` directly.
