# Ad-hoc Trade Rescue — Design

**Date:** 2026-06-22
**Branch:** `Using_Highcharts`
**Status:** Approved (forks chosen in-session: chain-backed pickers + advisory-only).

## Problem

The Rescue board only surfaces trades the app already knows about (paper positions
+ captured signals). The user wants to **define a trade themselves** — a spread
they hold elsewhere — with **calculator-like inputs**, and get the ranked rescue
menu for it. Advisory-only (it's not a paper position → no one-click Apply).

## Decisions

- **Inputs:** chain-backed pickers (calculator-style). Enter a symbol, **Load** its
  chain, pick expiration + strikes from real dropdowns. Reuses the Calculator's
  `calc_load` command + pure chain extractors.
- **Apply:** advisory-only (same as captured) — the ranked menu is guidance to
  place manually; no Apply button.
- **Structures:** PCS / CCS / IC (what the rescue engine supports).

## Backend

### Shared engine core (refactor — no behavior change)
Extract the common flow inside `compute.compute_rescue` (from repricing through
building the `RescueAdvisory`) into a helper:
`_advisory_from_position(pos, *, source, force_advisory, position_id)` →
reprice via `reprice_swing` (snapshot-spot fallback), build `engine_mark`, fetch
gamma + regime, run `_rescue_candidates`, optionally force every candidate
`apply_kind="advisory"`, `_assess_position_risk`, construct the advisory dict
(per-candidate, defensive). The **paper** path calls it with
`force_advisory=False`; **captured** with `True`. Existing tests must stay green.

### New: `compute.compute_rescue_adhoc(spec)`
`spec` = `{symbol, strategy (PCS|CCS|IC), short_strike, long_strike, call_short,
call_long, expiration, quantity, entry_credit}`. Build a position-like dict from
it (map fields; `quantity` default 1; `max_loss_total` = `abs(short-long)*100*qty`
if not derivable), validate the minimum (symbol + strategy + short/long strikes +
expiration), then call `_advisory_from_position(pos, source="adhoc",
force_advisory=True, position_id="adhoc")`. Fully defensive → `{"error": ...}`;
never raises. Missing required fields → a clear error advisory.

### Handler + command
- `handlers.run_rescue_adhoc(bus, spec)` → `compute.compute_rescue_adhoc(spec)` →
  cache `cache:options:rescue:adhoc` (`f"{CACHE_RESCUE}:adhoc"`) + publish
  `EVENT_RESCUE` with `{version, position_id: "adhoc"}`.
- `handle_command`: `elif command.type == "rescue_adhoc": run_rescue_adhoc(bus,
  command.args.get("spec") or command.args)`.
- `rescue_apply` unchanged (advisory cards have no Apply; a string "adhoc" id can't
  reach a paper primitive anyway).

### Contract
No change — `RescueAdvisory.position_id: int | str`, `source` free string. Adhoc
uses `position_id="adhoc"`, `source="adhoc"`.

## Page (`webgui/pages/options/rescue.py`)

A new **"Rescue an ad-hoc trade"** card in the left column, below the at-risk board
(a `ui.expansion`, collapsed by default so it doesn't crowd the board):
- **Symbol** input + **Load** button → enqueue `calc_load` (args symbol); version-
  poll `cache:options:calc_chain`; on new chain, populate the expiry + strike
  dropdowns using the Calculator's pure `chain_expiries(chain)` /
  `chain_strikes(chain, expiry, "PUT"|"CALL")` (imported from `.calculator`).
- **Strategy** select (PCS/CCS/IC) → shows the relevant strike fields (PCS: short/
  long put; CCS: short/long call; IC: all four).
- **Expiration** select (from `chain_expiries`), **strike** selects (from
  `chain_strikes` for the chosen expiry/side), **Quantity** (default 1), **Entry
  credit** (per-contract credit received at entry — needed for max-loss/economics).
- **Compute rescue options** button → build the spec, set `state["selected_id"] =
  "adhoc"`, enqueue `rescue_adhoc`; the EXISTING advisory poll
  (`options:rescue:<selected_id>` → `options:rescue:adhoc`) + candidate-card
  rendering show the advisory-only menu on the right. A basic-validation guard
  (all required strikes chosen) before enqueue; else `ui.notify`.
- The chain load is shared single-user cache with the Calculator (`calc_chain`) —
  acceptable (one page at a time).

Pure helpers added: `adhoc_spec(inputs)` (assemble + validate the spec dict → spec
or an error string) and `adhoc_strike_fields(strategy)` (which strike inputs a
strategy needs) — unit-tested. `render()` wiring verified by the shell smoke test.

## Testing
- contracts: unchanged (adhoc reuses `position_id: int|str` + `source`).
- compute: `_advisory_from_position` refactor leaves paper/captured tests green;
  `compute_rescue_adhoc` builds from a spec, returns advisory-only candidates under
  `source="adhoc"`, errors on missing fields, never raises.
- handlers: `rescue_adhoc` command caches under `cache:options:rescue:adhoc`.
- webgui: `adhoc_spec` validation + `adhoc_strike_fields`; existing rescue tests +
  shell smoke stay green.
- Live check: `compute_rescue_adhoc` against the live proxy for a real spread.

## Out of scope
- No persistence of the ad-hoc trade (it's a one-shot advisory).
- No Apply / paper-open from ad-hoc (advisory-only by decision).
