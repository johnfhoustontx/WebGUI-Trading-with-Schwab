# CLAUDE.md — portfolio-analyzer

Per-app guidance. Read the repo-root `CLAUDE.md` first for monorepo-wide rules
(paths/ports via `repo_paths.py`, proxy-owns-auth, secrets in `shared/`).

## Role

Single-user **desktop** portfolio / sector analyzer. On-demand: the user
launches it to break their holdings down by sector and compare each position's
performance against its sector, with live-streaming prices and P&L, plus a
Performance tab with live per-position grades and advisory suggestions. It is
**not** part of the automated startup chain and binds no cross-app port. It is a
*consumer* of `schwab-proxy` (like the scanner/sentiment/trade-analyzer apps).

## Entry point

`portfolio_analyzer.py` — a `customtkinter` GUI launched under
`if __name__ == '__main__'`. The repo root is added to `sys.path`; it imports
`PROXY_URL` / `PORTFOLIO_ANALYZER` from `repo_paths`.

```powershell
cd portfolio-analyzer && python portfolio_analyzer.py   # proxy must be up on :8100
```

## Proxy endpoints used

Every Schwab call funnels through `{PROXY_URL}` — this app owns **no**
OAuth/tokens (the proxy does). It relies on the proxy's `/accounts`,
`/positions`, `/transactions/{account_hash}`, `/pricehistory`, and the SSE
`/stream/quotes` endpoints. Single-leg order execution targets
`/orders/{account_hash}` (see execution stub below). Add new Schwab access as a
method on the relevant client — never reintroduce direct `api.schwabapi.com`
access or a local token file.

## Architecture (`src/`)

- **`data.py`** — `PortfolioData`, the proxy-backed data client (positions,
  accounts, transactions, daily price history, and the SSE quote-stream
  consumer `stream_quotes`).
- **`trade_store.py`** — persistent JSON trade store
  (`data/entries.json`): dedupe-by-`trade_id` merge, `last_sync` watermark.
  Pure logic + filesystem, no network.
- **`trades_import.py`** — one-time CSV bootstrap to seed the trade store with
  historical trades.
- **`sync.py`** — incremental **daily auto-sync**: on the first launch each day
  it pulls new trades since `last_sync` from the proxy and merges them in. The
  decision helpers are pure; `sync_trades` takes the data client by injection so
  it tests offline.
- **`live.py`** — pure live-update logic for streamed quotes (`apply_tick`,
  `stream_symbols`, `parse_sse_line`); no network/GUI imports so it unit-tests
  in isolation. The blocking SSE thread lives in `data.py`; the Tk refresh
  wiring lives in `portfolio_analyzer.py`.
- **`sectors.py`** / **`benchmark.py`** / **`portfolio.py`** — sector grouping,
  vs-sector / vs-benchmark relative strength, and `build_portfolio`. Sector
  classification + relative strength reuse
  `shared/analysis_lib/sector_analysis.py` — do not duplicate that logic here.
- **`execution.py`** — order-execution **foundation** (`build_order_body`,
  `ExecutionClient.preview_order` / `place_order`). Single-leg equity orders
  only; it works end-to-end via the proxy but is intentionally **not yet wired
  to any GUI button** — it is groundwork for the live-trading roadmap.
- **`evaluation.py`** — split-speed per-position scorecard: `compute_baseline`
  runs once at load on the worker; `evaluate_portfolio` runs per tick.
  Baselines/evaluation are **EQUITY-only by design** — options and other
  non-equity holdings get no baseline and land on a minimal REVIEW card.
- **`suggestions.py`** — advisory-only rules engine (never auto-trades);
  `Thresholds`-injected so rule boundaries stay tunable and testable.
- **`thresholds.py`** — tunable rule boundaries persisted to
  `data/eval_settings.json`.
- **`view_model.py`** / **`entries.py`** — GUI-facing view model and trade-entry
  shaping.

## Tests

```powershell
cd portfolio-analyzer && python -m pytest tests
```

`tests/conftest.py` puts the app folder and repo root on `sys.path` so
`from src...` and `import repo_paths` resolve. The suite is pure — it does not
need the proxy running (network-bound code like the streaming thread and live
`place_order` path is exercised via injected fakes / pure helpers).
