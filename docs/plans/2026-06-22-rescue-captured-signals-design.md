# Rescue for Captured Signals (CUT) — Design

**Date:** 2026-06-22
**Branch:** `Using_Highcharts`
**Status:** Approved (design discussed in-session; user said proceed).

## Problem

The Captured Signals screen shows `CUT` recommendations — trades that hit a
money/delta/time stop, i.e. genuinely at-risk. But they never appear on the
Rescue board. Two independent "in trouble" signals exist and aren't connected:

1. **Captured signals** carry a `recommendation` (`HOLD`/`TAKE_PROFIT`/`CUT`) +
   `recommendation_code` (one of `_STOP_CODES = TARGET_HIT/MONEY_STOP/DELTA_STOP/
   TIME_STOP`), computed in `compute.reprice_open_signals`. `CUT` = one of the
   three loss stops (NOT `TARGET_HIT`, which is a winner → TAKE_PROFIT).
2. **The Rescue board** filters on `rescue_state ∈ {tested, critical}` / `heat`,
   written only by the manage cycle's `_apply_rescue_overlay` on
   `cache:options:paper_account` — never on the captured view.

So `at_risk_rows`'s captured branch is dead (captured rows never get a
`rescue_state`). The original design intended captured signals to be **advisory**
rescue candidates ("advisory on captured too; apply only on real paper
positions"); the trigger was just never wired.

## Decisions

- **Surface captured `CUT` signals on the Rescue board** as **advisory-only**
  candidates (no one-click Apply — `rescue_apply` only mutates paper positions).
- **Trigger:** `recommendation == "CUT"` (the three loss stop codes), escalated to
  at least `tested` so a CUT always lands on the board regardless of borderline
  heat math. `TARGET_HIT`/TAKE_PROFIT is NOT at-risk and is excluded.
- **Scope:** CUT only (not winners); keep it minimal.

## Changes

### 1. Contract — `shared/contracts/options.py`
- `RescueAdvisory.position_id: int | str` (a captured id is the string
  `signal_id`; paper ids stay int — backward compatible).
- Add `RescueAdvisory.source: str = "paper"` ("paper" | "captured") so the page
  knows captured = advisory.

### 2. Detection on captured — `compute.reprice_open_signals`
After building each signal's `mark`, attach a rescue assessment:
- Build an engine-mark from the reprice (`current_underlying`/
  `current_short_delta`/`unrealized_pnl`/dte) + the signal's static fields, run
  `rescue.assess_position_risk(...)` → `state`/`heat`, merge onto the signal row
  (`rescue_state`/`heat`).
- **Escalate:** if `recommendation == "CUT"` (or `recommendation_code` in the
  three loss stops), force `rescue_state` to at least `tested`.
- Fully defensive (any failure → leave the row untagged; never raise).
The captured view (`cache:options:captured`) is fed by this path when marks are
computed (the path that already produces the `CUT` the user sees).

### 3. Advisory menu for a captured signal — `compute.compute_rescue`
- Signature `compute_rescue(position_id, source="paper")`.
- `source == "captured"`: load the signal via `signal_db.get_signal(signal_id)`,
  build a position-like dict (symbol; `strategy` from the signal `strategy`/`type`;
  short/long + call legs; expiration; entry_credit; quantity default 1; max_loss
  from width), reprice, run the engine, and **force every candidate
  `apply_kind="advisory"`** (there is no executable paper position). Set
  `source="captured"` on the advisory. Cache at `cache:options:rescue:<signal_id>`.
- Defensive → `{"error": ...}`; never raises. Paper path unchanged.

### 4. Handler — `handlers.run_rescue` / `handle_command`
- `run_rescue(bus, position_id, source="paper")` → `compute_rescue(position_id,
  source)`; cache key `f"{CACHE_RESCUE}:{position_id}"` (string signal_id works).
- `handle_command` `rescue` branch passes `args.get("source", "paper")`.
- `rescue_apply` unchanged — it already refuses non-paper ids; captured cards are
  advisory so the page shows no Apply button.

### 5. Page — `webgui/pages/options/rescue.py`
- `at_risk_rows` captured branch: key on `signal_id` (`id_field="signal_id"`).
- Row select passes the row's `source`; enqueue `{type:"rescue", args:
  {position_id: id, source}}`; poll `cache:options:rescue:<id>`.
- Captured advisory cards render with no Apply (all `apply_kind="advisory"`); the
  summary line / a chip marks them "captured · advisory".

### Out of scope (noted)
- The nav **badge** stays paper-only (`assess_open_positions`); captured CUTs show
  on the board but don't bump the badge. (Discoverable via the Captured screen +
  the Rescue table.) Can be added later.

## Testing
- contracts: `RescueAdvisory` accepts a str position_id + `source`.
- compute: `reprice_open_signals` tags `rescue_state`/`heat` and escalates on CUT;
  `compute_rescue(source="captured")` loads from signal_db, returns advisory-only
  candidates, caches under the signal_id; defensive on missing signal.
- handlers: `rescue` command routes `source`; captured advisory cached.
- webgui: `at_risk_rows` includes a CUT captured signal (with rescue_state) keyed
  by signal_id; pure-builder tests stay green.
- Full suites: shared/contracts, services/options_svc, webgui.
