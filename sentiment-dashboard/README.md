# Daily Sentiment Dashboard
## Stock Trading Blueprint v2.0 - AI Analysis Layer

A Windows GUI application for tracking and calculating market sentiment scores based on the Market Sentiment Integration Framework.

---

## v4.0 Methodology (current)

The v4.0 composite is a **confidence-weighted blend of 6 components**.
VIX Term, VIX1D, and Term Slope have been merged into a single
**VIX Complex** component (30%); the three sub-scores are still
visible on the VIX tab but no longer enter the composite directly.
All live inputs are now fetched from Schwab — no third-party survey
sources remain.

### Component weights (single source of truth: `scoring/__init__.py:WEIGHTS`)

| Component | Weight | Source / Scoring |
|---|---|---|
| VIX Complex | 30% | Internal blend of Term 50% + VIX1D 33% + Slope 17% (the original 15/10/5). Each sub-score is still displayed on the VIX tab. |
| Put/Call | 15% | CBOE $CPCE (Equity) primary; $CPCI (Index) shown for ref. |
| Breadth | 15% | NYSE A/D ratio primary ($ADVN/$DECN), % above 50 DMA secondary, H/L ratio adjusts ±1. |
| Rotation | 15% | Blended day/3d/week cyclical-vs-defensive spread (40/40/20). Day and 3d sub-scores let us catch regime inflections **before** the weekly average rolls over. |
| Sector Performance | 15% | S&P cap-weighted daily move across 11 GICS sectors. |
| Credit Pulse | 10% | Duration-matched HYG/IEI z-score (60%) blended with HYG distance from 50d MA (40%). IEI replaces LQD because LQD's long duration confounds the credit signal with rate moves. 60d history cached locally; refreshed once per session. |

**Composite formula:**

```
composite = Σ(weight_i × score_i × conf_i) / Σ(weight_i × conf_i)
```

Aggregate confidence (`Σ w·c`, since `Σ w = 1`) is displayed next to
the composite score — e.g. `7.74 / 10 @ 97% conf`.

### VIX Term piecewise scoring (v3.9)

Replaces the linear curve. The narrow neutral band ensures small
moves through 1.05 produce visible score changes:

| VIX / 10d MA | Score |
|---|---|
| < 0.85 | 10 |
| 0.85-0.95 | linear 7 → 9 |
| 0.95-1.05 | linear 5 → 6 (neutral band) |
| 1.05-1.15 | linear 3 → 4 |
| 1.15-1.30 | linear 1 → 2 |
| > 1.30 | 1 |

### Composite velocity (v3.9)

Three metrics computed from `sentiment_history.json` and surfaced
beneath the composite table:

- `3d ROC` = today's composite − composite 3 sessions ago
- `5d ROC` = today's composite − composite 5 sessions ago
- `20d Z` = (today − 20d mean) / 20d std

When `|20d Z| > 1.5` a **REGIME BREAK** flag is surfaced (e.g.
`REGIME BREAK: -2.33σ from 20d mean`). This catches the kind of fast
sentiment shifts that day-over-day change misses.

### Divergence flag (stretch)

When any two component scores differ by ≥4 points, a
`DIVERGENCE: X vs Y — low conviction` note appears beside the
composite. Useful when components are sending contradictory signals
and the composite hides the disagreement.

### Bridge schema (additive, backwards compatible)

`sentiment_bridge.json` v3.9 adds:
- `component_scores.vix1d`, `.term_slope`, `.credit_pulse`, `.sector`
- `component_confidence`: per-component 0-1
- `aggregate_confidence`: single weighted number
- `weights`: dict (for downstream introspection)
- `velocity`: `roc_3d`, `roc_5d`, `z_20d`, `regime_break`
- `divergence_flag`: optional string
- `raw_data.vix1d`, `.vix9d`, `.credit_state`

All existing v3.8 fields preserved — Blueprint Analyzer doesn't need
to change.

---

## Installation

### Requirements
- Python 3.8 or higher
- tkinter (included with Python)

### Setup
1. Ensure Python is installed and added to PATH
2. Double-click `Launch_Dashboard.bat` to run

---

## Features

### Sentiment Categories Tracked (v4.0, 6 components)
1. **VIX Complex (30%)** - blended Term / VIX1D / Slope sub-scores
2. **Put/Call (15%)** - CBOE $CPCE equity P/C
3. **Breadth (15%)** - NYSE A/D ratio, % above 50 DMA, New Highs/Lows
4. **Rotation (15%)** - blended day/3d/week cyclical-vs-defensive spread
5. **Sector Performance (15%)** - S&P cap-weighted move across 11 GICS sectors
6. **Credit Pulse (10%)** - HYG/IEI z-score + HYG vs 50d MA

### Automatic Calculations
- Component scores (0-10 scale, contrarian interpretation)
- Weighted composite score
- Position size modifier recommendations
- Trading bias suggestions
- Contrarian signal detection

### Data Management
- Auto-save current session
- Historical tracking (90 days)
- JSON export for analysis
- Day-over-day change tracking

---

## Scoring Guide (Contrarian)

### VIX Scoring
| VIX Level | Score | Interpretation |
|-----------|-------|----------------|
| > 35 | 10 | Panic - Max Opportunity |
| 30-35 | 9 | High Fear |
| 25-30 | 8 | Elevated Fear |
| 20-25 | 7 | Above Avg Fear |
| 15-20 | 5 | Normal |
| 12-15 | 4 | Complacent |
| < 12 | 2 | Extreme Greed |

### Put/Call Ratio Scoring
| P/C Ratio | Score | Interpretation |
|-----------|-------|----------------|
| > 1.3 | 10 | Panic Puts |
| 1.1-1.3 | 8 | Fear |
| 0.9-1.1 | 6 | Neutral |
| 0.7-0.9 | 4 | Bullish |
| < 0.7 | 2 | Extreme Greed |

### Position Size Modifiers
| Composite Score | Modifier | Action |
|-----------------|----------|--------|
| 9-10 | 1.25x | Max contrarian opportunity |
| 7-8 | 1.10x | Favorable for longs |
| 5-6 | 1.00x | Standard sizing |
| 3-4 | 0.85x | Reduce exposure |
| 1-2 | 0.70x | Defensive mode |

---

## Weight Distribution (v4.0)

| Component | Weight |
|-----------|--------|
| VIX Complex | 30% |
| Put/Call | 15% |
| Breadth | 15% |
| Rotation | 15% |
| Sector Performance | 15% |
| Credit Pulse | 10% |

---

## Daily Workflow

### Pre-Market (8:00-9:00 AM)
1. Launch Dashboard
2. Click **Fetch Live Data** (or wait — autofetch runs every 15 min during weekday market hours)
3. Review any flagged regime break / divergence

### Market Open (9:30-10:00 AM)
1. Update live VIX level
2. Note early breadth readings
3. Calculate initial composite score

### End of Day (4:00 PM)
1. Update all final values
2. Calculate composite score
3. Save to history
4. Note change from yesterday
5. Document key observations

---

## File Structure

```
D:\AI_Based_Analysis\SentimentDashboard\
├── sentiment_dashboard.py    # Main application
├── Launch_Dashboard.bat      # Windows launcher
├── README.md                 # This file
├── sentiment_data.json       # Current session data (auto-created)
└── sentiment_history.json    # Historical data (auto-created)
```

---

## Data Sources

All live data is fetched from the **Schwab API** (single source, no
third-party scraping). Symbols used:

| Indicator | Schwab Symbol(s) |
|-----------|------------------|
| VIX / VIX1D / VIX9D / VVIX | `$VIX.X` (variants) / `$VIX1D` / `$VIX9D` / `$VVIX.X` |
| Put/Call (Equity / Index) | `$CPCE` / `$CPCI` (SPX-chain walk as fallback) |
| Breadth A/D, Highs/Lows, %>50DMA | `$ADVN`/`$DECN`, `$NYHGH`/`$NYLOW` (variants), `$SPXA50R` (variants) |
| Sector ETFs | XLY, XLP, XLE, XLF, XLI, XLK, XLC, XLB, XLU, XLV, XLRE |
| Rotation pair dropdowns (auto-derived) | XLY/XLP, SMH/SPY, IWM/SPY, QQQ/SPY (5d % spread) |
| Flow dropdowns (auto-derived) | HYG/IEI 5d spread, TLT 5d, UUP 5d |
| Credit Pulse 60d cache | HYG, IEI |

---

## Keyboard Shortcuts

- **Ctrl+S** - Save current data

---

## Discord / Telegram Notifications

The dashboard can post each sentiment reading to Discord and/or
Telegram as it autosaves. Credentials are resolved in this order
(first match wins):

1. **Constructor kwargs** (when programmatically instantiating
   `SentimentNotifier`).
2. **Environment variables** — `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
   `DISCORD_WEBHOOK_URL`.
3. **Local file** — copy `config_notifications.example.py` to
   `config_notifications.py` next to `sentiment_dashboard.py` (or
   anywhere on `sys.path`) and fill in the values. The real file is
   gitignored.
4. **Shared OptionsScanner file** — if you already have credentials
   configured at
   `D:\Schwab Test Project\OptionsScanner\config_notifications.py`,
   the dashboard loads them from there automatically. Both apps share
   one place to rotate tokens.

Leave any field blank/missing to disable that channel. Set none of
them and the notifier is a silent no-op — no configuration required.

Posts are throttled so the 15-min autofetch doesn't spam the channel:
the first save per session always posts, then subsequent saves post
only on a ≥ 0.3 composite-score move, a bias change, a fresh regime
break / divergence flag, or once an hour as a heartbeat.

---

## Version History

- **4.0** - Merged VIX Term / VIX1D / Slope into single VIX Complex (30%). Composite drops to 6 components. Rotation + Flow dropdowns auto-derived from 5d Schwab price action; manual inputs no longer required for scoring.
- **3.9** - Confidence-weighted blend of 8 components; added VIX1D, Term Slope, Credit Pulse.
- **1.0.0** (2025-01-01) - Initial release

---

*Part of Stock Trading Blueprint v2.0.0*
