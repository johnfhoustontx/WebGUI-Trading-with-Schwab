# Trade detail panel — validation + triage redesign

**Date:** 2026-08-09
**Scope:** `webgui/pages/options/detail.py` and its four adapters
**Status:** design approved, plan pending

## Why

The shared Trade detail panel is fed by four sources — Scanner, Swing, Paper,
Captured — through adapters that map each source's dict onto one set of keys. An
audit of those adapters against `options-scanner/scanner_engine.py` and
`scoring.py` found eight defects, several of which corrupt the decision the panel
exists to support.

The panel's job, confirmed with the user, is **triage**: *is this trade worth
taking?* Triage is mostly fast rejection, and rejection turns on four
dealbreakers — thin liquidity, credit too thin for the risk, short strike inside
the expected move, and wrong side of dealer gamma or trend.

Today all four are buried as four of eleven identically-weighted bars inside a
collapsed card. The panel is shaped for verification but used for triage.

## Validated defects

**1 — Unit collision, including within a single row.** Scanner signals carry
`credit` and `max_loss` **per share** (`scanner_engine.py:57`).

The paper adapter is worse than a cross-source mismatch. `paper_trader.py:114-117`
stores `entry_credit` as the **per-share** credit but `max_loss_total` as
`max_loss × quantity × 100` — the **whole position**. The adapter takes `credit`
from the first and `max_loss` from the second (`paper.py:237-238`), so one paper
trade displays Credit $1.55 beside Max Loss $1,035. Because `quantity` is a
factor, the discrepancy is not even a constant 100×.

`expected_pnl_10` sits in the same card and is itself a total
(`× contracts × 100`), so one card mixes three scales with nothing on screen to
distinguish them.

This class of bug has bitten this codebase twice already in the driver — see
`driver-executed-but-rejected-risk-too-high`.

**2 — Iron condor breakeven is permanently "—".** The engine stores it as a
string, `f"{p['breakeven']}/{c['breakeven']}"` (`scanner_engine.py:1069`). The
tile formats via `_money`, which requires `isinstance(v, (int, float))`
(`detail.py:77`), so every IC falls through to the em-dash.

**3 — A fabricated IV reading.** `range_marker_svg(low, high, current_iv or low)`
(`detail.py:161`) draws the marker at the 52-week low when current IV is missing.
That reads as "IV is dirt cheap" rather than "unknown".

**4 — Missing factors render as scored zeros.** `fs.get(key, 0) or 0`
(`detail.py:73`) draws an absent factor as the same red zero-bar as a genuine
zero.

**5 — The gauge silently changes scale.** It shows composite score, or falls back
to PoP for paper trades (`detail.py:218-221`), while remaining captioned with
`grade`. Two different 0–100 scales on one unlabelled face. The project already
treats Fit+Quality and the premium composite as non-commensurable; this is the
same error one layer down.

**6 — "DTE" means two different things.** Live days-to-expiry for paper
(`paper.py:246`) versus days-at-entry for captured (`captured.py:182`), under one
label. An aged captured signal displays a DTE that has already elapsed.

**7 — Debit structures show no cost.** `detail_signal` leaves `credit` unset for
debits (`strategy_table.py:279-281`) so it is not mislabelled as a credit —
correct, but nothing then renders the debit, so a long call's cost is simply
absent.

**8 — Paper's vega sign flip is CORRECT.** Verified: `paper_trader.py:123` stores
`entry_vega = -signal["net_vega"]`, so `paper.py:249`'s `-entry_vega` is an exact
round-trip. No change; a regression test pins the round-trip so a future edit to
either side cannot silently break it.

### The deeper problem: missing data is indistinguishable from a score

The normalizers in `scoring.py` collapse "unavailable" into a real-looking value,
in two directions:

- **Missing → 0**, looking terrible: `rr`, `pop`, `theta`
- **Missing → 50**, looking neutral: `iv`, `iv_hv`, `vega`, `em`, `liq`, `trend`,
  `gex`, `dex`

The panel renders both as confident coloured bars. The second group is the
dangerous one: eight factors can appear as calm mid-grey when the truth is "never
measured". That is false reassurance, which for triage is worse than false
rejection.

### A structural mismatch

The user rejects on liquidity, but `DEFAULT_WEIGHTS` gives `liq` 5 points against
`rr`'s 16 (`scoring.py:48-60`). A thin-liquidity trade can still post a strong
composite. Meanwhile the panel draws all eleven bars at identical visual weight,
so a 4-point `dex` reads as decisively as 16-point `rr`.

Triage on the composite score alone will therefore pass trades the user would
reject. The dealbreakers need their own treatment above the fold, not a bar in a
list.

## Design

### Chosen approach

Keep the existing 290–360px right-hand panel and restructure it into a strict
priority ladder. Considered and rejected: a pinned-header/scrolling-body variant
(same benefit, more layout risk against the persistent-Highcharts constraint, and
reachable later without rework); and widening to ~520px two columns, which would
take width from the Scanner table — the very thing being scanned.

### Layout

```
┌────────────────────────────────┐
│ Trade detail          [2] [>|] │  ← flag count badge, visible when collapsed
├────────────────────────────────┤
│ SPY · Put Credit Spread        │
│ Swing · 12 DTE                 │
│         ╭─────────╮            │
│         │   72    │   Good     │
│         ╰─────────╯            │
│       Composite score          │  ← gauge always names its metric
│                                │
│ ⚠ Liquidity not measured       │  ← only when tripped; absent when clean
│ ⚠ Short strike inside 1σ move  │
├────────────────────────────────┤
│ Sell 400 P  /  Buy 395 P       │
│ 5 wide · Exp Fri 21 Aug        │
├────────────────────────────────┤
│ Credit       $155 per contract │  ← every number carries its unit
│ Max loss     $345 per contract │
│ Breakeven         $398.45      │
│ Probability        72%         │
├────────────────────────────────┤
│ ▸ Score factors                │
│ ▸ Greeks                       │
│ ▸ Implied volatility           │
│ ▸ Expected move                │
└────────────────────────────────┘
```

The ladder runs **reject → verify → explore**. Flags sit above the contract
because rejection should happen before strikes are read.

**The gauge always names what it shows.** One named metric, caption always
present. Paper trades have no stored composite, so the gauge shows PoP captioned
"Probability of profit" rather than borrowing the composite's face and grade.

**Strikes become an instruction.** `Sell 400 P / Buy 395 P` replaces
`$400 - $395 (5-wide)`, which reads as a descending range and hides which leg is
short. Iron condors get two such lines.

**Per-contract is primary**, computed as the exact `per_share × 100`, with
per-share available in the expanded detail.

**Four cards, not five.** Trade Info dissolves upward into the contract and
economics blocks. Nothing is dropped: `max_contracts` and `E[P&L]` move into
economics, `R:R` into Score factors.

**Collapsed state** carries a flag-count badge on the toggle, matching the
existing nav badge idiom, so a flagged trade stays visible at 44px.

Labels follow the whole-words rule: "Put Credit Spread" spelled out; `DTE` and
`PoP` kept as genuine trader terms.

### Flag rules

Three thresholds fall out of the scorer's own definitions and invent nothing:

| Flag | Rule | Basis |
|---|---|---|
| Inside expected move | `em < 50` | `norm_em_buffer` returns 0–50 only when the short strike is inside 1σ (`scoring.py:208-210`) |
| Trend against | `trend < 50` | 25 = partially against, 0 = against (`scoring.py:248`) |
| Thin liquidity | `liq < 50` | 50 ⇒ spread > 3% of mark; zeroes at 5% (`scoring.py:231-236`) |
| Near a gamma wall | `gex` or `dex` < `WALL_FLAG_BAR` | 100 = ≥1% of spot away, linear to 0 on the wall |
| Credit too thin | `rr_pct` < `MIN_RR_PCT` | `norm_rr` reaches 100 at 50% |

The two judgment values live as documented constants at the top of `detail.py`,
defaulting to `MIN_RR_PCT = 20` and `WALL_FLAG_BAR = 30`, to be tuned in use.
They are deliberately not Settings knobs yet — promote them only if they turn out
to change often.

### Provenance

Each flag carries a **measured / not measured** state. Where the raw inputs ride
on the signal, presence decides it: `bid`/`ask` for liquidity, `rr_pct` for R:R.
A not-measured factor renders as a distinct amber "not measured" chip, and its
bar shows "—" rather than a number.

For `em`, `gex`, `dex` and `trend` the raw inputs are not on the signal. The
original design inferred provenance from an exact-50.0 sentinel. **Implementation
proved that unsound, and it was removed** — see the two findings below.

The clean fix is an additive **`factors_unavailable`** list emitted from Tier 2,
which the documented Tailwind-first exception explicitly permits ("refactor the
Tier-2 source to emit one"). The panel consumes it whenever it is present.

### Two findings from implementation (2026-08-09)

Both were found by driving the real `scoring.py` normalizers through the flag
engine rather than reasoning from the code, and both were confirmed in source.

**The 50.0 sentinel could not distinguish missing from measured.**
`norm_trend` returns exactly `50.0` for a missing trend **and** for a genuine
`"NEUTRAL"` reading (`scoring.py:250-251`) — and NEUTRAL is one of five routine
trend values, not an edge case. Worse, `gex` and `dex` are 50.0 *by design* for
swing trades, which have no walls. A realistic swing signal therefore raised
**four** "not measured" chips, destroying the absent-when-clean property the
layout depends on.

*Decision:* only **liquidity** reports "unmeasured", because `bid`/`ask` presence
is external proof rather than a sentinel guess. `em`, `trend`, `gex` and `dex`
flag only when genuinely tripped. This accepts a silent gap for a truly missing
`em`/`gex`/`dex` until Tier 2 emits provenance; for `trend` the silence is
actually correct, since NEUTRAL means "not against the structure".

**The flag engine was blind to iron condors.** IC signals carry
`factor_scores = {pcs_leg, ccs_leg, delta_bonus}` (`scanner_engine.py:1654-1658`)
— none of `em`/`liq`/`trend`/`gex`/`dex` exists. An iron condor with its short
strike deep inside the expected move rendered a clean, flagless panel: a silent
false negative across an entire first-class strategy the scanner emits. For a
feature whose purpose is fast rejection, that is more dangerous than a spurious
warning.

*Decision:* detect the IC shape and emit one explicit note — "Dealbreaker checks
unavailable for iron condors" — which **counts toward the collapsed-strip badge**,
so a flagless IC can never be mistaken for a clean one. `rr_pct` IS present on IC
signals (`scanner_engine.py:1065`), so the credit-vs-risk check stays active.

The proper fix is deferred but tractable: the engine already computes full
per-leg factor scores inside `_leg_score` and then discards everything but the
composite. Preserving them would let both legs be flagged normally.

### The root cause: `factor_scores` is three different vocabularies

The iron-condor blindness was not a one-off. Chasing it revealed that
`factor_scores` is not one schema but **three**, and the flag engine had been
designed against only the first:

| Source | Keys | Where |
|---|---|---|
| Scanner credit spreads | `rr, pop, theta, iv, iv_hv, vega, em, liq, trend, gex, dex` | `scoring.py:48-60` |
| Scanner iron condors | `pcs_leg, ccs_leg, delta_bonus` | `scanner_engine.py:1654` |
| Swing / Strategy Finder | `fit_dir, fit_vol, q_rr, q_be, q_pop, q_liq` | `strategy_scoring.py:664` |

Any page-side logic that reads `factor_scores` by key name silently does nothing
on two of the three. **Check all three before adding another.**

**Mapping between them is unsafe.** `q_breakeven_vs_em` rewards a breakeven
*inside* the expected move — "closer / inside EM = higher"
(`strategy_scoring.py:369`) — because a directional debit trade profits from a
small move. `norm_em_buffer` rewards the opposite, because a credit seller wants
distance. Same concept, **inverted sign**, driven by opposite payoff profiles. A
naive `q_be < 50` test would have flagged good swing trades as bad and passed bad
ones.

*Decision:* for swing signals, derive flags from the engine's OWN gate result
rather than re-deriving thresholds. `evaluate_gates` (`strategy_scoring.py:555`)
already returns a family-aware `reasons` list over `["liquidity", "R:R", "PoP"]`,
with separate bars per family (credit / debit / naked), surfaced on the signal as
`grade_reason`. Reading it is family-correct by construction and cannot invert
semantics.

The swing path therefore covers liquidity, R:R and PoP but **not** the
expected-move dealbreaker, because breakeven-vs-EM is deliberately not a hard
gate in that engine ("a ranking quality factor, not a hard filter"). That gap is
left silent rather than chipped, on the same reasoning as `gex`/`dex` above: a
warning present on every swing signal is noise, not information.

### Data contract

Adapters stop emitting bare numbers whose units depend on their source. Each
declares units explicitly, and the panel renders per-contract. Debit structures
emit a signed `net_cost` so the panel can label Credit or Debit rather than
showing neither.

## Testing

Pure helpers — `flags_for`, `economics_rows`, `contract_lines`, `factor_rows`,
`gauge_caption` — are unit-tested against fixtures captured from all four
sources, including an iron condor (breakeven string), a debit vertical (no
credit), a paper trade (no composite), and a signal with absent factors.

Regression tests pin the specific defects: an IC must render a numeric
breakeven; a signal without `current_iv` must render no IV marker; an absent
factor must render "—" and never `0`; per-share and total inputs must produce the
same displayed per-contract figure.

`test_no_inline_style.py` continues to guard the page.

## Out of scope

Widening the panel; moving thresholds into Settings; changing the composite
scoring model or its weights. The weight-versus-dealbreaker mismatch is recorded
above as a finding, and addressed in the panel by surfacing flags independently
of the score — not by re-weighting the scorer.
