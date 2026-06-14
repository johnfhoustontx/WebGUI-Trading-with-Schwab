# SPX Options Expected Close Calculator — App Specification

**Version:** 1.0.1  
**Last Updated:** 2026-04-07  
**Author:** John F. Houston  
**Location:** `D:\Schwab Test Project\OptionsScanner\`

---

## 1. Purpose

A Python application that calculates the options-implied expected closing price for SPX (and optionally other tickers) by combining max pain analysis, implied volatility expected moves, open interest distribution, put/call ratios, and dealer gamma exposure (GEX). Delivers results via a FastAPI endpoint (for consumption by NinjaTrader, Streamlit, or other tools) and a Streamlit dashboard for visual analysis.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      OptionsScanner                            │
│                                                                 │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────┐ │
│  │ data_provider│───►│ calculation_engine│───►│ api_server    │ │
│  │  .py         │    │  .py             │    │  .py (FastAPI) │ │
│  └──────────────┘    └──────────────────┘    └───────────────┘ │
│         │                     │                      │          │
│         │                     ▼                      ▼          │
│         │            ┌──────────────┐        ┌─────────────┐   │
│         │            │ models.py    │        │ dashboard.py │   │
│         │            │ (dataclasses)│        │ (Streamlit)  │   │
│         │            └──────────────┘        └─────────────┘   │
│         ▼                                                       │
│  ┌──────────────┐                                               │
│  │ config.json  │  Schwab API creds, symbols, refresh interval │
│  └──────────────┘                                               │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
   External Data Sources
   ├── Schwab API (options chain, quotes)
   ├── CBOE (VIX quote — fallback)
   └── Polygon.io (backup options data — optional)
```

---

## 3. File Structure

```
D:\Schwab Test Project\OptionsScanner\
├── config.json                  # API keys, symbols, settings
├── api_server.py                # FastAPI server (port 8100)
├── data_provider.py             # Options chain + quote fetchers
├── calculation_engine.py        # All math: max pain, GEX, expected move
├── models.py                    # Pydantic/dataclass models
├── dashboard.py                 # Streamlit dashboard
├── requirements.txt             # Dependencies
├── cache/                       # Local JSON cache for rate-limit protection
│   └── spx_chain_YYYYMMDD.json
└── logs/
    └── expected_close.log
```

---

## 4. Data Models (`models.py`)

### 4.1 `OptionContract`

| Field | Type | Description |
|-------|------|-------------|
| `strike` | `float` | Strike price |
| `expiration` | `date` | Expiration date |
| `option_type` | `str` | `"CALL"` or `"PUT"` |
| `bid` | `float` | Bid price |
| `ask` | `float` | Ask price |
| `last` | `float` | Last traded price |
| `volume` | `int` | Day's volume |
| `open_interest` | `int` | Open interest |
| `implied_volatility` | `float` | IV (decimal, e.g., 0.27) |
| `delta` | `float` | Delta greek |
| `gamma` | `float` | Gamma greek |
| `theta` | `float` | Theta greek |
| `vega` | `float` | Vega greek |
| `in_the_money` | `bool` | ITM flag |

### 4.2 `MaxPainResult`

| Field | Type | Description |
|-------|------|-------------|
| `expiration` | `date` | Expiration date |
| `max_pain_strike` | `float` | Strike where total OI dollar payout is minimized |
| `total_call_oi` | `int` | Sum of call open interest |
| `total_put_oi` | `int` | Sum of put open interest |
| `put_call_ratio` | `float` | Total put OI / total call OI |
| `call_oi_itm` | `int` | In-the-money call OI |
| `call_oi_otm` | `int` | Out-of-the-money call OI |
| `put_oi_itm` | `int` | In-the-money put OI |
| `put_oi_otm` | `int` | Out-of-the-money put OI |
| `oi_by_strike` | `dict[float, dict]` | Per-strike breakdown: `{strike: {call_oi, put_oi, call_vol, put_vol}}` |

### 4.3 `ExpectedMoveResult`

| Field | Type | Description |
|-------|------|-------------|
| `spot_price` | `float` | Current underlying price |
| `vix` | `float` | Current VIX level |
| `iv_30d` | `float` | 30-day implied volatility (from ATM straddle) |
| `daily_expected_move_1sigma` | `float` | 1σ daily move in points |
| `daily_expected_move_2sigma` | `float` | 2σ daily move in points |
| `upper_1sigma` | `float` | Spot + 1σ |
| `lower_1sigma` | `float` | Spot − 1σ |
| `upper_2sigma` | `float` | Spot + 2σ |
| `lower_2sigma` | `float` | Spot − 2σ |

### 4.4 `GammaExposureResult`

| Field | Type | Description |
|-------|------|-------------|
| `gex_by_strike` | `dict[float, float]` | Net gamma exposure per strike (calls − puts) in $ terms |
| `total_gex` | `float` | Sum of all GEX |
| `gex_flip_strike` | `float` | Strike where GEX flips from positive to negative |
| `gex_peak_call_strike` | `float` | Strike with largest positive gamma (call wall) |
| `gex_peak_put_strike` | `float` | Strike with largest negative gamma (put wall) |

### 4.5 `ExpectedCloseReport`

Top-level response combining all sub-analyses.

| Field | Type | Description |
|---------|------|-------------|
| `symbol` | `str` | Ticker (e.g., `"$SPX"`) |
| `timestamp` | `datetime` | When calculation was performed |
| `spot_price` | `float` | Current spot |
| `previous_close` | `float` | Prior session close |
| `max_pain` | `MaxPainResult` | 0DTE max pain analysis |
| `max_pain_weekly` | `MaxPainResult` | Nearest weekly expiry max pain |
| `max_pain_monthly` | `MaxPainResult` | Nearest monthly OpEx max pain |
| `expected_move` | `ExpectedMoveResult` | IV-based expected range |
| `gamma_exposure` | `GammaExposureResult` | Dealer GEX profile |
| `expected_close_price` | `float` | **Composite expected close** (see §5.5) |
| `expected_close_range` | `tuple[float, float]` | Low/high range of expected close |
| `confidence_level` | `str` | `"HIGH"`, `"MEDIUM"`, `"LOW"` based on signal agreement |
| `bias` | `str` | `"BULLISH"`, `"BEARISH"`, `"NEUTRAL"` |

---

## 5. Calculation Engine (`calculation_engine.py`)

### 5.1 Max Pain Calculation

For each candidate strike `K` in the chain:

```
total_payout(K) = Σ [call_oi(s) × max(0, s - K) × 100]   for all strikes s
                + Σ [put_oi(s) × max(0, K - s) × 100]    for all strikes s
```

Max pain = strike `K` that **minimizes** `total_payout(K)`.

**Implementation notes:**
- Iterate all strikes in the chain as candidate settlement prices
- For each candidate, sum the intrinsic payout to all open contracts
- Return the strike with minimum total payout
- Handle strike intervals (SPX uses 5-point strikes near ATM, 25-point further OTM)

### 5.2 Expected Move from Implied Volatility

**Method A — VIX-derived (broad market):**

```
daily_move_1σ = spot × (VIX / 100) / √252
```

**Method B — ATM straddle (more precise per-expiry):**

```
expected_move = straddle_price × 0.85
```

Where `straddle_price` = ATM call mid + ATM put mid for the target expiration. The 0.85 multiplier converts straddle price to ~1σ move (empirical adjustment).

**Method C — IV of nearest ATM options:**

```
daily_move_1σ = spot × IV_atm × √(DTE / 365)
```

Use Method B as primary for 0DTE; fall back to Method A when straddle data is unavailable.

### 5.3 Gamma Exposure (GEX)

Per-strike net gamma exposure:

```
GEX(K) = [call_oi(K) × call_gamma(K) × 100 × spot² × 0.01]
        - [put_oi(K) × put_gamma(K) × 100 × spot² × 0.01]
```

The `× 0.01` normalizes to "per 1% move in underlying."

**GEX flip point:** Interpolate the strike where cumulative GEX crosses zero. Above the flip, dealers are long gamma (mean-reverting flow); below, short gamma (trending/volatile flow).

### 5.4 Put/Call Ratio Analysis

| Metric | Formula | Signal |
|--------|---------|--------|
| Volume P/C | Total put volume / total call volume | Intraday sentiment |
| OI P/C | Total put OI / total call OI | Positioning sentiment |
| Equity-only P/C | Equity puts / equity calls (excl. index) | Retail sentiment |

Interpretation thresholds (SPX-specific):
- P/C > 1.5 → strongly bearish positioning (contrarian bullish)
- P/C 1.0–1.5 → moderately bearish
- P/C 0.7–1.0 → neutral
- P/C < 0.7 → bullish positioning (contrarian bearish)

### 5.5 Composite Expected Close (Weighted Model)

Combine the signals into a single point estimate and range:

```python
WEIGHTS = {
    "max_pain_0dte":    0.30,   # Strongest near-term magnet
    "max_pain_weekly":  0.10,   # Broader positioning context
    "gex_flip":         0.25,   # Dealer hedging pivot
    "straddle_center":  0.20,   # Market-priced expected center
    "spot_price":       0.15,   # Momentum/inertia
}

expected_close = sum(signal_value * weight for signal_value, weight in signals)
```

**Confidence scoring:**
- `HIGH` — All 5 signals within 0.5% of each other
- `MEDIUM` — 3+ signals cluster within 0.5%; remainder within 1.5%
- `LOW` — Signals diverge > 1.5% (conflicting information)

**Bias determination:**
- `BULLISH` if expected_close > spot + 0.15%
- `BEARISH` if expected_close < spot − 0.15%
- `NEUTRAL` otherwise

---

## 6. Data Provider (`data_provider.py`)

### 6.1 Primary Source: Schwab API

| Endpoint | Data | Usage |
|----------|------|-------|
| `GET /marketdata/v1/chains` | Full options chain with greeks | Max pain, GEX, OI analysis |
| `GET /marketdata/v1/quotes` | Real-time quote (spot, bid/ask) | Spot price, previous close |
| `GET /marketdata/v1/quotes/$VIX.X` | VIX quote | Expected move calculation |

**Authentication:** OAuth2 client credentials flow. Token refresh via `schwab-py` or manual refresh logic.

**Rate limiting:** Cache chain data for 60 seconds (configurable). VIX/spot quotes refresh every 15 seconds.

### 6.2 Fallback Source: CBOE Data (web scrape)

If Schwab API is unavailable:
- VIX from `https://www.cboe.com/tradable_products/vix/`
- SPX delayed quote from Yahoo Finance API

### 6.3 Caching Strategy

```
cache/spx_chain_{YYYYMMDD}_{HHMM}.json   # Options chain snapshots
cache/spx_quotes_{YYYYMMDD}.json          # Intraday quote history
```

- Chain data: cache 60s (configurable via `config.json`)
- Quotes: cache 15s
- On startup, attempt to load most recent cache if API fails

---

## 7. API Server (`api_server.py`)

**Port:** 8100 (configurable)

### 7.1 Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Server health check |
| `GET` | `/expected-close/{symbol}` | Full `ExpectedCloseReport` for symbol |
| `GET` | `/max-pain/{symbol}` | Max pain only, with optional `?expiry=YYYY-MM-DD` |
| `GET` | `/expected-move/{symbol}` | IV-based expected range |
| `GET` | `/gex/{symbol}` | Gamma exposure profile |
| `GET` | `/summary/{symbol}` | Condensed one-liner: `{expected_close, range, bias, confidence}` |
| `POST` | `/refresh/{symbol}` | Force data refresh (bypass cache) |

### 7.2 Example Response: `/expected-close/$SPX`

```json
{
  "symbol": "$SPX",
  "timestamp": "2026-04-07T10:30:00-05:00",
  "spot_price": 6565.67,
  "previous_close": 6582.69,
  "expected_close_price": 6551.8,
  "expected_close_range": [6471, 6695],
  "confidence_level": "MEDIUM",
  "bias": "BEARISH",
  "max_pain": {
    "expiration": "2026-04-07",
    "max_pain_strike": 6545.0,
    "total_call_oi": 62460,
    "total_put_oi": 75830,
    "put_call_ratio": 1.21
  },
  "expected_move": {
    "vix": 26.95,
    "daily_expected_move_1sigma": 111.8,
    "upper_1sigma": 6694.5,
    "lower_1sigma": 6470.9
  },
  "gamma_exposure": {
    "gex_flip_strike": 6560.0,
    "gex_peak_call_strike": 6600.0,
    "gex_peak_put_strike": 6500.0,
    "total_gex": -1250000.0
  }
}
```

### 7.3 NinjaTrader Integration

The FastAPI endpoint is callable from NinjaScript using the same `HttpClient` pattern already in `MLEnsembleStrategyV5`:

```csharp
// In OnBarUpdate or a timer callback
string url = "http://localhost:8100/summary/$SPX";
var response = await httpClient.GetStringAsync(url);
var result = JsonConvert.DeserializeObject<ExpectedCloseResult>(response);
```

This allows any NinjaTrader strategy to query the options-implied expected close as a pre-market or intraday signal.

---

## 8. Dashboard (`dashboard.py`)

Streamlit UI with the following panels:

### 8.1 Layout

```
┌─────────────────────────────────────────────────────┐
│  SPX Options Expected Close Calculator              │
│  Symbol: [$SPX ▼]   Refresh: [Auto 60s ▼] [Now]   │
├────────────┬────────────┬────────────┬──────────────┤
│ Spot Price │  Max Pain  │    VIX     │ Expected     │
│  6,565.67  │   6,545    │   26.95    │ Close: 6,552 │
├────────────┴────────────┴────────────┴──────────────┤
│                                                      │
│  [Expected Range Chart — horizontal bar]             │
│  Shows 1σ/2σ ranges, max pain levels, GEX flip      │
│                                                      │
├──────────────────────┬───────────────────────────────┤
│  Max Pain by Expiry  │  Open Interest Distribution   │
│  (table: 0DTE,       │  (bar chart: calls vs puts    │
│   weekly, monthly)   │   by strike, near ATM)        │
├──────────────────────┴───────────────────────────────┤
│                                                      │
│  [GEX Profile Chart]                                 │
│  Per-strike gamma exposure, flip point highlighted   │
│                                                      │
├──────────────────────────────────────────────────────┤
│  Signal Agreement Table                              │
│  Max Pain 0DTE | GEX Flip | Straddle | Spot | Bias  │
│    6,545       |  6,560   |  6,558   | 6,566| BEAR  │
└──────────────────────────────────────────────────────┘
```

### 8.2 Controls

| Control | Type | Options |
|---------|------|---------|
| Symbol | Dropdown | `$SPX`, `SPY`, `QQQ`, `$NDX` |
| Auto-refresh | Dropdown | Off, 15s, 30s, 60s, 5m |
| Expiry filter | Multi-select | Today, This week, Monthly, All |
| Strike range | Slider | ±N strikes from ATM (default: ±30) |

### 8.3 Charts (Plotly)

1. **Expected range** — Horizontal floating bar: 2σ range (light), 1σ range (dark), max pain marker, GEX flip marker, spot line
2. **OI distribution** — Grouped bar chart: call OI (blue) vs put OI (red) per strike, ±20 strikes from ATM
3. **GEX profile** — Bar chart: positive GEX (green) vs negative (red) per strike, with flip point vertical line
4. **Max pain convergence** — Small multiples showing max pain by expiry (0DTE, 1DTE, weekly, monthly) as a scatter/line

---

## 9. Configuration (`config.json`)

```json
{
  "schwab_token_path": "C:\\Users\\john_\\.schwab-mcp\\token.json",
  "symbols": ["$SPX", "SPY"],
  "default_symbol": "$SPX",
  "api_port": 8100,
  "cache_ttl_chain_seconds": 60,
  "cache_ttl_quote_seconds": 15,
  "log_level": "INFO",
  "log_file": "logs/expected_close.log",
  "composite_weights": {
    "max_pain_0dte": 0.30,
    "max_pain_weekly": 0.10,
    "gex_flip": 0.25,
    "straddle_center": 0.20,
    "spot_price": 0.15
  },
  "strike_range_from_atm": 30,
  "dashboard_port": 8501
}
```

---

## 10. Dependencies (`requirements.txt`)

```
fastapi>=0.110.0
uvicorn>=0.29.0
httpx>=0.27.0
schwab-py>=1.4.0
pandas>=2.2.0
numpy>=1.26.0
pydantic>=2.6.0
streamlit>=1.32.0
plotly>=5.20.0
python-dateutil>=2.9.0
```

Python version: **3.11+** (consistent with ML server environment).

---

## 11. Startup & Deployment

### 11.1 FastAPI Server

```bash
cd "D:\Schwab Test Project\OptionsScanner"
python -m uvicorn api_server:app --host 0.0.0.0 --port 8100
```

### 11.2 Streamlit Dashboard

```bash
cd "D:\Schwab Test Project\OptionsScanner"
streamlit run dashboard.py --server.port 8501
```

### 11.3 Combined Launch (batch file)

```batch
@echo off
REM D:\Schwab Test Project\OptionsScanner\start.bat
start "OptionsScanner API" cmd /k "cd /d "D:\Schwab Test Project\OptionsScanner" && python -m uvicorn api_server:app --host 0.0.0.0 --port 8100"
timeout /t 3
start "OptionsScanner Dashboard" cmd /k "cd /d "D:\Schwab Test Project\OptionsScanner" && streamlit run dashboard.py --server.port 8501"
```

---

## 12. Future Enhancements (V2.0)

| Feature | Description | Priority |
|---------|-------------|----------|
| Multi-symbol support | Simultaneous SPX + NDX + QQQ analysis | High |
| Historical backtesting | Compare predicted vs actual close over N days | High |
| Pre-market agent integration | Feed expected close to `PremarketAgent` scoring pipeline | Medium |
| NinjaScript indicator | Custom indicator that displays expected close as a horizontal line on chart | Medium |
| Dark pool / OPRA feed | Level 2 options flow for real-time OI updates | Low |
| Webhook alerts | Notify when spot crosses max pain or GEX flip | Medium |
| Schwab MCP integration | Use existing MCP server tools for data pull instead of direct API | Low |

---

## 13. Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.1 | 2026-04-07 | Relocated project from `D:\AI_Based_Analysis\OptionsExpectedClose\` to `D:\Schwab Test Project\OptionsScanner\`; updated all path references |
| 1.0.0 | 2026-04-07 | Initial specification |
