# Swing Scanner — Quality-Gated Grading — Design

**Date:** 2026-06-30
**Branch:** `Using_Highcharts`
**Status:** Approved (brainstorm complete) — ready for implementation plan
**Builds on:** [multi-strategy swing scanner](2026-06-30-multi-strategy-swing-scanner-design.md)

## Problem

The multi-strategy Swing Scanner grades each candidate Strong/Good/Marginal/Weak off a
composite score that is **50% Thesis-Fit + 50% Structural-Quality**. In practice the
grade doesn't reflect whether a trade is genuinely *good* — half of it is just "does the
structure agree with the inferred market view," and because Fit and the soft factors all
hover near 50, everything clusters in Marginal/Good and nothing earns a meaningful top
grade. The user's ask: **the grade must reflect the QUALITY of the trade, not a blended
number.**

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| What drives the grade | **Quality dominates + hard gates.** Structural quality drives the grade; a trade that fails hard quality bars is capped low regardless of fit. View-fit becomes a minor tiebreaker, not half the grade. |
| Which dimensions are hard gates | **Liquidity (fillability), Risk/Reward & capital-efficiency, Probability of profit.** (Breakeven-vs-EM stays a scoring *contributor*, not a gate.) |
| How gates handle opposite R:R/PoP profiles | **Per-family bars** — each structure is judged against a *good version of itself* (credit = high PoP/low R:R; long = low PoP/high R:R; naked = high PoP + capital-efficiency). |

## 1. Per-family gate bars

A config table keyed by a **gate profile** derived from the signal `type`. Each profile
declares a **minimum** (must-pass to avoid Weak) and an **excellent** level (required for
Strong) on the three gated dimensions. Starting bars (tunable constants):

| Profile (types) | reward metric | min liq / rr(or cap-eff) / pop | excellent liq / rr / pop |
|---|---|---|---|
| `LONG` (LONG_CALL, LONG_PUT) | R:R (unbounded profit ⇒ auto-pass reward) | 40 / 0.8 / 30 | 70 / 1.5 / 45 |
| `NAKED` (SHORT_CALL, SHORT_PUT) | capital-eff (R:R undefined) | 40 / 0.10 / 65 | 70 / 0.20 / 78 |
| `DEBIT` (BULL_CALL, BEAR_PUT) | R:R | 45 / 0.6 / 30 | 75 / 1.2 / 45 |
| `CREDIT` (PCS, CCS) | R:R | 45 / 0.15 / 60 | 75 / 0.33 / 72 |
| `NEUTRAL` (IRON_CONDOR) | R:R | 45 / 0.12 / 55 | 75 / 0.25 / 68 |

- **reward metric per profile:** `LONG` uses `rr` but treats unbounded profit (`rr is
  None` + `net_debit`) as auto-pass (infinite upside clears any R:R bar). `NAKED` uses
  **capital-efficiency** (`max_profit / capital`) because a naked short's R:R is undefined
  (unbounded loss). All others use `rr`.
- **liquidity** = `norm_liquidity` averaged across legs, AND an open-interest/volume floor
  (a true *fillability* check — see §3).
- Numbers are constants in one block, easily tuned.

## 2. Gate → grade

```
gates = evaluate_gates(signal, profile)     # passed_min, passed_excellent, reasons[]
composite = 0.7 * quality_score + 0.3 * fit_score   # quality-dominant (was 50/50)

if not gates.passed_min:
    grade = "Weak"
    composite = min(composite, GATE_FAIL_CAP=39)     # junk sinks in the ranking
    grade_reason = "Fails: " + ", ".join(reasons)    # e.g. "Fails: liquidity, PoP"
elif gates.passed_excellent and composite >= STRONG_MIN (78):
    grade = "Strong"
    grade_reason = "Excellent on all quality gates"
elif composite >= GOOD_MIN (58):
    grade = "Good"
    grade_reason = "Passes all quality gates"
else:
    grade = "Marginal"
    grade_reason = "Fillable but middling quality"
```

- **Weak** = fails a gate (with the specific reason). **Marginal/Good** = fillable,
  fair-value trades. **Strong** = passes the *excellent* bars on every gated dimension +
  top overall quality → genuinely rare and meaningful.
- **Fit no longer inflates the grade** (it's only 30% of the composite, which now mostly
  moves Marginal↔Good and breaks ranking ties among gate-passers).

## 3. Making the liquidity gate real (supporting change)

The normalized `legs` currently carry only `mark` + greeks — **not** `bid`/`ask`/
`volume`/`oi` — so `q_liq` (which needs bid/ask) returns a neutral **50** for every
directional/debit signal, and the liquidity gate couldn't actually judge fillability.
Fix: carry `bid`/`ask`/`volume`/`oi` onto the normalized legs.
- `strategy_scanner._leg_from` already receives the full `leg_data` (which has
  bid/ask/volume/oi from `extract_options`) — include those fields.
- `adapt_credit_spread`/`adapt_iron_condor`: fill per-leg bid/ask/volume where the source
  dict provides them (the short-leg `bid`/`ask`/`volume` + the spread marks); where a
  leg's bid/ask is genuinely absent, `norm_liquidity` degrades to 50 (unchanged).
- The liquidity gate = `avg(norm_liquidity across legs) >= liq_bar` **AND**
  `min(leg oi) >= OI_FLOOR` **AND** `min(leg volume) >= VOL_FLOOR` (OI/volume floors are
  lenient defaults, skipped when the data is absent so we never false-fail on missing
  fields).

## 4. Transparency — `grade_reason`

Each scored signal gains a **`grade_reason`** string (why it's capped / what makes it
strong). Surfaced in:
- **`strategy_table`**: a tooltip on the Grade cell + a colored Grade (Strong→green /
  Good→emerald / Marginal→amber / Weak→red via a finite `_grade_class` map, Tailwind-first).
- The user can see *why* a trade got its grade → the grade becomes trustworthy.

## 5. Where the code changes

- **`options-scanner/strategy_scanner.py`** — carry bid/ask/volume/oi onto legs (`_leg_from`
  + the two adapters).
- **`options-scanner/strategy_scoring.py`** — add the gate config + `gate_profile` +
  `evaluate_gates`; change `score_strategy` to the quality-dominant composite + gated grade
  + `grade_reason`. `score_all` unchanged (still sorts by composite desc — gate-failures
  now carry a capped composite so they sink).
- **`webgui/pages/options/strategy_table.py`** — carry `grade_reason` + `_grade_class` in
  `strategy_rows`.
- **`webgui/pages/options/swing.py`** — a Grade body-cell slot (colored + tooltip).
- **No service/contract change** — `compute.swing_scan` already passes `em_1sd`; the new
  fields ride inside the existing signal dicts + `cache:options:swing` payload.

## 6. Testing

TDD every pure piece: `evaluate_gates` (each profile passes/fails on each dimension incl.
the naked capital-eff path + long unbounded-profit auto-pass), the gated grade logic
(fail→Weak+reason, pass→Marginal/Good, excellent→Strong, gate-fail composite cap), the leg
liquidity-field plumbing, and the `strategy_table` grade_reason/color mapping. Live-verify
against the proxy that grades spread sensibly (Weak for illiquid/poor trades with reasons,
Strong rare).

## Out of scope

- Changing the *ranking* philosophy beyond the quality-dominant reweight (fit still ranks
  among passers).
- New gate dimensions beyond the three chosen (breakeven-reach stays a contributor).
- Retuning the underlying `scoring.py` credit-spread model (untouched).
