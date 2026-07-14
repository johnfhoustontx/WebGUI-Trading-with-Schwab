# Rescue coverage — Phase 1: Single options (the #1 menu family)

**Date:** 2026-06-23
**Branch:** `Using_Highcharts`
**Status:** Approved (user: "start with #1 in the menu pulldown").

## Scope

Add ad-hoc rescue coverage for the **Single** strategy family (first in the picker):
`LONG_CALL`, `LONG_PUT`, `NAKED_CALL`, `NAKED_PUT`. Everything else still pops the
"not available yet" message (already shipped). Advisory-only (ad-hoc has no Apply).

Naked shorts are **undefined-risk** (the credit-spread feature deliberately excluded
them); included here because they're in the #1 family, but every naked candidate
carries an explicit undefined-risk warning.

## Spec shape (singles)

`compute_rescue_adhoc` accepts, for a single strategy:
`{symbol, strategy, short_strike (= the single strike), expiration, quantity,
entry_credit (SIGNED: +premium received for naked, −premium paid for long)}`.
(Reuses `short_strike`/`entry_credit` — no contract change.)

## Backend engine (`services/options_svc/rescue.py`, pure)

### `assess_single_risk(position, mark, gex=None, regime=None) -> {state, heat}`
- **LONG_CALL / LONG_PUT** (defined risk = debit): heat from (1) unrealized loss as a
  fraction of the debit paid, (2) moneyness — how far OTM the underlying is vs the
  strike (deep OTM = worse), (3) DTE/theta (near expiry + OTM = worse). ok→critical.
- **NAKED_CALL / NAKED_PUT** (undefined risk = credit): heat from short-strike
  proximity (underlying vs strike, like a credit short leg) + short delta + loss;
  higher base heat (undefined downside). ok→critical.

### `single_candidates(position, mark, price_leg, gex, regime) -> [candidate]`
All `apply_kind="advisory"`, commission-aware (`commission_for`):
- **Long call/put** (repair a losing long):
  - `close` — sell to close, recover remaining premium (credit).
  - `roll_out` — same strike, +~30 DTE (debit; buys time).
  - `convert_to_vertical` — sell a further-OTM same-type option → debit spread
    (recovers premium, caps the position; new_max_loss = debit − credit collected).
- **Naked call/put** (defend an undefined-risk short):
  - `close` — buy to close (debit).
  - `roll` — roll away + out for a credit (put → down & out; call → up & out).
  - `buy_protection` — buy a further-OTM option → credit spread that DEFINES the
    risk (the key naked rescue; new_max_loss = width − net credit). ⚠ undefined-risk
    warning until applied.

Each candidate: `action, label, apply_kind="advisory", gross_cash, commission,
net_cash, new_max_loss?, est_fill_legs[], rationale[], warnings[]` (naked → undefined-risk
warning). Order by a simple priority (close first as the safe floor, then the
repairs); scoring can stay light (advisory-only).

## compute_rescue_adhoc routing

If `strategy` is a single → build the single-leg position dict + call
`assess_single_risk` + `single_candidates` (force advisory, `source="adhoc"`,
`position_id="adhoc"`) and assemble the `RescueAdvisory`. Else → the existing
spread path (`_advisory_from_position`). Fully defensive → `{"error": …}`.

## Mapper + page (`webgui/pages/options/rescue.py`)

- `adhoc_spec_from_legs`: recognize a **single leg** → `LONG_CALL` (long call) /
  `LONG_PUT` (long put) / `NAKED_CALL` (short call) / `NAKED_PUT` (short put); spec
  `short_strike = leg.strike`, `entry_credit = ±premium` (short → +, long → −),
  `quantity = leg.qty`. Keep the credit-spread and "not available yet" branches.
- Add `LONG_CALL, LONG_PUT, NAKED_CALL, NAKED_PUT` to `RESCUE_ADHOC_SUPPORTED`.

## Testing
- `assess_single_risk`: long OTM+down → high heat; naked underlying-through-strike →
  critical; benign → ok.
- `single_candidates`: long → close/roll_out/convert_to_vertical (all advisory);
  naked → close/roll/buy_protection (all advisory, undefined-risk warning present);
  economics signs correct.
- `compute_rescue_adhoc` for each single strategy → advisory-only advisory.
- `adhoc_spec_from_legs`: 1 long call → LONG_CALL spec; 1 short put → NAKED_PUT spec.
- Full suites green.

## Out of scope
- Debit verticals, all-call/put condors & butterflies, calendars/diagonals (later
  phases — still pop "not available yet"). No Apply (advisory-only).
