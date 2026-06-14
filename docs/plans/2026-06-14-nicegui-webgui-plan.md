# NiceGUI Web GUI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stand up a self-contained NiceGUI web app that copies the active backend of the `Trading With Schwab` monorepo and surfaces Options / Sentiment / Trade / Portfolio / Driver features in one multi-page UI.

**Architecture:** Copy each app's active Python backend (engines, `scoring/`, `src/`, `shared/analysis_lib/`) plus the path/port glue into this repo, excluding old UIs (Dash/React/Tk), caches, DBs, logs, and docs history. Build a single NiceGUI server (`webgui/`) whose pages call the copied engines through the central `schwab-proxy` (:8100). Keep the copied test suites runnable.

**Tech Stack:** Python 3.11+, NiceGUI, FastAPI/uvicorn (proxy), schwab-py, pandas/numpy/scipy, pytest. Source repo (reference only): `D:\Trading With Schwab`.

---

## Phase 0 — Repo skeleton & glue (mechanical, no TDD)

### Task 0.1: Copy path/port glue
**Files:** Copy `repo_paths.py`, `config/ports.toml` from source into repo root.
- Add `nicegui = 8500` to `config/ports.toml`.
- Add `NICEGUI_PORT` / `NICEGUI_URL` to `repo_paths.py`.
- **Verify:** `python -c "import repo_paths; print(repo_paths.PROXY_URL, repo_paths.NICEGUI_PORT)"`.
- **Commit.**

### Task 0.2: Copy `shared/`
**Files:** Copy `shared/analysis_lib/` (exclude `__pycache__`), all `*.example.*`
templates, and the **real** `appsettings.json` / `tokens.json` / `sentiment_bridge.json`
(gitignored — confirm `git status` does NOT list them).
- **Verify:** `git status` shows analysis_lib + examples staged, real secrets ignored.
- **Commit** (examples + analysis_lib only).

### Task 0.3: Copy `schwab-proxy/`
**Files:** Copy all `.py` (`schwab_proxy.py`, `proxy_client.py`, `stream_bridge.py`, `perf_writer.py`, `trade_detector.py`, `trade_registry.py`), `tests/`, `CLAUDE.md`, `Launch_Proxy.bat`. Exclude `proxy_tokens.json`, `__pycache__`, `.pytest_cache`, `logs/`, `*.log`.
- **Commit.**

### Task 0.4: Copy `options-scanner/` backend
**Files:** Copy all root `.py` engine modules (scanner, scanner_engine, gex_*, scoring, signal_*, paper_*, options_calculator, iv_*, fill_model, regime_filter, dealer_pinch, event_calendar, watchlist, theme, html_render, ai_prompt_builder, trades_db, trade_performance_db, etc.), `options_simulator/`, `scripts/`, `tools/`, `tests/`, `config_*.example.py`, `CLAUDE.md`, `requirements.txt`.
**Exclude:** `dashboard.py` (Dash UI), `frontend/`, `*.db*`, `*.docx`, `daily-gex-briefing.html`, `docs/plans/`, `__pycache__`, `.pytest_cache`, `logs/`, `*.log`, `config_notifications.py` (gitignored real config).
- **Commit.**

### Task 0.5: Copy `sentiment-dashboard/` backend
**Files:** Copy `scoring/`, `bridge.py`, `headless_snapshot.py`, `history_backfill.py`, `market_calendar.py`, `notifier.py`, `sector_rotation_assessment.py`, `validate_new_symbols.py`, `Sectors_Industries_ETFs.xlsx`, `tests/`, `CLAUDE.md`, `config_notifications.example.py`, `README.md`.
**Exclude:** `sentiment_dashboard.py` (UI), runtime `sentiment_*.json`/`.log`, `rotation_*.json*`, `ai_config.json`, `__pycache__`, `.pytest_cache`.
- **Commit.**

### Task 0.6: Copy `trade-analyzer/` + `portfolio-analyzer/` backends
**Files:** trade-analyzer: copy `src/`, `tests/`, `pytest.ini`, `requirements.txt`, `CLAUDE.md`, `README.md` (exclude `trade_analyzer.py` Tk UI, `*.log`). portfolio-analyzer: copy `src/`, `config/`, `tests/`, `requirements.txt`, `CLAUDE.md` (exclude `portfolio_analyzer.py` Tk UI, `data/`, `*.log`).
- **Commit.**

### Task 0.7: Copy `claude-driver/` + root `tools/`
**Files:** claude-driver: copy all `.py` (approval_server, morning_agent, intraday_monitor, order_executor, order_preview, trade_selector, feature_engineer, perf_report, config), `tests/`, `requirements.txt`, `CLAUDE.md`, `README.md`. root `tools/`: copy `check_env.py`, `db_admin.py`, `tests/`, `README.md`.
- **Commit.**

### Task 0.8: Consolidated requirements + venv + libraries
**Files:** Copy root `requirements.txt`; append `nicegui`.
- Create `.venv`: `python -m venv .venv`
- Install: `.venv\Scripts\python -m pip install -r requirements.txt`
- **Verify:** `.venv\Scripts\python -c "import nicegui, fastapi, pandas, scipy; print('ok')"`
- **Commit** updated `requirements.txt`.

### Task 0.9: Backend sanity — run a copied test suite
- Run `cd trade-analyzer && ..\.venv\Scripts\python -m pytest -q` (pure, no proxy).
- Expected: green (175 tests per source notes).
- If imports fail, fix `sys.path` glue before proceeding. **No commit unless fixes made.**

---

## Phase 1 — CLAUDE.md + .claude

### Task 1.1: Write root `CLAUDE.md`
Document: new tech stack (NiceGUI), architecture diagram, copied-app map + ports,
run order (proxy → nicegui), secrets handling, test commands, and a
"**Keep this updated**" maintenance note. **Commit.**

### Task 1.2: `.claude/settings.json`
Adapt source `.claude/settings.json` (drop machine-specific bits). **Commit.**

---

## Phase 2 — NiceGUI shell (TDD)

### Task 2.1: `webgui/proxy.py` — proxy client wrapper
**Files:** Create `webgui/proxy.py`, `webgui/tests/test_proxy.py`.
- **Step 1 (test):** `test_proxy_url_from_repo_paths` — asserts wrapper reads `PROXY_URL`; `test_proxy_health_handles_down` — stubbed `requests` raising ConnectionError returns `{"up": False}`.
- **Step 2:** run → FAIL.
- **Step 3:** implement thin wrapper over `schwab-proxy/proxy_client.py` + a `health()` helper.
- **Step 4:** run → PASS. **Step 5:** commit.

### Task 2.2: `webgui/main.py` — nav shell
**Files:** Create `webgui/main.py`, `webgui/tests/test_shell.py`.
- **Step 1 (test):** import `main`, assert it registers 5 routes (`/`, `/sentiment`, `/trade`, `/portfolio`, `/driver`) without starting the server (use NiceGUI test harness / `ui.page` registry).
- **Step 2:** FAIL. **Step 3:** build left-nav shell + empty page stubs + proxy-down banner using `proxy.health()`. **Step 4:** PASS. **Step 5:** commit.

### Task 2.3: Launch script + smoke run
**Files:** `start_all.bat` (proxy → nicegui), `webgui/README.md`.
- Manual smoke: start proxy, start `webgui/main.py`, confirm `http://127.0.0.1:8500` loads shell. Screenshot via /run if available. **Commit.**

---

## Phase 3 — Feature pages (TDD, one per page)

Each page follows the same loop: write a test that the page imports + renders with
a **stubbed** proxy, verify FAIL, port the data flow from the source UI entrypoint
(read from `D:\Trading With Schwab`), render with NiceGUI widgets/charts, verify
PASS, manual smoke against live proxy, commit.

### Task 3.1: Options page (`webgui/pages/options.py`)
Port from source `options-scanner/dashboard.py`. Surface GEX levels + scanner
results table + signal detail. Charts via NiceGUI Plotly/ECharts.

### Task 3.2: Sentiment page (`webgui/pages/sentiment.py`)
Port from source `sentiment_dashboard.py` + `headless_snapshot.py`. Composite
score, sub-scores (breadth, put/call, vix, rotation, credit), sector rotation.

### Task 3.3: Trade analyzer page (`webgui/pages/trade.py`)
Use `trade-analyzer/src/analysis`. Symbol input → MTF analysis + position/investor
verdicts + fundamentals.

### Task 3.4: Portfolio page (`webgui/pages/portfolio.py`)
Use `portfolio-analyzer/src`. Sector breakdown, vs-sector performance, **live
streaming** via `ui.timer` polling proxy stream.

### Task 3.5: Driver page (`webgui/pages/driver.py`)
Port from `claude-driver/approval_server.py` + `morning_agent.py`. Orchestration
controls + order approval queue.

---

## Phase 4 — Finalize

### Task 4.1: webgui smoke tests pass
Run `cd webgui && ..\.venv\Scripts\python -m pytest -q`. Green. **Commit.**

### Task 4.2: Update `CLAUDE.md`
Reflect final page list, any port/flow changes. **Commit.** (Recurring per the
user's "update regularly" requirement — repeat after each phase.)

### Task 4.3: Final review
Use superpowers:requesting-code-review before declaring done.

---

## Notes
- DRY: reuse copied engines; do not reimplement scoring/data logic in `webgui/`.
- YAGNI: pages render existing computed data; no new analytics in this project.
- Secrets never committed (verify `git status` each phase).
- Run order always: **schwab-proxy first**.
