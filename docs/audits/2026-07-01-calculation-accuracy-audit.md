# Calculation-Accuracy Audit — WebGUI Trading with Schwab

**Date:** 2026-07-01 · **Branch:** `Using_Highcharts` · **Auditor:** Claude (five parallel quant deep-dives — BSM pricing/Greeks/IV/EM, GEX/gamma-exposure, trade-economics/PoP/P&L/commissions, factor-model/backtest/Markov, technical-indicators/sentiment/RRG/portfolio). Every formula was located and quoted at file:line and compared against both the strict textbook/industry standard and the app's actual requirement.

**Question asked:** Are the calculations accurate — do they match what the application requires, and do they match strict industry standards?

**Short answer:** The high-stakes math — options pricing, the Greeks, expected move, trade economics, GEX regime signals, and the validated factor model — is **textbook-correct and, notably, honest about its own limitations**. The weak spot is the **base technical indicators** (RSI, ADX, VWAP), which use non-standard smoothing/anchoring under standard names, so they won't match any charting platform. And there is **one correctness gap that reaches the autonomous driver**: commissions are missing from the swing scanner's R:R and grading.

---

## ⚑ Remediation status (updated 2026-07-01)

**All High + Medium findings have been fixed** (TDD, every affected suite green: options-scanner 1166 [+10 pre-existing baseline fails], options_svc 314, trade_svc 68, sentiment_svc 52, portfolio-analyzer 198, portfolio_svc 27), **except two Medium quant items deferred** as documented below.

| Finding | Sev | Status | Notes |
|---|---|---|---|
| C1 commissions in swing economics/grading | High | **FIXED** | New PURE `options-scanner/commissions.py`; round-trip commission folded into `payoff_metrics` (`/options/swing` page). |
| C1b **driver-facing** commission gap (follow-up) | High | **FIXED** | The audit said C1 feeds the driver; during the fix we confirmed the live driver sizes from the **FLAT scanner** (`cache:options:scan`), not the swing scanner. Rather than mutate the flat scanner's tuned composite score / sort / paper-BP sizing (which all consume the gross `credit`/`max_loss`/`rr_pct`), `scanner_engine._attach_net_economics` adds **additive** `commission`/`net_credit`/`net_max_loss`/`net_rr_pct` to every PCS/CCS/IC signal, and the driver's model menu (`driver_svc.compute._menu_item` + the decider system prompt) now shows the model the **net** credit/max_loss + commission. So the driver's perceived edge is net-of-fees while the tuned scoring/ranking/sizing and the webgui display are untouched. Guardrail BP sizing still keys off the raw gross `max_loss` (structural margin — commission is a transaction cost, not margin), by design. |
| C2 RSI → Wilder RMA | High | **FIXED** | Validated vs StockCharts worked example (70.53/66.32). |
| C3 ADX → Wilder smoothing | High | **FIXED** | +DM/−DM rule & DX were already correct; only smoothing changed. |
| C4 VWAP → session-anchored | High | **FIXED** | Resets each session. |
| C5 RS return-ratio → parity | High | **FIXED** | `technical.py` + `analysis_lib/sector_analysis.py` (2 occurrences). |
| C6 simulator expiry 16:00 ET tz | Med | **FIXED** | Shared `expiry_time_to_years`; 0DTE 15:30 was collapsing to intrinsic-only. |
| C7 single-source `r`; document `q=0` | Med | **FIXED (2026-08-19 — see note)** | `q=0` documented in BSM docstrings on 2026-07-01. The single-source half was **marked fixed prematurely**: the calculator, the simulator and `compute.calc_iv` were converged onto `options_calculator.RISK_FREE_RATE`, but `gamma_tool` kept five `0.045` literals, `options_svc.compute`'s projection band a sixth, and `backtest_0dte` its own `RISK_FREE = 0.04` — so the exact divergence C7 names (0.045 vs 0.04) survived this row for seven weeks. All six now import the constant, and a source-level guard in `test_expiry_time_rate_consistency.py` fails on a seventh. |
| C8 two PoP conventions | Med | **FIXED (labeled)** | Documented distinctly; intentionally not unified (would shift tuned gates). |
| C9 term-GEX ×0.01 units | Med | **FIXED** | Was 100× off; nearest-expiry-relative documented. |
| C10 swing payoff per-share vs ×100 | Med | **FIXED** | Normalized to per-contract ×100; `_normalize_credit` capital bug fixed. |
| C11 live-vs-fit z-basis | Med | **FIXED** | Live scorer now matches fit's 2/98 winsorization (±3 clip only on thin-snapshot fallback). |
| C14 portfolio annualization basis | Med | **FIXED** | 252 trading-day basis via `busday_count`, matching √252 vol. |
| C15 volume-profile value area | Med | **FIXED** | Contiguous growth from POC. |
| C12 covariance-aware factor weighting | Med | **DEFERRED** | Requires re-running `fit_swing_model.py` against live 5-yr proxy data to regenerate + re-validate `swing_model.json`; documented in-code (`swing_model.py`). |
| C13 regime-gate `low_vol` sign | Med | **DEFERRED** | Same refit requirement; the artifact's regime-key structure is ready for it. |

**Why C12/C13 were deferred, not done:** both change the *methodology* of a **validated** statistical model and require regenerating the versioned artifact via the explicitly-manual, network-dependent offline fit. Altering a validated model's weighting without re-measuring its OOS IC would be worse than the disclosed, caveated weakness. The code paths are documented in `swing_model.py`; run `fit_swing_model.py` to complete them.

**Also fixed (was Low):** the **paper engine now debits commission into realized P&L at close** (`paper_engine.net_realized_pnl` → both close sites), so the driver performance scorecard **and** the manual paper account are net-of-fees. A *managed* close (BUY_TO_CLOSE) debits the round-trip commission (open + close); an *OTM expiration* debits only the opening commission. The debit reduces both the stored position `realized_pnl` and account cash from the one value in `_close`, keeping equity consistent. (The rescue-apply close path already debited commission.)

The remaining Low-severity items (covariance-weighter report detail, gamma flip-band width, "1σ ≈ 68%" labeling, etc.) remain open by design — see the recommendations below.

---

## Scores by calculation domain

| Domain | Score | One-line verdict |
|---|---|---|
| Options pricing · Greeks · IV solver · Expected Move | **8 / 10** | Textbook BSM; the bugs the code's own history worried about are all genuinely fixed |
| GEX / gamma exposure · flip · walls | **8 / 10** | Correct dealer-gamma formula & signs; magnitudes are internally-relative, not SpotGamma-comparable |
| Trade economics · PoP · P&L · commissions · buying power | **8 / 10** | Execution math correct; commissions missing from scanner grading feeds optimistic driver edge |
| Factor model · backtest · Markov | **8 / 10** | Genuinely look-ahead-free and honestly caveated; thin edge, one basis mismatch |
| Technical indicators · sentiment · RRG · portfolio | **6.5 / 10** | Scoring/RRG strong; RSI/ADX/VWAP/RS deviate from standard under standard names |
| **Overall calculation accuracy** | **~7.7 / 10** | Trustworthy where money is at stake; indicator labels overstate standard-conformance |

---

## What is genuinely correct (the reassuring part)

Several of these are the exact formulas that are *most commonly* implemented wrong, and here they're right — often with the code's own history showing a prior bug was found and fixed:

- **Black-Scholes d1/d2, call/put pricing, put-call parity** — textbook (`options_calculator.py:51,80`), intrinsic-floored so prices can't go negative.
- **All five Greeks** including the two most-often-botched: **theta has the correct sign and /365 calendar scaling** (`options_calculator.py:345-350`) and **vega/rho are correctly per-1%** — directly refuting the classic "theta off by 365× or sign-flipped" failure.
- **The IV solver** is robust bisection with correct guards (returns `None` on sub-intrinsic marks, arbitrage-violating prices, T≤0) — production-quality for a retail tool (`options_calculator.py:85-114`).
- **Intraday time-to-expiry / 0DTE** is genuinely calendar-fractional (`compute.py:1973`); the documented "or 1/365 clamp that over-priced 0DTE ~20×" is gone from the request path.
- **Expected Move** = S·σ·√(t/365) with the correct √-time cone, and the off-hours-collapse bug is bypassed by a code-authoritative stable 1-day EM (`compute.py:1343,2519`).
- **The ×100 contract multiplier** is applied consistently — including the simulator What-if, where a prior "−$200 vs −$20,000" bug is fixed (curve *and* baseline both scaled).
- **GEX** has the correct `Γ·OI·100·S²·0.01` per-1% dollar-gamma with the **correct dealer put-sign convention** (`gamma_tool.py:466,483,487`); the **gamma flip is a real interpolated zero-crossing** (`:1164-1172`) computed on the **full grid, not the cropped ±20 display window** — a real bias trap that was avoided. Charm/Vanna analytic forms are textbook.
- **Trade economics**: vertical/IC max-P/L with proper ×100×qty and the **wider-wing IC max-loss** (`options_calculator.py:529`); realized P&L; **max-loss = buying-power reservation** with symmetric release and rescue reconciliation; the **`unbounded` flag correctly catches a naked short call** as unbounded-loss (`strategy_scanner.py:83,101`); breakpoint extrema read a short put's true `(strike−credit)` max loss, not a grid-clipped value.
- **Factor model**: cross-sectional per-date z-scoring is **genuinely look-ahead-free** (`backtest.py:60-74`); factors are causal and textbook-faithful (12-1 momentum skips the recent month, short-term-reversal sign correct); **walk-forward train/test are disjoint with per-fold refit and test-only OOS IC**; **IC is proper per-date Spearman**, ICIR correctly defined (not a t-stat). The **Markov** layer is mathematically sound (row-normalized, correct Dirichlet shrinkage, convergent power-iteration stationary) and the **no-feedback-loop claim is verified true**.
- **Aggregation/scoring**: confidence-weighted blends are correct and guarded; all weight schemes sum to 1.0 (import-time asserts); **VIX term-structure signs are right** (backwardation = stress); the **flagship RRG that the page actually renders is a textbook JdK implementation** (`sector_rotation_assessment.py:213-234`).

---

## Findings that matter

### High — reaches the autonomous driver
| # | Domain | Finding | Impact |
|---|---|---|---|
| C1 | Economics | **Commissions absent from the swing scanner's `max_profit`/`max_loss`/`rr`/PoP and the quality-gated grade** (`strategy_scanner.py`, `strategy_scoring.py`). "Commission-aware" is true only in `rescue.py`, not the scanner. For an IC that's 4 legs × $0.65 × 2 sides = **$5.20/contract** unmodeled against a max-profit that may be $30–60. | The driver picks from signals whose R:R/edge ignore commissions → its net-$500/day target and grade filter are **systematically optimistic**. It inflates *perceived edge*, not position size (sizing/BP key off max_loss, which is defended correctly and is if anything slightly conservative). The single driver-facing correctness gap. |

### High — user-facing indicators don't match standard under standard names
| # | Domain | Finding | Impact |
|---|---|---|---|
| C2 | Indicators | **RSI uses a simple rolling mean, not Wilder's RMA** (`technical.py:120-121`; repeated in `trade_svc/compute.py:224`). | Values differ from every charting platform (TOS/TradingView/StockCharts); labeled "RSI" on the Trade page. Fix is one line: `ewm(alpha=1/period, adjust=False)`. |
| C3 | Indicators | **ADX/DI use simple rolling means, not Wilder smoothing** (`technical.py:161-167`) — though the +DM/−DM rule and DX formula are correct. | ADX magnitude and the ADX>25 "trending" timing differ from standard charts; feeds the trend needle amplitude. |
| C4 | Indicators | **VWAP is a cumulative over the whole multi-day 5-min frame, not session-anchored** (`technical.py:253-254`). | Not the VWAP any trader sees; price-vs-VWAP is systematically wrong after day 1. Fix: reset cumsum per session. |
| C5 | Portfolio | **"vs Sector (RS)" divides return-by-return** (`sector_analysis.py:555`, `technical.py:653`) — unstable and **sign-inverts when the benchmark return is negative** (a stock that fell less than its sector can read as *underperforming*). | The displayed RS column is unreliable in down markets. (The *scorecard's* vs-sector uses difference-of-returns correctly, so grades are unaffected — only the display column and `calculate_relative_strength`.) |

### Medium
| # | Domain | Finding | Impact |
|---|---|---|---|
| C6 | Pricing | **Simulator expiry settlement is naive `hour=15` (no timezone); calculator uses 16:00 ET** (`options_simulator/engine.py:137,181` vs `compute.py:1956`). | 1-hour T discrepancy + tz-naivety; materially wrong Greeks/theo only at 0DTE. Paper fills unaffected (simulator is a visualizer). |
| C7 | Pricing | **Risk-free `r` hardcoded and inconsistent** (calculator 0.045 vs simulator 0.04) and **`q=0`** (no dividend) undocumented — matters for SPX/SPY ~1.3% yield on longer swing DTE. | Small dollar impact (few cents short-dated); internal inconsistency; should be single-sourced. |
| C8 | Pricing/Economics | **Two coexisting PoP conventions** — calculator uses risk-neutral lognormal with r-drift; swing scanner uses zero-drift normal (`options_calculator.py:630` vs `strategy_scanner.py:136`). | Same trade shows different "PoP" on different pages; both defensible but should be unified/labeled. |
| C9 | GEX | **Term-structure GEX drops the ×0.01** that the intraday path has (`gamma_tool.py:871` vs `:466`) → term cells are 100× the intraday scale; and **GEX aggregates nearest-expiry only**, not full-surface like SpotGamma. | Magnitudes are internally-relative only, not a numeric replica of a reference source. Sign/flip/wall *locations* are correct. Label any displayed magnitude as "nearest-expiry, relative." |
| C10 | Economics | **Swing payoff units inconsistent**: native families (directional/debit) report per-share max_loss/capital while credit adapters override to ×100 dollars, in the same signal list; `capital` stays per-share in the credit adapter, skewing the capital-efficiency gate. | Cosmetic on the display/ranking surface today (doesn't missize a real position), but latent for Phase-2 structures that reuse the adapter. |
| C11 | Quant | **Live-vs-fit z-basis mismatch**: live scorer clips z to ±3 but the calibration composite is only 2/98-winsorized, un-clipped (`swing_model.py:88`). | Mild tail miscalibration — strongly-positioned names biased toward the middle (fewer BUY/SELL than calibration implies). Not a leak. |
| C12 | Quant | **Univariate signed-IC weighting double-counts the correlated momentum cluster** (mom_12_1/mom_6_1/vol_adj_mom/trend_quality) with no orthogonalization (`backtest.py:92`). | The composite barely beats its best single factor; "diversification" is partly illusory. Honestly a simplification, not an error. |
| C13 | Quant | **`low_vol` carries the largest weight (−0.34) on a regime-overfit inverted sign** — high-vol outperformed in this 5-yr bull sample, contradicting the well-documented long-run low-vol anomaly. | The composite's forward validity hinges on its least-robust factor. Disclosed in the docs; regime-gating (the C-ready keys) is the intended fix. |
| C14 | Portfolio | **Annualized return uses 365 (calendar) while vol uses √252**, so the Sharpe-like ratio mixes bases (~1.2× scale error) (`evaluation.py:214,225`). | Risk-grade bands shift; internally self-consistent per leg but the two legs disagree on basis. |
| C15 | Indicators | **Volume-profile value area is built from a sorted-set of high-volume bins, not contiguous growth from POC**, and buckets by close-only (`technical.py:322-347`). | VAH/VAL can mislabel with disjoint shelves; low stakes (soft S/R context). POC is correct. |

### Low / defensible (noted, not urgent)
Dividend `q=0` acceptable for short-dated retail (C7 covers it); "1σ ≈ 68%" not surfaced with its probability; Charm/Vanna use a looser sign model than GEX (documented deviation); flip search band ±3% can drop the flip on high-vol days; `FILL_FRAC=0.40` fill model has no explicit slippage beyond the concession; IC/ICIR standard errors optimistic due to 20-day label overlap (mean_ic/OOS IC unaffected); isotonic calibration imposes monotonicity as a prior (disclosed); survivorship bias in the fit universe (disclosed).

---

## Cross-cutting themes

1. **The math is most correct exactly where it's most dangerous.** Pricing, Greeks, defined-risk economics, buying-power/margin, and the guardrail budget are all textbook — there is **no case where wrong math lets the driver exceed its intended per-trade or daily risk budget**. The one driver-facing gap (C1, commissions) inflates *perceived edge*, not risk-taking.

2. **"Standard name, non-standard formula" is the recurring defect class.** RSI/ADX/VWAP/RS (C2–C5) are the cluster: each is individually a small fix, but collectively they mean the Trade page's indicator strip won't reconcile with a user's broker chart. For a *relative* trend read the directional signal survives; the risk is purely that they're labeled as, and expected to be, standard values.

3. **Honesty is a genuine strength of the quant work.** The factor model discloses its thin +0.037 OOS IC, its 5/13 negative folds, its survivorship bias, and its regime-dependent low_vol sign. That candor is why the "validated swing verdict" framing is defensible — the code and docs don't oversell. This is rarer than correct formulas and worth protecting.

4. **Two "internally-relative, not reference-comparable" surfaces should be labeled as such**: GEX magnitudes (C9, nearest-expiry vs SpotGamma's full-surface) and the swing per-share-vs-×100 units (C10). Neither corrupts the decision signal; both mislead if read as absolute.

---

## Recommendations (prioritized across all domains)

**High**
1. **Fold round-trip commissions into the swing scanner economics** (`max_profit`/`max_loss`/`rr`) before grading and driver selection — mirror `rescue.py`'s `commission_for`. The only correctness gap that feeds the autonomous trade choice. *(C1)*
2. **Fix RSI and ADX to Wilder RMA smoothing and anchor VWAP per session** — three small, high-visibility changes so the Trade page matches broker charts. *(C2–C4)*
3. **Replace the return-ratio RS** with price-ratio or excess-return so "vs Sector (RS)" can't sign-invert in down markets. *(C5)*

**Medium**
4. Standardize expiry settlement on **16:00 ET tz-aware** everywhere (fixes the simulator 0DTE discrepancy); single-source **`r`**; document or add **`q`**. *(C6, C7)*
5. Unify the **PoP** convention (recommend zero-drift real-world) or label the two distinctly. *(C8)*
6. Add the **×0.01 to term-structure GEX** (or document the intentional per-$1² unit) and offer a **full-surface GEX** option; label displayed magnitudes "nearest-expiry, relative." *(C9)*
7. Unify **swing payoff units to per-contract-dollars** across native and adapted families; add a scale-invariant test. *(C10)*
8. Fix the **live-vs-fit z-basis** mismatch and **regime-gate `low_vol`** in the factor model. *(C11, C13)*
9. Annualize portfolio return on a **252 trading-day basis** to match √252 vol. *(C14)*

**Low**
10. Also **debit commissions in the paper engine** entry/close so the driver scorecard is net-of-fees. *(economics C1 sibling)*
11. Move to a **covariance-aware factor weighter**; report raw + isotonic band stats; note the IC label-overlap. *(C12 and quant lows)*
12. Contiguous **value-area growth** from POC; widen the **gamma flip band**; document Charm/Vanna sign model and the "1σ ≈ 68%" framing. *(C15 and pricing/GEX lows)*

---

### The one-paragraph answer to the question

Do the calculations match what the application requires and strict industry standards? **For the money-bearing math — yes.** Options pricing, the Greeks, expected move, defined-risk trade economics, buying-power/margin, and the GEX regime signals are textbook-correct, and the validated factor model is genuinely look-ahead-free and unusually honest about its thin edge. **Two qualifications:** (1) the base technical indicators (RSI/ADX/VWAP) and the portfolio RS column use non-standard formulas under standard names, so they won't match a broker chart — small fixes, but real defects of user expectation; and (2) the swing scanner omits commissions from its R:R and grade, which makes the autonomous driver's perceived edge optimistic (though not its risk-taking, which is correctly bounded). Fixing C1–C5 — each a small, well-scoped change — would lift the indicator and economics domains to the ~9 level the pricing and quant work already occupy.
