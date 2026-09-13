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
| Long strangle | its own OTM wings nearest **0.30 delta**, independent of the band. *Revised while building:* buying the short strangle's band-midpoint strikes put both wings near 0.15 delta, where PoP measured 22–25 in every IV × DTE cell — always under LONG's 30 bar (the two rows' PoPs sum to ~100) |
| Collar put, protective put | the long put nearest **0.25 delta**, not the sell band; skipped under 0.10 delta (a collar also needs a call ≥ 0.05) — below that the position is essentially plain long stock. Front expiry ≥ 7 DTE, as for calendars. *Revised while building:* the band midpoint bought a 0.08-delta hedge, and at the page's default DTE min of 0 a same-day hedge passed the gates |
| Butterfly / iron butterfly / condor wings | the strike nearest half the 1-σ expected move away |
| Calendar | front = nearest expiry with DTE ≥ max(DTE min, **7**); back = expiry nearest front + 28 days that is ≤ DTE max, at least 7 days later; same strike (ATM), skipped when either expiry's own ladder has a hole at the money (spacing read next to the money; a local step wider than max(10% of spot, $2.50) is a hole). *Revised while building:* the page's default DTE min is 0, so the front was a 0–2 DTE expiry and every calendar measured R:R −0.004 to 0.27 and was cut |
| Diagonal | short the FRONT month out of the money near **0.30 delta** (accepted only inside 0.15–0.45), long the BACK month in the money near **0.70 delta**; skipped when the debit reaches the strike width. ⚠ The playbook (`2026-09-11-options-strategy-playbook.md`) says *debit ≤ 75% of width*; 100% is deliberate — measured on a $1 ladder at 7/35 DTE the call diagonal costs 87.5% and the put 78.5% of width, so 75% would reject both. *Revised three times while building:* an out-of-the-money long priced as a credit; an in-the-money long against an at-the-money short cost more than the width on every call chain measured (debit = width + back time value − front time value), so only put diagonals could ever pass, and only on interest-rate carry. The 0.30/0.70 pairing is the practitioner shape the width rule assumes |

**No calendar is built when the window has no two expiries ≥ 7 days apart.** The
back month is the expiry nearest front + 28 days, so DTE max decides whether a
calendar exists and how close to four weeks it gets; the page's default window
(0–120) reaches it. This is the accepted cost of not fetching a second window — `$SPX` on a
wide window has already timed out at the proxy.

**Never emitted:** a calendar or diagonal whose max profit is not above zero, and any
straddle, butterfly, condor, calendar or diagonal whose at-the-money strike is missing
from one side of the chain (the builder skips rather than recentring on the next
strike, which would build an off-centre structure under a neutral name).

**The delta-band ceiling applies only where a short is out of the money by
design** — strangle, covered call, collar. A straddle's or iron butterfly's shorts
are ~0.50 delta by definition; applying the ceiling would delete them every time.

## Scoring

Fit (net delta vs the view, net vega vs the vol regime) is already
structure-agnostic. What each structure needs is a **gate profile** in
`strategy_scoring._TYPE_PROFILE` — an unmapped type silently falls to `DEBIT`,
which gives an unbounded long an unjudgeable reward and cuts it.

Measured with **`tools/sweep_strategy_gates.py`** — a synthetic Black-Scholes chain
(front and front + 28 days), the real builders at the page's default delta bands (put
−0.20…−0.10, call 0.10…0.20), scored against a neutral view. Default parameters: **spot
100, IV 0.28, $2.50 strikes**; grade at front DTE 14 / 30 / 45.

| Structure | Profile | 14 / 30 / 45 DTE | Deciding figure (vs the `min` bar) |
|---|---|---|---|
| Long straddle | LONG | Marginal / Marginal / Marginal | PoP 42.7 vs 30; reward auto-passes (unbounded); composite 53–55 |
| Long strangle | LONG | Marginal / Marginal / Marginal | PoP 34.5–37.8 with ~0.30-delta wings; composite 50–52 |
| Short straddle | NAKED | **Weak / Weak / Weak** | **PoP 57.3 vs 65** |
| Short strangle | NAKED | Good / Good / Good | PoP 77.8–79.1; capeff 0.50–0.93/yr vs 0.10 |
| Call / put butterfly | DEBIT | Weak / Good / Good | PoP 29.2 at 14 (vs 30); R:R 2.99–3.98 |
| Iron butterfly | DEBIT | Weak / Good / Good | within 0.14 R:R and 1.1 PoP of the long butterfly (by parity) |
| Call / put condor | DEBIT | Good / **Weak** / Good | R:R 0.53–0.54 at 30 (vs 0.6), where the wing lands at 5; 0.79–0.92 at 14 and 45 |
| Call / put calendar | DEBIT | Good / Good / Good | R:R 0.82–2.19; PoP 43.6–50.0 |
| Call diagonal | DEBIT | Weak / Good / Good | R:R 0.44 at 14; 0.69 / 0.90 |
| Put diagonal | DEBIT | Weak / Weak / Good | R:R 0.45 / 0.47 at 14 / 30; 0.68 at 45 |
| Covered call | NAKED | **Weak / Weak / Weak** | **PoP 51.8–52.4 vs 65** |
| Protective put | LONG | Good / Good / Good | PoP 42.3–44.6 |
| Collar | DEBIT | Good / Good / Good | R:R 1.66–2.00; PoP 44.0–47.0 |

Other parameters change rows, which is why every figure is quoted with its own:

- **`--step 5`**, 14 DTE: **no short strangle, covered call or collar is built** (the
  nearest sold call, 105 at 0.203 delta, is over the 0.20 ceiling); butterflies pass
  (PoP 45.1) and condors fail (R:R 0.22); the long strangle fails PoP (26.7); both
  diagonals pass.
- **`--iv 0.20`**: butterflies fail PoP at 30 and 45 DTE too (28.1, 23.6–24.7) and pass
  at 14; condors fail R:R at 14 (0.49); the put diagonal fails at 14 and 30.

**What the sweep showed, against the estimates this table replaced.** The first draft
was a hand-priced estimate taken before several builder revisions, and four rows did not
survive measurement:

- *Butterflies "pass, barely on PoP" (31–34)* — at 14 DTE on a $2.50 ladder the wing is
  2.5 and PoP is **29.2**: Weak. They pass at 30/45, but at IV 0.20 fail there too.
- *Condors "pass on fine ladders, R:R 0.67–0.81"* — they fail wherever the wing rounds
  **up** past half the expected move (R:R 0.53 at 30 DTE on $2.50 strikes). The condor is
  the most ladder-dependent row.
- *Long straddle/strangle "passes"* — the gates pass, but the composite sits at **50–55**,
  so they list as **Marginal**, only just over the service's 50 cut.
- *Collar "R:R 1.06, PoP 51"; protective put "PoP 46–48"* — measured after the
  0.25-delta hedge and the 7-day front: collar R:R **1.66–2.00**, PoP 44–47; protective
  put PoP **42–45**. Both still Good.

The two cuts held: short straddle PoP 57.3 and covered call PoP ~52, against 65, at
every DTE, ladder and IV swept. (The short straddle would pass `NEUTRAL` — PoP 57.3 vs
55 — so `NAKED` is the choice that cuts it, not the only profile that could.)

Decisions:

- **Iron butterfly is DEBIT, not NEUTRAL/CREDIT**, despite taking a credit: by
  put–call parity it is the long butterfly's payoff, measured identical. Under
  NEUTRAL's 55 PoP bar it would be cut every time — and so would the long
  butterfly if judged the same way.
- **Flies, condors and calendars carry `family = "NEUTRAL"`**, so
  `q_breakeven_vs_em` rewards a wide profit zone rather than a breakeven near
  spot, which is meaningless for a position centred on spot. The **short** straddle
  and strangle are `NEUTRAL` too; share structures and diagonals keep a directional
  family. *Revised while building:* the **long** straddle and strangle carry
  `family = "VOLATILITY"`, which takes the near-breakeven branch — a long volatility
  trade profits from a move, so the move it needs should be small.
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
- Columns unchanged. **Legs cell:** a share leg prints `L 100 shares` (*revised while
  building*); a leg of more than one contract carries `N×` (`S 2×100C`); a leg on a
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
  `BUTTERFLY_CALL`, `BUTTERFLY_PUT`, `CONDOR_CALL`, `CONDOR_PUT` to
  `strategy_table._PAPER_TYPES` **and**
  `paper_trader.PAPER_DEBIT_TYPES` (both now read from
  `shared.structures.LEDGER_DEBIT`, pinned equal by `test_cross_tier_mirrors`).
  **Exit rule (operator decision 2026-09-13): no time exit** for butterflies and
  condors — they keep the 50%-of-max-profit target and settle at expiry. A 21-DTE
  exit suits trades that lose value to time; a long fly gains most of its value in the
  last two weeks (95/100/105 at spot 100: $1.20 at 30 DTE, $1.42 at 21, target ~$3.10
  only near 3 DTE), so it would close flat within days of opening.
  **Precondition:** the ledger must reprice and settle a `qty 2` body leg
  correctly; if it does not, the butterflies ship without the button rather than
  the ledger being changed.
- **Straddles and strangles, long and short, stay ANALYSIS ONLY** (D1,
  `docs/plans/2026-09-12-straddle-strangle-design.md` — operator decision
  2026-09-13 to keep it). The Finder builds and shows them; none gets a Paper
  button, an exit-rule table, a repricer leg layout or a driver entry. The one D1
  test that asserted `strategy_scanner` never names them is rewritten to assert
  none of the four can be *opened*.
- No Paper button for iron butterfly, straddles or strangles, calendars,
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
