# Gamma forward projection on the GEX heatmap — design (2026-07-11)

## Problem

The Gamma page's intraday heatmap shows only *collected* history — the GEX/strike
landscape up to "now." Traders want a **forward look**: where the gamma landscape
(flip, call/put walls, ATM concentration) is heading through the rest of the
session as time decays, plus a sense of where price might actually travel.

## Key insight — intraday gamma is a re-pricing problem, not a forecast

Dealer gamma exposure per strike is
`Σ (openInterest × BS_gamma(S, K, T, σ) × multiplier × sign)`. **Open interest is
fixed for the whole session** (Schwab republishes OI only overnight), so within
today the entire GEX/flip/wall landscape moves for exactly three reasons: spot (S)
moves, time (T) passes, and IV (σ) shifts. That means the future landscape is not
statistically *forecast* — it is **re-computed** by re-pricing today's standing OI
at future `(S, T, σ)`. With spot held flat, the time dimension is the deterministic
**charm morph** (walls sharpen, gamma concentrates ATM into the close). The unknown
is the future spot path, which we show honestly as an **expected-move cone** rather
than baking a guessed path into the colored grid.

## Decisions (locked in brainstorming)

1. **Flat-spot time-decay grid + expected-move cone overlay.** Future heatmap columns
   hold spot at its current level (pure charm morph); the spot uncertainty is drawn
   as an up/mid/down EM cone on top of the grid. The colored grid stays deterministic;
   the path uncertainty is shown, not hidden.
2. **Project to the 4pm ET close, in 15-min steps.** The collected (past) side keeps
   its true 1-min resolution; the future side is coarser (15 min) because the decay
   morph is smooth and hundreds of near-identical future minutes would swamp the real
   data and cost more to serialize each tick.
3. **GEX only.** The flip and call/put walls — the levels that drive intraday behavior
   — live on GEX. Charm/DEX/Vanna keep showing collected history only. The UI is
   *not* materially different from an all-views projection (the future band would just
   also appear on three views of marginal trading value), so GEX-only ships the
   valuable 90% at ~¼ the compute/test surface.
4. **Sticky-strike IV.** Each strike holds its current implied vol as time decays. A
   genuine vol shock is not modeled (noted in the UI). Standard honest default.
5. **Hide the future band off-hours.** Once past the close (or before the open) there
   is no "rest of session" to project to, so only the collected history shows. Never
   project across the overnight OI reset — that's exactly where the math is least valid.
6. **Split strike/heatmap fixed at 40/60**, single constant, trivially flippable to
   70/30 (`_STRIKE_HEAT_SPLIT`). The full-day display makes the heatmap the star.
7. **Collection cadence 2 min → 1 min.**

## Architecture

Additive across the existing three tiers. No new cache key, no new service, no new
route — the projection rides the existing `cache:options:gamma` view and the existing
Analyze/Explain flows.

### 1. Projection math — Tier 2 (`services/options_svc/compute.py`), pure

`project_gex_grid(engine, chain, spot, now)`:
- Build the future timeline: 15-min marks from the next quarter-hour to 4pm ET.
  Empty list once the session is over → off-hours hides the band.
- For each future mark, recompute **net GEX per strike** from the chain's standing OI
  at `(S = current spot, T = time-to-expiry at that future mark, σ = each strike's
  current IV)`, **reusing the engine's exact per-strike GEX formula** (`gamma_tool` /
  `options_calculator.bs_gamma`) — NOT a parallel reimplementation — so projected
  columns sit on the same scale as collected ones.
- Return `{"times":[...15-min labels...], "grid":{strike:[net_t0, net_t1, ...]}, "spot":spot}`.

`project_em_cone(spot, atm_iv, times, now)`:
- Up/mid/down fan: `width(τ) = spot · atm_iv · √(τ/365)` anchored at current spot,
  `τ` = minutes-from-now for each future mark. Reuses `_session_expected_move`'s
  ATM-IV derivation.

Both pure and defensive (missing chain/IV/spot → empty → collected-only display).

### 2. Payload — embed in the existing `gamma` view

`gamma_snapshot(...)` adds a **`projection`** block to the **GEX view only**, computed
off the live `chain` it already fetches (no extra fetch, no extra DB read):

```
views["GEX"]["projection"] = {
    "times": ["13:15","13:30", … "15:00"],
    "grid":  { "<strike>": [net_t0, net_t1, …] },   # cropped to the display window
    "cone":  { "mid":[…], "up":[…], "down":[…] },   # spot levels per future time
    "spot":  <flat spot used>,
}
```

Cropped to the same ±display-window strikes as the collected grid (rides
`_crop_gamma_views`, so the payload stays well under 1 MB). Absent past-close or when
chain/IV is unavailable. Additive and defensive — every other view and the existing
`history`/`flow` blocks are untouched.

### 3. UI — Tier 1 (`webgui/pages/options/gamma.py`), pure builders

- `heatmap_figure` gains an optional `projection` arg. When present (GEX view, RTH):
  append the future columns to the right of the collected ones on the same time axis,
  add a dashed **"now" divider** plotLine at the seam, continue the spot line along
  `cone.mid`, and overlay the up/down **cone** as two faint dashed lines on the strike
  axis. Same interpolated-image look; the future band is visually continuous but marked.
- **Split:** replace the dynamic `panel_flex(...)` intraday call with a single module
  constant `_STRIKE_HEAT_SPLIT = (0.40, 0.60)` (strike, heat) fed to `flex_class`.
  One-line flip to `(0.70, 0.30)`. `panel_flex` stays for the Term full-width case but
  is no longer the intraday driver.
- The shared crosshair + press-and-hold tooltip keep working (they key off plot
  geometry / the mounted series, both unchanged).

### 4. Explain / Analyze / scheduled briefings — reflect the projection

Framed reader-first (consistent with the Explain/Analyze language reframe just shipped):

- **Analyze + scheduled auto-briefings** (shared `gamma_analyze` → `_gamma_blocks_for`):
  for each index compute the projection and fold a compact **"into the close"** summary
  into the prompt bundle — projected flip / call wall / put wall at the close, the charm
  morph, and the EM range to the close. Add an optional `close_outlook` string to the
  `submit_analysis` schema so the infographic shows a one-line forward read per index.
  The scheduled calls inherit this automatically (same function).
- **Explain** (rule-based, GEX): add a forward **"Into the close"** block derived from
  the same `project_gex_grid` output — no new LLM cost, just projected levels + a plain
  "what to do as the day decays" line. Charm/DEX/Vanna Explain unchanged.

### 5. Condense the header — reclaim vertical real estate

Collapse four stacked rows into **two tight rows**:
- **Row 1 (controls):** Symbol · view toggle · Refresh · Explain · Analyze · a single
  **"Briefings ▾"** menu (the four slot buttons move into a dropdown; highlight/dim
  state carries onto the menu items).
- **Row 2 (one compact status strip, small `·`-separated text):** collector status ·
  last/next scan · next-refresh countdown · summary (spot / strikes / net / flip). The
  0-DTE hedge-pressure panel folds inline on the DEX view.

Net 4 rows → 2, giving the now-taller full-day heatmap more height. History picker
stays below the charts.

### 6. Collection cadence 2 → 1 min

`POLL_INTERVAL_MIN 2→1` (gex_collector) · `scheduler._GEX_INTERVAL_MIN 2→1` ·
`gex_status.STALE_AFTER_SEC 240→120`, kept in lockstep by the existing
`test_scheduler.py` drift-guard. Load: ~2× chain fetches (still inside a 60 s cycle for
~24 symbols) and ~2× DB growth (the keep-5-sessions purge already bounds it).

## Testing

Pure unit tests, TDD per layer:
- `project_gex_grid` — flat-spot decay sharpens walls / concentrates ATM into the close;
  empty list past-close; defensive on missing chain/IV.
- `project_em_cone` — √-time fan, anchored at spot, one point per future mark.
- `heatmap_figure` — future columns appended, "now" divider present, cone series present,
  absent when `projection=None`.
- `_STRIKE_HEAT_SPLIT` wiring (40/60).
- cadence drift-guard (1-min lockstep).
- `close_outlook` schema present + parse-defensive.

## Non-goals

No IV-shock modeling; no intraday-OI / order-flow re-estimation (Schwab has no tape —
volume/premium carry no opening-vs-closing or buy/sell sign); no projection on
Charm/DEX/Vanna; no off-hours forward band.
