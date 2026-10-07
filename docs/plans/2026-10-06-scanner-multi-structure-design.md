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
| Ratio spreads | `CALL_BACKSPREAD`, `PUT_BACKSPREAD` (short 1, long 2 further out) | yes | yes | built 2026-10-07 |

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
- **Per-symbol cap:** per family, the best `max_per_family` by score (2 as
  shipped). A single cap across families was measured to drop long volatility
  every time; see the measurement below.
- **Short strangle strikes:** sold between `[structures] short_delta_min` and
  the Scanner's own entry ceiling, `[selection] max_entry_short_delta`.
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

### Gate bars at short expiries: measured 2026-10-06, none moved

The gate bars had been measured at 14, 30 and 45 days. The Scanner's windows are
0..4 and 5..15, so `tools/sweep_strategy_gates.py --scanner` measured them there
before any page work.

**How it was measured.** A fairly priced Black-Scholes chain, spot 100, flat
volatility, generous liquidity; the real builders called the way the Scanner
will call them; the real scorer against a neutral view. Nine chains: volatility
0.20, 0.28 and 0.45 by strike steps of 0.25, 1 and 2.5 (0.25% of spot is an
index ETF's ladder, 2.5% a coarse single stock's). Front expiries 0, 1, 2 and 4
days for the 0-DTE window, 7, 10 and 15 for Swing. The clock is pinned at 10:00
CT, because probability of profit reads the time actually left.

```
python tools/sweep_strategy_gates.py --scanner --iv 0.28 --step 1 --at 10:00
```

**Shown** means not graded Weak and scoring 50 or more, the display cut as
designed. Each cell is shown / built, out of the chains and expiries tried.

| Structure | 0-DTE window (of 36) | Swing window (of 27) | Expiration day only (of 9) | When it fails |
|---|---|---|---|---|
| Bull call spread | 28 / 28 | 27 / 27 | 4 / 4 | Never. Not built when the ladder has no two strikes near 0.60 and 0.30 delta |
| Bear put spread | 27 / 27 | 27 / 27 | 4 / 4 | As above |
| Long straddle | 36 / 36 | 27 / 27 | 9 / 9 | Never, but it scores 53 to 56 (Marginal) on every day except expiration day |
| Short straddle | 0 / 36 | 0 / 27 | 0 / 9 | Always: PoP 57 to 58 against 65 |
| Long strangle | 19 / 36 | 24 / 27 | 3 / 9 | PoP, where a coarse ladder puts the wings too far out. Scores 50 to 54 when shown |
| Short strangle | 25 / 35 | 25 / 25 | 0 / 9 | Never shown on expiration day: its reward is annualised and a zero-day horizon cannot be judged. Shown on 25 of 26 otherwise |
| Call butterfly | 20 / 36 | 22 / 27 | 5 / 9 | PoP when the ladder's wing is narrow, reward to risk when it is wide |
| Put butterfly | 21 / 36 | 21 / 27 | 5 / 9 | As above |
| Iron butterfly | 20 / 36 | 21 / 27 | 5 / 9 | As the butterflies (the same payoff) |
| Call condor | 15 / 30 | 22 / 27 | 0 / 4 | Reward to risk; never passes on expiration day |
| Put condor | 15 / 30 | 22 / 27 | 0 / 4 | As above |
| Call calendar | not built | 18 / 27 | | Reward to risk with a 7-day front, on all nine chains |
| Put calendar | not built | 27 / 27 | | Never |
| Call diagonal | not built | 2 / 27 | | Reward to risk |
| Put diagonal | not built | 0 / 27 | | Reward to risk |

One chain in full (volatility 0.28, step 1). Letter is the grade, number the
score, `*` shown, `-` not built:

```
structure             0      1      2      4      7     10     15
BULL_CALL          -    G  75* G  77* G  75* G  76* G  74* G  75*
BEAR_PUT           -    G  75* G  77* G  78* G  77* G  76* G  77*
LONG_STRADDLE    G  64* M  55* M  54* M  55* M  54* M  53* M  53*
SHORT_STRADDLE   W  39  W  39  W  39  W  39  W  39  W  39  W  39
LONG_STRANGLE    W  39  M  50  M  53* M  54* M  51* M  52* M  50*
SHORT_STRANGLE   W  39    -    G  68* G  67* G  67* G  68* G  68*
BUTTERFLY_CALL   G  69* G  72* W  39  W  39  G  69* W  39  G  69*
BUTTERFLY_PUT    G  69* G  73* W  39  W  39  G  69* W  39  G  70*
IRON_BUTTERFLY   G  70* G  74* W  39  W  39  G  69* W  39  G  69*
CONDOR_CALL        -    W  39  G  74* G  73* G  74* G  77* G  74*
CONDOR_PUT         -    W  39  G  74* G  74* G  74* G  77* G  74*
CALENDAR_CALL      -      -      -      -    W  39  G  71* G  74*
CALENDAR_PUT       -      -      -      -    G  71* G  74* G  77*
DIAGONAL_CALL      -      -      -      -    W  39  W  39  W  39
DIAGONAL_PUT       -      -      -      -    W  39  W  39  W  39
```

**What it showed against what this document first predicted.** The first draft
expected the 0-DTE tab to show few of these. That was wrong: debit spreads, long
straddles, butterflies and condors all pass there on most chains. Three
predictions held. The short straddle never passes. Long straddles and strangles
sit just over the cut. Butterflies and condors pass or fail by where the strike
ladder puts the wing.

**What the measurement does not include.** No skew, no real bid-ask spreads, no
volatility gate, no earnings gate, no market-state tilt, and one neutral view.
A real chain will cut more, most of all on the wings of a same-day structure,
where the liquidity bar is hardest to clear. The short strangle also has to
clear the IV rank floor (35 on the 0-DTE tab, 30 on Swing) before any of this.

**Three things the measurement raised.**

1. *The per-symbol cap would drop long volatility every time.* Up to ten
   structures pass in one window, and their scores sit in bands by family:
   debit spreads and condors 73 to 78, butterflies 68 to 78, the short strangle
   near 67, long straddles and strangles 50 to 56. A cap of 6 by score keeps the
   first three families and never the last. The cap is therefore **per family**
   (the best `max_per_family` of each, 2 as shipped), not across them.
2. *An expiration-day row is judged against a full day of movement.* The scorer
   uses `max(dte, 1)` days for the expected move and the butterfly wing, while
   at 10:00 CT about five hours remain, which is less than half that move. This
   is why the long straddle scores 64 on expiration day and 53 to 56 on every
   other. The single-leg Directional tab has always been scored this way. It is
   left alone here and listed as a decision, because changing it moves the
   Directional tab's scores too.
3. *Diagonals do not pass at these expiries.* 2 of 54. They are still built
   (the calendar builder emits them together) and counted in the funnel as below
   the quality bar.

The standing rule holds: no bar was moved.

### What the pass costs: measured 2026-10-06

`tools/measure_structure_scan.py` times the real `build_window` and `select`
for both windows on synthetic chains shaped like the three the scan holds (5, 9
and 4 expirations). One thread, after the imports are warm:

| Strikes each side, per expiration | Per symbol, mean | Per symbol, worst | 45-symbol scan |
|---|---|---|---|
| 60 | 63 ms | 78 ms | about 3 s |
| 200 | 108 ms | 167 ms | 4.9 s |
| 400 | 152 ms | 168 ms | about 7 s |

The budget was one second per symbol. The pass adds a few seconds to a scan
whose chain fetches already take about two minutes, and no Schwab call. A
synthetic figure: the first live scan's duration is the one to read.


## The ratio backspread

`strategy_scanner.build_backspreads`: sell one option near the money (about
0.50 delta), buy two further out (about 0.30 delta), same expiry. The call
version profits from a large rise and the put version from a large fall; the
worst case is the underlying finishing at the long strike.

- Emitted only when the net is a credit under the strike distance, a debit of at
  most `[structures] backspread_max_debit_frac` (0.25) of it, or even money. A
  credit at or over the distance cannot happen on real quotes and is refused as
  a bad mark, the reading `_priced_inside` gives a butterfly.
- `payoff_metrics` already handles a two-contract leg (the butterfly body) and
  an unbounded tail, so the payoff path does not change. Two fields are set by
  the builder because that function cannot know them: `capital` is the max loss
  (it would otherwise be a margin estimate, because a call backspread is flagged
  unbounded for its *profit*), and `target_breakeven` is the far breakeven.
- The names are literals in the builder, like every other structure's.
  `shared/structures.py` gains nothing: its sets describe what the paper books
  hold, and a backspread is in none of them.
- The Finder gains a "Ratio spreads" family and the Calculator two templates, so
  Send to Calculator opens the structure by name.

### Two scoring changes the backspread needed

**The breakeven it is scored on.** `q_breakeven_vs_em` scores a directional row
on its *nearest* breakeven. A backspread entered for a credit has two, and the
nearer one sits beside its short strike, where its loss zone begins. Scored on
that, the trade would be rewarded for sitting next to its own loss. The scorer
now reads `target_breakeven` when a row names one. No other structure sets it.

**The reward gate.** `_reward_metric` auto-passes an unbounded-profit long, and
detected one by "R:R is None and a debit is set". A call backspread entered for
a credit has unbounded profit and no debit, and read as unjudgeable. The test is
now "a debit is set, or `unbounded_profit` is true". Both sweeps were compared
before and after: no existing structure's score or grade moved.

### Gate profile: measured 2026-10-07

Same nine chains and pinned clock as the measurement above. `c` marks a row
entered for a credit, `d` for a debit; shown / built.

| Structure | Profile `LONG` | Profile `DEBIT` |
|---|---|---|
| Call backspread, 0-DTE window | 30 / 36 | 0 / 36 |
| Call backspread, Swing window | 22 / 27 | 0 / 27 |
| Put backspread, 0-DTE window | 22 / 35 | 22 / 35 |
| Put backspread, Swing window | 18 / 26 | 18 / 26 |

`LONG` is the profile. Under `DEBIT` a call backspread's reward cannot be judged
at all (its R:R is None), so none is ever shown.

Three things this measurement shows that a reader of the table should know:

1. **Only backspreads entered for a credit are shown.** Every credit one passed
   (probability of profit 63 to 69) and every debit one was cut (16 to 21,
   against a bar of 30). Which side of even money a backspread lands on is
   decided by the strike ladder, so the same symbol can show one on one scan and
   not the next.
2. **That 63 to 69 is mostly the chance of keeping a small credit.** The trade
   also profits if the price stays on the near side of the short strike, where
   it earns the credit and nothing more. On the chain above a call backspread
   collects about $40 against a worst case of about $260. The probability bar
   was not written with this shape in mind; it was not moved.
3. **The put version scores higher than the call version, and that is not a
   better trade.** A put backspread's best case is the stock at zero, a bounded
   but very large figure, so its reward to risk reads 40 to 100 and it scores 63
   to 77 against the call's 55 to 70. The same thing separates a long put from a
   long call on the Directional tab.

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
- Day persistence (`merge_day_signals`) covers the two new lists. Its setup
  key is already symbol, structure and front expiry, which these rows carry.
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

## Implementation plan

[2026-10-06-scanner-multi-structure-plan.md](2026-10-06-scanner-multi-structure-plan.md).
Phases 1 to 3 are written step by step. Phase 4 is written task by task and is
expanded once Phase 2 has run a session, apart from the Account guard, which is
complete and ships first.

## Out of scope

- Automatic entry of any new structure into the paper Account.
- Paper buttons for structures the Ledger cannot book today.
- Push alerts, trade-idea posts and site ideas from the new lists.
- Changing any credit-spread rule, score or floor.
- New gate bars. They follow from the outcome data phase 4 starts collecting.
