# Strategy Finder — ask before loading a large chain: implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (this
> session) to implement this plan task by task.

**Goal:** A Finder scan over more than 30 expirations answers with four choices instead
of fetching the chain; the pick is fetched, remembered per symbol, and shown as "Scanned N
of M"; the DTE max box fits its placeholder; the docs carry live timings.

**Architecture:** Counting and choosing live in Tier 2 (`services/options_svc/compute.py`,
`handlers.py`). `swing_scan` gains `expiry_choice=None, ask_if_large=False`; only the
Finder handler asks. Tier 1 renders the chooser from the answer (`finder_view.py` pure,
`swing.py` wiring). No builder changes.

**Design:** [`2026-09-14-strategy-finder-large-chain-chooser-design.md`](2026-09-14-strategy-finder-large-chain-chooser-design.md)

---

## Ground rules for every task

- TDD: failing test first, see it fail for the right reason, implement, see it pass.
- **Never weaken an existing assertion.** If one genuinely conflicts, stop and report —
  unless the task names it.
- Tier 1 imports no `services.*`; Tailwind classes only; no `.style()`.
- A missing reading is `None`, never 0.
- Stage files by name; one commit per task; message ends with
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Windows worktree; Python `D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe`.
  Service tests from the worktree root one file at a time; webgui tests from `webgui/`.
- The options_svc test conftest stubs the client's `get_option_expirations` to a 503 by
  default (`_no_live_expiration_list`); tests that need typed rows stub
  `compute.option_expiration_rows` (Task 1) or the client method themselves.
- Base: `6ae54bf` (prod).

---

### Task 1: Typed expirations, counting and choice selection (pure service helpers)

**Files:** `services/options_svc/compute.py`;
test `services/options_svc/tests/test_expiry_choice.py` (new).

Add near the whole-chain fetch helpers:

```python
LARGE_CHAIN_EXPIRIES = 30          # more than this in range -> ask first
SCAN_SEC_PER_EXPIRY = 0.75         # live 2026-09-14: SPY 26 s / 34, $SPX 40 s / 56
EXPIRY_CHOICES = (("next_30", "Next 30 days"), ("next_90", "Next 90 days"),
                  ("monthly", "Monthlies only"), ("all", "Everything"))
```

- `parse_expiration_rows(payload, today=None) -> list[tuple[str, str | None, int]]` —
  `(ISO date, expirationType, dte)` from `/expirationchain`, sorted by date, unique by
  date, junk rows dropped (same tolerance as `parse_expiration_list`). The type is
  stripped and upper-cased; a missing, blank or non-string type is `None`; a duplicate
  date keeps `"S"` if any of its rows says so. `dte` is **Schwab's `daysToExpiration`**
  (the same basis the chain keys use) when it is a non-negative whole number (int, or a
  float with no fraction; bool/NaN/str rejected), else the calendar difference against
  `today` — the only use of `today`.
- `option_expiration_rows(api)` — like `option_expirations` but returns the typed rows
  (`[]` on no method / non-200). `option_expirations` stays as is (the Calculator uses
  it).
- `rows_in_range(rows, dte_min, dte_max)` — rows whose `dte` is in `[dte_min, dte_max]`
  (`dte_max` None = no upper bound; the lower bound is clamped at 0, so a negative
  `dte_min` never admits a past row).
- `choice_dates(rows, choice, dte_min, dte_max) -> list[str]` — the in-range
  dates a choice keeps: `next_30` DTE ≤ 30, `next_90` DTE ≤ 90, `monthly` type `"S"`, `all`
  everything. Unknown `choice` → `ValueError`.
- `choice_summary(rows, dte_min, dte_max) -> list[dict]` — one dict per
  `EXPIRY_CHOICES` entry, in that order: `{"key", "label", "count", "est_seconds"}` with
  `est_seconds = count * SCAN_SEC_PER_EXPIRY` rounded half up (not to even: 4.5 → 5).

Tests: parsing (W/S/Q/M kept; bad date and non-dict rows dropped; duplicates collapse;
order); range with a `dte_min` floor, a `dte_max`, and `None`; each choice's dates on a
fixture shaped like $SPX (dailies/weeklies inside 30 d, monthlies S, quarterlies Q, a
month-end M, LEAPS); `monthly` excludes Q and M; `next_30` inside `dte_min=7` excludes
DTE < 7; a row whose `daysToExpiration` differs from the calendar difference counts by
Schwab's number; a missing or garbage `daysToExpiration` falls back to the calendar;
summary order, counts and half-up `est_seconds`; unknown choice raises. Pass `today=`
wherever the calendar fallback can be reached (no midnight flake).

Commit `feat(finder-svc): typed expirations and the four load choices`.

---

### Task 2: The large-chain answer and choice-limited fetch (service + handler)

**Files:** `services/options_svc/compute.py`, `services/options_svc/handlers.py`;
tests `services/options_svc/tests/test_large_chain_answer.py` (new).

1. **Fetch.** `fetch_scan_chain(symbol, dte_max, *, rows=None, dates=None)`:
   - `rows` given → do not list again (no second `/expirationchain` call).
   - `dates` given → fetch exactly those dates: `expiry_runs([r[0] for r in rows],
     dates)` (runs consecutive in the LISTING), each run split to at most
     `SCAN_RUN_EXPIRIES`; failed-run counting unchanged.
   - Neither given → today's behaviour exactly (existing tests stay green unmodified).
2. **`swing_scan(..., expiry_choice=None, ask_if_large=False)`**, validated in
   `_validate_scan_args` (unknown non-None choice → `ValueError`, before any fetch):
   - After the quote, list typed rows once (`option_expiration_rows`, degrade-guarded like
     the existing listing; empty/raise → today's fallback path, never asks).
   - `in_range = rows_in_range(rows, dte_min, dte_max)`; `large = len(in_range) >
     LARGE_CHAIN_EXPIRIES`.
   - `ask_if_large and large and expiry_choice is None` → return `_scan_result(...,
     spot=spot, expiries_failed=0, needs_choice=True, expiration_count=len(in_range),
     choices=choice_summary(rows, dte_min, dte_max))` **without fetching a chain**.
   - `large and expiry_choice` → `dates = choice_dates(rows, expiry_choice, dte_min,
     dte_max)`. **The IV input must not change with the choice:** `run_iv_analysis` reads
     the expiry nearest 30 DTE within 7–60 days, so if no chosen date has a DTE in 7–60,
     also FETCH the listed date nearest 30 DTE (DTE throughout is the row's Schwab
     `daysToExpiration`, the chain keys' basis). Then **slice the fetched chain to the
     chosen dates** (`chain_slice`) for everything after the IV analysis, so that helper
     date can never become a candidate. The answer carries `expiry_choice`,
     `expiration_count=len(in_range)`, `expirations_scanned=len(dates)`, `choices`.
   - Not large (or `ask_if_large` False) → a supplied choice is ignored: scan everything
     as today; `expiry_choice` in the answer is `None`, `expiration_count` =
     `len(in_range)` when rows were listed, else `None`.
   - `_scan_result` gains `needs_choice=False, expiration_count=None,
     expirations_scanned=None, choices=None, expiry_choice=None` so every path carries
     the keys.
3. **Handler.** `_SWING_DEFAULTS` gains `"expiry_choice": None`; the Finder call passes
   `ask_if_large=True`; the payload publishes `needs_choice`, `expiration_count`,
   `expirations_scanned`, `choices`, `expiry_choice` (`.get` with the same defaults).
   The error answer carries `needs_choice: False` and `choices: None`.
   `income_scan` passes neither keyword.

Tests (stub quote, `option_expiration_rows`, `se.fetch_option_chain` with a spy; use the
existing `scan_env` idea or a small typed fixture):
31 in-range rows + no choice + `ask_if_large=True` → `needs_choice`, choices as
`choice_summary`, **zero chain fetches**; 30 rows → scans, no ask; `ask_if_large=False`
with 56 rows → scans everything (Income); `monthly` → the spy saw exactly the monthly
dates as single-date runs (plus the IV helper date if needed) and the built signals'
expirations ⊆ monthly dates; `next_30` → contiguous runs ≤ 8; a choice on a 20-row range
is ignored (`expiry_choice` None, all dates fetched); unknown choice → handler publishes an
`error` answer; no expiration list → never asks; handler payload carries the new keys;
`income_scan` passes neither keyword; the per-symbol conftest stub still keeps tests off
the proxy.

Commit `feat(finder-svc): a large chain answers with choices before any fetch`.

---

### Task 3: Page vocabulary for the chooser (pure)

**Files:** `webgui/pages/options/finder_view.py`; test `webgui/tests/test_finder_view.py`.

- `chooser_facts(payload) -> dict | None` — `None` unless `needs_choice` is truthy.
  Otherwise `{"title": "$SPX lists 56 expirations in this range.", "prompt": "Choose what
  to scan:", "buttons": [{"key", "text": "Next 30 days · 23 · ~17 s", "enabled":
  count > 0}, ...]}` in the payload's order; unreadable counts/estimates drop that part
  of the text, never print 0 for an unreadable value; a choices list that is missing or
  malformed → `None` (the page then shows the ordinary empty line).
- `choice_label(key)` — the label for a key from the payload's `choices`, falling back to
  a fixed map of the four keys.
- `summary_facts`: on `needs_choice`, counts = `"56 expirations — choose what to scan"`
  (price still shown). When `expiry_choice` is set and `expirations_scanned` and
  `expiration_count` are real numbers, append `"Scanned 19 of 56 expirations · Monthlies
  only"` as its own part, and expose `"can_change": True` in the facts.
- `no_data_label`: on `needs_choice` → `"Choose which expirations to scan for $SPX."`
  (before every other branch except `error`).

Tests for each, including an old payload (no new keys) rendering exactly as before.

Commit `feat(finder): the chooser's words and the scanned-of-listed line`.

---

### Task 4: Page wiring — chooser card, remembered pick, Change, DTE max box

**Files:** `webgui/pages/options/swing.py`; test `webgui/tests/test_options_swing.py`.

1. On an answer with `needs_choice`, the top-pick grid shows ONE chooser card built from
   `fv.chooser_facts` (title, prompt, four `BTN` buttons, disabled when `enabled` is
   False); no placeholders, no still card; the list shows the chooser's empty line.
2. A button click → remember `state["choice_by_symbol"][SYMBOL] = key` → re-send the scan
   with the current bar's params plus `expiry_choice=key` (through `_request_scan`, so the
   in-flight dedupe, spinner and stale guard all apply).
3. `_request_scan` adds `expiry_choice` from `choice_by_symbol` for the symbol being
   scanned (absent key when none remembered). A different symbol sends none.
4. The summary strip shows the "Scanned N of M" part; when `can_change`, a small **Change**
   link (a `ui.link`-looking button, Tailwind only) re-renders the chooser card from the
   last answer's `choices` without scanning; picking from it behaves as in 2.
5. **DTE max box:** widen so the *no limit* placeholder is not clipped (measure the
   rendered width in the harness; pick the smallest Tailwind width that fits, e.g.
   `w-24`/`w-28`) — keep DTE min the same width for alignment.

Tests: chooser renders on a `needs_choice` payload with four buttons (one disabled at 0);
clicking sends `expiry_choice`; the same symbol's next scan sends the remembered choice; a
new symbol sends none; the scanned-of line and Change reopen the chooser without a
`bus_client.request`; stale guard: a chooser answer for another symbol does not paint
during a scan; DTE min/max width classes.

Commit `feat(finder): choose what to load for a large chain; the DTE max box fits`.

---

### Task 5: Docs and live timings

**Files:** `webgui/page_help.py`, `docs/manuals/user-guide/user-guide.md`,
`docs/manuals/reference-guide/reference-guide.md`,
`docs/manuals/technical-reference/technical-reference.md`,
`docs/manuals/api-reference/api-reference.md`, `docs/webgui-routes.md`,
`docs/CHANGELOG.md` (new top entry; demote the whole-chain entry to *Prior —* and fill its
"Still to measure" with the live numbers from the design), `CLAUDE.md` (route row and the
whole-chain note, in place), the design docs' "about 20 s" lines if they still say so.

- Replace every "about 20 s" scan-time claim with the live figures (whole chain: $SPX
  ~40 s, SPY ~26 s, NVDA ~14 s) and say that a large chain (more than 30 expirations in
  range) now asks first, with the four choices and their meaning.
- API reference: the `expiry_choice` arg and the new payload keys.
- Rebuild manuals; commit only content changes.

Commit `docs(finder): choose what to load for a large chain; live scan times`.

---

### Task 6: Verify

1. Suites: options_svc, webgui, options-scanner, shared/tests, tests, tools/tests; ruff.
2. Harness: typed 40-expiry synthetic list (W/S/Q/M) and chain; the chooser shows the right
   counts; *Monthlies only* scans only monthlies and shows "Scanned N of 40"; rescanning the
   same symbol does not ask; a small symbol range (1–2 wk) does not ask; the DTE max box
   shows *no limit* in full.
3. Final whole-branch review, then the operator decides on merge and promote.
4. After promotion: live $SPX and SPY — the chooser counts match the design's table; wall
   time for *Monthlies only* and *Next 30 days*; no errors in the journal.
