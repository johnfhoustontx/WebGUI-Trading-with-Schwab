# Recommender lifecycle gate + trailing / manual-paper placeholders — design + plan

**Date:** 2026-08-11
**Status:** approved (continues the 2026-08-09 captured-autoclose feature)
**Scope:** `options-scanner/signal_recommender.py` (+ `paper_engine.py`, `paper_account_db.py`,
`services/options_svc`). Combined design + plan (task list inline).

## Motivation — a shipped regression

The 2026-08-09 captured-autoclose feature reworked the **shared**
`signal_recommender.recommend()` into a lifecycle: at +50% credit it now **arms** break-even
and returns `HOLD` instead of the old `TAKE_PROFIT` / `TARGET_HIT`. Its design scoped the
change to *captured signals, paper-only*, and explicitly kept the **manual paper account and
the isolated driver account OUT of scope** ("they already have their own manage cycles").

But `recommend()` is shared, and `paper_engine.run_manage_cycle` — used by **both** the manual
paper account and the driver's isolated account — calls `recommend()` directly with a minimal
ctx and closes only on `action in ("TAKE_PROFIT", "CUT")`. Because the reworked Rule 5 returns
`HOLD` at +50% for **every** caller, **both paper books stopped taking profit at +50%** — and,
never setting `be_armed`, they got no break-even protection either. Confirmed live:
`recommend({"entry_credit":1.0, "unrealized_pnl":60.0, ...})` → `{"action":"HOLD",
"code":"HOLD"}`. The regression shipped to prod. It is **paper-only** (no real money) but skews
both books' P&L and works against the driver's press-and-bank mandate. The full suite stayed
green because `test_paper_engine.py` **mocks** `recommend()` (`_fake_recommend`), so it never
exercised the changed function.

## Task 1 (the fix) — gate the +50% arming on an explicit `lifecycle` opt-in

`recommend()` Rule 5 (the +50% arming transition) becomes:

- `ctx.get("lifecycle")` truthy → `HOLD` ("break-even armed") — the captured lifecycle, unchanged.
- else → `{"action":"TAKE_PROFIT", "reason":">=50% credit captured", "code":"TARGET_HIT"}` —
  the pre-feature behavior, **restored** for the paper/driver callers.

Only the two live callers of `recommend()` change:

- **`build_mark(...)`** (the captured lifecycle path) passes `lifecycle=True` in its recommend
  ctx. `build_mark` is captured-only (its only production callers are `compute.reprice_captured`
  and `compute.run_captured_manage_cycle`), so this is correct for all its callers and preserves
  the captured lifecycle end-to-end.
- **`paper_engine.run_manage_cycle`** keeps its minimal ctx (no `lifecycle` key) → gets
  `TAKE_PROFIT` back. Manual paper + driver restored.

Rules 1–4 (money/time hard stops; break-even stop gated on `be_armed`; recovery-deferrable
delta stop) are unchanged and already inert for the minimal ctx — paper never sets `be_armed`,
and recovery needs `spot`/`short_strike`/`strategy` keys paper does not pass. **Gating only
Rule 5 is behaviorally faithful:** at +50% profit a credit spread's short delta has decayed, so
"+50% profit AND delta-breached" is economically contradictory — the one theoretical ordering
difference (Rule 4 delta-stop vs the restored Rule 5 take-profit) closes at the *same mark* with
*identical* realized P&L, differing only in the exit-reason label.

**Tests (TDD):**
- The recommender tests that assert `HOLD`-arming on a **non-lifecycle** ctx
  (`test_arms_at_50pct_does_not_close`, `test_arms_beyond_threshold_still_holds`,
  `test_low_dte_profitable_arms_not_time_stops`, `test_recommend_code_arms_hold_not_target_hit`)
  are updated to pass `lifecycle=True` — they were pinning the regressed behavior.
- NEW: a non-lifecycle +50% ctx returns `TAKE_PROFIT` / `TARGET_HIT` (regression guard).
- NEW **unmocked** `test_paper_engine` integration test: drive a +50% open position through the
  **real** `recommend()` and assert `run_manage_cycle` CLOSES it with reason `TARGET_HIT` (RED
  before the gate, GREEN after). Keep the existing mocked tests; add this alongside.
- `_lctx` helper gains `lifecycle=True` (it represents the captured lifecycle ctx). `build_mark`'s
  arming tests stay green because `build_mark` passes `lifecycle=True`.

This task is the **priority** and is independently promotable.

## Task 2 (placeholder) — peak-driven profit-lock ladder ("ratchet"), inert by default

Deferred item from the captured-autoclose design ("Further trailing beyond break-even … a
laddered trail is a later option"). Build the **logic**, keep it **inert** (default = today's
single break-even level).

`recommend()` Rule 3 (the armed break-even stop) generalizes to a ladder. With
`ctx["trail_ladder"]` = list of `(peak_frac, lock_frac)` rungs and `ctx["peak_pnl_frac"]` (peak
profit ÷ credit, i.e. the best `pnl / credit_total` the trade has reached), the locked stop is:

```
lock_frac* = max lock_frac over rungs whose peak_frac <= peak_pnl_frac   (0.0 if none / no peak)
stop_level_$ = max(be_level, lock_frac* * credit_total)
if pnl <= stop_level_$ -> CUT / BREAKEVEN_STOP
```

Constants (single source, `signal_recommender.py`):

- `DEFAULT_TRAIL_LADDER = [(0.50, 0.0)]` — single rung, locks break-even only:
  `max(be_level, 0.0) = be_level` → **byte-identical to today**.
- `RATCHET_TRAIL_LADDER = [(0.50, 0.0), (0.65, 0.25), (0.80, 0.50)]` — the sensible default
  ratchet: peak ≥ 65% locks +25% of credit, peak ≥ 80% locks +50%.

`ctx.get("trail_ladder")` absent/None → `DEFAULT_TRAIL_LADDER`; `ctx.get("peak_pnl_frac")`
absent/None → treat peak as unknown → lock_frac* = 0.0 (be_level only). `build_mark` gains
optional `trail_ladder=None, peak_pnl_frac=None` params threaded into `recommend()` (default
None → today's behavior). **No flag, no peak-tracking column, no manage-cycle change** — nothing
passes a non-default ladder yet, so live behavior is unchanged. Activation (documented, later):
the captured/paper cycle tracks `peak_pnl_frac` and passes `RATCHET_TRAIL_LADDER` when a ratchet
flag is on.

**Tests:** default ladder = unchanged break-even stop (existing tests stay green); the ratchet
ladder locks +25% at peak 65% and +50% at peak 80% and cuts on a retrace through the locked
level; a lower peak locks nothing (be_level only); absent peak → be_level only.

## Task 3 (placeholder) — wire the MANUAL paper account into the lifecycle, inert by default

Deferred item ("Applying the same lifecycle to the manual paper / driver accounts"). Wire the
**manual** paper account only (**driver excluded**), gated **OFF** by default.

- **`paper_engine.run_manage_cycle(..., lifecycle=False, be_level=None)`:** when
  `lifecycle=True`, build the fuller recommend ctx — `lifecycle=True`, `be_armed` (from the
  position), `spot`/`short_strike`/`call_short`/`strategy` (from the position), `be_level` — so
  a manual paper position arms at +50%, rides to full credit under the break-even stop, and gets
  recovery-aware delta deferral, the same lifecycle captured signals have. When `False` (default)
  → today's minimal ctx → restored `TAKE_PROFIT` (Task 1). Arm in the cycle when
  `lifecycle & pnl >= 50% & not be_armed`.
- **`paper_account_db`:** add `be_armed INTEGER DEFAULT 0` to `paper_positions` (idempotent
  `ALTER` in `init_schema`, mirroring the `signals` table), a `set_be_armed(position_id,
  db_path)`, and carry `be_armed` in `fetch_open_positions`. `be_level` = round-trip commission,
  computed by the options_svc caller (which owns the commission model via `commission.py`) and
  passed in; default 0.0 when absent.
- **options_svc:** the **manual** paper caller (`compute.run_manage_and_refresh` → the paper
  `run_manage_cycle`) reads a flag (default OFF) and passes `lifecycle=<flag>` + `be_level`; the
  **driver** caller (`compute.run_driver_manage_cycle`) passes `lifecycle=False` **always**. Flag
  mirrors the autoclose pattern but **defaults OFF**: `app_settings.manual_paper_lifecycle_enabled`
  (default `False`) → a `cmd:options` command → `cache:options:manual_paper_lifecycle` →
  `handlers.manual_paper_lifecycle_enabled(bus)` read each cycle (default `False` on a missing
  key — only an explicit ON enables it) → a Settings-page toggle. Re-asserted from settings.json
  at webgui startup (mirrors `sync_ticker_setting` / the autoclose sync).

**Tests:** default-OFF `run_manage_cycle` = today's behavior (TAKE_PROFIT at +50%, restored by
Task 1); ON = arms at +50% (be_armed persisted, position NOT closed) and break-even-stops on a
retrace; driver caller stays on TAKE_PROFIT regardless of the flag; `paper_account_db` be_armed
migration + round-trip; flag defaults OFF on a missing/unreadable key.

## Build order + regression

1. **Task 1 (fix)** — options-scanner recommender + **unmocked** paper_engine tests green; commit.
   Priority, independently promotable.
2. **Task 2 (ratchet)** — recommender ladder tests green (default unchanged); commit.
3. **Task 3 (manual paper lifecycle)** — paper_engine + paper_account_db + options_svc flag tests
   green; commit.

Full-suite regression on **options-scanner**, **options_svc**, and **webgui**, compared by the
failing **SET** (never the count) against the documented baselines (options-scanner 1370/11/3;
options_svc 932/2; webgui 1190/0). Then FF `Using_Highcharts` + `main`, **verify in dev**, and
promote via `tools\promote.bat`.

## Out of scope (unchanged from the 2026-08-09 design)

- Real broker execution (stays paper/advisory-record only).
- Applying the lifecycle to the **driver** account (explicitly excluded here; it keeps
  TAKE_PROFIT). Could be unified later.
- Activating the ratchet / manual-paper lifecycle by default — both ship **inert** (default
  ladder / flag OFF); flipping them on is a deliberate later step.
