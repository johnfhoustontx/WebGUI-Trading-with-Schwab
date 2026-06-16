# Paper trade — live Analyze on select (2026-06-16)

Populate the Trade-detail panel with **live** Greeks/IV/breakeven/PoP when a paper
trade is selected, layered on top of the instant stored-data view.

## Why
`synth_from_trade` now fills the panel from data stored at entry, but IV +
composite were never captured and Greeks are blank for trades whose signal lacked
them. The live `trade_analyzer.analyze_trade` already computes current Greeks, ATM
IV, breakeven, underlying, PnL — but `analyze_paper` discarded all but the verdict
`action`. This surfaces that live data in the panel.

## Changes

1. **Engine (additive)** — `options-scanner/trade_analyzer.py` `analyze_trade`:
   expose the already-computed `atm_iv` by adding `"atm_iv": atm_iv` to the
   returned `market` block. Purely additive.

2. **Service** — `services/options_svc/compute.py` `analyze_paper(trade_id)`:
   return `{trade_id, symbol, action, detail}`. `detail` maps the live analysis to
   the detail-panel field names:
   - `short_delta`/`net_theta`/`net_vega` ← `greeks.current.{delta,theta,vega}`
   - `short_iv`/`current_iv` ← `market.atm_iv`; `iv_rank` ← `market.iv_rank_now`
   - `breakeven` ← `profit_target.breakeven`
   - `underlying_price` ← `position.underlying_now`; `dte` ← `position.dte_remaining`
   - `unrealized_pnl` ← `position.unrealized_pnl`
   - `pop_pct` ← delta-approx `(1 − |delta|)·100` from the live delta
   Fully guarded: `analyze_trade` raises a RuntimeError when live data can't be
   fetched (after-hours/no chain) → return `detail=None`, `action="—"`. (The
   handler `paper_analyze` already caches `cache:options:paper_analyze` + publishes
   — unchanged.)

3. **Page** — `webgui/pages/options/paper.py`:
   - On row select: render `synth_from_trade` instantly (stored data), set
     `state["sel_id"]`, enqueue `paper_analyze{trade_id}`, show a subtle
     "analyzing live…" hint.
   - The existing `options:paper_analyze` version-watch: when a result arrives with
     `trade_id == state["sel_id"]` and `detail` is present, MERGE `detail` over the
     synth dict (drop None values) and re-render — live values override stored.
     `detail is None` → keep the stored view + a small "live data unavailable" note.
   - Keep the "Analyze selected" button as a manual re-trigger.
   - Pure merge helper `merge_detail(base, detail)` (unit-tested): overlays only
     non-None detail fields onto the base synth dict.

## Testing
- `services/options_svc/tests/test_compute.py`: `analyze_paper` maps the detail
  fields from a faked `analyze_trade`; RuntimeError → `detail=None`, `action="—"`.
- `webgui/tests/test_options_paper.py`: `merge_detail` overlays non-None fields,
  preserves base on None/empty.
- Engine: assert `analyze_trade` return includes `atm_iv` (light test or rely on
  the compute test's fake). Visual verify: click a trade → live Greeks/IV/breakeven.
