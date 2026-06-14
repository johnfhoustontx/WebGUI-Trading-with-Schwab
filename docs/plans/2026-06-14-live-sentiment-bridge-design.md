# Live intraday sentiment + bridge from the GEX collector — design

**Date:** 2026-06-14
**Status:** approved

## Goal

Make `shared/sentiment_bridge.json` update **live, intraday** (not the stale
47h-old file, and not a once-a-day last-session value):

1. A headless **live intraday composite** computed from current Schwab quotes,
   reusing the copied pure scoring modules.
2. The **GEX collector** computes it + writes the bridge **every 5-min cycle**
   while it runs (08:30–15:20 CT), independent of the webgui.
3. The webgui **Sentiment page** also shows the live composite during market
   hours, falling back to last-completed-session (backfill) off-hours.

## Why this shape

- The source tk dashboard computed a **live** composite (current quotes →
  `calculate_all_scores` every ~15 min); this webgui shortcut used
  `history_backfill` (drops today → last completed session), which is why the
  page/bridge looked "once a day". The live path uses the **same pure scoring
  modules** with current inputs.
- This repo's precedent for UI-independent background data is a **standalone
  collector process** (`options-scanner/gex_collector.py`, 5-min loop). The user
  wants the bridge refreshed there. The GEX collector already polls Schwab every
  5 min with per-cycle failure isolation — the natural host.

## New shared engine: `sentiment-dashboard/live_composite.py`

Neutral module imported by BOTH the collector and the webgui page (neither
imports the other). Reuses `scoring/` + `sectors_ref` + `bridge.py`.

- `compute_live(schwab, sector_data, prior_vix1d=None, prior_sector_trends=None)
  -> dict` — the live scoring path. Fetches:
  - quotes: `$VIX`, `$VIX1D`, `$VIX9D`, breadth (`$ADVN/$DECN/$NYHGH/$NYLOW/$SPXA50R`
    w/ variant fallbacks), 11 sector ETFs (`change_pct`), `$IRX`.
  - history: `$VIX` 1mo (10d MA), sector ETFs 12mo (63d dual-momentum + RRG).
  - `/chains` per sector ETF → cap-weighted sector P/C.
  Then calls each scorer exactly as source `calculate_all_scores`:
  `vix.score_term/score_vix1d/score_term_slope→score_complex`,
  `put_call.score_sector_weighted(sector_pcr, sp_weights)` (fallback `score(cpce…)`),
  `breadth.score(...)`, `rotation.compute_dual_momentum(closes, sp_weights, irx/10,
  63)`, `sector_perf.sectors_score(...)`; `composite.blend(scores, confs, WEIGHTS)`.
  Returns a dict shaped like a backfill snapshot:
  `{date, composite:{total_score,bias,size_modifier,aggregate_confidence},
  component_scores{...}, component_confidence{...}, volatility/options/breadth/
  rotation interp, source:"live"}` so page + bridge consume it uniformly.
  Component confidences mirror source (sector_perf `(n/11)**0.5`, rotation from
  dual-momentum, etc.). Missing data → score 0 / conf 0 (component drops out of
  the confidence-weighted blend). No min-component quality gate (headless).
- `signal_band(total) -> (modifier, bias, signal)` — the score bands
  (≥9 1.25x/Long/Strong Bull … <3 0.70x/Short/Strong Bear). Moved here; the page
  imports it (was a local `_signal_band`).
- `build_bridge_payload(snapshot, history_scores, spy_closes, generated_at,
  sector=None) -> dict` — faithful bridge dict (mirrors source
  `_build_bridge_payload`): `composite_score`, `regime` band (≥8 strong_bullish…
  <3.5 strong_bearish), `bias`/`position_size_modifier`/`contrarian_signal` from
  `signal_band` (bias lowercased → long/neutral/cautious/short, the vocab
  `regime_filter` checks), `momentum`, `rolling_averages{5d,20d}`,
  `component_scores` (+ back-compat aliases), `component_confidence`,
  `aggregate_confidence`, `weights`, `velocity`, `divergence_flag`,
  `trend_regime{state,confidence,...}`, additive `sector_breakdown`/
  `rotation_detail`, `source="WebGUI-Sentiment"`, `date`, `generated_at`.
- `publish_bridge(schwab=None) -> dict|None` — convenience for the collector:
  resolve a proxy client if none, load sector_data, `compute_live`, fetch SPY 12mo
  for trend_regime, build payload (`generated_at=now UTC`), `bridge.write_bridge`.
  Fully defensive (returns None on failure, logs).

## GEX collector hook

In `options-scanner/gex_collector.py` `run_collector_loop`, after the existing
GEX `poll(...)` try/except, add a SECOND independently-guarded block:
```python
try:
    _publish_sentiment()   # defensively-imported hook; resolves its own proxy client
except Exception:
    log.exception("sentiment publish failed; continuing")
```
`_publish_sentiment` is a tiny module-level function that imports
`live_composite.publish_bridge` lazily (so the collector has no hard dependency on
sentiment, and a missing/broken sentiment import never affects GEX collection).
It self-resolves a proxy client (avoids coupling to the collector's client type).
Net collector change: ~10 lines + the helper. GEX and sentiment are independently
failure-isolated both ways.

## Webgui page → live intraday

In `webgui/pages/sentiment.py`:
- `_load_snapshots` (or a new `_load_composite`) chooses live vs backfill: during
  market hours (and if `compute_live` succeeds) use the live snapshot for the
  gauge/component table/tiles/velocity/divergence; else fall back to the backfill
  last-session snapshot. The 30-day history (backfill series) + sector table stay
  as-is. Live runs off-thread via `io_bound`.
- `_publish_bridge()` (page-open publish) builds the payload via the shared
  `build_bridge_payload` and writes it on each load — try/except, never breaks UI.
- The page's `_signal_band`/`tiles` use the shared `signal_band`.

## Testing

- TDD pure: `signal_band` (bands), `build_bridge_payload` (regime bands, bias
  vocab, trend block, sector enrichment; round-trip through `bridge.write_bridge`
  to a tmp path → re-read).
- `compute_live` verified against the live proxy via a temp script (real
  component scores + composite), and `regime_filter.evaluate_regime()` reads the
  written bridge without error.
- Collector hook: unit test that a raising `_publish_sentiment` does not break the
  GEX `poll` path in `run_collector_loop` (inject a poll + a failing publish).

## Caveats

- Live compute ≈ 15–18 proxy calls/cycle (collector every 5 min — matches GEX's
  own budget). Off-hours the collector is idle → bridge not rewritten; the page
  covers freshness while open.
- The collector's `client` may not be the proxy client; the hook self-resolves a
  `SchwabProxyClient` rather than reuse it.
- `regime_filter` only needs `generated_at`, `composite_score`, `bias`,
  `trend_regime.{state,confidence}`, `aggregate_confidence`, `divergence_flag` —
  all produced.

## Out of scope

- Replacing the backfill 30-day history with live points (history stays backfill).
- Any change to GEX collection logic itself.
