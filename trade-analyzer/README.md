# trade-analyzer

Multi-timeframe technical-analysis desktop tool (part of the **Trading With
Schwab** monorepo). Enter a symbol; it pulls multi-timeframe candles from
Schwab (via the shared proxy), computes EMA/RSI/ADX/VWAP + volume profile, and
produces two verdicts — a short-horizon **PositionVerdict** (1–8 weeks) and a
fundamentals-driven **InvestorVerdict** (months+).

## Features

- **Multi-timeframe analysis**: 1-min, 5-min, 15-min, 60-min, Daily
- **EMA alignment**: EMA 12/21/50 across all timeframes
- **Momentum**: RSI, ADX, VWAP, relative volume
- **Volume profile**: POC, VAH, VAL
- **Verdict engines**: `PositionVerdict` (11 weighted factors + hard gates) and
  `InvestorVerdict` (valuation/growth/earnings) — see `src/analysis/`
- **Charts**: hover candlestick popups (mplfinance)
- **Sector/industry peers**: FinViz screening fallback

## Schwab access

This app fetches **all** market data through `schwab-proxy` on
`repo_paths.PROXY_URL` (`http://127.0.0.1:8100`). It does **not** own OAuth,
tokens, or credentials — the proxy does. **Start `schwab-proxy` first** (see the
repo root `CLAUDE.md`); if the proxy is down, the analyzer logs a warning and
data calls return `None`.

## Run

```powershell
# from the repo root: start the proxy first, then:
cd trade-analyzer
pip install -r requirements.txt
python trade_analyzer.py
```

## Tests

```powershell
cd trade-analyzer
python -m pytest tests        # 175 tests, all green
```

The analysis test suite (`tests/analysis/`) is pure and does not need the proxy.

## Symbols

- **Stocks**: any symbol (AAPL, MSFT, …)
- **Futures**: ES, NQ, MES, MNQ, RTY, YM, CL, GC
