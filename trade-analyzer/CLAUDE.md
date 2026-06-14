# CLAUDE.md — trade-analyzer

Per-app guidance. Read the repo-root `CLAUDE.md` first for monorepo-wide rules
(paths/ports via `repo_paths.py`, proxy-owns-auth, secrets in `shared/`).

## Role

Single-user **desktop** technical-analysis tool. On-demand: the user launches
it to analyze a symbol; it is **not** part of the automated startup chain and
binds no cross-app port. It is a *consumer* of `schwab-proxy` (like the
scanner/sentiment dashboards).

## Entry point

`trade_analyzer.py` (repo-root added to `sys.path`; imports `PROXY_URL` from
`repo_paths`). Launches a `customtkinter` GUI under `if __name__ == '__main__'`.

```powershell
cd trade-analyzer && python trade_analyzer.py   # proxy must be up on :8100
```

## Architecture

`trade_analyzer.py` is a large module containing the GUI, indicator math, and
the data layer. The data layer is two small classes:

- **`SchwabClient`** — proxy-backed. Every market-data call funnels through
  `_request(endpoint, params)` which hits `{PROXY_URL}{endpoint}`
  (`/quotes`, `/pricehistory`, `/chains`). It owns **no** OAuth/tokens — the
  proxy does. `is_token_valid()` / `is_refresh_token_valid()` report proxy
  health; `authorize()` is a no-op.
- **`MarketDataClient`** — thin v2-compatible wrapper over `SchwabClient`.

If you need a new Schwab endpoint, add a method that calls `_request` — do not
reintroduce direct `api.schwabapi.com` access or a local token file.

## The analysis package (`src/analysis/`) — the valuable core

Pure, well-tested, proxy-independent modules:

| Module | What it provides |
|---|---|
| `scoring.py` | 13 normalized scoring primitives → `[-100, +100]` (RSI, ADX, MACD, vol profile, PE/PEG, growth, ROE, margin, EPS streak, …) |
| `recommendation.py` | `PositionVerdict` (1–8 wk, 11 weighted factors + hard gates: ADX<15, below 200EMA, earnings window, sector downtrend) and `InvestorVerdict` (months+, fundamentals) |
| `fundamentals.py` | `parse_schwab_fundamentals` / `parse_finviz_fundamentals` → `Fundamentals` dataclass with `is_sufficient()` gate |
| `sector_strength.py` | `compute_sector_strength` — 3-mo RS percentile vs SPY, confirmed-downtrend gate |

`recommendation.py` imports `scoring.py`, `fundamentals.py`, `sector_strength.py`.

> **Overlap note (future work):** `scoring.py`, `recommendation.py`, and
> `fundamentals.py` are unique and have no equivalent in `shared/analysis_lib`.
> A deliberate follow-up could *promote* them to `shared/analysis_lib` so the
> scanner's `blueprint_scorer` can reuse them. The duplicated/inferior modules
> (the old `technical.py`, `sectors.py`) were **dropped** during migration —
> `shared/analysis_lib` already has richer equivalents.

## Tests

```powershell
cd trade-analyzer && python -m pytest tests
```

Baseline: **175 passed**. Suite lives in `tests/analysis/` and is pure (no proxy
needed). `tests/conftest.py` puts the app folder on `sys.path` so
`from src.analysis...` resolves.

## Migration provenance

Imported from the standalone `TradeAnalyzer-Python` project (the `main_v3_schwab.py`
edition). Dropped on the way in: 6 legacy `main_*.py` variants, the PyInstaller
build/`venv`/`dist` machinery, the app's own OAuth client + plaintext
`config/settings.json` secrets + local token file, and the orphaned
`technical.py` / `sectors.py` / old `gui/` modules. Rewired to fetch data
through `schwab-proxy`.
