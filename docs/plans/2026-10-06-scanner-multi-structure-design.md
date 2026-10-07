# Market Scanner — structures beyond credit spreads

**Date:** 2026-10-06 · **Status:** approved design · **Route:** `/options/scanner`

## Request

"The 0-DTE and Swing scans only look for credit spreads. Expand to other
strategies: debit spreads, strangles, back ratio, calendar spreads etc."

Decided with the operator on 2026-10-06:

| Question | Decision |
|---|---|
| How far down the pipeline | **Show and capture.** Scanned, shown, recorded to `signals.db` and tracked to an outcome. No automatic entry into the paper Account. |
| The 0-DTE tab | **Short-dated structures are allowed on the Scanner.** The Strategy Finder's 7-day minimum stays as it is. |
| Layout | **Same two tabs, grouped by family.** Credit spreads keep their score, their capture floor and their calibration history. |
| Earnings | **Long premium is kept through a report and flagged.** Short premium is still dropped. |
| Directional tab | **Captured too**, in phase 4. |
| Cap for tracked structures | **2 open per symbol, in its own pool** (the proposed default; the operator did not change it). |

## Where it stands

- `scanner_engine.run_full_scan` builds PCS, CCS and iron condors for the 0-DTE
  bucket (DTE 0..4) and the Swing bucket (DTE 5..15), scores them with the
  9-factor premium-seller model (`scoring.py`), and records them
  (`signal_recorder`). The paper Account enters from those records.
- A third list, `signals_directional`, holds single long/short calls and puts,
  scored on the Finder's Fit + Quality model. It is shown and not recorded.
- The Strategy Finder (`strategy_scanner.py`, one symbol on demand) already has
  tested builders for debit verticals, straddles and strangles, butterflies,
  condors, iron butterflies, calendars and diagonals, plus the payoff math and a
  gate profile for each (`strategy_scoring._TYPE_PROFILE`).
- **The ratio backspread has no builder anywhere.** The playbook lists it as
  "Not supported".
- Each scan already holds three chains per symbol: today..+4, +5..+15 and
  +20..+45 days. Nothing in this design fetches another one.
- The capture path is credit-shaped end to end: `signals` has
  `short_strike` / `long_strike` columns, the dedup key is built from them, and
  marks come from `signal_repricer.reprice_swing`. The Paper Ledger's leg-based
  functions (`reprice_legs`, `legs_intrinsic_value`, `_recommend_debit`) are the
  starting point for tracking anything else.

## What gets built

| Family (filter chip) | Structures | 0-DTE tab (0..4) | Swing tab (5..15) | Builder |
|---|---|---|---|---|
| Debit spreads | `BULL_CALL`, `BEAR_PUT` | yes | yes | exists |
| Straddles and strangles | long and short straddle, long and short strangle | yes | yes | exists, needs a front-DTE parameter |
| Butterflies and condors | call/put butterfly, iron butterfly, call/put condor | yes | yes | exists, needs the same parameter |
| Calendars | call/put calendar, call/put diagonal | no | yes: front in 7..15, back from the +20..+45 chain | exists, needs the two chains merged |
| Ratio spreads | `CALL_BACKSPREAD`, `PUT_BACKSPREAD` (short 1, long 2 further out) | yes | yes | **new** |

Not built: the front ratio spread (long 1, short 2: undefined risk, and not
what was asked for), share structures (the Scanner holds no shares), and 0-DTE
calendars (measured in September with a 0..2 day front: every one was cut on
reward, R:R −0.004 to 0.27).

## Approach

Three were considered.

1. **A pass inside the existing scan (chosen).** A new engine module,
   `options-scanner/structure_scan.py`, takes the chains, price, volatility and
   market view the scan already has and returns candidates plus a funnel bucket.
   `run_full_scan` calls it in a guarded block, as it does the single-leg pass.
   No Schwab call is added.
2. **Run the Finder per symbol.** `compute.swing_scan` fetches its own chain, so
   this doubles the scan's chain requests against the shared 5-a-second limit.
3. **A separate scheduled scan off the proxy's stored chains.** The store ships
   in shadow mode, so this would depend on a switch that is not on.

Results travel in **two new lists**, `structures_0dte` and `structures_swing`,
and never in `signals_0dte` / `signals_swing`. Ten modules read the existing
lists and assume the credit shape (the trade-idea post, push alerts, the matrix,
the EOD page, the Symbol page, the regime filter, the IV floor, the gamma gate).
A new key means none of them sees a row it cannot read.

## Selection rules

The new pass reuses rules that already exist; it adds none of its own.

- **Scoring:** `strategy_scoring.score_all(..., daily_move=)`, the call the
  single-leg pass and the Finder make, so one candidate scores the same on
  every surface.
- **Quality cut:** score at or above the floor and grade not excluded, from a
  new `[structures]` table in `config/scanner.toml` (shipped equal to
  `[single_leg]`: 50, `["Weak"]`).
- **Volatility gate:** `shared.vol_gate.signal_blocks`, per candidate, on its
  own vega sign, with the window's floor and ceiling.
- **Earnings gate:** `earnings_gate_applies(trade_type, dte)` per candidate,
  reading a calendar's later expiry. What happens to a candidate that spans a
  report depends on its own vega sign, the key the volatility gate already
  uses: **long premium is kept and flagged** (`spans_earnings: True` and
  `earnings_date`, the fields the Finder's flag mode writes); **short premium
  is dropped**, as every credit spread is today. A candidate whose vega cannot
  be read is dropped. The switch is `[structures] earnings_long_premium`
  (`"flag"` as shipped, `"drop"` to restore one rule for everything). The
  credit-spread lists are not touched.
- **Per-symbol cap:** the best one of each structure, then the top
  `max_per_symbol_window` by score.
- **Regular-hours gate:** held outside 08:30–15:00 CT like every other list.
- **Not applied:** the momentum veto, the sentiment regime filter and the index
  gamma gate. They exist to stop selling premium into a trend; Fit already
  scores each structure against the inferred direction.

Every new number goes in `config/scanner.toml` and `webgui/config_schema.py`.

### The 7-day floor

`_front_pair` applies `_MIN_FRONT_DTE = 7` itself "so no caller can forget it".
It gains a `min_front_dte` argument defaulting to 7. The Finder passes nothing
and is unchanged, pinned by a test that its output is byte-identical. The
Scanner passes the window's own minimum.

### Gate bars at short expiries: measured first, never loosened

The gate bars were measured at 14, 30 and 45 days. The Scanner's windows are
0..4 and 5..15. `tools/sweep_strategy_gates.py` gains those expiries and is run
before any page work, because its result decides what the tabs can show.

What the existing measurements predict, to be confirmed:

- Butterflies fell under the 30 PoP bar at 14 days on a $2.50 ladder. Shorter
  is unlikely to help.
- The short straddle failed PoP (57 against 65) at every expiry measured.
- Long straddles and strangles passed but scored 50–55, just over the cut.
- Condors pass or fail by where the wing lands on the strike ladder.

So on fairly priced chains the 0-DTE tab may show few of these on most days.
That result is reported as measured. The standing rule holds: no bar is moved
without outcome data, and the funnel shows what was cut and why.

## The ratio backspread

`strategy_scanner.build_backspreads`: sell one option near the money (about
0.50 delta), buy two further out (about 0.30 delta), same expiry. The call
version profits from a large rise and the put version from a large fall; the
worst case is the underlying finishing at the long strike.

- Emitted only when the net is a credit or a debit under a configured fraction
  of the strike distance, and when the marks price inside the structure's
  arbitrage bounds (the check `_priced_inside` makes for butterflies).
- `payoff_metrics` already handles a two-contract leg (the butterfly body) and
  an unbounded tail, so the payoff path does not change.
- Gate profile: measured with the sweep, not assumed. `LONG` is the candidate
  (unbounded reward, PoP bar 30).
- Names go through `shared/structures.py`. The Finder gains a "Ratio spreads"
  family checkbox and the Calculator two templates, so Send to Calculator opens
  the structure by name.

## Page

Each of the 0-DTE and Swing tabs gains a two-way switch: **Credit spreads**
(today's table, untouched) and **Other structures**.

- Other structures uses the Finder's columns from `strategy_table.py`:
  Strategy, Bias, Legs, Debit or Credit, Max profit, Max loss, Reward to risk,
  Probability of profit, Breakevens, Score, Grade.
- Family filter chips above it, all on by default.
- Row actions: Send to Calculator and Expected Move for every row; Paper only
  for structures in `shared.structures.LEDGER_DEBIT`. Straddles and strangles
  stay analysis only, as decided on 2026-09-13.
- The tab header counts both tables. "Why no trade?" gains a Structures bucket
  per window that partitions exactly:
  `built == vol_gate + earnings + score_cut + capped + outside_rth + emitted`.
- A row kept through a report shows the report date beside its strategy name
  and in the trade detail panel.
- Day persistence (`merge_day_signals`) covers the two new lists with a
  leg-based setup key.
- New page code goes in `pages/options/scanner_structures.py`; `scanner.py`
  gains wiring only.

No push alert, trade-idea post, X post or site idea is produced from the new
lists.

## Capture and tracking

**Storage.** The same `signals` / `signal_marks` / `signal_outcomes` tables,
with additive columns (`legs_json`, `family`, `entry_max_profit`, `unbounded`)
and two new scanner types, `0DTE_STRUCT` and `SWING_STRUCT`. A fifth column,
`entry_spans_earnings`, records whether the trade was opened through a report,
so those outcomes can be read apart from the rest. A separate table
was considered and rejected: marks, outcomes, the Captured Signals page and the
nightly calibration are already generic over `signal_id`.

**Units.** A Finder candidate carries per-contract dollars; `signals` stores
per-share. The recorder converts, as the Income capture does. A debit is
stored as a negative `entry_credit`, the existing convention.

**The paper Account must never open one.** `run_entry_cycle` reads every open
captured signal with no type filter. Two guards, both required:

1. The two new scanner types join `_NO_AUTO_ENTRY_TYPES`.
2. The cycle gains an allow-list by structure (PCS, CCS, IC). Anything else is
   refused by name, so a future structure cannot be traded by omission.

**The capture cap gets its own pool.** `[capture] max_open_per_symbol` (2) is
counted across every type, and the Account enters from captures. Sharing it
would let a tracked butterfly take the slot of a credit spread the Account
would have traded. A second key, `max_open_per_symbol_structures`, counts only
the new types.

**Marks.** `reprice_legs` is extended to price each leg on its own expiry (a
calendar needs two chains) and to carry a credit as well as a debit. A leg with
no usable quote means no mark, never a zero.

**Exits.** Per structure, in `config/trade_mgmt.toml [structures.*]`, sourced
from the playbook and not fitted:

| Structure | Rule |
|---|---|
| Debit verticals, long straddle and strangle, backspread | `_recommend_debit` as shipped: profit target, loss stop off, no time exit at these expiries |
| Butterflies, condors, iron butterfly | profit target, settle at expiry (the 2026-09-13 decision) |
| Short straddle and strangle | credit rules: profit target and the money stop |
| Calendars and diagonals | profit target; closed on its mark on the front leg's expiry day before the close |

Every mark is stored, so a different exit can be replayed against the same
rows later.

**Settlement.** One-expiry structures settle at intrinsic through
`legs_intrinsic_value` against `paper_engine.settlement_underlying`, the one
rule the three books share. A calendar is never settled at intrinsic: its back
month still has time value. If it cannot be marked on the front expiry day it
is closed as unmarkable with no P&L and excluded from the statistics.

**Risk denominator.** Calibration measures in R (P&L over dollars at risk). A
short straddle or strangle has no maximum loss, so its row carries
`unbounded = 1` and uses the Finder's capital figure. Those rows are reported
in their own bucket and never averaged with defined-risk ones.

**The Directional tab.** Its single long and short calls and puts are the same
normalized shape, so the same recorder path captures them, under the scanner
type of the window each came from. They follow the rules above: the Account
never opens one, they count against the structures cap, and a short call's row
is `unbounded`. `[structures.LONG_CALL]` and `[structures.LONG_PUT]` already
have exit rules; a short put uses `[structures.SHORT_PUT]`; a short call takes
the credit rules.

**Other readers of `signals.db`.** The Rescue assessment skips leg rows; the
Captured Signals page shows a Legs cell for them; calibration buckets them by
the new scanner types, so the credit baseline does not move. Each reader of
`get_open_signals_with_latest_mark` (four call sites in `compute.py`) is
checked against a leg row by test.

## Phases

Each phase ships on its own and updates `page_help.py`, the manuals and the
CHANGELOG in the same commits.

| Phase | Contents | Visible result |
|---|---|---|
| **1. Measure** | The sweep at 0, 1, 2, 4, 7, 10 and 15 days; the scan's added time on recorded chains | A table of what will and will not pass. Decides whether anything below changes |
| **2. Show** | `min_front_dte`; chain merge for calendars; `structure_scan.py`; config; the two lists on the contract; funnel; day persistence; the page switch and chips; hand-offs | The four existing families on the Scanner |
| **3. Backspread** | Builder, measured gate profile, taxonomy, Finder checkbox, Calculator templates | Ratio spreads on the Scanner and the Finder |
| **4. Capture** | Schema, recorder, the two Account guards, the cap pool, marks, exits, settlement, Captured page, calibration buckets; the Directional tab's rows | Tracked outcomes for every structure |

## Testing

- **Builders:** the Finder's output byte-identical with the default floor;
  each structure built at 0, 2 and 10 days on a synthetic chain; no calendar
  without two expiries seven days apart; backspread strikes, quantities and
  worst case.
- **Scan:** counting never moves a decision (`collect_funnel` on and off give
  the same lists); the funnel partitions; a crash in the new pass leaves the
  credit lists identical and sets `build_failed`.
- **Isolation:** every existing reader of `signals_0dte` / `signals_swing`
  gets the same payload with the new pass on and off.
- **Account:** a captured leg row of every new structure is refused by
  `run_entry_cycle` through each guard alone.
- **Earnings:** a long straddle spanning a report is kept and flagged, a short
  strangle spanning the same report is dropped, a row with no vega is dropped,
  and `"drop"` removes all three.
- **Tracking:** mark, target, expiry settlement and unmarkable deferral for a
  debit, a credit, a two-contract leg and a two-expiry row; a NaN quote yields
  no mark.
- **Page:** columns, chips, counts, which rows carry Paper, the Calculator
  payload for a backspread and a calendar; the route stays in
  `test_no_inline_style.py`.
- **Config:** each new key is proved read by monkeypatch and reload, and is in
  the configuration catalogue.

## Verification without a dev environment

The local page harness on a fake bus for the page; a Redis-driven scan against
recorded chains for the engine; and, after promote, the first session's funnel
and capture rows read on prod. Promote after the close, since it stops the
whole target.

## Out of scope

- Automatic entry of any new structure into the paper Account.
- Paper buttons for structures the Ledger cannot book today.
- Push alerts, trade-idea posts and site ideas from the new lists.
- Changing any credit-spread rule, score or floor.
- New gate bars. They follow from the outcome data phase 4 starts collecting.
