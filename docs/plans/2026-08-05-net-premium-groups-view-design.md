# Net Premium view — Dealer Positioning subtab (design)

**Date:** 2026-08-05
**Status:** approved, ready for implementation planning

## Problem

The Dealer Positioning page (`/options/gamma`) can show intraday options-flow
premium for exactly one symbol at a time (the **Flow** view: price + cumulative
call/put premium + a net-premium panel). There is no way to compare net premium
*across* symbols — to see that $SPX is put-led while NVDA is call-led, or which
sector is absorbing the day's call money.

## What we are building

A seventh subtab on Dealer Positioning — **Net Prem** — plotting intraday net
premium (call$ − put$) as one colored line per symbol, for any combination of
symbols drawn from three groups:

1. **Indices & Broad** — `$SPX`, `$NDX`, `BIG10`, `SPY`, `QQQ`, `IWM`, `DIA`
2. **SPDR Sectors** — `XLB`, `XLC`, `XLE`, `XLF`, `XLI`, `XLK`, `XLP`, `XLRE`,
   `XLU`, `XLV`, `XLY`
3. **Mega-caps** — `NVDA`, `AVGO`, `AAPL`, `META`, `MSFT`, `TSLA`, `PLTR`,
   `AMZN`, `GOOGL`, `AMD`

Each symbol has a fixed color that does not change with the selection.

## Findings that shaped the design

Queried the live `gex_history.db` (80 symbols collected on 2026-08-05):

- Groups 1 and 3 are **fully covered**. `BIG10` is not a real symbol but all ten
  of its members are collected, so it can be summed.
- **No SPDR sector is collected.** They are absent from both the index base
  (`gex_collector.SYMBOLS`) and `Top 20.xlsx`, so there is zero premium history
  for group 2.
- Magnitudes span four orders: `$SPX` net −$902M, `SPY` −$609M, `NVDA` +$382M,
  but `IWM` −$15.5M, `AVGO` +$10.8M, `DIA` −$0.2M. On one linear dollar axis the
  small names are a flat line on zero — and sector ETFs will land in that band.
- `handlers.collect_gex_history` **already** calls `load_flow_series` for every
  collected symbol each minute to build the Opportunity Board
  (`compute.build_matrix`). The data this feature needs is already being read.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Sector data | Add the 11 SPDR sectors to GEX collection | Gives them the same intraday series as every other name. Costs ~+4,300 Schwab calls/day; history starts the day it ships. |
| Chart form | Intraday lines, one per symbol | Matches the neighbouring Flow view; shows *when* a symbol flipped from put-led to call-led, not just where it ended. |
| Y scale | `Dollars ($M)` / `Skew %` toggle | Dollars is honest about size (right for SPX vs SPY vs QQQ); Skew % = net ÷ (call+put) normalises to ±100% (right for 11 sectors or 10 mega-caps). Neither alone is sufficient. |
| Selection UI | Group tab + per-symbol checkboxes, **selection persists across tabs** | The tab is a filter, not a mode: check `$SPX` on Indices, switch to Sectors, check `XLK`, both plot. Keeps the picker compact at 28 symbols while still allowing any cross-group combination. |
| Data path | Publish all 28 on the existing 1-min GEX branch; page filters client-side | Reuses the flow series `build_matrix` already loads, so marginal server cost is ~zero. Checkbox toggles are instant (no command round-trip). ~500 KB Redis key. |

### Rejected alternatives

- **On-demand `net_premium` command per selection.** Smallest footprint, but a
  visible delay on every checkbox click and another command + cache view to
  maintain. The chosen path costs almost nothing because the read already happens.
- **Extend `gamma_snapshot`.** That cache is keyed to the one *viewed* symbol and
  republishes on a different cadence; per-user selection state would fight it.
- **Dollars-only or Skew-%-only.** Dollars-only makes DIA and every sector
  unreadable beside SPX; Skew-%-only discards how much money is behind the move.
- **Group tabs that reset the selection.** Simpler, but makes comparing `$SPX`
  against `XLK` impossible — which contradicts the requirement.

## Architecture

### Tier 2 — collection

`options-scanner/gex_collector.py`: add the 11 SPDR sectors to `SYMBOLS`. This is
the surgical spot — `collection_symbols()` is `SYMBOLS ∪ Top 20.xlsx`, while the
*scanner's* universe (`watchlist.get_scan_symbols`) is separate and untouched.

Known consequences, all additive:

- +11 chain fetches per 1-min poll ≈ **+4,300 Schwab calls/day** (~+14% on GEX
  collection, the stack's #1 caller).
- Sectors also enter the **Opportunity Board** (`build_matrix` iterates the same
  universe) and the **Dealer Positioning symbol dropdown**
  (`cache:options:gamma_symbols`).
- Premium history for sectors starts the day this ships; earlier sessions stay
  empty for them.

### Tier 2 — pure module

New `services/options_svc/net_premium.py` (no I/O, unit-tested):

- `GROUPS` — the three groups as ordered data (label + members). The single
  source of truth for membership, ordering, and the page's tabs.
- `BASKETS = {"BIG10": (NVDA, MSFT, GOOGL, AMZN, META, AAPL, TSLA, AVGO, PLTR,
  AMD)}` — identical to `market_svc/symbols.py`, so BIG10 means the same thing on
  both pages.
- `build_series(flow_by_symbol)` → `{symbol: [[ts, call_prem, put_prem], …]}`.
  `BIG10` is summed **server-side** from its members aligned on `ts`. Dollar sum
  is the only sensible aggregation for money; the basket's skew is therefore
  `Σnet ÷ Σ(call+put)` — the same dollar-weighted convention
  `market_svc.symbol_premium_skew` already uses.

### Tier 2 — orchestration

`compute.build_net_premium(...)` — DB-only. Reuses the same read-only connection
and the `_rth_only` crop already used by `build_matrix` and the Flow view, so the
RTH window matches the rest of the page. Session-gated via
`_display_session_date`, so off-hours shows the last session (consistent with
Flow).

Published from `handlers.collect_gex_history` on the existing 1-min GEX branch,
alongside `build_matrix`, in its **own guarded `try/except`** — a net-premium
failure can never break GEX collection (house rule). Key
`cache:options:net_premium`, `skip_unchanged=True`, additive
`NetPremiumSnapshot` contract.

### Tier 1 — the view

`webgui/pages/options/gamma.py`:

- **Tab** `Net Prem` inserted after `Flow`: GAMMA · Charm · DELTA · Vanna · Flow
  · **Net Prem** · Term.
- **Rendered before the `if not snap:` early-return** in `_render_view` — this
  view is symbol-independent and must paint even with no gamma snapshot. Otherwise
  it copies the Flow branch: full-width single chart via `_set_chart` +
  `_apply_flex(0, term=True)`, heatmap hidden.
- Data arrives by adding `options:net_premium` to the **existing** coalesced 2 s
  `read_versions` poll — no extra round-trip.
- **Controls** (shown only on this view, hidden on the others — the same pattern
  as the Bar-size picker being hidden for the line spot style): group tab;
  per-symbol checkboxes with labels tinted in each symbol's line color;
  `Selected: N` + `Clear all`; `Dollars ($M)` / `Skew %` mode picker.
- **Colors**: a fixed `NET_PREM_COLORS = {symbol: hex}` map — a symbol keeps its
  color regardless of what else is selected. Curated for the dark navy
  background, hues spread within each group so an all-sectors or all-mega-caps
  selection stays distinguishable. A fixed 28-entry map satisfies the
  Tailwind-first "map dynamic values to a finite palette" rule.
- **Persistence**: `gamma_netprem_symbols` / `gamma_netprem_group` /
  `gamma_netprem_mode` in `app_settings`, following `gamma_spot_style`. Default
  selection `$SPX, SPY, QQQ` — the three whose magnitudes are comparable in
  Dollars mode.
- **Chart**: Highcharts line, one series per symbol, synthetic category x-axis of
  RTH times (the same gap-packing trick `flow_figure` uses), a zero plotLine,
  legend on with click-to-hide, and a **non-shared tooltip** (a shared tooltip
  with 20+ series is an unreadable wall).

## Error handling and degradation

- Nothing selected → "Select one or more symbols."
- A selected symbol with no rows (every sector until collection starts) → omitted
  from the chart with a note naming it: *"XLK, XLU — no data yet (collected from
  today)."* Never a blank chart with no explanation.
- Missing `call_prem`/`put_prem` on legacy rows → that point is skipped, as the
  Flow view already does.
- Cache key absent (service down / not yet published) → the existing
  waiting-for-service placeholder.

## Testing

TDD per layer:

- `services/options_svc` — the pure `net_premium.py` (group data, basket summing,
  ts alignment, missing-member tolerance) and `build_net_premium` orchestration.
- `shared/contracts` — `NetPremiumSnapshot` validation.
- `webgui/tests` — pure page builders: figure structure, **color stability across
  different selections**, skew math, empty/partial-data states, and the
  `test_no_inline_style.py` guard.
- Live end-to-end against the real proxy + Redis before calling it done.

## Follow-ups

- `webgui/page_help.py` entry for the new view.
- CLAUDE.md update (routes table + a "Last updated" entry).
- Restart `options_svc` (collection + publish) and the webgui.
