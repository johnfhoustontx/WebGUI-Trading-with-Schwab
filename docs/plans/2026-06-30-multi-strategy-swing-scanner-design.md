# Multi-Strategy Swing Scanner — Design

**Date:** 2026-06-30
**Branch:** `Using_Highcharts`
**Status:** Approved (brainstorm complete) — ready for implementation plan

## Problem

Today the **Swing Scanner** (`/options/swing`) is narrowly a *credit-spread premium
scanner*. The page collects gates that only make sense for **selling premium** (DTE,
put/call **delta windows**, **min credit %**); `compute.swing_scan` runs
`screen_spreads` (→ **PCS** put-credit + **CCS** call-credit) + `build_iron_condors`,
then `scoring.score_all_signals`. The scoring engine (`scoring.py`) is a **9-factor
model built for credit spreads** (R:R = credit/max_loss, PoP ≈ 1±delta, theta
efficiency, IV rank, IV/HV, vega, **expected-move buffer**, liquidity, trend).

The user wants the scan expanded to cover four strategy families:

1. **Directional** — Long Call/Put, Short Call/Put
2. **Spreads** — Debit Call/Put, Credit Call/Put
3. **Neutral** — Condor / Iron Condor / Butterfly
4. **Diagonal**

The crux: **long calls/puts, debit spreads, and butterflies are not premium-selling
trades.** They have **negative theta**, **low PoP** (~delta), **undefined R:R**
(credit/max_loss doesn't exist), **want** to breach the expected-move buffer rather
than avoid it, and prefer **low** IV (you're buying) instead of high. Fed into the
existing scorer, they would all rank near zero. So the heart of this feature is a
**new unified scoring model** that makes a long call and a put-credit-spread
comparable on one 0–100 scale.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Presentation | **One unified ranked list** — a single scan emits every requested structure for a symbol, all on one comparable 0–100 scale, ranked together. |
| Bias anchor | **Scanner infers the bias** from the symbol's technicals + IV regime; ranks each candidate by fit. No bias input from the user. |
| Universe | **Single symbol** per scan (as today). |
| Scoring model | **Two-part Fit + Quality score** (see §5). |
| Naked shorts | **Include**, scored on the same rubric, **no special risk treatment**. |
| Handoff scope | **Calculator + Expected-Move now** for all new structures; **Paper-trade deferred** for non-credit-spread types (paper engine assumes defined-risk credit spreads). |

## 1. Strategy taxonomy (candidate generators)

New `type` codes + `family` tags. ✓ = reuses today's engine functions.

| Family (`family`) | Structures (`type`) | Stance |
|---|---|---|
| `DIRECTIONAL` | `LONG_CALL`, `LONG_PUT`, `SHORT_CALL`, `SHORT_PUT` | directional ± |
| `VERTICAL` | `BULL_CALL` (debit), `BEAR_PUT` (debit), `PCS`✓, `CCS`✓ | directional ± |
| `NEUTRAL` | `IRON_CONDOR`✓, `CONDOR` (all-call/all-put), `IRON_FLY`, `CALL_FLY`/`PUT_FLY` | neutral |
| `DIAGONAL` | `CALL_DIAG` (bullish), `PUT_DIAG` (bearish) | directional + calendar |

Strike-selection heuristics per builder (delta-targeted, tunable constants):
- **Longs:** ATM-ish, ~0.45–0.60 delta (convexity vs cost trade-off).
- **Naked shorts:** ~0.20–0.35 delta (premium vs assignment risk). Max-loss for a
  naked short put = strike − credit; naked short call is unbounded → use a
  margin-based capital proxy purely for the quality/capital-efficiency factor (the
  trade is still ranked, per the user's "no special treatment" choice — the score
  naturally reflects the poor capital efficiency).
- **Debit verticals:** buy ~0.55–0.65 delta, sell ~0.25–0.35 delta.
- **Credit verticals:** reuse `screen_spreads` (PCS/CCS).
- **Condor / fly:** body/wings placed around the **expected-move** band; iron fly
  short legs ATM.
- **Diagonals (Phase 3):** long leg deeper/longer-dated, short leg nearer/OTM
  (needs multi-expiration chain handling).

Each builder emits a small **top-N per expiration** by its own internal merit to keep
candidate counts bounded; the unified scorer ranks across all.

## 2. Bias inference — `infer_market_view(technicals, iv_analysis)` (pure)

Returns:

```python
{"direction": "bullish"|"bearish"|"neutral",
 "conviction": 0.0..1.0,          # strength of the directional read
 "vol_regime": "low"|"mid"|"high"}
```

Inputs are **already computed** in `swing_scan` today (`calc_technicals` → EMA
alignment / RSI / ADX / MACD; `run_iv_analysis` → IV Rank, IV/HV). Direction +
conviction from trend/momentum; vol regime from IV rank + IV/HV. Surfaced to the user
as a banner (e.g. *"Inferred: Bullish · conviction 0.6 · IV Rank 22 (low) → favors
long / debit"*).

## 3. Normalized signal shape (the unifying key)

Every candidate — 1-leg or 4-leg — becomes one dict with a canonical **`legs`** list:

```python
leg = {"kind": "call"|"put", "side": "long"|"short", "strike": float,
       "expiration": "YYYY-MM-DD", "qty": int,
       "mark": float, "delta": float, "theta": float,
       "vega": float, "gamma": float, "iv": float}
```

plus economics computed off the payoff diagram:

```python
{"id", "symbol", "type", "family", "strategy_label", "bias",
 "legs": [...], "expiration"/"dte" (front),
 "net_debit"|"net_credit", "max_profit", "max_loss", "capital",
 "breakevens": [float, ...], "pop_pct", "rr",
 "net_delta", "net_theta", "net_vega", "net_gamma",
 "underlying_price", "timestamp",
 # scoring (added by the scorer):
 "composite_score", "grade", "factor_scores", "fit_score", "quality_score"}
```

This `legs` form is what makes display, the detail panel, and the
Calculator/Expected-Move handoff work uniformly (the Calculator already speaks
multi-leg `legs`). Existing PCS/CCS/IC dicts are adapted into this shape (keep their
current fields for back-compat; add `legs` + `family` + `strategy_label`).

Payoff economics (max profit/loss, breakevens, PoP) are computed by a small pure
**payoff helper** over the `legs` (terminal value at expiration across a strike grid;
PoP from delta-implied terminal distribution). Reuse `options_calculator` / simulator
math where it already exists rather than re-deriving Black-Scholes.

## 4. Scoring — `score_strategy(signal, view, iv, tech)` → 0–100 (pure)

Two components, each normalized 0–100, combined ≈50/50 (weights in one tunable
constants block):

**Thesis-Fit** — does the structure match the inferred view?
- *Directional fit:* the structure's net-delta sign/magnitude vs inferred
  `direction × conviction`. A bullish structure scores high in a high-conviction
  bull read, low in a bear read; neutral structures score high **only** when
  conviction is low.
- *Vol fit:* the structure's net-vega sign vs `vol_regime`. Long-premium / long-vega
  fits **low** IV (cheap to buy, expansion expected); short-premium / short-vega fits
  **high** IV.

**Structural-Quality** — strategy-agnostic merit, family-appropriate inputs:
- Liquidity across **all** legs (reuse `norm_liquidity` + the liquidity gate).
- Bounded R:R / capital efficiency (`max_profit / capital`).
- Breakeven-vs-expected-move: directional → is the breakeven within reach of the EM;
  neutral → is the profit zone wide vs the EM.
- PoP from the payoff.

Output is unified (one 0–100 + grade via the existing thresholds Strong ≥80 / Good
≥60 / Marginal ≥40 / Weak <40), but the **inputs are computed per family** — exactly
the Fit+Quality model chosen. `factor_scores` carries both `fit_*` and `quality_*`
sub-scores for the detail panel.

## 5. Where the code lives (respects 3-tier + module-collision rules)

- **`options-scanner/strategy_scanner.py`** (NEW) — builders + payoff economics. Pure,
  TDD'd against synthetic chains. Reuses `screen_spreads` for credit verticals.
- **`options-scanner/scoring.py`** (or sibling `strategy_scoring.py`) — add
  `infer_market_view` + `score_strategy` + Fit/Quality normalizers. Pure.
- **`services/options_svc/compute.py`** — extend `swing_scan`: fetch chain/tech/IV
  (as today) → `infer_market_view` → build the selected families → `score_strategy` →
  rank → return `{signals, view}`. `scoring`/`strategy_scanner` imported lazily (same
  cross-app `scoring` collision guard rationale as today).
- **`services/options_svc/handlers.py`** — same `swing_scan` command; extended payload
  `{signals, symbol, params, view}`; **same `cache:options:swing` key** (the page is
  the only reader → backward-compatible). New default args for the family selector.
- **`webgui/pages/options/swing.py`** — add a **strategy-family multiselect**
  (default: all families), an inferred-**Market View banner**, and
  **strategy-agnostic table columns** (Strategy · Bias · Legs · Debit/Credit · Max P ·
  Max L · R:R · PoP · BE · Score · Grade). The spread-only Δ/credit gates move into an
  **"Advanced (credit spreads)"** expander (they only constrain PCS/CCS).
- **`webgui/pages/options/detail.py`** — render generically from `legs` (keep the
  existing spread rendering as a fallback).
- **`webgui/pages/options/handoff.py`** — extend `signal_to_em_payload` +
  `send_to_calculator` mappings to read the canonical `legs` for **all** new types.
  `add_row_actions` shows Calculator + Expected-Move for every type; **Paper-trade is
  shown only for credit-spread types** (PCS/CCS/IC) in this work.

## 6. Testing (TDD)

Every pure piece is unit-tested before wiring:
- Each builder: synthetic chain → expected legs, economics, breakevens, PoP.
- `infer_market_view`: technicals/IV fixtures → expected direction/conviction/regime.
- Fit + Quality normalizers: boundary + sign behavior (bullish structure in bear read
  scores low; long-vega in high-IV scores low; etc.).
- End-to-end `compute.swing_scan` with a fake chain (mirrors existing
  `services/options_svc/tests/test_compute.py` monkeypatch pattern).
- Handler payload shape; page pure builders (columns/rows/banner/legs-summary);
  handoff mappings for new types.
- Matches existing `options_svc` + `webgui` suites; add new page to
  `webgui/tests/test_no_inline_style.py` if any markup changes.

## 7. Phasing (ship incrementally; each phase tested + browser-verified)

1. **Phase 1 — Foundation + Directional + Verticals.** Normalized signal model + payoff
   helper + `infer_market_view` + Fit/Quality scoring + builders for longs, naked
   shorts, debit verticals (+ reuse credit verticals) → end-to-end on the page
   (family multiselect, view banner, new columns, Calculator/EM handoff).
2. **Phase 2 — Neutral.** Condor (all-call/all-put), butterfly (call/put fly), iron
   fly builders + their quality inputs.
3. **Phase 3 — Diagonals.** Multi-expiration strike selection (the most complex
   builder) for `CALL_DIAG` / `PUT_DIAG`.

## Out of scope (explicit)

- Multi-symbol / watchlist sweep (single-symbol only; code structured to allow it
  later but not built).
- Paper-trade open/track/manage for non-credit-spread structures (deferred).
- Live trading (paper only — `config.PAPER_TRADE` stays True).
- Changing the existing 0-DTE full scan (`cache:options:scan`) — untouched.
