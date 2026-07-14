# Rescue coverage — Phase 1b: Debit verticals

**Date:** 2026-06-24
**Branch:** `Using_Highcharts`
**Status:** Approved (user: "move on to the debit verticals").

## Scope

Add ad-hoc rescue coverage for **debit verticals** — `VERT_CALL_DEBIT` (bull call:
long lower call + short higher call) and `VERT_PUT_DEBIT` (bear put: long higher put
+ short lower put). Defined risk = debit paid; directional. Advisory-only.

After this: credit spreads · iron condor/fly · singles · **debit verticals** = 8 of
19 structures. The rest still pop "not available yet."

## Spec shape (debit vertical)

`compute_rescue_adhoc`: `{symbol, strategy in ("VERT_CALL_DEBIT","VERT_PUT_DEBIT"),
long_strike (the LONG leg), short_strike (the SHORT leg), expiration, quantity,
entry_credit (SIGNED per-share: NEGATIVE = debit paid)}`.
- VERT_CALL_DEBIT: long_strike < short_strike (buy lower call, sell higher).
- VERT_PUT_DEBIT: long_strike > short_strike (buy higher put, sell lower).

## Backend engine (`services/options_svc/rescue.py`, pure)

### `assess_debit_risk(position, mark, gex=None, regime=None) -> {state, heat}`
A debit vertical is a directional bet; "at-risk" = the underlying moved against it
(toward max loss). Reuse the long-single heat model keyed on the **long leg** (the
directional leg) + a loss-fraction term:
- VERT_CALL_DEBIT behaves like a long call at `long_strike` (OTM/losing when
  `und < long_strike`); VERT_PUT_DEBIT like a long put at `long_strike` (losing when
  `und > long_strike`).
- heat = `min(50, loss_frac·60) + min(25, otm_depth·300) + (15 if dte≤5 and OTM)`,
  where `loss_frac = max(0,−unrealized_pnl)/max(1, debit_dollars)` and `otm_depth`
  is measured from `long_strike`. state via the ≥75/≥50/≥25 ladder. Defensive.

### `debit_candidates(position, mark, price_leg, gex=None, regime=None) -> [dict]`
All advisory, commission-aware. `cv` = current spread value/share (= long-leg mid −
short-leg mid), `debit = abs(entry_credit)`, `w = |short_strike − long_strike|`.
- `close`: sell the spread to close → `gross = +cv·100·qty` (recover remaining
  value); commission 2 legs; `new_max_loss = 0`.
- `roll_out`: sell current + buy same-strikes spread at +30d → debit; commission 4
  legs; `new_expiry`, `dte_after`. rationale "more time for the thesis (costs a debit)."
- `convert_to_butterfly`: sell an equal-width spread extending beyond the short
  strike → credit that reduces the net debit and caps the position:
  - bull call: SELL `short_strike` call + BUY `short_strike+w` call → `L/short/short+w`
    call butterfly.
  - bear put: SELL `short_strike` put + BUY `short_strike−w` put → put butterfly.
  `gross = +credit·100·qty`; `new_max_loss = max(0, debit_dollars − gross)` (reduced).
Skip any candidate whose needed leg prices None.

### `compute_rescue_adhoc` routing (compute.py)
`_DEBIT_STRATEGIES = ("VERT_CALL_DEBIT","VERT_PUT_DEBIT")`. Route those to a new
`_advisory_from_debit`: price the two legs via `_make_leg_pricer` (cv = long − short),
underlying from the gamma-snapshot spot, `unrealized_pnl = (cv − debit)·100·qty`, run
`assess_debit_risk` + `debit_candidates`, force advisory, `source="adhoc"`,
`position_id="adhoc"`. Defensive → `{"error": …}`. Spread/single paths unchanged.

## Mapper + page (`webgui/pages/options/rescue.py`)

- `adhoc_spec_from_legs`: recognize a 2-leg **debit** vertical BEFORE the generic
  error:
  - 1 short call + 1 long call, `long.strike < short.strike`, no puts → VERT_CALL_DEBIT.
  - 1 short put + 1 long put, `long.strike > short.strike`, no calls → VERT_PUT_DEBIT.
  - `entry_credit = Σ short − Σ long` (NEGATIVE for a debit) — the ">0 net credit"
    guard applies only to credit structures, NOT debit verticals.
  - spec carries `long_strike`, `short_strike`, `entry_credit` (negative), quantity =
    a leg's qty.
- Add `VERT_CALL_DEBIT, VERT_PUT_DEBIT` to `RESCUE_ADHOC_SUPPORTED`.

## Testing
- `assess_debit_risk`: bull call with underlying below the long strike + big loss →
  high heat/critical; comfortably ITM → low; defensive on None.
- `debit_candidates`: close (credit, max_loss→0), roll_out (debit), convert_to_butterfly
  (credit, new_max_loss < debit); all advisory; unpriceable leg skipped.
- `compute_rescue_adhoc` for each debit strategy → advisory-only advisory.
- `adhoc_spec_from_legs`: bull-call legs → VERT_CALL_DEBIT (negative entry_credit);
  bear-put legs → VERT_PUT_DEBIT; a credit spread still maps to PCS/CCS (unchanged).
- Full suites green.

## Out of scope
- All-call/put condors & butterflies, calendars/diagonals (later phases). No Apply.
