# Options Strategy Calculator — screen redesign (2026-08-19)

Rebuild `/options/calculator` to a supplied design: a three-step numbered layout
(① STRATEGY · ② SYMBOL · ③ LEGS) with a fixed 424 px input column and a results
column carrying six metric cards over the P&L matrix.

The design arrived as a `.dc.html` design component plus a readme. It ships its
own Black-Scholes and a mock chain, because it had to run standalone. **None of
that is ported.** The page stays a Tier-1 reader over `options_svc`; the design
contributes layout, palette and a handful of readouts that turn out to be
derivable from what the bus already caches.

## What the design adds, and where each number comes from

The redesign is not only a restyle — the mockup reads more than today's screen
shows. Every added readout resolves against the existing cache, so **no Tier-2
change is needed**:

| Design element | Source |
|---|---|
| Per-leg `DELTA` | `chain[…][strike][0]["delta"]` — a new pure extractor beside `extract_premium`. The Schwab chain already carries delta (`flow_alerts` reads the same field), so this is real market delta, not a re-implemented model. |
| `NET` premium · `MAX LOSS` strip on the ③ LEGS frame | Pure functions over the current legs, porting the mockup's own arithmetic. |
| Matrix `%` column | `pnl / summary.max_profit`, computed page-side. |
| Status pill · chain hint · scan bar | The page's existing `loading` / `chain` state, mapped by one pure function. |
| Strategy tags + one-line thesis | New authored content in `strategies.py`, covering all 18 codes. |

⚠ **Delta degrades to `—`, never to `0`.** Index option chains read hollow
outside regular hours (see the standing note on `$SPX`/`$NDX` open interest
zeroing after hours); a `0.00` delta on a live-looking row would be a confident
wrong number, which is the failure mode this repo keeps re-learning.

### Two semantics that change

**The matrix `%` column now means *% of max return*.** It was *% of premium
received* (`calc_spread_pnl` computes `pnl / abs(total_premium_received)`). Both
are computable page-side from the same payload; % of max return is the reading
that pairs with the MAX RETURN tile directly above the matrix. The service
keeps emitting `pnl_pct` — the page simply stops using it and derives its own.

**The top-level Expiry survives the redesign.** The mockup has expiry per-leg
only. The real page's top-level Expiry drives `calc_compute`'s `expiry` argument
and the `apply_expiry` propagation to every leg, so dropping it would delete
working behaviour to match a mock that never had to call a service. It moves
into the ② SYMBOL readout row, where the design puts the other scalars.

## Layout

```
STRATEGY CALCULATOR                          ● CHAIN LOADED · SPY
─────────────────────────────────────────────────────────────────
┌ 424px ─────────────┐  ┌ flex-1 ──────────────────────────────┐
│ ① STRATEGY         │  │ ② SYMBOL              [LIVE]         │
│  cascading picker  │  │  TICKER SPOT PRICE IV% RATE% IVΔ%    │
│  [CREDIT][2 LEGS]  │  │  CONTRACTS STRIKES EXPIRY            │
│  one-line thesis   │  │  [LOAD CHAIN] [IV UPDATE]  ▁▁ status │
├────────────────────┤  ├──────────────────────────────────────┤
│ ③ LEGS  2·NET·MAX  │  │  6 metric cards (accent left border) │
│  ┌ leg card ─────┐ │  ├──────────────────────────────────────┤
│  │ TYPE SIDE EXP │ │  │  P&L MATRIX                          │
│  │ STRK QTY PREM │ │  │  sticky header, 7 date cols × ($,%)  │
│  │           Δ ✕ │ │  │  amber spot row, amber expiry col    │
│  └───────────────┘ │  │                                      │
│  [+ ADD LEG][RESET]│  │                                      │
├────────────────────┤  │                                      │
│ [FETCH][CALCULATE] │  │                                      │
│ [EXP MOVE][→ SIM]  │  │                                      │
│ status line        │  │                                      │
└────────────────────┘  └──────────────────────────────────────┘
```

Before CALCULATE, the region below ② is the design's dashed **AWAITING
CALCULATION** panel rather than an empty card — the mockup distinguishes
*awaiting chain* from *awaiting calculation* and says which is which, which is
worth keeping.

The numbered frames use the design's notched border: a `relative` frame with an
`absolute -top-1.5` label chip painted in the page background so it interrupts
the border line. Pure Tailwind — no CSS rule needed.

## Theme — a page-scoped `[calc]` section

The design's palette is near-black with cyan/green/amber and JetBrains Mono,
deliberately unlike the app-wide dark-navy look. It becomes a **page-scoped
language**, mirroring `[console]` / `[macro]` / `[sectors]` / `[rotation]`
exactly: config-driven, not surfaced in Settings → Appearance, not the app
palette.

`config/theme.toml` gains `[calc]`. `theme.py` gains `build_calc_tokens`,
`build_calc_font_head_html` and `CALC_KEYFRAMES_CSS`, exporting `CALC_PAGE` /
`CALC_FRAME` / `CALC_TILE` / `CALC_INPUT` / `CALC_BTN` / `CALC_BTN_PRIMARY` /
`CALC_EYEBROW` / `CALC_VALUE` plus a semantic `CALC_POS` / `CALC_NEG` /
`CALC_ACCENT` / `CALC_WARN` / `CALC_DIM` set.

Palette: page `#05070a` under `radial-gradient(1100px 560px at 14% -12%,
#0b1a24 0%, #05070a 62%)`; frames `#0b1118 → #06090d` with `#26505c` active and
`#1d2937` idle borders; cyan `#22d3ee`, green `#2dd4a7`, red `#fb5f7c`, amber
`#f5b841`, dim `#6f8598`, base text `#cfdae8`, bright `#eaf2f9`.

**Scope hook is `.calc-v3`.** The Quasar-internal escape-hatch block (field
controls, the teleported strategy popup, the leg cells) is rewritten against it.
`.calc-v2` stays exactly as it is for the Simulator and Trade — this redesign
must not restyle two pages by accident.

Two keyframes carry over from the mockup: the status pill's `blip` pulse and
the chain-load `scan` sweep.

The two data-driven colours stay legal under the Tailwind-first standard: the
matrix cell tint lives in a raw `ui.html()` fragment (already out of scope, as
today), and the leg accent maps a finite `{long, short}` set onto fixed classes.

## Leg editor — card layout, on both pages

`build_leg_editor` gains `layout="card"`: the design's two grid rows (TYPE ·
SIDE · EXPIRY, then STRIKE · QTY · PREMIUM · DELTA) plus the remove button, with
a side-coloured left accent.

**The Simulator adopts the card too, but keeps its navy skin.** That works
because the palette enters as a `tokens` argument — a small dict of Tailwind
class strings defaulting to the navy set. The Calculator passes `CALC_*`; the
Simulator passes nothing and looks as it does today, with the new geometry.

`state['legs']` remains the single source of truth. `strategies.py`,
`normalize_legs`, `coerce_strike`, `coerce_choice`, `apply_expiry` and the
cross-page copy payload are untouched — this is a render change, not a model
change.

Two new optional injections, both defaulted so the Simulator opts out by saying
nothing: `delta_for(leg)` and a `min_legs` floor (the design locks removal at
two legs).

## Strategy picker

The shared cascading family→variant picker stays — the Simulator mounts the same
component, and a flat list of 18 codes would lose the grouping. It is restyled
into the ① STRATEGY frame, with the design's tag chips and one-line thesis
underneath.

Tags and blurbs are authored in `strategies.py` beside `STRATEGY_MENU`, so the
two pages cannot drift and the content is pure and unit-testable.

## Testing

Every new behaviour lands as a pure function, testable without a browser:

- `extract_delta(chain, option_type, strike, expiry)` — including the missing /
  hollow-chain path returning `None`.
- `net_premium(legs)` and `max_loss_estimate(legs)`.
- `matrix_pct_of_max(pnl, max_profit)` — including `max_profit == 0`.
- `strategy_tags(code)` / `strategy_blurb(code)` — with a completeness test over
  every `STRATEGY_TEMPLATES` key, so a new strategy cannot ship untagged.
- `build_calc_tokens(theme)` — missing / malformed `[calc]` degrades to the
  built-in defaults and never raises, as every other section builder does.
- `chain_status_facts(phase, symbol, chain)` → the pill label, colour and hint.

The existing extractor and grid-banding tests in `test_options_calculator.py`
stay; the tile and grid render assertions are updated. `test_leg_editor.py`
gains card-mode coverage and `test_options_simulator.py` a card regression.
`test_no_inline_style.py` already covers this page and holds the rewrite to the
Tailwind-first standard.

## Documentation

`docs/webgui-routes.md` `/options/calculator`, and `webgui/page_help.py`'s
calculator entry — which describes the old layout and is the most-read prose in
the app — are rewritten in the same change, not left to rot. CLAUDE.md gains
`[calc]` in its list of page-scoped theme sections; the CHANGELOG gets the
shipping entry.

## Rejected alternatives

**Porting the mockup's Black-Scholes.** It exists only because a standalone
`.dc.html` cannot call a service. Pricing already lives in `options_svc`, and a
second model in Tier 1 would be a second answer to the same question — the exact
shape of the `RISK_FREE_RATE` duplication this repo already paid for once.

**Mapping the design onto the existing navy tokens.** Cheaper, but the screen
would not be the design. The page-scoped-language precedent exists precisely so
a screen with its own visual argument can have one.

**A flat strategy `<select>`.** Matches the mockup literally at the cost of the
family grouping and of drift from the Simulator.

**Extending `calc_compute` to return greeks and a max-return `%`.** Both are
already derivable Tier-1 side. A service change would add contract surface and
test surface for numbers the page can compute from the payload it already holds.
