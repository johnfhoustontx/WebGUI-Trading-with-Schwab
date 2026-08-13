# Per-symbol swing signals (1–8 weeks) — 2026-07-21

> ### ⚠ SYSTEMATIC MODEL OUTPUT — NOT financial advice
> These are transparent, rules-based outputs of mechanical models over price data (as of the 2026-07-20 close). They do **not** account for your financial situation, objectives, or risk tolerance and are **not** a recommendation to buy or sell any security. I am not a licensed financial advisor. Signals are horizon-specific (≈1–8 weeks), decay quickly around earnings and macro events, and must be combined with your own diligence, position sizing, and risk management.

**Two independent lenses are shown per name — read them together:**

1. **Factor model** — the app's *validated* 20-trading-day model (`swing_model.json`, OOS IC ≈ +0.037, thin/regime-dependent). Cross-sectional over the 39 stocks; BUY = top calibration band (hist. **+1.35%** avg 20-day excess return, 52% hit), SELL = bottom band (**−0.80%**, 43% hit), HOLD = middle. It is a **momentum + volatility-premium + mean-reversion** model, *not* a trend follower.

2. **Technical posture** — classical trend/momentum (Wilder's EMA-20/50/200 stack, MACD, RSI-14, ADX-14).

**Agreement** flags where they confirm vs **CONFLICT** (opposite → treat as low-confidence). Index ETFs (SPY/QQQ/IWM/DIA) get posture only — a single-name cross-sectional factor model is invalid for a basket.

> **Why so many factor-model BUYs are beaten-down high-vol names:** the model's largest weight is `low_vol −0.34` (high-volatility names scored *positively* in the bull-ish fit period) plus 6–12-month momentum. So it mechanically favours high-β semis/miners that have sold off, and fades low-vol defensives/large-caps. The app's own CLAUDE.md flags this `low_vol` sign as the model's key fragility — it could invert in a risk-off regime. **Where the factor model says BUY but posture says Bearish, the model is betting on mean-reversion against the current downtrend — the lowest-confidence setups.**

## Master table (stocks by factor composite, ETFs last)

| # | Symbol | Factor | Posture | Agreement | Pctile | ExpFwd20d | Trend | RSI | MACD | ADX | 1mo% | β3mo | Driver |
|--:|---|:--:|:--:|:--:|--:|--:|:--:|--:|:--:|--:|--:|--:|:--:|
| 1 | **MU** | BUY | Neutral | Mixed | 100 | +1.3% | Mixed | 42 | Bear | 20 | -24 | 4.7 | XLK |
| 2 | **DELL** | BUY | Neutral | Mixed | 97 | +1.3% | Mixed | 45 | Bear | 23 | -7 | 2.8 | XLK |
| 3 | **NBIS** | BUY | Neutral | Mixed | 95 | +1.3% | Mixed | 39 | Bear | 16 | -36 | 3.4 | XLK |
| 4 | **MRVL** | BUY | Bearish | CONFLICT (low conf.) | 92 | +1.3% | Mixed | 38 | Bear | 25 | -37 | 5.0 | XLK |
| 5 | **INTC** | BUY | Neutral | Mixed | 90 | +1.3% | Mixed | 38 | Bear | 21 | -28 | 4.0 | XLK |
| 6 | **ALAB** | BUY | Bearish | CONFLICT (low conf.) | 87 | +1.3% | Mixed | 40 | Bear | 28 | -26 | 4.8 | XLK |
| 7 | **AMD** | BUY | Neutral | Mixed | 85 | +1.3% | Mixed | 48 | Bear | 18 | -6 | 4.9 | XLK |
| 8 | **WULF** | BUY | Neutral | Mixed | 82 | +1.3% | Mixed | 37 | Bear | 23 | -35 | 3.2 | XLK |
| 9 | **IREN** | BUY | Bearish | CONFLICT (low conf.) | 79 | +1.3% | Mixed | 43 | Bear | 33 | -33 | 4.1 | XLK |
| 10 | **RKLB** | BUY | Bearish | CONFLICT (low conf.) | 77 | +1.3% | Mixed | 30 | Bear | 29 | -39 | 4.2 | XLK |
| 11 | **QCOM** | HOLD | Bearish | Mixed | 74 | +0.1% | Mixed | 37 | Bear | 13 | -25 | 3.8 | XLK |
| 12 | **AAL** | HOLD | Bearish | Mixed | 72 | +0.1% | Mixed | 41 | Bear | 27 | -5 | 1.7 | XLY |
| 13 | **RGTI** | HOLD | Bearish | Mixed | 69 | +0.1% | Bear | 33 | Bear | 28 | -33 | 4.8 | XLK |
| 14 | **SMCI** | HOLD | Bearish | Mixed | 67 | +0.1% | Bear | 34 | Bear | 25 | -22 | 5.3 | XLK |
| 15 | **HOOD** | HOLD | Bearish | Mixed | 64 | +0.1% | Mixed | 46 | Bear | 25 | -8 | 2.3 | XLK |
| 16 | **IONQ** | HOLD | Bearish | Mixed | 62 | +0.1% | Mixed | 26 | Bear | 34 | -39 | 4.4 | XLK |
| 17 | **SPCX*** | HOLD | Bearish | Mixed | 59 | +0.1% | Bear | 65 | Bear | nan | -35 | 4.2 | XLY |
| 18 | **CRWV** | HOLD | Bearish | Mixed | 56 | -0.2% | Bear | 32 | Bear | 23 | -38 | 2.4 | XLK |
| 19 | **AVGO** | HOLD | Neutral | Mixed | 54 | -0.2% | Mixed | 47 | Bull | 17 | -8 | 2.4 | XLK |
| 20 | **GOOGL** | HOLD | Neutral | Mixed | 51 | -0.2% | Mixed | 46 | Bear | 12 | -4 | 1.5 | XLC |
| 21 | **AAPL** | HOLD | Bullish | Mixed | 49 | -0.2% | Bull | 64 | Bull | 27 | +10 | 0.4 | XLY |
| 22 | **SOFI** | HOLD | Bearish | Mixed | 46 | -0.2% | Mixed | 44 | Bear | 22 | -5 | 2.2 | XLY |
| 23 | **NVDA** | HOLD | Neutral | Mixed | 44 | -0.2% | Mixed | 49 | Bull | 14 | -4 | 2.1 | XLK |
| 24 | **META** | HOLD | Bullish | Mixed | 41 | -0.2% | Mixed | 57 | Bull | 17 | +12 | 1.2 | XLC |
| 25 | **PLTR** | HOLD | Neutral | Mixed | 38 | -0.2% | Mixed | 56 | Bull | 13 | +5 | 1.0 | XLC |
| 26 | **BAC** | SELL | Bullish | CONFLICT (low conf.) | 36 | -0.8% | Bull | 63 | Bull | 39 | +8 | 0.2 | XLF |
| 27 | **ABBV** | SELL | Bullish | CONFLICT (low conf.) | 33 | -0.8% | Bull | 63 | Bear | 33 | +17 | -0.6 | XLV |
| 28 | **JPM** | SELL | Bullish | CONFLICT (low conf.) | 31 | -0.8% | Bull | 58 | Bear | 21 | +4 | 0.3 | XLF |
| 29 | **XOM** | SELL | Bullish | CONFLICT (low conf.) | 28 | -0.8% | Mixed | 61 | Bull | 20 | +8 | -1.0 | XLE |
| 30 | **TSLA** | SELL | Bearish | Confirmed bear | 26 | -0.8% | Mixed | 39 | Bear | 12 | -8 | 2.7 | XLK |
| 31 | **AMZN** | SELL | Neutral | Mixed | 23 | -0.8% | Mixed | 54 | Bull | 22 | +2 | 1.1 | XLY |
| 32 | **UBER** | SELL | Bearish | Confirmed bear | 21 | -0.8% | Bear | 48 | Bear | 10 | +1 | 0.7 | XLC |
| 33 | **T** | SELL | Neutral | Mixed | 18 | -0.8% | Mixed | 49 | Bull | 36 | -0 | -0.7 | XLRE |
| 34 | **CMG** | SELL | Neutral | Mixed | 15 | -0.8% | Mixed | 48 | Bear | 17 | +2 | -0.2 | XLP |
| 35 | **NFLX** | SELL | Bearish | Confirmed bear | 13 | -0.8% | Bear | 29 | Bear | 34 | -13 | -0.0 | XLC |
| 36 | **MSFT** | SELL | Bullish | CONFLICT (low conf.) | 10 | -0.8% | Mixed | 56 | Bull | 14 | +6 | 0.3 | XLC |
| 37 | **WMT** | SELL | Neutral | Mixed | 8 | -0.8% | Mixed | 42 | Bull | 21 | -4 | -0.4 | XLP |
| 38 | **CMCSA** | SELL | Neutral | Mixed | 5 | -0.8% | Mixed | 50 | Bull | 25 | +6 | -0.3 | XLP |
| 39 | **PFE** | SELL | Neutral | Mixed | 3 | -0.8% | Mixed | 50 | Bull | 23 | -2 | -0.1 | XLV |
| 40 | **SPY** | n/a | Neutral | ETF — posture only | — | — | Mixed | 48 | Bear | 18 | -1 | 0.0 | SPY |
| 41 | **QQQ** | n/a | Bearish | ETF — posture only | — | — | Mixed | 42 | Bear | 18 | -6 | 1.8 | XLK |
| 42 | **IWM** | n/a | Neutral | ETF — posture only | — | — | Mixed | 47 | Bear | 12 | -1 | 1.1 | XLI |
| 43 | **DIA** | n/a | Neutral | ETF — posture only | — | — | Mixed | 47 | Bear | 18 | +0 | 0.7 | XLI |

`*` limited history (young listing; 12-mo momentum factor neutralised).

---

## Detailed cards

### 1. MU — Micron · Factor **BUY** / Posture **Neutral** · _Mixed_

- **Price / return** — $865.46 · 1w -12.0% · 1mo -23.7% · 3mo +93.0% · at 69% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 20); MACD bearish & improving; RSI 42. ATR 9.6%/day.
- **Factor model (BUY, 100th pctile)** — composite +1.84 → band 4 (exp +1.35% / 20d, 52% hit). Drivers: mom12-1 strong+ (+2.7z) · mom6-1 strong+ (+2.2z) · trend strong+ (+2.2z) · low-vol strong- (-1.2z) · RS-sector strong+ (+2.4z) · turnover ~0 (-0.1z).
- **Relationship / risk** — daily-3mo β(SPY) 4.7 · intraday market R² 0.36 · dominant driver **XLK** · 3mo corr vs SPY +0.57 · VIX corr -0.52 (risk-on).

### 2. DELL — Dell · Factor **BUY** / Posture **Neutral** · _Mixed_

- **Price / return** — $381.88 · 1w -16.5% · 1mo -6.7% · 3mo +87.0% · at 76% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 23); MACD bearish & weakening; RSI 45. ATR 8.3%/day.
- **Factor model (BUY, 97th pctile)** — composite +1.35 → band 4 (exp +1.35% / 20d, 52% hit). Drivers: mom12-1 + (+0.6z) · mom6-1 strong+ (+2.6z) · trend strong+ (+2.2z) · low-vol - (-0.9z) · RS-sector strong+ (+2.5z) · turnover ~0 (-0.1z).
- **Relationship / risk** — daily-3mo β(SPY) 2.8 · intraday market R² 0.27 · dominant driver **XLK** · 3mo corr vs SPY +0.37 · VIX corr -0.47 (risk-on).

### 3. NBIS — Nebius · Factor **BUY** / Posture **Neutral** · _Mixed_

- **Price / return** — $182.62 · 1w -5.9% · 1mo -36.3% · 3mo +14.7% · at 56% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 16); MACD bearish & improving; RSI 39. ATR 13.2%/day.
- **Factor model (BUY, 95th pctile)** — composite +1.35 → band 4 (exp +1.35% / 20d, 52% hit). Drivers: mom12-1 strong+ (+1.8z) · mom6-1 strong+ (+1.6z) · trend strong+ (+1.0z) · low-vol strong- (-1.4z) · RS-sector ~0 (+0.1z) · turnover strong+ (+1.1z).
- **Relationship / risk** — daily-3mo β(SPY) 3.4 · intraday market R² 0.24 · dominant driver **XLK** · 3mo corr vs SPY +0.38 · VIX corr -0.43 (risk-on).

### 4. MRVL — Marvell · Factor **BUY** / Posture **Bearish** · _CONFLICT (low conf.)_

- **Price / return** — $194.94 · 1w -12.4% · 1mo -37.2% · 3mo +31.9% · at 52% of the 52-wk range
- **Technical posture (Bearish)** — No clean trend (EMAs entangled); strong trend (ADX 25); MACD bearish & improving; RSI 38. ATR 11.2%/day.
- **Factor model (BUY, 92th pctile)** — composite +1.33 → band 4 (exp +1.35% / 20d, 52% hit). Drivers: mom12-1 strong+ (+1.0z) · mom6-1 strong+ (+2.7z) · trend strong+ (+1.3z) · low-vol strong- (-1.4z) · RS-sector + (+0.8z) · turnover strong- (-1.0z).
- **⚠ Conflict** — factor model (BUY) opposes the bearish price trend; this is a mean-reversion bet against momentum. Low confidence — wait for trend confirmation or treat as contrarian only.
- **Relationship / risk** — daily-3mo β(SPY) 5.0 · intraday market R² 0.39 · dominant driver **XLK** · 3mo corr vs SPY +0.57 · VIX corr -0.55 (risk-on).

### 5. INTC — Intel · Factor **BUY** / Posture **Neutral** · _Mixed_

- **Price / return** — $97.06 · 1w -9.9% · 1mo -27.6% · 3mo +47.7% · at 64% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 21); MACD bearish & improving; RSI 38. ATR 9.2%/day.
- **Factor model (BUY, 90th pctile)** — composite +1.31 → band 4 (exp +1.35% / 20d, 52% hit). Drivers: mom12-1 strong+ (+2.0z) · mom6-1 strong+ (+1.9z) · trend strong+ (+1.4z) · low-vol strong- (-1.0z) · RS-sector + (+0.9z) · turnover - (-0.5z).
- **Relationship / risk** — daily-3mo β(SPY) 4.0 · intraday market R² 0.36 · dominant driver **XLK** · 3mo corr vs SPY +0.52 · VIX corr -0.53 (risk-on).

### 6. ALAB — Astera Labs · Factor **BUY** / Posture **Bearish** · _CONFLICT (low conf.)_

- **Price / return** — $309.09 · 1w -14.6% · 1mo -25.9% · 3mo +75.8% · at 55% of the 52-wk range
- **Technical posture (Bearish)** — No clean trend (EMAs entangled); strong trend (ADX 28); MACD bearish & weakening; RSI 40. ATR 12.4%/day.
- **Factor model (BUY, 87th pctile)** — composite +1.20 → band 4 (exp +1.35% / 20d, 52% hit). Drivers: mom12-1 strong+ (+1.1z) · mom6-1 strong+ (+1.2z) · trend strong+ (+1.6z) · low-vol strong- (-1.4z) · RS-sector strong+ (+2.0z) · turnover - (-0.8z).
- **⚠ Conflict** — factor model (BUY) opposes the bearish price trend; this is a mean-reversion bet against momentum. Low confidence — wait for trend confirmation or treat as contrarian only.
- **Relationship / risk** — daily-3mo β(SPY) 4.8 · intraday market R² 0.27 · dominant driver **XLK** · 3mo corr vs SPY +0.56 · VIX corr -0.46 (risk-on).

### 7. AMD — AMD · Factor **BUY** / Posture **Neutral** · _Mixed_

- **Price / return** — $503.57 · 1w -8.1% · 1mo -6.3% · 3mo +83.1% · at 82% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 18); MACD bearish & weakening; RSI 48. ATR 7.3%/day.
- **Factor model (BUY, 85th pctile)** — composite +0.99 → band 4 (exp +1.35% / 20d, 52% hit). Drivers: mom12-1 + (+0.9z) · mom6-1 strong+ (+1.2z) · trend strong+ (+2.0z) · low-vol - (-0.6z) · RS-sector strong+ (+2.1z) · turnover - (-0.7z).
- **Relationship / risk** — daily-3mo β(SPY) 4.9 · intraday market R² 0.37 · dominant driver **XLK** · 3mo corr vs SPY +0.74 · VIX corr -0.54 (risk-on).

### 8. WULF — TeraWulf · Factor **BUY** / Posture **Neutral** · _Mixed_

- **Price / return** — $18.86 · 1w -2.8% · 1mo -34.9% · 3mo -8.0% · at 58% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 23); MACD bearish & improving; RSI 37. ATR 11.3%/day.
- **Factor model (BUY, 82th pctile)** — composite +0.96 → band 4 (exp +1.35% / 20d, 52% hit). Drivers: mom12-1 strong+ (+2.4z) · mom6-1 + (+0.9z) · trend ~0 (+0.3z) · low-vol - (-0.6z) · RS-sector - (-0.7z) · turnover strong+ (+1.4z).
- **Relationship / risk** — daily-3mo β(SPY) 3.2 · intraday market R² 0.24 · dominant driver **XLK** · 3mo corr vs SPY +0.49 · VIX corr -0.45 (risk-on).

### 9. IREN — IREN · Factor **BUY** / Posture **Bearish** · _CONFLICT (low conf.)_

- **Price / return** — $40.20 · 1w +4.2% · 1mo -33.0% · 3mo -17.5% · at 41% of the 52-wk range
- **Technical posture (Bearish)** — No clean trend (EMAs entangled); strong trend (ADX 33); MACD bearish & improving; RSI 43. ATR 11.1%/day.
- **Factor model (BUY, 79th pctile)** — composite +0.70 → band 4 (exp +1.35% / 20d, 52% hit). Drivers: mom12-1 strong+ (+1.9z) · mom6-1 - (-0.4z) · trend - (-0.5z) · low-vol strong- (-1.4z) · RS-sector - (-1.0z) · turnover + (+0.6z).
- **⚠ Conflict** — factor model (BUY) opposes the bearish price trend; this is a mean-reversion bet against momentum. Low confidence — wait for trend confirmation or treat as contrarian only.
- **Relationship / risk** — daily-3mo β(SPY) 4.1 · intraday market R² 0.28 · dominant driver **XLK** · 3mo corr vs SPY +0.47 · VIX corr -0.46 (risk-on).

### 10. RKLB — Rocket Lab · Factor **BUY** / Posture **Bearish** · _CONFLICT (low conf.)_

- **Price / return** — $65.74 · 1w -16.6% · 1mo -38.7% · 3mo -26.5% · at 24% of the 52-wk range
- **Technical posture (Bearish)** — No clean trend (EMAs entangled); strong trend (ADX 29); MACD bearish & improving; RSI 30. ATR 12.0%/day.
- **Factor model (BUY, 77th pctile)** — composite +0.45 → band 4 (exp +1.35% / 20d, 52% hit). Drivers: mom12-1 + (+0.9z) · mom6-1 - (-0.3z) · trend - (-0.6z) · low-vol strong- (-1.6z) · RS-sector strong- (-1.2z) · turnover - (-0.6z).
- **⚠ Conflict** — factor model (BUY) opposes the bearish price trend; this is a mean-reversion bet against momentum. Low confidence — wait for trend confirmation or treat as contrarian only.
- **Relationship / risk** — daily-3mo β(SPY) 4.2 · intraday market R² 0.32 · dominant driver **XLK** · 3mo corr vs SPY +0.46 · VIX corr -0.51 (risk-on).

### 11. QCOM — Qualcomm · Factor **HOLD** / Posture **Bearish** · _Mixed_

- **Price / return** — $170.32 · 1w -4.4% · 1mo -24.7% · 3mo +23.9% · at 36% of the 52-wk range
- **Technical posture (Bearish)** — No clean trend (EMAs entangled); weak trend (ADX 13); MACD bearish & improving; RSI 37. ATR 6.8%/day.
- **Factor model (HOLD, 74th pctile)** — composite +0.08 → band 3 (exp +0.12% / 20d, 49% hit). Drivers: mom12-1 - (-0.4z) · mom6-1 ~0 (+0.1z) · trend ~0 (-0.2z) · low-vol - (-0.7z) · RS-sector + (+0.3z) · turnover strong- (-1.4z).
- **Relationship / risk** — daily-3mo β(SPY) 3.8 · intraday market R² 0.28 · dominant driver **XLK** · 3mo corr vs SPY +0.56 · VIX corr -0.46 (risk-on).

### 12. AAL — American Airlines · Factor **HOLD** / Posture **Bearish** · _Mixed_

- **Price / return** — $15.14 · 1w -3.4% · 1mo -5.3% · 3mo +23.7% · at 62% of the 52-wk range
- **Technical posture (Bearish)** — No clean trend (EMAs entangled); strong trend (ADX 27); MACD bearish & improving; RSI 41. ATR 4.6%/day.
- **Factor model (HOLD, 72th pctile)** — composite -0.01 → band 2 (exp +0.12% / 20d, 49% hit). Drivers: mom12-1 - (-0.4z) · mom6-1 - (-0.4z) · trend ~0 (+0.3z) · low-vol + (+0.4z) · RS-sector + (+0.7z) · turnover strong+ (+2.7z).
- **Relationship / risk** — daily-3mo β(SPY) 1.7 · intraday market R² 0.13 · dominant driver **XLY** · 3mo corr vs SPY +0.45 · VIX corr -0.33 (risk-on).

### 13. RGTI — Rigetti · Factor **HOLD** / Posture **Bearish** · _Mixed_

- **Price / return** — $14.25 · 1w -11.5% · 1mo -33.3% · 3mo -27.4% · at 3% of the 52-wk range
- **Technical posture (Bearish)** — Downtrend (price below falling EMAs); strong trend (ADX 28); MACD bearish & improving; RSI 33. ATR 10.5%/day.
- **Factor model (HOLD, 69th pctile)** — composite -0.04 → band 2 (exp +0.12% / 20d, 49% hit). Drivers: mom12-1 ~0 (-0.2z) · mom6-1 - (-0.7z) · trend strong- (-1.4z) · low-vol strong- (-1.5z) · RS-sector strong- (-1.4z) · turnover strong- (-1.4z).
- **Relationship / risk** — daily-3mo β(SPY) 4.8 · intraday market R² 0.33 · dominant driver **XLK** · 3mo corr vs SPY +0.54 · VIX corr -0.50 (risk-on).

### 14. SMCI — Super Micro · Factor **HOLD** / Posture **Bearish** · _Mixed_

- **Price / return** — $23.83 · 1w -13.8% · 1mo -22.3% · 3mo -17.3% · at 8% of the 52-wk range
- **Technical posture (Bearish)** — Downtrend (price below falling EMAs); strong trend (ADX 25); MACD bearish & weakening; RSI 34. ATR 9.0%/day.
- **Factor model (HOLD, 67th pctile)** — composite -0.07 → band 2 (exp +0.12% / 20d, 49% hit). Drivers: mom12-1 - (-0.8z) · mom6-1 - (-0.5z) · trend strong- (-1.5z) · low-vol strong- (-1.6z) · RS-sector - (-1.0z) · turnover strong- (-1.4z).
- **Relationship / risk** — daily-3mo β(SPY) 5.3 · intraday market R² 0.36 · dominant driver **XLK** · 3mo corr vs SPY +0.57 · VIX corr -0.53 (risk-on).

### 15. HOOD — Robinhood · Factor **HOLD** / Posture **Bearish** · _Mixed_

- **Price / return** — $99.28 · 1w -12.5% · 1mo -8.2% · 3mo +8.8% · at 39% of the 52-wk range
- **Technical posture (Bearish)** — No clean trend (EMAs entangled); strong trend (ADX 25); MACD bearish & weakening; RSI 46. ATR 6.7%/day.
- **Factor model (HOLD, 64th pctile)** — composite -0.09 → band 2 (exp +0.12% / 20d, 49% hit). Drivers: mom12-1 - (-0.4z) · mom6-1 - (-0.5z) · trend ~0 (+0.1z) · low-vol - (-0.3z) · RS-sector ~0 (-0.2z) · turnover - (-0.5z).
- **Relationship / risk** — daily-3mo β(SPY) 2.3 · intraday market R² 0.27 · dominant driver **XLK** · 3mo corr vs SPY +0.39 · VIX corr -0.48 (risk-on).

### 16. IONQ — IonQ · Factor **HOLD** / Posture **Bearish** · _Mixed_

- **Price / return** — $34.24 · 1w -12.9% · 1mo -39.5% · 3mo -29.1% · at 14% of the 52-wk range
- **Technical posture (Bearish)** — No clean trend (EMAs entangled); strong trend (ADX 34); MACD bearish & improving; RSI 26 (oversold); stretched -2.7 ATR vs EMA20 — bounce risk. ATR 11.1%/day.
- **Factor model (HOLD, 62th pctile)** — composite -0.13 → band 2 (exp +0.12% / 20d, 49% hit). Drivers: mom12-1 - (-0.4z) · mom6-1 - (-0.3z) · trend strong- (-1.2z) · low-vol - (-0.9z) · RS-sector strong- (-1.3z) · turnover - (-0.8z).
- **Relationship / risk** — daily-3mo β(SPY) 4.4 · intraday market R² 0.34 · dominant driver **XLK** · 3mo corr vs SPY +0.59 · VIX corr -0.53 (risk-on).

### 17. SPCX — SPAC/SPCX · Factor **HOLD** / Posture **Bearish** · _Mixed_ · ⚠ limited history

- **Price / return** — $119.85 · 1w -11.9% · 1mo -35.2% · 3mo +nan% · at 0% of the 52-wk range
- **Technical posture (Bearish)** — Downtrend (price below falling EMAs); weak trend (ADX nan); MACD bearish & weakening; RSI 65. ATR 11.3%/day.
- **Factor model (HOLD, 59th pctile)** — composite -0.20 → band 2 (exp +0.12% / 20d, 49% hit). Drivers: mom12-1 n/a (+nanz) · mom6-1 n/a (+nanz) · trend strong- (-1.7z) · low-vol n/a (+nanz) · RS-sector n/a (+nanz) · turnover n/a (+nanz).
- **Relationship / risk** — daily-3mo β(SPY) 4.2 · intraday market R² 0.13 · dominant driver **XLY** · 3mo corr vs SPY +0.52 · VIX corr -0.31 (risk-on).

### 18. CRWV — CoreWeave · Factor **HOLD** / Posture **Bearish** · _Mixed_

- **Price / return** — $73.06 · 1w -8.6% · 1mo -38.1% · 3mo -37.8% · at 10% of the 52-wk range
- **Technical posture (Bearish)** — Downtrend (price below falling EMAs); weak trend (ADX 23); MACD bearish & improving; RSI 32. ATR 9.9%/day.
- **Factor model (HOLD, 56th pctile)** — composite -0.26 → band 1 (exp -0.20% / 20d, 47% hit). Drivers: mom12-1 - (-0.8z) · mom6-1 ~0 (-0.3z) · trend strong- (-1.2z) · low-vol - (-0.6z) · RS-sector strong- (-1.4z) · turnover ~0 (+0.0z).
- **Relationship / risk** — daily-3mo β(SPY) 2.4 · intraday market R² 0.25 · dominant driver **XLK** · 3mo corr vs SPY +0.36 · VIX corr -0.45 (risk-on).

### 19. AVGO — Broadcom · Factor **HOLD** / Posture **Neutral** · _Mixed_

- **Price / return** — $378.16 · 1w -2.8% · 1mo -8.1% · 3mo -5.4% · at 49% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 17); MACD bullish & weakening; RSI 47. ATR 4.5%/day.
- **Factor model (HOLD, 54th pctile)** — composite -0.29 → band 1 (exp -0.20% / 20d, 47% hit). Drivers: mom12-1 - (-0.3z) · mom6-1 ~0 (-0.3z) · trend ~0 (+0.1z) · low-vol ~0 (+0.3z) · RS-sector - (-0.7z) · turnover - (-0.7z).
- **Relationship / risk** — daily-3mo β(SPY) 2.4 · intraday market R² 0.31 · dominant driver **XLK** · 3mo corr vs SPY +0.55 · VIX corr -0.49 (risk-on).

### 20. GOOGL — Alphabet · Factor **HOLD** / Posture **Neutral** · _Mixed_

- **Price / return** — $351.99 · 1w -2.1% · 1mo -4.4% · 3mo +4.3% · at 77% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 12); MACD bearish & weakening; RSI 46. ATR 3.3%/day.
- **Factor model (HOLD, 51th pctile)** — composite -0.30 → band 1 (exp -0.20% / 20d, 47% hit). Drivers: mom12-1 ~0 (-0.1z) · mom6-1 - (-0.3z) · trend ~0 (+0.3z) · low-vol + (+0.8z) · RS-sector + (+0.3z) · turnover ~0 (-0.1z).
- **Relationship / risk** — daily-3mo β(SPY) 1.5 · intraday market R² 0.11 · dominant driver **XLC** · 3mo corr vs SPY +0.52 · VIX corr -0.32 (risk-on).

### 21. AAPL — Apple · Factor **HOLD** / Posture **Bullish** · _Mixed_

- **Price / return** — $326.59 · 1w +3.7% · 1mo +9.6% · 3mo +19.6% · at 95% of the 52-wk range
- **Technical posture (Bullish)** — Uptrend (price above rising EMAs); strong trend (ADX 27); MACD bullish & weakening; RSI 64. ATR 2.5%/day.
- **Factor model (HOLD, 49th pctile)** — composite -0.32 → band 1 (exp -0.20% / 20d, 47% hit). Drivers: mom12-1 - (-0.4z) · mom6-1 ~0 (-0.3z) · trend + (+0.6z) · low-vol strong+ (+1.0z) · RS-sector + (+0.8z) · turnover + (+0.4z).
- **Relationship / risk** — daily-3mo β(SPY) 0.4 · intraday market R² 0.05 · dominant driver **XLY** · 3mo corr vs SPY +0.19 · VIX corr -0.19 (risk-on).

### 22. SOFI — SoFi · Factor **HOLD** / Posture **Bearish** · _Mixed_

- **Price / return** — $17.01 · 1w -8.3% · 1mo -5.0% · 3mo -12.8% · at 11% of the 52-wk range
- **Technical posture (Bearish)** — No clean trend (EMAs entangled); weak trend (ADX 22); MACD bearish & weakening; RSI 44. ATR 5.4%/day.
- **Factor model (HOLD, 46th pctile)** — composite -0.36 → band 1 (exp -0.20% / 20d, 47% hit). Drivers: mom12-1 - (-0.5z) · mom6-1 - (-0.8z) · trend - (-0.7z) · low-vol ~0 (+0.1z) · RS-sector ~0 (-0.3z) · turnover + (+0.5z).
- **Relationship / risk** — daily-3mo β(SPY) 2.2 · intraday market R² 0.27 · dominant driver **XLY** · 3mo corr vs SPY +0.46 · VIX corr -0.47 (risk-on).

### 23. NVDA — Nvidia · Factor **HOLD** / Posture **Neutral** · _Mixed_

- **Price / return** — $203.28 · 1w -4.0% · 1mo -3.5% · 3mo +0.6% · at 54% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 14); MACD bullish & weakening; RSI 49. ATR 3.6%/day.
- **Factor model (HOLD, 44th pctile)** — composite -0.42 → band 1 (exp -0.20% / 20d, 47% hit). Drivers: mom12-1 - (-0.4z) · mom6-1 - (-0.3z) · trend ~0 (+0.1z) · low-vol + (+0.6z) · RS-sector - (-0.4z) · turnover - (-0.5z).
- **Relationship / risk** — daily-3mo β(SPY) 2.1 · intraday market R² 0.33 · dominant driver **XLK** · 3mo corr vs SPY +0.64 · VIX corr -0.51 (risk-on).

### 24. META — Meta · Factor **HOLD** / Posture **Bullish** · _Mixed_

- **Price / return** — $645.85 · 1w -2.3% · 1mo +11.9% · 3mo -3.7% · at 45% of the 52-wk range
- **Technical posture (Bullish)** — No clean trend (EMAs entangled); weak trend (ADX 17); MACD bullish & weakening; RSI 57. ATR 4.0%/day.
- **Factor model (HOLD, 41th pctile)** — composite -0.44 → band 1 (exp -0.20% / 20d, 47% hit). Drivers: mom12-1 - (-0.7z) · mom6-1 - (-0.6z) · trend ~0 (-0.1z) · low-vol + (+0.5z) · RS-sector ~0 (+0.0z) · turnover ~0 (+0.1z).
- **Relationship / risk** — daily-3mo β(SPY) 1.2 · intraday market R² 0.09 · dominant driver **XLC** · 3mo corr vs SPY +0.33 · VIX corr -0.28 (risk-on).

### 25. PLTR — Palantir · Factor **HOLD** / Posture **Neutral** · _Mixed_

- **Price / return** — $134.85 · 1w +0.8% · 1mo +5.0% · 3mo -7.6% · at 28% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 13); MACD bullish & weakening; RSI 56. ATR 5.0%/day.
- **Factor model (HOLD, 38th pctile)** — composite -0.45 → band 1 (exp -0.20% / 20d, 47% hit). Drivers: mom12-1 - (-0.7z) · mom6-1 - (-0.8z) · trend - (-0.4z) · low-vol ~0 (+0.3z) · RS-sector ~0 (-0.0z) · turnover - (-0.5z).
- **Relationship / risk** — daily-3mo β(SPY) 1.0 · intraday market R² 0.17 · dominant driver **XLC** · 3mo corr vs SPY +0.24 · VIX corr -0.36 (risk-on).

### 26. BAC — Bank of America · Factor **SELL** / Posture **Bullish** · _CONFLICT (low conf.)_

- **Price / return** — $60.42 · 1w -0.3% · 1mo +7.5% · 3mo +12.0% · at 93% of the 52-wk range
- **Technical posture (Bullish)** — Uptrend (price above rising EMAs); strong trend (ADX 39); MACD bullish & weakening; RSI 63. ATR 2.1%/day.
- **Factor model (SELL, 36th pctile)** — composite -0.48 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.5z) · mom6-1 - (-0.4z) · trend + (+0.4z) · low-vol strong+ (+1.3z) · RS-sector ~0 (+0.2z) · turnover + (+0.9z).
- **⚠ Conflict** — factor model (SELL) opposes the bullish price trend; this is a mean-reversion bet against momentum. Low confidence — wait for trend confirmation or treat as contrarian only.
- **Relationship / risk** — daily-3mo β(SPY) 0.2 · intraday market R² 0.05 · dominant driver **XLF** · 3mo corr vs SPY +0.15 · VIX corr -0.22 (risk-on).

### 27. ABBV — AbbVie · Factor **SELL** / Posture **Bullish** · _CONFLICT (low conf.)_

- **Price / return** — $253.38 · 1w +3.5% · 1mo +17.0% · 3mo +24.4% · at 90% of the 52-wk range
- **Technical posture (Bullish)** — Uptrend (price above rising EMAs); strong trend (ADX 33); MACD bearish & improving; RSI 63. ATR 2.5%/day.
- **Factor model (SELL, 33th pctile)** — composite -0.48 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.5z) · mom6-1 - (-0.5z) · trend + (+0.4z) · low-vol strong+ (+1.0z) · RS-sector + (+0.5z) · turnover - (-0.4z).
- **⚠ Conflict** — factor model (SELL) opposes the bullish price trend; this is a mean-reversion bet against momentum. Low confidence — wait for trend confirmation or treat as contrarian only.
- **Relationship / risk** — daily-3mo β(SPY) -0.6 · intraday market R² 0.02 · dominant driver **XLV** · 3mo corr vs SPY -0.28 · VIX corr +0.13 (defensive/inverse).

### 28. JPM — JPMorgan · Factor **SELL** / Posture **Bullish** · _CONFLICT (low conf.)_

- **Price / return** — $338.87 · 1w -1.2% · 1mo +4.2% · 3mo +6.9% · at 87% of the 52-wk range
- **Technical posture (Bullish)** — Uptrend (price above rising EMAs); weak trend (ADX 21); MACD bearish & weakening; RSI 58. ATR 2.3%/day.
- **Factor model (SELL, 31th pctile)** — composite -0.50 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.5z) · mom6-1 - (-0.4z) · trend ~0 (+0.3z) · low-vol strong+ (+1.2z) · RS-sector ~0 (+0.1z) · turnover + (+1.0z).
- **⚠ Conflict** — factor model (SELL) opposes the bullish price trend; this is a mean-reversion bet against momentum. Low confidence — wait for trend confirmation or treat as contrarian only.
- **Relationship / risk** — daily-3mo β(SPY) 0.3 · intraday market R² 0.05 · dominant driver **XLF** · 3mo corr vs SPY +0.16 · VIX corr -0.23 (risk-on).

### 29. XOM — Exxon · Factor **SELL** / Posture **Bullish** · _CONFLICT (low conf.)_

- **Price / return** — $148.36 · 1w +2.3% · 1mo +7.7% · 3mo +0.5% · at 65% of the 52-wk range
- **Technical posture (Bullish)** — No clean trend (EMAs entangled); weak trend (ADX 20); MACD bullish & improving; RSI 61. ATR 2.2%/day.
- **Factor model (SELL, 28th pctile)** — composite -0.54 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.5z) · mom6-1 - (-0.4z) · trend ~0 (+0.1z) · low-vol strong+ (+1.0z) · RS-sector ~0 (-0.1z) · turnover ~0 (-0.2z).
- **⚠ Conflict** — factor model (SELL) opposes the bullish price trend; this is a mean-reversion bet against momentum. Low confidence — wait for trend confirmation or treat as contrarian only.
- **Relationship / risk** — daily-3mo β(SPY) -1.0 · intraday market R² 0.04 · dominant driver **XLE** · 3mo corr vs SPY -0.45 · VIX corr +0.17 (defensive/inverse).

### 30. TSLA — Tesla · Factor **SELL** / Posture **Bearish** · _Confirmed bear_

- **Price / return** — $369.57 · 1w -6.7% · 1mo -7.7% · 3mo -5.8% · at 36% of the 52-wk range
- **Technical posture (Bearish)** — No clean trend (EMAs entangled); weak trend (ADX 12); MACD bearish & weakening; RSI 39. ATR 4.6%/day.
- **Factor model (SELL, 26th pctile)** — composite -0.55 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.5z) · mom6-1 - (-0.6z) · trend - (-0.5z) · low-vol + (+0.4z) · RS-sector - (-0.7z) · turnover strong- (-1.2z).
- **Relationship / risk** — daily-3mo β(SPY) 2.7 · intraday market R² 0.30 · dominant driver **XLK** · 3mo corr vs SPY +0.70 · VIX corr -0.45 (risk-on).

### 31. AMZN — Amazon · Factor **SELL** / Posture **Neutral** · _Mixed_

- **Price / return** — $249.99 · 1w +1.0% · 1mo +2.3% · 3mo +0.7% · at 67% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 22); MACD bullish & weakening; RSI 54. ATR 3.0%/day.
- **Factor model (SELL, 23th pctile)** — composite -0.56 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.6z) · mom6-1 - (-0.4z) · trend ~0 (+0.1z) · low-vol strong+ (+1.0z) · RS-sector ~0 (+0.1z) · turnover - (-0.5z).
- **Relationship / risk** — daily-3mo β(SPY) 1.1 · intraday market R² 0.09 · dominant driver **XLY** · 3mo corr vs SPY +0.50 · VIX corr -0.27 (risk-on).

### 32. UBER — Uber · Factor **SELL** / Posture **Bearish** · _Confirmed bear_

- **Price / return** — $72.17 · 1w +0.1% · 1mo +0.7% · 3mo -6.9% · at 11% of the 52-wk range
- **Technical posture (Bearish)** — Downtrend (price below falling EMAs); weak trend (ADX 10); MACD bearish & weakening; RSI 48. ATR 3.4%/day.
- **Factor model (SELL, 21th pctile)** — composite -0.59 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.7z) · mom6-1 - (-0.7z) · trend - (-0.5z) · low-vol + (+0.7z) · RS-sector ~0 (+0.0z) · turnover ~0 (-0.2z).
- **Relationship / risk** — daily-3mo β(SPY) 0.7 · intraday market R² 0.05 · dominant driver **XLC** · 3mo corr vs SPY +0.25 · VIX corr -0.21 (risk-on).

### 33. T — AT&T · Factor **SELL** / Posture **Neutral** · _Mixed_

- **Price / return** — $21.95 · 1w +3.1% · 1mo -0.3% · 3mo -16.2% · at 16% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); strong trend (ADX 36); MACD bullish & improving; RSI 49. ATR 3.0%/day.
- **Factor model (SELL, 18th pctile)** — composite -0.61 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.7z) · mom6-1 - (-0.6z) · trend - (-0.7z) · low-vol strong+ (+1.0z) · RS-sector - (-0.6z) · turnover strong+ (+1.9z).
- **Relationship / risk** — daily-3mo β(SPY) -0.7 · intraday market R² 0.07 · dominant driver **XLRE** · 3mo corr vs SPY -0.30 · VIX corr +0.23 (defensive/inverse).

### 34. CMG — Chipotle · Factor **SELL** / Posture **Neutral** · _Mixed_

- **Price / return** — $33.13 · 1w -9.1% · 1mo +2.0% · 3mo -7.5% · at 19% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 17); MACD bearish & weakening; RSI 48. ATR 3.9%/day.
- **Factor model (SELL, 15th pctile)** — composite -0.61 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.8z) · mom6-1 - (-0.7z) · trend - (-0.6z) · low-vol + (+0.7z) · RS-sector - (-0.3z) · turnover ~0 (+0.2z).
- **Relationship / risk** — daily-3mo β(SPY) -0.2 · intraday market R² 0.00 · dominant driver **XLP** · 3mo corr vs SPY -0.05 · VIX corr -0.05 (neutral).

### 35. NFLX — Netflix · Factor **SELL** / Posture **Bearish** · _Confirmed bear_

- **Price / return** — $67.60 · 1w -8.1% · 1mo -12.6% · 3mo -28.7% · at 0% of the 52-wk range
- **Technical posture (Bearish)** — Downtrend (price below falling EMAs); strong trend (ADX 34); MACD bearish & weakening; RSI 29 (oversold). ATR 4.1%/day.
- **Factor model (SELL, 13th pctile)** — composite -0.63 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.8z) · mom6-1 - (-0.6z) · trend strong- (-1.4z) · low-vol + (+0.9z) · RS-sector - (-0.8z) · turnover strong+ (+2.7z).
- **Relationship / risk** — daily-3mo β(SPY) -0.0 · intraday market R² 0.00 · dominant driver **XLC** · 3mo corr vs SPY -0.01 · VIX corr -0.03 (neutral).

### 36. MSFT — Microsoft · Factor **SELL** / Posture **Bullish** · _CONFLICT (low conf.)_

- **Price / return** — $402.29 · 1w +4.5% · 1mo +6.0% · 3mo -3.8% · at 26% of the 52-wk range
- **Technical posture (Bullish)** — No clean trend (EMAs entangled); weak trend (ADX 14); MACD bullish & improving; RSI 56. ATR 3.0%/day.
- **Factor model (SELL, 10th pctile)** — composite -0.64 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.7z) · mom6-1 - (-0.7z) · trend - (-0.4z) · low-vol + (+0.9z) · RS-sector ~0 (+0.1z) · turnover - (-0.4z).
- **⚠ Conflict** — factor model (SELL) opposes the bullish price trend; this is a mean-reversion bet against momentum. Low confidence — wait for trend confirmation or treat as contrarian only.
- **Relationship / risk** — daily-3mo β(SPY) 0.3 · intraday market R² 0.06 · dominant driver **XLC** · 3mo corr vs SPY +0.11 · VIX corr -0.20 (risk-on).

### 37. WMT — Walmart · Factor **SELL** / Posture **Neutral** · _Mixed_

- **Price / return** — $112.20 · 1w -1.3% · 1mo -4.2% · 3mo -12.3% · at 44% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 21); MACD bullish & weakening; RSI 42. ATR 2.5%/day.
- **Factor model (SELL, 8th pctile)** — composite -0.64 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.5z) · mom6-1 - (-0.5z) · trend - (-0.3z) · low-vol strong+ (+1.1z) · RS-sector - (-0.5z) · turnover ~0 (-0.1z).
- **Relationship / risk** — daily-3mo β(SPY) -0.4 · intraday market R² 0.08 · dominant driver **XLP** · 3mo corr vs SPY -0.20 · VIX corr +0.25 (defensive/inverse).

### 38. CMCSA — Comcast · Factor **SELL** / Posture **Neutral** · _Mixed_

- **Price / return** — $23.78 · 1w +2.5% · 1mo +6.0% · 3mo -20.0% · at 13% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 25); MACD bullish & weakening; RSI 50. ATR 3.3%/day.
- **Factor model (SELL, 5th pctile)** — composite -0.67 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.8z) · mom6-1 - (-0.7z) · trend - (-0.8z) · low-vol + (+0.6z) · RS-sector - (-0.7z) · turnover ~0 (-0.1z).
- **Relationship / risk** — daily-3mo β(SPY) -0.3 · intraday market R² 0.01 · dominant driver **XLP** · 3mo corr vs SPY -0.08 · VIX corr +0.11 (defensive/inverse).

### 39. PFE — Pfizer · Factor **SELL** / Posture **Neutral** · _Mixed_

- **Price / return** — $24.75 · 1w +2.1% · 1mo -1.8% · 3mo -10.1% · at 28% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 23); MACD bullish & weakening; RSI 50. ATR 2.2%/day.
- **Factor model (SELL, 3th pctile)** — composite -0.70 → band 0 (exp -0.80% / 20d, 43% hit). Drivers: mom12-1 - (-0.6z) · mom6-1 - (-0.5z) · trend - (-0.3z) · low-vol strong+ (+1.3z) · RS-sector - (-0.6z) · turnover + (+0.5z).
- **Relationship / risk** — daily-3mo β(SPY) -0.1 · intraday market R² 0.00 · dominant driver **XLV** · 3mo corr vs SPY -0.04 · VIX corr +0.03 (neutral).

### 40. SPY — S&P 500 ETF · Posture **Neutral** (index ETF — factor model n/a)

- **Price / return** — $742.09 · 1w -1.3% · 1mo -0.6% · 3mo +4.7% · at 87% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 18); MACD bearish & weakening; RSI 48. ATR 1.1%/day.
- **Relationship / risk** — daily-3mo β(SPY) 0.0 · intraday market R² 1.00 · dominant driver **SPY** · 3mo corr vs SPY +1.00 · VIX corr -0.89 (risk-on).

### 41. QQQ — Nasdaq-100 ETF · Posture **Bearish** (index ETF — factor model n/a)

- **Price / return** — $696.06 · 1w -3.3% · 1mo -6.0% · 3mo +7.6% · at 74% of the 52-wk range
- **Technical posture (Bearish)** — No clean trend (EMAs entangled); weak trend (ADX 18); MACD bearish & weakening; RSI 42. ATR 2.1%/day.
- **Relationship / risk** — daily-3mo β(SPY) 1.8 · intraday market R² 0.80 · dominant driver **XLK** · 3mo corr vs SPY +0.92 · VIX corr -0.79 (risk-on).

### 42. IWM — Russell 2000 ETF · Posture **Neutral** (index ETF — factor model n/a)

- **Price / return** — $292.31 · 1w -0.7% · 1mo -1.1% · 3mo +5.4% · at 90% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 12); MACD bearish & weakening; RSI 47. ATR 1.6%/day.
- **Relationship / risk** — daily-3mo β(SPY) 1.1 · intraday market R² 0.56 · dominant driver **XLI** · 3mo corr vs SPY +0.77 · VIX corr -0.67 (risk-on).

### 43. DIA — Dow 30 ETF · Posture **Neutral** (index ETF — factor model n/a)

- **Price / return** — $517.94 · 1w -1.3% · 1mo +0.5% · 3mo +4.8% · at 87% of the 52-wk range
- **Technical posture (Neutral)** — No clean trend (EMAs entangled); weak trend (ADX 18); MACD bearish & weakening; RSI 47. ATR 1.1%/day.
- **Relationship / risk** — daily-3mo β(SPY) 0.7 · intraday market R² 0.46 · dominant driver **XLI** · 3mo corr vs SPY +0.73 · VIX corr -0.63 (risk-on).
