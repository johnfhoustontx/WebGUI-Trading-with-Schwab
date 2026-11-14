# Machine learning roadmap

Date: 2026-11-14. Status: proposal, nothing built.

Source: the operator's sheet "Predictive ML Examples for Trading Work" (six
examples: meta-labeling, day-type classifier, range forecast, dealer-positioning
validation, learned component weights, calibration and drift), read against what
this app stores and what it has already measured.

## 1. Where the app stands today

The app already has two fitted models and a dozen measurement tools. What it
lacks is anything shared between them.

| Exists today | What it is | What it built for itself |
|---|---|---|
| Scalp chance of target (`options_svc/scalp_prob_fit.py`) | Logistic model with priors, refit every Saturday | Its own training rows, walk-forward, calibration table, model store (`prob_models`) |
| Swing factor model (`tools/refit_swing_model.sh`) | Factor weights, refit monthly, ships only past an out-of-sample gate | Its own history pull, gate, artifact, report diff |
| Score calibration (`tools/signal_calibration.py`, `shared/calibration.py`) | Entry score against realised R | Its own join and day-clustered statistics |
| Studies (scalp, intraday, gamma regime, HIRO, flow sides, idea review, exit replay) | One-off or daily measurements | Each its own loader and report |

Three facts shape everything below:

1. **No machine learning library is installed.** `requirements.lock` has numpy,
   pandas and scipy only. Both models are hand-written.
2. **Every model rebuilt the same plumbing**: point-in-time features, labels,
   walk-forward splits by day, a baseline to beat, a place to keep the model.
3. **The measurements agree on where the signal is.** Range and volatility are
   predictable here; direction is not.

| Measured result | What it says for machine learning |
|---|---|
| Scalp direction: no usable edge (0 of 108 buckets) | Do not build a direction model on intraday bars |
| Negative net gamma: afternoon range wider (+13 points, in sample) | Range is the target with signal |
| Heavy volume: wider range later, no direction | Same |
| Entry IV rank is the strongest predictor of a credit trade (really IV against realised volatility) | A realised-volatility forecast is the missing half of that number |
| Trade management, not selection, makes the money; 0-DTE loses under every exit | Selection models help most on 0-DTE entries; exits are a modelling target of their own |
| Swing model has no edge once beta is removed | A return label rewards beta; choose labels carefully |
| Daily reports lose to an informed baseline | A language model is not the predictor |

## 2. What the app holds to learn from

Depths are from earlier notes and must be re-counted on prod in Phase 0.

| Store | Holds | Depth | Usable as |
|---|---|---|---|
| `scalp.db` `bars` | 1-minute and 5-minute price bars, kept forever | 5-minute from about March 2026 | Range and volatility labels; price features |
| `signals.db` `signals`, `signal_marks`, `signal_outcomes` | Every captured signal, 5-minute marks, outcome | About 900+ closed, 50 to 90 entry days | Meta-labels, exit modelling |
| `trades.db`, `paper_account.db` | Both paper books, trade events | Since summer 2026 | Outcome labels, management paths |
| `gex_history.db` + `gex_archive.db` | Per-strike gamma, charm, delta, vanna, premium per minute | Archive from 2026-10-20; backups add about 36 sessions | Dealer-positioning features |
| `shared/raw_store` | Raw chains per minute, replayable | Recent | Re-deriving any feature after the fact |
| `hiro_minutes`, `flow_contract_days` | Hedging-flow model, bought/sold tallies | From October 2026 | Flow features, alert labels |
| `iv_history.db` | Implied and realised volatility per symbol per day | Growing daily | Volatility features |
| Sentiment stores (`sentiment_intraday`, `composite_close`, `market_state`, `sector_pcr`) | Composite and components, intraday and at close | Backfilled daily; intraday since summer | Regime features |
| `news.db`, `xfeed.db`, `earnings`, `econ_obs` | Headlines, posts, earnings dates, economic releases | From late September 2026 | Event features |

The binding limit is **days, not rows**. A model here sees 60 to 160 distinct
sessions. That rules out deep networks and favours regularised linear models and
small gradient-boosted trees, judged with each day counted once.

## 3. The roadmap

### Phase 0. Foundation (build once, used by every later phase)

The piece that turns scattered studies into machine learning.

- **`shared/ml/`**, a pure library (numpy, pandas, scikit-learn, lightgbm):
  - `dataset`: builds a training table by joining features to labels **as of the
    decision time**. A feature stamped after the decision is refused, not dropped
    silently.
  - `splits`: walk-forward by session with a gap between train and test.
  - `metrics`: Brier score, log loss, pinball loss, reliability table,
    day-clustered bootstrap intervals, and comparison against a named baseline.
  - `registry`: saves a model with its training window, feature list, settings
    hash and test scores; loads the one marked live.
- **`ml.db`**, a prediction log: every prediction any model makes, with model
  version and inputs, and the outcome filled in later. This is what makes
  calibration and drift measurable without a separate study each time.
- **Training runs outside the services**, on a timer, as the swing refit does.
  A service loads a finished model at start and only predicts.
- **Dependencies**: scikit-learn and lightgbm added to `requirements.lock` by
  hand, proven in a fresh environment first.
- **First task**: move the scalp chance model onto this library with identical
  output. It proves the foundation on a model whose answers are already known.

Done when: the scalp refit runs through `shared/ml` and its weekly numbers match
the current ones.

### Phase 1. Calibration and drift (sheet row 6)

No trading effect; makes every probability in the app honest.

- Reliability for each stated probability: scalp chance of target, the Finder's
  probability of profit, delta-implied win rate on captured signals.
- Isotonic recalibration where a probability is measurably off, shown beside the
  raw figure before it replaces it.
- Input drift (population stability index) per feature per day. A day of
  unusable Greeks or frozen quotes shows up here the same morning.
- A private **Model Health** page: one row per model with calibration, drift and
  whether it still beats its baseline.

Done when: the page shows the scalp model and the Finder probability with 30+
sessions behind each.

### Phase 2. Range and volatility forecast (sheet rows 3 and 2)

The first new model, on the target with the most data and the clearest signal.

- **Targets**: realised range for the rest of the session, and for the next 30
  and 60 minutes, for SPY, QQQ, IWM and `$SPX`.
- **Models**: HAR-RV regression as the baseline to beat, then LightGBM quantile
  regression (10th, 50th, 90th percentile).
- **Features**: realised volatility at several lags, time of day, overnight gap,
  implied volatility and the VIX term structure, net gamma sign, the volume
  read, scheduled economic releases and earnings.
- **What it feeds**, each in shadow first:
  - Expected Move: a forecast range beside the implied one.
  - Daily condor: a shadow rule that skips a day forecast wider than the wings.
  - Volatility gate: forecast realised volatility against implied, per symbol.
  - Day type: "trend day" or "range day" becomes a label on the forecast, not a
    second model.

Done when: it beats HAR-RV and the implied move on out-of-sample pinball loss
over 40+ sessions, each day counted once.

### Phase 3. Meta-labeling and exits (sheet row 1)

- **3a. Entry.** For each captured signal: the chance it reaches its target
  before its stop, and expected R. Labels are rebuilt from `signal_marks` by
  replay under one fixed exit rule, because the recorded outcomes of September
  to November are distorted by the time stop that fired on the first mark.
  Trained per population (0-DTE, Swing, Income), never pooled. Starts as a
  column in shadow; later a rank and a size multiplier in the Paper Account only.
- **3b. Exit.** A model of the open position: given the mark path so far, the
  chance of reaching 50% of the credit before the stop. This is where the
  measured edge is, and the 5-minute marks already hold the paths.

Done when: out-of-sample, the model's top third of signals beats the bottom
third in R with a day-clustered interval clear of zero, for at least one
population. If it does not, it stays a shadow column and that is recorded.

### Phase 4. Dealer positioning and flow (sheet row 4)

Waits on data, not on code. Earliest useful start is when the Gamma Archive
holds about 80 sessions (roughly February 2027).

- Finish the declared gamma regime study first. Its sample is spoken for; a
  model fitted on the same sessions before it reports would spoil the test.
- Then: quantile regression of forward range on net gamma, flip distance, charm
  and vanna, added to the Phase 2 model as features and kept only if they improve it.
- Flow alert quality: for each alert type, the chance of a range expansion in
  the next 30 minutes. Lets HIRO and the big-delta detector earn or lose their
  place in the rankings on a number.

### Phase 5. Sentiment and text (sheet row 5)

Lowest expected payoff; cheap once Phases 0 and 2 exist.

- Ridge regression of the eight sentiment components against forward **range**
  (not return, which rewards beta). The output is evidence for which components
  carry signal, shown on Model Health; weights change only by the operator's decision.
- Headline and post features from `news.db` and `xfeed.db` (event type, count,
  surprise) as inputs to the range model. No model reads text to pick a direction.

## 4. What changes on screen

One new page; everything else is a row, column or chip on a screen that exists.
Nothing is removed and no existing switch or rule is overridden. The figures in
the sketches are illustrative.

### The new page: Model Health (Phase 1)

Private, beside System Status. One row per model.

```
Model Health
Model                  Status   Says vs happened         Beats baseline     Inputs
Scalp chance of target Live     "60%" came true 57%      Yes (usual rate)   Normal
Finder probability     Live     "70%" came true 81%      n/a                Normal
Range forecast         Shadow   12 of 40 sessions        Not yet judged     Net gamma drifted
```

Each model also gets one switch under Settings → Configuration.

### Existing screens

| Screen | Phase | Change |
|---|---|---|
| Expected Move (`/options/expected-move`) | 2 | A forecast row beside the implied move, with its likely range and how much more (or less) the options are pricing |
| Desk (`/desk`) | 2 | A day-type chip per index row after the first 30 to 60 minutes ("Range day likely" / "Trend day likely"); kept off the public Desk |
| Paper Ledger (`/options/paper`) | 2 | The 08:45 condor row tagged "Model would have skipped today" / "Model agreed"; the condor still books every day |
| Market Scanner, Captured Signals | 3a | A "Chance of target" column with the usual rate beside it, and one line in the detail panel's Go / No-Go checklist |
| Paper Account and Ledger, open positions | 3b | An advice chip: chance of reaching 50% of the credit before the stop; the exit rules keep running unchanged |
| Opening Scalp (`/options/scalp`) | 0, 2 | Phase 0: no visible change. Later: the target distance can come from the forecast range |
| Flow Alerts (`/options/flow`) | 4 | Each alert type's record ("followed by a wider range 61% of the time, usual 44%"), which decides whether it joins the rankings |
| Sentiment (`/sentiment/bullbear`) | 5 | An evidence panel: each of the eight components marked "carries signal" or "no measurable signal"; weights change only by the operator's decision |
| End of day (`/eod`) | 1 on | A scorecard: what each model predicted today and what happened |

Example, the Scanner column (Phase 3a):

```
Symbol  Structure   Credit  Score  Chance of target
AMD     Put spread  $0.62   64     71%  (usual 58%)   Shadow
NVDA    Condor      $1.10   61     49%  (usual 58%)   Shadow
```

How every model figure looks:

- **Marked "Shadow"** until it passes its test, with hover text saying it is not
  used in any decision.
- **The usual rate beside it**, so 71% is read against 58%.
- **Private first.** Nothing reaches `live.neuralstrike.co` unless the operator
  publishes it, model by model.

## 5. Rules every phase follows

1. **Shadow, then paper, never automatic live.** A model records predictions
   first. It may later rank or size in the Paper Account. No model places,
   changes or cancels a live order.
2. **A named baseline, stated before the fit.** Usual rate, HAR-RV, implied
   move, or the rule it would replace.
3. **One day counts once.** Walk-forward by session; intervals by day-clustered
   bootstrap.
4. **Point in time.** Features are taken as of the decision; a carried or
   backfilled row is marked and can be excluded.
5. **One switch per model** in a `config/ml.toml`, with the thresholds, windows
   and model names in it and in Settings.
6. **Tiers hold.** Training in a timer-run tool, prediction in the owning
   service, the page reads a view. A prediction written to the cache is public
   unless its view is kept off the public origin.
7. **A failed gate is a result.** It is written to the CHANGELOG and the model
   stays in shadow.

## 6. What this roadmap leaves out, and why

- **Direction models on intraday bars**, deep networks, reinforcement learning:
  the direction studies found nothing, and 60 to 160 sessions cannot train them.
- **A language model as a predictor**: the daily reports lost to an informed baseline.
- **The NinjaTrader side** (TrendPullbackScalp meta-labeling, the F1 to F25
  prediction servers): outside this repo. `shared/ml` is plain Python and could
  be reused there.

## 7. Order and rough size

| Phase | Depends on | Size | Earliest value |
|---|---|---|---|
| 0 Foundation | nothing | 2 to 3 weeks | Scalp refit on shared code |
| 1 Calibration and drift | 0 | 1 to 2 weeks | Model Health page |
| 2 Range forecast | 0 | 3 to 4 weeks, then 40 sessions in shadow | Expected Move, condor shadow rule |
| 3 Meta-labeling and exits | 0, replay labels | 3 to 4 weeks, then shadow | Scanner column, exit advice |
| 4 Dealer positioning and flow | 2, about 80 archived sessions | from about February 2027 | Alert rankings on evidence |
| 5 Sentiment and text | 0, 2 | 1 to 2 weeks | Component evidence |

## 8. Decisions for the operator

1. **Where training runs**: on the server after the close (data is there; a
   LightGBM fit on this much data takes seconds), or on the PC against the E:
   backups. Recommended: the server, on a timer.
2. **Whether Phase 0 is worth doing before any new model.** Recommended: yes.
   The alternative is a third and fourth model each with private plumbing.
3. **Whether a model may ever size a Paper Account trade**, or stays advisory.
