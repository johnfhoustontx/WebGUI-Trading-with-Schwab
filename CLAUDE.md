# CLAUDE.md — WebGUI Trading with Schwab

Guidance for Claude Code sessions working in this repository. Read this first,
then the per-app `CLAUDE.md` for the folder you are editing.

> **Maintenance:** This document is the living architecture/tech record for the
> project and is **updated regularly** as the build progresses (an explicit
> standing requirement). After any structural change — new page, new dependency,
> port change, copied/removed module — update the relevant section here.

**Last updated:** 2026-06-14 (Phase 2 shell + full Options section incl. Gamma/Simulator built; **Sentiment page built** — composite gauge + component table + 5 tiles + 30d history/rolling-avgs + trend regime + **Sector & Industry Performance** (Day/Week/Month %, P/C, RRG, rotation banner), reusing `history_backfill` + `scoring.rotation`/`sector_perf`; 127 webgui tests green; Trade/Portfolio/Driver pages still stubs — next session. See "webgui development notes" below.)

## What this project is

A **self-contained NiceGUI web GUI** for the Schwab trading stack. It is a fork
of the active backend of the original `D:\Trading With Schwab` monorepo, with the
old per-app UIs (Dash, built React, Tk desktop) **replaced by a single NiceGUI
multi-page web app** (`webgui/`).

The original repo (`D:\Trading With Schwab`) is **reference only** — this project
does not import from it or depend on it at runtime.

## Tech stack

| Layer            | Technology                                                        |
|------------------|-------------------------------------------------------------------|
| Web GUI          | **NiceGUI** (`>=2.0`) — single multi-page app, Python-only        |
| API gateway      | FastAPI + uvicorn (`schwab-proxy`)                                |
| Brokerage SDK    | `schwab-py` (`schwab` package) — auth, market data, streaming     |
| Data / numerics  | pandas, numpy, scipy                                              |
| Scheduling       | APScheduler (claude-driver)                                       |
| Notifications    | winotify (Windows toast)                                          |
| Spreadsheet I/O  | openpyxl                                                          |
| Testing          | pytest                                                            |
| Runtime          | Python 3.11+, Windows-first, single-user                         |

## Architecture

```
schwab-proxy (:8100)  ──HTTP──>  webgui NiceGUI app (:8500)
        │                              │
   owns Schwab auth/                   ├─ Options  page  → options-scanner engines
   tokens + market data               ├─ Sentiment page → sentiment-dashboard scoring
                                       ├─ Trade    page  → trade-analyzer src/analysis
                                       ├─ Portfolio page → portfolio-analyzer src (live)
                                       └─ Driver   page  → claude-driver orchestration
        │
   shared/analysis_lib  ← shared library (schwab_client, market_data, technical, mtf…)
```

**The proxy must be running first.** All feature backends resolve their Schwab
client and market data through `http://127.0.0.1:8100`.

## Folder map (what was copied in)

| Folder                 | Role                                                        | UI status        |
|------------------------|------------------------------------------------------------|------------------|
| `schwab-proxy/`        | Central Schwab API gateway / token manager. **Start FIRST.**| backend, :8100   |
| `options-scanner/`     | GEX/options scanner engines, scoring, paper engine, simulator. | engines only (Dash UI dropped) |
| `sentiment-dashboard/` | Market sentiment `scoring/` + `history_backfill` + `live_composite.py` (live intraday composite + bridge payload) + `publish_bridge.py` (headless bridge writer) + bridge + `sectors_ref.py`. | ported to NiceGUI `/sentiment` |
| `trade-analyzer/`      | `src/analysis` — fundamentals, recommendation, scoring, sector. | engines only (Tk UI dropped) |
| `portfolio-analyzer/`  | `src/` — sector breakdown, vs-sector perf, live streaming.  | engines only (Tk UI dropped) |
| `claude-driver/`       | Morning/intraday orchestration + order approval logic.      | engines only (approval UI to be NiceGUI) |
| `shared/`              | `analysis_lib/` shared library + secret templates/values.   | library          |
| `tools/`               | `check_env.py`, `db_admin.py` maintenance utilities.        | CLI              |
| `webgui/`              | **NEW** NiceGUI multi-page front-end. Shell + Options section built. | the new UI, :8500 |

> The old UI entrypoints (`dashboard.py`, `sentiment_dashboard.py`,
> `trade_analyzer.py`, `portfolio_analyzer.py`, the React `frontend/dist`) were
> **not copied**. When porting a feature to NiceGUI, read those from the source
> repo `D:\Trading With Schwab` for reference.

## webgui structure (NiceGUI app)

`webgui/main.py` is the server + nav shell: a left-nav with expandable
**Options** and **Sentiment** groups (Sentiment children: Sentiment dashboard +
Sector Rotation) plus flat Trade / Portfolio / Driver items. Pages
live in `webgui/pages/`; each leaf exposes `render()` called inside the shell
`_layout`. `webgui/proxy.py` wraps `schwab-proxy/proxy_client.py` and adds
`health()`. Pure transforms / SVG builders are unit-tested (`webgui/tests/`);
heavy engine calls run off-thread via `nicegui.run.io_bound`.

Routes:

| Route | Page | Status |
|-------|------|--------|
| `/` | Options · Scanner (0-4 / 5-15 DTE, two-pane + detail panel) | built |
| `/options/paper` | Paper Trades | built |
| `/options/captured` | Captured Signals | built |
| `/options/portfolio` | Paper Portfolio (paper account) | built |
| `/options/calculator` | Calculator (summary tiles + P&L heatmap) | built |
| `/options/swing` | Swing Scanner | built |
| `/options/gamma` | Gamma (GEX/Charm/DEX/Vanna bars + flip/walls + intraday heatmap) | built |
| `/options/simulator` | Simulator (What-if + IV-shock; Replay TODO) | built |
| `/sentiment` | Sentiment (two-column top: gauge+regime / component table; traffic-light tiles; 30d history + rolling avgs; full-width **Sector & Industry Performance** w/ Day/Week/Month %, P/C, RRG, rotation banner, **expandable industries w/ P/C+RRG**; bottom status bar; **persists across navigation**; **server-side 120s auto-refresh + bridge publish, tab-independent**) | built |
| `/sentiment/rotation` | Sector Rotation (RRG-vs-SPY: Risk-ON/OFF headline + spread, ROTATING FROM/INTO w/ S&P weights, quadrant-map table, **RRG scatter**; reuses `sector_rotation_assessment`; cached, **manual Refresh only**) | built |
| `/trade` `/portfolio` `/driver` | other apps | **stubs** |

The `pages/options/` subpackage shares `header.py` (compact quotes/VIX/sentiment
strip), `detail.py` (collapsible Trade detail panel, reused by all signal
tables), and `svg.py` (speedometer / gradient-bar / range-marker SVG). Options
design + plan: [`docs/plans/2026-06-14-options-section-expansion-design.md`](docs/plans/2026-06-14-options-section-expansion-design.md)
/ [`-plan.md`](docs/plans/2026-06-14-options-section-expansion-plan.md).
Gamma/Simulator: [`docs/plans/2026-06-14-gamma-simulator-design.md`](docs/plans/2026-06-14-gamma-simulator-design.md) / [`-plan.md`](docs/plans/2026-06-14-gamma-simulator-plan.md).

## webgui development notes (read before adding a page)

**Page pattern.** Add a leaf module `webgui/pages/<name>.py` exposing `render()`.
In `webgui/main.py`, add a `@ui.page("/route")` that does `with _layout(active,
title): from pages import <name>; <name>.render()`, and add the item to `NAV`
(flat) — `_layout` handles header, drawer, and the proxy-down banner. Register
the route in `test_shell.py`'s expected set.

**Import an app's engine (sys.path glue).** App folders have hyphens / no package
init, so a page adds the app dir to `sys.path` then imports the module by name —
e.g. `from repo_paths import TRADE_ANALYZER; sys.path.insert(0,
str(TRADE_ANALYZER))` then `import <engine>`. `webgui/conftest.py` already puts
the repo root + `webgui` on `sys.path` for tests. The proxy client is
`proxy.schwab_py_client` (schwab-py compatible) and `proxy.schwab_client`
(SchwabClient compatible).

> **Cross-app module-name collisions (IMPORTANT, bitten us).** Putting multiple
> app dirs on `sys.path` means same-named top-level modules clash process-wide
> (one `sys.modules` entry wins). Known clashes: **`scoring`** (options-scanner
> `scoring.py` vs sentiment-dashboard `scoring/` package) and **`notifier`**.
> Once the Sentiment page loads, `import scoring` resolves to sentiment's, which
> broke `scanner_engine.run_full_scan`'s lazy `from scoring import …`. Mitigation:
> `pages/options/engines.py` `options_scoring()` context manager pins the options
> `scoring` for the duration of an options engine call and restores after — used
> in `scanner.py`/`swing.py`. When wiring Trade/Portfolio/Driver, watch for the
> same trap (e.g. `notifier`); prefer importing engine deps eagerly at module
> load (binds the name once) and/or wrap lazy engine calls similarly.

**Structure for testability.** Keep pure transforms/figure-builders as
module-level functions (TDD them with sample dicts); keep `render()` thin
(widgets + wiring). Heavy/blocking engine calls go through
`await nicegui.run.io_bound(fn, ...)` with a spinner + try/except → `ui.notify`.

**NiceGUI gotchas (learned, costly):**
- `ui.html(...)` **strips `<style>` and `<iframe>`**. For CSS use `ui.add_css(css)`
  (rules only, scope with a class); render HTML *fragments*, not full documents.
  See `pages/options/gamma.py` Explain (`EXPLAIN_CSS` + `wrap_explain`).
- Charts: `ui.plotly(fig_dict)` where `fig_dict={"data":[...],"layout":{...}}`;
  build the dict in a pure function so it's unit-testable. Heatmaps/bars used in
  Gamma; curves/bars in Simulator.
- Tables: `ui.table(columns=[{name,label,field,...}], rows=[...], row_key="id")`;
  selection via `selection="single"` + `table.selected`; row click via
  `table.on("rowClick", handler)` where `event.args[1]` is the row dict.
- Number/select/slider/toggle fire `on_value_change`. Set values with
  `el.value = ...; el.update()`. Auto-refresh + autoload via `ui.timer(secs, fn)`
  and `ui.timer(0.1, fn, once=True)` (see Gamma).
- A page is built per request inside `_layout`; keep page state in a local dict
  closure, not module globals.

**Verify in the browser.** `.claude/launch.json` defines the `webgui` dev server
on **:8500** (`autoPort:false` — the NiceGUI port is fixed). Use the Claude
Preview tool (start `webgui`, screenshot). Restart the preview after code changes
to pick them up. To drive Quasar inputs from the preview, set the native value +
dispatch `input`/`change`/`blur` events.

**Tests:** `cd webgui && ..\.venv\Scripts\python -m pytest -q` (127 green as of
this writing). TDD pure functions; smoke-verify `render()` with a screenshot.

**Environment quirks to expect:**
- The proxy on `:8100` may be the *source* repo's proxy (its `/health`
  `token_file` points at `D:\Trading With Schwab\...`). Fine for reading data;
  just know live data isn't this repo's proxy.
- Weekend / off-hours → sparse 0-DTE option data (e.g. "no non-zero GEX within
  ±2%", swing scans returning 0). Not a bug.
- `options-scanner/data/Top 20.xlsx` (scanner watchlist) is present locally but
  **gitignored** (`data/`), like the real secrets — a fresh clone degrades to
  base symbols. Same applies to the paper-trading DBs (start empty).

**Sentiment (`/sentiment`) — DONE.** `webgui/pages/sentiment.py` reuses the
copied `history_backfill.backfill_history(...)` engine (latest completed-session
composite + 30d history) + `scoring` (`composite.velocity/divergence`,
`trend_regime.classify/commit_state`) + the ported `sectors_ref.load_sectors_data`.
**Layout:** a two-column top region — left: speedometer + bias + size/conf with the
**Market Trend regime** blended beneath it; right: the component **table**
(Value/Score[2dp]/Weight/Conf — Contrib computed for reconciliation but not shown;
credit_pulse excluded per v4.3 `WEIGHTS`). Then 5 summary **tiles**
(Modifier/Bias/Signal/Yesterday/Change) sized down with a **traffic-light background**
(`traffic_color(total)`), a 30d Plotly history (soft grid) + 5d/20d rolling averages
+ velocity/divergence, the full-width **Sector & Industry Performance** table
(11 sectors × Day/Week/Month %, P/C, RRG; per-cell colored; subtle gridlines + row
hover via a `.sent-sectors` `ui.add_css` block) with a rotation banner
(`scoring.rotation.compute_rotation`) + "% green | Cap-wtd | Score" summary, and a
bottom **status bar** (Updated/Next/Sectors/Proxy — proxy checked off-thread in
`load()`, cached, not on the status timer). Each sector **expands** into its industry
sub-rows (▷ toggle or Expand/Collapse All; lazy-fetched via `_load_industries` +
cached; industries show Day/Week/Month % **and P/C + RRG**).
The sector load (`_load_sector_perf`, ~24 proxy calls incl. 11 `/chains` for P/C)
runs at startup + on manual Refresh. **Auto-refresh is server-side and
tab-independent:** a module-level background task (`start_background_refresh` →
`refresh_cache`, started from `main.py` `@app.on_startup`, mirroring
`scanner.start_autoscan`) updates `_CACHE` (composite-only) **and republishes the
bridge every 120 s** regardless of any open/active tab — even with no browser open.
The page **never fetches on activation**: it paints from `_CACHE` instantly and a
fetch-free `ui.timer(120, _repaint_from_cache)` tracks the background cache;
**manual Refresh** is the only page-driven fetch. Persists across navigation via
`_CACHE` (single-user), incl. expanded sectors. Verified against the live proxy to
match the source dashboard exactly (82% green | Cap-wtd +0.70% | Score 7.8/10; real
industry rows on expand). Designs/plans:
[base](docs/plans/2026-06-14-sentiment-page-design.md) /
[sector-perf](docs/plans/2026-06-14-sentiment-sector-perf-design.md) /
[persistence+industries](docs/plans/2026-06-14-sentiment-persistence-industries-design.md)
(+ matching `-plan.md` files).
**Live intraday + bridge (DONE).** `sentiment-dashboard/live_composite.py`
`compute_live(schwab, sector_data)` computes a **live** composite from current
quotes reusing the pure scoring modules (the live analog of
`history_backfill._score_one_day`); `build_bridge_payload(...)` + `bridge.write_bridge`
publish `shared/sentiment_bridge.json` (consumed by `options-scanner/regime_filter`).
The **GEX collector** (`options-scanner/gex_collector.py`) publishes the bridge each
5-min cycle via a **subprocess** (`publish_bridge.py`, sentiment dir on `sys.path[0]`
to dodge the `scoring` package-vs-module collision) — independent of the webgui. The
webgui page uses the live composite during RTH (`is_rth`, Mon–Fri 08:30–15:00 CT),
falling back to the backfill last-session snapshot off-hours, and also publishes the
bridge on each load. Verified live: collector/page write a fresh bridge (score 6.73,
bullish, bull_trend) and `regime_filter.evaluate_regime()` reads it. Design/plan:
[live-bridge](docs/plans/2026-06-14-live-sentiment-bridge-design.md) (+ `-plan.md`).

**Next session — remaining pages (Phase 3.3–3.5 of the webgui plan):**
- **Trade** (`/trade`): `trade-analyzer/src/analysis` — symbol → MTF analysis +
  position/investor verdicts + fundamentals. Source UI: `trade_analyzer.py`.
- **Portfolio** (`/portfolio`): `portfolio-analyzer/src` — sector breakdown,
  vs-sector performance, **live streaming** via `ui.timer` polling the proxy
  stream. Source UI: `portfolio_analyzer.py`.
- **Driver** (`/driver`): `claude-driver/approval_server.py` +
  `morning_agent.py` — orchestration controls + order approval queue.
- Reuse the page pattern above; verify each engine function's real signature in
  the copied module before wiring (explorations of source can drift from copy).
- Optional follow-ups: Simulator **Replay** tab; the Gamma intraday-heatmap
  **collector** process (the page only reads `gex_history_db`).

## Paths and ports: `repo_paths.py` + `config/ports.toml`

`repo_paths.py` at the repo root is the single source of truth for cross-app
paths and ports. Each entrypoint prepends the repo root to `sys.path` and imports
the constants it needs:

```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import PROXY_URL, APPSETTINGS, TOKENS, NICEGUI_PORT  # etc.
```

`config/ports.toml`:

```toml
proxy = 8100
options_analytics = 8200
approval = 8300
dashboard_frontend = 5173
nicegui = 8500            # NEW: the NiceGUI app
```

**Rule: never hard-code `D:\` paths or port numbers in the apps.** Add them to
`repo_paths.py` / `config/ports.toml` and import them.

## Secrets

Live in `shared/` and are **all gitignored**. Real values were copied locally so
the app runs out-of-the-box; only the `*.example.*` templates are committed.

| Real file (gitignored)         | Template                                | Holds               |
|--------------------------------|-----------------------------------------|---------------------|
| `shared/appsettings.json`      | `shared/appsettings.example.json`       | Schwab API keys     |
| `shared/tokens.json`           | `shared/tokens.example.json`            | Schwab OAuth tokens |
| `shared/sentiment_bridge.json` | `shared/sentiment_bridge.example.json`  | Sentiment bridge    |

`schwab-proxy/proxy_tokens.json` and `**/config_notifications.py` are also
gitignored. **Never commit real keys, tokens, or account numbers.**

## Running

```powershell
# 1. Activate the venv
.\.venv\Scripts\Activate.ps1

# 2. Start the proxy FIRST (waits to bind :8100)
python schwab-proxy\schwab_proxy.py

# 3. In another terminal, start the NiceGUI app  (once webgui/ exists)
python webgui\main.py      # serves http://127.0.0.1:8500
```

## Tests

Each app's tests run from **inside that app folder** (entrypoints add the repo
root to `sys.path` at runtime):

```powershell
cd schwab-proxy        ; python -m pytest tests
cd options-scanner     ; python -m pytest tests
cd sentiment-dashboard ; python -m pytest tests
cd trade-analyzer      ; python -m pytest .
cd portfolio-analyzer  ; python -m pytest tests
cd claude-driver       ; python -m pytest .
cd webgui              ; python -m pytest .   # 127 tests: transforms + shell smoke
```

- **options-scanner** has ~2 known date-relative failing tests carried over from
  the source repo — do not "fix" them as part of unrelated work.

## External processes (not in this repo)

The ML prediction servers (MES 8000 / MNQ 8001 / ES 8004 / NQ 8005) and the
options analytics service on 8200 are **separate, external processes**.
claude-driver addresses them over HTTP; this repo does not contain or start them.

## Design / plan docs

- [`docs/plans/2026-06-14-nicegui-webgui-design.md`](docs/plans/2026-06-14-nicegui-webgui-design.md)
- [`docs/plans/2026-06-14-nicegui-webgui-plan.md`](docs/plans/2026-06-14-nicegui-webgui-plan.md)
