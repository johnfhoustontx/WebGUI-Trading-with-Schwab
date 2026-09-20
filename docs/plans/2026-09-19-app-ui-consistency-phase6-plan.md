# App UI consistency — Phase 6 (the system pages) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move System Status, Settings → General, Settings → Configuration, Stop All Services, the EOD Report and User Manuals onto the page kit — and give the app's destructive controls one confirm vocabulary.

**Architecture:** Phase 0 built `pages/ui_kit.py`; Phases 1–5 migrated every feature screen. This phase finishes the app. **Settings → Appearance was migrated in Phase 0** and is the in-repo template for everything here: read `pages/appearance.py` before writing a line.

**Tech Stack:** NiceGUI 3.13, pytest.

**Prerequisite:** Phase 5 is done, green and promoted. Read the design first, then the Phases 3 & 4 plan for the **ten rules every page task applies** — especially rule 10 — and the Phase 2 plan's "CORRECTED after doing it" notes.

---

## What makes this phase different

**This is the app's destructive surface.** Between them these six pages stop the whole stack, restart nine of eleven components, overwrite the day's saved report, wipe a config file back to shipped values and VACUUM a database. Three of those five have **no confirmation today**. The styling is the small half of this phase.

**It is also the cheapest phase for tests.** `test_status.py` (53), `test_settings.py` (9), `test_config_editor.py` (6), `test_terminate.py` (~30) and `test_eod.py` (~50) are **entirely against pure builders** — no element selection, no "first button", no dict keyed by button text, no render test at all outside `test_appearance.py`. Almost nothing breaks; almost every new render test is new, and **must be proved red against the pre-migration page** (rule 10).

## Decisions settled up front

**1. The Status stamp is driven by hand, and the kit is not edited.** `kit.header`'s stamp is bound to a bus view — `stamp.set_visibility(view is not None)`, and `poll()` only exists when a view is given. Status's freshness is a **probe**, not a view. So: `head = kit.header("System Status")` with no view, then once in `render()` `head.stamp.set_visibility(True)`, and at the end of `_refresh` `head.set_stamp(<iso utc>)`. `freshness()` renders *"Updated 4:16 PM CT"*, and *"Waiting for data"* before the first sweep — exactly right, using only the public handle. **Adding `set_visibility(True)` inside `kit.set_stamp` was considered and rejected**: a kit edit inside a page phase changes every migrated page's behaviour for one page's benefit. Put a comment at the call site saying why the stamp is hand-driven.

**2. All three Settings tabs use `kit.page()` (full width).** They are peers in one page and a content column that jumps width when you switch tabs is exactly the inconsistency this migration exists to remove. General's cards keep their own `max-w-2xl` cap, which is what caps them today anyway. Standalone form routes — **Manuals** and **Stop All Services** — take `kit.page(width="form")`.

**3. `kit.confirm` models the TOTP dialog, and no guard exception is needed.** The two things that look missing are both already in the kit:
- **An input field** — `kit.confirm` returns `handle.content`, an empty column between the body and the button row, documented as *"Add any inputs to `handle.content` before `open()`"*. The 6-digit input, the "Confirm with the 6-digit code" line and the `problem` label all go there.
- **A second factor that can refuse** — **`on_confirm` returning `False` keeps the dialog open.** That is exactly `_go`'s refusal branch, and it preserves the load-bearing comment at `terminate.py:219-221` (*closing it on a refusal would read as done*).

Everything else already lines up: `danger=True` → the same `BTN_DANGER_SOLID` the page uses today, Cancel-then-confirm is already the order, and the kit's re-entrancy guard is strictly better than today's none (`st["done"]` resets on each open, so a refusal can be retried). Build it **once at `render()`'s own level** — not `ephemeral=True`, because this dialog must survive a refusal and be reopened.

**4. `BTN_3D` / `BTN_3D_DANGER` die at the end of this phase.** Their remaining users are `portfolio.py` (Phase 5) and `status.py` / `settings.py` / `manuals.py` (here). ⚠ **`BTN_3D_DANGER` has no user at all** outside its own definition — verify and delete both in the final task, not before.

## The three findings that are defects, not styling

**1. ⚠ EOD's Generate can destroy the day's real report, and the guard against it already exists one function away.** `write_archive` overwrites `<root>/<date>/summary.html` and `detail.html` in place. `has_data(snap)` exists precisely to stop a cold cache replacing a real report with an all-"No data" document — its own docstring says *"Callers that are not a person clicking Generate check this first"*, and `tools/generate_eod_report.py:86` does. **`_on_generate` calls `generate()` with no snapshot and no `has_data` check**, so a click while the stack is stopped silently destroys that day's report. Verified in the source, both halves. **This ships as its own commit, before any styling.**

**2. ⚠ No Restart on the Status page confirms.** Nine of eleven components are restartable (`systemctl --user restart trading-<env>-<name>`), including **this web app itself** — which kills the page you clicked from — and, when this checkout owns it, the **proxy**, whose restart stops market data for the whole stack mid-session. Redis is read-only in every environment (a system unit `systemctl --user` cannot reach) and the auth card's action is Authorize, not a restart.

**3. ⚠ Two naive machine-local clocks.** `status.py:568-570` stamps `_dt.datetime.now()` and renders `%H:%M:%S` with no zone — the Macro Board's retired `SESSION` clock in a different spelling; the header stamp replaces it. And `config_store.save` writes `_dt.datetime.now().isoformat()` with no zone, rendered in the Recent changes expansion as `2026-09-20 14:03`. ⚠ That second one touches the **stored** format, so the reader must tolerate the naive rows already in `changes.jsonl`.

## Two more things the survey found

- ⚠ **`config_editor._paint_footer` clears and rebuilds the footer** — the exact bug `appearance.py:184-186` already fixed, with the note *"Built ONCE and mutated. Rebuilding them swallowed the click that caused a field's blur."* It is reproducible on this page today. Fix it while migrating.
- ⚠ **`config_editor._PENDING` and `_restart_webgui` are a de-facto public API between the two Settings tabs.** `appearance.py` reads and writes `_PENDING` in three places and calls `_restart_webgui()`; `tests/test_appearance.py` monkeypatches `_PENDING` in four. **Keep both names, both modules, and `_PENDING`'s `set` type.**
- **Both `config_editor` dialogs ARE built inside containers a repaint clears** (`pending_box` → `_paint_pending`; `body` → `_paint_body`), and `_go` calls `_paint_pending()` while it is still running. Build both at `render()`'s own level.
- **`page_help["/manuals"]` is missing the Options Glossary**, which `MANUALS` has carried since it was added. Fix it in Task 1.

## Tests that will FAIL and must be re-aimed, never weakened

| file:line | why |
|---|---|
| `test_terminate.py:286` | asserts `"Cancel" in getsource(render)`; `kit.confirm` owns that literal. Re-aim at `kit.confirm(` being used and the handle exposing `.cancel`; keep the `"This also stops THIS web app"` half. |
| `test_status.py:422` | presence-asserts `"w-[280px]"` and `"justify-end"` in `render`'s source — layout classes the migration rewrites. |

**And three that keep passing only by a margin — verify each after implementing:**
- `test_terminate.py:275` splits `render`'s source on `"verify_stop_code("`. Build `kit.confirm(on_confirm=_go)` **after** `_go`, the `appearance.py` order.
- `test_terminate.py:53` counts `"only in the environment that owns it"` ≥ 2 and today finds 3 (docstring + render + dialog). Compressing the dialog body leaves exactly 2. **Do not drop both render copies.**
- `test_eod.py:21` asserts `".eod-report" in EOD_CSS` — keep every scope prefix through the recolour.

**Vacuous-by-deletion, replace with the positive form:** `test_status.py:424` (`'ml-auto {BTN_3D}' not in src` — unfailable once `BTN_3D` leaves), `test_settings.py:119`.

**Shapes in `test_appearance.py` this phase must not step on:** `_buttons(host)` is a dict keyed by button **text** — do not copy it into a Phase 6 test module, because `config_editor` has **two different `Restart now` buttons**. `_dialog_body(dlg)` picks the first `text-sm` label — it will break on terminate's dialog, whose `content` holds a `text-sm` line above the body, so use a different selector there. `_confirm(dlg)` (368-381) is **the working recipe for driving a `kit.confirm` under pytest** — reuse it verbatim for all four new confirms.

---

## Task 1 — Manuals, the phase preview

**Files:** `pages/manuals.py`, `webgui/page_help.py`, new `tests/test_manuals.py`.

`kit.page(width="form")` + `kit.header("User Manuals")` + `kit.button("Open", kind="secondary", icon="open_in_new")`; cards to `theme.CARD`, `opacity-*` to `EYEBROW`/`MUTED`. Move the description sentence to `page_help["/manuals"]` **and add the missing Options Glossary entry while you are there.**

⚠ `shared/tests/test_cross_tier_mirrors.py` pins the `MANUALS` dual registration and `main.py` uses that dict as the **serving whitelist** — do not reorder, rename or restructure it. ⚠ `width="form"` is a widening from `max-w-2xl` to `max-w-3xl`; name it.

**Guard:** `manuals.py` **deleted**. **⚠ STOP and show the operator before Task 2** — this is the phase's first look at a form page, and it is 15 lines.

## Task 2 — Stop All Services

**Files:** `pages/terminate.py`, `tests/test_terminate.py`.

`kit.page(width="form")` + `kit.header("Stop All Services")` + one `kit.confirm(danger=True)` per decision 3 above. Page button → `kit.button(kind="danger", icon="power_settings_new")`.

⚠ **State the visual change:** that button goes from **solid red** to **red outline**. That is the standard (solid red only *inside* its confirm dialog) and it matches the rail, which already draws this route as a danger-outlined button — but an operator will notice.

Two improvements to name in the commit: **Enter now confirms** (`CONFIRM_ENTER_JS` fires for any non-TEXTAREA target, so Enter in the 6-digit field submits — today it does nothing), and the toast timeout moves 10000 → the kit's 8000 (irrelevant, the page dies).

⚠ **No spinner, and none should be added** — `test_busy_coverage.py:46` exempts it because *the page intentionally goes unresponsive after confirm*. Keep that entry. **No `view=`, no stamp** — nothing here has a freshness.

**Guard:** `terminate.py` **deleted**. Say in the commit that no exception was needed and why.

## Task 3 — EOD's Generate defect, alone

**Files:** `pages/eod.py`, `tests/test_eod.py`.

Add the `has_data` refusal — `kit.toast("warn", …)` rather than a write — and a `kit.confirm(danger=True)` naming that Generate **replaces the day's saved files**. Build the dialog at `render()`'s own level, **not inside `container`**, which `_repaint` clears.

**No styling in this commit.** `test_eod.py` already pins `has_data` from both sides; the test that matters here is the converse and is new: **a `render()` Generate against a cold snapshot must not write.**

## Task 4 — EOD's two frames

**Files:** `pages/eod.py`, `tests/test_busy_coverage.py`, `tests/test_eod.py`.

⚠ **`render_detail` is a second `render()` in the same module and is easy to miss** — both frames get `kit.page()` + `kit.header` (*EOD Report* / *EOD Report — Detail*; neither has a title today, `main.py` supplies them as browser titles only).

Three buttons → `kit.button`; two notifies → `kit.toast`; `kit.region` around the fragment. ⚠ **`kit.set_busy` on the Generate button is unsafe** — `_repaint` deletes and rebuilds it, taking the busy timer with it. Use `region.busy.show("Generating the report…")`.

Cross `run.io_bound` for `read_snapshot()` (**8 sequential bus reads**, on the loop in both frames) and `generate()` (builds two documents and writes two files, on the loop). ⚠ **`test_busy_coverage.py:48`'s exemption reason is wrong** — it says *"no in-page repaint"* and `_on_generate` calls `_repaint()`. Re-word or drop it.

**No `view=`, no stamp.** There is no single primary view — the page reads **eight** — and the fragment already carries `generated_at` in CT, which is the page's real freshness. A stamp on one of eight would name the age of a key that is only part of what is on screen; that is the reasoning the gamma commit used. **Keep the `.meta` line.**

**Guard:** `eod.py` **deleted**.

## Task 5 — `EOD_CSS` takes the app's colours

**Files:** `pages/eod.py`, `tests/test_eod.py`.

`EOD_CSS` has **two destinations**: `ui.add_css` in both frames (the page's one escape hatch, in scope) and `wrap_document` into the standalone `summary.html` / `detail.html` (a raw document — **recoloured, not deleted, not routed through the kit**, exactly as the design says).

Segoe UI → IBM Plex; `#e8e8e8` → the palette text; the `rgba(255,255,255,.12)` / `.08` rules → `card_border`; the `.tile`'s three-layer inset/drop shadow → the flat card ground and a hairline (**it is the retired 3D button look**); `a { color:#64b5f6 }` → the palette accent; `wrap_document`'s `<body style="background:#1e1e1e">` → the navy page ground.

⚠ **`.pos { #4caf50 }` / `.neg { #ef5350 }` is a fourth green/red pair** beside the design's three. Re-point both at the semantic positive/negative **hex values** — this is raw CSS in a standalone document, not Tailwind tokens. `_pn_class` returns `""` for zero, which already matches "muted for zero"; keep it.

Ship alone so the exported documents are inspectable by themselves.

## Task 6 — Status: the frame, the stamp and the toasts

**Files:** `pages/status.py`, `webgui/page_help.py`, `tests/test_status.py`.

`kit.page()` + `kit.header("System Status")` with the hand-driven stamp per decision 1, **deleting the naive `%H:%M:%S` clock**. Description to `page_help`. `ui.separator()` + the two freshness labels → `kit.section_title("Published data freshness")`.

`kit.region` around `comps`; `kit.set_busy(refresh_btn)` replacing the manual `disable()`/`enable()` pair and the bare `ui.spinner`. ⚠ **The spinner is a sibling of both cleared containers — this page does NOT have the bug**, and is the only one of the six that already mounted around it. Say so rather than claiming a fix.

Four notifies: three are outcomes → `kit.toast`; the fourth (*"Restarting {label}… ~15s"*) is **work-started** and becomes `region.busy.show(f"Restarting {label}…")` — ⚠ **not `kit.set_busy` on the Restart button**, which `_paint_components` rebuilds every 15 s and would delete mid-wait. ⚠ The *"can't be restarted from here"* toast is **unreachable from the UI** (the button is only built when `restart_spec` is non-None) — keep it defensively and note it.

Colours: the banner's raw Quasar palette (`bg-green-2 text-green-10` etc. — the only place in the app that uses it) → `kit.notice` plus `TXT_POS`/`TXT_NEG`/`MUTED`; `text-orange` → `TXT_WARN`; `text-negative` → `TXT_NEG`. **Quasar `color=` props stay** (`status_color(up)` on badges and icons) — out of scope by the design, and `test_no_inline_style.py:177` already records it. `status_word` / `status_color` / `status_icon` are pure and tested — **do not change them.**

Move `_paint_freshness`'s seven blocking reads off the loop (`_sweep` already crosses `run.io_bound`; this one does not). ⚠ `test_busy_coverage.py:50`'s exemption for this page is **already inert** (the list only bites pages containing `bus_client.request(`, and Status has none) — leave or re-word it, but do not claim it was load-bearing.

Re-aim `test_status.py:422` and `:424`. **Guard:** `status.py` → `{"button": 2}` (Authorize + Restart, pending Task 7).

## Task 7 — Status: Restart gets a confirm

**Files:** `pages/status.py`, `tests/test_status.py`.

One `kit.confirm(danger=True)` built **once at page level** (not in `comps`, which is rebuilt every 15 s), retitled per click — the kit's documented build-once-retitle pattern. Two bodies must differ:
- `kind == "self"` → *"This web app restarts. The page you are looking at disconnects and needs a reload."*
- `kind == "proxy"` during market hours → name that market data stops for ~15 s for the whole stack.

Authorize → `kit.button(kind="secondary", icon="login")`; Restart → `kit.button(kind="danger", icon="restart_alt")`.

**Nothing today pins that a Restart does *not* confirm**, so adding one breaks nothing — and `restart_spec`'s ten tests and `restart_command`'s five are pure and untouched.

**Guard:** `status.py` **deleted**.

## Task 8 — Settings → General

**Files:** `pages/settings.py`, `webgui/page_help.py`, `tests/test_settings.py`.

`kit.page()` + `kit.header("General")` — **named after its tab, not the page**; "Settings" would read as a second title under the breadcrumb, and Appearance and Configuration already set this precedent.

Seven buttons → `kit.button`; the dialog → `kit.confirm(danger=True)` with Cancel-then-confirm (today it is confirm-then-Cancel with Cancel styled as a full primary — the exact inconsistency the design names). ⚠ `_vacuum` opens with `vac_dlg.close()`; the kit closes it, so drop that line. ⚠ **Add what the dialog does not say today: whether `--purge` is on** — `kit.confirm` lets the body be set at open time, and `--purge` deletes all but the last 5 sessions.

Three `ui.select` with Quasar floating labels (Sound, Voice, Speed) → `kit.select_field`; `Minimum score to alert` → `kit.number_field(min=0, max=100, step=5, integer=True)` — today's `min`/`max` on the widget **silently clamp on blur**, where the kit validates and says why. Two sliders → `kit.field("Volume")`. **Eight switches stay raw** — the kit has no switch builder, the guard does not cover them, and they already match "apply immediately".

`kit.set_busy` on **Test voice** (`voice.ensure` blocks 0.9–2.4 s on a cache miss — released in a `finally`) and on **Vacuum**; the `"Running VACUUM…"` write is work-started and the spinner replaces it. Keep `vac_result` — it holds subprocess stdout, and its `font-mono` is legitimate.

One notify → `kit.toast("warn", …)`. **No `view=`, no stamp.** **Guard:** `settings.py` **deleted**.

## Task 9 — Settings → Configuration

**Files:** `pages/config_editor.py`, `tests/test_config_editor.py`, `tests/test_appearance.py` (verify only).

`kit.page()` + `kit.header("Configuration")`; description to `page_help` (largely there already). The pending banner → `kit.notice` — **copy `appearance._paint_banner`, which is the migrated version of the same block.** The footer is **built once and MUTATED**, per decision above; copy `appearance.py:184-186`'s tokens with it.

Twelve buttons → `kit.button` / `kit.icon_button`. ⚠ The per-file **Reset all** is destructive and is styled as a quiet grey link today → `kind="danger"`. ⚠ The ladder's remove icon has **no tooltip**; the kit requires one — a real improvement.

Ten notifies: one becomes inline field validation (*"Type a symbol and pick its sector"* — both fields are two inches away); **`"Restarting…"` is deleted** — `dlg.close()` runs before it, so there is no element left to spin. **Keep the dialog open instead**, `kit.set_busy` its confirm, and close it when the results land; that also stops a second click firing a second restart. The remaining eight → `kit.toast`. ⚠ Two of them are **loops** (cross-check problems, per-unit results) and can stack several 8-second errors — flag as a decision, not a rule.

Both dialogs → `kit.confirm(ephemeral=True)` **built at `render()`'s own level**, the Reset one `danger=True`. The restart dialog's per-unit checkboxes and market-open warning go in `handle.content`.

⚠ **`_PENDING` and `_restart_webgui` keep their names and module.** ⚠ `store.save` is not touched — the `config/local/` write path is guarded below the page by `shared/tests/test_config_overrides.py::test_the_tracked_file_is_never_written`. **Add a page-level test that Save reaches `store.save` and nothing else**, mirroring `test_appearance.py:314`.

⚠ **`_build_control` stays as it is.** Its row layout is deliberate, and its `ui.number`s deliberately keep min/max **off** the widget so `config_schema.parse` can say *why* a value is refused. Restyle props to the kit's only where they already match — and verify in the harness before changing all eleven.

The search field is **placeholder-only**, which the standard forbids → `kit.text_field("Search", placeholder="delta, take profit, VIX")` in a `kit.control_bar()`.

**No `view=`, no stamp.** **Guard:** `config_editor.py` **deleted**.

## Task 10 — The change-log clock

`config_store.save` stamps CT; `_paint_history` labels the zone and **tolerates the naive rows already in `changes.jsonl`**. Small and separable — cut it if the phase runs long, but then do not leave an unlabelled clock on screen.

## Task 11 — `BTN_3D` dies; guard docstring; full suite; harness

Delete `BTN_3D` and `BTN_3D_DANGER` from `theme.py` once no page imports either (verify `BTN_3D_DANGER` has no user at all). ⚠ **Update the guard's module docstring** — it says *"Every other entry is a page no phase has migrated yet"* and counts nine permanents; after this phase there are ten and no unmigrated page. Correct it in place.

Full suite, comparing the failing **and skipped** sets. Then the harness: the confirm dialog on each of Terminate (exercising a **refused** TOTP path), Status Restart, Configuration Reset and EOD Generate; one screenshot of Status and one of Configuration.

## Task 12 — Docs

CHANGELOG entry; `page_help.py` (verify every sentence deleted from a frame landed there); the User Guide and Reference Guide control lists (**Restart now confirms · Stop all services is a red outline · Generate confirms and refuses a cold cache**); `CLAUDE.md`'s theme section. ⚠ **These six routes have no `docs/webgui-routes.md` section today** — decide whether Phase 6 adds one or says why not.

---

**Where the guard lands:** all six entries deleted. **No page in this phase earns a written exception** — every one of its 28 buttons is a genuine action, with no segmented picker, cycling toggle or menu anchor among them.
