# NiceGUI Web GUI for Trading With Schwab — Design

**Date:** 2026-06-14
**Status:** Approved

## Purpose

Build a **self-contained** NiceGUI web application that surfaces the active
functionality of the existing `Trading With Schwab` monorepo. The active backend
logic is copied into this new repo (`D:\WebGUI Trading with Schwab`) so the
project is independent of the old repo, and a single NiceGUI multi-page front-end
replaces the old Dash/React/Tk UIs.

Source repo (reference only, not a dependency): `D:\Trading With Schwab`.

## Decisions (from brainstorming)

| Question | Decision |
|----------|----------|
| Relationship to old repo | **Self-contained copy** of active backend logic |
| Apps surfaced in GUI | Options scanner, Sentiment, Trade analyzer, Portfolio analyzer |
| schwab-proxy | Copied in (required gateway, start first) |
| claude-driver | **Included** — gets a NiceGUI page (orchestration + approvals) |
| Front-end shape | **Single multi-page NiceGUI app** with left-nav per feature |
| Old UI layers (Dash/React/Tk) | **Leave out** — read from source as porting reference only |
| Secrets | **Copy real** `appsettings.json` / `tokens.json` (stay gitignored) |

## Architecture

```
D:\WebGUI Trading with Schwab\
├── CLAUDE.md            # living architecture/tech doc (updated as we build)
├── .claude/             # settings.json
├── .gitignore           # carried from source (secrets/db/logs never committed)
├── repo_paths.py        # paths + ports single source of truth (adapted)
├── config/ports.toml    # ports + new `nicegui` port
├── requirements.txt     # consolidated deps + nicegui
├── docs/plans/          # design + plan docs
├── shared/
│   ├── analysis_lib/     # shared library (schwab_client, market_data, technical, mtf, ...)
│   ├── appsettings.json  # real secret (gitignored)  + .example template
│   ├── tokens.json       # real secret (gitignored)  + .example template
│   └── sentiment_bridge.json (+ .example)
├── schwab-proxy/        # central Schwab gateway / token manager — START FIRST (:8100)
├── options-scanner/     # engines only: scanner, gex, scoring, simulator, paper engine
├── sentiment-dashboard/ # scoring/ + bridge + headless snapshot + data (.xlsx)
├── trade-analyzer/      # src/analysis (fundamentals, recommendation, scoring, sector)
├── portfolio-analyzer/  # src/ (data, live, portfolio, evaluation, sectors, ...) + config
├── claude-driver/       # orchestration + approval/order logic
└── webgui/              # NEW NiceGUI multi-page app
    ├── main.py          # NiceGUI server + nav shell
    ├── proxy.py         # thin client to schwab-proxy (wraps existing proxy_client)
    └── pages/
        ├── options.py
        ├── sentiment.py
        ├── trade.py
        ├── portfolio.py
        └── driver.py
```

### What gets copied vs excluded

**Copied (active):** all backend `.py` modules, `scoring/`, `src/`,
`options_simulator/`, `scripts/`, `tools/`, `shared/analysis_lib/`,
`repo_paths.py`, `config/ports.toml`, secret `.example` templates + real secrets,
data files the scoring needs (e.g. `Sectors_Industries_ETFs.xlsx`), per-app and
aggregate `requirements.txt`, and the existing **test suites** (pure, keep the
backend verifiable).

**Excluded:** `__pycache__`, `.pytest_cache`, `*.db` / `*.db-shm` / `*.db-wal`,
`logs/`, `*.log`, `docs/plans/` history, `.docx` files, built `frontend/dist`
(React), old Dash `dashboard.py`, Tk desktop entrypoints
(`trade_analyzer.py`, `portfolio_analyzer.py`, `sentiment_dashboard.py`),
`.worktrees/`, runtime/state JSON.

> The excluded UI entrypoints are read from the **source repo** as reference
> while porting each feature to NiceGUI; they are not copied or run here.

## NiceGUI front-end

- One NiceGUI server (new port `nicegui = 8500` in `config/ports.toml`).
- Left-nav / tabbed shell with one page per feature.
- Each page calls the copied backend through `schwab-proxy` (`PROXY_URL`).
- Live-updating views (portfolio streaming, intraday GEX) use NiceGUI
  `ui.timer` / websocket refresh.
- Charts: NiceGUI supports Plotly/ECharts natively — reuse existing chart data
  shapes from the engines.

## Data flow

```
schwab-proxy (:8100)  ──HTTP──>  webgui pages
        │                            │
        └── owns Schwab auth/tokens  └── calls engines (options/sentiment/
            + market data feed            trade/portfolio/driver) which fetch
                                          market data via proxy_client
```

Run order: **schwab-proxy first**, then the NiceGUI app.

## Error handling

- Proxy-down detection: NiceGUI pages show a clear banner if `:8100` is
  unreachable rather than crashing.
- Secrets missing: startup check (reuse `tools/check_env.py`) warns before launch.
- Per-page exceptions are caught and surfaced as `ui.notify` errors.

## Testing

- Copied backend test suites run per-app (`python -m pytest`) exactly as in the
  source repo (entrypoints prepend repo root to `sys.path`).
- New `webgui/` gets lightweight smoke tests (pages import + render without a
  live proxy, using stubbed proxy responses).

## Git + libraries

- `git init` in this folder; carry over `.gitignore`; initial commit includes
  this design doc.
- Create `.venv`; `pip install -r requirements.txt` plus `nicegui`.

## The CLAUDE.md doc

Root `CLAUDE.md` documents the new tech stack (NiceGUI), architecture, the
copied-app map, ports, run order, and secrets handling — **kept updated as the
build progresses** (an explicit, ongoing requirement from the user).
