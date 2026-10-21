# Trade ideas in the Paper Ledger + weekly signal review — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Book every posted hourly trade idea into the Paper Ledger as a "Trade idea" row, follow it every 5 minutes to its target, stop or expiry while recording its worst point, show it on `/eod`, and write a weekly Claude-judged review on a server timer.

**Architecture:** A new `services/options_svc/idea_ledger.py` owns the idea rows end to end (build, book, mark, close, settle), writing through `paper_trader` with no cap check and excluded from every existing Ledger path. A new 5-minute scheduler branch drives it. `/eod` reads the rows from the existing `options:paper_trades` view. The weekly review is a pure stats module (`shared/idea_review.py`) plus a `tools/weekly_idea_review.py` oneshot on a systemd timer that calls Claude through `claude_cli.CliMessagesClient`.

**Tech Stack:** Python 3.11, SQLite (`trades.db`), Redis bus, NiceGUI, systemd user units, pytest + fakeredis.

**Spec:** [docs/plans/2026-10-20-trade-idea-paper-tracking-design.md](2026-10-20-trade-idea-paper-tracking-design.md) — read it first; this plan argues from it.

## Global Constraints

- Every Ledger mutation runs under `paper_lock.BOOK_LOCK` (`@_paper_lock.serialized`).
- Booking never raises into `run_trade_idea` and never refuses: no `book_caps`, no structure-name refusal.
- Idea rows are identified by `source == "trade_idea"` and nothing else.
- Every tunable lives in `config/*.toml` and has a `webgui/config_schema.py` `Field`.
- A mark that cannot be made is skipped, never written as zero.
- Settlement uses `paper_engine.should_settle` and `paper_engine.settlement_underlying` only.
- No Telegram, Discord, X or public-site output anywhere in this feature.
- The weekly judgment uses `CliMessagesClient` directly; never `FallbackClient`, never `ANTHROPIC_API_KEY`.
- `compute.py` line count may not grow beyond its ceiling: new service code goes in `idea_ledger.py`, which imports nothing from `compute`.
- Styling in webgui: Tailwind classes only (`test_no_inline_style.py`), page kit only (`test_ui_kit_guard.py`).
- Tests: service suite from repo root `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests -q -rf`; options-scanner from inside `options-scanner/` with `-m pytest tests -q -rf`; webgui from inside `webgui/`. Record the failing SET before Task 1 and compare against it at the end.
- Write test files with the editor, never a heredoc.

## Review Focus

1. **A slot that runs twice** (service restart inside the 10-minute grace) must not book the same post twice — test in Task 3 (`test_book_is_idempotent_per_post`).
2. **The same trade re-posted on a later day while the first is open** must book a SECOND row — test in Task 3 (`test_repost_on_later_day_books_second_row`).
3. **A mark crossing both the stop and the target in one cycle** (gap) must close at STOP — test in Task 5 (`test_stop_checked_before_target`).
4. **The user's own book unchanged** with idea rows present: caps, equity, generic exits, `Delete closed trades`, `/eod` "Manual paper" — tests in Tasks 4, 7, 8.
5. **Claude citing a fact id that does not exist, or a number not in the facts** — test in Task 10 (`test_unknown_citation_drops_suggestion`, `test_judgment_numbers_must_appear_in_facts`).

---

### Task 1: Configuration

**Files:**
- Modify: `config/paper.toml` (new `[idea_trades]` table)
- Modify: `shared/paper_limits.py` (DEFAULTS + accessors)
- Modify: `config/sessions.toml` (new `[slots.idea_review]`)
- Modify: `webgui/config_schema.py` (fields under the existing "Paper books" catalogue entry, ~:1563; `slots.idea_review.at` beside `slots.eod_report.at`)
- Test: `shared/tests/test_paper_limits.py`, `webgui/tests/test_config_schema.py` (existing — it fails on an uncatalogued key)

**Interfaces:**
- Produces: `paper_limits.idea_trades() -> dict` with keys `enabled: bool`, `quantity: int`, `mark_interval_min: int`, `debit_stop_frac: float`, `clean_mae_pct: float`, `min_n: int`, `review_model: str`. Bad/missing values fall back to defaults; never raises.

- [ ] **Step 1: Write the failing tests**

```python
def test_idea_trades_defaults():
    cfg = paper_limits.idea_trades()
    assert cfg == {"enabled": True, "quantity": 1, "mark_interval_min": 5,
                   "debit_stop_frac": 0.60, "clean_mae_pct": 25.0, "min_n": 5,
                   "review_model": "sonnet"}

@pytest.mark.parametrize("key,bad", [("quantity", 0), ("mark_interval_min", -5),
                                     ("debit_stop_frac", 1.5), ("debit_stop_frac", float("nan")),
                                     ("min_n", "x"), ("enabled", "yes")])
def test_idea_trades_bad_value_falls_back(monkeypatch, key, bad):
    # monkeypatch paper_limits.load to return {"idea_trades": {key: bad}}
    assert paper_limits.idea_trades()[key] == paper_limits.DEFAULTS["idea_trades"][key]
```

`debit_stop_frac` valid range is (0, 1]. `mark_interval_min` valid range 1..60.

- [ ] **Step 2: Run, expect FAIL** (`AttributeError: idea_trades`)
- [ ] **Step 3: Implement** `DEFAULTS["idea_trades"]`, `idea_trades()`, the `[idea_trades]` table with a comment per key in `config/paper.toml`, `[slots.idea_review] at = "15:30"` in `config/sessions.toml` with a comment saying systemd reads it (the `[slots.eod_report]` comment's wording), and the schema `Field`s ("Trade ideas in the Paper Ledger" section).
- [ ] **Step 4: Run** `shared/tests/test_paper_limits.py` and `webgui/tests/test_config_schema.py` — PASS
- [ ] **Step 5: Commit** `feat(paper): [idea_trades] config for trade ideas in the Ledger`

---

### Task 2: Ledger columns

**Files:**
- Modify: `options-scanner/trades_db.py` (SCHEMA, `_TRADE_COLUMNS`, `init_db` migration list)
- Test: `options-scanner/tests/test_trades_db.py`

**Interfaces:**
- Produces columns (TEXT unless noted): `source`, `idea_id`, `idea_posted`, `idea_grade`, `idea_score` REAL, `idea_label`, `target_pnl` REAL, `stop_pnl` REAL, `mae_pnl` REAL, `mae_at`, `last_mark_pnl` REAL, `last_mark_at`, `entry_source`. (The design's `idea_trade_type` and `idea_spot` are the existing `trade_type` and `underlying_at_entry` columns — no duplicates.)

- [ ] **Step 1: Failing tests** — `test_idea_columns_round_trip` (insert a dict with every new key, `fetch_one` returns them unchanged) and `test_idea_columns_migrate_old_db` (create a DB with the pre-change SCHEMA, run `init_db`, the new columns exist and an old row reads `source is None`).
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement** — append to SCHEMA, `_TRADE_COLUMNS` and the migration tuple.
- [ ] **Step 4: Run** `tests/test_trades_db.py` and the whole options-scanner suite — PASS
- [ ] **Step 5: Commit** `feat(ledger): columns for trade-idea rows`

---

### Task 3: Build and book an idea row

**Files:**
- Create: `services/options_svc/idea_ledger.py`
- Test: `services/options_svc/tests/test_idea_ledger.py`

**Interfaces:**
- Consumes: `paper_limits.idea_trades()` (Task 1); `site_ideas.entry_facts(idea, posted=now)`, `site_ideas.exit_levels(facts) -> (target, stop)`; `paper_trader.add_trade`, `paper_trader.get_all_trades`.
- Produces:
  - `SOURCE = "trade_idea"`
  - `is_idea(trade: dict) -> bool`
  - `levels(idea: dict, cfg: dict) -> tuple[float | None, float | None]` — `(target_pnl, stop_pnl)` per contract, positive numbers.
  - `build_row(idea: dict, now: datetime, cfg: dict) -> dict` — PURE.
  - `book(idea: dict, now: datetime) -> dict` — `@_paper_lock.serialized`; returns `{"status": "booked"|"duplicate"|"disabled", "trade_id": str|None}`.

`build_row` values (from the design, section 2): `trade_id` = 8-char uuid; `mode "PAPER"`; `status "OPEN"`; `strategy = idea["type"]`; `trade_type = idea["trade_type"] or ""`; `dte_at_entry = idea["dte"]`; `quantity = cfg["quantity"]`; `legs = idea["legs"]` (always, credit too); `entry_credit = idea["entry_cash"] / 100` (signed per share, commission in); `entry_credit_total = entry_cash * qty`; `direction = "DEBIT"` when `entry_cash < 0` else `None`; for a debit `entry_debit = -entry_cash`, `entry_debit_total = -entry_cash * qty`; `max_loss_per = max_loss / 100`; `max_loss_total = max_loss * qty`; `max_profit_total = max_profit * qty` or `None` with `unbounded = True`; for PCS/CCS fill `short_strike`, `long_strike`, `width` from the short/long legs, for IC also `call_short`, `call_long`; `breakeven` = the breakevens joined `" / "`; `underlying_at_entry = idea["spot"]`; `entry_time = idea_posted = now.isoformat(timespec="seconds")`; `idea_id`, `idea_grade`, `idea_score`, `idea_label`; `target_pnl`, `stop_pnl` from `levels`; `mae_pnl = 0.0`; `entry_source = "card"`; `notes = "Trade idea"`.

`levels`: `exit_levels(entry_facts(idea, posted=now))`; when its stop is `None` AND `entry_cash < 0`, stop = `round(cfg["debit_stop_frac"] * -entry_cash, 2)`.

`book` dedupe key is `(idea_id, idea_posted)` against existing rows.

- [ ] **Step 1: Failing tests**

```python
def test_build_row_debit_gets_own_stop():            # LONG_CALL, entry_cash -250, no tp levels stop
    row = idea_ledger.build_row(LONG_CALL_IDEA, NOW, CFG)
    assert row["source"] == "trade_idea" and row["direction"] == "DEBIT"
    assert row["entry_credit"] == -2.50 and row["stop_pnl"] == 150.0
    assert row["target_pnl"] == site_ideas.exit_levels(site_ideas.entry_facts(LONG_CALL_IDEA, posted=NOW))[0]

def test_build_row_credit_keeps_its_stop_and_strikes():   # PCS: stop = stop_mult x credit
def test_build_row_backspread_has_no_target_and_own_stop():
def test_build_row_straddle_is_bookable():            # LONG_STRADDLE builds, legs stored
def test_book_bypasses_caps(monkeypatch):            # book_caps.evaluate patched to raise -> still booked
def test_book_is_idempotent_per_post():               # book twice same idea+now -> second "duplicate", 1 row
def test_repost_on_later_day_books_second_row():      # same idea id, now+1 day -> 2 OPEN rows
def test_book_disabled_books_nothing():               # cfg enabled False -> "disabled", 0 rows
def test_book_takes_the_book_lock():                  # paper_lock.BOOK_LOCK held during add_trade
```

Fixtures: build idea dicts with `trade_idea.normalize` from scanner-row fixtures already in `test_trade_idea.py` so the shapes are real. Redirect `trades_db.DEFAULT_DB_PATH` to `tmp_path`.

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement** in `idea_ledger.py`. Import `paper_trader`, `paper_lock` the way `compute.py` does (options-scanner on `sys.path` via `repo_paths.OPTIONS_SCANNER`); import nothing from `compute`.
- [ ] **Step 4: Run** `services/options_svc/tests/test_idea_ledger.py` — PASS
- [ ] **Step 5: Commit** `feat(ideas): book each posted idea into the Paper Ledger`

---

### Task 4: Hook the post, and keep idea rows out of the user's book

**Files:**
- Modify: `services/options_svc/handlers.py` (`run_trade_idea` `if sent:` branch ~:2939; new `_book_trade_idea_ledger(idea, now) -> dict`)
- Modify: `services/options_svc/compute.py` — `ledger_book_state` (skip `is_idea`), `manage_ledger_trades` (skip), `expire_ledger_trades` (skip), `_reprice_open_pnl` (for an idea row set `unrealized_pnl = last_mark_pnl * quantity` when present, never fetch)
- Modify: `options-scanner/paper_trader.py` — `delete_closed_trades` spares `source == "trade_idea"` rows
- Modify: `options-scanner/tests/test_straddle_analysis_only.py` (add one case)
- Test: `services/options_svc/tests/test_idea_ledger_wiring.py`, `options-scanner/tests/test_paper_trader_tracking.py` (or the delete test file in that suite)

**Interfaces:**
- Consumes: `idea_ledger.book`, `idea_ledger.is_idea` (Task 3).
- Produces: `result["ledger"]` in `cache:options:trade_idea` = `book`'s dict, or `{"status": "failed"}`.

- [ ] **Step 1: Failing tests**

```python
def test_posted_idea_is_booked(fake_bus, ...):        # run_trade_idea with send stubbed True -> 1 idea row, result["ledger"]["status"] == "booked"
def test_unsent_idea_is_not_booked():                 # send False -> 0 rows
def test_booking_failure_never_costs_the_post(monkeypatch):  # book raises -> status still "posted", ledger "failed", degrade counted
def test_idea_rows_excluded_from_caps_and_equity():    # ledger_book_state over [manual OPEN, idea OPEN, idea CLOSED +500] -> open has 1 row, equity ignores +500
def test_manage_ledger_skips_idea_rows():             # reprice_legs patched to raise if called for an idea row
def test_expire_ledger_skips_idea_rows():
def test_reprice_view_uses_last_mark_for_idea_rows(): # no chain fetch, unrealized = last_mark_pnl * qty
def test_delete_closed_spares_idea_rows():            # 1 manual closed + 1 idea closed -> returns 1, idea row remains
def test_idea_straddle_row_is_stored_but_button_still_refuses():   # in test_straddle_analysis_only.py
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement.** `_book_trade_idea_ledger` follows `_publish_trade_idea_site`'s guard shape and calls `_degrade.degraded("options.run_trade_idea.ledger")` on failure. `delete_closed_trades` returns the count actually deleted (excluding spared rows); implement with a SQL `source IS NOT 'trade_idea'` filter in a new `trades_db.delete_closed_except_source(conn, source)` rather than filtering in Python after an unconditional delete.
- [ ] **Step 4: Run** both suites — PASS; existing ledger tests (`test_ledger_caps*.py`, `test_ledger_manage_*.py`) still PASS unchanged.
- [ ] **Step 5: Commit** `feat(ideas): book on post; idea rows outside the user's caps, exits and deletes`

---

### Task 5: The 5-minute idea cycle

**Files:**
- Modify: `services/options_svc/idea_ledger.py`
- Modify: `services/options_svc/scheduler.py` (new `idea_marks_due(now, last_slot, interval_min)`, a branch beside `captured_manage`)
- Modify: `services/options_svc/handlers.py` (new `run_idea_marks_and_publish(bus) -> None`: cycle then `refresh_paper_trades(bus)`)
- Test: `services/options_svc/tests/test_idea_ledger.py`, `services/options_svc/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `structure_marks.reprice(row, client, today)` (row needs `legs_json`: pass `{**t, "legs_json": t["legs"]}`), `structure_marks.pnl_of(row, value)`, `structure_marks.expiry_value(row, spot)`, `paper_engine.should_settle(exp, today, now_ct)`, `paper_engine.settlement_underlying(client, symbol, expiration, today)`, client = `services._proxy.schwab_py_client`.
- Produces:
  - `close_row(t: dict, value: float, reason: str, when: datetime) -> dict` — PURE; the CLOSED (or EXPIRED for `reason == "EXPIRED"`) row: `exit_debit = value`, `exit_debit_total = value * 100 * qty`, `realized_pnl = pnl_of(t, value) * qty`, `exit_time`, `exit_reason`.
  - `apply_mark(t: dict, pnl: float, when: datetime) -> tuple[dict, str | None]` — PURE; updates `last_mark_*`, lowers `mae_pnl`/`mae_at` when `pnl < mae_pnl`, returns `(row, "STOP"|"TARGET"|None)`; STOP when `stop_pnl` set and `pnl <= -stop_pnl`, checked BEFORE TARGET (`target_pnl` set and `pnl >= target_pnl`).
  - `mark_cycle(now_ct: datetime, client=None, *, settle_only: bool = False) -> dict` — `@serialized`; `{"marked": n, "closed": n, "settled": n, "skipped": n}`. `settle_only=True` settles and marks nothing: `handlers.run_paper_settle` (the 15:05 CT `[slots.paper_settle]` pass) calls it that way, because the 5-minute cycle stops at 15:00 and expiry-day settlement is due at/after 15:00.

Exit commission: check what `idea["entry_cash"]`'s `commission` field covers (`scanner_engine` / `structure_scan` row builders). If it is the round trip, `close_row` adds nothing; if it is the opening side only, `close_row` subtracts the closing commission from `services/options_svc/commission` (per contract × qty). Pin whichever it is in a test named for it.

Cycle order per OPEN idea row: settle if `should_settle` (value = `expiry_value(row, sp)`; `sp is None` → defer, count `skipped`), else reprice → no mark/`error` → `skipped` (row untouched) → `apply_mark` → close at the mark's value on STOP/TARGET. Write each row with `paper_trader.update_trade`. A row that raises is logged and skipped; the pass continues.

Scheduler: trading day, regular session (08:30–15:00 CT — `shared.market_calendar` predicates), one slot per `mark_interval_min`. Before fixing the minute offsets, read the proxy's access log (CLAUDE.md, "Every service shares the proxy's 5 requests a second") and skip a slot whose minute falls in :00, :02-:05, :17-:20, :30, :32-:35, :47-:50 — the next slot catches up. Put the forbidden minutes in a module constant with that comment.

- [ ] **Step 1: Failing tests**

```python
def test_mae_only_moves_down():                       # marks -40, -10, -80 -> mae -80 at third mark time
def test_mae_stays_zero_when_never_negative():
def test_stop_checked_before_target():                # pnl that satisfies both (target None vs edge) -> "STOP"
def test_target_closes_at_mark_value_not_level():     # pnl 180 vs target 125 -> realized 180*qty
def test_no_mark_leaves_row_untouched():               # reprice returns error -> row equal before/after, skipped 1
def test_expiry_settles_every_posted_structure(...):  # parametrize over STRATEGY_LABELS keys with bounded loss
def test_expiry_deferred_without_settlement_price():
def test_paper_settle_slot_settles_idea_rows():       # run_paper_settle at 15:05 on expiry day -> idea row EXPIRED, no reprice called
def test_cycle_continues_after_one_row_raises():
def test_idea_marks_due_slots_and_gate():             # 5-min slots, false on weekend/holiday/16:00, forbidden minutes skipped
def test_exit_commission_basis():                     # per the check above
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** service suite — PASS
- [ ] **Step 5: Commit** `feat(ideas): 5-minute marks, worst point, target/stop/expiry exits`

---

### Task 6: Manual close of an idea row

**Files:**
- Modify: `services/options_svc/compute.py` `close_paper` (~:2916): when `idea_ledger.is_idea(t)`, call `idea_ledger.manual_close(t, debit)` instead of `paper_trader.close_paper_trade`
- Modify: `services/options_svc/idea_ledger.py`
- Test: `services/options_svc/tests/test_idea_ledger.py`

**Interfaces:**
- Produces: `manual_close(t: dict, debit: float, when: datetime | None = None) -> dict` — the page's `debit` is the positive price typed in the close prompt; cost-to-close `value = debit` for a credit row (`entry_credit > 0`) and `value = -debit` for a debit row; returns `close_row(t, value, "MANUAL", when)`.

- [ ] **Step 1: Failing tests** — `test_manual_close_credit_row`, `test_manual_close_debit_row` (realized P&L equals the sign-correct `(entry_credit − value) × 100 × qty`), `test_close_paper_routes_idea_rows` (`close_paper_trade` patched to raise for an idea row).
- [ ] **Steps 2–4: Run FAIL → implement → run PASS**
- [ ] **Step 5: Commit** `feat(ideas): a hand close of an idea row books as MANUAL`

---

### Task 7: Paper Ledger page — tag, filter, delete wording

**Files:**
- Modify: `webgui/pages/options/paper.py` (`paper_rows` adds `"tag": "Trade idea"` for `source == "trade_idea"` else `""`; new pure `filter_rows(rows, which: str) -> list` with `which` in `("all", "mine", "ideas")`; a `kit` toggle in the header; `ledger_status` counts only the shown rows; the Delete-closed confirm says idea trades are kept; the per-row Delete confirm on an idea row says it removes the trade from the weekly review)
- Modify: `webgui/page_help.py` (Paper Ledger guide)
- Test: `webgui/tests/test_options_paper.py`

- [ ] **Step 1: Failing tests** — `test_paper_rows_tags_idea_rows`, `test_filter_rows_three_ways`, `test_delete_closed_confirm_mentions_trade_ideas_kept`, `test_page_help_describes_trade_ideas` (the guide names the tag, the 5-minute check and that the limits do not apply).
- [ ] **Steps 2–4: Run FAIL → implement → run PASS** (plus `test_no_inline_style.py`, `test_ui_kit_guard.py`)
- [ ] **Step 5: Verify in a browser** with `tools/ui_harness.py` on the Paper Ledger page, seeded with one manual and two idea rows: the toggle filters, the tag shows. Stop the harness. Screenshot for the handoff.
- [ ] **Step 6: Commit** `feat(ledger page): Trade idea tag and filter`

---

### Task 8: `/eod` — the "Trade ideas" book and section

**Files:**
- Modify: `webgui/pages/eod.py`
- Modify: `webgui/page_help.py` (EOD guide)
- Test: `webgui/tests/test_eod.py`

**Interfaces:**
- Produces (pure, in `eod.py`):
  - `split_ledger(trades) -> tuple[list, list]` — `(manual, ideas)` by `source`.
  - `idea_rows(trades, today: date) -> dict` with keys `posted_today`, `closed_today_earlier`, `open` — each a list of display dicts: `posted` (HH:MM), `symbol`, `strategy`, `grade`, `entry` (`entry_credit_total`), `exit` (`exit_debit_total` or `None`), `reason`, `held` (`exit_time − idea_posted` as `"2h 05m"` / `"3d 4h"`, open rows: to now), `mae` (`mae_pnl × qty`), `mae_pct` (`mae_pnl / (max_loss_per × 100) × 100`), `pnl`.
  - `idea_section(paper_trades_cache, today) -> str` — the HTML section: the three tables, then closed P&L totals for day / week / month (`period_buckets` over the idea rows) and an "Open trades, unrealized" line that is NOT added to the totals.
- `_books` returns three books: `("Manual paper", manual…)`, `("Captured signals", …)`, `("Trade ideas", ideas, None)`; `_book_slug("Trade ideas") == "trade"`.
- `detail_fragment` gains a `("trade-ideas", "Trade ideas")` nav entry and `details_section`; `summary_fragment` gains one tile: today's closed idea P&L and the count closed.

- [ ] **Step 1: Failing tests** — `test_manual_book_excludes_idea_rows`, `test_trade_ideas_is_third_book`, `test_idea_rows_buckets` (posted today / closed today from Monday / still open), `test_held_formats`, `test_mae_pct_of_max_loss`, `test_open_unrealized_not_in_period_totals`, `test_idea_section_empty_says_no_trade_ideas_yet`, `test_summary_has_idea_tile`.
- [ ] **Steps 2–4: Run FAIL → implement → run PASS** (whole webgui suite: `test_eod.py`'s HTML assertions and `tools/tests/test_generate_eod_report.py`)
- [ ] **Step 5: Verify in a browser** — harness `/eod` with a seeded `options:paper_trades`, Generate, open the detail; screenshot.
- [ ] **Step 6: Commit** `feat(eod): Trade ideas book and section`

---

### Task 9: Weekly review — the numbers (pure)

**Files:**
- Create: `shared/idea_review.py` (stdlib + `shared.config_toml`-free: takes cfg as an argument)
- Test: `shared/tests/test_idea_review.py`

**Interfaces:**
- Produces:
  - `rate(t: dict, clean_mae_pct: float) -> str` — one of `"clean win"`, `"win after drawdown"`, `"stopped"`, `"expired win"`, `"expired loss"`, `"closed by hand"`. TARGET with `mae_pct > -clean_mae_pct` → clean win, else win after drawdown; STOP → stopped; EXPIRED by sign of `realized_pnl` (0 counts as loss); MANUAL → closed by hand.
  - `week_bounds(d: date) -> tuple[date, date]` — Monday..Sunday of `d`'s ISO week.
  - `is_review_day(d: date) -> bool` — `is_trading_day(d)` and `next_trading_day(d)` in a later ISO week (import from `shared.market_calendar`).
  - `facts(trades: list[dict], today: date, cfg: dict) -> dict` — `{"week": {...}, "all_time": {...}, "open": [...], "closed_week": [...], "repeats": [...]}`. Each group table is a list of fact dicts `{"id": "week.grade.Strong", "n", "targets", "stops", "expired", "manual", "win_rate", "avg_pnl", "total_pnl", "avg_mae_pct", "avg_held_h", "thin": n < min_n}` for dims `grade`, `strategy`, `trade_type`, `score_band` (`<60`, `60-69`, `70-79`, `80+`), `post_hour` (CT hour of `idea_posted`), `symbol`. Closed means `status != "OPEN"`, week membership by `exit_time` date for closed and listed in `open` regardless. `repeats`: `idea_id`s booked more than once in the week, with their count.
  - `fact_ids(f: dict) -> set[str]`, `fact_numbers(f: dict) -> set[str]` — every number rendered the way the report prints it (`fmt` two decimals for money/pct, integer counts), for the verification in Task 10.

- [ ] **Step 1: Failing tests** — `test_rate_each_outcome` (parametrize six), `test_is_review_day_friday_and_holiday_friday` (an ordinary Friday is the review day and its Thursday is not; for a full-day Friday holiday taken from `shared.market_calendar`'s own holiday table, the Thursday before is the review day; a Saturday never is), `test_facts_groups_and_thin_flag`, `test_week_membership_by_exit_date`, `test_open_trades_listed_not_graded`, `test_repeats_counted`, `test_empty_book_gives_empty_facts_not_error`.
- [ ] **Steps 2–4: Run FAIL → implement → run PASS**
- [ ] **Step 5: Commit** `feat(review): weekly idea-trade facts and ratings`

---

### Task 10: Weekly review — the tool, Claude's judgment, the archive

**Files:**
- Create: `tools/weekly_idea_review.py`
- Create: `tools/prompts/weekly_idea_review.md` (the system prompt: plain English, whole words, judge only from the facts, cite fact ids, say when a pattern is too thin, and write no number that is not in the facts — no dates or times either, since `verify` drops any)
- Modify: `.gitignore` (`webgui/data/idea_review/`) — the backup list is derived from it; run its test
- Test: `tools/tests/test_weekly_idea_review.py`

**Interfaces:**
- Consumes: `idea_review.*` (Task 9); the rows from `pages.eod.read_snapshot()["paper_trades"]["trades"]` (webgui on `sys.path`, exactly as `tools/generate_eod_report.py` does, so the two tools read the bus one way); `CliMessagesClient` from `services.options_svc.claude_cli`; `pages.eod.build_eod_css`, `pages.eod.wrap_document`.
- Produces:
  - `ARCHIVE_ROOT = repo/webgui/data/idea_review`, one folder per `YYYY-Www` with `report.html`.
  - `JUDGMENT_SCHEMA` — `{"assessment": [str], "suggestions": [{"text": str, "cites": [str]}]}`, all required.
  - `verify(judgment: dict, f: dict, min_n: int) -> dict` — drops a suggestion whose `cites` is empty or names an id not in `fact_ids(f)`; drops any assessment paragraph or suggestion containing a number not in `fact_numbers(f)`; stamps each kept suggestion with the summed `n` of its cites and `thin` when every cite is thin.
  - `render(f: dict, judgment: dict | None, week: str) -> str` — full document: Assessment, Suggestions (with "based on N trades", thin ones labelled "too few trades to act on"), This week's closed trades (with rating, entry, exit, reason, held, MAE), Open trades, then week and all-time group tables. `judgment None` → one line "Claude's assessment is missing this week; the numbers below are complete."
  - `main(argv) -> int` — `--date YYYY-MM-DD` (default today CT), `--force` (skip the review-day gate). Not a review day → print one line, return 0. Bus empty/unreadable → write nothing, return 2. Claude fails (`CliUnusable` or any exception) → write the numbers-only report, return 3. Success → atomic write, return 0.
- The Claude call: `client.messages.create(model=cfg["review_model"], max_tokens=4000, system=<prompt file>, messages=[{"role": "user", "content": json.dumps(facts)}], tools=[{"name": "weekly_judgment", "input_schema": JUDGMENT_SCHEMA}], tool_choice={"type": "tool", "name": "weekly_judgment"})`; read `content[0].input`.

- [ ] **Step 1: Failing tests**

```python
def test_not_review_day_writes_nothing(tmp_path):
def test_unknown_citation_drops_suggestion():
def test_judgment_numbers_must_appear_in_facts():      # "won 9 of 10" with no 9 in facts -> dropped
def test_thin_suggestion_labelled():
def test_claude_failure_writes_numbers_only_and_exits_3(fake_cli_raises):
def test_empty_bus_writes_nothing_and_exits_2():
def test_never_uses_fallback_client():                 # source scan: no "FallbackClient" / "ANTHROPIC_API_KEY" in the tool
def test_report_lists_closed_and_open_separately():
```

Use a fake `run=` callable in `CliMessagesClient(run=...)` that returns a stream-json transcript with `apiKeySource: "none"` and a `structured_output` (see `services/options_svc/tests/test_claude_cli.py` for the shape).

- [ ] **Steps 2–4: Run FAIL → implement → run PASS** (`tools/tests/`, and the backup-list test)
- [ ] **Step 5: Commit** `feat(review): weekly idea review tool with Claude's verified judgment`

---

### Task 11: The timer, and the page that shows the archive

**Files:**
- Modify: `deploy/systemd/generate_units.py` (new `_idea_review_units()` modelled on `_eod_report_units`; `OnCalendar=Mon..Fri *-*-* HH:MM:00` from `slot_times("idea_review")["at"]`; `Persistent=false`; `TimeoutStartSec=900` (the CLI's own timeout is 360 s); `EnvironmentFile` for the bus password; registered in `render_all`)
- Modify: `webgui/main.py` (`@_page("/eod/weekly")` inside `_layout("/eod", "Weekly idea review")`; `@app.get("/eod/weekly/file")` serving `report.html` for a `week` that matches `^\d{4}-W\d{2}$` only)
- Create: `webgui/pages/idea_review.py` (`render()`: list of weeks newest first via the `/eod` archive browser's pattern, frame the chosen one; empty → "No weekly review yet — the first one is written after the close on the last trading day of the week.")
- Modify: `webgui/tests/test_shell.py` (route), `webgui/tests/test_no_inline_style.py` (page)
- Test: `tests/test_systemd_units.py`, `webgui/tests/test_idea_review_page.py`

- [ ] **Step 1: Failing tests** — `test_idea_review_timer_from_slot`, `test_idea_review_service_runs_tool_with_env_file`, `test_weekly_file_route_refuses_bad_week` (`../x`, `2026-43`, empty), `test_weeks_listed_newest_first`, `test_empty_archive_message`.
- [ ] **Steps 2–4: Run FAIL → implement → run PASS**
- [ ] **Step 5: Verify in a browser** — harness the weekly page over a `tmp` archive holding two weeks written by Task 10's `render`; screenshot.
- [ ] **Step 6: Commit** `feat(review): weekly timer and the Weekly idea review page`

---

### Task 12: Manuals, routes doc, changelog — and the whole-suite check

**Files:**
- Modify: `docs/manuals/` User Guide (Paper Ledger: the tag, the filter, the exemption, delete behaviour; EOD: the section; the weekly review), Reference Guide (the two tabs), Technical Reference (5-minute cadence, MAE definition, the idea book's own debit stop, the rating thresholds, the Friday-or-last-trading-day rule)
- Modify: `docs/webgui-routes.md` (`/eod/weekly`), `docs/CHANGELOG.md` (dated entry)
- Rebuild: `python docs/manuals/build_docs.py`

- [ ] **Step 1:** Edit the manuals and docs; rebuild; the manuals tests pass.
- [ ] **Step 2:** Run every suite listed in `docs/reference/testing.md`; compare the failing SET and skipped SET with the one recorded before Task 1 — identical.
- [ ] **Step 3: Commit** `docs: trade ideas in the Ledger and the weekly review`

---

## Delivery (after the user's go-ahead, not part of the tasks)

Merge to `main`, push, and promote with the verbatim command from CLAUDE.md — **outside 06:30–15:20 CT on a trading day** (it restarts `options_svc`, which holds the GEX collector). Then `generate_units --install` + `systemctl --user daemon-reload` for the new timer. First live checks: the next 08:35 post creates one idea row; a 5-minute mark updates `last_mark_at`; `/eod` at 15:15 shows the section; the first Friday's 15:30 run writes a report (a `--force` dry run the evening of the promote proves the CLI token and the write path).
