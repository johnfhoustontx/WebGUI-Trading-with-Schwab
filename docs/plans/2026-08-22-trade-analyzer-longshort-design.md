# Trade Analyzer — long/short research + recommendations

**Date:** 2026-08-22
**Status:** Phase 0 shipped; Phases 1–6 designed
**Scope:** `trade-analyzer/src/analysis`, `services/trade_svc`, `services/options_svc`
(one bugfix), `shared/contracts`, `webgui/pages/trade.py`

## Problem

An audit of the Trade Analyzer's two verdicts (Position 1–8 wk, Investor months+)
found the engines sound in shape but weak in three specific ways, and found the
app's richest dataset unused by them.

**Position.** The validated swing model's harness is genuinely good — causal
factors, cross-sectional winsorization, walk-forward with non-overlapping test
windows, isotonic-smoothed calibration bands. Its *payload* is fragile: the
largest single weight is `low_vol` at **−0.34** (now −0.39), i.e. the model
rewards high realized volatility — a faithful description of the 2021→2026 fit
window and a direct contradiction of the long-run low-vol anomaly. The
artifact's `regimes` key was built C-ready for `"trend"/"chop"/"highvol"` and
**only `"all"` has ever been fit**, so the one mechanism designed to fix that
sign has never been used. The legacy heuristic beside it computes RSI/ADX/VWAP
on **5-minute bars** for a 1–8 *week* verdict, and weights sub-daily timeframes
at 62.5% of its EMA-alignment factor.

**Investor.** Designed as a 60/40 fundamental/technical blend; on live Schwab
data roughly **35 of its 100 weight points cannot contribute**. `earnings_traj`
(15) is always 0 — `/instruments` carries no EPS surprises or guidance.
`valuation` (20) runs at half strength — `sector_pe_median` is passed `None`, so
only PEG scores. The FCF gate cannot fire. Live ceiling ≈ **+59.5** against a
designed +74.5 with BUY at +40: a structural HOLD bias, not a judgment about
any company.

**Both.** `days_to_earnings` is always `None`, so the earnings gate — the one
that matters most for a multi-week single-stock hold — is dead on both verdicts.

**Dealer positioning.** The stack collects per-strike GEX/charm/DEX/vanna every
minute for ~93 symbols and publishes per-symbol `call_wall`/`put_wall`/`flip`/
`net_gex`/`atm_iv`/`iv_state`/`dealer_regime` in `cache:options:matrix`. The
Trade Analyzer reads **none** of it (verified: zero gamma/wall/flip/skew
references in `trade-analyzer/src/analysis`, no dealer field in the
`TradeAnalysis` contract).

**And the model is only ever asked about one symbol at a time,** although it
scores that symbol by ranking it against a daily universe cross-section it
computes anyway — and then discards the symbol names.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Verdict fusion | **Keep Position and Investor separate**; add an agreement *note*, never a blended score | A 20-day cross-sectional rank and a months-horizon fundamental screen answer different questions. Precedent: `/sentiment/bullbear` declines to add a third headline and counts its own rows instead |
| Dealer positioning | **Advisory overlay first**; a weight only after IC-testing | Mirrors the house pattern — `scanner_engine.apply_gex_gate` uses positioning as a *gate*, not a return predictor |
| Short side | **Not the long side mirrored** — needs market-state conditioning | Labels are excess return vs SPY: a bottom-band name is predicted to LAG, not to fall. A naked short on a correct SELL read still loses in a rising tape |
| Short expression | **Defined-risk options**, not stock | Borrow mechanics never enter; max loss is structural; matches the existing paper book |
| Scanner vs single-symbol | **The universe is the ruler, not the product** | The per-symbol snapshot lands early because it powers sector-peer/neighbour lines on the *single-symbol* card; the rank board is a later surface of the same data |
| Investor validation | **Deferred until a point-in-time store exists** | Live-parsed ratios would leak today's data into history. Start the store now; it pays in calendar time |

## Phase 0 findings (shipped 2026-08-22)

Phase 0 was bench-clearing: refresh the stale artifact, fix a wall-side bug,
correct a stale docstring. **The refit result is the most important thing in
this document.**

Re-running `fit_swing_model.py` unchanged (same methodology, same 78-name
universe, fresh 5-yr window) moved the model materially:

| | fit 2026-06-28 | fit 2026-08-22 |
|---|---:|---:|
| Composite OOS IC | +0.0367 | **+0.0206** |
| Negative folds | 5 of 13 | 6 of 13 |
| Factors carrying weight | 6 of 10 | 9 of 10 |
| `low_vol` weight | −0.344 | **−0.391** |
| `mom_12_1` mean IC | +0.0407 | **+0.0183** |
| `mom_6_1` mean IC | +0.0332 | **+0.0214** |
| `trend_quality` mean IC | +0.0228 | **+0.0104** |
| `rs_spy` weight | 0.000 | **−0.059** (sign flip) |
| `rs_sector` weight | +0.084 | 0.000 (dropped) |
| Top-band hit rate | 52.29% | 52.68% |

Three readings, in order of importance:

1. **The decay is concentrated in the theoretically-grounded factors.** The
   momentum cluster's IC roughly halved in two months of added data. The
   calibration bands held up (the top band is marginally better), so the *rank*
   still separates outcomes — but the composite's ability to order the
   cross-section fell 44%.
2. **More factors kept is a symptom, not an improvement.** `signed_ic_weights`
   admits any factor with `|mean_ic| > 0.005`. As the strong factors decayed,
   noise crossed the floor: `str_5d` (+0.0058), `pth` (−0.0050) and `rs_spy`
   (−0.0093) now carry weight. **`rs_spy`'s negative weight means the model
   mildly rewards a stock for LAGGING SPY** — theoretically backwards for a
   momentum model, and visible to the user in the evidence expander. Verified
   live on AAPL: `rs_spy z −0.300 × w −0.059 = +0.018` contribution, i.e. AAPL
   scored *up* for underperforming. This is the C12 correlated-cluster problem
   interacting with a too-permissive noise floor, and it is now biting in
   production rather than in theory.
3. **`low_vol` grew its share** exactly as the audit warned: it is now 39% of
   the absolute weight, on an inverted sign, in a softening tape.

**This does not argue against the refit** — the new artifact is fit through
today and is a more honest estimate of the current edge; the old one was scoring
a changed market with June weights. It does sharply re-prioritise Phase 4: the
noise floor and the correlated-momentum cluster are no longer deferred
theoretical findings.

**Open question for Phase 4** (do not fix silently): is `min_abs_ic = 0.005` too
permissive? It is an n-independent floor chosen for stability across folds, and
raising it is a methodology change that must be *measured* (re-fit at several
floors, compare OOS IC), never assumed.

### ⚠ The validated model has never run in prod

Discovered while verifying the refit: **`D:\WebGUI Trading Prod\trade-analyzer\data\`
does not exist.** `SWING_MODEL` resolves under each checkout's own root, the
artifact is **gitignored** (`trade-analyzer/data/`), and `promote.bat` moves code
by `git pull --ff-only` — so no promotion has ever carried an artifact to prod,
and none ever will. `load_artifact()` returns `None`, `score_symbol` is never
called, `swing_block` stays `None`, and `trade.py` renders
`_legacy_verdict_body`.

Confirmed from both ends: the file is absent, and prod's own
`cache:trade:analysis` (db 0, symbol AVAV, 2026-08-16) carries
`swing_model: null` beside a legacy `position_verdict` of HOLD 31.

**So prod's Position card has only ever shown the legacy hand-weighted heuristic**
— the 5-minute-bar engine the validated model was built to replace. Every claim
about the validated model (its OOS IC, its calibrated bands, its ranked-tilt
presentation) describes **dev only**.

Two consequences:

1. **Phase 0's refit does not reach prod by itself.** Deploying it is a manual
   file copy or a prod-side fit run — a deliberate decision, not a mechanical
   step, and one that changes what the Position card displays.
2. **The plan had a gap:** it assumed "re-run the fit" was sufficient. Any
   artifact-producing phase (P4 especially) needs an explicit deployment step,
   and the promote path needs to state how gitignored artifacts travel. Until
   that is settled, treat "shipped to dev" and "live in prod" as genuinely
   different states for anything under a gitignored `data/` directory.

### The wall-side bug

`options_svc/compute._gex_from_snapshot` read `gamma_walls()`'s result
positionally — `put_wall = walls[0]`, `call_wall = walls[1]` — but that helper
**filters `None` out**, so a chain with strikes only above spot returned
`[call_wall]` and the call wall was silently filed as the **put** wall, with no
call wall reported. This fed `rescue.assess_position_risk` / `strategic_context`,
which judge whether a short strike sits past its barrier. `_matrix_dealer_levels`
documents and avoids the same trap by splitting on spot; that call site did not.
Fixed at the consumer (the list contract is pinned by an existing test and the
Gamma page draws that list as wall lines), with the picker's own contract as the
disambiguator: put wall strictly below spot, call wall strictly above. A lone
wall with no usable spot is now **dropped rather than guessed** — a wrong-side
wall is worse than no wall, and the flip still carries context.

## Architecture of the remaining phases

Three tracks run concurrently:

- **Product** (P1 → P2 → P3 → P5): each phase ships user-visible value on the
  single-symbol page *before* the rank board exists.
- **Research** (P4): offline; improves P5's board but never blocks it.
- **Accrual** (stores start in P1, surface in P6): calendar-bound, not
  effort-bound — which is why they land first.

**P1 Data foundations.** Parse `shortIntToFloat`/`shortIntDayToCover` (already in
the payload every analyze fetches). Merge the already-written
`parse_finviz_fundamentals` over Schwab, filling only `None` fields, reviving the
earnings gate on both verdicts. Compute `sector_pe_median` from the existing
sector map's peers and average only *available* valuation sub-scores. Start
`fundamentals_history.db` and `rec_journal.db`.

**P2 Both sides + context.** Mirrored short gates (above rising 200-EMA, sector
uptrend) plus a squeeze gate. A `direction_clearance` block (SPY vs 200-DMA +
the committed regime, staleness-guarded) yielding cleared / relative-only /
blocked per side, with **both sides always rendered** — a blocked short with its
reasons is a research finding. A dealer/IV context line joined from
`cache:options:matrix` behind the Desk's trustworthiness guard. The per-symbol
universe snapshot, powering sector-peer rank and nearest-neighbour lines.

**P3 The trade ticket.** Unify the two opposite 25Δ skew sign conventions and the
three gamma-flip algorithms *first*. Then a pure structure matrix (side × IV
state × dealer levels → structure + 30–45 DTE tenor) and a Trade Plan block:
entry zone, ATR stop, calibrated target, earnings line, and a **20-trading-day
time stop** (the model's own horizon; nothing enforces it today).

**P4 Model refit.** Universe to ~150 optionable names. A regime classifier in
`src/analysis` (shared by fit and live scorer — one source), per-regime weights
into the artifact keys built for them, scorer selector with `"all"` fallback.
Covariance-aware weighting vs signed-IC — keep whichever wins OOS. The noise
floor measured, not assumed. Backtestable short factors (MAX effect, downside
beta, distance-below-200-EMA); short interest stays a *gate* until the P1 store
gives it history. Folds tagged by prevailing regime; long/short split stats.

**P5 Rank board.** Score the per-symbol snapshot through the existing scorer →
`cache:trade:rank_board`; top/bottom deciles as candidate pools with gates and
dealer columns.

**P6 Feedback loop.** Nightly labeler + live IC monitor split long vs short;
an isolated model paper book auto-trading both cleared deciles; monthly
scheduled refits; Investor validation once the PIT store matures.

## Phase 4 findings (2026-08-22) — the model is a beta bet

**Read this before touching the swing model again.** Phase 4 set out to raise
the noise floor, expand the universe, condition weights on regime and fix the
correlated-cluster problem. Four of those were measured; the fifth question —
one nobody had asked — turned out to subsume them all.

Every study ran against ONE cached panel per configuration
(`trade-analyzer/research/panel_cache.py`), because Phase 0 moved OOS IC 44%
with no methodology change at all: a variant comparison that re-fetched between
runs cannot separate a methodology effect from a fetch-date effect. Every
comparison is a PAIRED test over the same walk-forward folds, because at this
signal level the ranking of five means is not a result.

### The headline: the composite is beta, not alpha

Splitting the 173-name panel on the market's own forward 20-day return:

| | market UP | market DOWN |
|---|---:|---:|
| composite (shipping weights) | **+0.1598** | **-0.1142** |
| composite (orthogonalized weights) | +0.1800 | -0.1282 |
| `downside_beta` | -0.1923 | +0.1702 |
| `low_vol` | -0.1507 | +0.1195 |
| `semivol` | -0.1465 | +0.0991 |

**Nine of fourteen factors flip sign with the market**, and the down-market
weight set is nearly the negation of the up-market one. Averaged over a window
that was roughly 2:1 up, that nets to the small positive OOS IC every study in
this phase measured.

The cause is the **label**. `r_symbol - r_SPY` is a RAW excess return, so a
high-beta stock earns positive excess whenever the market rises — mechanically,
no skill. Fit over a mostly-rising five years, any model on this label MUST
discover that volatile names outperform. See `research/labels.py` for the
beta-adjusted alternative.

⚠ **The regime split could not have caught this**, which is worth remembering
before trusting a regime cut again: `highvol` is a VOLATILITY regime, so a
violent rally and a violent selloff both land in it.

⚠ **Splitting on a FORWARD market return is look-ahead** and is labelled a
diagnostic throughout. It could never be traded. The question it answers is not
"what should we buy" but "what does this model do when the market falls" — a
property of the model, knowable only by looking.

### What that does to the other four results

Each was individually measured, and each turns out to be a measurement of how
much beta the configuration loads:

| task | result | paired t | verdict |
|---|---|---:|---|
| 4.1 noise floor | no floor differs from 0.005 | all \|t\| < 1.4 | **keep 0.005** |
| 4.2 universe 78 → 173 | +0.0206 → +0.0333 | +0.82 | **do not adopt** |
| 4.3 regime-conditioned weights | +0.0206 → +0.0128 | −0.37 | **do not adopt** |
| 4.4 orthogonalized residual IC | +0.0206 → +0.0834 | +3.01 | **do not adopt — see below** |
| 4.5 short-factor slate | +0.0206 → +0.0698 | +2.64 | **do not adopt — see below** |

The last two are the trap. Both are large and (4.4) nominally significant, and
both are **pre-specified fixes for documented problems** (C12's correlated
cluster; the absence of asymmetric factors), so neither is a fishing result.
They still must not ship: each wins by concentrating weight on the volatility
cluster, and the up/down table above shows exactly what that buys. The
orthogonalized weighting has a *worse* down-market IC than the scheme it
replaces.

Note also that this phase ran roughly nine comparisons; a Bonferroni-style
correction at 13 folds would want |t| > ~3.4, which even +3.01 does not reach.

### C13 is refuted, not deferred

`low_vol`'s inverted sign was documented as a regime artifact to be fixed by
regime-gating. It is not one. Per-regime IC: **trend −0.0972, chop −0.1254,
highvol −0.0721** — the same sign in all three, and *stronger* in every regime
than pooled (−0.0614). Regime-conditioned weights measured worse than pooled.

### The sample cannot support per-regime fits anyway

Regime mix over the 5-year window (984 days after warmup): trend 653 / highvol
182 / chop 149. One walk-forward fold needs `train + test` = **441** days, so
only `trend` clears the floor — and at 66% of the sample a "trend" block is the
pooled fit wearing a regime label. Lengthening the window to 15+ years would
populate the others, but an edge that decays 44% in two months should not be
estimated on a market that far gone. The machinery ships (`research/artifact.py`
builds the keys, `swing_model` selects them, the card names the one that scored)
and the keys stay empty until a fit can fill them honestly.

### What DID ship from Phase 4

- **Out-of-sample calibration.** The artifact calibrated its bands on the same
  rows its weights were fitted on, so the "calibrated mean" the Trade page
  prints as an expectation was an in-sample statistic. Measured: the top band's
  hit rate is **49.86% out of sample against 52.68% in-sample**. Both bands are
  now built from walk-forward test windows, with the in-sample set retained as
  `calibration_insample` so the flattery is visible rather than assumed.
- **The bottom band's edge is real** (the exit criterion's question): OOS mean
  forward **−0.93%** vs SPY over 20 days, against **+0.85%** at the top.
- ⚠ **Every band's hit rate is below 50%,** top included. The label is excess
  return vs a cap-weighted index, so most stocks lose to SPY most of the time;
  the mean is the honest read and the hit rate is not a coin-flip comparison.
- **The exposure is now on the card.** `risk_share` — the share of absolute
  weight on volatility factors — is computed by the scorer and rendered in the
  evidence expander. The live artifact reads **47.6%**.

### Exit criterion

Phase 4's gate was: does the new artifact OOS-beat the single-regime fit? Two
configurations do, numerically. **The gate fails on the question it was standing
in for** — those gains are beta loading, not cross-sectional skill — so per the
plan, the Phase-0 artifact stays primary and the outcome is written down here.

## Standing guardrails

- **Overlays before weights.** Dealer data, clearance and context inform; only
  the IC-tested harness grants scoring weight.
- **Keep the tilt posture.** Every new surface inherits "ranked tilt, not a
  verdict" (`trade.swing_tilt`). A 52%-hit-rate edge must never render as a bold
  BUY — and after the Phase 0 refit the measured edge is *thinner*, not thicker.
- **No third headline.** Position and Investor stay separate voices.
- **Acceptance gate unchanged:** positive OOS IC and a real spread, or the model
  is not promoted — and a failed gate is documented, not hidden.

## Out of scope (YAGNI)

ML models; intraday factors in the swing model; a paid fundamentals vendor;
shorting stock rather than options; blending the two verdicts.
