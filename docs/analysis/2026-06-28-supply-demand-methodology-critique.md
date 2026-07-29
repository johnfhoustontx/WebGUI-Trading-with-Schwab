# Critical Analysis — the Supply/Demand ("Market Structure") methodology

**Date:** 2026-06-28
**Status:** Research / decision note. **Nothing here is implemented.** This evaluates a
third-party trading methodology for possible integration into the Trade Analyzer and
recommends what (if anything) to take from it.

> **Written to match this project's ethos.** We just spent a session (a) replacing an
> unvalidated legacy verdict with a backtested factor model, (b) softening a thin edge
> from a bold "BUY" to a "slight tilt," and (c) collapsing four contradicting Position
> opinions into one validated voice. This critique holds the new methodology to the same
> bar: *disclosed math, measured out-of-sample edge, costs counted, anecdotes distrusted.*

---

## TL;DR

The methodology has **two metrics** — **Demand** (a proprietary 1–10 "Market Structure
Sentiment" score) and **Supply** (short-volume %) — and a rule: *buy rising Demand + falling
Supply above the 5.0 "nexus"; sell the reverse; take gains fast.*

- **~Half of it is sound-but-generic** and you **already have it in validated form**:
  the options-expiration "structural events" idea (your Gamma/GEX page), a broad macro
  risk gate (your five-state Market Trend classifier), a liquidity gate (the swing
  scanner), and "backtest first" (your `fit_swing_model.py` harness).
- **The core rule is a black box on a data-mined threshold, evidenced only by anecdotes.**
  The Demand metric's formula is undisclosed, so it is *un-validatable and un-buildable*;
  the 5.0 nexus and 50% / 4.0–6.0 cutoffs are round numbers with no stated derivation; the
  NVDA/TSLA/TLRY/PM cases are hand-picked hindsight, not a backtest. It **preaches
  validation and shows none.**
- **The one genuinely novel, sourceable, testable idea is short-volume-% as a signal.**
  Recommendation: **don't adopt the system.** Instead feed short-volume % into your
  existing validation harness as a *candidate factor* and let out-of-sample IC decide —
  exactly how every other factor earned (or failed to earn) its weight. Reject the
  black-box Demand metric and the 5.0-nexus rule outright.

---

## 1. The disqualifier for as-is adoption: the Demand metric is a black box

"Demand" / "Market Structure Sentiment" is a proprietary 1–10 score with an **undisclosed
construction**. You cannot validate, replicate, or *implement* what you cannot compute.
Every rule keyed to it — the 5.0 nexus, "surges over 5.0," "trending up" — is therefore
both **unfalsifiable** and **un-buildable in your app**.

And note what it most likely *is*: a score that runs "oversold (1) → overbought (10)" is,
mechanically, a **smoothed oscillator / momentum read**. If so, "buy Demand > 5.0 and
rising" is **momentum trading in proprietary clothing** — which your validated model
already captures via `mom_12_1` / `mom_6_1` / `trend_quality`, with *disclosed* math and a
*measured* out-of-sample IC. Paying (in money or in a data dependency) for a black-boxed
re-skin of momentum is a poor trade.

---

## 2. Metric-by-metric

### Demand (1–10, "Market Structure Sentiment")
- **Undisclosed → unfalsifiable and un-buildable.** This alone blocks direct adoption.
- **The 5.0 "nexus" is a data-mined parameter.** A round number in the exact middle of a
  1–10 scale, presented as a hard pivot, with no stated derivation. Fixed-threshold rules
  whipsaw around the boundary and overfit to the sample they were tuned on.
- **Level + direction is reasonable in principle** ("over 5.0 *and* rising"), but the
  undisclosed smoothing sets the lag. A confirmation signal that only fires after both
  conditions hold tends to fire *late* — much of the move can be spent by then.

### Supply = Short-Volume %
- **This is the one concretely-defined input, and it is sourceable.** FINRA publishes
  **daily short-sale volume per symbol** (the Reg SHO / `CNMSshvol` daily files, free);
  exchanges publish it too. **Schwab's API does not** — so "Supply" needs a *new data
  pipeline*, not a Schwab call.
- **But the signal is conceptually muddled.** Short **volume** is *not* "borrowed vs owned
  shares as supply pressure." A large share of FINRA daily short volume is **bona-fide
  market-maker hedging and intraday shorts covered the same day** — liquidity provision,
  not directional bearish conviction. Liquid large caps routinely print **45–55% short
  volume** with no bearish meaning. So ">50% hinders gains" risks confusing *"liquid /
  high market-maker activity"* with *"bearish."*
- **Short volume ≠ short interest.** The more-studied bearish signal is short *interest*
  (settled shares short, borrow cost, days-to-cover) — and even that is weak and
  regime-dependent. Daily short *volume* is the noisier cousin.

### The core rule — "buy rising Demand + falling Supply, take gains fast"
- **It's confirmation/divergence trading** — sensible in spirit (two independent signals
  beat one).
- **But it is coincident/lagging, high-turnover, and stop-less.** The only exit is a
  signal reversal ("Supply jumps or Demand < 5.0"), which whipsaws in chop. "Take gains
  fast" maximizes **turnover**, and turnover is where net edge dies — commissions,
  slippage, and tax drag, all of which you already model elsewhere and know matter. The
  pitch never nets them out.

### Broad Market Sentiment + the "4.0–6.0" claim
- **This is the market-timing pitch.** The asymmetry-of-days argument ("avoid down days,
  catch up days") is real *in isolation* — but the well-replicated counter is that **the
  best and worst days cluster together** (they arrive within days of each other in
  high-vol regimes), so rules that dodge the worst days tend to **miss the best days too**,
  netting to underperformance vs buy-and-hold *after costs*. This is one of the most-studied
  results in the timing literature.
- **"Nearly all gains occur in 4.0–6.0 when Broad Demand is rising"** is stated with **no
  methodology** — window? universe? net of costs? in-sample or out-of-sample? As phrased,
  it is effectively **unfalsifiable and almost certainly in-sample**.
- You already have a broad risk gate: the **five-state Market Trend & Sentiment
  classifier**, whose honest validated edge is a *thin, regime-dependent* ordinal IC
  ≈ +0.087. Same idea, disclosed math, and a measured (small) edge instead of a round-number
  claim.

---

## 3. Supporting principles — graded

| Principle | Grade | Why |
|---|---|---|
| Options expirations are structural events | **A** | The strongest, most-defensible claim — and **you already implement it**: Gamma/GEX (dealer gamma flip/walls, max-pain, monthly OpEx) + flow alerts *are* this. Keep leaning here. |
| $/Trade liquidity gate | **A−** | Sound, standard market-impact sizing; **already present** (swing scanner's liquidity hard gate on bid/ask/volume/OI; paper sizing). |
| Backtest ("Profiler") first | **A in theory / F in practice** | The best principle — and the methodology *violates it*, presenting anecdotes instead of backtests. Hold it to its own standard. |
| Divergence over direction | **C** | Fine in spirit (confirmation is good), but "pulling apart" is loosely defined → cherry-pickable in examples. |
| Fundamentals don't override structure | **C−** | Overstated. Earnings surprises drive **post-earnings drift (PEAD)**, among the strongest short-horizon effects. Your app splits **Position (technical)** vs **Investor (fundamental)** precisely because both matter on different horizons. |
| Trade "stability" (Demand ≥ 5.0) in volatile markets | **C** | Rests on the black-box metric + a single anecdote (PM +20 pts). "Low-vol / defended names outperform in drawdowns" has some support, but that's the *low-vol anomaly* — which your fit already tests (`low_vol`, and note its **inverted sign** in this regime). |

---

## 4. Cross-cutting red flags

1. **Anecdotes ≠ evidence.** NVDA / TSLA / TLRY / PM are hand-picked, hindsight-selected
   illustrations. Every methodology has winning examples; a curated set of them is
   **narrative fallacy**, not a backtest.
2. **Data-mined thresholds.** 5.0, 50%, 4.0–6.0 — round numbers, no derivation shown.
3. **Undisclosed core metric.** Un-auditable, so un-trustable.
4. **Costs and turnover ignored.** The "fast gains" framing is exactly where realized net
   edge tends to vanish.
5. **Preaches validation, provides none.** It fails its own "Profiler" standard.

---

## 5. What's actually additive — mapping onto what you already have

| Their piece | You already have | Verdict |
|---|---|---|
| Demand (a momentum-ish score) | Validated factor model (`mom_12_1`/`mom_6_1`/`trend_quality`) | **Redundant** — and yours is validated with disclosed math |
| Broad Sentiment risk gate | Five-state Market Trend classifier | **Redundant** — yours has a measured (thin, honest) edge |
| OpEx as structural events | Gamma/GEX page + flow alerts | **Already done** — keep leaning here |
| $/Trade liquidity gate | Swing scanner liquidity gate + paper sizing | **Already done** |
| Backtest-first | `fit_swing_model.py` walk-forward harness | **Already your ethos** |
| **Supply = short-volume %** | **Not in the app** | **The one genuinely novel, testable input** |

**Net: the only additive idea is short-volume-% as a signal** — and it's the piece with
the weakest theoretical footing (market-maker noise) and a new data dependency.

---

## 6. Recommendation (validation-first)

1. **Reject as-is.** Do not adopt the black-box Demand metric or the 5.0-nexus rule — they
   are un-buildable *and* un-validatable, and much of the framework is a re-skin of what you
   already do better.
2. **Test the one novel piece the right way.** Add short-volume % (and derived features) as
   **candidate factors** in the *existing* harness and let it decide:
   - Source: FINRA daily short-volume files (free); `short_vol_pct = short_vol / total_vol`.
   - Features: the **level**, a **5–20d trend** (their thesis: *falling* Supply = bullish),
     and an **interaction** `momentum_up AND short_vol_falling` (their "diverging S/D").
   - Gate: run through `backtest.py` `factor_ic` / `walk_forward` on the ~78-name fit
     universe with the 20-day forward excess-vs-SPY label. Keep it **only** if it clears the
     same bar every other factor did — **positive OOS IC + a monotone quantile spread**.
   - **Honest prior:** given the market-maker noise in daily short volume, the base rate for
     a durable, tradable IC here is **low**. Expect to discard it; keep it only if it
     surprises you. (This is exactly how `pth` / `str_5d` / `vol_adj_mom` / `rs_spy` already
     earned **zero** weight — the harness is built to say "no.")
3. **Do not add a standalone "Demand/Supply" verdict to the UI.** You just collapsed four
   contradicting Position opinions into one validated voice; a fifth proprietary verdict
   would re-introduce the exact contradiction you removed. If short volume validates, it
   becomes *one weighted factor inside the existing composite* — not a new headline.
4. **Keep leaning on the parts you already do better:** Gamma/GEX for OpEx structure, the
   five-state classifier for the macro gate, and the liquidity gate.

---

## 7. If you decide to test short volume — concrete sketch

- **Data ingestion (offline/service):** download FINRA `CNMSshvol*.txt` daily consolidated
  short-volume files; store per (date, symbol) `short_vol`, `total_vol`. Free, but a new
  fetch/parse/store path (not a Schwab call). Watch the caveats: consolidated vs per-venue,
  and that a big chunk is market-making.
- **Factor definitions** (add to `trade-analyzer/src/analysis/factors.py`, causal + sign-
  corrected so higher = more bullish):
  - `short_vol_pct` (level; likely sign: high = bearish → negative),
  - `short_vol_trend_20d` (falling = bullish per the thesis),
  - `demand_supply_divergence` = momentum-up × short-vol-falling (their signature setup).
- **Validation:** the `FACTORS` registry + `fit_swing_model.py` already decide weights by
  signed IC; just let these compete. **No hand-set weights, no 5.0 threshold.**
- **Wire live only if it passes.** A passing factor flows into the existing `swing_model`
  composite → the same single Position tilt. A failing one is dropped, documented, done.

**Bottom line:** the methodology's *discipline* (structure over hype, backtest before you
trust, respect expirations and liquidity) is good and you already embody it. Its *specific
signals* are either things you already have in validated form, or a black box you can't
build, or one noisy-but-testable input worth a cheap experiment you should expect to fail.
