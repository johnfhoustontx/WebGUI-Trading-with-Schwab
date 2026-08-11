# Plan — `big_delta` options-flow detector

**Date:** 2026-08-10
**Pairs with:** [design](2026-08-10-big-delta-flow-detector-design.md)
**Branch:** `Using_Highcharts` (dev checkout), promote per the standing rule

TDD throughout: write the failing test named in each task, then the code. Every phase leaves
the tree green — `big_delta` is additive, and nothing before Phase 5 changes what a user sees.

---

## Phase 0 — Precondition (does NOT block Phases 1–5)

**Blocked on:** the 2026-08-11→13 instrumentation series, which fixes `min_notional`.

Only the **default value in the TOML** waits on that number. Every function, test and wiring
task below can be written and merged first with the provisional `min_notional = 25000000`.

When the series completes, take the **median** session (design §10), and read it off the
reconciliation-corrected numbers — per design §4a the closing-delta bias makes these a **lower
bound**, so do not round the floor up to "be safe"; rounding up compounds a bias that already
points that way.

---

## Phase 1 — Pure engine (`services/options_svc/flow_alerts.py`)

The whole detector is pure and lives here. Tests: `services/options_svc/tests/test_flow_alerts.py`.

### 1.1 Characterization test for `detect_uoa` — BEFORE touching it

Task 1.2 restructures a function that currently feeds a live alert channel. Pin it first.

- Capture a real chain fixture (one symbol, from a live `get_option_chain`, trimmed) into
  `services/options_svc/tests/fixtures/chain_uoa.json`.
- `test_detect_uoa_characterization` asserts `detect_uoa("SPY", fixture, cfg)` equals a
  checked-in expected list, **including order** (it sorts by premium desc and slices `top_n`).

Acceptance: passes against today's code, unmodified.

### 1.2 `_walk_contracts(symbol, chain) -> (rows, gross)`

One traversal of `callExpDateMap` + `putExpDateMap`. Emits normalized rows
`{side, strike, expiry, dte, volume, oi, mark, delta, premium, delta_notional}` and the
symbol's summed `delta_notional`.

- `test_walk_drops_sentinel_delta` — a `-999.0` row is absent from `rows` **and** contributes
  nothing to `gross`. (The gross half is the one that silently corrupts a ratio rule.)
- `test_walk_skips_unusable` — missing/zero volume, non-numeric delta, unparseable strike,
  missing `underlyingPrice` → skipped, no raise.
- `test_walk_traverses_once` — pass a chain whose maps are counting proxies; assert each is
  iterated exactly once.
- `test_walk_gross_is_sum_of_abs` — `gross == sum(r["delta_notional"])`, all non-negative.

`delta_notional = abs(delta) * volume * 100 * spot`, matching
`tools/flow_delta_instrumentation.py` exactly — the calibration is meaningless if the two
formulas drift.

### 1.3 Re-implement `detect_uoa` over `_walk_contracts`

Public signature and behaviour unchanged.

Acceptance: **1.1 still passes untouched.** If it fails, the refactor is wrong — do not edit
the characterization expectation.

### 1.4 `detect_big_delta(symbol, chain, cfg)`

Gates, all from `cfg["big_delta"]`:

1. `enabled` (default True) — else `[]`
2. warm-up: `gross >= min_gross` — else `[]`
3. per contract: `delta_notional >= min_notional` **AND** `delta_notional / gross >= min_share`
4. sort by `delta_notional` desc, slice `top_n`

- `test_big_delta_requires_both_gates` — the XLB case from design §2: a row at 21% of a tiny
  gross but only $0.1M notional does **not** fire; a $300M row at 4% of gross does **not** fire.
- `test_big_delta_fires_when_both_met`
- `test_big_delta_warmup_suppresses` — symbol under `min_gross` yields `[]` even with a
  huge contract.
- `test_big_delta_ignores_open_interest` — a row with `oi == 0` **can** fire (it must NOT
  inherit UOA's `oi > 0` gate; that gate exists only because UOA's ratio is undefined there).
- `test_big_delta_drops_sentinel` — `-999.0` never appears.
- `test_big_delta_top_n` / `test_big_delta_disabled` / `test_big_delta_malformed_chain`.

Emits the design §6.4 payload; **no `id`/`ts`/`text`** (the handler stamps those).

### 1.5 `detect_contract_alerts(symbol, chain, cfg) -> {"uoa": [...], "big_delta": [...]}`

Walks once, applies both rules to the shared rows.

- `test_contract_alerts_single_walk` — counting-proxy chain iterated once for BOTH rules.
- `test_contract_alerts_matches_individual` — output equals calling `detect_uoa` and
  `detect_big_delta` separately.

### 1.6 `alert_text` branch + `_DEFAULTS`

- `test_alert_text_big_delta` — contains symbol, strike/side, humanized notional and the
  share; asserts it contains **no** buy/sell/bullish/bearish wording (house rule: volume is
  unsigned, we cannot claim direction).
- `test_defaults_carry_big_delta` — `load_thresholds()` returns the block with a missing TOML.

---

## Phase 2 — Config (`config/flow_alerts.toml`)

Append the design §6.5 block. Comment each knob, including **why `min_notional` exists at all**
(a bare ratio fires on 63-lot contracts in thin names) and that it is calibrated on closing
delta and is therefore a lower bound.

`test_toml_parses_big_delta` in the existing config test.

---

## Phase 3 — Service wiring

### 3.1 `services/options_svc/compute.py`

The `on_chain` hook (~line 2192) calls `detect_uoa` and stashes per symbol. Generalize:

- `_UOA_STASH` → `_CONTRACT_ALERT_STASH`, holding `{symbol: {"uoa": [...], "big_delta": [...]}}`
- `stash_uoa`/`take_uoa_stash`/`clear_uoa_stash` → `stash_contract_alerts` /
  `take_contract_alert_stash` / `clear_contract_alert_stash`
- `on_chain` calls `detect_contract_alerts`, still inside its existing `try/except`

**Rename rather than add a parallel stash.** Two stashes drained at different points is how
one silently stops being drained.

- `test_stash_roundtrip_both_kinds`
- `test_take_clears` — a second take returns empty (consume-once; a stale stash would re-alert
  every tick)
- `test_on_chain_failure_does_not_break_collection` — `detect_contract_alerts` raising leaves
  GEX collection unaffected. **This is the one that protects production**: the flow detector
  rides the GEX poll, and collection matters more than alerts.

### 3.2 `services/options_svc/handlers.py`

The drain loop (~1093–1107) iterates the stash and stamps `id`/`ts`/`text`. Extend to both
kinds, sharing the day-scoped cooldown-as-seen-set:

```python
cid = f"{sym}|big_delta|{c['side']}|{c['strike']:g}|{c['expiry']}"
```

- `test_big_delta_alert_gets_id_ts_text` — **`ts` especially**: UOA shipped without it and
  every unusual-activity alert rendered a blank time until 2026-08-09. Do not repeat it.
- `test_big_delta_once_per_contract_per_day` — second tick with the same contract emits nothing.
- `test_big_delta_respects_allowed_universe` — `$VIX` excluded, same as the other detectors.
- `test_big_delta_survives_reconciliation_shape` — the emitted `id` matches
  `tools/flow_delta_instrumentation.alert_uoa_id`'s sibling format, so the instrumentation can
  reconcile `big_delta` the way it now reconciles UOA.

**Record in a comment** why the seen-set is valid here: UOA's justification is that `vol/OI`
is monotonic; `big_delta`'s share is **not** (the denominator grows too), so the semantics are
"first crossing wins" — same mechanism, different reason (design §6.3).

---

## Phase 4 — Push notifications (`services/options_svc/push_notify.py`)

- `_FLOW_CATEGORIES` += `"big_delta": "flow_big_delta"`
- `_flow_is_bullish` += `(type == "big_delta" and side == "call")`

An unrouted category falls through to the global webhook/chat by design, so **no
`shared/notifications.json` change is required**; a dedicated `routes.flow_big_delta` is
optional and can come later.

- `test_flow_category_big_delta`
- `test_big_delta_call_is_green` / `put_is_red`

---

## Phase 5 — Webgui (`webgui/pages/options/flow.py`)

Tests: `webgui/tests/test_flow_page.py`.

- `_KIND_LABEL` += `"big_delta": "Big delta"` — this **automatically** adds the filter option,
  since `render()` builds the select from `dict(_KIND_LABEL)` and seeds
  `state["kinds"] = set(_KIND_LABEL)`.
- `_TONE` += `("big_delta", "call"): _TONE_POS`, `("big_delta", "put"): _TONE_NEG`
- `alert_detail` gains a `big_delta` branch: notional + share + volume + delta
- Module docstring says "three kinds" — make it four.

- `test_kind_label_big_delta` / `test_tone_class_big_delta`
- `test_alert_detail_big_delta`
- `test_filter_includes_big_delta_by_default` — the default `kinds` set contains it
- `test_unknown_type_still_renders` — an unrecognized type must not blow up the table

---

## Phase 6 — Verification

Unit suites (per-folder; never `pytest services`, which re-triggers the documented
`config`/`scoring` collisions):

```bash
.venv\Scripts\python -m pytest services\options_svc
```

```bash
cd webgui && ..\.venv\Scripts\python -m pytest -q
```

Compare the failing **set**, not the count (options_svc carries 2 documented date-relative
`test_expected_move` failures). A matching total has hidden real regressions in this repo before.

Then live, on a trading day with the stack up:

1. Restart `options_svc`; confirm `/status` shows it healthy.
2. Watch `cache:options:flow_alerts` for `type == "big_delta"` entries; check each carries a
   non-empty `ts`.
3. Open `/options/flow` — new rows render, tint correctly, and the kind filter toggles them.
4. **Count them against the day's UOA + crossover volume.** Design §4 predicts ~18/day on a
   Monday-like session against a real channel of ~175. An order of magnitude more means the
   warm-up gate is wrong — go to §5 of the design, not to the threshold.
5. Re-run `tools\flow_delta_instrumentation.py` after the close and read its reconciliation
   block: modelled vs actual for UOA should be unchanged, and `big_delta` should now be
   visible in the channel-load line.

**Watch item, day one:** the open. The ratio's denominator is smallest at 08:30, and `min_gross`
is the only thing standing between that and an alert burst — and its $250M value is a guess,
not a measurement (design §5).

---

## Rollback

`[big_delta].enabled = false` in `config/flow_alerts.toml` + restart `options_svc`. No code
revert, no cache surgery: published alerts age out with the day-scoped key, and the webgui
renders whatever types are present.

---

## Explicitly out of scope

- **The §11 calibration loop** (target alert rate, nightly floor recompute,
  `cache:options:big_delta_floor`). It needs its own design + plan, and it should be built on
  the shipped detector's own output rather than the instrumentation script — the detector
  samples every minute and so has none of the closing-delta bias described in §4a.
- Signed/directional delta notional, per-symbol thresholds, aggregate symbol-level alerting,
  backfill (design §9).
- The instrumentation's remaining gaps — no time gate, BRKB failing to fetch, no retry, no
  cross-session aggregation. Real, tracked, unrelated to shipping the detector.
