# Strategy Finder — every structure the Calculator knows

**Date:** 2026-09-13 · **Status:** approved design · **Route:** `/options/swing`

## Request

"In the Strategy Finder, scan for all strategies using the updated rules."
Clarified with the operator: teach the Finder to build and score the structures
it skips, all three groups (single-expiry options, calendars/diagonals,
share-based), with paper trading only where the ledger is already correct.

## Where it stands

The Finder builds **9** structures — `LONG_CALL`, `LONG_PUT`, `SHORT_CALL`,
`SHORT_PUT` (`strategy_scanner.build_directional`), `BULL_CALL`, `BEAR_PUT`
(`build_debit_verticals`), `PCS`/`CCS` (`adapt_credit_spread` over
`scanner_engine.screen_spreads`) and `IC` (`adapt_iron_condor`). The Calculator's
`STRATEGY_TEMPLATES` holds ~30.

The rules added on 2026-09-11/12 already run AFTER candidate building in
`compute.swing_scan` — the volatility gate (keyed on net vega), the per-signal
earnings gate, the quality cut, id assignment — so a new structure inherits them
by being built. The one rule that runs INSIDE the builders is the short-delta
band.

## Scope

13 new structures, named with the Calculator's template codes so a candidate
already carries the template it opens as:

| Family checkbox (Calculator group name) | Structures |
|---|---|
| Directional *(exists)* | long/short call, long/short put |
| Spreads *(exists)* | bull call, bear put, put/call credit |
| Neutral *(exists)* | iron condor |
| **Straddles & strangles** | `LONG_STRADDLE`, `SHORT_STRADDLE`, `LONG_STRANGLE`, `SHORT_STRANGLE` |
| **Butterflies & condors** | `BUTTERFLY_CALL`, `BUTTERFLY_PUT`, `IRON_BUTTERFLY`, `CONDOR_CALL`, `CONDOR_PUT` |
| **Calendars** | `CALENDAR_CALL`, `CALENDAR_PUT`, `DIAGONAL_CALL`, `DIAGONAL_PUT` |
| **Stock + options** | `COVERED_CALL`, `PROTECTIVE_PUT`, `COLLAR` |

**Not a new row: the cash-secured put.** `SHORT_PUT` already is one — it is
capitalised on its stock-to-zero loss — so a second row would list the same trade
twice.

## Payoff math

`strategy_scanner.payoff_metrics` values every leg at **intrinsic on one expiry
day**. That is exact for single-expiry option structures and wrong for a
calendar's back month (it would read as worthless) and for share legs.

**Decision: extend the Finder's own payoff math, do not borrow the Calculator's
`calc_summary_generic`.** Measured while designing, that function (a) ignores
commissions, which every Finder row nets, (b) prices all legs at ONE IV, where a
calendar's two IVs are most of the trade, and (c) uses a lognormal-with-drift PoP
that `options_calculator` documents keeping deliberately separate from the
Finder's zero-drift normal. Rows would not be comparable.

- A leg set of **single-expiry options takes today's code path byte for byte** —
  the existing 9 structures' numbers, grades and cuts do not move.
- When a set holds a **later-expiring leg or a share leg**, value each leg at the
  front expiry with `options_calculator.leg_value`: Black-Scholes at that leg's
  OWN IV and remaining T for a back month, the underlying price for shares. The
  same valuation feeds max profit/loss, breakevens and PoP.
- A set holding **shares scans from a price of zero**, as the Calculator does
  since D4, and the tail test counts a long share lot like a long call — so a
  protective put is unbounded upside rather than capped at the grid edge, and a
  covered call is bounded.
- **Commission skips share legs** (Schwab charges nothing for stock).
- A share leg is `{"kind": "stock", "side": "long", "strike": None,
  "expiration": None, "qty": 1}` with `qty` in **100-share lots**, the D4
  convention; its mark is spot.

## Strike and expiry selection

All inside the scan's existing DTE window — **no extra chain fetch, no extra
Schwab calls**.

| Structure | Rule |
|---|---|
| Straddle, iron butterfly, butterfly body | at-the-money strike |
| Strangle shorts, covered call, collar call | the delta band's midpoint (as the other shorts do) |
| Long strangle | same deltas as the short strangle, bought |
| Collar put, protective put | the long put at the put band's midpoint |
| Butterfly / iron butterfly / condor wings | the strike nearest half the 1-σ expected move away |
| Calendar | front = nearest expiry ≥ DTE min; back = expiry nearest front + 28 days that is ≤ DTE max; same strike (ATM) |
| Diagonal | as a calendar, the back leg one strike further IN the money — a debit. *Revised while building:* the out-of-the-money version first approved priced as a credit, which is not the structure the name means |

**No calendar is built when the window has no two expiries ≥ 7 days apart.** At
the default 5–30 window the calendar is short; widening DTE max gives longer
ones. This is the accepted cost of not fetching a second window — `$SPX` on a
wide window has already timed out at the proxy.

**The delta-band ceiling applies only where a short is out of the money by
design** — strangle, covered call, collar. A straddle's or iron butterfly's shorts
are ~0.50 delta by definition; applying the ceiling would delete them every time.

## Scoring

Fit (net delta vs the view, net vega vs the vol regime) is already
structure-agnostic. What each structure needs is a **gate profile** in
`strategy_scoring._TYPE_PROFILE` — an unmapped type silently falls to `DEBIT`,
which gives an unbounded long an unjudgeable reward and cuts it.

Measured by pricing each structure with Black-Scholes (spot 100, IV 28%,
14/30/45 DTE, wings at half the expected move) through the real scorers:

| Structure | Profile | Measured vs the `min` bar | Outcome |
|---|---|---|---|
| Long straddle, long strangle | LONG | PoP 36–43 vs 30; reward auto-passes (unbounded) | passes |
| Call/put butterfly | DEBIT | R:R 3.4–3.9 vs 0.6; PoP 31–34 vs 30 | passes, barely on PoP |
| Iron butterfly | DEBIT | identical to the long butterfly | passes, barely |
| Call/put condor | DEBIT | R:R 0.67–0.81; PoP 54–57 | passes |
| Calendar, diagonal | DEBIT | R:R 0.72–1.30; PoP 46–55 | passes |
| Collar | DEBIT | R:R 1.06; PoP 51 | passes |
| Protective put | LONG | PoP 46–48 | passes |
| Short strangle (1-σ) | NAKED | PoP 73–76 vs 65; capeff far above 0.10/yr | passes |
| **Short straddle** | NAKED | **PoP 57 vs 65 at every DTE** | **always cut** |
| **Covered call** | NAKED | **PoP ~54 vs 65** (and R:R 0.06–0.09 would fail DEBIT's 0.6) | **always cut** |

Decisions:

- **Iron butterfly is DEBIT, not NEUTRAL/CREDIT**, despite taking a credit: by
  put–call parity it is the long butterfly's payoff, measured identical. Under
  NEUTRAL's 55 PoP bar it would be cut every time — and so would the long
  butterfly if judged the same way.
- **Flies, condors and calendars carry `family = "NEUTRAL"`**, so
  `q_breakeven_vs_em` rewards a wide profit zone rather than a breakeven near
  spot, which is meaningless for a position centred on spot. Straddles and
  strangles are `NEUTRAL` too; share structures and diagonals keep a directional
  family.
- **Short straddles and covered calls keep the existing bars and are counted, not
  shown** (operator decision). No threshold is invented without outcome data; the
  status line's "N below the quality bar" includes them and the Calculator still
  offers both.
- `_STATE_TILT` gains no entries — an unlisted type tilts 0, which is safe.

The sweep ships as **`tools/sweep_strategy_gates.py`**, re-runnable like
`tools/sweep_naked_capeff.py`, so the table above is a reproducible measurement.
⚠ Its numbers move with wing width and the strike ladder — quote them with their
parameters.

## Earnings gate

A calendar's earnings check reads its **latest** expiry, not the front leg's: a
back month spanning a report is exposed to it even when the front expires first.

## Page

- Four new family checkboxes using the Calculator's group names; all seven on by
  default.
- Columns unchanged. **Legs cell:** a share leg prints `L 100 SH`; a leg on a
  later expiry carries its date (`S 100C / L 100C 10/16`).
- **Strategy cell** uses the Finder's existing label style ("Long Straddle",
  "Call Calendar", "Covered Call"), set by the builder. *Revised while planning:*
  the design said the Calculator's `strategy_label` wording, but the builders live
  in `options-scanner`, which cannot import that Tier-1 module, and a third copy
  of the label table would be the drift this doc avoids elsewhere.

## Hand-offs

- **Send to Calculator** already carries each leg's own expiry, and a share leg
  arrives as the Calculator's stock leg. No change; pinned by test for a calendar
  and a covered call.
- **Expected Move** already drops share legs and draws the front expiry.
- **Send to Paper** (operator decision: only where the ledger is right) adds
  `LONG_STRADDLE`, `LONG_STRANGLE`, `BUTTERFLY_CALL`, `BUTTERFLY_PUT`,
  `CONDOR_CALL`, `CONDOR_PUT` to `strategy_table._PAPER_TYPES` **and**
  `paper_trader.PAPER_DEBIT_TYPES`, plus a `[structures.*]` table with
  `exit_dte = 21` for each, mirroring the four existing debit structures.
  **Precondition:** the ledger must reprice and settle a `qty 2` body leg
  correctly; if it does not, the butterflies ship without the button rather than
  the ledger being changed.
- No Paper button for iron butterfly, short straddle/strangle, calendars,
  diagonals or share structures — the ledger's credit path only understands
  two-strike spreads and iron condors, it settles every leg at intrinsic (wrong
  for a back month), and it holds no shares.

## Taxonomy

New names go through `shared/structures.py` rather than a new literal set, so
`canonical()` keys the rule table and `test_structures.py`'s duplicate-set guard
covers them.

## Testing

- **Builders** — per structure, synthetic chain → expected strikes, expiries,
  quantities; no calendar without two expiries ≥ 7 days apart; the band ceiling
  binds strangles / covered calls / collars and not straddles / iron butterflies.
- **Payoff** — single-expiry option sets byte-identical before/after; calendar max
  loss = its debit; covered call max loss reaches price zero; protective put
  flagged unbounded upside.
- **Scoring** — the profile table pinned; the sweep committed.
- **Service** — `swing_scan` with all seven families on a fake chain; the family
  filter and cut count cover the new families.
- **Page** — labels, legs cell, which types get Paper, the Calculator payload for
  a calendar and a covered call.
- **Docs** — `page_help.py`, both end-user manuals' Strategy Finder sections, and
  the Technical Reference's Finder scoring section.

## Out of scope

- Paper-trading credit multi-leg structures, calendars or shares (ledger changes).
- New gate bars for short straddles / covered calls.
- A second chain fetch for longer calendars.
- Changing the existing 9 structures' economics.
