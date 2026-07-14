# Rescue coverage — Phase 1c: all-call/put condors & butterflies

**Date:** 2026-07-14
**Branch:** `Using_Highcharts`
**Status:** Approved (user: "move on to all-call/put condors & butterflies").

## Scope

Add ad-hoc rescue coverage for the **single-type range structures**:

- `CONDOR_CALL` — long call condor: long K1, short K2, short K3, long K4 (K1<K2<K3<K4).
- `CONDOR_PUT` — long put condor: same 1/-1/-1/1 shape, all puts.
- `BUTTERFLY_CALL` — long call butterfly (1-2-1): long K1, short 2×K2, long K3 (K1<K2<K3).
- `BUTTERFLY_PUT` — long put butterfly (1-2-1), all puts.

All four are **defined-risk DEBIT, neutral/range** structures (max loss = the debit
paid; max profit if the underlying sits in the body/between the shorts). `IC` and
`IRON_BUTTERFLY` are already covered (the mapper folds an iron fly into `IC`) — this
phase is the **single-type** (all-call OR all-put) siblings. Advisory-only (ad-hoc has
no Apply).

After this: credit spreads · IC/iron-fly · singles · debit verticals · **single-type
condors & butterflies** = **12 of 19** structures. The rest still pop "not available
yet."

## Representation (design decision — carry `legs`)

These 3–4-strike structures don't fit the `short_strike`/`long_strike` 2-strike mold, so
the spec carries the **normalized legs** directly (the cleanest, most extensible shape):

`compute_rescue_adhoc` accepts, for a range strategy:
```
{symbol, strategy in (CONDOR_CALL, CONDOR_PUT, BUTTERFLY_CALL, BUTTERFLY_PUT),
 legs: [{right: "CALL"/"PUT", side: "long"/"short", strike, qty}, …]  # PER-UNIT qtys
 expiration, quantity, entry_credit}   # entry_credit SIGNED per-share (NEGATIVE = debit)
```
- `legs` carry **per-structure-unit** qtys (butterfly body qty=2, wings qty=1; condor all
  qty=1). `quantity` = number of units.
- `entry_credit` = signed net premium per unit per share (`Σ short·qty − Σ long·qty`),
  NEGATIVE for these longs (a debit). The ">0 net credit" guard applies only to credit
  structures.

## Backend engine (`services/options_svc/rescue.py`, pure)

### `assess_range_risk(position, mark, gex=None, regime=None) -> {state, heat}`
A long range structure loses when the underlying leaves the profit zone toward a wing.
Derive from `legs`: the **center** (midpoint of the SHORT strikes — the fly body / the
two condor inner shorts) and **half_width** (center → nearest wing = nearest LONG strike).
- `debit_dollars = |entry_credit|·100·qty`; `loss_frac = max(0,−pnl)/max(1, debit_dollars)`.
- `range_frac = |underlying − center| / half_width` (0 = at center/max-profit, ~1 = at a
  wing/breakeven, >1 = outside the structure → toward max loss).
- `heat = min(50, loss_frac·60) + min(35, range_frac·35) + (15 if dte≤5 and range_frac>0.8)`,
  clamped [0,100]; state via ≥75/≥50/≥25 ladder. Fully defensive (missing underlying /
  degenerate strikes → skip the range term; never raises).

### `range_candidates(position, mark, price_leg, gex=None, regime=None) -> [dict]`
All `apply_kind="advisory"`, commission-aware (`commission_for`), `_leg` for display legs.
`cv` = current structure value/share (from `mark["current_value"]`, priced by compute from
the legs), `n = len(legs)`.
- **close** (score 60) — sell/close the whole structure → **gross = +cv·100·qty** (recover
  remaining value), commission `n` legs, `new_max_loss = 0`. The safe floor.
- **roll_out** (score 45) — close the structure + reopen the SAME strikes at +30d →
  **gross = (cv − new_cv)·100·qty** (a debit; the later-dated structure costs more),
  commission `2n` legs, `new_expiry`, `dte_after`. Skipped if any rolled leg is unpriceable.

Structure-specific rolls (recenter the body / tighten the tested wing) are **deferred** —
shifted strikes rarely land on the chain ladder, so a pure engine can't price them
reliably; close + roll_out are the two always-priceable, always-meaningful rescues (mirrors
the off-hours degradation of the singles/debit paths, where only `close` survives).

## Compute (`services/options_svc/compute.py`)

`_RANGE_STRATEGIES = ("CONDOR_CALL","CONDOR_PUT","BUTTERFLY_CALL","BUTTERFLY_PUT")`. Route
those to `_advisory_from_range` (mirrors `_advisory_from_debit`):
- price each leg via `_make_leg_pricer`; **cv = Σ (sign · mid · qty)** per unit
  (sign: long −1 pays, short +1 receives → cv is the net value; for a long fly/condor cv is
  a small positive number = what the structure is currently worth to close), falling back to
  `|entry_credit|` when any leg is unpriceable;
- underlying from the gamma-snapshot spot; `unrealized_pnl = (cv − |entry_credit|)·100·qty`;
- run `assess_range_risk` + `range_candidates`, force advisory, `source="adhoc"`,
  `position_id="adhoc"`; two `mark` dicts (RescueMark keys vs engine `current_*` keys, the
  singles/debit pattern). Defensive → `{"error": …}`. Other paths unchanged.

`_adhoc_range(spec)` validates + normalizes the legs and builds the position dict.

## Mapper + page (`webgui/pages/options/rescue.py`)

- `adhoc_spec_from_legs`: after the debit-vertical branch, recognize the range structures by
  aggregating legs into signed net qty per (right, strike):
  - all-CALL, 4 distinct strikes, ascending net-qty pattern `[+q,−q,−q,+q]` → `CONDOR_CALL`.
  - all-PUT, same → `CONDOR_PUT`.
  - all-CALL, 3 distinct strikes, pattern `[+q,−2q,+q]` → `BUTTERFLY_CALL`.
  - all-PUT, same → `BUTTERFLY_PUT`.
  - `quantity = q` (the wing qty); normalize legs to per-unit qtys; `entry_credit =
    Σ short·per_unit_qty − Σ long·per_unit_qty` (NEGATIVE = debit) — bypasses the >0 guard.
  - carry `legs: [{right, side, strike, qty(per-unit)}]` in the spec.
- Add `CONDOR_CALL, CONDOR_PUT, BUTTERFLY_CALL, BUTTERFLY_PUT` to `RESCUE_ADHOC_SUPPORTED`.

## Testing
- `assess_range_risk`: underlying at the body → low heat; at/past a wing with a big loss →
  high heat/critical; defensive on None underlying / degenerate strikes.
- `range_candidates`: close (credit, max_loss→0), roll_out (debit, +30d) for a call condor +
  a put butterfly; all advisory; unpriceable rolled leg → roll_out skipped, close survives.
- `compute_rescue_adhoc` for each of the four → advisory-only advisory (source="adhoc").
- `adhoc_spec_from_legs`: condor legs → CONDOR_CALL/PUT (per-unit legs, negative
  entry_credit); 1-2-1 fly legs → BUTTERFLY_CALL/PUT; a credit spread / debit vertical still
  map unchanged.
- Full options_svc + webgui suites green; ruff clean.

## Out of scope
- Structure-specific rolls (recenter/tighten). Calendars/diagonals (multi-expiration lift,
  a later phase). No Apply (advisory-only).
