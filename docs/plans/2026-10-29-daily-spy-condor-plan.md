# The daily SPY 0-DTE iron condor - implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Book one 15-delta, $5-wide SPY 0-DTE iron condor into the Paper Ledger at
08:45 CT each trading day, stopped at a loss of 2x its credit on the 5-minute idea
mark cycle, otherwise settled at the close.

**Architecture:** A new `services/options_svc/daily_condor.py` (pure picker + row
builder + a locked `book`) fired by a new scheduler slot. Its rows carry `source =
"daily_condor"`, and the trade-idea book mechanics (mark, stop, settle, exclusion
from the user's Ledger passes) are widened from `is_idea` to `is_self_managed`.

**Tech Stack:** Python 3.11, pytest, the options_svc scheduler, trades.db via
`paper_trader`.

**Spec:** [2026-10-29-daily-spy-condor-design.md](2026-10-29-daily-spy-condor-design.md)

## Global Constraints

- Source string: `"daily_condor"`. Tag on the Ledger page: `"SPY condor"`. Notes: `"Daily SPY condor"`.
- Config `config/paper.toml [daily_condor]`: `enabled = true`, `symbol = "SPY"`, `short_delta = 0.15`, `width = 5.0`, `stop_mult = 2.0`, `quantity = 1`.
- Slot `[slots.daily_condor]`: `grace_min = 10`, `at = "08:45"`.
- `stop_pnl = round((stop_mult * credit + k) * 100, 2)` per contract; `target_pnl = None`; `entry_credit = credit - k` (k = round-trip commission per share).
- The new module imports nothing from `compute` (its line count has a ceiling).
- No `.style(...)`, no raw `ui.*` widgets on the page change; Tailwind fixed classes only.

## Review Focus

1. A service restart at 08:50 on a day already booked must not book a second condor -> `test_book_refuses_a_second_condor_the_same_day`.
2. Schwab's `-999` deltas on every contract -> skip with reason, no row -> `test_pick_skips_when_no_usable_delta`.
3. A short strike with no strike exactly `width` beyond it -> skip -> `test_pick_skips_without_a_wing_at_width`.
4. A condor row reaching the user's manage / caps / expiry passes -> `test_self_managed_rows_are_skipped_by_ledger_passes`.
5. "Delete all closed" deleting a closed condor -> `test_delete_closed_clears_condor_rows`.

---

### Task 1: Configuration

**Files:**
- Modify: `shared/paper_limits.py` (DEFAULTS + `daily_condor()`), `config/paper.toml`, `shared/market_calendar.py` (`_DEFAULTS["slots"]["daily_condor"]`), `config/sessions.toml`, `webgui/config_schema.py`
- Test: `shared/tests/test_paper_limits_daily_condor.py`

**Interfaces:**
- Produces: `paper_limits.daily_condor() -> {"enabled": bool, "symbol": str, "short_delta": float, "width": float, "stop_mult": float, "quantity": int}`, every key falling back to its default on its own (as `idea_trades()` does). Bounds: `short_delta` (0, 0.5), `width` (0, 100], `stop_mult` (0, 20], `quantity` 1..10**6, `symbol` through `shared.symbols.clean_symbol`.
- Produces: `mc.slot_times("daily_condor") == {"at": time(8, 45)}`, `mc.slot_grace_min("daily_condor") == 10`.

- [ ] Write tests: defaults; a bad `short_delta` (0.7) alone reverts while the others keep; `symbol = "spy"` reads `"SPY"`; slot defaults.
- [ ] Run, see them fail. Implement. Run, see them pass.
- [ ] Schema fields for all six keys plus `slots.daily_condor.grace_min` / `slots.daily_condor.*`; run `webgui/tests/test_config_schema.py`.
- [ ] Commit.

### Task 2: Self-managed Ledger rows

**Files:**
- Modify: `services/options_svc/idea_ledger.py`, `services/options_svc/compute.py` (the six `_ideas.is_idea` sites), `services/options_svc/handlers.py` (`_carry_live_pnl`), `options-scanner/trades_db.py`, `options-scanner/paper_trader.py` (`delete_closed_trades`), `webgui/pages/options/paper.py`, `webgui/pages/eod.py`, `webgui/pages/symbol_facts.py`
- Test: `services/options_svc/tests/test_idea_ledger.py`, `services/options_svc/tests/test_idea_ledger_wiring.py`, `webgui/tests/` (the Ledger page's existing filter test file)

**Interfaces:**
- Produces: `idea_ledger.CONDOR_SOURCE = "daily_condor"`, `idea_ledger.SELF_MANAGED_SOURCES = frozenset({SOURCE, CONDOR_SOURCE})`, `idea_ledger.is_self_managed(t) -> bool`.
- `trades_db.SELF_MANAGED_SOURCES` twin (pinned equal by the wiring test); `delete_closed_except_sources(conn, sources)` and `clear_closed_of_sources(conn, sources, when)` replace the single-source pair.
- `mark_cycle` and `show_mark` act on `is_self_managed` rows. `is_idea` keeps its meaning (review, site results).
- Paper page: `CONDOR_TAG = "SPY condor"`; filter `mine` excludes both tags, `ideas` shows the idea tag only, `all` everything.

- [ ] Write failing tests: `test_self_managed_rows_are_skipped_by_ledger_passes` (a condor row is not repriced, not in `ledger_book_state`, not expired or managed by `expire_ledger_trades` / `manage_ledger_trades`); `test_mark_cycle_stops_a_condor_row` (a row with `stop_pnl` closes STOP when the mark's pnl <= -stop); `test_delete_closed_clears_condor_rows`; the page's filter test for a condor row.
- [ ] Run, fail. Implement. Run the options_svc and webgui suites' touched files, pass.
- [ ] Commit.

### Task 3: The picker, the row and the booking

**Files:**
- Create: `services/options_svc/daily_condor.py`
- Test: `services/options_svc/tests/test_daily_condor.py`

**Interfaces:**
- Consumes: `paper_limits.daily_condor()`, `idea_ledger.CONDOR_SOURCE`, `fill_model.realistic_vertical_fill`, `commission.round_trip_commission`, `shared.greeks.delta`, `signal_repricer._fetch_chain`, `paper_trader.add_trade` / `get_all_trades`, `paper_lock.serialized`.
- Produces: `pick(chain: dict, expiration: str, cfg: dict) -> dict` returning either `{"legs": [...4 legs...], "put_short", "put_long", "call_short", "call_long", "credit": float, "spot": float}` or `{"skip": "<reason>"}`; `build_row(pick: dict, now, cfg, commission_per_contract: float) -> dict`; `book(now, client=None) -> {"status": "booked"|"duplicate"|"disabled"|"skipped", "trade_id"|None, "reason"|None}`.
- Leg shape: `{"kind", "side", "strike", "expiration", "qty": 1}` (`structure_marks.legs_of`).
- Pick rule: among contracts whose `greeks.delta` is usable, the short with `abs(abs(delta) - short_delta)` smallest; a tie takes the strike farther from spot. The wing is the strike exactly `width` beyond (put: below, call: above); it must have a bid and an ask. Credit = put vertical + call vertical through `realistic_vertical_fill(..., "SELL_TO_OPEN")`, rounded to 2 places.

- [ ] Write failing tests on a synthetic chain (SPY 580, $1 strikes, deltas falling linearly): `test_pick_takes_the_15_delta_shorts_and_5_dollar_wings`, `test_pick_tie_takes_the_farther_strike`, `test_pick_skips_when_no_usable_delta`, `test_pick_skips_without_a_wing_at_width`, `test_pick_skips_a_leg_with_no_market`, `test_build_row_sets_the_price_stop` (credit 1.00, k 0.052 -> `entry_credit == 0.948`, `stop_pnl == 205.2`, `target_pnl is None`, `strategy == "IC"`, strikes on the IC fields), `test_book_refuses_a_second_condor_the_same_day`, `test_book_disabled`, `test_book_skips_a_holiday`.
- [ ] Run, fail. Implement. Run, pass.
- [ ] Commit.

### Task 4: The slot

**Files:**
- Modify: `services/options_svc/scheduler.py` (`daily_condor_due(now, ran_slots)` mirroring `trade_idea_due`, a branch in the tick), `services/options_svc/handlers.py` (`run_daily_condor(bus)`: `daily_condor.book(_now_ct())`, then `refresh_paper_trades(bus, reprice=False, keep_live_pnl=True)` when booked)
- Test: `services/options_svc/tests/test_daily_condor.py`

- [ ] Failing tests: due at 08:45 and 08:54 on a trading day, not at 08:56, not on a Saturday, not twice for one day.
- [ ] Implement, pass, commit.

### Task 5: Docs and verification

- [ ] `docs/manuals/` User Guide + Technical Reference (Paper Ledger section), `webgui/page_help.py` (the Ledger's guide), `docs/CHANGELOG.md`.
- [ ] Run the options_svc, shared, options-scanner and webgui suites; compare the failing set to none.
- [ ] Render the Ledger page with a condor row in `tools/ui_harness.py`, screenshot.
- [ ] Commit.
