# Watchlist ↔ Index & Indicator Correlation Study

**Date:** 2026-07-21
**Window:** 2026-07-07 → 2026-07-20 (10 trading sessions)
**Granularity:** 5-minute RTH bars · **770 aligned return observations** per symbol
**Universe:** the 40-name scanner watchlist (`Top 20.xlsx`, deduped) + the 4 broad ETFs
(SPY/QQQ/IWM/DIA), correlated against indexes, volatility, breadth, and the 11 SPDR sectors.
**Data source:** live Schwab price history via the local proxy (`:8100`).

Deliverables produced alongside this note:
- `2026-07-21-intraday-correlation.xlsx` — full matrix + per-name summary + reference grid + clustered stock-stock grid + data coverage (color-scaled)
- `2026-07-21-correlation-heatmap.png` — watchlist × references heatmap (rows sorted by intraday beta)
- `2026-07-21-stockstock-clustermap.png` — hierarchically clustered stock-stock grid

---

## 1. Method

For every symbol we compute **within-session 5-minute log returns** (the first bar of each
session is dropped so overnight gaps never enter a return). All 59 series share an identical
780-bar timeline, so returns align exactly (770 valid rows after removing the 10 session-open
bars). Relationships are then measured on those returns:

| Metric | Definition | Reads as |
|---|---|---|
| **Correlation** | Pearson ρ of 5-min returns vs a reference | how tightly the name co-moves with that driver (−1…+1) |
| **Beta (SPY)** | OLS slope of name-returns on SPY-returns | intraday move per 1% of SPY (see caveat) |
| **R² (SPY)** | ρ² vs SPY | share of the name's intraday variance the *market* explains |
| **Index lean** | max(ρ vs $SPX, ρ vs $NDX) | whether it tracks the broad tape or the Nasdaq-100 |
| **Sector driver** | argmax ρ over the 11 SPDR sectors | its dominant sector factor |
| **VIX ρ** | ρ vs $VIX returns | risk-on (negative) vs defensive (positive) character |
| **Breadth ρ** | ρ vs Δ($ADVN − $DECN) | whether it moves with market *breadth* or with the mega-cap index |
| **Relative strength** | exp(Σ(ret − SPY_ret)) over the window | >1 outperformed SPY, <1 lagged |
| OBV / MACD / VWAP | on-balance-volume slope, MACD state at last bar, % of last session above VWAP | current momentum/flow snapshot (context, not relationship) |

**Pipeline validation (why to trust the numbers):** SPY–QQQ ρ = **+0.90**, SPY–$SPX ρ = **+0.99**,
SPY–$VIX ρ = **−0.89**, XLK–QQQ ρ = **+0.96** — all textbook. Defensive names correctly flip to a
*positive* VIX correlation. The relationships below are internally consistent across five
independent measures.

> **Beta caveat — read this.** These are **intraday 5-minute betas**, not daily betas.
> β = ρ·(σ_stock ⁄ σ_SPY); on 5-min bars SPY's volatility is tiny, so a volatile name's β scales
> to 3–5 even at moderate correlation. Treat **correlation** as the comparable cross-sectional
> measure and beta as "intraday amplitude vs SPY," not the ~1–2 daily beta you'd quote elsewhere.

---

## 2. Headline: this was a narrow, Nasdaq-100-led tape

The dominant relationship in the window is a single **risk factor = Nasdaq-100 / semiconductors**,
proxied almost perfectly by **XLK (ρ 0.96 with QQQ)**. Two structural facts:

1. **Breadth diverged from the index.** Δ(advancers−decliners) correlates with **IWM 0.62 / DIA 0.56**
   but only **QQQ 0.28 / $NDX 0.29**. The index was pulled by a handful of mega-cap/semis names while
   the average stock (breadth) tracked small-caps — a classic *narrow* rally. Use breadth to confirm
   IWM/DIA moves, **not** QQQ moves.
2. **A defensive counter-cohort moved *inverse* to the tape.** Staples/telecom/health/energy
   (WMT, T, XOM, ABBV, CMCSA, PFE) posted **negative** SPY correlation and **positive** VIX
   correlation — money rotated into them on risk-off ticks.

---

## 3. Derived cohorts (the relationships)

Rows are grouped by how they relate to the market. Full numbers in §5 and the workbook.

### A · High-beta NDX engine — the "risk switch" (ρ_NDX 0.6–0.8, β 3–5.5, driver XLK)
`ALAB, NBIS, IREN, WULF, DELL, MRVL, MU, RKLB, IONQ, INTC, RGTI, AMD, CRWV, HOOD, SMCI, QCOM, AVGO`
— AI-hardware, semis, crypto-miners, quantum. These **are** the Nasdaq-100 intraday, amplified.
They rise/fall together (stock-stock ρ 0.5–0.8, see clustermap), lean **NDX**, and carry the
strongest **negative** VIX loading. When QQQ/XLK turns, this whole block turns — the least
diversifying group on the list.

### B · Large-cap growth, index-correlated but tamer (ρ_NDX ~0.6, β ~2, driver XLK)
`NVDA, AVGO, TSLA, QCOM, PLTR, SPCX` — track NDX but with lower R² than cohort A; NVDA/AVGO are
the "quality" semis (high ρ, lower amplitude). PLTR leans **SPX** and was the window's RS leader.

### C · Mega-cap, market-*labelled* but idiosyncratic (R² < 0.12)
`AAPL, MSFT, AMZN, GOOGL, META` — despite being the largest index weights, their **intraday** R²
vs SPY is 0.05–0.11. Each is governed by its own sector/story: **META→XLC 0.73, AMZN→XLY 0.69,
GOOGL→XLC 0.57, MSFT→XLC 0.38, AAPL→XLY 0.18**. Do **not** hedge these with SPY intraday — the market
explains almost none of their 5-min variance. META/MSFT/AAPL/AMZN all outperformed SPY (RS > 1.05).

### D · Financials — a clean XLF factor (ρ_SPY low, sector ρ high)
`JPM (XLF 0.78), BAC (XLF 0.71)` — near-zero NDX correlation (**0.01 / 0.02**), so they're a genuine
diversifier vs the tech block. Their relationship is to **rates/financials (XLF)**, not the tape.

### E · Defensive / inverse cohort — the risk-off hedge (β < 0, VIX ρ > 0)
`WMT (−0.63, XLP 0.77), T (−0.74, XLP 0.50), XOM (−0.47, XLE 0.85), ABBV (−0.40, XLV 0.66),
CMCSA (−0.38, XLP 0.54), PFE (−0.11, XLV 0.68)` — these **fell when the tape rose** this window and
have positive VIX loading. XOM is a pure **energy (XLE 0.85)** play; WMT/T/CMCSA are **staples/telecom**.
This is the natural intraday hedge against cohort A.

### F · Truly idiosyncratic (R² ≈ 0, no reliable driver)
`NFLX (R² 0.00), CMG (0.00), UBER (0.05), AAL (0.13)` — essentially uncorrelated to indexes intraday;
they trade on their own flow. NFLX/CMG's dominant "sector" tags (XLC/XLY) are weak (ρ ≤ 0.40).

---

## 4. Relationship to each indicator

- **Indexes ($SPX vs $NDX):** 24 of 43 names lean **NDX** — the semis/AI complex. Financials,
  mega-caps, and every defensive lean **SPX**. SPY↔$SPX is 0.99 and QQQ↔$NDX is 0.99, so the tradable
  ETFs are exact proxies.
- **Volatility ($VIX):** a clean risk axis. Correlation ranges from **−0.53 (IONQ)** at the risk-on
  extreme to **+0.25 (WMT) / +0.23 (T)** at the defensive extreme. Sign of VIX-ρ ≈ sign of "is this a
  risk asset."
- **Breadth ($ADVN−$DECN):** highest for **IWM 0.62, DIA 0.56, JPM 0.26**; lowest for the mega-cap
  tech (NVDA 0.07, AVGO 0.05, META 0.08). Breadth is a **small-cap/broad-market** signal here, not a
  QQQ signal — an important nuance for any breadth-gated strategy.
- **Sector drivers:** every name maps to a sensible SPDR sector — XOM→**XLE 0.85**, WMT→**XLP 0.77**,
  JPM→**XLF 0.78**, META→**XLC 0.73**, AMZN→**XLY 0.69**, PFE/ABBV→**XLV**, the whole semi block→**XLK**.
  The full mapping is the `Top_Sector`/`Sector_Corr` columns in the workbook.
- **Relative strength (vs SPY):** leaders `PLTR 1.17, META 1.12, NVDA 1.07, MSFT 1.07, AAPL 1.05`;
  laggards `SPCX 0.77, IONQ 0.75, RKLB 0.75, RGTI 0.84`. High-beta ≠ outperformance — the quantum/space
  names had the biggest amplitude but the worst relative strength.

---

## 5. Per-name summary (sorted by average correlation with the rest of the watchlist)

`Avg_Corr_Watchlist` = how "market-like" the name is (high → moves with everything; low/negative → diversifier).

| Symbol | β(SPY) | R²(SPY) | ρ SPX | ρ NDX | Lean | Sector | ρ Sect | ρ VIX | ρ Breadth | AvgρWL | RS |
|---|---:|---:|---:|---:|:--:|:--:|---:|---:|---:|---:|---:|
| SPY | 1.00 | 1.00 | 0.99 | 0.89 | SPX | XLK | 0.82 | −0.89 | 0.46 | 0.37 | 1.00 |
| QQQ | 1.64 | 0.80 | 0.90 | 0.99 | NDX | XLK | 0.96 | −0.79 | 0.28 | 0.36 | 0.99 |
| IWM | 1.13 | 0.56 | 0.74 | 0.65 | SPX | XLI | 0.71 | −0.67 | 0.62 | 0.31 | 0.98 |
| IONQ | 4.15 | 0.34 | 0.59 | 0.68 | NDX | XLK | 0.69 | −0.53 | 0.29 | 0.30 | 0.75 |
| RGTI | 4.02 | 0.33 | 0.57 | 0.64 | NDX | XLK | 0.66 | −0.50 | 0.30 | 0.29 | 0.84 |
| RKLB | 4.24 | 0.32 | 0.56 | 0.63 | NDX | XLK | 0.63 | −0.51 | 0.28 | 0.28 | 0.75 |
| SMCI | 3.45 | 0.36 | 0.60 | 0.70 | NDX | XLK | 0.73 | −0.53 | 0.22 | 0.28 | 0.89 |
| MRVL | 4.57 | 0.39 | 0.63 | 0.79 | NDX | XLK | 0.83 | −0.55 | 0.18 | 0.28 | 0.88 |
| IREN | 5.05 | 0.28 | 0.54 | 0.62 | NDX | XLK | 0.62 | −0.46 | 0.26 | 0.27 | 0.82 |
| INTC | 4.11 | 0.36 | 0.61 | 0.78 | NDX | XLK | 0.81 | −0.53 | 0.17 | 0.27 | 0.91 |
| AMD | 3.96 | 0.37 | 0.62 | 0.81 | NDX | XLK | 0.84 | −0.54 | 0.11 | 0.27 | 0.94 |
| CRWV | 3.77 | 0.25 | 0.51 | 0.60 | NDX | XLK | 0.61 | −0.45 | 0.18 | 0.26 | 0.84 |
| NBIS | 5.24 | 0.24 | 0.49 | 0.59 | NDX | XLK | 0.61 | −0.43 | 0.25 | 0.26 | 0.84 |
| HOOD | 3.67 | 0.27 | 0.53 | 0.56 | NDX | XLK | 0.56 | −0.48 | 0.24 | 0.26 | 0.89 |
| WULF | 4.71 | 0.24 | 0.50 | 0.58 | NDX | XLK | 0.58 | −0.45 | 0.26 | 0.25 | 0.88 |
| MU | 4.40 | 0.36 | 0.60 | 0.78 | NDX | XLK | 0.80 | −0.52 | 0.15 | 0.25 | 0.97 |
| SOFI | 2.72 | 0.27 | 0.52 | 0.49 | SPX | XLK | 0.48 | −0.47 | 0.32 | 0.25 | 0.90 |
| TSLA | 2.12 | 0.30 | 0.55 | 0.60 | NDX | XLY | 0.55 | −0.45 | 0.17 | 0.24 | 0.92 |
| ALAB | 5.52 | 0.27 | 0.53 | 0.70 | NDX | XLK | 0.72 | −0.46 | 0.12 | 0.24 | 0.75 |
| QCOM | 2.84 | 0.28 | 0.54 | 0.66 | NDX | XLK | 0.68 | −0.46 | 0.18 | 0.24 | 0.97 |
| AVGO | 2.36 | 0.31 | 0.56 | 0.67 | NDX | XLK | 0.70 | −0.49 | 0.05 | 0.23 | 1.02 |
| DIA | 0.69 | 0.46 | 0.67 | 0.38 | SPX | XLI | 0.68 | −0.63 | 0.56 | 0.23 | 0.99 |
| DELL | 4.68 | 0.27 | 0.53 | 0.62 | NDX | XLK | 0.67 | −0.47 | 0.18 | 0.23 | 0.92 |
| NVDA | 2.11 | 0.33 | 0.58 | 0.67 | NDX | XLK | 0.71 | −0.51 | 0.07 | 0.21 | 1.07 |
| SPCX | 2.21 | 0.13 | 0.35 | 0.41 | NDX | XLK | 0.39 | −0.31 | 0.20 | 0.18 | 0.77 |
| PLTR | 2.16 | 0.17 | 0.41 | 0.38 | SPX | XLK | 0.36 | −0.36 | 0.17 | 0.18 | 1.17 |
| AAL | 1.60 | 0.13 | 0.37 | 0.28 | SPX | XLY | 0.43 | −0.33 | 0.28 | 0.15 | 0.95 |
| UBER | 0.84 | 0.05 | 0.22 | 0.15 | SPX | XLY | 0.32 | −0.21 | 0.16 | 0.09 | 0.93 |
| META | 1.31 | 0.09 | 0.29 | 0.25 | SPX | XLC | 0.73 | −0.28 | 0.08 | 0.09 | 1.12 |
| GOOGL | 1.04 | 0.11 | 0.33 | 0.24 | SPX | XLC | 0.57 | −0.32 | 0.03 | 0.07 | 0.98 |
| AMZN | 0.89 | 0.09 | 0.30 | 0.19 | SPX | XLY | 0.69 | −0.27 | 0.09 | 0.07 | 1.03 |
| BAC | 0.46 | 0.05 | 0.20 | 0.02 | SPX | XLF | 0.71 | −0.22 | 0.21 | 0.07 | 0.98 |
| JPM | 0.58 | 0.05 | 0.22 | 0.01 | SPX | XLF | 0.78 | −0.23 | 0.26 | 0.06 | 0.99 |
| MSFT | 0.66 | 0.06 | 0.23 | 0.10 | SPX | XLC | 0.38 | −0.20 | 0.08 | 0.06 | 1.07 |
| AAPL | 0.56 | 0.05 | 0.22 | 0.09 | SPX | XLY | 0.18 | −0.19 | 0.07 | 0.01 | 1.05 |
| NFLX | 0.09 | 0.00 | 0.02 | 0.01 | SPX | XLC | 0.40 | −0.03 | 0.01 | −0.00 | 1.03 |
| CMG | 0.09 | 0.00 | 0.02 | −0.06 | SPX | XLY | 0.30 | −0.05 | 0.16 | −0.01 | 0.93 |
| PFE | −0.11 | 0.00 | −0.05 | −0.22 | SPX | XLV | 0.68 | 0.03 | 0.21 | −0.05 | 1.02 |
| CMCSA | −0.38 | 0.01 | −0.12 | −0.27 | SPX | XLP | 0.54 | 0.11 | 0.09 | −0.09 | 0.99 |
| ABBV | −0.40 | 0.02 | −0.15 | −0.31 | SPX | XLV | 0.66 | 0.13 | 0.05 | −0.10 | 1.02 |
| XOM | −0.47 | 0.04 | −0.21 | −0.29 | SPX | XLE | 0.85 | 0.17 | 0.03 | −0.12 | 1.02 |
| T | −0.74 | 0.07 | −0.27 | −0.37 | SPX | XLP | 0.50 | 0.23 | −0.04 | −0.15 | 1.03 |
| WMT | −0.63 | 0.08 | −0.28 | −0.44 | SPX | XLP | 0.77 | 0.25 | −0.00 | −0.18 | 0.97 |

---

## 6. How to use this

- **Diversification / correlated risk:** cohort A names are near-interchangeable intraday — holding
  several is one concentrated NDX bet, not a spread. Pair them with cohort D/E (financials,
  staples, energy) for genuine offset.
- **Hedging:** hedge cohort A with QQQ/XLK (high R²); do **not** hedge mega-caps (C) or financials (D)
  with SPY — the market explains <12% of their intraday variance. Hedge them with their sector ETF.
- **Regime read:** if VIX is bid, expect the whole A block down together and D/E (positive VIX-ρ) up.
- **Breadth-gated logic:** confirm with IWM/DIA, not QQQ — breadth and the mega-cap index decoupled.

## 7. Caveats

- **Window-specific.** 10 sessions of one regime (NDX-led, narrow). Correlations are *not* stationary —
  a rotation would re-order cohorts C/D/E especially. Re-run periodically; consider a multi-window
  (1mo/3mo) pass to see which relationships are stable vs transient.
- **Intraday ≠ daily.** These 5-min relationships describe same-day co-movement and are the right lens
  for intraday hedging/pairs, but daily-horizon betas/correlations differ (esp. the β magnitudes — see §1).
- **Indexes have no volume**, so $SPX/$NDX/$VIX/$ADVN/$DECN contribute correlation only (no OBV/VWAP).
- **Small samples per name are fine** (770 obs), but the newest/thinnest tickers (SPCX, CRWV, NBIS,
  ALAB) have shorter real trading histories — their relationships may be less stable going forward.

## 8. Reproduce

```
# proxy must be running on :8100 (start schwab-proxy/schwab_proxy.py)
python docs/analysis/fetch_data.py   # pulls 10d × 5-min RTH for all 59 symbols -> prices.pkl
python docs/analysis/analyze.py      # computes everything -> docs/analysis/2026-07-21-*
```
Both scripts are archived next to this note in `docs/analysis/`. Paths inside them point at the
session scratchpad for the intermediate `prices.pkl`; edit `HERE`/`OUT` to relocate.

---

## 9. Multi-window daily pass — which relationships are stable? (added 2026-07-21)

The §1–8 result is one intraday snapshot. To separate **structural** relationships from
**regime-transient** ones, this pass recomputes everything on **daily close-to-close returns**
over three trailing windows — **1-month (21 d), 3-month (63 d), 6-month (126 d)** — on ~1 year of
daily history (all names have a full year except **SPCX**, ~5-week listing → 24 obs, flagged).

New deliverables: workbook sheets **`Daily_1mo` / `Daily_3mo` / `Daily_6mo`** (watchlist × references,
color-scaled) + **`Stability`** (per-name ρ_SPY and β_SPY across all horizons, sector consistency,
drift, sign-flip flag), and **`2026-07-21-stability-across-horizons.png`** (ρ vs SPY at
intraday / 1mo / 3mo / 6mo).

> **Read 1-month with caution.** 21 daily observations is a *thin* sample (SE on ρ ≈ 0.22), so the
> `1mo` column swings wildly (e.g. MSFT −0.24, T −0.69, SMCI 0.05) and reverts by 3mo/6mo. Lean on
> **3mo/6mo** for structural conclusions; use 1mo only as a "current regime" read.

### What's STABLE (structural — trust it going forward)

- **Sector membership.** The dominant-sector mapping is identical intraday vs daily-3mo for **84%**
  of names, and the mismatches are all borderline dual-sector names (TSLA XLY↔XLK, SOFI, PLTR, UBER,
  CMG). **XOM→XLE, JPM/BAC→XLF, MSFT/META/GOOGL→XLC, PFE/ABBV→XLV, WMT/T→XLP, all semis→XLK** hold at
  every horizon. This is the most reliable relationship on the list — use it for sector hedging/pairs.
- **The semiconductor / NDX engine.** AMD, MRVL, MU, INTC, QCOM, IONQ, RGTI, AVGO, NVDA hold ρ_SPY
  ≈ 0.5–0.7 with **low drift** and a consistent XLK/NDX lean across intraday → 6mo. The high-beta tech
  block is a *durable* co-moving cluster, not a window artifact. Daily-6mo betas normalise toward
  conventional values (AMD 3.4, NVDA 1.9, QQQ 1.5).
- **Reference structure.** SPY–QQQ 0.92, SPY–$VIX −0.84, QQQ≈$NDX 1.00 at daily-6mo — the risk / vol
  axis is rock-solid at every horizon.

### What's REGIME-DEPENDENT (don't extrapolate)

- **Market (SPY) correlation is not stationary** — mean drift across 1mo→6mo is **0.22**. Least stable:
  **MSFT 0.56, UBER 0.54, SMCI 0.52, WULF 0.51, T 0.41**.
- **Mega-caps & financials are idiosyncratic intraday but market-correlated over months.** They warm
  from left to right in the stability map: **MSFT 0.11→0.32, AAPL 0.19→0.39, JPM 0.16→0.42,
  BAC 0.15→0.42, META 0.33→0.50, AMZN 0.50→0.56** (3mo→6mo). Intraday you hedge them with their sector;
  for multi-week exposure they behave like market beta. (Cohort C from §3 is an **intraday** truth.)
- **The defensive/inverse cohort is a recent rotation, not a permanent inverse.** It is real and
  strongest at 3mo but mean-reverts toward zero by 6mo:

  | | intraday ρ_SPY | daily 3mo | daily 6mo |
  |---|---:|---:|---:|
  | XOM | −0.20 | **−0.45** | −0.39 |
  | T | −0.26 | −0.30 | −0.29 |
  | WMT | −0.28 | −0.20 | −0.02 |
  | ABBV | −0.14 | −0.28 | −0.03 |
  | CMCSA | −0.12 | −0.08 | −0.00 |
  | PFE | −0.05 | −0.04 | +0.12 |

  **Only XOM (energy) is persistently negative** — the energy-vs-tech axis is durable. The rest are a
  *last-few-months* risk-off rotation that fades over 6mo, so treat their inverse hedge as tactical.

- **Genuinely idiosyncratic at every horizon:** NFLX and CMG sit at ρ≈0 across all windows (their
  intraday-vs-daily "sign flip" is just noise around zero) — no index/sector reliably drives them.
- **Breadth re-converges over time.** The narrow-rally divergence (§2) is largely intraday: at
  daily-6mo, Δbreadth ρ with SPY rises to 0.59 (from 0.46 intraday) and with IWM to 0.70 — over months
  breadth and the index track more closely.

### Bottom line
Use the **daily-3mo/6mo** columns for position-level relationships (sector driver, market beta,
diversification) and the **§1–8 intraday** figures for same-day hedging/pairs. The semis→NDX/XLK block
and the sector mappings are the relationships to trust; the mega-cap idiosyncrasy and the defensive
inverse are horizon- and regime-specific.

---

## 10. Per-symbol swing signals, 1–8 weeks (added 2026-07-21)

> ### ⚠ SYSTEMATIC MODEL OUTPUT — NOT financial advice
> The signals below are transparent, rules-based outputs of mechanical models over price data (as of the
> 2026-07-20 close). They do **not** account for your financial situation, objectives, or risk tolerance,
> and are **not** a recommendation to buy or sell any security. I am not a licensed financial advisor.
> Signals are horizon-specific (≈1–8 weeks), decay around earnings/macro events, and must be paired with
> your own diligence and risk management.

Full per-symbol write-ups are in the companion file **[`2026-07-21-swing-per-symbol.md`](2026-07-21-swing-per-symbol.md)**
(a card for all 43 names) and the workbook's **`Swing_Signals`** sheet. Each name is scored on **two
independent lenses**:

1. **Factor model** — the app's *validated* 20-trading-day model (`swing_model.json`; OOS IC ≈ +0.037,
   thin & regime-dependent). Cross-sectional over the 39 stocks: **BUY** = top calibration band (hist.
   **+1.35%** avg 20-day excess return, 52% hit), **SELL** = bottom band (**−0.80%**, 43%), HOLD = middle.
   It is a **momentum + volatility-premium + mean-reversion** model — deliberately *not* a trend follower.
2. **Technical posture** — classical trend/momentum (Wilder's EMA-20/50/200 stack, MACD, RSI-14, ADX-14).

An **Agreement** flag marks Confirmed vs **CONFLICT** (opposite lenses → low confidence). Index ETFs get
posture only (a single-name cross-sectional model is invalid for a basket).

### The headline finding — the two lenses disagree almost everywhere right now
Factor signal (39 stocks): **10 BUY · 15 HOLD · 14 SELL**. Posture (all 43): **7 Bullish · 19 Neutral ·
17 Bearish**. Agreement: **3 Confirmed-bear · 9 Conflict · 27 Mixed · 0 Confirmed-bull.**

**There is not a single "buy" name where the model and the trend agree.** That is itself the signal:
after the mid-July pullback, the factor model wants to **buy the beaten-down high-β semiconductor/miner
complex** (MU, DELL, NBIS, INTC, AMD, MRVL, ALAB, WULF, IREN, RKLB — all down 6–39% on the month, so their
*posture* is Bearish/Neutral) on the strength of their 6–12-month momentum and the **`low_vol −0.34`**
weight (its single biggest factor rewards high volatility), while it **fades the names actually trending
up** — financials (BAC, JPM), healthcare (ABBV), energy (XOM), and MSFT. So:

| Bucket | Names | How to read it |
|---|---|---|
| **Confirmed bear** (model SELL + posture Bearish) | **TSLA, UBER, NFLX** | The only two-lens agreement — the clearest "avoid / weakest" names. |
| **Conflict — mean-reversion longs** (BUY vs Bearish) | **MRVL, ALAB, IREN, RKLB** (+ MU/NBIS/INTC/WULF near it) | Model bets on a bounce *against* a downtrend. Contrarian only; wait for a trend turn (MACD cross / reclaim EMA20). |
| **Conflict — faded uptrends** (SELL vs Bullish) | **BAC, JPM, ABBV, XOM, MSFT** | Technically healthy but the vol-premium model rates them low. Trend-followers and the model disagree — size accordingly. |
| **Posture-bullish, model-neutral** | **AAPL, META, PLTR** (Bullish posture, HOLD) | Trending up with no factor-model edge either way — the most "clean" longs by price action alone. |

**Caveat you must carry:** the model's edge leans on the `low_vol` inverted sign, which the app's own
CLAUDE.md flags as its key fragility — in a genuine risk-off it can invert (then "buy the high-β dip"
becomes exactly wrong). Where the lenses **conflict**, treat the signal as low-confidence. The most
defensible reads are the **Confirmed-bear** trio and the **sector/relationship context** from §1–9 (which
is far more stable than any 4-week directional call).

## 11. How often should this be run?

Different parts have different half-lives — match the cadence to what decays fastest:

| Layer | Refresh cadence | Why |
|---|---|---|
| **Swing signals (§10)** | **Weekly** (e.g. Sunday), plus **before acting on any name** and **around its earnings** | The model's horizon is 20 trading days (~4 wks); daily trend/RSI/MACD shift week-to-week. A weekly re-run keeps signals inside their useful life. |
| **Intraday correlation cohorts (§1–8)** | **Weekly**, and **re-pull before relying on a same-day hedge/pair** | Intraday co-movement re-clusters fast around earnings, opex, and VIX spikes (the narrow-breadth divergence was an intraday effect). |
| **Daily 3mo/6mo relationships & sector map (§9)** | **Monthly** (full re-run) | These are the stable layer — sector mappings and the semis→NDX block barely move; monthly catches genuine regime rotations. |
| **Event-driven, any layer** | **On demand** after an **FOMC / CPI, a VIX spike, monthly opex, or an earnings cluster** | Regime breaks invalidate correlations and betas immediately — don't wait for the calendar. |
| **The factor model itself** (`swing_model.json`) | **Re-fit ~quarterly** via `fit_swing_model.py` | The artifact is dated **2026-06-28**; the app warns when a fit is >60 days old. Signals inherit any staleness in the weights (esp. the fragile `low_vol` sign), so refit the model, not just the data. |

**Practical setup:** a **weekly automated run** (pull 10-day 5-min + 2-yr daily → regenerate the workbook +
this report + the per-symbol cards) is the right default, with **ad-hoc re-runs on event days** and a
**quarterly model re-fit**. The heavy relationship study (§1–9) only needs a monthly refresh; the swing
signals (§10) are the part that genuinely benefits from weekly cadence.

### Reproduce (swing layer)
```
# proxy on :8100
python docs/analysis/fetch_daily2y.py   # 2yr daily (for 12-mo momentum) -> daily2y.pkl
python docs/analysis/swing_signals.py   # -> Swing_Signals sheet + 2026-07-21-swing-per-symbol.md
```
