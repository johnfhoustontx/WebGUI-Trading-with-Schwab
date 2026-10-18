# Opening Scalp — design

**Date:** 2026-10-18 · **Status:** written from the brainstorm, awaiting the
operator's review.

**Asked for by the operator:** "an app with a basic interface that is a scalping
strategy for options for just $SPX, SPY, QQQ. Look at the premarket price action
and give me the highest probability that the price will move in a particular
direction after the first 5 minutes after the start of RTH. Strategy —
directional / CCS / PCS."

Calls marked **(my call)** were made by Claude while writing this and were not
discussed. The operator can overturn any of them.

## 1. Intent

Every trading morning the operator reads the premarket themself (Asia and Europe,
the ES/NQ futures, a handful of sector ETFs, the premarket report, the news and
flow alerts) and decides whether to scalp the open. This page gives them a
**measured** answer to one question, per symbol, at 08:35 CT:

> Given how this morning set up, how often has a 0DTE scalp in each direction
> reached +50% on its premium before −50%, within an hour?

The operator **reads the call and decides themself.** The page places nothing:
there is no Paper button and no order route (this app is paper-only and the proxy
has no order route anyway).

Why it has to be measured rather than asserted: the operator has repeatedly
preferred an honest weak result to a confident one, and this sample is small (§4).
A number on this page is only worth showing if it beat the plain base rate on
mornings it was not fitted to.

## 2. Goals, non-goals, anti-goals

**Goals**
- One call per symbol ($SPX, SPY, QQQ) shortly after 08:35 CT, plus a manual
  refresh.
- For each symbol, the win probability of four 0DTE structures — long call,
  long put, put credit spread (PCS), call credit spread (CCS) — conditioned on
  the morning's measured inputs, with the base rate, the sample size and an
  interval beside it.
- The live point distances that would take each structure to its target and its
  stop, from that morning's real 0DTE chain.
- The unmeasured context (report, news, flow alerts) on the same screen, kept
  apart from the number.
- Start **saving the bars now**: Schwab's 1-minute history rolls off after about
  six weeks and the 5-minute history after about seven months, so every day not
  saved is history lost.
- A running scorecard: each day's call, graded afterwards from the bars.

**Non-goals (v1)**
- No trade entry of any kind, paper or real.
- No in-trade management: no live exit alerts, no trailing. The ±50% rule and
  the 60-minute time stop define the backtest's exit only.
- No public (`live.`) screen.
- No Claude call. The page writes nothing in prose that code did not compute.
- No symbols beyond $SPX, SPY and QQQ.
- Reports, news and flow alerts do not feed the probability (§5.3).

**Anti-goals — it fails even if it works if:**
- it shows a probability to one decimal that rests on 11 mornings without saying
  so;
- it shows a directional call on a morning where nothing measured beats the base
  rate (it must say "no measured edge" instead);
- the probability was chosen by looking at the same mornings it is scored on;
- its 08:3x fetches push the one-minute GEX poll off its slot (08:30–08:36 is
  the busiest window of the day for the proxy — CLAUDE.md "Every service shares
  the proxy's 5 requests a second").

## 3. Trade definitions (the operator's)

| Structure | Contract | Win | Loss |
|---|---|---|---|
| Long call | 0DTE call, at the money or one strike out of it | premium +50% | premium −50% |
| Long put | 0DTE put, same | premium +50% | premium −50% |
| PCS | 0DTE short put at or one strike out of the money, long put `width` below | buy back at 50% of the credit | spread worth 1.5× the credit |
| CCS | mirror of the PCS on calls | same | same |

- **Entry:** the close of the 08:30–08:35 bar (the first five minutes).
- **Time stop:** 60 minutes after entry (`time_stop_min`). A trade that reaches
  neither level by then is a **timeout**, scored as its own outcome and never
  folded into wins.
- **Strike rule (my call):** the operator said "at or slightly out of the money".
  Every contract the structure is short or long at entry — the long call, the long
  put, and the SHORT leg of each spread — is the nearest strike that is at the
  money or out of the money **for that contract**: a call at or above spot, a put
  at or below spot. So a PCS's short put sits at or just below spot, a CCS's short
  call at or just above. `otm_steps = 0`, configurable to one step further out.
- **Spread width (my call):** $SPX 5, SPY 1, QQQ 1 — config.
- **The spread win/loss rule, confirmed by the operator 2026-10-18:** win when
  the spread can be bought back for half the credit (50% kept); stop when the
  spread is worth 1.5× the credit (a loss of half the credit).

## 4. Data — what exists (measured 2026-10-18 on prod)

| Source | What | Depth |
|---|---|---|
| Schwab price history, 5-min, extended hours | SPY, QQQ, IGV, SMH, EWY, XLE, XLK, XLV, /ES, /NQ, $VIX | ~7 months (from about 2026-03-01 to 03-30 depending on symbol); a request returns at most 40,000 candles, so long ranges must be paged |
| Schwab price history, 1-min, extended hours | the same | ~6 weeks (from 2026-09-02) |
| $SPX price history | regular session only — the index has no premarket | 1-min ~6 weeks, 5-min ~7 months |
| $VXN | 08:30 onward only | ~7 months |
| `$VIX1D` | not served (empty) | — |
| `gex_history.db` | GEX/charm/DEX/vanna per minute, 08:00–15:19 CT | 5 sessions |
| `hiro_minutes` | per minute, from 08:31 | since 2026-10-02 |
| Raw chain store (`config/feed.toml`) | **`mode = "off"` on prod — nothing is being collected**, and its design keeps 5 sessions of chains from 08:00 | none |
| Past option prices | Schwab serves none | — |

Consequences:
1. **The raw database the operator named cannot be the source.** It is off, it
   holds chains rather than bars, and even on it keeps 5 sessions. This design
   does not depend on it. (Turning it on is a separate decision under its own
   design, `2026-10-14-raw-feed-and-catch-up-design.md`.)
2. **The backtest sample is ~140 mornings of 5-minute bars**, of which ~33 also
   have 1-minute bars. Enough to see a strong pattern; not enough for many
   features (§5.2).
3. **$SPX's premarket is read from /ES.** Its first five minutes come from $SPX
   itself.
4. **Premiums in the backtest are modelled**, not observed (§5.1).
5. The proxy's `/pricehistory` route takes no `startDate`/`endDate`; only
   `/passthrough` does. The bar store needs dated requests, so either the route
   grows the two parameters or the store goes through `/passthrough`
   (builder's choice). ⚠ The proxy is a feed file: a proxy change promotes
   outside the trading window only.

## 5. How the probability is made

### 5.1 Outcomes (the labels)

For every stored morning, symbol and structure, simulate the trade along the
real bar path:
- **Implied volatility at entry:** $VIX at 08:35 for $SPX and SPY, $VXN for QQQ,
  times `iv_scale` (default 1.0). This is an approximation of a 0DTE contract's
  own IV, which runs differently from a 30-day index. The live chain captures
  (§6.3) are what later calibrate `iv_scale`.
- **Valuation:** Black–Scholes through the repo's one pricer
  (`options_calculator`, its `RISK_FREE_RATE` and `expiry_time_to_years` — never
  an inline time-to-expiry), time to the 15:00 CT settlement shrinking minute by
  minute, IV held at the entry value.
- **Touch rule:** walk the bars. A level is reached when the bar's high or low
  would price the structure to it. With 5-minute bars, **a bar that reaches both
  the win and the loss levels counts as a loss** (conservative), and how often that
  happened is reported beside the result. The study's verdict uses 5-minute bars
  throughout (§5.2); 1-minute bars measure that rule's bias and grade the live
  scorecard.
- Output per (date, symbol, structure): `win` / `loss` / `timeout`, minutes to
  exit, and the underlying points to each level.

### 5.2 Inputs (the features) — the operator's premarket routine, made measurable

| Operator's input | Measured as |
|---|---|
| Asia session | /ES and /NQ return 17:00 → 02:00 CT |
| Europe session | /ES and /NQ return 02:00 → 08:30 CT |
| ES / NQ into the open | gap vs the prior 15:00 CT settle; where 08:30 sits in the overnight range |
| Sector ETFs: IGV, SMH, EWY → QQQ; XLE, XLK, XLV → SPY / broad | premarket return vs prior close, each |
| (implied) | $VIX premarket change |
| First five minutes | the symbol's own 08:30–08:35 return, its range against the trailing average opening range, close location in the bar, and whether it took out the premarket high or low |

Symbol mapping (my call, following the operator's arrows): QQQ reads /NQ, IGV,
SMH, EWY, XLK; SPY and $SPX read /ES, XLE, XLK, XLV. ⚠ XLV's premarket is thin
(about a quarter of the bars the others have); a missing input is ABSENT, never
zero (CLAUDE.md "Sentiment and regime scoring" — the same rule).

**The model (my call — the one place a wrong choice silently ruins the page).**
With ~140 mornings and ~15 inputs, a fitted model would find patterns that are
not there. v1 is deliberately low-freedom and **declared before looking at
outcomes:**

1. The first five minutes set a direction (up or down). A morning whose first
   bar moved less than `dead_zone_pct` has no direction and gets no call.
2. **Outcomes are recorded relative to that direction (my call)**, so up and
   down mornings pool into one sample instead of halving it. The four structures
   become four roles:

   | Role | Up morning | Down morning |
   |---|---|---|
   | Long, with the open | long call | long put |
   | Long, against the open | long put | long call |
   | Credit, with the open | PCS | CCS |
   | Credit, against the open | CCS | PCS |

   The page names the actual contract for that morning ("long put" on a down
   morning) and shows all four, so a fade is visible too. Up-only and down-only
   rates are reported in the study as a diagnostic, never as the number shown.
3. An **alignment score** counts how many of the symbol's other inputs agree with
   that direction (each input's sign; a dead zone of `dead_zone_pct` around zero
   counts as neither).
4. Mornings are bucketed by alignment (`buckets`, e.g. low / mid / high).
5. Each bucket's win rate per role is the probability shown, with a Wilson
   interval and its sample count.
6. **Per symbol (my call).** $SPX, SPY and QQQ are not independent, so pooling
   would triple the count without tripling the evidence. Each symbol is its own
   sample (~140 mornings, ~45 a bucket). A pooled run appears in the study as a
   diagnostic only.

**Bar resolution for the verdict (my call):** the study's verdict uses **5-minute
bars on every morning**, so the "both levels in one bar is a loss" bias is the
same across the whole walk-forward window and cannot masquerade as an edge where
the 1-minute era begins. On the mornings that have both, the 1-minute result is
computed alongside to measure how large that bias is.

**Volatility sensitivity (my call):** the verdict is reported at `iv_scale` 0.8,
1.0, 1.25 and 1.5. A bucket counts as an edge only if it beats the base rate
across that range; an edge that exists at one scale only is reported as such and
not shown on the page.

**Evaluation is walk-forward:** each morning is scored only with buckets built
from the mornings before it. The page shows a structure's probability as a call
only if, walk-forward, that bucket's rate beat the structure's unconditional base
rate (Brier score lower than the base rate's) over at least `min_samples`
mornings. Otherwise it shows the base rate with "no measured edge".

A study step (§8, step 2) is allowed to propose a different model (for example a
regularised logistic regression on a handful of inputs) and replaces the
alignment score only if it beats it walk-forward. That comparison, and its
numbers, go in a dated `docs/reference/` note, not here.

### 5.3 What is shown but not measured

- **Premarket and prior-close reports:** a link to `neuralstrike.co/report.html`
  (and its title line if one can be read cheaply). Not parsed into the number.
- **News:** the top `news_items` headlines from `cache:news:feed` mentioning SPY,
  QQQ, SPX/ES, or the market.
- **Flow alerts:** today's alerts on the three symbols from
  `cache:options:flow_alerts`, since 08:30.

These carry a visible "not in the probability" label. When enough of their own
history exists they can be tested the same way and promoted (a later design).

## 6. Components

All Tier-2 code lives in `services/options_svc` as **sibling modules that import
nothing from `compute.py`** (CLAUDE.md: its line count only goes down). Names are
the builder's.

### 6.1 The bar store

- SQLite, one file, path through `repo_paths`, `db_path=None` resolved at call
  time. Rows: `(symbol, interval, ts, open, high, low, close, volume)`, primary
  key `(symbol, interval, ts)`; `interval` ∈ {1, 5}.
- **Kept forever** (my call): ~14 symbols × ~1,300 one-minute bars a day is a few
  MB a month. Gitignored, and **in the nightly backup** — this is history that
  cannot be re-fetched once Schwab drops it.
- **Initial backfill:** page backwards through Schwab's retention for every
  symbol, 1- and 5-minute, extended hours.
- **Daily top-up:** a scheduler slot after the close (`[slots.scalp_bars]`, my
  call 16:20 CT — clear of the 16:00 flow-delta report and the collection's
  15:20 stop). ~28 price-history calls a day.
- Symbols: `$SPX, SPY, QQQ, /ES, /NQ, IGV, SMH, EWY, XLE, XLK, XLV, $VIX, $VXN`
  — the list is config.

### 6.2 The study and the outcome table

- A module that builds §5.1's outcomes and §5.2's inputs from the bar store into
  an outcome table (same DB or a sibling), rebuilt nightly after the top-up.
- The walk-forward evaluation and the bucket rates are computed from that table.
  Deterministic: the same bars give the same numbers.

### 6.3 The morning call

- **Slot:** `[slots.scalp] call = "08:36"` (my call). Not 08:35: the autoscan
  fetches from :32 to about :35, and Schwab refuses in the first minute after
  the half hour. ⚠ Before fixing the minute, read the proxy's access log for
  08:30–08:40 on a few mornings (CLAUDE.md asks exactly this before a new fan-out).
- **Fetches:** today's 1-minute bars for the 13 symbols (or only the ones not
  already current) and **one 0DTE chain each for $SPX, SPY and QQQ**, through the
  proxy, normal lane (never `X-Priority` — that lane is the GEX poll's alone).
- **Computes:** the inputs, the bucket, each structure's probability, and from
  the live chain each structure's real entry premium and the underlying points
  to its +50% / −50% levels.
- **Saves** the near-the-money slice of the three 0DTE chains and the call itself
  (the day's record for the scorecard and for calibrating `iv_scale`).
- **Publishes** one view, `cache:options:scalp` (name the builder's), with a small
  write-side contract model (the page reads only that view).
- **Refresh:** a `cmd:options` command (`_cmd_scalp_refresh`, a row in
  `handlers._COMMANDS`, slow lane — it touches no paper book), replay-guarded so
  a stale click never re-fetches.
- **Grading (my call):** the nightly study run grades **all four roles every
  day**, whatever was highlighted, so the scorecard has no selection bias. Each is
  graded twice: on modelled premiums from the bars (the same method as the study)
  and from the real 08:36 entry premium captured from the chain (the check on the
  model). The scorecard's headline is the highlighted role's hit rate on the days
  one was highlighted, beside that role's base rate; a no-edge day counts in the
  all-roles table only.

### 6.4 The page

- Route `/options/scalp`, label **"Opening Scalp"** (my call), in the Options
  group. Built from the page kit; Tailwind only; registered in `test_shell.py`
  and `test_no_inline_style.py`; a `page_help.py` entry.
- **One card per symbol:** the first-five-minutes direction; the four structures
  each with probability, interval, sample count and base rate (the best one that
  shows a measured edge highlighted, or "no measured edge"); the live strikes,
  entry premium and point distances to target and stop.
- **Inputs panel:** each input's value and whether it agreed, so the operator can
  check the call against their own read.
- **Context panel:** report link, headlines, flow alerts — labelled as not in the
  probability.
- **Scorecard:** the last N graded calls and the running hit rate against the
  base rate.
- Before 08:36 the page shows the previous call and a "next call at 08:36" line.
  Version-gated repaint (`watch_view`).

## 7. Configuration

`config/scalp.toml`, read through `shared/config_toml.toml_loader`, every key in
`webgui/config_schema.py`: symbols and their input mapping, `otm_steps`, spread
widths, `target_pct` (50), `stop_pct` (50), `time_stop_min` (60), `iv_scale`,
`dead_zone_pct`, `buckets`, `min_samples`, `news_items`, scorecard length. Slot
times in `config/sessions.toml`.

## 8. Build order (smallest usable slice first)

1. **The bar store + backfill + daily top-up, plus the 08:36 capture of the three
   0DTE chains' near-the-money slice** — first, because history is rolling off
   now, and because the chain captures are what calibrate `iv_scale`: each week
   they run before the page exists is a week of calibration already banked
   (three chain calls a day).
2. **The study:** outcomes, inputs, walk-forward evaluation; its findings written
   up in a `docs/reference/` note **before** any page is built. If nothing beats
   the base rate, the operator hears that first and decides whether the page is
   still wanted.
3. **The morning call** (slot, chains, view, refresh command).
4. **The page.**
5. Grading and the scorecard.

## 9. Testing

- Outcome simulator: hand-built bar paths with known answers (win first, loss
  first, both in one bar → loss, timeout, settlement-time decay).
- Walk-forward: a test that fails if any morning's probability used that morning
  or a later one.
- Absent inputs stay absent (a missing ETF bar never scores as "agrees" or as
  zero).
- The page through the local page harness on a fake bus.
- The view's contract, the command's replay guard, the config catalogue.

## 10. Documentation with the build

CHANGELOG; `docs/webgui-routes.md` (the route); the User Guide and Reference
Guide (a new page); the Technical Reference (how the probability is computed);
`webgui/page_help.py`; CLAUDE.md only if an invariant changes (the bar store's
"never drop" rule may be one).

## 11. Left to the builder

Module and view names; whether the proxy's `/pricehistory` grows dated parameters
or the store uses `/passthrough`; the exact bucket boundaries (declared in config
before the study is read); page layout details within §6.4.
