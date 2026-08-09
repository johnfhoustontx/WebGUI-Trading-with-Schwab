# Captured-trade auto-management — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn captured signals from advisory into an autonomously auto-managed paper book —
raise the stop to break-even after +50%, defer delta-drift cuts on recoverable trades,
auto-close on the recommender's exit codes / expiry, and roll the day's closed trades into
the EOD report.

**Architecture:** Rework the pure `recommend()` into a stateful lifecycle (`be_armed` +
recovery inputs); add an `options_svc` 5-min captured-manage cycle (reprice → arm → recommend
→ auto-close → settle → publish); persist `be_armed` on `signals`; publish a new
`cache:options:captured_closed` view the Tier-1 `/eod` page renders; gate it all behind a
`captured_autoclose_enabled` Settings toggle written through to the service.

**Tech Stack:** Python 3.11, pytest (TDD), SQLite (`signals.db`), Redis/Memurai bus,
NiceGUI. Design: `docs/plans/2026-08-09-captured-autoclose-breakeven-design.md`.

**Test commands:**
- options-scanner: `cd options-scanner && ..\.venv\Scripts\python -m pytest tests\<file> -q`
- options_svc: `.venv\Scripts\python -m pytest services\options_svc\tests\<file> -q`
- webgui: `cd webgui && ..\.venv\Scripts\python -m pytest tests\<file> -q`

**Baselines to hold:** options-scanner has documented pre-existing failures (compare the
failing SET, not the count). options_svc has 2 date-relative `test_expected_move` fails.

---

### Task 1: Recommender lifecycle (break-even arming + recovery + hard floors)

**Files:**
- Modify: `options-scanner/signal_recommender.py` (`recommend()` + constants)
- Test: `options-scanner/tests/test_signal_recommender.py`

New constants: `RECOVERY_DTE_MIN = 5`, `RECOVERY_MIN_CUSHION = 0.015`. New close code
`BREAKEVEN_STOP`. `recommend(ctx)` gains ctx keys: `be_armed` (bool), `be_level` ($, the
break-even+commissions exit floor), `spot`, `short_strike`, `call_short` (IC), `strategy`
(PCS/CCS/IC). Keep back-compat: missing new keys ⇒ old behavior degrades sensibly
(`be_armed` false, recovery off when spot/strike absent).

**Precedence** (see design §"Exit logic"):
1. money-stop (hard) → `MONEY_STOP`
2. time-stop (hard, DTE≤`CUT_DTE` & underwater) → `TIME_STOP`
3. if `be_armed` and `pnl ≤ be_level` → `BREAKEVEN_STOP`
4. delta-stop breach → `DELTA_STOP` **unless** `_recoverable(ctx)` → HOLD ("recovery: …")
5. if `pnl ≥ TP_FRAC·credit_total` and not armed → HOLD ("break-even armed") — arm signal
6. HOLD (score-drift note)

`_recoverable(ctx)`: `dte ≥ RECOVERY_DTE_MIN` AND not breached AND cushion ≥
`RECOVERY_MIN_CUSHION`, where breach/cushion use `strategy` + `spot` + `short_strike`
(+`call_short` for IC). PCS breached if `spot ≤ short_strike`; CCS if `spot ≥ short_strike`;
IC if either side breached; cushion = `min(|spot−short|/spot over relevant sides)`.

**Tests (write first, watch fail):**
- `test_arms_at_50pct_does_not_close`: pnl=+50% credit, not armed → action HOLD, reason
  mentions "break-even armed" (NOT TAKE_PROFIT).
- `test_armed_breakeven_stop_closes_at_be_level`: be_armed, pnl ≤ be_level → CUT/`BREAKEVEN_STOP`.
- `test_armed_holds_while_above_be_level`: be_armed, pnl above be_level, no delta/time breach → HOLD.
- `test_delta_stop_deferred_when_recoverable`: delta breach + DTE≥5 + 2% cushion, not breached → HOLD (recovery).
- `test_delta_stop_fires_when_short_dte`: delta breach + DTE=3 → CUT/`DELTA_STOP`.
- `test_delta_stop_fires_when_breached`: delta breach + spot at/through short strike → CUT/`DELTA_STOP`.
- `test_money_stop_is_hard_even_when_recoverable`: pnl ≤ −2× + DTE≥5 + cushion → CUT/`MONEY_STOP`.
- `test_time_stop_hard_when_underwater`: DTE≤2 & pnl<0 → CUT/`TIME_STOP`.
- `test_ic_recovery_uses_both_sides` (breach on the call side blocks recovery).
- Back-compat: existing `recommend` tests still pass (old ctx w/o new keys ⇒ no arm, no recovery).

Commit: `feat(recommender): break-even trailing + recovery-aware delta stop`.

---

### Task 2: `signal_db` — `be_armed` column + closed-today query

**Files:**
- Modify: `options-scanner/signal_db.py` (SCHEMA/`init_db` migration; `get_open_signals_with_latest_mark` SELECT; new `set_be_armed`, `get_outcomes_for_date`)
- Test: `options-scanner/tests/test_signal_db.py`

- Idempotent migration: add `be_armed INTEGER DEFAULT 0` to `signals` (ALTER-if-missing in
  `init_db`, mirroring how additive columns were handled).
- `get_open_signals_with_latest_mark`: add `s.be_armed AS be_armed` (additive, like the
  recent `current_value`).
- `set_be_armed(signal_id, db_path=…)`: `UPDATE signals SET be_armed=1 WHERE signal_id=?`.
- `get_outcomes_for_date(date_iso, db_path=…)`: join `signal_outcomes` (by `close_date`) to
  `signals` → `[{signal_id, symbol, strategy, entry_credit, exit_value, realized_pnl,
  exit_reason, close_ts}]`, newest first.

**Tests:** be_armed defaults 0 + survives round-trip + set flips it; open view exposes
be_armed; `get_outcomes_for_date` returns today's closed rows with realized P&L, excludes
other dates. Commit: `feat(signal_db): be_armed flag + closed-by-date query`.

---

### Task 3: round-trip commission helper (break-even level)

**Files:**
- Modify: `services/options_svc/commission.py` (add `round_trip_commission(strategy, symbol, qty)`)
- Test: `services/options_svc/tests/test_commission.py`

`round_trip_commission` = open+close commission for the structure's leg count (PCS/CCS = 2
legs, IC = 4 legs) via `commission_for(legs, symbol, qty)` ×2 (open + close). Returns $ per
position; the manage cycle divides by `qty·MULTIPLIER`? No — `be_level` is compared to
`unrealized_pnl` in **dollars**, so `be_level = round_trip_commission(...)` in dollars is
directly comparable. **Test:** PCS 1-contract round-trip = 4 leg-fills × per-leg rate;
IC = 8 leg-fills. Commit: `feat(commission): round-trip commission helper`.

---

### Task 4: `compute` — captured manage cycle + closed-today view

**Files:**
- Modify: `services/options_svc/compute.py` (`run_captured_manage_cycle()`, `captured_closed_today()`, extend `reprice_captured` to pass lifecycle ctx)
- Test: `services/options_svc/tests/test_compute.py`

`run_captured_manage_cycle()` (mirrors the driver manage pattern; fully defensive):
1. `sigs = signal_db.get_open_signals_with_latest_mark()`.
2. For each: `rep = signal_repricer.reprice_swing(r, _proxy.schwab_py_client)`; skip on
   error/None (stale-price guard). `mark = signal_recommender.build_mark(r, rep, now)`;
   `signal_db.insert_mark(mark)`.
3. Arm: if `mark.unrealized_pnl ≥ TP_FRAC·entry_credit·100` and not `r.be_armed` →
   `signal_db.set_be_armed(signal_id)`; `r.be_armed=1`.
4. Build lifecycle ctx (add `be_armed`, `be_level = round_trip_commission(strategy, symbol,
   qty=1)`, `spot=rep.current_underlying`, `short_strike`, `call_short`, `strategy`) and call
   `recommend()`. (Prefer having `build_mark` accept + thread these so the mark's
   recommendation IS the lifecycle one — refactor `build_mark` to take the extra ctx.)
5. **Expiry:** if `dte_remaining ≤ 0`, close at `rep.current_value` (intrinsic; OTM ⇒ ~0 ⇒
   full credit) with reason `EXPIRED`.
6. Else if `mark.recommendation_code` in the close set (`BREAKEVEN_STOP`/`MONEY_STOP`/
   `TIME_STOP`/`DELTA_STOP`, and `TARGET_HIT` no longer emitted) → auto-close via
   `signal_db.close_signal_manually(signal_id, exit_val=rep.current_value, reason=code)`.
7. Each open-signal iteration wrapped in try/except; returns `{"closed":[…], "armed":[…]}`
   for the handler to log/notify.

`captured_closed_today()` → `{"closed": signal_db.get_outcomes_for_date(today_ct),
"total_realized": Σ realized_pnl}`.

**Tests (seams patched like existing `reprice_captured` tests):** arm-at-50 sets be_armed +
does not close; armed retrace ≤ be_level auto-closes with `BREAKEVEN_STOP`; delta breach w/
recovery does NOT close; money stop closes; expiry OTM closes at 0 (full credit); stale
reprice skips (no close); `captured_closed_today` sums realized. Commit:
`feat(options_svc): captured auto-manage cycle + closed-today view`.

---

### Task 5: handlers + scheduler wiring + toggle

**Files:**
- Modify: `services/options_svc/handlers.py` (`run_captured_manage_and_publish`, publish
  `CACHE_CAPTURED_CLOSED`, `captured_manage` command, `set_autoclose_enabled` +
  `autoclose_enabled` read, notify on closes)
- Modify: `services/options_svc/scheduler.py` (`captured_manage_due` 5-min slot + a
  `_captured_manage_branch` in `loop`, gated by `autoclose_enabled`)
- Test: `services/options_svc/tests/test_handlers.py`, `test_scheduler.py`

- Cache keys: `CACHE_CAPTURED_CLOSED = "cache:options:captured_closed"`,
  `CACHE_AUTOCLOSE_ENABLED = "cache:options:autoclose_enabled"`.
- `run_captured_manage_and_publish(bus)`: run cycle → `refresh_captured(bus)` (open view) →
  publish `captured_closed_today()` under `CACHE_CAPTURED_CLOSED` → fire `push_notify` /
  chime for each close (reuse existing captured-flag notify path).
- `captured_manage_due(now, last_slot)` = 1-min-style slot at `_CAPTURED_MANAGE_INTERVAL_MIN
  = 5`, trading-day + market-hours gated (copy `manage_due`).
- In `loop`: add branch when `captured_manage_due` fires **and** `autoclose_enabled(bus)` is
  true; also publish `CACHE_CAPTURED_CLOSED` once at startup.
- `handle_command`: `captured_manage` → `run_captured_manage_and_publish`;
  `set_autoclose` (args enabled) → `cache_set(CACHE_AUTOCLOSE_ENABLED, {"enabled": bool})`.
- `autoclose_enabled(bus)`: read the flag, default **True** on missing/unreadable.

**Tests:** `captured_manage_due` fires once/5-min slot in hours, not off-hours; handler
publishes both views + calls close; toggle read defaults True, respects explicit False;
command dispatch. Commit: `feat(options_svc): schedule + gate captured auto-manage`.

---

### Task 6: EOD "Captured — closed today" section

**Files:**
- Modify: `webgui/pages/eod.py` (pure builders `captured_closed_rows`, `captured_closed_section`; read `options:captured_closed`; wire into summary + detail + TOC)
- Test: `webgui/tests/test_eod.py`

Pure builder over `{"closed":[…], "total_realized": …}` → an HTML `<details>` section (rows:
symbol/strategy/credit/exit/realized/reason/CT-time + a day total), graceful-empty ("No
captured trades closed today"). Add to `read_snapshot` (read the new cache), the summary +
detail fragments, and the TOC. **Tests:** rows map + total; empty note; section appears in
both fragments. Commit: `feat(eod): captured closed-today section`.

---

### Task 7: Settings toggle (write-through to service)

**Files:**
- Modify: `webgui/app_settings.py` (`captured_autoclose_enabled: True` in DEFAULTS)
- Modify: `webgui/pages/settings.py` (a switch + `apply_captured_autoclose` enqueuing
  `set_autoclose` on `cmd:options`, mirroring `apply_ticker_enabled`)
- Modify: `webgui/main.py` (re-assert the flag to the service at startup, inside the
  `__main__` guard — mirror `sync_ticker_setting`; heed the module-scope `on_startup` trap)
- Test: `webgui/tests/test_app_settings.py`, `test_settings` (if present)

**Tests:** default True present; `apply_captured_autoclose(True/False)` enqueues the right
`cmd:options` command. Commit: `feat(settings): captured auto-close toggle`.

---

### Task 8: Integration — Redis e2e + live verify + restart

- **Redis-driven e2e** (options_svc test): seed an OPEN signal in a temp `signals.db`, stub
  the repricer to return +50% then a retrace to ≤ be_level, enqueue `captured_manage` twice,
  assert: first arms (`be_armed=1`, still open), second auto-closes with `BREAKEVEN_STOP`
  into `signal_outcomes`, and `cache:options:captured_closed` reflects it.
- Run each affected suite; hold baselines (compare failing SET).
- **Live:** restart `options_svc` (+ webgui) via the windowless relaunch; confirm `/health`;
  verify the toggle default ON, a manual `captured_manage` runs, and `/eod` shows the
  closed-today section. **Do NOT** mass-close real signals to test — use the temp-DB e2e for
  the close path; live-verify only the wiring/health/UI.
- Commit: `test(options_svc): captured auto-manage e2e`.

---

## Notes / gotchas
- **Paper-only:** `close_signal_manually` writes an outcome + realized P&L; never a broker order.
- **Bounded risk:** money-stop + time-stop stay hard floors; recovery defers the delta-stop only.
- **Cross-app collisions:** options_svc already imports these engines in-process; keep lazy
  imports where `reprice_captured` does. No `scoring`/`notifier` new exposure.
- **Cadence cost:** 5-min ≈ 45–90 chain fetches/cycle; `_CAPTURED_MANAGE_INTERVAL_MIN` is the lever.
- **Dev/prod:** work in dev; `schedulers` flag gates the new cycle off in dev exactly like the
  others (it rides the same `make_app` scheduler gate) — verify it does.
