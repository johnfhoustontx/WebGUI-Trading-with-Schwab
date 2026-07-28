# Changelog

Notable changes to this project. Format loosely follows [Keep a Changelog](https://keepachangelog.com).
The **detailed** per-feature record (with rationale + file-level notes) lives in
[`CLAUDE.md`](CLAUDE.md); this file is the human-scannable summary. Design docs and audit
reports are under [`docs/plans/`](docs/plans) and [`docs/audits/`](docs/audits).

This is a single-user project on a long-lived `Using_Highcharts` branch; entries are dated
rather than version-tagged.

## 2026-07-28

### Added
- **Momentum cascade** — a regime-conditioned momentum score across three levels
  (11 sectors, **70** industry ETFs, 311 stocks), sourced from the new **Stocks** tab in
  `Sectors_Industries_ETFs.xlsx`. Pure math in `sentiment-dashboard/scoring/momentum.py`
  (Clenow trend x R², relative strength, acceleration, path quality, participation) and
  `scoring/momentum_regime.py` (dispersion + crash risk → favorable / neutral / suppressed).
  Orchestrated by `services/sentiment_svc` on **one nightly slot at 16:20 CT** into a new
  SQLite store (`momentum.db`) and published as **`cache:sentiment:momentum`**
  (`MomentumSnapshot`). New webgui tab **`/sentiment/momentum`** — regime banner, quadrant
  scatter, rank ribbon, and a decomposable top/bottom-15 leaderboard.
  Design/plan: [docs/plans/2026-07-28-momentum-cascade-design.md](docs/plans/2026-07-28-momentum-cascade-design.md)
  / [-plan.md](docs/plans/2026-07-28-momentum-cascade-plan.md).

### Notes
- Momentum is **context on its own cache key, NOT a sentiment component** —
  `scoring/__init__.py:WEIGHTS`, the composite, and the bridge are untouched.
- The industry level scores **70** ETFs, not the 74 industries on the Stocks tab: `MJ`,
  `XRT`, `BETZ` and `VEGI` are each listed under two industries, and scoring one price
  series twice would invent a "two industries agree" signal. Those four surface in
  `excluded` with `reason: "duplicate_etf"`.

## 2026-07-02

### Added
- **Engineering tooling (best-practices Tier 1):** GitHub Actions CI (`.github/workflows/ci.yml`
  — per-folder test matrix + `ruff` + `pip-audit`, on windows-latest); `ruff.toml` lint gate
  (select `E9,F`, legacy engine dirs grandfathered); `.pre-commit-config.yaml`; `requirements.lock`
  (pinned runtime) + `requirements-dev.txt`; `README.md`, `LICENSE`, `SECURITY.md`, this changelog.
- **Best-practices / industry-standards validation** ([docs/audits/2026-07-02-best-practices-validation.md](docs/audits/2026-07-02-best-practices-validation.md)).

### Fixed
- Removed 39 unused imports / empty f-strings in the active tiers via `ruff --fix` (tests green).
- One-time GEX DB `VACUUM` reclaimed ~1.66 GB (3.04 GB → 1.38 GB) after enabling retention.

## 2026-07-01

### Added
- **Technical audit** (5 pillars) + **calculation-accuracy audit** ([docs/audits/](docs/audits)).

### Changed — Reliability remediation (audit pillar, lowest-scored)
- **R1** driver open results captured/logged/surfaced (`last_open_results`) — retires a known
  silent-failure class. **R2** dead scheduler now auto-restarts + `/health` `scheduler_alive`.
  **R3** per-service rotating file logging + silent `except: pass` → `log.exception`. **R4b/R8**
  chime/badge alerts on stale views + down services. **R5** `Command.ts` + stale-command gate.
  **R6** buying-power reconciliation at startup. **R7** driver stand-down reason taxonomy.
  **R9** proxy 4xx-no-retry + log rotation, SSE backoff, watcher tick guard, atomic EOD writes.

### Changed — Performance + Architecture remediation
- GEX retention (`purge_keep_sessions`, bounds the 3 GB DB) + 14 MB→3 MB gamma payload crop;
  command handlers dispatched off the event loop; command-stream dead-lettering + `maxlen`;
  concurrent scheduler branches; sentiment off-hours gating + once-per-day backfill; big webgui
  reads via `run.io_bound`; sargable GEX queries; portfolio off-hours rebuild gate.

### Changed — Calculation-accuracy remediation
- RSI + ADX → Wilder smoothing; VWAP session-anchored; volume-profile value-area contiguous;
  relative-strength parity ratio; swing-scanner + driver economics net-of-commission; paper P&L
  net-of-fees; single risk-free rate; simulator expiry 16:00 ET tz-aware; term-GEX ×0.01 unit fix;
  factor-model live/fit z-basis aligned; portfolio annualized-return on a 252-day basis.

## Earlier

The full history — the 3-tier migration, every page/feature, and per-session decisions — is in
[`CLAUDE.md`](CLAUDE.md) and the paired design/plan docs under [`docs/plans/`](docs/plans).
