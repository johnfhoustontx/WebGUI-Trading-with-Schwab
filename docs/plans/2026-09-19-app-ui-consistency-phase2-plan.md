# App UI consistency — Phase 2 (Options tools) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move the Calculator, Simulator, Strategy Finder, Expected Move and Dealer Positioning onto the page kit, together with the three shared widgets they mount — `entry_panel`, `leg_editor` and `strategy_menu`. Every chart colour, payoff ramp, heat map, quadrant hue and greek encoding is untouched.

**Architecture:** Phase 0 built `pages/ui_kit.py`; Phase 1 migrated the nine Options boards; Phases 3 & 4 migrated the Trend & Sentiment screens and the three entry points, and retired the `[rotation]` and console surface vocabularies. This phase needs no new kit piece — but it is the first to touch **shared widgets a finished page already mounts.**

**Tech Stack:** NiceGUI 3.13, pytest.

**Prerequisite:** Phases 0, 1, 3 and 4 are done, green and promoted (`5f3efa5`). Read the design first: [`2026-09-19-app-ui-consistency-design.md`](2026-09-19-app-ui-consistency-design.md), and the Phases 3 & 4 plan for the ten rules and the conventions they settled: [`2026-09-19-app-ui-consistency-phase34-plan.md`](2026-09-19-app-ui-consistency-phase34-plan.md). **All ten of that plan's "rules every page task applies" bind here**, especially rule 10 (a render test must be scoped to its own render, and proved red against the pre-migration page).

---

## What makes this phase different

**1. It reaches backwards into a finished page.** `rescue.py` was migrated in Phase 1 and mounts `leg_editor` (`layout="row"`, no `tokens=`) and `strategy_menu` (`boxed=True`, no class overrides). It does **not** touch `entry_panel`.

⚠ **The load-bearing fact, measured:** the `row` layout references `tk` **nowhere**. `tk = leg_tokens(tokens)` is computed at `leg_editor.py:483`, and every consumer of it — `_remove_button` (507), `_table_footer` (521), `_table_head` (529), `_table_body` (538) — is in the **table** path. So **repainting `DEFAULT_LEG_TOKENS`, changing `leg_tokens()` or deleting the Calculator's `_LEG_TOKENS` cannot change one pixel of Rescue.** Rescue's rows are styled by Quasar defaults plus `theme.APP_FIELD_CSS`'s `.leg-head` / `.leg-row` rules, which are app-wide already.

**The `row` layout is a FROZEN CONTRACT for this phase.** Scope every `leg_editor` change to the table path or to `tk`. The two exceptions are the only places Phase 2 can visibly touch Rescue, and both are genuine actions the kit improves:
- `leg_editor.py:503` — the row's remove-leg icon button
- `leg_editor.py:666` — the row's `Add leg`

Say so in the commit when you touch either. ⚠ And **do not change `layout`'s default away from `"row"`**: Rescue passes no `layout`, so a changed default silently flips it to the table layout with `delta_for=None` and `price_for=None` — a different screen, and nothing raises. `test_leg_editor.py:330` is the guard.

**2. Most of these "buttons" are not buttons.** `leg_editor.py` has ten and `entry_panel.py` three; counted honestly, **only six of the thirteen are action buttons**. The rest are side toggles, strike steppers, a cycling type picker, a typed-price reset and the expiry pills — the "segmented picker / leg-table toggle" the guard's own docstring already names as legitimate exceptions. Phases 3 & 4 set the precedent twice (the Macro skin picker, Momentum's name chips).

**3. The Calculator's `[calc]` palette is the last page-scoped surface language**, and this phase retires it — see the decision below.

## The `.calc-v3` decision — RETIRE IT

The Calculator is the app's one genuine instrument, and the case for keeping its near-black language is real: a dense numeric surface read for long stretches, with a scope rule (`.leg-trow .q-field__native{font-size:11px}`) that is doing actual work. **It still goes**, for four reasons:

1. **The design already decided it.** The doc's "Page-scoped palettes" names `[calc]` among those that "lose their background, text, font and button values as their pages migrate; only data-colour keys survive", and its Trade-offs lists the Calculator among the redesigns that "lose their own surfaces and fonts; their layouts and data colours stay."
2. **The precedent is exact and stronger.** The Macro Board was a whole bespoke instrument with two skins and five animations, and its ground, faces and text ladder went anyway.
3. **No encoding is lost.** `DEFAULT_LEG_TOKENS` and `DEFAULT_PANEL_TOKENS` already carry bid-green, ask-red, ATM-amber, the ITM wash, long-cyan/short-green and the typed-price amber. The P&L matrix ramp is not in the TOML at all. `pos` / `neg` / `warn` / `accent` stay as four `[calc]` keys.
4. Keeping it would leave one page-scoped surface in a codebase where every other retired — and the guard would carry a permanent `add_head_html` entry for a **font link**, the one thing its docstring does not allow a reason for (the Desk's exception is explicitly *"a `<script>`, not a font"*).

⚠ **One rule is genuinely load-bearing and must move FIRST, in its own commit.** `.calc-v3 .leg-trow .q-field__native{font-size:11px}` (`theme.py:1130-1131`) is size-only and has **no `.ns-app` equivalent**; its comment says the narrow tracks need it so "Mark" and a four-digit strike fit. Relocating it into `build_quasar_css` under `{scope}` **also changes the Simulator and Rescue** — toward consistency, but visibly. Ship that move alone so the reflow is inspectable by itself.

## Corrections this survey made to the record

- ⚠ **`CLAUDE.md`'s stock-module rule was too broad and is already narrowed** (commit `4980539`). Measured on the bundled Highcharts 12: the Expected Move page combines `extras=["stock"]`, `type="stockChart"` and in-place `update()` — which the blanket wording forbade — and `update()` neither throws nor drops series (1 → 3). **The gamma prohibition stands and is explicitly unmeasured**: its heatmap carries a `colorAxis`, that is the case that failed live, and the probe used neither.
- **The Simulator's chart reflow set is incomplete.** `_reflow_charts` (`simulator.py:869`) fires only on `tabs.on_value_change` (880), but the path a cold page takes is the `set_visibility(True)` at 670 and 716, which call nothing. CLAUDE.md's own rule says any element that mounts hidden belongs in the reflow set **after** the repaint's `set_visibility`.
- **Neither the Simulator, Expected Move nor the Calculator has the cleared-container spinner bug.** Phase 3's count of five stands. The Calculator's `rating_dialog` comment shows the author had already met that class of bug on `swing.py` and mounted around it.
- **`@keyframes scan` (`theme.py:1150`) is dead** — no `animate-[scan…]` anywhere under `pages/` — and `test_theme_calc.py:146` pins it. A test keeping a dead rule alive.
- **`CALC_BTN_PRIMARY` is imported at `calculator.py:43` and never used.**
- **`calculator.py:991` hardcodes `font-family:'JetBrains Mono',…`** inside the raw matrix HTML, independently of `CALC_MONO` and `CALC_FONT_HEAD_HTML`. Deleting those two without touching 991 leaves the matrix asking for a face nobody loads.
- **The Calculator's `LIVE` hint is a static claim** the design's header spec forbids ("no static 'live' claims"), over a chain that may be minutes old. `chain_status_facts` returns it; `test_options_calculator.py:558` pins it.

## Nine vacuous-by-deletion tests to fix, not leave

Each asserts a token name is ABSENT from a source; once the token is gone none can fail. Task 10 of Phases 3 & 4 hit six of this shape. **Replace them with the positive form** `test_theme.py:541` already uses for `[rotation]` (`assert "\n[rotation]" not in text` — asserting the retired section is GONE, which can fail):

`test_options_calculator.py:1163` and `:1164` · `test_theme_calc.py:113` · `test_options_calculator.py:147` and `:1190` · `test_strategy_menu_gate.py:88` and `:116` · `test_theme.py:375` and `:44-46`.

⚠ Two of them will **FAIL rather than go vacuous**, because the line above the absence-assert is a presence-assert: `test_options_calculator.py:1162` (`assert "calc-v3" in src`) and `test_theme_calc.py:112` (`assert ".calc-v3" in css`). Rewrite both; do not delete them.

## What the guard should land on

| file | now | after | reason for what stays |
|---|---|---|---|
| `options/calculator.py` | `add_head_html 1, button 3, dialog 1, notify 3` | **deleted** | font link goes with `[calc]`; the dialog is `kit.info_dialog`; two notifies are validation, one an outcome |
| `options/entry_panel.py` | `button 3` | **`{"button": 1}`** | the expiry pill — a segmented picker, N per repaint |
| `options/leg_editor.py` | `button 10` | **`{"button": 4}`** | side toggle, two strike steppers, cycling type picker |
| `options/strategy_menu.py` | `button 2` | **`{"button": 2}`** | a cascading value picker standing in for `ui.select`; the kit has no such field |
| `options/simulator.py` | `button 1, notify 1` | **`{"button": 1}`** | the three Days snap chips — a stepper beside a slider |
| `options/expected_move.py` | `button 1, notify 1` | **deleted** | Draw is the Go button; the notify is validation |
| `options/swing.py` | `button 7, table 1` | **`{"button": 2, "table": 1}`** | `_Segmented`'s pill and `_chip`'s filter chip; the table keeps server paging |
| `options/gamma.py` | `button 8, notify 7` | **deleted** | five actions + a menu anchor all go to the kit |

---

### Task 1: The one load-bearing calc rule moves — alone

**Files:** Modify `webgui/pages/options/theme.py`; Test `webgui/tests/test_theme.py`, `webgui/tests/test_theme_calc.py`

`.calc-v3 .leg-trow .q-field__native{font-size:11px}` (`theme.py:1130-1131`) is **size-only and has no `.ns-app` equivalent**. Its comment says the narrow tracks need it so "Mark" and a four-digit strike fit. Move it into `build_quasar_css` under `{scope}`.

⚠ **CORRECTED after doing it: this reaches the Simulator, NOT Rescue.** The rule targets `.leg-trow`, which `leg_editor` emits only at line 542 — inside `_table_body`, the **table** path. Rescue mounts `layout="row"` and gets `.leg-row`, a different hook. So the blast radius is the Calculator (which already had the rule under `.calc-v3`, same value) and the **Simulator**, which gains it.

Measured in the harness on the real `sim_meta` payload after the move: all six `.leg-trow .q-field__native` fields at **11px**, **zero** clipped, and `.leg-row` absent from the page — the confirmation that Rescue's hook is not involved. Shipping it alone was still right; the reason was narrower than stated.

**Commit:** `refactor(theme): the leg-table font size becomes app-wide`

---

### Task 2: The three shared widgets

**Files:** Modify `webgui/pages/options/leg_editor.py`, `entry_panel.py`, `strategy_menu.py`; Test their three test files + the guard

⚠ **`rescue.py` is a FINISHED page that mounts two of these.** The `row` layout is a **frozen contract**: it references `tk` nowhere, so retokenising cannot touch Rescue. Scope every change to the table path or to `tk`. **Do not change `layout`'s default away from `"row"`** — Rescue passes none, and a changed default silently gives it the table layout with `delta_for=None`. `test_leg_editor.py:330` guards it.

**The two places Phase 2 CAN touch Rescue, both genuine actions:** `leg_editor.py:503` (the row's remove icon) → `kit.icon_button("delete", tooltip="Remove leg")`, and `:666` (the row's `Add leg`) → `kit.button(kind="secondary", icon="add")`. **Name Rescue in the commit** and re-check it in the harness.

**Table-path buttons:** `ADD LEG` (523) → `kit.button("Add leg", …)`; `RESET TO TEMPLATE` (526) → `kit.button("Reset to template", kind="quiet")`; the `✕` (514) and the typed-price `↺` (598) → `kit.icon_button`, **keeping the wrapper div** at 513 (Quasar kills pointer events on a disabled `q-btn`, so the tooltip needs the wrapper — the comment at 510-512 says so).

**Stay raw, with written reasons:** the SELL/BUY side toggle (544), both strike steppers (563, 576), the cycling CALL/PUT/STOCK picker (582), `entry_panel`'s expiry pill (228), and both `strategy_menu` triggers.

`entry_panel`'s `REFRESH` (156) → `kit.button(kind="primary", icon="refresh")` and **relabel it `Load`** — the design says a page with a Symbol control bar has no header Refresh because its Load button *is* the refresh, and `rescue.py:832` already ships exactly that. ⚠ `test_options_simulator.py:579 test_the_reload_button_says_refresh` asserts the literal upper-case `"REFRESH"` — **re-aim and rename it in the same commit.** `COLUMNS` (165) → `kit.button("Columns", kind="secondary", icon="view_column")`; the menu still mounts as a child.

⚠ **Keep every hook class**: `leg-trow`, `leg-num`, `leg-side`, `leg-type`, `leg-strike-dn`, `leg-strike-up`, `leg-price-reset`, `leg-expiry`, `leg-strike`, `leg-price`, `leg-price-source`, `leg-thead`, `leg-remove`, `leg-row`, `leg-head`, `entry-expiry`, `entry-refresh`, `entry-columns`, `strategy-menu-btn`. Twelve test files select on them.

**CORRECTED after doing it — four things this task settled:**

1. **"`kit.icon_button` … keeping the wrapper" is a conflict the plan did not
   resolve.** `kit.icon_button` REQUIRES a tooltip and builds it INSIDE the
   button, which is the one place a *disabled* q-btn cannot deliver it — the
   whole reason the wrapper exists. Shipped: the kit's tooltip carries the
   state's sentence, and the LOCKED state repeats it on the wrapper, which is
   then the only one that can fire. Live → one tooltip, inside. Locked → one
   tooltip, outside.
2. **The typed-price amber needs `classes(replace=…)`, not an added class.**
   `kit.icon_button` paints `_t.MUTED`; both it and `tk['manual']` are
   one-class `text-[#hex]` arbitraries, so they tie on specificity and
   stylesheet order alone would pick the winner — the `DESK_NEON_CSS` trap.
3. **Five tokens die with the buttons they painted** and are NOT data colours:
   `DEFAULT_LEG_TOKENS`' `remove` / `remove_off` / `add` / `reset` and
   `DEFAULT_PANEL_TOKENS`' `btn`. Leaving them would be the half-live defect
   Task 3 of Phases 3 & 4 already names. Every encoding the plan lists is
   untouched. `test_leg_editor.py:100` was re-aimed to match.
4. ⚠ **`strategy_menu`'s caption is rendered by NOBODY**, and its own comment
   claimed otherwise ("the Simulator and Rescue … are byte-identical"). Measured:
   `entry_panel` passes `caption=False` for the Calculator AND the Simulator, and
   `rescue.py` passes it too, inside `kit.field("Strategy")`. Moving the caption
   onto `theme.EYEBROW` is therefore a zero-visual-impact correctness fix. The
   comment and two test docstrings are corrected in place. **This narrows Task 3's
   warning about `test_the_page_does_not_caption_the_strategy_picker_twice`: the
   Calculator's caption is already off at the panel, so only adopting
   `kit.field("Strategy")` around it can re-introduce the word.**

**Label re-aims beyond the one the plan named** (`test_options_simulator.py:579`):
`test_leg_editor.py` ×5 (the two footer labels ×4 sites, the `tk['remove']` state
test, the token-coverage list), `test_options_simulator.py::test_simulator_legs_footer_offers_add_leg_and_no_reset`,
and `test_options_calculator_apply.py:381` (`_click(root, "REFRESH")` → `"Load"`),
which the plan had filed under Task 3.

**Commit:** `feat(leg-editor, entry-panel): the shared leg widgets on the page kit`

---

### Task 3: The Calculator, and `[calc]` retires

**Files:** Modify `webgui/pages/options/calculator.py`, `webgui/pages/options/theme.py`, `config/theme.toml`; Test `test_options_calculator.py`, `test_options_calculator_apply.py`, `test_theme_calc.py`, `test_theme.py`, the guard

- `kit.page()` + `kit.header("Calculator")` — **no `view=`**. `options:calc_chain` / `calc_result` / `calc_iv` / `calc_rating` are request/response views the page enqueues itself; a freshness stamp on them says nothing.
- The `STRATEGY CALCULATOR` title (1143) and the status pill (1147-1155) go. The pill's *label* becomes `kit.status_line()`; its *loading* state is already covered by the full-screen overlay; the blip dot goes (the Macro STREAMING-dot precedent). ⚠ **`chain_status_facts`' `"LIVE"` hint is a static claim the design forbids** — drop it or make it a real stamp; `test_options_calculator.py:558` pins it, so re-aim deliberately.
- `EXPECTED MOVE` + `RATE MY TRADE` (1177-1185) → `head.actions`, primary last. Keep `rate_btn.set_enabled(False)` and its enable at 1416.
- `rating_dialog` (1069-1078) → `kit.info_dialog("Rate my trade", width=…)`; `rating_status` / `rating_banner` / `rating_panel` go in `handle.content`. It already wears `theme.CARD` / `MUTED`. ⚠ It must stay built at the page root — a dialog deletes itself with the slot it was built in, and the leg table's container is cleared on every edit.
- Three notifies: 1550 → `kit.symbol_error`; 1739 → **delete** (line 1738 already writes the same sentence to `status_lbl`); 1801 → `kit.toast("warn", …)`.
- Pass `tokens=None` to both `leg_editor` and `entry_panel` — `DEFAULT_LEG_TOKENS` and `DEFAULT_PANEL_TOKENS` already carry every encoding (`bid`/`ask`/ATM/ITM/long/short/typed-price).
- ⚠ **`calculator.py:991` hardcodes `font-family:'JetBrains Mono',…`** in the raw matrix HTML. Change it with `CALC_MONO`, or the matrix asks for a face nobody loads.
- The ten `MATRIX_*` constants (409-421): `_MATRIX_PROFIT_RGB`/`_LOSS_RGB`/`_PROFIT_FG`/`_LOSS_FG` and `MATRIX_SPOT` are **DATA and stay**; the other five become `theme` tokens.
- Delete `CALC_BTN_PRIMARY` (imported at 43, never used), `build_calc_css`, `CALC_KEYFRAMES_CSS`, `build_calc_font_head_html` and the 25 surface/text/font/button `CALC_*` tokens. Trim `[calc]` to `pos`, `neg`, `warn`, `accent`. ⚠ `@keyframes scan` is **already dead** and `test_theme_calc.py:146` pins it — delete both.
- ⚠ **Rewrite, do not delete, the two tests whose presence-assert fails first:** `test_options_calculator.py:1162` (`assert "calc-v3" in src`) and `test_theme_calc.py:112`. Give the `[calc]` trim the positive form `test_theme.py:541` uses for `[rotation]`.
- ⚠ `test_options_calculator_apply.py:389 test_the_page_does_not_caption_the_strategy_picker_twice` asserts `"Strategy" not in _texts(root)`. Adopting Rescue's `kit.field("Strategy")` fails it — its reason (the `① STRATEGY` frame chip) disappears with the frame chips, so rewrite it in the same commit.
- Label-selecting tests to re-aim: `test_options_calculator_apply.py` `_click(root,"REFRESH")` (381) and `_rate_button` / `_click(root,"RATE MY TRADE")` (599, 647, 674, 722).

**Commit:** `feat(calculator): the Calculator on the page kit; [calc] retires`

---

### Task 4: The Simulator, and the reflow gap

**Files:** Modify `webgui/pages/options/simulator.py`; Test `test_options_simulator.py`, the guard

- `kit.page()` + `kit.header("Simulator", view="options:sim_meta", stale=False)` — all four `sim_*` views are on-demand.
- Drop `ui.add_css(QUASAR_INTERNAL_CSS)` (318) and the `calc-v2` class (375): `theme.APP_FIELD_CSS` is the identical block under `.ns-app`, already injected app-wide. ⚠ **Keep `QUASAR_INTERNAL_CSS` in `theme.py`** — `trade.py:743` (Phase 5) still uses it.
- The `PAGE` token on 375 goes (a second bordered panel inside the page).
- The notify (803) is **validation** → `kit.symbol_error`.
- The three Days snap chips (614) **stay raw** with a written reason.
- `lookback_sel` (420) uses a Quasar floating label → `kit.select_field`.
- ⚠ **FIX THE REFLOW GAP.** `_reflow_charts` (869) fires only on `tabs.on_value_change` (880), but a cold page reveals its charts through `set_visibility(True)` at **670** and **716**, which call nothing — and a chart that mounted hidden measured 0×0. Call `_reflow_charts` after both. **Write a test that fails without it.**
- ⚠ **Keep every `sim-*` class byte-for-byte** — unlike Momentum's dead hooks, eleven tests select on them.
- ⚠ `charts[0]` at `test_options_simulator.py:745` means "the Replay chart" only because the History panel is built first (418) while the strip lists it last. **Do not reorder the panels.**
- ⚠ `test_a_stale_replay_for_other_legs_is_not_drawn` (733) selects on the raw class `"opacity-70"`, which `kit.empty` replaces — re-aim it.

**Commit:** `feat(simulator): the Simulator on the page kit; hidden charts reflow when they appear`

---

### Task 5: Expected Move

**Files:** Modify `webgui/pages/options/expected_move.py`; Test `test_expected_move.py`, the guard

- `kit.page()` + `kit.header("Expected Move", view="options:expected_move", stale=False)` — this page has **no title and no tab strip**, so nothing names it on screen today.
- The control row (267) → `kit.control_bar()`; the four floating-labelled controls → `kit.symbol_field` / `kit.select_field`. `type_tog` (272) is a toggle and stays.
- `Draw` (275) → `kit.button(kind="primary", icon="show_chart")` — it is the Go button, already positioned right after the last field.
- The notify (328) is **validation** → prefer `kit.gate(draw_btn, symbol_in, expiry_sel)` so Draw is simply disabled until both are set. One caller already routes around the toast deliberately (518-520).
- ⚠ **Do not touch** `extras=["stock"]`, `type="stockChart"`, `xAxis.ordinal`, the disabled `rangeSelector`/`navigator`/`scrollbar`, or the X-crosshair-label-disabled construction. The stock-module question was **measured and settled** (`4980539`): `update()` neither throws nor drops series here.
- ⚠ **Two test helpers break and must be re-aimed, not weakened:** `_find` (301) selects widgets by their Quasar `label` prop — the kit puts the label in a sibling `ui.label`, so **seven call sites across six tests** raise. `_page_polls` (308) filters timers by the `build_busy` qualname — `kit.region`'s is `region.<locals>.tick` and `kit.header`'s is `header.<locals>.poll`, so it must select `_poll` by name rather than by position.
- ⚠ **The scrim has a cross-dismissal bug worth fixing here**: both `show()` sites (340, 370) raise the same scrim and either version bump hides it (488, 491), so a chain load landing mid-compute drops the spinner early.

**Commit:** `feat(expected-move): Expected Move on the page kit`

---

### Task 6: The Strategy Finder

**Files:** Modify `webgui/pages/options/swing.py`; Test `test_options_swing.py`, the guard

- `kit.page()` + `kit.header("Strategy Finder", view="options:swing", stale=False)` — on-demand only.
- The scan bar (447-448) → `kit.control_bar()`; the three hand-rolled `ui.column().classes("gap-1")` + `EYEBROW` groups → `kit.field()`.
- `Scan` (482) → `kit.button("Scan", kind="primary", icon="search")`. ⚠ `h-10 px-4` exists to line up with the 40px pill boxes; `kit.button` is `min-h-[34px]`, so **re-check the row's vertical alignment in the harness**.
- `Change` (776) → `kit.button(kind="quiet")`, carrying its `aria-label` onto the handle. `Calculator` (862) / `Paper` (866) / the chooser picks (896) → `kit.button(kind="secondary")`. ⚠ **`click.stop` on 862 and 866 is load-bearing** ("a button press is not also a card selection") — wire `.on("click.stop", …)` on the handle and pass `on_click=None`.
- **`_Segmented`'s pill (144) and `_chip`'s filter chip (800) stay raw**, with written reasons. `test_the_scan_bar_uses_pill_groups_not_quasar_toggles` (210) asserts `font-normal` on each pill, which `kit.button` does not emit — a second reason.
- ⚠ **THE TABLE KEEPS SERVER PAGING.** `kit.table(rows_per_page=)` builds a pagination dict with **no `rowsNumber`**, which is client-side paging — and `rowsNumber` is precisely what puts Quasar in server mode. The design protects this explicitly. **Either add a `rows_number=` passthrough to `kit.table` or leave this one table raw** and keep `{"table": 1}`. Do not pass `numeric=`: the money columns are deliberately left-aligned.
- **The one real table gap:** the row click opens the panel but does not mark the row. Wire `kit.mark_selected` + the row-class function.
- ⚠ `table._props["no-data-label"]` is written **directly, never as a props string** (681-692) — a quote typed into the symbol box would break a props string. `kit.table` does not model it; that write must survive.
- ⚠ **`_scan_busy` (733) does a single-element unpack over EVERY `ui.spinner` in the render.** A `kit.region` or a busy button adds a second and raises `ValueError`, taking **eight** tests with it. Re-aim it to select the region's spinner.
- ⚠ If `kit.region` replaces `build_busy`, **pass `timeout=SCAN_TIMEOUT_SEC + 5`** (the kit defaults to 30 s; a whole-chain $SPX scan measured 40 s live) — and note `elapsed_label=` has **no kit equivalent**, so `fv.scan_timeout_text`'s running count would be lost. `test_the_counter_names_the_current_request_s_symbol` (770) pins it. Prefer keeping `build_busy` here and say why.
- ⚠ Keep `FINDER_CSS` — both rules are load-bearing (`max-height:none` uncaps the list from the app's 65vh; the focus-helper rule kills a grey slab no prop turns off).
- ⚠ `_buttons(card)` (176) keys a dict by button TEXT; `symbol = _widgets(card, ui.input)[0]` is "the first input" and `eyebrows[:3] == ["Symbol","Expiry","Risk style"]` is an ORDER assertion. All three need re-checking against the kit's label-above shape.

**Commit:** `feat(finder): the Strategy Finder on the page kit`

---

### Task 7: Dealer Positioning

**Files:** Modify `webgui/pages/options/gamma.py`; Test `test_options_gamma.py`, `test_live_commands.py`, `test_busy_coverage.py`, the guard

The largest file in the phase, four public screens, and the one with a proof attached.

- `render(symbol=None, view=None)` — **the signature is pinned** by `test_live_screens.py:214`. Do not touch it.
- `kit.page()` + `kit.header("Dealer Positioning", view=snapshot_view(symbol), stale=False)`. ⚠ **`stale=False` is a decision**: `alerts.stale_after` would go amber 45 minutes after 15:20 CT every evening and all weekend, because `refresh_gamma_current` genuinely stops. `stale=True` would need `"options:gamma"` added to `alerts.RTH_ONLY_VIEWS`, which changes the nav badge — a bigger decision than this migration. Using `snapshot_view(symbol)` makes a pinned screen stamp its **own** key.
- ⚠⚠ **THE GATE. `may_enqueue` is proved TWICE** — page-locally (`test_options_gamma.py:2398`) and across every published module (`test_live_commands.py:138`) — and both require the literal `if not _may_enqueue: … return` as the **first statement after the docstring** of every function containing a `.request(`. **`kit.set_busy` inserted as a first statement would break both.** Put it after the gate.
- ⚠⚠ **Absent, not disabled.** Keep every `if _may_enqueue else None`. A kit button built unconditionally and disabled on a pinned render **fails `test_a_pinned_page_builds_no_control_that_sends_a_command` (2429)**, which reads captions off built elements.
- ⚠ **`ENQUEUEING_CONTROLS` (2420) contains the literal `"Refresh now"`.** The design's one-word rule wants `Refresh`; renaming breaks `:2424` and **weakens `:2429` to vacuity**. Change the set in the same commit or keep the label — say which.
- Five actions → `kit.button`: `Refresh now` (2133, primary), `Explain` (2164), `Analyze` (2166), `Select all`/`Only this group`/`Clear all` (2250/2256/2262), `Open` (2387). `Briefings` (2188) is a menu anchor; `kit.button` works as the host.
- ⚠ `dense flat` + `BTN` on the three `np_*` buttons is a contradiction today (flat kills the fill) — `kind="secondary"` is the honest version.
- Seven notifies: 2738 and 2874 are **validation** → `kit.symbol_error`; 2741 is **work-started → delete** (the spinner at 2743 says it); 2935 is an **outcome** → `kit.toast("warn", …)`; 2877 / 2904 / 2939 announce a tab that opens seconds later with **no spinner behind them** — either keep as `kit.toast("info", …)` or add `kit.set_busy` released in the matching `_watch_*`. Say which and why.
- ⚠ **Keep `gamma-xhair-row` (2315)** — it is the only selector `_CROSSHAIR_JS` queries, and dropping it kills the shared crosshair **silently**. Keep `relative` on the same row (the scrim anchors to it) and `w-[calc(100%+1rem)]` (a deliberate overflow into the content padding). **Do not wrap `chart_row` in a `CARD`.**
- ⚠ **Keep `FLOW_KEYFRAMES_CSS`** — `.fx-pulse` is the live dot and `.fx-panel`'s crosshair cursor **is** the readout affordance. **`[flow]` survives entirely**: the design's page-scoped list does not name it, and its keys are inside a raw SVG chart fragment.
- ⚠ **`EXPLAIN_CSS` (1195-1207) appears DEAD** — `grep` finds `gx-explain` on those twelve lines and nowhere else; the in-app Explain dialog is gone and `/options/explain` is a raw `HTMLResponse` with its own `<style>`. **Verify in the harness, then delete it and its `ui.add_css`.**
- ⚠ **Touch none of the chart facts**: `HEAT_STOPS`' transparent zero stop, `interpolation: True`, the `colorAxis` that must be present at element creation, `_HEAT_PRESS_TOOLTIP_JS`'s **three** attachment sites, `_set_chart`'s recreate-on-kind-change, `uniform_strike_grid`, the constant nine-series count, and `_INIT_FLEX`/`_apply_flex` (the 40/60 split that keeps the two panels' strike axes pixel-aligned). `hedge_plot`/`hedge_lbl` mount hidden — `_reflow_charts` must still run **after** `set_visibility`.
- ⚠ `test_busy_coverage.py:126` asserts the literal `"build_busy(" in gamma` and `"build_loading_overlay(" not in`. Update it to whichever spelling ships.
- Empty states → `kit.empty`. ⚠ `"Fetch a symbol…"` is **wrong on a pinned public screen** — there is no symbol box there.

**Commit:** `feat(gamma): Dealer Positioning on the page kit`

---

### Task 8: Guard, config and the vacuous tests

**Files:** `webgui/tests/test_ui_kit_guard.py`, `webgui/pages/options/theme.py`, `config/theme.toml`, `webgui/page_help.py`

- `ALLOWED` lands on the table at the top of this plan — five entries for Phase 2, each with a written reason above it, plus the four from earlier phases.
- Replace the **nine vacuous absence tests** with the positive form. ⚠ Two of them fail rather than go vacuous — rewrite, don't delete.
- Verify `page_help.py` carries every description sentence deleted from a page frame. **Verify, do not assume** — every earlier task reported theirs already covered.

**Commit:** `refactor(theme): retire the calc surface vocabulary`

---

### Task 9: Full suite

`cd webgui && $PY -m pytest -q` against the Phase 3/4 baseline **5328 passed, 1 skipped** (`tests/test_auth_store.py:39`), then `$PY -m pytest tools/tests shared/tests tests -q` (1718 + 155). Compare the failing and skipped **sets**. Every deliberately changed assertion must be named in the commit that changed it.

---

### Task 10: The harness

Seed from the real prod payloads already in the scratchpad. Check, in this order — **Task 1's reflow check comes first and alone**:

1. **After Task 1 only:** the Calculator, the Simulator and **Rescue** leg tables — the font-size move is the one change that touches a finished page.
2. **Calculator** — header + actions, the info dialog, the P&L matrix still monospaced, every chain-grid and leg colour unchanged.
3. **Simulator** — the three tabs, and a cold page's charts **not** collapsed to 0×0 when the first result lands.
4. **Expected Move** — the cone draws; Draw gated until symbol + expiry.
5. **Strategy Finder** — scan, page 2, sort, a selected row's accent, the Paper dialog surviving a repaint.
6. **Dealer Positioning** — all seven views, the crosshair, the press-and-hold tooltip, the plasma zero-point transparency, and a **pinned** render (`symbol="$SPX", view="GEX"`) building no enqueueing control.

### Task 11: Documentation

CHANGELOG entry; `docs/webgui-routes.md` for the five routes; CLAUDE.md's page-scoped palette paragraph again (`[calc]` joins the data-only list, leaving `[flow]` as the **only** full page-scoped language); the manuals if any user-visible wording moved.
