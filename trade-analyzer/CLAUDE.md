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
| `recommendation.py` | `PositionVerdict` (1–8 wk, 11 weighted factors + hard gates: ADX<15, below 200EMA, earnings window, sector downtrend) and `InvestorVerdict` (months+, fundamentals). **`PositionVerdict` is now the *legacy* swing fallback** — the live 1–8 wk verdict is the validated factor model (see *Offline swing-model fit* below) |
| `fundamentals.py` | `parse_schwab_fundamentals` / `parse_finviz_fundamentals` → `Fundamentals` dataclass with `is_sufficient()` gate |
| `sector_strength.py` | `compute_sector_strength` — 3-mo RS percentile vs SPY, confirmed-downtrend gate |
| `markov.py` | PURE Markov-chain forecast over **5 composite-score bands** — `classify_band`/`count_matrix`/`pooled_prior`/`shrink` (Dirichlet)/`project`/`forecast`/`drift_tilt`; powers the Trade page's **Markov Forecast** card (forecasts the *legacy* technical-momentum composite) |
| `factors.py` | PURE **swing factor library** — 10 causal, sign-corrected daily-OHLCV factors (`mom_12_1`/`mom_6_1`/`pth`/`str_5d`/`vol_adj_mom`/`trend_quality`/`low_vol`/`rs_spy`/`rs_sector`/`turnover`) + the `FACTORS` registry. Each is `(daily_df) → pd.Series`, **no look-ahead** (winsorize cross-sectionally at scoring, never per-factor) |
| `backtest.py` | PURE **offline validation harness** — `factor_ic` (per-date Spearman rank IC + ICIR), `quantile_spread`, `zscore_by_date`, **`signed_ic_weights`** (the production weighter — keeps a factor's IC sign), `composite`, `walk_forward` (rolling OOS IC), `calibrate` (quantile bands → mean-fwd/hit-rate, isotonic-smoothed) |

`recommendation.py` imports `scoring.py`, `fundamentals.py`, `sector_strength.py`.

> **Overlap note (future work):** `scoring.py`, `recommendation.py`, and
> `fundamentals.py` are unique and have no equivalent in `shared/analysis_lib`.
> A deliberate follow-up could *promote* them to `shared/analysis_lib` so the
> scanner's `blueprint_scorer` can reuse them. The duplicated/inferior modules
> (the old `technical.py`, `sectors.py`) were **dropped** during migration —
> `shared/analysis_lib` already has richer equivalents.

## Offline swing-model fit (validated swing evaluation)

The Trade page's **Position (1–8 wk)** verdict is a **backtested, IC-weighted
cross-sectional factor model**, not the hand-weighted `recommendation.PositionVerdict`
(kept as a legacy fallback). The model is **fit offline here**, scored **online** in
`services/trade_svc/swing_model.py`, bridged by a versioned artifact:

- **`fit_swing_model.py`** (repo root on `sys.path`) — the orchestrator. **Run
  manually/periodically; NEVER import it from a service or the request path.** It pulls
  ~78 liquid symbols' **5-yr** daily history via the proxy (a curated `UNIVERSE_SECTOR` →
  sector-ETF map; concurrent), builds a `(date, symbol)` panel with **20-day forward
  excess-return-vs-SPY** labels, runs `src/analysis/backtest.py`
  (train/test/step **378/63/63**), and writes the artifact + a markdown research report.

  ```powershell
  cd trade-analyzer && ..\.venv\Scripts\python fit_swing_model.py   # proxy must be up
  ```

- **Artifact** `data/swing_model.json` (+ the research report) — **gitignored**
  (`trade-analyzer/data/`); paths `repo_paths.SWING_MODEL` / `SWING_MODEL_REPORT`. It
  holds, per regime key (`"all"` today, **C-ready** for `"trend"/"chop"/"highvol"`), the
  signed `weights`, per-factor `factor_ic`, the cross-sectional `norm`, the score→outcome
  `calibration` bands, and the walk-forward `oos_ic`. The live scorer loads it on demand
  and **degrades to the legacy verdict if it's missing**.

- **Honest framing:** the model is *validated* (a small positive OOS IC + a calibrated
  quintile spread), not *guaranteed* — the current fit's edge is thin and
  regime-dependent. **Re-run `fit_swing_model.py` periodically.** Full design, factor
  list, current-fit results, and caveats live in the repo-root `CLAUDE.md` "Validated
  swing evaluation" section + `docs/plans/2026-06-22-swing-validated-evaluation*.md`.

## Tests

```powershell
cd trade-analyzer && python -m pytest tests
```

Baseline: **251 passed**. Suite lives in `tests/analysis/` and is pure (no proxy
needed). `tests/conftest.py` puts the app folder on `sys.path` so
`from src.analysis...` resolves.

## Migration provenance

Imported from the standalone `TradeAnalyzer-Python` project (the `main_v3_schwab.py`
edition). Dropped on the way in: 6 legacy `main_*.py` variants, the PyInstaller
build/`venv`/`dist` machinery, the app's own OAuth client + plaintext
`config/settings.json` secrets + local token file, and the orphaned
`technical.py` / `sectors.py` / old `gui/` modules. Rewired to fetch data
through `schwab-proxy`.
