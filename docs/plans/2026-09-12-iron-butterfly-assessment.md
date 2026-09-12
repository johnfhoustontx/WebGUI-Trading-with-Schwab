# The iron butterfly scanner (D2) — measured, and not built

*Assessment, 2026-09-12. Gap assessment item **D2**.*

## What the assessment asked for

> **D2. An iron butterfly scanner.** Emit IC candidates with coincident shorts;
> the rest of the IC pipeline already carries them. It needs a per-structure
> profit target: Option Alpha takes 25%, or exits 5 days before expiry;
> TradingBlock takes 50%.

**Both halves of that first sentence are false**, and measuring it turned up a
live money-path defect instead. The scanner is not built; two bugs are fixed.

## Finding 1 — the IC pipeline cannot produce coincident shorts, ever

`build_iron_condors` pairs any PCS with any CCS sharing a symbol and expiration,
so on the face of it an iron butterfly is simply the case where the two short
strikes coincide. Measured on prod:

- **10 IC signals** have ever been recorded, and **0** have coincident shorts.
- Their short-strike separation is **min 11.00, median 70.00, max 95.00** points.

It is not bad luck. `screen_spreads` selects short strikes **by delta band** —
directional PCS −0.55..−0.30 / CCS 0.30..0.55, income put −0.25..−0.15 / call
0.15..0.25 — and a put short and a call short inside *any* of those bands sit on
**opposite sides of spot**. An iron butterfly needs both shorts **at the money**,
around 0.50 delta each. So the body is not a by-product of the condor pairing; it
needs a **new ATM selection path**, which is a different piece of work from
"emit IC candidates with coincident shorts".

The structure is at least buildable from real chains — the ATM strike is quoted on
both sides for every symbol checked (SPY 764, $SPX 7655, AAPL 330, XOM 165).

## Finding 2 — the pipeline's probability model would mis-score it badly

`build_iron_condors` computes `pop_pct = P(put ok) + P(call ok) − 100`, and the
comment explains why: an IC's two breaches are **disjoint**, so that is the
correct two-sided probability (it replaced a `min(...)` that reported the better
leg). For **coincident** shorts it is wrong:

| put leg | call leg | formula says |
|---|---|---|
| 50% | 50% | **0%** |
| 52% | 51% | 3% |
| 55% | 54% | 9% |

Both shorts of an ATM body are ~50/50, so the formula reports a near-zero
probability of profit — while a real iron butterfly's profit zone is *the body
strike ± the credit*, which is not a knife edge. `max_loss = max(width) − credit`
stays correct, but `rr_pct`, `pop_pct`, `calc_expected_pnl` and therefore the
composite score would all be wrong, so a butterfly reaching the pipeline would be
cut by every quality gate for the wrong reason.

⚠ **Fixing that means writing a new probability model for a structure with no
outcome data in this app** — zero iron butterflies have ever been scanned,
recorded or traded. That is precisely the unmeasured change this audit keeps
catching, so it is not being guessed at.

## Finding 3 — the reachable path had a live data-loss defect, now fixed

A butterfly **can** already exist in the paper book: `rescue.build_convert_butterfly`
is an `execute` action on the Rescue board, and it has been used — once, on manual
position **403** (SPY), on 2026-06-29.

`_apply_convert` adds the opposite side and relabels the row `IC`. For a **PCS**
that is right: the put strikes stay in `short_strike`/`long_strike` and the new
call legs go to `call_short`/`call_long`. For a **CCS** it was **data loss** — a
standalone CCS keeps its strikes in `short_strike`/`long_strike` (read off the
*call* map; only an IC uses `call_short`), and the CCS branch wrote the new **put**
strikes straight over them without moving the calls anywhere.

Position 403 shows it exactly: **`puts 747.0/746.0, calls NULL/NULL`**, status
`EXPIRED`. The consequence is worse than a wrong number — `reprice_swing`'s IC
branch needs all four strikes, so the position became **unmarkable**: no mark, no
exit rule, no P&L, until it expired on its own.

Fixed by moving the original call strikes into `call_short`/`call_long` before
writing the new put strikes. Six tests, including a PCS control that passes
before and after (that path was never broken) and one that drives the converted
row through `position_greeks` to show it can be priced again.

## Finding 4 — and a bug of my own from earlier today

The same investigation exposed an error in the C4 Greeks shipped hours earlier:
`_LEG_LAYOUT["CCS"]` read `call_short`/`call_long`, so **every CCS position
returned all-`None` Greeks** and contributed nothing to the book total.

⚠ It passed review because the test fixture was written to match the wrong
assumption (`_pcs(strategy="CCS", call_short=500.0)`) — the repo's documented "a
unit test over an invented fixture passes while the live column is entirely
blank" trap, the same shape as the `get_quotes` envelope bug. Live impact was
**nil**: all 11 currently open positions are PCS, and historically CCS is 15 of
273 rows, with `positions_priced` disclosing any gap. The guard that now holds it
is an **AST cross-check** that reads the strike field names out of
`reprice_swing`'s own branches, so the layout and the pricer cannot drift.

## What would actually be needed to ship D2

1. An **ATM short-selection path** in the scanner (new, not a by-product).
2. A **probability model** for a coincident-short body, replacing the disjoint
   formula for that case — with nothing to validate it against yet.
3. `IRON_BUTTERFLY` as a **first-class tradeable structure**:
   `shared/structures`, `signal_repricer`'s leg layout (a converted body is
   currently labelled `IC`, so it silently inherits the condor's exit rules),
   `_LEG_LAYOUT`, four-leg commissions, sizing, Rescue, and its own
   `[structures.*]` table carrying the profit target the item names (Option Alpha
   25% / TradingBlock 50%).
4. Assessment **defect 12** alongside it: the Rescue ad-hoc form lists an iron
   butterfly and relabels it an iron condor before submitting.

That is a coherent piece of work and a much larger one than the item describes.
The honest sequencing is **after** something produces a butterfly worth scoring —
which, like D5, waits on outcome data this app does not yet have.
