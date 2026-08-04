# EquityDeepDive → Trade Analyzer "Deep Dive" button (3-tier, no API)

**Date:** 2026-08-04
**Branch:** `Using_Highcharts`
**Source:** `D:\AI_Based_Analysis\EquityDeepDive\` (external tool, migrated in)
**Target page:** `/trade` (`webgui/pages/trade.py`), service `services/trade_svc`

## Goal

Add a **Deep Dive** button (+ an **AI Query** button) to the Trade Analyzer that, for
the symbol currently in the field, runs the EquityDeepDive quant analysis and opens a
self-contained HTML report in a new browser tab; the AI Query button generates the
chat-prompt (quant digest injected) for the user to paste into a chat. Migrate the
EquityDeepDive scripts into this repo, adapted to the 3-tier architecture. **No
Anthropic API calls** — the "AI note" is a *generated query*, not an API-produced note.

## What EquityDeepDive is

A standalone Schwab-API equity toolkit (see its README). Two engines on a shared data
pull, both routing through SchwabProxy `:8100`:

- **`equity_deep_dive.py`** (1,866 lines) — quant core: `analyze_symbol()` → result
  dict; `render_html()` → a self-contained HTML report. Computes trend/momentum
  technicals, fundamentals (incl. short interest), and options analytics (ATM IV,
  implied move, put/call, max pain, 25Δ skew, IV term structure, constant-maturity 30d
  IV, net GEX/flip, OI walls), plus IV/RV rank via the SQLite store.
- **`iv_history.py`** (419 lines) — SQLite persistence for IV/RV history. Schwab serves
  no IV history, so IV rank **builds forward** from the first run (≥20 snapshots); RV
  rank works immediately from price history.
- **`make_chat_prompt.py`** (235 lines) + **`chat_query_template.md`** — the no-API
  path: inject the quant digest into a prompt template (only `{{SYMBOL}}`, `{{TODAY}}`,
  `{{QUANT_DATA}}` substituted) → a markdown file to paste into a chat.
- **`ai_analyst.py`** (1,123 lines) — the Anthropic API path. **Excluded** (no API).

The `SchwabClient` uses `/passthrough` (verified present on this repo's proxy) because
the proxy's `/quotes` drops fundamentals; the app's proxy exposes both `/passthrough`
and `/instruments`, so the fetcher works against `:8100` essentially unchanged.

## Decisions (user-approved)

- **Respect the 3-tier boundary; no API calls, only a query is generated.** → engine
  lives in a Tier-2 service; `ai_analyst.py` is not migrated; the webgui stays engine-free.
- **Button scope: both** — the quant report (free/fast) **and** an optional AI *query*.
- **IV history: on-demand only** — migrate the store + record per run; defer the daily job.
- Service home: **extend `trade_svc`**. UI: **two buttons**. DB: **fresh**.

## Design

### 1. Migration — Tier-2 subpackage
```
services/trade_svc/deepdive/
  __init__.py
  engine.py              ← equity_deep_dive.py (compute fns + analyze_symbol + render_html;
                            CLI main()/argparse/logging.basicConfig stripped; render_html
                            adapted to RETURN the HTML string, not write to reports/)
  iv_history.py          ← iv_history.py (DB path from repo_paths, not ./iv_history.db)
  chat_prompt.py         ← make_chat_prompt.py builder (JSON/result dict + template → md)
  chat_query_template.md ← the template (sits beside chat_prompt.py, per its contract)
```
Adaptations, kept minimal (preserve the tested compute):
- `PROXY_BASE` → `repo_paths.PROXY_URL`; drop `--direct`/`TOKEN_PATH` (always proxy mode).
- `render_html(...)` returns the HTML string (the service caches it; no `reports/` disk write).
- `iv_history` DB path → `repo_paths.IV_HISTORY_DB`.
- `chat_prompt` locates its template beside itself (unchanged contract) and takes the
  in-memory result dict (skip the JSON-file round-trip the CLI uses).

### 2. Service wiring (`services/trade_svc`)
- `compute.run_deep_dive(symbol) -> {"html", "symbol", "ts"}` — build a proxy `SchwabClient`,
  open the iv_history conn, `analyze_symbol` → `render_html`. Defensive: returns an error
  HTML page on failure (never raises), mirroring the Gamma-analyze degrade pattern.
- `compute.build_deep_dive_query(symbol) -> {"markdown", "symbol", "ts"}` — run the deep
  dive (or reuse the last result) → inject the digest into the template.
- `handlers`: `deepdive` and `deepdive_query` commands → `cache:trade:deepdive` /
  `cache:trade:deepdive_query` (+ published events). `_FIELDS`/contract: these are new
  standalone cache keys (loose `{html|markdown, symbol, ts}` dicts), not part of
  `TradeAnalysis`. On-demand only (no scheduler change).

### 3. Webgui (Tier-1, engine-free)
- `webgui/pages/trade.py`: **Deep Dive** + **AI Query** buttons (themed `cv2-btn`),
  each enqueues its command for the current symbol and version-polls its cache key.
  - Deep Dive: on a new version, open `/trade/deepdive?v=…` in a new tab (like
    `_watch_analyze` on the Gamma page).
  - AI Query: on a new version, show the markdown in a **copyable dialog** (Copy button)
    — or open `/trade/deepdive-query?v=…` raw. (Dialog preferred: pasting into a chat is
    the point.)
- `webgui/main.py`: `@app.get("/trade/deepdive")` serves `cache:trade:deepdive`'s HTML
  raw (`HTMLResponse`), mirroring `/options/analyze`; `@app.get("/trade/deepdive-query")`
  serves the markdown as `text/plain` (fallback for the dialog).

### 4. Paths / config / deps
- `repo_paths.py`: add `IV_HISTORY_DB` (under `services/trade_svc/data/`, gitignored) and
  a deepdive data dir constant if needed. `services/trade_svc/data/` is `.gitignore`-d.
- **No new pip deps** (pandas/numpy/requests already present; no `anthropic`).

### 5. Data flow
`Deep Dive click → bus_client.request("trade", {"type":"deepdive","args":{"symbol"}}) →
trade_svc handler → compute.run_deep_dive → cache:trade:deepdive {html} → page version-poll
→ open /trade/deepdive?v in new tab (raw HTML)`. AI Query is the same shape →
`cache:trade:deepdive_query {markdown}` → copyable dialog.

### 6. Error handling
Every layer degrades, never raises: proxy/token dead → an error HTML page (the tool's own
`/health` preflight message) served in the tab; thin history / no options → the engine's
existing "n/a"/"building" states; a cold service → the page's "waiting for service" note.

### 7. Testing
- `services/trade_svc/tests`: `run_deep_dive` returns HTML + records an iv_history snapshot
  (mock the `SchwabClient` fetch methods with a canned quote/history/chain); `build_deep_dive_query`
  injects `{{SYMBOL}}`/`{{TODAY}}`/`{{QUANT_DATA}}` and strips the HOW-TO block; a couple
  of engine compute fns (e.g. `rsi`, `atm_straddle`, `max_pain`) pinned on canned frames.
- `webgui/tests`: the `/trade/deepdive` route smoke test + any pure button helpers; render
  stays smoke-tested.
- **Live verify:** restart `trade_svc`, enqueue `deepdive` for a real symbol via Redis,
  confirm `cache:trade:deepdive` HTML populates + a snapshot lands in the DB; open the
  report + query in the browser.

## Scope excluded (YAGNI)
- `ai_analyst.py` / any Anthropic call (no API).
- The daily IV-snapshot scheduled job (deferred).
- Multi-symbol / watchlist (button is single-symbol = the `/trade` field).
- The `reports/` disk archive (cache-served instead).

## Risk
Moderate. The engine is large but its compute is self-contained and reused as-is (low
regression risk to the app); the integration surface (service wrapper + two commands +
two serve routes + two buttons) is small and follows the proven Gamma-analyze pattern.
The main unknowns are internal signatures of `render_html`/`make_chat_prompt` (verified
during implementation) and adapting the DB/proxy/template paths.
