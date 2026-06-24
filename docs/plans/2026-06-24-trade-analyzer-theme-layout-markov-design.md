# Trade Analyzer — theme + layout + Markov-chart fix + input behavior

**Date:** 2026-06-24
**Branch:** `Using_Highcharts`
**Page:** `/trade` (`webgui/pages/trade.py`), service `services/trade_svc`

## Problem

Four asks for the Trade Analyzer screen:

1. Apply the shared dark-navy **"dashboard" theme** (currently the page uses the
   default NiceGUI dark cards, not the navy theme the Calculator/Simulator share).
2. Optimize the **dead space** — the Position/Investor cards stretch to the tall
   Markov card, leaving large empty bottoms.
3. Make the **Markov forecast chart** theme-compatible **and** fix that "the graph
   looks the same for every stock regardless of composite score."
4. **Tabbing out** of the Symbol field should behave like clicking **Analyze**.
5. Returning to the page should **hold the last analyzed symbol** in the field.

## Diagnosis — why the Markov chart looks the same for every symbol

Confirmed against live Redis data (AAPL) + the live 17-symbol pooled prior, and by
tracing the chain math. The chart plots band-probabilities at **now / 5d / 10d /
20d**. The forecast **converges**:

| Start band   |  1d  |  3d  |  5d  | 10d  | 20d  |
|--------------|------|------|------|------|------|
| Strong-Bear  | 0.02 | 0.12 | 0.22 | 0.41 | **0.54** |
| Neutral      | 0.29 | 0.47 | 0.52 | 0.55 | **0.58** |
| Strong-Bull  | 0.97 | 0.88 | 0.80 | 0.68 | **0.60** |

(*values = P(Weak-Bull)+P(Strong-Bull), the "green" portion of the stacked area.*)

By **10–20 days every symbol converges to the same bull-leaning distribution**
(~0.54–0.60 green). The pooled prior is strongly bull-leaning (stationary ≈ 73%
green) and Dirichlet shrinkage (α=30) pulls thin-data symbols toward it. So the
chart's **right two-thirds (10d/20d) are identical for every stock** — only the
near term (now→5d) carries score-specific signal, and it's squeezed into one or two
columns. This is a model/horizon-choice issue, **not** a rendering glitch. The
near-term trajectory, by contrast, differs dramatically by score and is the right
thing to surface.

## Decisions (user-approved)

- **Markov fix → "Add near-term horizons."** Surface the divergent near-term
  trajectory. **Keep the model and tilt/adjusted-score math unchanged.**
- **Layout → "Compact in place."** Tighten the existing structure; do not repack
  into a grid.

## Design

### 1. Theme (the documented shared pattern)
- `ui.add_css(DASHBOARD_CSS)` (from `pages/options/theme.py`); wrap the page body in
  a `.calc-v2` column. Title → `color:#eaf0fb`.
- Header card + all verdict/secondary cards → `.calc-card`.
- Analyze button → `color=None` + `.cv2-btn-primary`.
- Semantic colors (BUY-green/HOLD-amber/SELL-red, bias, sector strength, Markov band
  chip) stay — they read on navy.

### 2. Markov chart — near-term horizons + chart theming
- **Service** (`trade_svc/compute.py`): add `_MK_TRAJECTORY_HORIZONS = [1,2,3,5,10,20]`.
  In `build_markov_block`, attach a new **additive** `trajectory` field — a list of
  `{n, dist}` — computed by reusing `_markov.forecast(P, current,
  _MK_TRAJECTORY_HORIZONS)["horizons"]` (no new engine math, no model change). The
  existing `horizons` (5/10/20), `drift`, `tilt`, `confidence`,
  `markov_adjusted_score`, `transition_row`, `persistence`, `stationary` stay
  byte-for-byte unchanged. **No contract change** — `TradeAnalysis.markov` is a loose
  `dict | None`.
- **Page** (`markov_forecast_figure`): build series/categories from
  `mk.get("trajectory") or mk.get("horizons")` (back-compat fallback). Categories
  become `now → 1d → 2d → 3d → 5d → 10d → 20d`. The "now" one-hot at `current_band`
  is unchanged. Metric cards + drift line still read `horizons` (5/10/20d) — unchanged.
- **Chart theming**: `chart.backgroundColor:"transparent"`, light axis/label/legend
  colors (match the Simulator's dark axis idiom), subtle gridlines, and fix the
  broken `{value:.0%}` y-axis label (renders ".0%" today) → `{value}%`. Height
  `260 → ~200`. Keep `accessibility.enabled:False` and the reflow-on-show timer.

### 3. Compact-in-place layout
- Verdict row `items-stretch → items-start` (short cards stop stretching to the tall
  Markov card → removes the empty bottoms).
- Markov chart height `260 → ~200`; tighten row/card gaps + padding.
- Keep the MTF / Momentum / Sector / Fundamentals row directly beneath the verdict
  row; structure stays familiar (3-up verdicts + secondary row).

### 4. Tab-out = Analyze
- Add `symbol_in.on("blur", …)` → `_request_analyze()`, **deduped** against
  `state["last_requested"]` so (a) the blur-then-click sequence when clicking Analyze
  (which blurs the field first) doesn't double-fire, and (b) tabbing out without
  changing the symbol is a no-op. Enter keeps working.

### 5. Persist last analyzed symbol
- The *result* already persists via the `trade:analysis` cache. On page build, seed
  the symbol input's initial value from the cached result's `symbol` (fallback
  `"AAPL"`), and initialize `last_requested` to it — so the field matches the
  displayed analysis on return.

## Data flow

`trade_svc.analyze` → `build_markov_block` (now also emits `trajectory`) →
`TradeAnalysis(markov=…)` → `cache:trade:analysis` → page version-poll →
`markov_forecast_figure(trajectory)` → denser, score-specific chart.

## Testing

- **Pure (TDD, `webgui/tests/test_trade.py`)**: figure builder uses trajectory
  categories when present; back-compat (only `horizons` → old categories); a
  **regression test** that two different current-band/trajectory inputs yield
  visibly different near-term series (guards "looks the same"). Small pure helpers
  for seed-symbol + dedupe predicate, unit-tested.
- **Service (`services/trade_svc/tests`)**: the block carries `trajectory` (dense
  horizons) while `horizons` stays `[5,10,20]`; tilt/adjusted-score unchanged.
- **Live verify (Redis-driven)**: enqueue `analyze` for a bullish and a weak symbol;
  confirm the trajectories differ. Restart `trade_svc` to pick up the change.
  Screenshot the themed page (`:8500/trade`).

## Risk

Low. Model/tilt untouched; contract additive; theme is the documented page pattern;
all pure transforms unit-tested. Confined to `webgui/pages/trade.py` +
`services/trade_svc/compute.py` (+ tests).
