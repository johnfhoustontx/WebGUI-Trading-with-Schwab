# App UI consistency — Phases 3 & 4 (Trend & Sentiment, Desk/Symbol/Macro) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move the six Trend & Sentiment screens and the three entry-point screens (Desk, Symbol, Macro Board) onto the page kit. Each gets the app surface, the app font, one header line with an Updated stamp and page actions, one button vocabulary, one loading region and one toast vocabulary. Every chart, heat ramp, quadrant hue, regime colour and macro tile colour is untouched.

**Architecture:** Phase 0 built `pages/ui_kit.py`, the app-wide surface and the guard; Phase 1 migrated the nine Options boards and added the information dialog, the section title and the detail-panel action footer. This phase needs no new page module — it rewrites nine `render()` frames and retires the surface half of four page-scoped theme sections.

**Tech Stack:** NiceGUI 3.13, pytest.

**Prerequisite:** Phase 1 ([`2026-09-19-app-ui-consistency-phase1-plan.md`](2026-09-19-app-ui-consistency-phase1-plan.md)) is done, green and promoted. Read the design first: [`2026-09-19-app-ui-consistency-design.md`](2026-09-19-app-ui-consistency-design.md), in particular **Page-scoped palettes**, **Charts keep their data colours** and the **Trade-offs** section, which is where this phase's cost is written down: *"The August/September redesigns (console, heat grid, rotation board, Macro instrument, Calculator, Signal Desk) lose their own surfaces and fonts; their layouts and data colours stay."*

---

## What makes this phase different from Phase 1

Phase 1 migrated table boards that had no design of their own — the kit was strictly more consistent than what it replaced. **These nine screens have deliberate visual identities**, five of them built in the 2026-08-17 rebuild and the Macro Board's in its own redesign. So the work is not 36 guarded calls; it is **~305 page-scoped token usages** plus four theme sections, and the visible change is far larger than any phase so far.

Two consequences for how this plan is executed:

1. **RRG goes first** (Task 2), alone, and the harness check for it happens **before** the other five screens are touched. It is the smallest of the family (247 lines) and wears every part of the rotation vocabulary, so it is the honest preview of what the whole family will look like. If the operator dislikes the result, five screens have not yet been rewritten.
2. **A data colour is never touched.** The classification is written out once, below, and every task refers to it rather than re-deciding.

## The token classification (decided once, applied everywhere)

| Source | Keys | Verdict |
|---|---|---|
| `[rotation]` in `theme.toml` | `void`, `panel`, `font_url` | **GO** — the section empties and is deleted |
| `rotation_view.NEUTRAL` → `NT` / `NB` / `NE` | the warm-neutral ladder (text, body, rail, eyebrow, grid, btn_edge, btn_hover) | **GO** — surface/text roles; replace with app tokens |
| `rotation_view.TONE` | `down` / `up` risk-off / risk-on accents | **STAY** — data |
| `rotation_view.QUAD_HUE` / `QUAD_CHROMA` | the four quadrant hues | **STAY** — data |
| `[sectors]` | `void`, `edge`, `edge_hi`, `txt`, `dim`, `faint`, `font_url` | **GO** |
| `[sectors]` | `up`, `dn`, `warn` | **STAY** — regime word + dot colours |
| `sector_heat` heat ramp | the oklch green/red cell map | **STAY** — data (it is not in the TOML) |
| `[console]` | background / text / button / font keys | **GO** |
| `[console]` | regime colours | **STAY** |
| `[macro]` | background / text / button / font keys | **GO** |
| `[macro]` | risk-on / risk-off tile colours | **STAY** |
| `CONSOLE_KEYFRAMES_CSS`, `DESK_NEON_CSS` | animation | **STAY** — data effects, not chrome |

⚠ `desk.py`'s third `ui.add_head_html` is a `<script>` (the voice unlock JS), **not** a font link. It stays, and earns a written reason in the guard's `ALLOWED`, exactly like `options/detail.py`'s collapse toggle.

## Conventions

Same as Phase 1. The worktree root is `D:\WebGUI Trading with Schwab\.claude\worktrees\market-summary-social-images-2333b5`. `$PY` is `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"`, and tests run with `cd webgui && $PY -m pytest …`. Commit with explicit paths, never `git add -A`, never `--amend`, and end every commit message with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. The baseline is Phase 1's final full-suite run: **webgui 5217 passed, 1 skipped** (`tests/test_auth_store.py:39`, POSIX mode bits). Compare the failing and skipped **sets**, never the counts.

**The rules every page task applies** (so each task below shows only what is page-specific):

1. **Frame.** Wrap the body in `with kit.page():`. Delete the bespoke wrapper column's surface classes (its `*_VOID_BG`, `*_SANS`, rounding and page padding) and the `*_FONT_HEAD_HTML` call. Keep any `ui.add_css` that carries an ANIMATION; delete any that carries surface or chrome.
2. **Header line.** `head = kit.header("<Page name>", view=<primary view>, stale=<see the task>)`. The bespoke headline label and its eyebrow go. Page actions go inside `with head.actions:`, danger first and primary last.
3. **Public-origin gating stays exactly as it is.** Every `if _may_enqueue:` wrapper around a Refresh is a Schwab-budget control, not styling — keep the condition, change only the widget it builds. `kit.header` already omits the title on the public origin.
4. **Status line.** The verdict/summary text becomes `kit.status_line()` directly under the header. Any clock time leaves it — the header stamp owns that.
5. **Loading.** One `kit.region(...)` around what a repaint replaces, with `region.busy.show(<message>)` on request and `.hide()` in the repaint. This is also the fix for the mounted-on-a-cleared-container spinner bug the design predicts on five of these pages — **confirm the bug in the harness before claiming it in a commit message**.
6. **Buttons.** `kit.button(text, kind=…, icon=…)`. `secondary` for Refresh and page state, `primary` for the one main action if the page has one, `quiet` for a link-like control.
7. **Toasts.** `kit.toast(kind, text)` with kind in `info`, `ok`, `warn`, `error`. Drop any toast that only repeats what a spinner already says.
8. **Tokens.** Replace surface/text tokens per the table above with `theme` app tokens (`TITLE`, `LABEL`, `MUTED`, `EYEBROW`, `CARD`, `TXT_*`). Never touch a data colour.
9. **Tests.** When an assertion pins behaviour this plan deliberately changes (a class string, a headline, a status clock), change it to the new behaviour and say so in the commit. Never delete one to reach green, and never weaken a guard.

---
**The tiebreaker for anything the table does not name:** a colour that encodes a VALUE stays (a quadrant hue, a heat cell, a regime word, a tone dot, a bar fill). A colour that draws a FRAME stays only if nothing in the app vocabulary covers it — axis ticks, gridlines, crosshairs, grooves and tracks are chart furniture, not data, and take `theme` border/muted tokens. Named cases settled in Task 1: `NE['grid']` and `NB['hair']` (gridlines) take the card border, `NB['btn_hover_edge']` (the crosshair) takes the one-step-brighter button border because it marks the plot's single fixed reference at RS 100/100.

**`theme.CARD` brings its own padding, and that is safe here.** Task 1 replaced `RT_PANEL_BG` with `CARD` on the RRG plot: every child of that box is absolutely positioned, and an absolute child resolves against the **padding** box, so the percentage geometry is preserved. It does round the corners to the app's card radius — a real but intentional-looking change. Expect the same wherever a `*_PANEL_BG` becomes `CARD`, and check the geometry claim holds for that page's children before assuming it.

**Sequencing note — the ladder dies last.** `rotation_view`'s `NT`/`NB`/`NE` are imported by RRG, Rotation, Bull/Bear and Momentum. Each task stops *using* them; the ladder itself is deleted in Task 5, the last of the four. ⚠ `rotation_view.TONE["flat"]` is BUILT from that ladder, so Task 5 must give `TONE["flat"]` its own neutral rather than deleting the ladder out from under it.

---

### Task 1: RRG — the family preview

**Files:** Modify `webgui/pages/sentiment_rrg.py`, `webgui/page_help.py`; Test `webgui/tests/test_sentiment_rotation.py`, `webgui/tests/test_ui_kit_guard.py`

This is the smallest screen of the rotation family and wears every part of its vocabulary, so it is the honest preview of Phase 3. **Stop after Step 5 and run the harness check in Task 12's step 1 before starting Task 2.**

**Step 1: Failing tests.** Append to `test_sentiment_rotation.py`:

```python
def _rrg_render(monkeypatch=None):
    """Render the RRG page in a slot context, as test_render_graceful_empty does."""
    from nicegui import ui
    from pages import sentiment_rrg
    with ui.card():
        sentiment_rrg.render()
    return list(ui.context.client.elements.values())


def test_the_scrim_survives_the_repaint_that_used_to_delete_it():
    """The bug this migration fixes. ``build_busy`` mounted the scrim INSIDE
    ``plot``, and ``_paint_plot`` opens with ``plot.clear()`` - so the first
    ``_apply()`` on build deleted it, and every later Refresh raised a scrim
    that no longer existed. ``kit.region`` keeps the spinner on ``outer`` and
    clears only ``content``, so it is still here after the build repaint."""
    from nicegui import ui
    els = _rrg_render()
    assert any(isinstance(e, ui.spinner) for e in els), \
        "the region's spinner was deleted by the build-time repaint"


def test_the_rrg_frame_is_the_kit_and_carries_no_surface_of_its_own():
    import inspect
    from pages import sentiment_rrg
    src = inspect.getsource(sentiment_rrg.render)
    assert "kit.page()" in src
    assert 'kit.header("RRG", view=VIEW, stale=False)' in src
    # The page-scoped ground, face and ladder are gone from the frame.
    for token in ("RT_VOID_BG", "RT_SANS", "ROTATION_FONT_HEAD_HTML"):
        assert token not in src, f"{token} is a page-scoped surface value"


def test_the_refresh_toast_is_gone_because_the_spinner_says_it():
    import inspect
    from pages import sentiment_rrg
    src = inspect.getsource(sentiment_rrg.render)
    assert "ui.notify" not in src and "Refreshing — the page updates" not in src


def test_the_quadrant_hues_and_the_tone_dots_are_untouched():
    """Charts keep their data colours: this is the half that must NOT change."""
    import inspect
    from pages import sentiment_rrg
    src = inspect.getsource(sentiment_rrg.render)
    for name in ("QUAD_WASH", "QUAD_CORNER_TXT", "STRIP_TINT", "TONE"):
        assert name in src, f"{name} is a data colour and must survive"
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_sentiment_rotation.py -q` — Expected: FAIL (the first on a missing spinner, the rest on the old frame).

**Step 3: Implement** in `sentiment_rrg.py`:

- Delete `ui.add_head_html(ROTATION_FONT_HEAD_HTML)` (line 56) and its import. Delete the dead `state = {"ver": None}` (line 54) — `watch_view` owns the version.
- Replace the `wrap` column (58-60) and the header row (64-81) with:

```python
    with kit.page():
        head = kit.header("RRG", view=VIEW, stale=False)
        if _may_enqueue:                      # a Refresh is 11 sector chains
            with head.actions:
                kit.button("Refresh", kind="secondary", icon="refresh",
                           on_click=lambda: _request_refresh())
        eyebrow_lbl = kit.status_line()
```
  The headline *"Where every sector sits"* goes; if `page_help.HELP_MD["/sentiment/rrg"]` does not already say it, add the sentence there. The eyebrow keeps its runtime text (`"Relative Rotation Graph vs SPY · as of …"`) — it carries a DATE, not a clock, so rule 4 leaves it alone.
- The plot: keep the geometry exactly. Swap `_T['RT_PANEL_BG']` → `theme.CARD`, `NE['grid']` → the app card border, `NT['caption']`/`NT['rail']`/`NT['body']`/`NT['ghost']` → `theme.MUTED` / `theme.LABEL` per role, and `NB['btn_hover_edge']` (the crosshair, 163/165) → the app border token. **`R.QUAD_WASH`, `R.QUAD_CORNER_TXT`, `R.STRIP_TINT`, the `R.plot_points` classes and every `TONE[...]` stay byte-identical.**
- Wrap the plot in the region and delete the old scrim:

```python
        plot_region = kit.region("Refreshing rotation…")
        with plot_region.content:
            plot = ui.element("div").classes(...)     # the same geometry classes
```
  Delete `rrg_busy = _busy.build_busy(plot, …)` (121) and the `_busy` import. `_paint_plot` keeps `plot.clear()` — `plot` now lives inside `region.content`, which is exactly the container a repaint is allowed to clear. Replace `rrg_busy.hide()` (227) with `plot_region.busy.hide()` and `rrg_busy.show()` (242) with `plot_region.busy.show()`.
- Delete the `ui.notify` (240). `_request_refresh` keeps `if not _may_enqueue: return` as its **first statement** — `test_live_commands.py:100-112` reads for exactly that shape.

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_sentiment_rotation.py tests/test_live_commands.py tests/test_no_inline_style.py tests/test_ui_kit_guard.py -q`. Delete the `sentiment_rrg.py` entry from the guard's `ALLOWED` in the same commit. Expected: PASS.

**Step 5: Commit**:

```bash
git commit -m "feat(rrg): the RRG on the page kit; the refresh spinner survives

The plot keeps its quadrant hues, washes and tone dots; the page loses its own
ground, face and neutral ladder. The wait scrim moves off the container
_paint_plot clears, so Refresh shows a spinner for the first time.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---
### Task 2: Sector Rotation

**Files:** Modify `webgui/pages/sentiment_rotation.py`, `webgui/page_help.py`; Test `webgui/tests/test_sentiment_rotation.py`, `webgui/tests/test_ui_kit_guard.py`

Task 1's shape, one screen over. Page-specific:

- The view is **inline** at two sites (`bus_client.read("sentiment:rotation")` 362, `watch_view("sentiment:rotation", …)` 381). Introduce `VIEW = "sentiment:rotation"` and use it in both, so the header, the read and the watch cannot drift.
- Title `"Sector Rotation"`, `stale=False` (same one-shot view as RRG — `handlers.refresh_rotation` runs once at service start and otherwise only on command; `scheduler.py:216-220` says so in its own comment).
- Headline *"Sector Rotation"* (89-91) and eyebrow (88) → `kit.header` + `kit.status_line`; `V.eyebrow(...)` keeps its date text.
- Drop the dead `state = {"ver": None}` (76).
- **Region:** the spinner is on `quad_box` (185), which `_paint_quadrants` clears (261). Wrap the whole board body — `gauge_box`, `axis_box`, `band_box`, `foot_box` and `quad_box` are all replaced by a refresh — in ONE `kit.region("Refreshing rotation…")`, and let every existing `.clear()` keep working inside `region.content`.
- **Chart furniture to app tokens:** `NB['track']` (the gauge groove, 203) and `NB['hair']` (the chip bar trough, 322, reached through `V.rgba('hair')` in `_PANEL` 42). **Data stays:** every `V.TONE[...]`, `_DOT_CLASSES` (50) and every `V.quad_classes(...)` key.
- Tests: add the same four shapes as Task 1 (scrim survives, kit frame, no toast, data colours intact), renaming the assertions for this page. `test_render_graceful_empty` (38) must keep passing untouched.
  ⚠ Two things Task 1 established about those four. **Only THREE go red on the old code** — the data-colours one is a must-not-change guard that passes before and after, so a green result there is correct, not a broken test. And Task 1's `_rrg_render` helper carries an unused `monkeypatch=None` parameter; **drop it when you copy the helper**.
- `page_help` is conditional on every task from here: Task 1 needed no edit, because `HELP_MD["/sentiment/rrg"]` already opened with the headline sentence. Check before editing, and take `page_help.py` off the task's Files list if it turns out unchanged.

**Commit:** `feat(rotation): Sector Rotation on the page kit; the board's spinner survives`

---

### Task 3: Sector & Industry

**Files:** Modify `webgui/pages/sentiment_sectors.py`, `webgui/pages/sector_heat.py`, `webgui/page_help.py`; Test `webgui/tests/test_sector_heat.py`, `webgui/tests/test_ui_kit_guard.py`

- Title `"Sector & Industry"`, **`stale=False`**. ⚠ It is published hourly (`scheduler.sectors_due`, `SECTORS_MINUTE = 38`), and `alerts.STALE_OVERRIDES` has **no entry** for `sentiment:sectors` — so the 600 s default would paint it amber for ~50 minutes of every hour. `stale=True` is only correct here **after** adding a `STALE_OVERRIDES["sentiment:sectors"]` of at least ~70 min, which is out of this phase's scope. Leave it off and say so in the commit.
- **Three buttons.** Refresh stays inside `if _may_enqueue:` and moves to `head.actions` as `kind="secondary"`. **Expand all** and **Collapse** are ungated page state and belong in a `kit.control_bar()` under the header, not in the header actions — the header carries page-level actions, and these two operate on the grid.
- **The eyebrow carries a CLOCK, and it is in the WRONG ZONE.** `sector_heat.eyebrow` (204-214) renders `"MARKET STRUCTURE · AUG 17, 2026 · 16:00 ET"` — Eastern, while every other stamp in the app is Central (`ui_kit.CT`). Rule 4 takes the clock out: `eyebrow` keeps the date and drops the ` · HH:MM ET` clause, and the header stamp carries the time in CT.
  ⚠ This deliberately changes two passing tests in `test_sector_heat.py` — `test_eyebrow_renders_the_stamp_in_eastern_time` (249) and `test_eyebrow_without_a_stamp_says_so_rather_than_inventing_a_time` (255). Rewrite the first to assert the date **and the absence of a time**, keep the second. **Name both changes in the commit message.**
- The regime dot + `regime_lbl` + `regime_detail` (119-126) and `summary_lbl` (132-134) become the `kit.status_line()` row.
- **Region:** the spinner is on `grid_box` (143), cleared by `_render_rows` (165) — and `_render_rows` is also the repaint for `_toggle`, `_sort_by`, `_expand_all` and `_collapse_all`, so the scrim has been dying on every interaction, not only on build. One `kit.region("Refreshing sectors…")` around the grid.
- **The heat ramp is not in the TOML and is not touched** (`sector_heat.HEAT_BG` / `HEAT_TXT` / `heat_classes`). `SC_UP`, `SC_DN`, `SC_WARN` and their `_BG` partners stay (the regime tone and the P/C amber). `SC_SANS`, `SC_MONO`, `SC_VOID_BG`, `SC_TXT`, `SC_DIM`, `SC_FAINT`, `SC_EDGE`, `SC_EDGE_HI` go.
- ⚠ While you are here: `_TONE_TXT` (57-58) is **dead on the add path** — lines 311-312 `remove=` its values but `add=` `SC_TXT` / `SC_FAINT`. Either wire it or delete it; do not leave it half-live.

**Commit:** `feat(sectors): Sector & Industry on the page kit; the stamp moves to the header in CT`

---

### Task 4: Bull / Bear Map

**Files:** Modify `webgui/pages/sentiment_bullbear.py`, `webgui/page_help.py`; Test `webgui/tests/test_sentiment_bullbear.py`, `webgui/tests/test_ui_kit_guard.py`

The one page of the four whose spinner is **already right** — its own comment (261-263) explains the `scroll_box` / `rows_box` split that `kit.region` formalises. Converting it must preserve that split, not undo it.

- Title `"Bull / Bear Map"`, **`stale=True`** — the only one of the four that qualifies (`_bullbear_publish_loop`, 30 s in session and 5 min closed).
- Eyebrow literal `"BULL / BEAR MAP"` (236) and headline *"Where the market is strong and weak"* (238-240) become the kit header; the sentence moves to `page_help` if it is not already there.
- **TWO clocks, and BOTH stay in the body.** `scores_lbl` ("Scores as of <date>") and `quotes_lbl` ("Quotes HH:MM:SS") are the provenance of two different feeds, not the page's own freshness — and `quotes_lbl` recolours to `TONE["down"]["txt"]` when the quote call failed, a real state the header stamp cannot express. They become the `kit.status_line()` row. `clocks()` (122-134) is unchanged.
- **`ui.add_css(_BULLBEAR_CSS)` STAYS.** It is Quasar expansion DOM (`.q-item`, `.nicegui-expansion-content`) with no colour, font or background in it — the documented escape hatch, not a palette block. Keep the `bullbear` scope class on the kit page wrapper.
- **Toasts:** the `"Refreshing — …"` (466) goes; `NOTHING_CHANGED` (482) is a real outcome and becomes `kit.toast("info", NOTHING_CHANGED)`.
- **`P.WAITING` must NOT become `copy.WAITING_SENTIMENT`** — `test_desk.py:3718` asserts the Desk and the map say the same thing about a cold map, and that `P.WAITING` names 16:20 CT.
- **Four test repairs, each deliberate:**
  1. `test_the_page_imports_nothing_below_tier_one` (111) — add `"pages.ui_kit"` to the allowed set. The assertion's intent survives: `ui_kit` is itself Tier 1.
  2. `_scrim` (313-316) takes the **first** spinner; a kit region plus a busy button gives more than one. Select the region's spinner explicitly.
  3. `_click_refresh` (319-323) takes the **first** button; the header now holds it. Select by `.text == "Refresh"`.
  4. `test_the_quotes_line_turns_warning_when_the_call_failed_and_calm_again_after` (569) asserts `NT["ghost"]` — a ladder token that is going. Re-point it at the app token that replaces it.
- `_timer()` (352-355) picks the timer with `interval == 2.0`; `kit.header` adds one at 5.0, so it still resolves — but confirm it, because the header timer is new on this page.

**Commit:** `feat(bullbear): Bull / Bear Map on the page kit`

---

### Task 5: Momentum, and the neutral ladder retires

**Files:** Modify `webgui/pages/sentiment_momentum.py`, `webgui/pages/rotation_view.py`, `webgui/pages/options/theme.py`, `config/theme.toml`, `webgui/page_help.py`; Test `webgui/tests/test_sentiment_momentum.py`, `webgui/tests/test_rotation_view.py`, `webgui/tests/test_theme.py`, `webgui/tests/test_ui_kit_guard.py`

The last of the rotation family, so it also retires the shared vocabulary.

**The page:**
- Title `"Momentum"`, **`stale=False`** — `[slots.momentum] at = "16:20"` recomputes it once a night, and `alerts.stale_after` would call a once-a-day view stale every single day.
- `render(level="industry")`'s signature is **load-bearing** — `live_screens.py:59-60` pins `kwargs={"level": "industry"}` and `test_gallery_routes.py:59` drives `?level=`. Do not touch it.
- Level `ui.select` (257-261) becomes `kit.select_field("Level", LEVEL_OPTIONS, …)` in a `kit.control_bar()`. It stays drawn on the public origin (the comment at 262-263 says why).
- Refresh (265-271) moves to `head.actions`.
- **"Top ranked"** (582-585) becomes `kit.button("Top ranked", kind="quiet")`.
- **The per-member name chips stay `ui.button`, with a written reason.** `_name_chip` (498-512) is one call site that produces on the order of the whole level's universe per repaint, and it is a *selectable name chip* — 10.5px, ring-on-select, `max-w-full` — not a page action. Forcing it through `kit.button` would put a full-size action button inside a quadrant panel hundreds of times. Restyle it with app tokens (app face, `theme.MUTED` text, app border, the accent for the selected ring) and record it in the guard's `ALLOWED` as `{"button": 1}` with the reason, exactly like `options/detail.py`. Do **not** add a `kit.chip` for one caller; if a second page ever needs one, that is when it earns a kit piece.
- **Table** (687-692): `kit.table(kit.table_columns(cols), rows=data, row_key="symbol")`. ⚠ This makes the leaderboard **sortable** for the first time (`table_columns` defaults `sortable: True`); the existing `align` values are preserved by `c.get("align", "left")`, so pass `numeric=()`. The row click keeps `_select`. Drop `momentum-table`, `momentum-board`, `momentum-level` and `momentum-more` — **no CSS rule in the repo defines any of them**.
- **Region:** the spinner is on `quad_box` (390), cleared by `_paint_quadrants` (515) — one `kit.region("Recomputing momentum…")`.
- The toast (763) goes.

**The shared vocabulary:**
- Delete `NEUTRAL` / `NT` / `NB` / `NE` from `rotation_view.py` (102-140) once no page imports them. ⚠ **`TONE["flat"]` (165-168) is built out of that ladder** — give it its own neutral first, or it dies with the thing it borrowed from.
- Delete `ROTATION_TOKENS` and `build_rotation_font_head_html` from `theme.py`, and the `[rotation]` section from `config/theme.toml` — `void`, `panel` and `font_url` are its only keys, and all three are surface.
- Keep `QUAD_HUE`, `QUAD_CHROMA`, `TONE`, `QUAD_CLASSES`, `REGIME_CLASSES` and every `momentum_view` data map.

**Commit:** `feat(momentum): Momentum on the page kit; the rotation neutral ladder retires`

---
### Task 6: Market Regime Console

**Files:** Modify `webgui/pages/sentiment.py`, `webgui/pages/console_page.py`, `webgui/page_help.py`; Test `webgui/tests/test_sentiment.py`, `webgui/tests/test_theme_console.py`, `webgui/tests/test_ui_kit_guard.py`

⚠ **The header is not in `sentiment.py`.** `console_page.render()` builds it (`_header`, 151-170) and `sentiment.py` only calls it (783). Both files change.

- Title `"Sentiment"`, **`stale=True`** — `sentiment:composite` refreshes every 120 s in session (`scheduler.REFRESH_INTERVAL_SEC = 120`) and is not in `alerts.RTH_ONLY_VIEWS`, so 600 s / 2700 s are honest thresholds.
- `console_page._header` goes: the `"MARKET REGIME CONSOLE"` title (161), the `"SENTIMENT · TREND · SIGNALS · REGIME SHARE"` eyebrow (165), the pulsing dot (158-160) and the `SESSION` / `DATA AS OF` chips (167-169). The kit header carries the name and the stamp. `session_label()` (38-51) still says something the stamp cannot — RTH vs EXT vs CLOSED — so it moves into `kit.status_line()`. `as_of_parts()` (54-67) is **superseded** by the kit stamp and its `STALE_AFTER_SEC = 420` with it; delete both rather than leaving a second freshness rule in the tree.
- `SHELL` (31) loses `CONSOLE_PAGE`; the page frame is `kit.page()`.
- **The three buttons.** Refresh (771-773, gated) → `head.actions`. **Components** (800) and **Trend Detail** (807) are press-and-hold `ui.menu` triggers, not actions — but they *are* labelled buttons with a hold gesture, so they go through `kit.button(kind="quiet")` and keep their `mousedown` / `mouseup` / `mouseleave` wiring; `kit.button` returns the element, so `with btn:` still mounts the menu. ⚠ `test_sentiment.py::_trend_detail_texts` (288-299) requires **exactly one** button whose `.text == "Trend Detail"` — keep the label verbatim.
- **Region:** `console_busy = _busy.build_busy(console_root, …)` (785) mounts inside the very container `console_page.apply` clears (`container.clear()`, console_page.py:110). One `kit.region("Refreshing sentiment…")`, with `console_root` built inside `region.content`.
- **Status line.** `_render_status` (991-1004) keeps `"Next ~HH:MM"`, `"Sectors …"` and `"Proxy: …"`, and **drops `f"Updated {_fmt_time(ca)}"` (995)** — the header stamp owns that, and `_fmt_time` (685-695) renders naive machine-local time with no zone label, which the app standard does not allow. Delete `_fmt_time` / `_parse_iso` if nothing else uses them.
- The toast (987) goes.
- **`CONSOLE_KEYFRAMES_CSS` (717) goes with the dot it animates.** ⚠ `desk.py:3003` injects the same block — do not delete the constant here; Task 9 decides its fate on the Desk, and Task 10 removes it from `theme.py` only if neither page wears `con-pulse`. `test_theme_console.py:135` pins its contents and moves with it.
- **Data stays:** `CON_POS` / `CON_NEG` / `CON_WARN`, `console_colors()["regimes"]`, and the page's own `CLR_GREEN` / `CLR_RED` / `CLR_YELLOW` map (46-60) feeding `traffic_color`, `sc_text_class` and the Highcharts `zones`.

**Commit:** `feat(sentiment): the Market Regime Console on the page kit`

---

### Task 7: Macro Board

**Files:** Modify `webgui/pages/market.py`, `webgui/pages/options/theme.py`, `webgui/page_help.py`; Test `webgui/tests/test_market.py`, `webgui/tests/test_ui_kit_guard.py`

- Title `"Macro Board"`, **`stale=True`** — `market:dashboard` is the scheduled view `status.py:78` already marks `True`, and it is the page's only view.
- **Two honesty fixes the header makes for free.** The static `"STREAMING"` claim (336-338) and its pulsing dot go — the design lists that dot as a bug, because the page cannot back the claim. The `SESSION` clock (343, `_tick_clock` 522-524) renders **naive machine-local `%H:%M:%S`**, the only clock in the app that is neither CT nor a data stamp; it goes too, with its 1 s timer. The kit stamp says the true thing instead.
- **The skin toggle stays two raw `ui.button`s, with a written reason.** `INSTRUMENT` / `HEAT LATTICE` (368-373) are a **segmented picker** — mutually exclusive by construction (`_paint_seg` 379-384), applied instantly with no Go, persisted through `app_settings.set("macro_skin", s)` (390). The guard's own docstring names "a segmented picker" as the legitimate exception, and `kit.button`'s four kinds have no selected state, so expressing selection through the kit would mean a page-side class swap over `button_classes(...)` — the exact drift the kit exists to stop. Restyle both with app tokens and record `market.py: {"button": 2}` in `ALLOWED` with the reason. **Do not build a `kit.segmented` for one caller.**
  ⚠ `test_market.py:265` asserts the literal `'app_settings.set("macro_skin"'` in `render`'s source — keep that call spelled exactly as it is.
- **`MACRO_CSS` is SPLIT, not deleted, and this is the trap in this task.** Every rule is scoped under `.macro-board` / `.macro-a|b`, and those wrapper classes (298-299) are what the data effects hang off. **Dropping the wrapper classes as "chrome" kills every data effect with them.** Keep the wrapper classes on the `kit.page()` column. Then:
  - **Goes (surface/chrome):** `.macro-board` radial-gradient ground (theme.py 1008-1012), `.mb-rail` notch + gradient (1014-1017), `.mb-panel` notch and `::before` accent bar (1019-1026), `.mb-tile` notch (1028-1031), the Skin-B panel/tile chrome overrides (1042-1044, 1047), `.mb-shear` (1053), `.mb-dot` pulse (1054-1055, with the STREAMING dot).
  - **Stays (data effect / animation):** `.mb-ig` + `@keyframes mbig` (1033-1036), the price flare `.mb-tile.fl .mb-px` + `@keyframes mbpx` (1037-1038), the Skin-A magnitude wash (1040), the Skin-B heat fill (1044) + `@keyframes mblat` (1045-1046), and the Skin-B legibility ramp (1050-1051).
  - **Stays (accessibility):** the `prefers-reduced-motion` block (1056).
- `MACRO_FONT_HEAD_HTML` (291) goes. `MB_MONO` / `MB_TITLE` / `MB_SYM` / `MB_TXT` / `MB_DIM` / `MB_EDGE` go; the tile polarity colours stay.
- Empty state (542-544) → `kit.empty(_copy.WAITING_MARKET)`.
- ⚠ **Two tests measure colour against the tile background.** `test_lattice_text_ramp_is_legible_on_the_hottest_tile` (140) asserts ≥ 4.5:1 contrast and a `price > sym > desc` reading order; `test_lattice_ramp_is_scoped_to_skin_b_and_hooks_the_real_classes` (154) pins exact rule strings. The tile background is a **data** colour and does not change, so both should survive — **if either goes red, the split took a data rule by mistake.** Treat that as the signal, not as a test to update.

**Commit:** `feat(macro): the Macro Board on the page kit; the STREAMING dot and the local clock go`

---

### Task 8: Symbol Dossier

**Files:** Modify `webgui/pages/symbol.py`, `webgui/page_help.py`; Test `webgui/tests/test_symbol_page.py`, `webgui/tests/test_ui_kit_guard.py`

- Title `"Symbol"`, **`view="options:matrix", stale=True`**. ⚠ **Not `own_view`.** A dossier is written once and never republished (comment 1253-1256), and a SCANNED symbol has no dossier at all — so a stamp on `options:dossier:<SYM>` would read `Waiting for data` on exactly the page's best-covered symbols. The per-symbol chip (`SCANNED HH:MM` / `FETCHED HH:MM`) stays: it answers a different question.
- The ticker label (786-788) is the page's real subject and stays where it is, in the header card row, beside spot and day change. `msg_lbl` (808) becomes `kit.status_line()`.
- **The ticker input gets a label.** Today it is placeholder-only `"Ticker"` (783-784), which the standard's field rule forbids. `kit.symbol_field(value=sym or raw, on_load=_open_typed)` gives it the label, the uppercase, the select-all and the tab-out.
  ⚠ **Behaviour change to state in the commit:** `kit.symbol_field` passes `enter_always=True`, while `bind_symbol_load(inp, _open_typed)` today defaults to `False`. Because `_open_typed` **navigates** rather than loading in place, Enter on an unchanged ticker will now re-navigate to the same URL instead of doing nothing. That is the standard's intent (Enter is the reader pressing Load) and it is cheap here — the page rebuilds from cache — but it is a change, not a port.
- **Find trades** (799-802) and **Refresh** (804-807) → `kit.button`, keeping the `no-wrap` row that holds them together. ⚠ `test_symbol_page.py::test_find_trades_and_refresh_are_one_unit_in_the_header` (1477) pins both the literal `"Refresh"` and `"no-wrap" in parent.classes` — keep the row and the labels.
- `finder_btn` keeps `set_visibility(_finder_ok())` (885) and its `can_navigate(FINDER_ROUTE)` gate. `refresh_btn` keeps `_enqueue_fetch`'s `may_enqueue` gate (1134).
- **`kit.set_busy` is additive here, and releasing it is the fiddly part.** Refresh already has three overlapping busy mechanisms: the `state["refreshing"]` re-entrancy claim (1185-1187, the documented double-tap fix), the manual `set_enabled` pair (1188/1201), and the **full-screen overlay** (1146) with its own 30 s `LOAD_TIMEOUT_SEC` backstop. The overlay **stays** — the design keeps it on the three pages where loading a new symbol invalidates every control, and Symbol is one. `kit.set_busy(refresh_btn)` replaces only the `set_enabled` pair, and because the answer arrives over the bus in `_paint` (1097-1100) rather than in the click coroutine, **release it in `_paint` and `_fetch_timeout` as well as in `_on_refresh`'s `finally`**, or the button un-spins while the fetch is still pending. Keep the page's own `set_enabled(sym is not None)` rule after the release.
- `CONSOLE_FONT_HEAD_HTML` (776) goes; `CONSOLE_PAGE` / `CONSOLE_CARD` / `CONSOLE_DISPLAY` / `CON_*` go. Band titles (`"STRUCTURE"`, `"VOLATILITY"`, `"CONTEXT"`, `"TODAY'S SIGNALS"`, `"FLOW ALERTS"`, `"YOUR POSITION"`) → `kit.section_title`, sentence-cased. `_EMPTY` (731) → `kit.empty`.
- **Data stays:** `CHIP_TONES` (244-245), `_desk.signed_class`, `_desk.flip_side_class`, `_bb.quadrant_class`, `r["score_class"]`, `_scanner.STALE_ROW_CLASS` (1034), `_svg.gradient_bar_svg` (966).
- The guard entry for `symbol.py` is **deleted** at zero.

**Commit:** `feat(symbol): the Symbol Dossier on the page kit`

---

### Task 9: The Desk

**Files:** Modify `webgui/pages/desk.py`, `webgui/page_help.py`; Test `webgui/tests/test_desk.py`, `webgui/tests/test_live_main.py`, `webgui/tests/test_ui_kit_guard.py`

The landing page and the largest file in the app — but the smallest control surface of the nine: one button, no toast, no dialog, no table, no spinner.

- Title `"Desk"`, **`view="options:matrix", stale=True`**. It is the widest-reach view on the page (it feeds two of the four panels), it is scheduled round the clock, and it is not in `alerts.RTH_ONLY_VIEWS`. ⚠ Deliberately **not** `options:gex_status`: the strip already prints that view's age as `Live · 41s ago` (3308-3312), and a header stamp on it would say the same thing twice. Deliberately **not** `market:summary`: it publishes a handful of times a day and already carries `sum_asof` (3369).
- ⚠ **`fresh_lbl` / `fresh_dot` stay.** The same `fresh["stale"]` flag **gates the dealer walls** (`dealer_rows(matrix, fresh["stale"])`, 3459-3474), so the computation is load-bearing even where the label is not.
- **The voice-unlock button is a prompt, not a page action.** It is built hidden and revealed only when the browser blocks autoplay (`_voice_blocked`, 3882-3891). Put it through `kit.notice(...)` with a `kit.button` inside, `self-start` above the strip — closer to the standard than a header action, which would imply it is always available.
  ⚠ Sentence-casing `VOICE_UNLOCK_LABEL` to `"Enable spoken alerts"` breaks `test_desk.py::test_render_hides_the_unlock_prompt_until_the_browser_complains` (3557), which asserts the literal. Update it. `test_live_main.py`'s two tests (638, 651) read the **constant**, so they survive the rename.
- Its gate is `app_settings.load()["voice_enabled"]` (3053) plus the handler's own re-read (3909-3911) — **not** `may_enqueue`, because the Desk enqueues nothing. Keep both halves.
- `CONSOLE_PAGE` + `DESK_FONT` (3027-3028) go; `CONSOLE_FONT_HEAD_HTML` (2992) and `DESK_FONT_HEAD_HTML` (2995) go with them. ⚠ **The `<script>` at 3008 stays** — it is `DESK_VOICE_JS`, not a font. The guard entry becomes `desk.py: {"add_head_html": 1}` with that reason written next to it.
- **`ui.add_css(CONSOLE_KEYFRAMES_CSS)` (3003) goes**: `con-pulse` appears nowhere in the Desk's own markup — it was injected alongside the console vocabulary. **Verify with a grep before deleting**, and if some element does wear it, keep the injection and say so.
- **`DESK_NEON_CSS` (3004) STAYS, untouched.** It encodes arrival — a new row, a flagged row — in the page's data hues. Every trap in `CLAUDE.md:928-944` and the code comment at 2142-2160 still applies: the whole-second negative `animation-delay` steps (`desk-neon-0…9`, never a computed delay), the `animation-name` / `-duration` **longhands** (the shorthand would reset `animation-delay: 0s` and the tie between two one-class selectors is decided by source order), and **no `animation-fill-mode: forwards`** (animation declarations outrank normal author declarations, so `forwards` would beat the row's `hover:` for the rest of the session). `test_desk.py` 2928-2996 pins all of it — **if any of those go red, the migration broke the effect, and the tests are right.**
- `PANEL_HEADS` (2858-2872) → `kit.section_title`, sentence-cased. `_PLACEHOLDER` (2528) → `kit.empty`. ⚠ `kit.EMPTY` **centres** its text and `_PLACEHOLDER` does not — a real visual change on four panels; accept it (it is the app's one empty style) and note it.
- The eight waiting/empty constants keep their exact text. `test_desk.py` pins `WAITING_OPTIONS` appearing **four** times (1979), `WAITING_BULLBEAR` differing from it (2124), and `EMPTY_POSITIONS` (2075).
- ⚠ `test_desk.py::test_every_panel_body_is_the_scroll_container_never_its_card` (2662) pins that the `CONSOLE_CARD` line in `_panel` must not carry `ns-panel-scroll`. Whatever replaces `CONSOLE_CARD` inherits that rule.

**Commit:** `feat(desk): the Desk on the page kit`

---

### Task 10: The console vocabulary retires; guard, help text and config

**Files:** Modify `webgui/pages/options/theme.py`, `config/theme.toml`, `webgui/page_help.py`, `webgui/tests/test_ui_kit_guard.py`; Test `webgui/tests/test_theme.py`, `webgui/tests/test_theme_console.py`

With Tasks 6, 8 and 9 done, nothing renders the console surface any more.

- Delete from `theme.py`: `CONSOLE_PAGE`, `CONSOLE_CARD`, `CONSOLE_CELL`, `CONSOLE_HAIRLINE`, `CONSOLE_TRACK`, `CONSOLE_RULE`, `CONSOLE_DIVIDER`, `CONSOLE_DISPLAY`, `CON_TXT*`, `build_console_font_head_html` / `CONSOLE_FONT_HEAD_HTML`, and `CONSOLE_KEYFRAMES_CSS` **if** Task 9's grep found no `con-pulse`. Keep `CON_POS` / `CON_NEG` / `CON_WARN` and `console_colors()["regimes"]`.
- Trim `[console]` in `config/theme.toml` to its data keys (`positive`, `negative`, `warning`, `olive`, `yellow`, the six `regime_*`). ⚠ `accent` is genuinely ambiguous — it drives the dial arc (data) **and** the header rules and chips (chrome). Keep it; it now has exactly one job.
- Trim `[macro]` and `[sectors]` to their data keys per Tasks 3 and 7. `[rotation]` went in Task 5.
- **The guard's `ALLOWED` must now read exactly:** `desk.py: {"add_head_html": 1}`, `market.py: {"button": 2}`, `sentiment_momentum.py: {"button": 1}` — each with its written reason above it — and **no entry at all** for `sentiment.py`, `sentiment_bullbear.py`, `sentiment_sectors.py`, `sentiment_rotation.py`, `sentiment_rrg.py` or `symbol.py`.
- `page_help.py`: every description sentence deleted from a page frame in Tasks 1-9 must be present in that route's help. Check each against `HELP_MD`; `test_page_help.py` covers the routes exist, not the wording.

**Commit:** `refactor(theme): retire the console surface vocabulary`

---

### Task 11: Full suite and the guard

Run, from the worktree root, and compare the failing and skipped **sets** against the Phase 1 baseline (webgui 5217 passed, 1 skipped — `tests/test_auth_store.py:39`):

```
cd webgui && $PY -m pytest -q
$PY -m pytest tools/tests shared/tests -q
```

Every deliberately changed assertion from Tasks 1-10 must be named in the commit that changed it. If a test was weakened rather than re-aimed, put it back — a narrowed guard is the failure mode this project has already paid for once.

---

### Task 12: See it in the harness

⚠ **Step 1 is a gate, not a step.** Do it after Task 1 and **before Task 2**.

Seed the fake bus from the existing page tests' fixtures — never invented shapes. Add a temporary `ui-harness` entry to `.claude/launch.json` as in Phase 1, then revert it with `git checkout -- .claude/launch.json` and delete the seed file when done.

1. **`sentiment_rrg`** (seed `sentiment:rotation`). The header reads `RRG` with an Updated stamp and Refresh on the right. The quadrant washes, the corner labels, the trail and the tone dots look exactly as they did. Click Refresh: **a spinner appears** — it never did before. **Stop here and show the operator before starting Task 2.**
2. **`sentiment_rotation`**, **`sentiment_sectors`**, **`sentiment_bullbear`**, **`sentiment_momentum`**. Header, stamp, one status line, one button vocabulary. Sectors: the eyebrow shows a date and no time. Momentum: the leaderboard sorts when a header is clicked, and the name chips still read as chips.
3. **`market`** (seed `market:dashboard`). No STREAMING dot, no local clock, an Updated stamp instead. Flip the skin: the tiles re-colour instantly and the heat ramp is intact.
4. **`symbol`** (seed `options:matrix` + a dossier). The ticker field has a label; Enter re-navigates; Refresh spins and the overlay still covers the page.
5. **`desk`** (seed all 11 views). Four panels, the neon arrival glow still fires on a new row, the empty states centre.

Take one screenshot of the RRG and one of the Desk.

---

### Task 13: Documentation

**Files:** `docs/CHANGELOG.md`, `docs/webgui-routes.md`, `docs/manuals/user-guide/user-guide.md`, `docs/manuals/reference-guide/reference-guide.md`, `CLAUDE.md`

- **CHANGELOG** entry at the top, previous **Last updated** demoted to **Prior —**. Title: "One look and one behaviour — Phases 3 & 4: Trend & Sentiment, Desk, Symbol, Macro". Bullets: the nine pages; the four spinner bugs fixed (RRG, Rotation, Sectors, Sentiment, Momentum — five, count them in the code before writing the number); the Macro Board's STREAMING dot and naive local clock; the Sector & Industry stamp moving from ET to CT; the rotation neutral ladder and the console surface vocabulary retiring.
- **`docs/webgui-routes.md`** — the nine routes' per-page notes, where they mention a header, a font or a surface.
- **CLAUDE.md** — the "App theme" section's page-scoped palette paragraph is now wrong in four places: `[rotation]` is gone, and `[console]`, `[sectors]`, `[macro]` are data-only. **Correct the sentences in place; do not append a note.**
- **Manuals** — rebuild the User Guide and Reference Guide with `build_docs.py` after editing their Markdown. A user-visible change lands here too, not only in the CHANGELOG.

**Commit:** `docs: phases 3 and 4 — one look across the Trend & Sentiment and entry-point screens`

---

## Done when

- The guard's `ALLOWED` holds exactly three entries — `desk.py`, `market.py`, `sentiment_momentum.py` — plus `options/detail.py` from Phase 1, each with a written reason.
- The full webgui suite matches the Phase 1 baseline sets, with every deliberate assertion change named in its commit.
- `[rotation]` is gone from `config/theme.toml`; `[console]`, `[sectors]` and `[macro]` hold only data colours.
- No page under `webgui/pages/` loads a Google font other than the app font.
- The harness shows each of the nine screens as in Task 12.
- **Not promoted by this plan.** After the operator promotes, eight of the nine are public (`/desk`, `/macro`, `/sentiment`, `/bullbear`, `/sectors`, `/rotation`, `/rrg`, `/momentum`) and can be checked directly; only `/symbol` needs the operator to click through.
