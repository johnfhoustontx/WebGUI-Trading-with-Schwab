[TOC]

# About this document

This is the **calculation and architecture reference** for the WebGUI Trading
with Schwab stack. It documents *how every number is derived* — the formulas,
weights, thresholds, lookback windows, and cadences used across the sentiment,
options, trade, and portfolio engines.

It is an **offline maintainer reference**, not an end-user guide. For task-oriented
usage see the *User Guide*; for the inter-service integration surface see the
*API / Developer Reference*.

> **Source-of-truth note.** Constants in this document were read from the code
> (file paths are given throughout). Where a value lives in a single canonical
> place — e.g. `sentiment-dashboard/scoring/__init__.py:WEIGHTS` — that file
> governs; if you change it there, update this document.

## Finding the math behind a screen

**This document is organised by engine, not by menu**, and deliberately so: one
engine feeds several screens. The GEX chapter alone supplies Dealer Positioning,
the Opportunity Board, Flow Alerts and Rescue's context reads — ordering by menu
would mean writing the same formulas out four times and letting the copies drift.

Use this map to get from a screen to its numbers. Menu order matches the rail.

| Menu page | Chapters that derive its numbers |
|---|---|
| **Desk** | Composition only — every figure is produced by the same function that produces it on the page it summarises, so follow that page's row. The one exception is the **Market read** popup, whose verdicts are decided in the market service: see *Market read*. Its own two constants (the arrival glow, the voice cache) are in the *Constants Appendix* |
| **Symbol** | Composition, like the Desk. Its own arithmetic — IV vs HV and the expected move — is in *Options Scoring* → **Expected move and IV analysis**; the signal age and score trend in **Signal age and score trend**; the look-up's cost in the *Constants Appendix* |
| **Dealer Positioning** | *GEX / Gamma* · *Black-Scholes & the Simulator* (the Greeks behind charm and vanna) |
| **Opportunity Board** | *GEX / Gamma* (the flip and flow series) · *Options Scoring* (its signal counts) |
| **Flow Alerts** | *GEX / Gamma* (the premium series, **Hedging-flow model (HIRO)** for the two hedging alerts, and **Bought / sold estimate on flow alerts**) · *Constants Appendix* (detector thresholds) |
| **Market Dashboard** | *Architecture Overview* — the board normalizes and colours quotes rather than deriving anything |
| **Sentiment** | *Sentiment Calculations*, including the composite blend, the intraday trend, and the blended market regime |
| **Sector & Industry** | *Sentiment Calculations* → **Sector Performance** |
| **Sector Rotation** · **RRG** | *Sentiment Calculations* → **Rotation** |
| **Momentum** | *Sentiment Calculations* (the nightly cascade; see also the cadence table) |
| **Calculator** · **Simulator** | *Black-Scholes & the Simulator* |
| **Market Scanner** | *Options Scoring* · *Technical Indicators* |
| **Strategy Finder** | *Options Scoring* (the Fit + Quality score) |
| **Expected Move** | *Options Scoring* → **Expected move and IV analysis** |
| **Captured Signals** · **Paper Ledger** · **Paper Account** | *Options Scoring* (entry quality) · *Rescue Tested Trades* (the management rules) |
| **Rescue** | *Rescue Tested Trades* |
| **Trade Analyzer** | *Trade Analyzer* · *Technical Indicators* |
| **Portfolio** | *Portfolio Analytics* |
| **EOD Report** | Aggregation only; it computes nothing of its own |
| **Blog** | *Site Blog* — the limits, what cleaning removes, the typeface copy, the cadence |

---

# Prerequisites

Everything required to run the stack successfully. Items marked **Required** must
be in place or the app will not start (or will start degraded in an obvious way);
**Optional** items disable a specific feature when absent, by design — the code
degrades rather than crashes.

## Platform and runtime

| Requirement | Detail | Status |
|-------------|--------|--------|
| **Operating system** | **Ubuntu Server 24.04 LTS.** The stack runs as ten `systemd --user` units; there are no launcher scripts. `loginctl enable-linger` is what makes them start at boot and survive logout. | Required |
| **Python** | **3.11+** (developed/tested on **3.11.9**; CI pins **3.11**; `ruff` targets `py311`). | Required |
| **Virtual environment** | A venv at the repo root: **`.venv`**. The launchers resolve `\.venv/bin/python.exe` explicitly and abort if it's missing. | Required |
| **Browser** | Any modern browser for the web GUI at `http://127.0.0.1:8500`. | Required |
| **`uv`** | Installs Python 3.11 alongside the distro's. The lock is resolved against 3.11, and the system Python is externally-managed (PEP 668). | Required |

Create the environment:

```powershell
python -m venv .venv
.\.venv/bin/python -m pip install -r requirements.txt
```

## Python dependencies

`requirements.txt` is the human-readable **direct-dependency** list (the union of
all apps). `requirements.lock` is a fully-pinned, byte-identical environment
(`pip freeze`) — install that for reproducibility. `requirements-dev.txt` holds
tooling (`ruff`, `pre-commit`, `pip-audit`, `pytest-cov`) and is **not needed to
run** the app.

⚠ **Regenerate the lock with `pip freeze --all`, not plain `pip freeze`.** The lock pins
**`setuptools`** and **`pip`** even though nothing imports them at runtime, because
`pip-audit` audits the whole environment and an unpinned tool is a package whose version
differs between machines — which is how four setuptools CVEs sat unnoticed until
2026-08-19, and how prod was still on **pip 24.0 (six advisories)** that same day while
dev audited clean. Plain `pip freeze` **omits both by default** (verified), so
regenerating the lock that way silently drops the pins and quietly reopens the gap.

Load-bearing runtime packages:

| Package | Role |
|---------|------|
| `nicegui[highcharts]>=2.0.0` | The web GUI and every chart/gauge. |
| `fastapi==0.137.0`, `uvicorn==0.49.0`, `starlette==1.3.1` | The proxy + the seven services. |
| `redis==8.0.0` | Client for the Redis backbone. `fakeredis>=2.20` backs the tests (no live server needed). |
| `pydantic>=2.0` | The typed cross-tier contracts. |
| `schwab-py==1.5.1` | Schwab auth / market data / streaming. |
| `requests==2.34.2`, `httpx==0.28.1` | HTTP clients. |
| `pandas>=2.0`, `numpy>=1.24`, `scipy` | Analytics; `scipy.stats.norm` powers Black-Scholes. |
| `openpyxl` | Reads the sector/watchlist workbooks. |
| `feedparser==6.0.11` (+ `sgmllib3k`) | `news_svc` parses the public RSS / Atom feeds it polls. |
| `lxml==6.1.1`, `tinycss2==1.5.1` (+ `webencodings`) | `blog_svc` parses a submitted entry and tokenizes its CSS. Both were already installed as dependencies of NiceGUI; they are named because the cleaner now imports them itself. |
| `anthropic==0.112.0` | Claude tool-use calls (imported lazily — the suite runs without it configured). |
| `matplotlib`, `Pillow`, `yfinance` | Charts/imaging, optional fallback data. Notifications are Telegram / Discord / SMS-over-SMTP / X — all HTTP or SMTP, no OS hooks. |

> **Licensing — read this.** `nicegui[highcharts]` pulls in **Highcharts**, which is
> free for **personal / non-commercial use only**. Commercial use requires a paid
> Highcharts license. This is a licensing prerequisite, not a technical one.

## Redis (the bus backbone)

| Requirement | Detail | Status |
|-------------|--------|--------|
| **Redis running on `:6379`** | `sudo systemctl enable --now redis-server`. It is the Tier-3 cache, pub/sub, and command bus — **without it none of the seven services can publish and every page shows a "Waiting for … service" placeholder.** It is a **system** unit, so a `systemctl --user` stop of the stack cannot reach it: it survives a Stop All by construction, not by a filter. | Required |
| `MEMURAI_PASSWORD` | Optional AUTH. Unset = no AUTH (the default, unchanged behavior). | Optional |

## Schwab API credentials

The proxy owns all Schwab authentication; no other process holds credentials.

| Requirement | Detail | Status |
|-------------|--------|--------|
| **Schwab developer account + registered app** | Yields an **App Key** and **App Secret**. Register the callback URL as **`https://127.0.0.1:8182`**. | Required |
| **`shared/appsettings.json`** | Copy `shared/appsettings.example.json` and fill `Schwab.AppKey` / `Schwab.AppSecret` (both default to `REPLACE_ME`). **Gitignored.** | Required |
| **`shared/tokens.json`** | The OAuth tokens (`AccessToken` / `RefreshToken` / expiry). Created by the first authorization — copy `shared/tokens.example.json` if you need the shape. **Gitignored.** | Required |
| **First-time authorization** | Start the proxy, then use the **Authorize** button on the app's **System Status** page and complete the Schwab login. The button opens `{proxy_public_url}/auth` (`repo_paths.PROXY_PUBLIC_URL`, from `config/env.local.toml`) — on the VPS the `tailscale serve` address of `:8100`; unset, `http://127.0.0.1:8100/auth`, reachable only on the same machine or through the SSH tunnel. | Required |

> **Token lifetimes matter operationally.** An **expired access token is normal** —
> the proxy refreshes it automatically. An **expired refresh token is fatal** to
> live data and requires re-authorizing via `/auth`. The proxy's `/health` reports
> `has_token`, `token_expired`, and `refresh_token_expired`, and the System Status
> page surfaces this as its own card.

## Anthropic API key (AI features)

| Requirement | Detail | Status |
|-------------|--------|--------|
| **`ANTHROPIC_API_KEY`** | Resolution order: the **env var** first, then a gitignored **`shared/anthropic_key.txt`**. Powers the Gamma **Analyze**/**Explain** infographics and the **fallback** for the 4×/day auto-briefings. | Optional |
| **Claude Code CLI + subscription token** | The 4×/day auto-briefings (both phases — news research and the analysis) run on the **Claude subscription** through `claude -p` instead of the API key, via `services/options_svc/claude_cli.py`. Needs the CLI at **`CLAUDE_CLI_PATH`** (default `~/.local/bin/claude`) and a 600 file at **`CLAUDE_CLI_TOKEN_FILE`** (default `~/.config/neuralstrike/claude.env`) holding `CLAUDE_CODE_OAUTH_TOKEN=…` from `claude setup-token`. **`BRIEFING_ENGINE=api`** in options_svc's environment switches it off. If either is missing the briefings use the API key exactly as before; if a CLI run fails, that one call is repeated on the API key. The ad-hoc **Analyze** button stays on the API key. | Optional |

Without a key those features **degrade safely** — the Gamma infographics render
a readable "no key" page.

The **Settings → API usage** Claude count is a count of calls **billed to the API
key**. A briefing call that ran on the subscription is not counted; one that fell
back to the key is.

## Data files

| File | Role | Status |
|------|------|--------|
| `sentiment-dashboard/Sectors_Industries_ETFs.xlsx` | The sector/industry ETF reference map, loaded once at startup by the sentiment engine. | Required |
| `options-scanner/data/Top 20.xlsx` | The scanner watchlist (also the GEX collection universe). **Gitignored** — a fresh clone degrades to the base index symbols (`$SPX`/`$VIX`/`SPY`/`QQQ`). | Optional |

The SQLite stores (paper-trading books, `gex_history.db`, signal DBs) are created
automatically and start empty.

## Ports that must be free

Ports come from `config/ports.toml` via `repo_paths.py` — never hard-coded.

| Port | Process | Status |
|------|---------|--------|
| 6379 | Redis | Required |
| 8100 | schwab-proxy — **start first**, everything depends on it | Required |
| 8210 | sentiment_svc | Required |
| 8211 | options_svc | Required |
| 8212 | portfolio_svc | Required |
| 8213 | trade_svc | Required |
| 8215 | market_svc | Required |
| 8216 | news_svc | Required |
| 8217 | blog_svc | Required |
| 8500 | webgui (NiceGUI) | Required |
| 8501 | webgui_live — the PUBLIC read-only screens, a second NiceGUI process | Required |

Those, plus Redis on 6379, are every port `config/ports.toml` lists. (The legacy
`options_analytics`, `approval`, `dashboard_frontend` and `[ml_servers]` entries
were removed in September 2026 — nothing in the stack talks to those processes.)

## Optional integrations

| Feature | Requirement |
|---------|-------------|
| **Push notifications** | Gitignored `shared/notifications.json` (template: `shared/notifications.example.json`). Telegram needs a bot token + chat id; Discord needs a channel webhook URL; Google Fi SMS needs your 10-digit Fi number plus a Gmail **App Password**. Each channel self-gates — missing creds are a silent no-op. Env vars override file values. **Per-category on/off** lives in `config/notify.toml` (`[channels.<category>] discord / telegram`, all on by default; Settings → General writes `config/local/notify.toml`), enforced in `shared/notify/channels.discord_target` / `telegram_target` and read at send time (mtime-cached, no restart). The trade idea's optional **Google Calendar event** (`channels.trade_idea.calendar`, `[calendar] calendar_id / lead_min / duration_min`) is created by `shared/notify/gcal.py` with the service-account key `shared/google_calendar_sa.json`; it uses the calendar's own default notification (`reminders.useDefault`). Each **posted** trade idea is also written into the public site by `services/options_svc/site_ideas.py` — `deploy/site/ideas/<day>/<HHMM>-<SYMBOL>.{png,webp}` plus the manifest `deploy/site/ideas.json` (gitignored), keeping the newest `[site] keep_days` posting days (default 6); `deploy/site/assets/ideas.js` draws the home page strip and `ideas.html` from it. Each idea's **result** applies the app's exit rules (`trade_mgmt.structure_rules`): target = `tp_frac` × the debit (single long option; else max profit) or × the credit; stop = `debit_stop_frac` × the debit, or `stop_mult` × the credit where `loss_rules`. A backspread has no target and no stop and is held to expiry, as the app's tracked rows hold it (`structure_marks.open_ended` with a sold leg). The option is modelled — Black-Scholes (`options_calculator`) at the IV solved from the entry price at the post — on every 1-minute stock bar (`/pricehistory`, incremental from `checked_to`); stop before target, a stop gapped through fills at the bar's open, a target at the level. Unhit ideas settle at intrinsic on the expiry-day daily close. Refreshed every `[site] refresh_min` (default 15) inside 08:00–15:15 CT: one batched `/quotes` call plus one minute-history call per symbol. No option quote is read. % = result ÷ max loss. |
| **Proxy hardening** | `PROXY_SHARED_SECRET` (**required** by the account routes `/accounts`, `/positions`, `/transactions`: with none set they refuse every caller; there is no order route) and `PROXY_CORS_ORIGINS` (overrides the local allowlist). See `docs/SECURITY.md`. |

## Startup order

The dependency chain is strict: **Redis → schwab-proxy → the seven services → webgui.**
Services wait on the proxy because they resolve market data through it (`news_svc`
and `blog_svc` call no proxy — one reads public feeds, the other a local store — but
both are ordered with the others).
`webgui_live` sits outside that chain: it reads Redis and nothing else — no proxy
call, no Schwab call, no service call — so it is ordered after nothing in the target.

| Launcher | Behavior |
|----------|----------|
| `systemctl --user start trading-prod.target` | Proxy + 7 services + webgui + webgui_live. Also starts at boot. |
| `systemctl --user stop trading-prod.target` | Stops all ten, the public live screens included. **Redis survives** — it is a system unit this cannot reach. |
| `systemctl --user restart trading-prod-options_svc` | One component. This is exactly what the Status page's Restart button runs. |
| `journalctl --user -u trading-prod-webgui -f` | Logs. Replaces the `logs/*.out.log` redirection. |
| `.venv/bin/python -m deploy.systemd.generate_units --install` | Regenerate the units after a port, path or identity change. Also reloads systemd and **arms every timer it wrote** — a written `.timer` that nothing enables never fires. Dev arms nothing, by design. |

> The nine processes must stay **separate OS processes**. Merging services into one
> Python process would re-introduce the top-level module-name collisions
> (`config` / `scoring` / `notifier` / `src`) that the 3-tier split exists to prevent.

## Verifying the install

1. Open **`http://127.0.0.1:8500/status`** — the System Status page probes Redis,
   the proxy, Schwab authorization, all seven services, the webgui and the public
   live screens, plus a data-freshness table.
2. Or probe directly: `GET http://127.0.0.1:8100/health` and
   `GET http://127.0.0.1:82{10..13}/health`, `:8215/health`, `:8216/health` and `:8217/health` (each returns `{"domain": …, "up": true}`).
3. Run the tests **one folder at a time** (never `pytest services` across all of
   them — that re-triggers the module-name collisions):

```powershell
.venv/bin/python -m pytest services\options_svc
cd webgui ; ..\.venv/bin/python -m pytest -q
```

## Operational notes

- **Off-hours data is legitimately sparse.** On weekends and outside market hours
  0-DTE scans can return nothing and Gamma may show no fresh data. That is expected,
  not a failure.
- **`gex_history.db` grows.** A daily retention purge keeps the last 5 sessions, but
  `DELETE` reuses pages without shrinking the file — reclaim space with the offline
  `tools/vacuum_gex.py` (or **Settings → Maintenance → Vacuum GEX history DB**), which
  refuses to run while the collector is active.

---

# Architecture Overview

## The three tiers

The stack is split into three physically separate tiers communicating over a local
Redis (Redis) backbone. No two Tier-2 services talk to each other directly.

```
TIER 1  GUI         webgui/ NiceGUI app (:8500). Renders pages, reads Redis cache,
                    subscribes to events, enqueues commands. No engine imports.
                    webgui/live_main.py (:8501) is a SECOND Tier-1 process: the
                    same page modules, published read-only and unauthenticated.
                    It reads and subscribes; it enqueues nothing.
   ▲ cache read / subscribe                │ commands
TIER 3  STORE+COMM  Redis (:6379): cache:{domain}:{view}, events:{domain}:{view}
                    pub/sub, cmd:{domain} command streams. shared/contracts (typed
                    payloads) + shared/bus (redis wrapper).
   ▲ publish                               │ consume
TIER 2  SERVICES    services/{domain}_svc FastAPI (sentiment/options/portfolio/
                    trade/market). Each imports only its engines, owns its
                    scheduler + command consumer, validates + caches + publishes.
                    Calls schwab-proxy (:8100) for market data.
```

## Process map and ports

| Process | Port | Role |
|---------|------|------|
| schwab-proxy | 8100 | Schwab auth/token manager + market-data gateway. Start first. |
| Redis | 6379 | Cache, pub/sub, command streams. |
| sentiment_svc | 8210 | Sentiment composite, trend, market regime, rotation, nightly momentum cascade. |
| options_svc | 8211 | Scans, paper trading, gamma collection, flow alerts, calculator, simulator, expected move, rescue. |
| portfolio_svc | 8212 | Holdings, sectors, performance, live P&L stream. |
| trade_svc | 8213 | On-demand single-symbol analysis + deep dive; the daily watchlist dividend pull. |
| market_svc | 8215 | Live macro-ticker Market Dashboard (~3 s RTH poll). Its /ES and /NQ tiles quote the front-month contract: quarterly (H, M, U, Z), expiring the third Friday of the month or the session before when that Friday is a closure, and switched `[futures] roll_days_before_expiry` days (8) before expiry — `shared/futures.py`. |
| news_svc | 8216 | Market News: polls free public RSS / Google News / Yahoo / SEC EDGAR feeds and the economic calendar (Fed, BLS, BEA, FRED, Nasdaq) into `news.db`. No Schwab, no Claude, no proxy. |
| blog_svc | 8217 | The site Blog: cleans an uploaded HTML document, keeps drafts and entries in `blog.db` and the files beside it, and writes published entries into the served site. No Schwab, no Claude, no proxy; the one outside request is the typeface copy from Google Fonts at upload. |
| webgui | 8500 | The web UI. |
| webgui_live | 8501 | The eighteen public screens, on their own origin. |

Ports come from `config/ports.toml` via `repo_paths.py` — never hard-coded.

> **These are the *prod* profile.** A **dev** checkout offsets the `[services]`
> ports to 9210–9213 and 9215–9217 and the web GUI to 9500, uses Redis **db 1** instead of db 0,
> and starts **no proxy of its own** — it borrows prod's on 8100, because the Schwab
> OAuth refresh token is a single rotating credential that two proxies would
> invalidate for each other. Identity comes from the gitignored
> `config/env.local.toml`; a missing marker resolves to prod. See
> `docs/dev-prod-environments.md`.

## Data flow (one request)

1. The GUI enqueues a command on `cmd:{domain}` (a Redis Stream).
2. The owning service's consumer loop drains the stream and dispatches to a handler.
3. The handler calls its **compute** layer (the engines), which fetches market data
   from the proxy and computes a result.
4. The result is validated against a **contract**, written to `cache:{domain}:{view}`
   (which increments a version counter), and an event is published on
   `events:{domain}:{view}`.
5. The GUI's version-poll timer sees the new version, reads the cache, and repaints.

## Local market data — the proxy's store, the collector's tiers, the scan's wide fetch

**Files:** `schwab-proxy/market_store.py` (the store and its gateway),
`options-scanner/chain_carry.py` and `gex_collector.py` (the collector),
`options-scanner/scanner_engine.py` (`scan_chains`, `slice_chain`),
`services/options_svc/compute.py` (`collection_tiers`). **Settings:**
`config/marketdata.toml`, in **Settings → Configuration → Local market data**; every
key is read when it is used, so a saved change needs no restart. Design:
`docs/plans/2026-10-03-market-data-store-design.md`.

> **Off as shipped.** The three features below ship as `mode = "shadow"`,
> `scan.wide_fetch = false` and `collection.tail_interval_min = 1`. With those values
> every request still reaches Schwab and every cadence in this document is unchanged.
> None of the three has been measured in production. The call savings quoted at the
> end of this section are **estimates** from one day's proxy log.

### The proxy's store and its three modes

The proxy is the only process that talks to Schwab, so every option chain, quote and
daily price series passes through it. It keeps the latest copy of each in memory —
nothing is written to disk or to Redis, and a restart starts empty. `mode` decides
what it does with that copy:

| `mode` | Behaviour |
|---|---|
| `off` | Every request goes to Schwab, as before the store existed. |
| `shadow` (shipped) | Every request still goes to Schwab and Schwab's answer is returned. The proxy also works out what `on` would have answered, and records whether it would have been reused and whether it matched. It costs no extra call and answers nothing locally. |
| `on` | A repeat request is answered from the stored copy when that copy is fresh and covers the request. |

What counts as a usable copy when the mode is `on`:

| Data | Reused when |
|---|---|
| Option chain | The same request was fetched at most `chains.max_age_sec` (45 s) ago while a session is open, or `chains.closed_max_age_sec` (1,800 s) ago while every session is closed. A request for a narrower date window is cut from a wider stored chain for that symbol, but only when both ask for every strike and both sides. |
| Quote | Every requested symbol was fetched at most `quotes.max_age_sec` (5 s) ago. Otherwise only the missing or older symbols are fetched and the answer is merged. |
| Daily price bars | The same symbol and range was already fetched in the same part of the day: before the open, during the session, or after the bar settles (`bars.settle_min`, 10 minutes after the close). During the session the stored series is re-served for at most `bars.session_ttl_sec` (1,740 s); with `bars.today_bar = "quote"` today's bar is rebuilt from the live quote instead. |

Intraday price bars are never stored. A caller can state its own limit with
`maxAge=<seconds>` (`maxAge=0` always fetches); the Market Dashboard's 3-second quote
poll sends 1.

Rules that hold in every case:

- A stored copy is never served across a change of market session, and never served
  past its limit because Schwab failed — the error is returned instead.
- A chain request with `strikeCount`, a `range` other than `ALL`, or one contract type
  is answered only by a stored copy of that exact request. The sector put/call ratio
  sums volume over the strikes it receives, so extra strikes would change it.
- An empty answer is never stored, and a cut that would hold no expiration is never
  served. For a date window with no expiration Schwab answers HTTP 200,
  `status: "SUCCESS"`, both maps empty and `underlyingPrice: 0.0` (measured
  2026-10-03); a cut from a stored chain would carry the real price.
- A fault in the store falls through to a plain fetch and is counted
  (`store_degrades` in `/stats/api_calls`).

**The order calls are sent in.** Every call to Schwab passes one gate, 0.2
seconds after the one before it. The one-minute collection poll marks its chain
and price requests, and the gate sends a marked request ahead of ordinary ones
that are waiting: 4 of every 5 calls while the poll is fetching, then an
ordinary one, so a scan or a page load is slowed and never stopped
(`[limiter] priority_run` in `config/marketdata.toml`; 0 = arrival order).
Measured before it, over four sessions: 22 one-minute collection slots lost, 20
of them in the minute after a quarter-hour scan started. The rate is the same,
so the lane changes who waits, not how many calls fit.

A locally answered request skips the proxy's rate limiter and its per-day call
counter, so the Schwab counts on **Settings → General → API usage** keep meaning
"calls sent to Schwab". The row **Answered locally today** beside them counts the
requests answered from the store. It reads zero while the mode is `shadow` or `off`.

Shadow mode makes `on`'s decision with `on`'s limits and leaves the store as `on`
would have left it, so its counts read as "calls `on` would have saved". They run
low: shadow cannot reproduce two identical requests sharing one fetch, or
the proxy fetching a wider window it already holds in place of a narrower one. There
is one known case where they run high: in shadow the collector sends no age limit, so
in the few minutes it polls while every session is closed (about 08:26 to 08:29 and
15:16 to 15:19 Central) its requests count as would-be hits against its own previous
chain. That is about 700 a day on chains for the caller `options_svc`, and they are not
a saving: with the mode on, the collector sends a 20-second limit.

### The collector's two tiers, and what "carried forward" means

The gamma collector writes one row per symbol per view every minute (see
*GEX / Gamma* → **Intraday collection**). As shipped, each of those rows comes from a
chain fetched that minute.

With the mode `on`, chain reuse on and `collection.tail_interval_min` above 1 (3 or
5), the collected symbols split into two tiers:

| Tier | Symbols | Real fetch |
|---|---|---|
| Core | Those named in `config/symbols.toml` `[collection]`, the symbol open on Dealer Positioning, the public Gamma page's hot symbols, the hedging-flow (HIRO) symbols and the symbols the gamma-flip alert watches | Every minute |
| Watchlist-only | Every other collected symbol — collected only because it is on the watchlist | One minute in every `tail_interval_min`, staggered by symbol |

On the minutes between its real fetches, a watchlist-only symbol's chain is **carried
forward** (`chain_carry.carry_chain`): the collector takes the chain the proxy still
holds, reads the live price in one batched quote call, and moves the chain to that
price. `underlyingPrice` becomes the live price, and each contract's gamma and delta
in the nearest expiration move by the Black-Scholes change between the fetch price
and the live price. Schwab's own value stays the base, and a carried gamma may grow
to at most `collection.max_gamma_ratio` (10) times it. Volume, open interest and
implied volatility are not changed.

A carried minute is still written to all five views, so no reader sees a gap. Its
volume and premium totals, its skew readings and its per-strike premium grid repeat
the last fetched values. A carried chain is **not** handed to the detectors that read
volume (unusual activity, big delta), because it holds no new trades. So with a
3-minute tail, an alert on a watchlist-only symbol could arrive up to two minutes
later than it does as shipped.

**Traded premium, booked as it trades (optional, off as shipped).** The premium
the collector stores is each contract's volume for the day times its current
mark, so it is re-priced every minute and falls when marks fall. With
`collection.traded_premium` on (Settings → Configuration → Local market data →
Collector), the options service also keeps a running total in which each
minute's new volume is priced once, at that minute's mark, and stores it per
strike as a sixth view. That total cannot fall. It is an estimate: it is not
split into bought and sold, a minute's volume did not all trade at that minute's
mid, and it leaves out what traded before the regular open and during a restart.
No page reads it yet. It is not written for a carried minute, because a carried
chain holds no new volume. Once a session, after the 15:00 close, the options
service logs how much volume it booked beside how much the chain showed.

Whenever the chain store is on, with or without a tail, the collector also sends
`collection.fresh_max_age_sec` (20 s) as its age limit for every symbol due a real
fetch, so that symbol is never answered with an older stored chain.

`tools/measure_chain_carry.py` is the gate before the tail is switched on: it fetches
a few symbols every minute and compares the engine's net gamma, flip level and walls
from a carried chain with those from the chain really fetched that minute.
Expiration-day comparisons are reported apart, because a fast move there understates
a carried gamma.

### The autoscan's single wide fetch

The auto-scan runs once per quarter hour, starting two minutes after it (09:02,
09:17, 09:32, 09:47; `windows.scan.offset_min` in `config/sessions.toml`). The
delay keeps its burst of chain requests out of the first minute after the hour
and half hour, where Schwab refuses calls.

Each 15-minute auto-scan reads three chain windows per symbol: today to +4 days, +5
to +15 days, and +20 to +45 days. As shipped that is three chain requests per symbol.
With `scan.wide_fetch = true`, `scanner_engine.scan_chains` makes one request from
today to +45 days and cuts the three windows from it locally (`slice_chain`). Symbols
in `scan.wide_fetch_exclude` (`$SPX`, `$NDX`, `SPY`, `QQQ`) keep three requests,
because their 45-day chain is too large for one. A wide answer that is missing or
holds no expiration falls back to the three requests for that symbol, with one
warning in the log.

### Measured baseline, and the estimates

**Measured** on prod for Friday 2026-10-02, from the proxy's access log and counter:
**84,125** calls sent to Schwab.

| Endpoint | Requests | Share |
|---|---|---|
| `/chains` | 50,505 | 64% |
| `/quotes` + `/quote` | 15,653 | 20% |
| `/pricehistory` | 11,994 | 15% |

40,345 of the chain requests were the collector's one-minute fetch, and 11,043 of the
price-history requests repeated a symbol-and-range pair already fetched that day.

**Estimated, not measured:** about 70,000 calls a day with the store on, about 65,000
with the wide scan fetch added, and about 48,000 with a 3-minute tail. The features
are meant to be switched on one at a time, each after the one before it has been
read: shadow → daily bars → chains → quotes → the wide scan fetch → the 3-minute
tail. Shadow mode's counts replace the first estimate, and
`tools/measure_chain_carry.py` is the gate for the last step.

## Folder map

| Folder | Contents |
|--------|----------|
| `schwab-proxy/` | Schwab gateway / token manager. |
| `options-scanner/` | GEX/options engines, scoring, paper engine, simulator, IV analysis. |
| `sentiment-dashboard/` | Sentiment `scoring/` package + live composite + bridge. |
| `trade-analyzer/` | `src/analysis` — recommendation, scoring, fundamentals, sector. |
| `portfolio-analyzer/` | `src/` — sector breakdown, comparisons, evaluation. |
| `shared/` | `analysis_lib/` (technical, market data), `contracts/`, `bus/`. |
| `services/` | The seven Tier-2 services. |
| `webgui/` | The NiceGUI front end. |

## Scoring conventions (shared idioms)

- **Sentiment scores** are integers **1–10, where higher means calmer, more
  supportive conditions** (10 = calmest / most risk-on, 1 = most stress). They are
  **not** contrarian: a VIX spike, VIX backwardation, heavy put buying and a weak
  tape all score low (pinned by `sentiment-dashboard/tests/test_scale_direction.py`).
  This manual called the scale contrarian until 2026-09-11, which was backwards.
  `0` means "input undefined".
- **Confidence** is a float in `[0.0, 1.0]`. Missing data → `0.0`; partial →
  fractional (often `sqrt(fields_present / fields_possible)`).
- **Composites are confidence-weighted**, never a plain weighted average, so a
  low-confidence component cannot dominate.
- Piecewise mappings use **narrow neutral bands** (≈0.95–1.05 of normal) so small
  moves through a breakpoint produce a visible change.

---

# Sentiment Calculations

All sentiment scoring lives in `sentiment-dashboard/scoring/` (pure functions, no
I/O). The single source of truth for component weights is
`scoring/__init__.py:WEIGHTS`.

## Composite blend

**File:** `scoring/composite.py` · `blend(scores, confs, weights)`

The master composite is a confidence-weighted blend of five components:

```
composite             = Σ(wᵢ · scoreᵢ · confᵢ) / Σ(wᵢ · confᵢ)
aggregate_confidence  = Σ(wᵢ · confᵢ)
```

**Component weights** (`scoring/__init__.py:WEIGHTS`, sum = 1.0):

| Component | Weight | Module |
|-----------|--------|--------|
| VIX Complex | 0.20 | `vix.py` |
| Put/Call (cap-weighted sectors) | 0.20 | `put_call.py` |
| Breadth | 0.20 | `breadth.py` |
| Rotation | 0.15 | `rotation.py` |
| Sector Performance | 0.25 | `sector_perf.py` |

> **Credit Pulse was removed from the composite (v4.3)** and its 5% reallocated to
> Put/Call. `credit_pulse.py` still *computes* a score for display, but it is not
> in `WEIGHTS` and does not enter the blend.

## VIX Complex

**File:** `scoring/vix.py` · `score_complex(...)`

The 20% VIX slot is itself a blend of three sub-scores
(`VIX_SUB_WEIGHTS` in `scoring/__init__.py`):

| Sub-score | Sub-weight | Input |
|-----------|-----------|-------|
| Term structure | 0.50 | VIX vs its 10-day MA |
| VIX1D | 0.33 | VIX1D vs VIX |
| Term slope | 0.17 | VIX9D vs VIX |

Each sub-score is a **piecewise mapping of a ratio to a 1–10 score**, with a narrow
neutral band around 1.0.

**Term structure** — `score_term(vix, vix_ma)` on `ratio = vix / vix_ma`:

```
ratio < 0.85          -> 10.0   (deep contango, calm)
0.85 <= ratio < 0.95  -> 9.0 - (ratio-0.85)/0.10 * 2.0
0.95 <= ratio < 1.05  -> 6.0 - (ratio-0.95)/0.10 * 1.0   (neutral band)
1.05 <= ratio < 1.15  -> 4.0 - (ratio-1.05)/0.10 * 1.0
1.15 <= ratio < 1.30  -> 2.0 - (ratio-1.15)/0.15 * 1.0
ratio >= 1.30         -> 1.0    (backwardation, stress)
```

The score never rises as the ratio rises: a calmer reading always scores at least
as high as a more stressed one. (Until 2026-10-04 the two calm segments read
`7.0 + …` and `5.0 + …`, so inside each band a higher ratio scored higher.)

**VIX1D** — `score_vix1d(vix1d, vix)` on `ratio = vix1d / vix`: same shape with
breakpoints 0.80 / 0.88 / 0.98 / 1.05 / 1.15.

**Term slope** — `score_term_slope(vix9d, vix)` on `slope = vix9d / vix`:
breakpoints 0.85 / 0.92 / 1.00 / 1.05.

**Merging the three** — an undefined sub-score (`score == 0`, e.g. `$VIX1D` absent
off-hours) **drops out** and the rest are renormalized over the weight actually
present, the same shape as the Put/Call blend below:

```
score      = Σ(w · s  for present subs) / Σ(w for present subs)   # clamp 1..10
confidence = Σ(w · c  over ALL THREE subs)                        # NOT renormalized
```

The confidence is deliberately left un-normalized: with `$VIX1D` missing it reads
0.67, which is how the top-level composite down-weights a thinner reading. Before
2026-08-20 the *score* was not renormalized either, so a missing sub contributed a
zero and dragged the published value toward 1 — term 6 + slope 8 printed **4**
where the two present components average **6.5**.

## Put/Call

**File:** `scoring/put_call.py`

The composite uses **cap-weighted per-sector** put/call ratios
(`score_sector_weighted`):

```
blended_pcr = Σ(pcr_etf · weight_etf) / Σ(weight_etf)        # S&P cap weights
score       = step_lookup(PC_THRESHOLDS, blended_pcr)        # clamp 1..10
confidence  = sqrt(sectors_used / sectors_possible)
```

`PC_THRESHOLDS` (ratio, score) — a step lookup, not an interpolation: the first row
whose ratio the blended P/C meets or exceeds sets the score. Heavier put buying
(higher P/C) scores **lower**; call-heavy flow scores higher:

```
[(1.3, 1), (1.1, 2), (0.9, 5), (0.7, 8), (0.0, 10)]
```

The single-market variant `score(pc_equity, pc_ma, skew)` adds ±1 adjustments for
P/C spikes/leans (current vs 10-day MA crossing ±15%) and for an Elevated/Inverted
skew categorical.

## Breadth

**File:** `scoring/breadth.py` · `score(...)`

Blends an advance/decline reading with the percent of stocks above their 50-day MA.

`BREADTH_THRESHOLDS` (% above 50DMA, score): `[(75,10),(65,8),(55,6),(45,5),(35,3),(0,1)]`.

**A/D ratio** (`advance / decline`) maps piecewise:

```
>=4.0 -> 10   >=3.0 -> 9   >=2.0 -> 8   >=1.5 -> 7   >=1.15 -> 6
>=0.87 -> 5   >=0.67 -> 4  >=0.50 -> 3  >=0.33 -> 2  <0.33 -> 1
```

**Blend:** when both are present, `score = 0.7 · ad_score + 0.3 · dma_score`; a new
highs/lows ratio nudges ±1. Confidence ≈ `sqrt(fields_present / 4)`.

## Rotation

**File:** `scoring/rotation.py`

Two paths exist:

- **Legacy / display** (the tk app's day/3d/week blend at 40/40/20) — base 5 with
  categorical adjustments.
- **Live composite** uses **dual momentum with a crash filter**
  (`compute_dual_momentum`), the more robust path used by `live_composite.py`.

**Dual momentum** (`lookback_days = 63`):

```
return_etf  = close[-1] / close[-63] - 1
cash_return = (1 + irx_yield_pct/100) ^ (63/252) - 1     # 13-week T-bill, $IRX
```

- **Crash filter:** if the *top* sector's trailing return is below the cash return,
  the regime is risk-off and `score = 1`.
- **Otherwise:** rank sectors by trailing return; compute the average rank of
  cyclical vs defensive ETFs; `rank_spread = def_avg_rank − cyc_avg_rank`
  (positive = defensives leading = risk-off); `score = clip(5.0 + rank_spread, 1, 10)`.
- **Confidence:** `sqrt((returns_available / possible) · irx_present)` where
  `irx_present` is 1.0 with a live $IRX yield, else 0.5.

**RRG quadrants** (`compute_rrg_quadrants`, `rs_window=50`, `mom_window=20`) — used
by the Sector Rotation page:

```
RS              = sector_close / benchmark_close          # per bar
RS_strength     = 100 · RS_today / mean(RS, last 50)
RS_momentum     = 100 · RS_today / RS_(20 bars ago)
```

Quadrants: **Leading** (strength≥100, mom≥100), **Weakening** (≥100, <100),
**Lagging** (<100, <100), **Improving** (<100, ≥100).

⚠ **A SECOND RRG engine exists and it is the one behind the RRG plot and the
rotation headline.** `sector_rotation_assessment` (used by
`compute.rotation_assessment()` → `cache:sentiment:rotation`, read by
`/sentiment/rrg` and `/sentiment/rotation`) normalizes differently:

```
RS          = 100 · sector_close / benchmark_close
RS-Ratio    = 100 + (RS  − SMA(RS, 10))  / rolling_std(RS, 60)
RS-Momentum = 100 + ROC / rolling_std(ROC, 60)     # ROC = RS-Ratio − RS-Ratio[-10]
```

The `compute_rrg_quadrants` figures above feed `/sentiment/sectors` and
`/sentiment/momentum` instead. Until 2026-08-20 the assessment's RS-Momentum also
subtracted ROC's own rolling mean, which differentiates twice — it measured
ACCELERATION rather than rate, and inverted the sign (a steadily rising RS-Ratio
read 99.70). On real data the correction moved 1 of 11 sector quadrants and left
the risk-on/off headline agreeing on 91% of sessions; the corrected spread's wider
tail means `RISK_THRESHOLD` fires on ~10% of sessions rather than ~6%.

### How the rotation screens draw those numbers

Display geometry only — none of this feeds a score. All of it is pure and lives in
`webgui/pages/rotation_view.py`, `rrg_view.py` and `momentum_view.py`.

**Sector Rotation — the spread gauge.** The cyclical-vs-defensive spread on a fixed
−3…+3 track:

```
pos(v)      = (clamp(v, -3, 3) + 3) / 6 · 100        # % across the track
fill        = between pos(spread) and pos(0)         # signed, so it spans to the centre
triggers at = pos(-threshold), pos(+threshold)       # the service's risk_threshold, ±1.50
```

The fill runs *between the reading and zero* rather than growing from the left, because
the quantity is signed: a left-anchored bar would encode −3 and +3 as "small" and "large"
instead of "opposite". The trigger sentence uses `|spread| / threshold`: under 1.0 no
signal has fired, under **1.5** it is "just past", beyond that "entrenched".

**Sector Rotation — the flow band.** Segment and side widths are `flex-grow` values equal
to the S&P weight, so segment *area* is index share. The split keys on the engine's own
`direction` field (`INTO`/`FROM`), not the quadrant. A segment under **7.5%** of its own
side drops its label rather than clipping it.

**RRG — the plot window.** Unlike the fixed axes above, this one is derived:

```
half = max(design_floor, max|value - 100| · 1.08)    # floors: 1.1 on RS-Ratio, 3.4 on RS-Mom
domain = [100 - half, 100 + half]                    # ALWAYS symmetric about 100
```

Symmetry is load-bearing: the quadrant washes and crosshair are drawn at exactly 50%/50%,
so an asymmetric window would put the axes somewhere other than 100/100 and silently
reassign every sector's quadrant on screen. Marker **diameter** is
`10 + sqrt(S&P weight) · 1.9`, so marker *area* is proportional to weight. Trails show
the last **5** readings, resampled along a Catmull-Rom spline (tension 0.5, end tangents
clamped) — the curve passes through every real reading and only chooses the route
between them.

**Momentum — the display arithmetic.** Level tracks scale with `sqrt(size / largest)`.
Component bars are `min(1, |z| / 3) · 50%` either side of a centre line, clamped at ±3
because that is where the service caps the z-scores. The rank chart puts every series on
one shared date axis (histories are ragged — 15, 10, 7 or 5 sessions) and derives its
rank domain from the data rather than assuming a top-20 window; live industry ranks reach
~61 and stock ranks ~272.

## Sector Performance

**File:** `scoring/sector_perf.py`

Cap-weighted daily move across the 11 GICS sectors, mapped to a 1–10 score:

```
cap_wtd_return = Σ(sector_daily_return · sector_cap_weight)
```

Mapping: `score = 5.0 + 2.5 · cap_wtd_return` (in percent), so a flat tape is 5.0
and +2.0% is 10. One point is added when at least 80% of sectors are up, and one
taken off when at least 80% are down. The result is held to **1–10**: a crash day
scores 1.0. With no sector data the score is **absent** (`None`), not 0, and the
component carries no weight; the 30-day history drops a day only for that.
Cap weights live in `sectors_ref.SP500_SECTOR_WEIGHTS`.

### The Sector & Industry heat scale

**File:** `webgui/pages/sector_heat.py` (display only — it feeds no score)

The colour of a tile on the **Sector & Industry** screen is a signed intensity level
in `-6..+6`, computed per column. For a reading `p` in column `c`:

```
band  = FLAT_BAND[c]                     # day 0.50, week 1.00, month 1.50 (%)
scale = quantile(|values in c|, 0.90)    # over sectors AND all industries
level = 0                                       if |p| <= band
      = sign(p) · clamp(ceil(f · 6), 1, 6)      otherwise,
        where f = (|p| - band) / (scale - band)
```

`scale` is the column's **90th percentile, not its maximum**. Industry ETFs have a fat
right tail — one +27% month against a ~3% median pins every sector into the bottom of
the ramp — so the top decile saturates at level 6 instead of defining the scale. When
every reading in a column is flat (`scale <= band`) any single reading past the band is
by definition that column's largest, and takes level 6.

The level maps to one of 13 fixed colours, an oklch ramp with lightness `0.175 → 0.300`
and chroma `0.022 → 0.110` at hue **158** (up) or **22** (down); level 0 is
`oklch(0.155 0.004 90)`. The figure's own colour lifts along a parallel ramp
(lightness `0.660 → 0.965`) so a saturated tile carries a bright number and a flat one a
grey one.

## Velocity and divergence

**File:** `scoring/composite.py`

**Velocity** (`velocity(history_scores, today)`):

```
roc_3d = today - scores[-3]
roc_5d = today - scores[-5]
z_20d  = (today - mean(scores[-20:])) / std(scores[-20:])
regime_break = abs(z_20d) > 1.5
```

**What `scores` holds.** When a live composite is on screen, the history is each
earlier session's **last live composite** (recorded by the service at every
intraday sample, 90 sessions kept), never the stored 30-day history. The two are
scored by different methods (the stored history has no put/call reading), and the
gap between them, measured at 0.60 points on average, was being read as movement.
With fewer than three live sessions recorded the rate-of-change readings are a
dash; the Yesterday and Change tiles use the same series. A stored day shown with
no live reading is compared with the stored history.

**Divergence** (`divergence(named_scores)`): of the components with score > 0, if
`max_score − min_score ≥ 4`, flag a low-conviction divergence between the highest
and lowest components.

## Intraday Market Trend (directional 0–100)

**File:** `scoring/intraday_trend.py`

A directional 0–100 trend score (50 = neutral, 100 = max bull) recomputed every
15 minutes, blended from four sub-scores by `TREND_WEIGHTS`:

```
TREND_WEIGHTS = {price: 0.45, breadth: 0.25, sector: 0.20, vix: 0.10}
```

Each sub-score returns `TrendSub(score, confidence)`; `blend_trend` combines them
confidence-weighted (same idiom as the composite), and `score_to_state` maps the
result to the five-state vocabulary used by the regime bridge.

**Price** — `score_price(alignment_pct, price_vs_vwap_pct, macd_hist, rsi, adx, n_timeframes)`:

```
align = alignment_pct / 100
vwap  = clamp(price_vs_vwap_pct / 0.5, -1, 1)
macd  = sign(macd_hist)
rsi   = (rsi - 50) / 20
direction = 0.50·align + 0.20·vwap + 0.15·macd + 0.15·rsi
adx_factor = clip(adx / 40, 0.3, 1.0)               # chop hugs 50
score = clamp(50 + 50·direction·adx_factor, 0, 100)
confidence = clip(n_timeframes / 3, 0, 1)
```

**Breadth** — `score_breadth_dir(net_ad, pct_above_50, new_highs, new_lows)`:
weighted blend (0.4 / 0.4 / 0.2) of normalized net A/D, `(pct−50)/50`, and
`(highs−lows)/(highs+lows)`; `score = 50 + 50·direction`; confidence = sum of the
weights actually used.

**Sector** — `score_sector_participation(n_green, n_total, cyc_def_spread)`:
`participation = (n_green/n_total − 0.5)·2`; `direction = 0.6·participation +
0.4·cyc_def_spread`; confidence = `clip(n_total/11, 0, 1)`.

**VIX** — `score_vix_context(vix, vix_change_pct, vix1d, vix9d)`:
`lvl = clip((20−vix)/10, −1, 1)`, `chg = clip(−vix_change_pct/5, −1, 1)`,
`term = clip((vix−vix1d)/2, −1, 1)`; `direction = 0.4·lvl + 0.4·chg + 0.2·term`;
confidence = 1.0.

**Volatility damper** — `vol_confidence_factor(vix_change_pct)`: a sharp VIX spike
lowers aggregate confidence: `clip(1 − 0.04·vix_change_pct, 0.4, 1.0)` for positive
changes, else 1.0.

## Daily trend-regime state machine

**File:** `scoring/trend_regime.py`

A SPY-based five-state classifier (independent of the composite) used to fill the
back-compat `sma_*`/`drawdown` bridge fields. Constants:

```
BULL_DD_MAX       = -5.0     # max drawdown from 252d peak for "bull"
PULLBACK_DD_MAX   = -12.0
BEAR_RALLY_DD_MIN = -10.0
SLOPE_BULL_MIN    =  0.05    # 200-DMA slope %, bull
SLOPE_BEAR_MAX    = -0.05
SLOPE_WINDOW      = 20
HYSTERESIS_DAYS   = 2
```

Inputs are SPY daily closes. It computes `sma50`, `sma200`, the 20-bar slope of the
200-DMA (as %), and the 252-day drawdown, then classifies:

```
close > sma50 > sma200  and slope > 0.05  and dd > -5   -> bull_trend
close <= sma50, sma50 > sma200, slope > 0, dd > -12     -> pullback_in_bull
close < sma50 < sma200  and slope < -0.05               -> bear_trend
close > sma50, slope < 0, dd < -10                      -> bear_rally
otherwise                                               -> range
```

`commit_state` requires the raw state to repeat for `HYSTERESIS_DAYS` before
flipping. Confidence: 0.0 below 50 bars, 0.5 from 50–200, 1.0 at ≥200 bars.

## Blended market regime (the Market Regime Console)

**File:** `sentiment-dashboard/scoring/market_regime.py`. Published to
`cache:sentiment:regime` + `:regime_history` every **5 min** in market hours.

This is a **different** classifier from the daily state machine above. It is a
five-member simplex — every regime holds a *share* of the current tape, and the
"label" is simply the largest share.

**Display names versus internal keys.** The names were changed for display in
August 2026; **the keys were not**, because they are the `RegimeState` contract,
and the `regime_intraday` DB columns.

| Key (contract, DB, logs) | Displayed as | Why the name changed |
|---|---|---|
| `mean_reversion` | **Balanced** | All five of its inputs say price is *at* its mean. Nothing measures an extreme, so the old name promised a fade the model never tested — and it was the only name naming a *strategy* rather than the tape. |
| `trending` | **Trending** | unchanged |
| `breakout` | **Breakout** | unchanged |
| `choppy` | **Whipsaw** | Same "not trending" axis as Balanced; what distinguishes it is *energy* (high ATR with low ADX, failed breaks, two-sided wicks). Balanced/Whipsaw carries that contrast; Mean-Reversion/Choppy did not. |
| `crisis` | **Stressed** | `VIX_STRESS_LO` is 22 and the fast-attack fires near VIX 30 — stress, not crisis. "Volatile" was also rejected: it equally describes breakout and whipsaw days. |

`REGIME_DISPLAY` in that module is the source. The mapping is **duplicated** in
`webgui/pages/sentiment.py` and `options_svc/market_snapshot.py` because none of those may import the package —
Tier 1 takes no engine imports, and the services would hit the documented
cross-app `scoring` name collision. Keep them in step.

### The direction axis

`trending` and `breakout` additionally render a direction word — **Rallying** /
**Firming** (up), **Retreating** / **Softening** (down), **Breakdown**. Balanced,
Whipsaw and Stressed are directionless by construction.

```
DIRECTION_SLOPE_DEADBAND = EMA_TREND_LO          # 0.05, the trending ramp's own floor
DIRECTION_TREND_DEADBAND = 3.0                   # points either side of the 50 neutral
DIRECTION_STRONG_SLOPE   = 0.5 * (EMA_TREND_LO + EMA_TREND_HI)   # Rallying vs Firming
```

**This is a label adornment, not a sixth regime.** The intensity maths stays
sign-blind (`ramp(abs(slope), …)`) — "is this a trend day" is answered identically
up or down. Splitting `trending` would need a DB column, a chart series and a
contract change, and would tear the membership across two bins when the slope flips
mid-session, defeating the blended model.

**How the contradiction risk is avoided — the load-bearing part.** The app has two
independent direction reads: this module's signed `ema_slope_atr` (SPY price,
5-minute) and the Market Trend composite (price + breadth + sector + VIX,
15-minute, hysteresis-committed). They diverge on a real condition — the index up
on narrow leadership while breadth is negative — so a word taken from either alone
can contradict the other panel.

`direction_sign` therefore names a direction **only when both agree past their
deadbands**; otherwise the neutral base label renders. `commit_direction` is
deliberately **asymmetric**: two consecutive reads to *claim* a direction, one to
drop back to neutral — never keep asserting a direction the evidence stopped
backing.

Two rendering rules follow from this and are easy to get wrong:

1. The stacked/ranked panel's **series names stay the base words**. A legend that
   renames itself intra-session destroys the reading position that makes it legible.
2. The headline **colour follows the direction** for the two directional regimes,
   because a fixed green would paint "Retreating" as though it were bullish.

---

# Options Scoring

**File:** `options-scanner/scoring.py`

## Composite 0–100 score

Each signal gets a 0–100 quality score: a weighted sum of eleven normalized factors
(`DEFAULT_WEIGHTS`, sum = 100):

| Factor | Weight | Group | Normalizer |
|--------|--------|-------|-----------|
| Risk/Reward | 15 | Value | `norm_rr` |
| Probability of Profit | 10 | Value | `norm_pop` |
| Theta efficiency | 10 | Value | `norm_theta` |
| Vol Rank | 12 | Context | `norm_iv_rank` |
| IV/HV ratio | 10 | Context | `norm_iv_hv_ratio` |
| Vega risk | 8 | Context | `norm_vega_risk` |
| Expected-move buffer | 12 | Context | `norm_em_buffer` |
| Liquidity | 5 | Execution | `norm_liquidity` |
| Trend alignment | 10 | Execution | `score_trend` |
| GEX wall proximity | 4 | Execution | `norm_gex_proximity` |
| DEX wall proximity | 4 | Execution | `norm_dex_proximity` |

```
score = Σ(weightᵢ · normalized_factorᵢ) / 100
```

## Factor normalizers

**Risk/Reward** — `norm_rr(rr_pct)`: `min(100, rr_pct / 50 · 100)` (50%+ R:R → 100).

**Probability of Profit** — `norm_pop(pop_pct)`: `min(100, (pop_pct − 50) / 45 · 100)`
(PoP 50% → 0, 95%+ → 100).

On the Strategy Finder the probability itself (`pop_from_payoff`) is the share of
a **lognormal** distribution of the stock price at the front expiry in which the
position is in profit: `ln(S_T / S_0) ~ N((r − q − σ²/2)·T, σ²·T)`, with `σ` the
at-the-money implied volatility and `T` the time left to the row's own expiry on
the clock. Inside two months it is within a point of the zero-drift normal model
it replaced on 2026-10-04; it is lower on long-dated short premium (two years at
70% volatility: 54.8% against 66.0%) and it reads the hours left on an
expiration-day row, which used to get a fixed twelve.

**Theta efficiency** — `norm_theta(net_theta, max_loss, all_theta_efficiencies)`:
`efficiency = |net_theta| / max_loss · 100` (daily decay as % of risk). Normalized
by **percentile rank** among all candidates when peer data exists, else linearly:
`min(100, efficiency / 0.5 · 100)`.

**Vol Rank** — `norm_iv_rank(iv_rank)`: pass-through clamped to `[0, 100]`.

**IV/HV ratio** — `norm_iv_hv_ratio(iv_hv)`: `(iv_hv − 0.5) / 1.0 · 100`, clamped
(ratio 0.5 → 0, 1.0 → 50, 1.5+ → 100). Rewards IV richer than realized vol.

**Vega risk** — `norm_vega_risk(net_vega, max_loss, iv_rank)`: penalizes vega
exposure in low-IV regimes:

```
vega_exposure = |net_vega| / max_loss            # typically 0.001..0.05
vega_score    = max(0, min(100, (1 - vega_exposure/0.05)·100))
score         = 0.6·vega_score + 0.4·(iv_rank/100·100)
```

**Expected-move buffer** — `norm_em_buffer(short_strike, underlying, em_1sd, spread_type)`:
how far the short strike sits outside the ±1σ expected move.

```
distance  = underlying - short_strike   (PCS) | short_strike - underlying (CCS)
em_ratio  = distance / em_1sd
em_ratio <= 0  -> 0                              (short strike ITM/ATM)
0 < em_ratio<1 -> em_ratio · 50                  (inside EM, penalized)
em_ratio >= 1  -> min(100, 50 + (em_ratio-1)·50) (outside EM, rewarded)
```

**Liquidity** — `norm_liquidity(bid, ask, mark)`: `spread_pct = (ask−bid)/mark·100`;
`max(0, min(100, (1 − spread_pct/5)·100))` (≤1% spread → 100, ≥5% → 0).

**Trend alignment** — `score_trend(...)`: market regime maps to ±10, normalized to
`[0, 100]`.

**GEX / DEX wall proximity** — `norm_gex_proximity` / `norm_dex_proximity`: distance
of the short strike to the nearest wall as % of spot; `min(100, min_dist_pct / 1.0 ·
100)` (≥1% away → 100, at the wall → 0).

## Expected move and IV analysis

**File:** `options-scanner/iv_analysis.py`

**Expected move** — `calc_expected_move(price, iv_pct, dte)`:

```
em_1sd = price · (iv_pct/100) · sqrt(max(dte, 0.25) / 365)
```

Returns the 1σ dollar/percent move plus ±1σ and ±2σ bands.
`calc_expected_moves(price, iv_pct)` returns daily/weekly/monthly variants.

**Vol Rank & percentile** — `calc_iv_rank_percentile(current_iv, hv_series, lookback_days=252)`:
compares current ATM IV to the trailing 252-day **HV-30** distribution (a realized-
vol proxy). The screens label it **Vol Rank**, which is what it is; the function and
the payload field keep the legacy `iv_*` names, and `hv_*` aliases carry the honest
ones. ⚠ It is therefore a **variance-risk-premium** reading — "is IV rich against
recent realized movement" — and not an IV-vs-IV-history rank. A true IV rank needs a
persisted daily IV series, which `shared/iv_history.py` began accruing on 2026-09-12
(one `cm30_iv` per symbol per scan) and which needs ~20 samples before it can be
ranked at all:

```
vol_rank       = (current_iv - hv_low_52w) / (hv_high_52w - hv_low_52w) · 100
vol_percentile = (# days with hv < current_iv) / total_days · 100
```

Returned under both `iv_*` (legacy) and `hv_*` (honest) keys.

**Historical volatility** — `calc_historical_vol_series(candles, window=30)`:
rolling 30-day std of daily log returns, annualized: `std · sqrt(252) · 100`.

**The scan funnel carries both inputs** (since 2026-09-17). Each symbol's account on
`cache:options:scan_funnel` holds `hv_current` (the latest HV-30) and `current_iv`
(ATM implied volatility) from the same `run_iv_analysis` pass that produced its Vol
Rank. ⚠ Both are **percents** — 48.5 means 48.5% — and are `null` when the analysis
did not measure them.

**IV vs HV** (the Symbol Dossier's Volatility band — `webgui/pages/symbol_facts.py:iv_vs_hv`):

```
ratio = current_iv / hv_current
band  = "high"  if ratio >= 1.2
        "low"   if ratio <= 0.9
        "mid"   otherwise
```

The boundaries and words are `strategy_scoring.infer_market_view`'s own fallback,
restated because Tier 1 cannot import the scorer and pinned equal to it by
`shared/tests/test_cross_tier_mirrors.py` — so the dossier cannot describe a symbol in
terms the Strategy Finder's scorer disagrees with. A missing input, `hv_current <= 0`
or a negative IV (Schwab's `-999` sentinel) gives no ratio and the band `na`.

**The dossier's expected move** (`symbol_facts.expected_move`) is the same 1σ form over
**calendar** days, for one day and one week, with no 0.25-day floor:

```
em_day  = spot · (atm_iv/100) · sqrt(1 / 365)
em_week = spot · (atm_iv/100) · sqrt(7 / 365)
```

`atm_iv` is the Opportunity Board's ATM IV, falling back to the look-up's
`current_iv` for a symbol the board does not carry. A non-positive spot or IV is no
reading, never a zero move.

## Strategy Finder scoring (Fit + Quality)

**Files:** `options-scanner/strategy_scanner.py` (builders, `payoff_metrics`,
`pop_from_payoff`), `options-scanner/strategy_scoring.py` (`GATE_BARS`,
`_TYPE_PROFILE`, `score_strategy`), `services/options_svc/compute.py` (`swing_scan`).
Design: `docs/plans/2026-09-13-strategy-finder-all-structures-design.md`.

This is a **separate** score from the eleven-factor composite above. Each candidate is
scored on one 0–100 scale so that different structures rank together:

```
fit       = 0.6 · directional fit (net delta vs the inferred view)
          + 0.4 · volatility fit  (net vega  vs the vol regime)
quality   = weighted q_rr 0.30 · q_be 0.25 · q_pop 0.25 · q_liq 0.20
composite = 0.7 · quality + 0.3 · fit
```

`q_be` rewards a breakeven **near spot** for every family except `NEUTRAL`, where it
rewards a **wide** profit zone (breakeven spread ÷ 1-σ move). Both are measured in units
of the 1-σ move **to that candidate's own expiry** — daily expected move × √DTE, floored
at one day — so a 30-DTE breakeven is judged against a 30-day move, not a 1-day one.
The **Market Scanner's Directional tab** scores its single-leg candidates the same way
(`run_full_scan` passes the same `daily_move`), so one candidate scores identically on
both pages; in either, a candidate without a usable DTE falls back to one move at its
scan window's DTE minimum. The Scanner's **other structures** (below; listed in the same
table as the credit spreads on 0-DTE and Swing since 2026-10-07) go through the same call. ⚠ **The Income board scores through the same `swing_scan`
path, so its `entry_score` changed basis on 2026-09-13** — from one move at the window's
30-DTE minimum to the move to each candidate's own 30–45 DTE expiry, a factor of
√(DTE / 30), so at most ~1.22 on the move. Scores captured under `scanner_type = "INCOME"`
before and after that date are not directly comparable for calibration. The capture
takes whatever the board publishes (`capture_min_income = 0`), so no capture floor moved;
a row whose score crosses the board's own 50 cut is the only way the published — and so
captured — set can differ (apart from the per-symbol cap: since 2026-09-16
`signal_recorder.record_signals` holds each symbol to `[capture] max_open_per_symbol`
= 2 OPEN captures across all scanner types, highest score first, and captures nothing
if it cannot read the open count). Flies, condors, calendars
and the short straddle/strangle carry `NEUTRAL`; the long straddle/strangle carry
`VOLATILITY`, which takes the near-breakeven branch, because a long volatility trade
wants the move it needs to be small. Diagonals and share structures are `DIRECTIONAL`.

**Hard gates and grade.** Each type maps to a gate profile (an unmapped type falls to
`DEBIT`). Failing any `min` bar caps the composite at **39** (`GATE_FAIL_CAP`) and grades
**Weak**; clearing every `excellent` bar with composite ≥ **78** is **Strong**; otherwise
≥ **58** is **Good**, else **Marginal**. The service then cuts anything **Weak or under 50**
(`SWING_MIN_SCORE`) and reports the count as *below the quality bar*. For the Strategy
Finder it then keeps the best **`FINDER_PER_TYPE_LIMIT` (25)** rows of each `type` by
`composite_score` (a missing or non-finite score ranks last) and reports the rest as
`not_shown` — *lower-scoring ideas not shown*. The counts never overlap and are taken in
the order `vol_filtered` → `filtered_out` → `not_shown`; ids and payoff curves are built
after the limit. Measured on a synthetic `$SPX`-sized chain (56 expiries, 25.6k
contracts), the every-expiry scan left 1,061 rows after the quality cut (2.27 MB of cache
payload); the limit publishes 510 (1.07 MB). The Income board passes no limit.

| Profile | Types | `min` liq / reward / PoP | `excellent` liq / reward / PoP |
|---|---|---|---|
| `LONG` | long call/put, long straddle/strangle, protective put | 40 / R:R 0.8 / 30 | 70 / 1.5 / 45 |
| `NAKED` | short call/put, short straddle/strangle, covered call | 40 / capeff 0.10 per yr / 65 | 70 / 0.20 / 78 |
| `DEBIT` | bull call, bear put, call/put/iron butterfly, call/put condor, call/put calendar, call/put diagonal, collar | 45 / R:R 0.6 / 30 | 75 / 1.2 / 45 |
| `CREDIT` | PCS, CCS | 45 / R:R 0.15 / 60 | 75 / 0.33 / 72 |
| `NEUTRAL` | iron condor | 45 / R:R 0.12 / 55 | 75 / 0.25 / 68 |

For `LONG`, an unbounded max profit auto-passes the reward bar. For `NAKED` the reward is
annualised capital efficiency, `(max_profit / capital) · 365 / max(dte, 5)` (see the NAKED
reward note in `strategy_scoring._reward_metric`). **The iron butterfly is judged `DEBIT`
although it takes a credit**: by put–call parity its payoff is the long butterfly's, and
under `NEUTRAL`'s 55 PoP bar both would be cut on nearly every fairly priced chain
measured (PoP 23.6–51.9 at 14/30/45 DTE), though a rich chain can lift them over it.

### Expiry floor and mispriced wings

`strategy_scanner._front_pair` picks the nearest expiry both the call and put maps list
**at least `_MIN_FRONT_DTE` (7) days out** — `max(dte_min, 7)`, so a window whose DTE min
is already higher keeps it. The floor is applied inside `_front_pair` itself, so every
caller takes it: straddles and strangles (`build_straddles_strangles`), call/put/iron
butterflies and call/put condors (`build_butterflies_condors`) and the share structures
(`build_stock_structures`). Calendars and diagonals apply the same constant to their front
month in `build_calendars`. The single-leg directionals, the debit and credit verticals
and the iron condor built from the credit spreads take no floor. At the page's default
DTE min of 0 this means no straddle, strangle, fly, condor or share structure is built on
a 0–6 DTE expiry (operator decision, 2026-09-13 — on a daily-listing name those were
same-day bets).

**Every expiry, not the nearest (the Strategy Finder, 2026-09-14).** Each builder takes
the NEAREST expiry in its window. The Finder's handler calls `swing_scan(...,
every_expiry=True)`, and `_build_every_expiry` runs each single-expiry builder once per
listed expiry *e* (DTE *d*) on the chain **sliced to *e*** with `dte_min = dte_max = d` —
so the builders themselves are unchanged and build exactly that expiry. On an expiry
under 7 DTE the `_front_pair` floor leaves nothing, as above. Calendars take each expiry
with *d* ≥ 7 as the front, over a slice holding it plus the expiries at least
`_CAL_MIN_GAP` (7) days later, and keep only rows whose front is *e* (a front with no
usable leg would otherwise build the next expiry's calendar twice). Credit spreads are one
`screen_spreads` pass over the whole chain, which already covers every expiry; iron
condors are paired **within** each expiry instead of the top three across the scan. The
Income Window keeps `every_expiry=False`.

**Earnings: flagged, not dropped.** The Finder also passes `earnings_mode="flag"`:
`screen_spreads` receives no earnings date, and every candidate the drop would have
removed — `earnings_gate_applies(trade_type, dte)` and `check_earnings_conflict(date,
latest leg expiry)` — keeps its place and gains `spans_earnings: True` plus
`earnings_date`. The Income Window keeps the default `"drop"`, and the Market Scanner's
`run_full_scan` still drops inside `screen_spreads`.

**The chain.** `compute.fetch_scan_chain(symbol, dte_max)` serves every `swing_scan`
caller. It lists expirations (`/expirationchain`, 0.2–0.3 s), keeps those from **today**
to today + `dte_max` + 2 (all of them when `dte_max` is `None`) — from today rather than
from DTE min, because `run_iv_analysis` reads the near expiries for the ATM IV and
expected move — and fetches runs of at most `SCAN_RUN_EXPIRIES` (8) consecutive listed
expiries, `SCAN_FETCH_WORKERS` (4) at a time, merging the raw expiry maps. A run whose
response is missing or holds no expiry adds its expiries to `expiries_failed` (a count of
expiries, not of runs). With no usable
expiration list it falls back to one fetch — bounded to 120 days when the scan has no
DTE max (`_FALLBACK_MAX_DTE`) — and reports `expiries_failed = None` (not
counted, which is not zero). Measured 2026-09-14 pre-market: SPY's whole chain (34
expiries, 12,956 contracts) **timed out at the proxy's 30 s** in one request and took
6.5 s grouped; `$SPX` (56 expiries, 25,650 contracts) took 11–12 s to fetch. Whole
scans measured live the same day (~12:30 CT, range *All*, every expiry, none failed):

| Symbol | Expirations | Wall time | Rows published |
|---|---|---|---|
| NVDA | 25 | 13.5 s | 139 |
| SPY | 34 | 25.7–27.4 s | 162–167 |
| `$SPX` | 56 | 40.1 s | 69 |

The per-type limit of 25 did not bind on any of them. `cmd:options` is consumed one
command at a time, so a Calculator or Simulator load enqueued during a `$SPX` scan waits
behind it — and those pages' wait overlay gives up at 30 s.

**A large chain asks first (the Strategy Finder, 2026-09-14).** `swing_scan` lists the
expirations ONCE (`option_expiration_rows` → `parse_expiration_rows`, rows of
`(date, expirationType, dte)`) and `_plan_fetch` decides the fetch before any chain call.
Each row's DTE is **Schwab's own `daysToExpiration`** — the number Schwab writes into the
chain keys (`"YYYY-MM-DD:dte"`) the builders filter on, which differs from the host's
calendar difference by a day between 23:00 and 24:00 CT, and the fetch asks for exactly
the chosen dates, so counting on the calendar could silently drop a boundary expiry. The
calendar difference is used instead when the field is not a non-negative whole number, or
when it is **more than one day** from the calendar difference (no clock skew explains
two days, so such a value is a bad field); that second case speaks once per listing as
the degrade `options.expiration_dte`. Checked live on SPY and NVDA: the list's
`daysToExpiration` matched the chain keys' DTE on every expiry. A date listed twice keeps
`"S"` if either row says so.

The expirations inside `dte_min`..`dte_max` (no upper bound for `None`, never below 0) are
counted. More than **`LARGE_CHAIN_EXPIRIES` (30)**, with `ask_if_large=True` — passed by the
Finder's handler alone; **the Income Window never asks** — and no `expiry_choice`, and the
scan returns `needs_choice: True`, `expiration_count` and `choices` **without fetching a
chain** (`expiries_failed` 0: nothing was attempted). `choice_summary` gives one entry per
`EXPIRY_CHOICES` key, in order, each counted inside the range:

| Key | Label | Keeps (`choice_dates`) |
|---|---|---|
| `next_30` | Next 30 days | DTE ≤ 30 |
| `next_90` | Next 90 days | DTE ≤ 90 |
| `monthly` | Monthlies only | `expirationType == "S"` — not `W` weekly, `Q` quarterly or `M` month-end |
| `all` | Everything | all of them |

`est_seconds` is `count × SCAN_SEC_PER_EXPIRY` (0.75 — live SPY 26 s / 34, `$SPX` 40 s /
56), a half rounding **up** (Python's `round` would take 4.5 to 4). A choice with count 0 is
still listed. Schwab's types, measured on prod the same day:

| Symbol | Total | W | S | Q | M | ≤ 30 d | ≤ 90 d |
|---|---|---|---|---|---|---|---|
| `$SPX` | 56 | 32 | 19 | 4 | 1 | 23 | 35 |
| `$NDX` | 47 | 27 | 15 | 4 | 1 | 23 | 32 |
| SPY | 34 | 16 | 13 | 4 | 1 | 14 | 19 |
| QQQ | 33 | 14 | 14 | 4 | 1 | 14 | 19 |
| IWM | 33 | 14 | 15 | 4 | — | 14 | 18 |
| NVDA | 25 | 10 | 15 | — | — | 9 | 13 |

**With `expiry_choice`** only that choice's dates are fetched (`fetch_scan_chain(rows=,
dates=)`: runs consecutive in the listing, each cut to 8 — so a monthly with weeklies
listed between it and the next is a run of its own, while far-dated monthlies that are
neighbours in the listing share one) and the chain is **sliced to them before any builder runs**.
Calendars therefore pair only within the choice: *Next 30 days* caps a calendar's back
month at 30 days, and *Monthlies only* pairs monthlies with monthlies. ⚠ **The IV
reference is always kept.** `_iv_reference_date` finds the expiry the WHOLE scan's
`extract_atm_iv` would read (nearest 30 DTE inside 7–60, else nearest 30 above 0, the
earlier on a tie, from today to `dte_max` + 2) and, when the choice leaves it out, fetches
it beside the choice for `run_iv_analysis` alone, then slices it back out — so the ATM
IV, the Vol Rank and the daily expected move every candidate is scored against are the
whole chain's whatever the choice. "Only when no chosen expiry is inside 7–60" would not
do: *Monthlies only* keeps a 46-day monthly that `extract_atm_iv` would read instead of the
29-day weekly. A reference that does not load degrades as `options.scan_iv_reference`,
and `extract_atm_iv` then reads the chosen expiry nearest 30 DTE instead (inside 7–60,
else any above 0) — or, when no chosen expiry is more than 0 DTE, returns `None`, so the
scan has no ATM IV rather than a fallback one. **`expiries_failed` counts chosen expiries
only**, recounted from what the merged chain holds, so it can never exceed
`expirations_scanned`; if no chosen expiry loaded the answer is `chain_missing`, with no
price history fetched. A choice holding no expiry in the range returns
`no_expiries_in_range` with `expirations_scanned` 0 and fetches nothing.

A choice is applied only to a large range: on 30 or fewer expirations, or without
`ask_if_large`, it is ignored, the whole range is scanned and `expiry_choice` is `None` —
which is what lets the page send a remembered pick without knowing the count. With no
expiration list there is nothing to count, so nothing is asked (the single fallback fetch,
`expiration_count` `None`). An unknown `expiry_choice`, or a non-bool `ask_if_large`, raises
`ValueError` before any fetch.

`_priced_inside` then drops a structure whose mid-mark price is impossible for its
payoff. A long butterfly or condor is worth between 0 and its wing at expiry, so its
`net_debit` must lie strictly inside `(0, wing × 100)`; an iron butterfly is that payoff
shifted down by the wing, so its `net_credit` must lie inside the same range. Outside it,
the marks are wrong rather than the trade good: measured, 95C 6.5 / 100C 4.1 ×2 / 105C
1.5 is a $20 credit for a long fly, which reported max loss 25.2, R:R 20.4, no
breakevens and PoP 100, and ranked first. Non-finite or absent prices are dropped too.

### Payoff: two valuation paths

`payoff_metrics` evaluates P&L at payoff breakpoints (price 0, each strike, 2× the top
strike) and interpolates breakevens on a fine grid.

- **Single-expiry option sets** (every structure that existed before 2026-09-13, plus
  straddles, strangles, butterflies and condors) value each leg at **intrinsic on the
  expiry**. This path is unchanged byte for byte, pinned by
  `test_single_expiry_options_never_take_the_front_valuation_path`.
- **A set with a later-expiring leg or a share leg** (calendars, diagonals, covered call,
  protective put, collar) is valued at the **front expiry**: a share leg is worth the
  price; a leg expiring then is worth intrinsic; a later leg is Black-Scholes at **its own
  IV** over the days remaining after the front, **floored at intrinsic** (equity options
  are American, so a deep in-the-money put is worth at least `K − S`). Because that curve
  peaks between breakpoints, 801 points to 2× the top strike are sampled, plus points out
  to **32×** — a put diagonal's worst case is the back put decaying to nothing far above
  the strikes. A later leg with an unusable IV (Schwab's `-999`, NaN, zero) is never built.
- Every Finder payoff is evaluated down to a price of zero. What is specific to a share
  leg: it is valued at the price itself, it counts like a long call in the upside tail
  test (so a protective put is unbounded upside and a covered call is bounded), and its
  structure's capital is the cash it ties up (below).

PoP (`pop_from_payoff`) integrates a **zero-drift normal** over ±6σ,
`σ = spot · atm_iv · √(max(dte, 0.5)/365)`, counting the prices where that same P&L is
positive; `dte` is the **front** leg's.

**Commission** is round-trip (open + close) **per option contract**:
`contracts × rate × 2`, where a leg's contracts are its `qty` — a butterfly body counts
two — and **share legs cost nothing**. It is subtracted from max profit and added to max
loss, so R:R and capital efficiency are net of it. **Capital** for a share structure is
the larger of its max loss and the cash the position ties up (`net debit × 100 +
commission`); without that a collar, whose max loss is ~10% of the shares' cost, rated
about ten times as capital-efficient as a covered call on the same lot.

### Measured outcomes, and how to re-measure

`tools/sweep_strategy_gates.py` builds a synthetic Black-Scholes chain (front and front +
28 days), runs the real builders with the page's default delta bands (put −0.20…−0.10,
call 0.10…0.20) and scores through `score_all` the way `swing_scan` does, against a
neutral view, with breakevens judged against the expected move to each candidate's own
expiry. It measures gates and grades only — not the volatility gate, the earnings filter,
the 50 / not-Weak cut or the market-state tilt. No Schwab call, no database.

```
python tools/sweep_strategy_gates.py                  # spot 100, IV 0.28, $2.50 strikes
python tools/sweep_strategy_gates.py --step 5
python tools/sweep_strategy_gates.py --iv 0.20 --days 7,14,30,45
python tools/sweep_strategy_gates.py --rich 1.2              # marks 20% over fair value
```

Default run (spot 100, IV 0.28, $2.50 strikes), grade by front DTE:

| Structure | 14 | 30 | 45 | Deciding figure |
|---|---|---|---|---|
| Long straddle / long strangle | Marginal | Marginal | Marginal | gates pass; composite 50–55 |
| Short straddle | Weak | Weak | Weak | PoP 57.3 vs 65 |
| Short strangle | Good | Good | Good | PoP 77.8–79.1 |
| Call / put / iron butterfly | Weak | Good | Good | PoP 29.2 at 14 (vs 30) |
| Call / put condor | Good | Weak | Good | R:R ~0.53 at 30 (vs 0.6) — the wing lands at 5 |
| Call / put calendar | Good | Good | Good | R:R 0.82–2.19 |
| Call diagonal | Weak | Good | Good | R:R 0.44 at 14 |
| Put diagonal | Weak | Weak | Good | R:R 0.45 / 0.47 at 14 / 30 |
| Covered call | Weak | Weak | Weak | PoP 52 vs 65 |
| Protective put / collar | Good | Good | Good | PoP 42–45 / R:R 1.66–2.00 |

⚠ **These move with the ladder, wing width and IV — quote them with their parameters.**
On **$5 strikes at 14 DTE** no short strangle, covered call or collar is built at all (the
nearest sold call, 105 at 0.203 delta, is over the 0.20 ceiling), butterflies pass (PoP 45) and condors fail
(R:R 0.22). At **IV 0.20** butterflies fail PoP at 30 and 45 DTE as well.

⚠ **The two NAKED cuts hold on FAIRLY PRICED chains.** `--rich` marks every option above
the volatility the scorer computes probability with. At spot 100, IV 0.28, $2.50 strikes,
30 DTE, the short straddle is still Weak at `--rich 1.1` (PoP 61.6) but **Good** from
`--rich 1.2` (PoP 66.3, composite 71.9) — its bigger credit widens the breakevens past 65.
The covered call stays Weak to at least `--rich 1.5` (PoP 56.6–60.1 across 7–45 DTE and both
ladders). So "counted, not shown" is a statement about fair prices, not a guarantee.

Condors are the
most ladder-dependent row: the wing is whichever listed distance is nearest half the
expected move, so one step changes the structure.

## Signal age and score trend

**Files:** `services/options_svc/compute.py` (`setup_key`, `merge_setups`) ·
`webgui/pages/options/persistence.py` (the display vocabulary). Shown in the Market
Scanner's **Seen since** and **Score trend** columns and on the Symbol Dossier's
signal rows.

**The setup key.** A signal's `id` encodes its strikes, and strikes are chosen by a
delta band, so one increment of spot mints a new `id` for what is economically the
same trade. Age is therefore tracked on a coarser key:

```
setup_key = SYMBOL | TYPE | EXPIRATION        e.g.  MU|PCS|2026-10-17
```

Strikes are excluded; `EXPIRATION` is the row's front expiration. A row missing any part
gets no key and shows a dash — it is never folded into another setup. Two adjacent
strikes on one expiration are two rows that share one age. The key is a **lookup only**:
row identity, the "New" badge and the Paper button all stay on `id`.

**What each scan records per setup** (on `cache:options:scan_day` → `setups`):

| Field | Rule |
|---|---|
| `seen` | +1 for every scan in which the setup has at least one live row |
| `scores` | that scan's **best** composite among the setup's rows, appended; the last 40 kept |
| `gaps` | +1 each time the setup returns after missing one or more scans (detected by scan sequence number, not the clock — a manual scan breaks the 15-minute grid) |
| `first_seen` | the scan time the setup first appeared — **only** when that was observed |
| `age_unknown` | set instead of `first_seen` when a setup appears in a scan that had no usable previous envelope (flushed, or from another date) **outside** the first 15 minutes of the scan window — at the day's first scan the time is exactly right, later it would be a guess |

A first-seen time is never stamped with "now" to fill a gap in knowledge: a restart at
noon would otherwise date every 09:00 setup to 12:00.

**The trend** (`persistence.score_trend(scores, window=4, deadband=2.0)`):

```
fewer than 4 readings            -> "new"      (no direction claimed)
delta = scores[-1] - scores[-4]
|delta| <= 2.0                   -> "steady"   (shown  ▬ +1.0)
delta >  2.0                     -> "rising"   (shown  ▲ +4.2)
delta < -2.0                     -> "fading"   (shown  ▼ -6.1)
```

Four readings is one hour at the 15-minute auto-scan cadence. The deadband exists
because the composite is recomputed from scratch on every scan, so a point or two of
movement between scans is noise. A trend is only ever computed within one setup, so
the credit-spread composite and the Directional tab's Fit + Quality score never meet.

### Other structures on the Market Scanner

Since 2026-10-06 each scan also builds, for the 0-DTE window (DTE 0–4) and the Swing
window (DTE 5–15), every structure that is not a credit spread
(`options-scanner/structure_scan.py`, called from `run_full_scan`):

| Family | Structures | Window |
|---|---|---|
| `VERTICAL` | bull call spread, bear put spread | both |
| `STRADDLE` | long and short straddle, long and short strangle | both |
| `BUTTERFLY` | call and put butterfly, iron butterfly, call and put condor | both |
| `CALENDAR` | call and put calendar, call and put diagonal | Swing |
| `RATIO` | call and put ratio backspread (since 2026-10-07) | both |

The builders are the Strategy Finder's, with one difference: the Finder keeps
straddles, strangles, butterflies and condors at least seven days out, and the
Scanner builds them at its window's own minimum. A calendar's front leg is still at
least seven days out; its back month is read from the +20 to +45 day chain the scan
already fetches for the volatility reading, so the pass makes no Schwab call. A short
strangle sells between `[structures] short_delta_min` (0.15) and `[selection]
max_entry_short_delta` (0.27).

Each candidate is scored on Fit + Quality and then passes four gates, in this order:

1. **Volatility.** `[iv_rank]` refuses a trade that sells premium below the window's
   floor; `[iv_rank_ceiling]` (off) would refuse one that buys it above the ceiling.
   An unknown rank skips the gate.
2. **Earnings.** A trade that would be held through a report (the same test the
   credit scan applies, read on a calendar's later expiry) is kept and marked if it
   buys premium (`[structures] earnings_long_premium = "flag"`) and dropped if it
   sells premium or its vega cannot be read. `"drop"` removes both.
3. **Quality.** Score at or above `[structures] min_score` (50) and grade not in
   `excluded_grades` (Weak).
4. **Cap.** The best `[structures] max_per_family` (2) of each family, per symbol,
   per window.

The momentum veto, the sentiment regime filter and the dealer-gamma gate are not
applied: they exist to stop selling premium into a trend, and Fit already scores
each structure against the inferred direction.

**What clears the bars.** Measured with `tools/sweep_strategy_gates.py --scanner` on
nine fairly priced synthetic chains (volatility 0.20, 0.28, 0.45; strike steps of
0.25%, 1% and 2.5% of spot; the clock pinned at 10:00 CT):

| Structure | 0-DTE window | Swing window |
|---|---|---|
| Debit spreads | all that could be built | all |
| Long straddle | all, scoring 53–56 | all, scoring 53–56 |
| Short straddle | none (probability of profit 57–58 against 65) | none |
| Long strangle | about half | most |
| Short strangle | most; never on expiration day | all |
| Butterflies, iron butterfly | a little over half | about four in five |
| Condors | half; never on expiration day | about four in five |
| Calendars | not built | put: all; call: fails with a 7-day front leg |
| Diagonals | not built | almost none |
| Call backspread | 30 of 36 | 22 of 27 |
| Put backspread | 22 of 35 | 18 of 26 |

The full tables and their parameters are in
`docs/plans/2026-10-06-scanner-multi-structure-design.md`. Probability of profit
reads the time actually left, so a short-dated figure depends on the time of day;
the sweep pins it for that reason. On expiration day the expected move used for the
breakeven factor and the butterfly wing is a full day's (`max(dte, 1)`), which by
mid-morning is more than twice the move left; expiration-day rows that buy premium
score higher for it (the long straddle 64 against 53–56).

**Cost.** About 0.1 second per symbol on a dense synthetic chain
(`tools/measure_structure_scan.py`), on the scan's own thread after the chains are
fetched.

**Ratio backspreads.** `strategy_scanner.build_backspreads` sells one option
nearest 0.50 delta and buys two of the same kind nearest 0.30 delta among the
strikes beyond it, on the nearest expiration in the window. With strike distance
*w* and net entry *n* (positive for a debit, negative for a credit, per share):

```
worst case (price at the long strike)  = (w + n) x 100 + commission
far breakeven, call                    = long strike + (w + n)
far breakeven, put                     = long strike - (w + n)
near breakeven (credit entries only)   = short strike -/+ n      (call / put)
```

It is built only when the net is a credit under *w*, a debit of at most
`[structures] backspread_max_debit_frac` (0.25) of *w*, or even money. A credit
at or over *w* cannot occur on real quotes and is refused as a bad mark.

Two scoring rules exist because of it:

- **The breakeven factor reads the far breakeven.** `q_be` scores a directional
  row on its nearest breakeven. A backspread entered for a credit has a nearer
  one beside its short strike, where its loss zone begins, so the builder names
  the far one (`target_breakeven`) and the factor reads that. No other structure
  sets the field.
- **The reward gate.** The `LONG` profile passes any trade whose profit is
  unbounded. It used to detect one by "reward to risk undefined and a debit
  paid"; it now also accepts `unbounded_profit`, because a call backspread
  entered for a credit has unbounded profit and no debit.

Backspreads use the `LONG` gate profile. Under `DEBIT` no call backspread is ever
shown (0 of 63 in the measurement above) because its reward to risk is undefined.
What the measurement showed: every backspread entered for a credit passed
(probability of profit 63–69) and every one entered for a debit was cut (16–21,
against a bar of 30). That 63–69 is mostly the chance of keeping the credit. The
put version's best case is the stock at zero, so its reward to risk reads 40–100
and it scores 63–77 against the call's 55–70.

The rows are published as `structures_0dte` and `structures_swing` on
`cache:options:scan` and the day union. They are not pushed to the phone and not
counted on the Opportunity Board. They are recorded to `signals.db` as
**tracked** rows, below.

### Tracked structures

**Files:** `options-scanner/signal_recorder.py` (`record_tracked`),
`options-scanner/structure_marks.py`, `services/options_svc/tracked.py`

After a scan that finishes inside regular hours, the two structure lists and the
Directional tab's rows are written to `signals.db` under scanner type
`0DTE_STRUCT` or `SWING_STRUCT` (a Directional row goes to the first when its DTE
is at or under the 0-DTE window's maximum). They are measured and never traded:
every other reader of the store leaves them out, and the paper Account refuses
them by type and by structure.

| Setting | Default | Meaning |
|---|---|---|
| `[capture] tracked` | on | records them at all |
| `[capture] max_open_per_symbol_tracked` | 1 | open tracked rows per symbol within one family |
| `[capture] max_open_per_family_tracked` | 10 | open tracked rows per family, across all symbols |
| `[scores] capture_min_tracked` | 0 | score floor; the scan's own bar has already applied |
| `[tracked] mark_interval_min` / `mark_offset_min` | 15 / 10 | priced at :10, :25, :40, :55 |
| `[tracked] front_expiry_close` | 14:00 | when a calendar is closed on its front expiry day |

**Which rows are recorded.** The family is the scan's group (`VERTICAL`,
`STRADDLE`, `BUTTERFLY`, `CALENDAR`, `RATIO`) or `DIRECTIONAL` for a single call
or put. Both tabs' rows are taken together. Within a family, while it holds
fewer than `max_open_per_family_tracked` open rows, the next row recorded is the
one whose structure has the fewest open rows in that family, and among those the
highest score; a row whose symbol already holds `max_open_per_symbol_tracked` in
that family is passed over. The order is not score alone because the structures
score in bands: on one measured session (2026-10-06) the second-best single
option outscored every long straddle on 88 of 119 symbols.

**Stored values** are per share. `entry_credit` is signed (a debit is negative).

**Mark.** Each leg at the mid of its bid and offer, on its own expiration's
chain:

```
value = sum(short leg mid x qty) - sum(long leg mid x qty)      # cost to close, per share
pnl   = (entry_credit - value) x 100                           # dollars, one contract
```

A bought position that is worth money therefore has a negative `value`. A leg
with no usable quote means no mark for the row.

**Exit**, first match (`structure_marks.recommend`):

| Code | Condition |
|---|---|
| `FRONT_EXPIRY` | legs on two expirations, front expiry is today, time at or after `front_expiry_close` |
| `MONEY_STOP` | `entry_credit > 0`, the structure's `loss_rules` is on, and `pnl <= -stop_mult x entry_credit x 100` |
| `TARGET_HIT` | `pnl >= tp_frac x base x 100`, with `base` chosen below |
| hold | anything else |

`base` depends on whether the profit is open-ended, which is read from the legs:
the position is net long calls or net long puts.

| Structure | `base` |
|---|---|
| not open-ended (verticals, butterflies, condors, calendars, short straddle and strangle, iron butterfly) | `entry_max_profit` |
| open-ended, buys options only (long call, put, straddle, strangle) | the debit paid, `-entry_credit` |
| open-ended and sells an option (both backspreads) | none: held to expiry |

The stored max profit is not used for that test. For a put it is the profit with
the stock at zero, a finite number about a hundred times the debit.

`tp_frac` and `stop_mult` are `config/trade_mgmt.toml [stops]`, the credit
spreads' values (0.50 and 2.0 as shipped). `loss_rules` is off for a short put
and for both backspreads.

**Settlement.** At or after 15:00 CT on the expiry day, or any later day, a row
whose legs share one expiration is valued at intrinsic against the price
`paper_engine.settlement_underlying` returns (reason `EXPIRED`); with no usable
price it waits for the next cycle. A row on two expirations is not valued that
way. If it is still open then, it is closed `UNMARKABLE` with no `realized_pnl`.

**Results** (`tracked.stats`), per structure over closed rows with a P&L:

```
win_pct = 100 x (rows with pnl > 0) / n
avg_r   = mean( pnl / (entry_max_loss x 100) )
```

Rows closed `UNMARKABLE` are counted separately and enter neither. For a
structure with no capped loss `entry_max_loss` is a margin estimate, so its
`avg_r` is flagged and not comparable with a defined-risk one. These rows are
not part of `cache:options:calibration`.

**Seen since** reads `HH:MM · Nx` — first seen, and the number of scans the setup was
live in. It is a dash when the age is unknown, and the time alone when the count is
unreadable (never `· 0x`). A dropped row keeps the values it had. The dossier adds one
line per setup, *Live since 09:15 · 1 gap*, and a 64×16 px sparkline of `scores`.

---

# Technical Indicators

**File:** `shared/analysis_lib/technical.py`. All operate on OHLCV DataFrames.

**EMA** — `calculate_ema(df, period)`:

```
multiplier = 2 / (period + 1)
EMA[t]     = close[t]·multiplier + EMA[t-1]·(1 - multiplier)
```

Seeded with the SMA of the first `period` bars; implemented vectorized via
`ewm(alpha=multiplier, adjust=False)`.

**RSI** — `calculate_rsi(df, period=14)`:

```
avg_gain = EMA(gains, 14);  avg_loss = EMA(losses, 14)
RSI = 100 - 100 / (1 + avg_gain/avg_loss)
```

Defaults to 50.0 on insufficient data. Reference bands: oversold 30, overbought 70.

**ADX** — `calculate_adx(df, period=14)`. Wilder's directional movement, then
Wilder smoothing (RMA, `alpha = 1/period`) at every stage:

```
up   = high - high[-1]            # signed
down = low[-1] - low              # signed: a RISING low is a NEGATIVE down-move
+DM  = up    if (up > down   and up > 0)   else 0
-DM  = down  if (down > up   and down > 0) else 0
+DI  = 100 · RMA(+DM, 14) / RMA(TR, 14)
-DI  = 100 · RMA(-DM, 14) / RMA(TR, 14)
DX   = 100 · |+DI - -DI| / (+DI + -DI)
ADX  = RMA(DX, 14)
```

(TR = true range.) >25 ≈ strong trend; an inside day yields **neither** +DM nor -DM.

⚠ The two comparisons above are against the **raw** `up`/`down`, and `down` carries
its sign. Until 2026-08-20 this function used `|Δlow|`, which books every up-day as
downward movement too, and compared `-DM` against an already-filtered `+DM`. It read
**76.6 where the true value is 32.5** on a realistic tape, and a **dead-flat series
read ADX 100**. ADX feeds the Day trend needle, the Week/Month structural arcs, the
regime classifier's *trending* tells and the Trade page's momentum block, so readings
recorded before that date are not comparable with ones after it.

**MACD** — `macd_histogram_series(df, fast=12, slow=26, signal=9)`:

```
macd_line   = EMA(close,12) - EMA(close,26)
signal_line = EMA(macd_line, 9)
histogram   = macd_line - signal_line
```

It returns the full histogram series (so callers can read the prior bar for
acceleration).

**VWAP** — `calculate_vwap(df)`:

```
typical = (high + low + close) / 3
vwap    = cumsum(typical · volume) / cumsum(volume)
```

**Relative volume** — `calculate_relative_volume(df, period=20)`:
`today_volume / mean(prior period volume)`, returned with today's raw volume.

**Volume profile** — `calculate_volume_profile(df, num_bins=20)`: buckets closes
into 20 price bins by volume (vectorized via `digitize`/`bincount`), returning the
**POC** (highest-volume bin), and the **VAH/VAL** bounding the 70% value area.

---

# Trade Analyzer

**File:** `trade-analyzer/src/analysis/scoring.py`. Each primitive returns an
integer in `[−100, +100]`; verdict engines blend them into Position and Investor
Buy/Hold/Sell verdicts.

## Technical primitives

| Function | Mapping (abridged) |
|----------|--------------------|
| `score_rsi(rsi)` | <30 → −90, 30–40 → −60, 40–50 → −20, 50–60 → 60, 60–70 → 30, 70+ → −20 |
| `score_adx_directional(adx, ema_slope)` | sign = EMA slope; ≥25 → 100·sign, ≥20 → 60·sign, ≥15 → 30·sign, else 0 |
| `score_macd(hist, hist_prev)` | up&rising → 80, up&falling → 30, down&rising → −20, down&falling → −80 |
| `score_relative_volume(rv, ema_slope)` | >1.5 → 60·sign, ≥1.0 → 20·sign, <0.7 → −30 |
| `score_vwap(price, vwap)` | far above → 60/30, far below → −80/−40 by % distance |
| `score_volume_profile_location(price, vp)` | at POC → 0, in value area above POC → 50, below VAL → −60 |
| `score_relative_strength_percentile(p)` | `round((p − 0.5)·200)`, clamped ±100 |
| `score_distance_from_52wk_high(d)` | ≤5% → 60, ≤15% → 20, ≤30% → −20, >30% → −60 |

## Fundamental primitives

| Function | Mapping |
|----------|---------|
| `score_pe_vs_sector(pe, sector_median)` | ratio ≤0.7 → 60, ≤1.0 → 30, ≤1.3 → −10, >1.3 → −50 |
| `score_peg(peg)` | <1 → 40, ≤2 → 0, >2 → −40 |
| `score_growth_metric(g)` | >0.15 → 80, ≥0.05 → 30, ≥0 → 0, ≥−0.05 → −30, else −80 |
| `score_roe(roe)` | >0.15 → 60, ≥0.05 → 20, else −40 |
| `score_earnings_surprise_streak(s)` | ≥4 beats >5% → 80, last miss <0% → −60, else 0 |
| `score_guidance_direction(d)` | RAISED → 40, LOWERED/CUT → −60, else 0 |

## Fundamentals parsing

**File:** `trade-analyzer/src/analysis/fundamentals.py`. The Schwab
`/instruments?projection=fundamental` payload is parsed as a **superset**: the real
Schwab fields are primary (`revChangeTTM`/`epsChangePercentTTM` as percent →
fraction; `returnOnEquity` as percent via a `>2` magnitude heuristic;
`operatingMarginTTM` vs `MRQ` for the margin trend), with legacy speculative names
as fallback. Fields the payload does not carry (next-earnings date, EPS surprises,
guidance, FCF) degrade to `None`, so those gates simply never fire.

## Validated swing model (Position, 1–8 wk)

**Files:** `trade-analyzer/src/analysis/factors.py` (factor library) +
`trade-analyzer/src/analysis/backtest.py` (IC engine) +
`trade-analyzer/fit_swing_model.py` (offline orchestrator) +
`trade-analyzer/data/swing_model.json` (artifact) +
`services/trade_svc/swing_model.py` (live scorer). The **Short Term** verdict's
hand-weighted scoring is replaced by a **backtested, IC-weighted cross-sectional factor
model** whose weights are learned from forward returns. Investing (months+) is deferred
(no point-in-time fundamentals source). Architecture: **offline fit → versioned
artifact → online score**.

**Factor library.** Each factor is `(daily_df) → pd.Series`, **sign-corrected so higher
= more bullish**, and **causal** — the value at bar *t* uses only data ≤ *t* (no
look-ahead). Winsorization and standardization are applied **cross-sectionally at
scoring** (across symbols per date), never per-factor over a symbol's own history (which
would leak future bars into a past value and inflate measured IC). The live value is the
Series' last element, so the same code feeds the backtest and the scorer.

| Factor | Definition (sign-corrected) | Rationale |
|--------|-----------------------------|-----------|
| `mom_12_1` | 12-month return, skip the last month | Intermediate continuation; skip-month avoids short-term-reversal contamination |
| `mom_6_1` | 6-month return, skip the last month | Shorter-memory momentum |
| `pth` | price ÷ 252-day high | 52-week-high anchoring (George & Hwang) |
| `str_5d` | −(5-day return) | Short-term reversal / entry timer |
| `vol_adj_mom` | 3-month return ÷ 60-day realized vol | "Sharpe momentum" |
| `trend_quality` | distance above the 50/200-EMA stack (+ slope) | Trend-following premium |
| `low_vol` | −(60-day realized vol) | Low-volatility anomaly |
| `rs_spy` | 63-day excess return vs SPY | Cross-sectional momentum |
| `rs_sector` | 63-day excess return vs the sector ETF | Idiosyncratic strength |
| `turnover` | volume ÷ 63-day average volume | Conditioning variable (turnover) |

The `FACTORS` registry is the single source of truth; **the harness's IC decides which
factors earn weight**, not the hand-picked list.

**IC engine** (`backtest.py`, pure; operates on a `(date, symbol)`-MultiIndex factor
panel + an aligned forward Series):

- `factor_ic` — per-date cross-sectional **Spearman rank IC** of a factor vs the forward
  excess return, summarized as `{mean_ic, icir, n_days}`. **ICIR = mean_ic / σ(daily IC)**
  is only trusted with ≥ 5 IC-days and real daily-IC dispersion (else 0).
- `zscore_by_date` — per date, across symbols: winsorize to the **2/98** cross-sectional
  band, then standardize `(x − mean) / std`. Look-ahead-free (only same-date data).
- **`signed_ic_weights`** — the production weighter: `weight_k = mean_ic_k / Σ|mean_ic|`,
  **keeping the sign**, for factors whose `|mean_ic|` clears an n-independent noise floor.
  A *wrong-sign-but-predictive* factor (e.g. low-vol with a negative IC in a high-beta
  regime) gets a **negative** weight and contributes with the correct sign; the
  `|weights|` sum to 1. Chosen over ICIR- or t-stat-weighting because those are
  n-dependent (a daily-IC ICIR is ≈ √252× smaller than a monthly one) and unstable across
  small per-fold samples.
- `composite` — the weighted sum of z-scored factors.
- **`walk_forward`** — rolling **train → test** (train/test/step **378 / 63 / 63**
  trading days): fit weights on each train window, score the *unseen* next test window,
  collect the composite's **out-of-sample IC**. Train and test never overlap within a
  fold; test windows tile when step = test.
- `calibrate` — bucket composite scores into 5 quantile bands; per band record the score
  range, **mean forward return**, and **hit-rate = P(forward > 0)**. The mean-forward and
  hit-rate are **isotonic (pool-adjacent-violators) smoothed** across the score-ordered
  bands, so thin-signal sampling noise can't make a higher-ranked band show a lower stat.
  This replaces the old ±40 score cuts — the BUY/SELL bands are the top/bottom calibrated
  bands.

**Offline fit** (`fit_swing_model.py`, run **monthly by a systemd timer (the 1st, 19:00
CT) — never in the request path**; a fit replaces the live artifact only if it passes the
ship gate in `config/swing_model.toml`, see the scheduled-jobs table): pulls ≈ 78 liquid symbols' **5-yr** daily history via the proxy (a curated
`UNIVERSE_SECTOR` map → sector ETFs; concurrent), builds a `(date, symbol)` panel with
**20-day forward EXCESS-return-vs-SPY** labels (the prediction target; factors are causal,
so using the future H-bar return as the label is legitimate), computes per-factor IC +
the signed weights + the calibration + the walk-forward OOS IC, and writes the artifact +
a markdown research report (both gitignored under `trade-analyzer/data/`).

**Artifact** `swing_model.json` (`repo_paths.SWING_MODEL`): `version` (the fit date),
`fit_universe_n`, `horizon`, and per regime key (`"all"` today; the loader/scorer are
**C-ready** for `"trend"/"chop"/"highvol"` later) the signed `weights`, per-factor
`factor_ic` (`mean_ic`/`icir`/`n_days`), the cross-sectional `norm` (per-factor
time-averaged winsorized cross-sectional mean/std — the basis the calibration was built
on), the `calibration` bands, and `oos_ic` + `oos_ic_by_fold` + `n_folds`.

**Live scorer** (`swing_model.py`, on-demand inside `analyze()`; **defensive** — returns
`None` on any failure so `analyze()` falls back to the legacy verdict). For the symbol's
current factor values it computes **cross-sectional** z-scores — each factor standardized
against the SAME factor across the **current universe snapshot** (`cache:trade:universe_factors`,
built over the artifact's `fit_universe`, ~78 names). This RE-CENTERS to today's regime,
matching how the per-date calibration was built; the artifact's time-averaged `norm` is a
FALLBACK only (used when the snapshot is too thin, <5 names). **(This re-centering fixed a
"Position always BUY" bug — norm-primary scoring did not re-center, so in an elevated
regime every symbol's z shifted positive into the top/BUY band.)** Each z is **clipped to
±3** (`Z_CLIP`, matching the fit's per-date 2/98 winsorization, so a live outlier such as a
turnover spike can't hijack the signed composite). Then:

```
composite  = Σ_k  signed_weight_k · clip(z_k, −3, +3)
band       = the calibration band whose [score_lo, score_hi] contains the composite
verdict    = BUY (top band) | SELL (bottom band) | HOLD (otherwise)
percentile = band-quantile midpoint  (e.g. top band of 5 → ~90th)
expected   = band.mean_fwd ;  hit_rate = band.hit_rate  (P beat-SPY over the horizon)
```

`analyze()` fetches **2-yr** daily history so every long-warmup factor (`mom_12_1` needs
252 + 21 bars; `pth`/`low_vol` roll 252) populates at the last bar.

**Honest caveats (acceptance gate = positive OOS IC + a meaningful spread on real data).**
The current fit (`version` 2026-06-28) shows composite **OOS IC ≈ +0.0367** across **13**
folds — but **5 of those folds are negative**, so the edge is thin and **regime-
dependent**. Top quintile ≈ **+1.35% / 4 wk at 52.3% beat-SPY**; bottom ≈ **−0.80% /
43.3%**. The signed weights that cleared the floor: **low_vol −0.34** (reclaimed with a
*negative* weight — high-vol names outperformed in this large-cap bull-ish period),
**mom_12_1 +0.21**, **mom_6_1 +0.17**, **trend_quality +0.12**, **rs_sector +0.08**,
**turnover +0.07** (`pth`/`str_5d`/`vol_adj_mom`/`rs_spy` fell below the floor → 0).
**Survivorship** (the fit universe is today's survivors) and **regime non-stationarity**
caveats apply; the model leans on low_vol's inverted sign, which could flip. Validation
reduces self-deception; it does not guarantee forward performance — which is why
it is **refit monthly**, and why a refit ships only past the gate. Regime-conditional weighting is the planned next
step (same harness, new regime keys).

## Markov 2.0 forecast (Position)

**Files:** `trade-analyzer/src/analysis/markov.py` (pure math) +
`services/trade_svc/compute.py` (reconstruction, prior, wiring). A probabilistic
forward layer on the **Short Term** composite score, rendered as the third card in the
verdict row. *(It forecasts the **legacy** technical-momentum `composite_daily`, a
separate lens from the validated swing model above — a documented coexistence.)*

**States — 5 score bands** anchored at the verdict's decision boundaries
(`classify_band`):

| Band | Composite-score range | Verdict zone |
|------|-----------------------|--------------|
| 0 Strong-Bear | [−100, −40) | SELL |
| 1 Weak-Bear | [−40, −15) | HOLD |
| 2 Neutral | [−15, +15) | HOLD |
| 3 Weak-Bull | [+15, +40) | HOLD |
| 4 Strong-Bull | [+40, +100] | BUY |

**Markov base score (`composite_daily`).** The live verdict mixes intraday-only
factors (intraday VWAP, intraday relative volume, multi-timeframe EMA alignment) that
cannot be reconstructed for past bars, so the chain instead runs on a parallel
**daily-only** composite computed identically for every historical bar and for "now"
(`reconstruct_daily_composite`): EMA-alignment (price vs the daily 12/21/50/200 EMA
stack), ADX (directional), RSI, MACD, daily relative volume, distance-from-252-day-
high, RS vs SPY (63d/126d), and sector-ETF-vs-SPY RS — the daily-reconstructable
subset of the Position factors, weights renormalized to a 100-point scale. A bar with
a missing close is excluded (it is not an observation).

**Transition matrix — hybrid (per-symbol + pooled prior).** Day-to-day band
transitions over ~1 yr of `composite_daily` form a 5×5 count matrix `C_sym`
(`count_matrix`). Each row is Bayesian-shrunk toward a pooled prior via a
Dirichlet-multinomial blend (`shrink`, α = 30):

```
P[i,j] = (C_sym[i,j] + α · Prior[i,j]) / (Σ_j C_sym[i,j] + α)
```

The **pooled prior** (`build_pooled_prior` / `get_prior`) aggregates band transitions
across a curated 17-symbol universe, row-normalized (`pooled_prior`); it is cached at
`cache:trade:markov_prior` and rebuilt lazily once per day (uniform fallback on
failure).

**Forecast** (`forecast`). From the current band, the n-step distribution is
`dist₀ · Pⁿ` (`project`) for n = 5 / 10 / 20 trading days, yielding **P(BUY)** =
P(band 4), **P(SELL)** = P(band 0), and **E[score]** = Σ midpoint·prob over band
midpoints `[−70, −27.5, 0, +27.5, +70]`. The row's self-transition probability is the
**persistence**; the **stationary** (long-run) distribution is found by power
iteration (robust to reducible chains).

**Drift tilt** (`drift_tilt`, `row_confidence`). The expected forward move drives a
bounded, confidence-weighted adjustment to the displayed score:

```
drift      = E[score @ 10d] − composite_daily_now
confidence = n / (n + 40)            # n = observed transitions out of the current band
tilt       = clip(0.5 · drift, −12, +12) · confidence
markov_adjusted_score = clip(composite_full + tilt, −100, +100)
```

`composite_full` is the live (intraday-enriched) Position score. **No feedback by
construction:** the chain is built only from `composite_daily`, so the tilt added to
`composite_full` can never feed back into the matrix. The tilt moves the **score**
only — the Buy/Hold/Sell **label** is never re-derived from it. Every step is
defensive: any failure yields no Markov block and the verdict is unchanged.

---

# Portfolio Analytics

**File:** `portfolio-analyzer/src/`.

**Position classification** (`sectors.classify_positions`): each position is tagged
with a sector and sector ETF (futures are bucketed as "Futures" and excluded from
normal classification).

**Sector weights** (`sectors.sector_weights`): market value by sector over the
**absolute-value** total, so shorts don't distort weights:

```
weight_sector = Σ(market_value in sector) / Σ|market_value|
```

**vs Benchmark** (`weights_vs_benchmark`): `my_weight[sector] − benchmark[sector]`
(positive = overweight).

**Holding vs sector RS** (`holding_vs_sector`): the stock's trailing return vs its
sector ETF over several lookbacks (100 = parity), via
`shared/analysis_lib/sector_analysis.calculate_stock_vs_sector_rs`.

**Since-purchase excess** (`since_purchase_vs_sector`): `stock_return −
sector_return` since entry.

**RRG quadrants** (`compute_rrg_quadrants`, `rs_window=50`, `mom_window=20`): same
quadrant rule as the sentiment RRG.

**Evaluation** (`evaluation.py`): a split-speed scorecard — slow **baselines**
(relative-strength percentile, technical and fundamental scores) computed at load
and refreshed on a cadence, plus a fast **live** P&L update per tick —
producing per-position letter grades (Return / Capital / Risk / Entry), a composite,
annualized return, and drawdown, with advisory suggestions.

---

# Black-Scholes & the Simulator

**File:** `options-scanner/options_calculator.py`.

## Pricing and Greeks

```
d1 = [ln(S/K) + (r + σ²/2)·T] / (σ·√T)
d2 = d1 - σ·√T
```

| Greek / price | Call | Put |
|---------------|------|-----|
| Price | `S·N(d1) − K·e^(−rT)·N(d2)` | `K·e^(−rT)·N(−d2) − S·N(−d1)` |
| Delta | `N(d1)` | `N(d1) − 1` |
| Gamma | `n(d1) / (S·σ·√T)` | same |
| Theta (per day) | `[−S·n(d1)·σ/(2√T) − r·K·e^(−rT)·N(d2)] / 365` | put sign on the second term |
| Charm | `−n(d1)·[2rT − d2·σ√T] / (2T·σ√T)` | call value `+ r·e^(−rT)` |
| Vanna | `−n(d1)·d2/σ / 100` (per 1 vol point) | same |

(`N` = standard-normal CDF, `n` = its PDF.) At/after expiration (T ≤ 0), price is
intrinsic value and second-order Greeks are 0.

**`r` is `options_calculator.RISK_FREE_RATE = 0.045`, static, and it is the single
source** — the calculator, the simulator, `gamma_tool` and `options_svc.compute`
(`calc_iv` and the forward projection band) all import it rather than declaring their
own. (`backtest_0dte` was in that list until 2026-08-20, when it was deleted with the
rest of the superseded CLI tooling.) It is a **fixed assumption, not a live rate**: nothing fetches
a Treasury yield, so a real move in short rates does not reach the model until this
constant is edited. That is a defensible simplification for the horizons this app trades
— at 0DTE a half-point of rate is worth about **0.23%** of the option price (measured on
a 6800/6750 SPX-like put at 15% vol; 0.33% at 1 DTE, 0.69% at 7 DTE) — but it is an
assumption, and on multi-week swing structures it is the least accurate input in the
model. **`q = 0`**: no dividend yield, which matters most for SPY/SPX at roughly a 1.3%
annual yield on longer-dated positions.

## Spread metrics and P&L grid

`calc_summary(legs, strategy, spot, r, iv, T)` returns credit/debit, max
profit/loss, break-even(s), strike width, and probability of profit — closed-form
for `PCS`/`CCS`/`IC` and the four single-leg strategies.

`calc_summary_generic(legs, spot, r, iv, T)` covers **any other structure**
(butterfly, condor, calendar, diagonal, or a hand-edited set): it evaluates net P&L
across a 601-point price grid at the **front (nearest) leg expiry** — calendars price
the back leg via Black-Scholes at its remaining `T` — and reads **max profit/loss** as
the curve's extremes, **break-evens** as its zero-crossings, and **PoP** as the
risk-neutral lognormal mass over the profitable region (same at-expiration convention
as the analytic path). The service `compute.calc_compute` routes to the analytic
function when the strategy code is one of the seven closed-form cases and the legs
still match that template, else to the generic one (via `strategies.summary_code`).

`calc_spread_pnl(legs, spot, iv, rate, eval_dates, price_range, expiry_date, iv_adjustment=0, per_leg_expiry=False)`
re-prices every leg via Black-Scholes at each (price point × evaluation date) and
sums P&L into the grid that drives the Calculator's P&L matrix. An `iv_adjustment`
shifts IV at every point for shock scenarios. With `per_leg_expiry=True` each leg is
priced at **its own** time-to-expiry per column (`t_leg = leg_T0 − elapsed`), so a
**calendar/diagonal** shows the back leg retaining value at the front-leg expiry;
legs that omit an `expiry` fall back to the column `T` (single-expiry output is
byte-identical to the legacy path). Each leg's `leg_T0` comes from
`_leg_expiry_years`, which settles at **16:00 America/New_York** via the shared
`expiry_time_to_years` — the same instant the summary tiles and the simulator use.
(It previously built its own naive 4pm against a host-local clock, which on the CT
box resolved to 17:00 ET: the grid carried an extra hour of time value in every
cell, and the T=0 "Exp" column showed premium instead of the kinked payoff.)

## Simulator engines

**File:** `options-scanner/options_simulator/`.

Positions are **multi-leg**: `Position(legs=[Leg(contract, sign, ratio), …])`, and
`aggregate_position` runs the per-leg pricer then scales each leg's Greeks by
`sign · ratio` before summing — the `ratio` field (default 1) lets a 1-2-1
**butterfly body** trade at 2×. `Position.from_legs([(contract, sign, ratio), …])`
builds one from resolved contracts.

- **What-if** (`WhatIfEngine`) — sweeps an 81-point ±20% price range. The service
  `compute.sim_run` advances **each leg by `Δt` elapsed days from now**
  (`t_leg = max(leg_DTE − Δt, …)` via `compute._leg_days_to_expiry`), so same-expiry
  structures decay together while a **calendar** decays each leg on its own clock.
  `leg_DTE` is **fractional and intraday-aware** — calendar hours to the 16:00 ET
  close / 365, the same convention as the Calculator. (Until 2026-08-20 it used
  whole-day `(expiry − today).days` floored at 0.01 days, so a 0-DTE leg priced at
  T ≈ 14 minutes however many hours were left — and the P/L baseline it is measured
  against used that same wrong T.)
- **IV shock** (`IVShockEngine`) — compares the position at base IV vs `IV × mult`
  (each leg already priced at its own expiry).
- **Replay** (`ReplayEngine.full_trace`) — steps the position bar-by-bar along the
  underlying's recent path, re-pricing and computing all Greeks at each bar. The
  service (`compute.sim_replay`) wraps this and compresses overnight/weekend gaps
  onto a consecutive integer x-axis for the six-panel chart.

**Replay time basis (fixed 2026-09-11).** Each bar is priced with
`expiry_time_to_years(bar_time, expiry)` — years to the 16:00 ET settlement — and
that helper reads a **naive** datetime as Central (`NAIVE_WALLCLOCK_TZ`). The proxy
client hands over its candle stamps as **naive UTC** (`pd.to_datetime(ms,
unit="ms")`), so `compute._replay_index` converts them first: take a naive stamp as
UTC, convert to Central, drop the zone.

| bars | Schwab stamps them | the Replay index |
|---|---|---|
| intraday | the bar's own minute, epoch-ms UTC (the 08:30 CT open arrives as 13:30) | that minute in Central |
| daily | midnight **Central** (05:00 UTC in summer, 06:00 in winter), so the date survives the conversion | the regular close, 15:00 CT (`market_calendar.regular_close_on`), never later than now |

Until 2026-09-11 the stamps went through unconverted, so every intraday bar was
priced five hours late (six in winter): a 0-DTE replay treated the option as
expired from about 10:00 CT on, and the axis labelled the open 13:30. Daily bars
were priced at 05:00 CT, ten hours before their close. A daily bar is stamped at
the close because that is when its price was printed; at midnight it would carry
fifteen hours of time value it never had.

Both `compute.sim_run` and `compute.sim_replay` take a `legs` list (each
`{kind, strike, expiry, side, qty}`) and remain **backward-compatible** with the
legacy single-contract positional arguments.

**Units (2026-09-11).** Every Simulator figure is in **position** units — the engine's
per-share value and Greeks times the contract multiplier (100) and the leg quantity.
The What-if rows always were; the IV-shock rows and the Replay Greeks were per share
until 2026-09-11, so the same spread read in thousands on one tab and tens on the next.
Both payloads now carry `units: "position"`, and the page scales a payload without the
marker (a pre-upgrade cache) itself. Theta is dollars per day and vega dollars per
volatility point, because `bs_theta` is per day and `bs_vega` per 1 vol point.

**Replay P/L.** `sim_replay` also returns `value` (the position's dollars per bar) and
`pnl` (`value − value[0]`, i.e. as if opened at the start of the window).

**The page's readouts** are pure functions in `webgui/pages/options/sim_view.py`. The
position tiles' max profit, max loss and breakevens solve the expiration payoff
exactly — it is piecewise linear with corners only at the strikes, so `{0} ∪ strikes`
plus the slope past the last strike (the net call quantity × 100) decides every figure,
the method of the Calculator's `max_loss_estimate`. The entry is `−whatif_baseline`,
the model value at today's spot and time. `sim_run` echoes the `symbol`, `legs`, `dt`
and `mult` it priced so the page never pairs new legs with the previous legs' price.

---

# GEX / Gamma

**File:** `options-scanner/gamma_tool.py`.

## Per-strike gamma exposure (GEX)

The standard GEX contribution per strike (calls positive, puts negative):

```
GEX_strike = gamma · open_interest · 100 · spot²
```

In code (`gamma_tool.py`) the per-strike value is computed as
`gamma · OI · 100 · spot · spot · 0.01` (the `· 0.01` scales the result for
display). A **Volume** variant substitutes total volume for open interest. The
engine returns, per strike, the call, put, and net values.

### What a bar or a heat-map cell draws

The Dealer Positioning **Value** picker chooses one of four numbers from each
strike's stored `{call, put, net}` (`webgui/pages/options/gamma_heat.py`,
`cell_value`):

| Value | Formula |
|-------|---------|
| Net | `net` (= `call + put`; puts are stored negative) |
| Calls | `call` |
| Puts | `put` |
| Size | `abs(call) + abs(put)`, given the sign of `net` (positive when `net` is zero) |

A cell missing either side has no Calls, Puts or Size value and is left out; it is
never drawn as zero. A session stored before cells carried both sides can draw Net
only, and the picker is disabled for it.

**Balanced strikes.** A strike on screen is marked *Balanced* when both hold:

```
abs(net) / (abs(call) + abs(put)) <= max_polarity          (0.15)
size >= the min_size_quantile rank of the strikes on screen (0.80)
```

At most `max_marks` (3) are marked, largest first. The three numbers are
`config/gamma_heat.toml` `[balanced]`, read on every paint, so a change in
Settings → Configuration applies with no restart.

**Level or change.** The **Show** picker draws the value, or its change
(`gamma_heat.delta`):

```
change[strike][t] = value[strike][t] − value[strike][basis(t)]

Change since open     basis(t) = the session's first column
Change over N min     basis(t) = the latest column at least N minutes before t
```

`N` is `config/gamma_heat.toml` `[show] change_window_min` (30). A cell is left
blank when the strike has no reading at the basis, or when no column is old enough
yet; it is never drawn as a change from zero. The change is taken per strike, before
the spot frame's resampling. The by-strike bars draw the same quantity for the
latest column: the live value less the value in that column's basis row
(`gamma_heat.basis_grid`).

The Greek values are weighted by open interest (`use_volume=False` in the collector),
and open interest is published once a day. So a change within a session comes from
the Greeks moving (spot, time to expiry, implied volatility), not from a change in
position size, which the feed does not report during the session.

**Colour scale.** The heat map's colours are symmetric about zero. The **Scale**
picker sets where they clamp:

| Scale | Colour maximum |
|-------|----------------|
| Locked | The view's `scale_lock` for the value drawn, once the session has one; until then, the Adaptive figure |
| Adaptive | The 95th percentile of the absolute values on screen, recomputed on every paint |
| Share of column | The same percentile, taken over cells that are each `100 · value / Σ abs(value)` of their own column, across the strikes on screen |

**The lock** is computed by the options service, not the page
(`services/options_svc/gamma_window.py`, `scale_lock`), and published in every view
of the gamma snapshot:

```
scale_lock = { minutes, net, call, put, size }
each figure = quantile( abs(cell) over the session's first `minutes`,
                        inside the display window around each minute's own spot )
              · headroom
```

`minutes` (60), `quantile` (0.95) and `headroom` (1.5) are `config/gamma_heat.toml`
`[lock]`. The figure is `None` until those minutes have passed. It is taken from the
uncropped history rows, which are append-only for a session, so it is identical on
every later build while those three numbers are left alone (changing one mid-session
moves the lock within a minute). The page could not compute it: the rows it receives are cropped to
a window that follows spot, and a lock taken from them would drift.

The by-strike bars take the lock as a **soft** axis extent (Highcharts
`softMin` / `softMax`): the axis holds at the lock while the data fits and widens for
a larger bar. In Size the bars' extent is the larger of the call and put locks.

The strip beside the controls prints the maximum in use. The unit follows the
formulas above: dollars of gamma per 1% move for GEX, dollars of delta for DEX. Charm
and vanna exposures are printed as plain figures, and a share scale in percent.

**The spot frame.** With **Frame** set to *From spot*, each heat-map column is
resampled onto a uniform ladder of offsets from that column's own spot
(`gamma_heat.to_spot_frame`):

```
offsets  = -half·step … +half·step            (step = the strike ladder's step)
cell     = linear interpolation between the two strikes around  spot + offset
           that have a reading in that column
gap      = when that point is outside the column's strikes, or the two strikes
           are more than 2.5 steps apart (a hole in the ladder)
```

Nothing is extrapolated. The level tracks, drawn when **Level movement** is on, are
`level − spot` per column; the level lines sit at `level − current spot`; and the bars
are drawn at `strike − current spot`, so both panels share one axis.

`half` is `config/gamma_heat.toml` `[window] spot_side` (10). The options service
keeps the same number of strikes, plus one, each side of the session's **low** and
**high** in every view's published history (`gamma_window.crop_keep`), so a column at
the day's low still has strikes below it. The extra one is needed because price is
rarely exactly on a strike: the frame's outermost row then falls between the 10th
strike and the 11th, and interpolating it needs both.

That crop is the cost of the frame, and it was measured before it was built, on six
stored sessions (`tools/measure_gamma_crop.py`):

| `spot_side` | $SPX, widest stored day (1.1% range) | QQQ | $NDX | SPY, NVDA | $SPX, quiet day |
|---|---|---|---|---|---|
| 10 (shipped) | +11.9% history | +9.1% | +8.7% | 0 | 0 |
| 20 | +40.1% | +36.8% | +19.8% | +23.7%, +9.5% | +13.3% |
| 0 | the crop as it was before the frame; the frame then shows gaps | | | | |

The display window itself, `[window] n_side` (20 strikes each side of spot), is read
from the same file by the page and the service each time they draw or publish.

**Contour lines.** With **Contours** on, the page traces lines of equal value over
the heat map's own grid, after the Value, Show, Scale and Frame have been applied
(`gamma_heat.contours`, marching squares):

```
levels   = zmax, zmax/2, zmax/4, …        (`[contours] steps` of them, 3 as shipped)
           drawn above zero and, mirrored, below it
zmax     = the top of the colour scale in use (the lock, in Locked)
crossing = linear between two neighbouring strikes, or two neighbouring minutes
```

A cell with a gap in any corner is skipped, so no line is drawn through a value that
was not measured. A line spanning fewer than `[contours] min_columns` minutes (3) is
left out as a speck, and past `[contours] max_points` (6,000 a sign) the shortest
lines are dropped. Measured on a stored `$SPX` session the lines cost about 27 ms and
35 KB a repaint. They cover the collected minutes only, not the forward band.

**The gravity well.** With **Gravity well** on (Gamma view), a panel draws the
by-strike bars' own net values as ground (`gamma_well`):

```
height(strike) = −net gamma                       (`[well] height = "linear"`)
               = −sign(net) · √|net|              (`"root"`, as shipped)
ball           = price, on the straight line between the two strikes around it
downhill       = toward the lower of those two strikes, then on to the first
                 strike that is lower than both its neighbours (the low point)
```

Positive net gamma is below the zero line (a valley) and negative above it (a hill).
The caption's "positive" or "negative gamma" is read from the net values, never from
the drawn height, so it is the same on either scale. The square root keeps every
strike's side of zero and the order of the strikes; it does not keep depths in
proportion. Nothing here uses order flow, time or volatility: it is the bars redrawn,
not a prediction.

## Dealer delta exposure (DEX) and projection

The DEX/hedge panel sums `OI · delta · contract_multiplier · spot` across strikes
for the current and a projected delta, surfacing net-delta-now vs projected-close
hedging pressure.

## Directional walls

`get_directional_walls(gex_dict, spot)` returns one **put wall** (the strike below
spot with the largest put GEX) and one **call wall** (the strike above spot with the
largest call GEX) — the single-wall pair the Gamma page draws.

## Intraday collection

The options service collects GEX snapshots every minute within 08:00–15:20 CT on
trading days (from 06:30 CT for ETH-eligible symbols) (reusing the standalone collector's `poll_once`) into `gex_history.db`,
which feeds the strike × time heat map. The universe is the `[collection]` list in
`config/symbols.toml` plus the watchlist.

Every collected symbol's chain is fetched every minute. An optional second tier —
watchlist-only symbols fetched every third or fifth minute and carried forward in
between — exists and is **off as shipped**; see *Architecture Overview* → **Local
market data**.

## Hedging-flow model (HIRO)

**Files:** `services/options_svc/hiro.py` (pure measurement and rules),
`compute.hiro_tick_row` (the per-minute memo), `handlers._run_hiro` (detection and
publishing), `options-scanner/gex_history_db.py` (the `hiro_minutes` table). Design:
`docs/plans/2026-10-01-hiro-alert-design.md`.

A **model** of the idea behind SpotGamma's HIRO — the stock dealers must trade to
hedge the options customers trade — built from the 1-minute chain poll the collection
above already makes, so it adds **no Schwab calls**. Schwab publishes no
time-and-sales tape and no aggressor side, so each contract gets **one** buy/sell
label per minute, inferred from the quote. It is not SpotGamma's number, and its
thresholds are unvalidated starting guesses.

**Scope.** The symbols in `[hiro].symbols` (`$SPX`, SPY, QQQ, IWM), the chain the
collector fetches for them (expiries from today through **+7 days**), and the
**regular session only**, 08:30–15:00 CT (`market_calendar.is_regular_hours`).

**Per contract, per minute.**

```
dv     = totalVolume − previous totalVolume          (books only when dv > 0)
side   = +1 if last ≥ ask          (customer bought)
         −1 if last ≤ bid          (customer sold)
         else the side of the midpoint last sits on
         0 (unlabelled) exactly at the midpoint, or on an unusable quote
impact = side · delta · dv · 100 · spot              ($ of stock the dealer trades)
```

`delta` is the contract's own **signed** delta (call positive, put negative), so a
customer buying a call (+1 · +Δ) makes the dealer **buy** stock and buying a put
(+1 · −Δ) makes it **sell**, with no call/put branch. An unusable quote is a missing
or non-finite field, a bid below zero, a last or ask of zero or less, or a locked or
crossed quote (ask ≤ bid). Volume with no label — that, an exact-midpoint print, or a
delta that is non-finite, beyond ±1 (Schwab's `−999` sentinel) or of the wrong sign
for its right — is added to `unclassified_vol`, never guessed. A non-finite or
non-positive spot makes the whole minute unusable: nothing is written and the volume
baseline is left untouched, so the next good minute books that volume.

**The volume memo** (`compute._HIRO_MEMO`, in memory) holds each contract's last
`totalVolume` as a **high-water mark** (volume never falls within a session, so a
glitch read of 0 books nothing), cleared when the CT session date changes. A
contract's first reading only **seeds** it. A symbol whose last good minute is more
than **150 s** old (`HIRO_MAX_GAP_SEC`, about 2.5 polls) is re-seeded rather than
measured, so several minutes of volume are never booked under one minute's quote. A
minute in which the symbol had no baseline at all writes **no row** — a zero would
read as a quiet minute to the baseline.

**Storage — `hiro_minutes` in `gex_history.db`.** One row per symbol per minute:
`symbol`, `ts`, `spot`, `impact`, `classified_vol`, `unclassified_vol`, primary key
`(symbol, ts)`. A second row for the same minute **adds** its impact and volumes
(spot takes the newer value), because two ticks can share a minute and replacing
would lose the first one's volume. A non-finite impact or volume refuses the insert.
Its retention is its own, `[hiro].keep_sessions` (20) — never fewer than
`baseline_sessions + 1` — because the GEX snapshots keep only 5 sessions and the
baseline needs 5 sessions **before** today.

**Normal size σ.** The **root-mean-square** of the symbol's full 15-minute window sums
over its last `baseline_sessions` (5) stored sessions before today:

```
σ = sqrt( mean( S² ) )   over every full-window sum S in those sessions
```

RMS, not a standard deviation: hedging flow's natural centre is zero, and a day-long
drift is the signal, not noise to subtract. A window is "full" when it ends at least
one window after the session's first stored row. With fewer prior sessions (or none
with a usable window), σ comes from today's own full windows once at least
`min_minutes` (30) rows exist; before that, neither rule runs. The prior-session σ is
memoized per symbol per day.

**Surge (`hiro_surge`).** With `S15` = the sum of `impact` over the last 15 minutes
(`window_min`), it fires when **all** of these hold:

| Condition | Default |
|---|---|
| abs(S15) ≥ `k` · σ | `k` = 3 |
| abs(S15) ≥ `min_notional` (dead-tape floor) | $25,000,000 |
| unlabelled share of the window's volume ≤ `max_unclassified` | 0.5 |
| newest stored row ≤ 300 s old against the clock (`hiro.STALE_ROW_SEC`) | — |

Side `dealers_buying` when S15 > 0 (upward pressure), else `dealers_selling`.
Cooldown `cooldown_min` (30) per symbol and direction. The clock check stops a
stalled collector — or the rows simply stopping at 15:00 — re-firing the same old
window every time its cooldown lapses. It is 300 s, not one poll, because a row's
`ts` is the minute floor of the collect START and the check runs after a 30–90 s
poll: a healthy newest row is routinely 90–150 s old, so a slow poll still counts
and only a stalled collector does not. A non-finite window or σ never fires.

**Reversal (`hiro_flip`).** The day's running total `cum` (the sum of every stored
minute since the session's first row, skipping a non-finite one) drives a two-state
hysteresis:

```
buying  → selling   when cum ≤ −flip_band · σ
selling → buying    when cum ≥ +flip_band · σ
no state yet        → the first side cum clears by flip_band · σ (the baseline; no alert)
```

Evaluation waits for `flip_not_before` (09:00 CT); the total still counts from the
first row. The rule is **stateless**: each tick replays today's rows and alerts only
on the latest transition if it is newer than the one recorded as seen
(`hiro_flip_seen:<SYM>` in the flow cooldown map) **and** at most 120 s older than
the **newest stored row** (`hiro.FLIP_MAX_AGE_SEC`), so a restart cannot fire an old
reversal. The age is measured against the newest row, never the clock: a reversal
refused on a slow tick would never be marked seen and would be lost for good, while
the daily report (which replays with the clock at each row) counted it. The same
300-s staleness check as the surge (`hiro.STALE_ROW_SEC`) refuses it on frozen data.
Cooldown `flip_cooldown_min` (60) per symbol; a transition inside it is marked seen
and dropped.

**Delivery.** Both alerts join `cache:options:flow_alerts` and carry two flags the
service stamps: `quiet` (`[hiro].push` is not `true`: no phone push, and the Desk does
not speak it) and `public` (`[hiro].public` is `true`; otherwise the row is hidden on
the public live screens and in gallery captures). Both fail closed. With `push` on, a
reversal is always pushed and a surge only at `mult` ≥ `push_k` (4), through the
`flow_hiro` push category. Neither ever chimes or toasts in the browser. Neither
counts toward the Opportunity Board's flow count or Hotness
(`compute._count_flow_alerts` skips `hiro_*` cooldown keys) or the EOD mover counts
(`compute._notable_movers`).

**The summary view `cache:options:hiro`** (`skip_unchanged`, event
`events:options:hiro`): `{date, symbols: {SYM: {ts, spot, impact, cum,
window_impact, sigma, mult, unclassified_share}}}`, where `ts` is that symbol's
newest stored minute. It is written only once a symbol has a stored minute, and the
window, σ and multiple are published as `null` rather than a non-finite number. A
reader must check `date` **and** each symbol's `ts` against the clock: a stalled
collector also leaves a today-dated view. No page reads it yet.

**Known limitations.**

- **One label per contract per minute**, not per trade, from this minute's quote — a
  trade may be up to a minute older than the bid and ask it is judged against.
- **Expiries beyond +7 days are not counted.**
- **The opening minute only seeds.** The first poll inside regular hours seeds every
  contract from its cumulative volume, so the trades printed between 08:30:00 and
  that poll are never booked.
- **A restart gap is lost.** The memo is in memory; the first poll after a restart
  (or after any gap over 150 s) seeds again.
- **A model, unvalidated.** The daily report below is what decides whether `push` or
  `public` is ever turned on.

**The daily validation report — `tools/hiro_report.py`.** Run by the systemd timer at
16:10 CT (see *Service cadences*), it replays the live rules minute by minute — the
same `hiro.detect_surge` / `detect_flip`, the handler's σ rule as live had it at each
minute, and the cooldowns — and writes `options-scanner/data/hiro_report/<date>/report.md`.
Per symbol: minutes measured, the unlabelled share, σ and its source, how many surges
would fire at each `k` in 2 · 2.5 · 3 · 3.5 · 4 · 5, and the reversals. A **hit** is a
fire after which spot moved the way the modelled hedging pushed it over **5** and
**15** minutes (read from the first stored row at or after the horizon, no more than
120 s past it); a return of exactly zero is **flat** and left out. Each hit rate is
set beside that day's **base rate for the same direction** — the share of every
measured minute after which spot simply rose (or fell) over the same horizon — never a
coin flip. Hits print as "k of n", with a percentage from 10 decided fires. A **Last N
sessions** block pools every stored session (up to `keep_sessions`) with surges and
reversals pooled separately. It reads `gex_history.db` read-only and nothing else, so
any stored day can be re-run: `--date YYYY-MM-DD` (default: the newest **closed**
session), `--force` (a weekend or holiday), `--out DIR`, `--k 2,3,4`,
`--horizons 5,15`. It exits **1** when nothing was measured (every symbol failed, or a
trading day with no stored minute for any symbol — a dead collector).

## Market read

**Files:** `services/market_svc/market_read.py` (pure: the six rows, the slots, the
assembly), `scheduler.refresh_read` (reads the sources and publishes),
`shared/market_read_config.py` + `config/market_read.toml`, `webgui/pages/desk.py`
(`read_rows`, `read_header`, `paint_read`). Design:
`docs/plans/2026-10-05-market-read-scorecard-design.md`.

Six readings, each given a verdict **for stocks**: `tailwind`, `headwind`, `neutral`,
or `none`. The verdict is absolute, not relative to the day's move. No Schwab call and
no Claude call: every input is a view another service already publishes.

| Row | Source | `tailwind` | `headwind` |
|---|---|---|---|
| `direction` | `market:dashboard` tiles `SPX`, `NDX` (`change_pct`) | both ≥ +`move_pct` (0.25) | both ≤ −`move_pct` |
| `breadth` | `market:dashboard`, tiles of the four equity frames (`symbols.BREADTH_CATEGORIES`), by `color_state`; basket tiles skipped | advancing share ≥ `strong_share` (0.60) | ≤ `weak_share` (0.40) |
| `structure` | `options:matrix` rows for `structure.symbols` (SPY, QQQ): `spot`, `flip`, `call_wall`, `net_gex`, `gex_regime`; gated by `options:gex_status` `age_seconds` | every symbol above the flip with room ≥ `room_pct` (0.50) | every symbol within `near_pct` (0.25) of the ceiling while above the flip, or below the flip |
| `volatility` | `market:dashboard` tiles `VIX`, `VIX1D`, `VIX3M`, `SPX` | VIX `change_pct` ≤ −`vix_move_pct` (1.0) and VIX < VIX3M | VIX `change_pct` ≥ +`vix_move_pct` with SPX up, or VIX1D > VIX |
| `flow` | `options:flow_sides` alone (each entry names its `side` and `osi`) | call lean ≥ `lean_pts` (5.0) and put lean below it | put lean ≥ `lean_pts` and call lean below it |
| `cross_asset` | `market:dashboard` tiles `TLT`, `$DXY`, `HYG`, by `color_state` | at least two risk-on | at least two risk-off |

All bounds are inclusive. Everything between the two is `neutral`.

- **Breadth needs a sample.** Fewer than `breadth.min_tiles` (10) tiles with a price
  (rising, falling or flat) is `none`: a quote outage that leaves one tile up would
  otherwise read as 100% advancing. Every tile priced and none moving is `neutral`.
- **Volatility needs all five numbers:** the VIX level and change, the one-day, the
  three-month, and the SPX change. Each rule uses one of them, and a rule that cannot
  be evaluated is not a rule that came out false, so a missing one is `none`.
- **Cross-asset:** two tiles that agree decide the row whatever the third says.
  Otherwise a tile with no colour makes the row `none`, because it is the one that
  could have decided it.

- **Room** is `(call_wall − spot) ÷ spot × 100`. At or through the ceiling counts as at
  it. The symbols must agree: one at its ceiling and one with room is `neutral`.
- **Lean** is `(Σ bought − Σ sold) ÷ Σ (bought + sold + unlabelled) × 100`, pooled by
  volume over the poll tallies of today's flagged contracts, calls and puts apart. A
  contract flagged by two alerts is counted once. Unlabelled volume dilutes it. Every
  flagged contract is read, not only the ones still on the capped alert list.
- **Cross-asset counts the board's own colours.** Whether a falling Treasury fund is
  good or bad for stocks is decided once, in `classify.color_state`; this row does not
  hold a second opinion.

**`none` is "no reading", never `neutral`.** A row is `none` when a tile it needs has no
number, a matrix row has no flip or (above the flip) no ceiling, a symbol's net gamma is
exactly zero (the after-hours artefact the dealer panel also hides), the dealer levels
are not current, fewer than `flow.min_contracts` (10) contracts are flagged, or its
source view is absent. A row that raises is also `none`, and the other five still
publish (`market.read.<row>` on `/health`).

**Too old.** Every source is rewritten on a clock while its publisher is alive (an
unchanged write still refreshes the view's `:ts`), so an old one means the publisher
has stopped. A view is dropped when its `:ts` is older than `stale_after_sec` (300);
the dashboard, republished every 3 seconds, has its own shorter limit,
`dashboard_stale_after_sec` (60). The flow view is also dropped when the session
`date` it carries is not today's.

**Structure follows the collector, not the matrix.** The matrix is republished every
minute whether or not the GEX collector ran, so its own age proves nothing about the
levels in it. The row is `none` (`facts.stale: true`, no levels handed on) when the
collector's age is unknown or above `structure.stale_after_sec` (150). That age is
`options:gex_status` `age_seconds` plus the age of the status view itself, so a status
publisher that stops cannot leave the collector looking fresh. 150 is the limit the
Desk's dealer panel greys its walls at (`desk.STALE_AFTER_SEC`); a mirror test pins
the two.

**Slots.** A reading is taken on every clock multiple of `interval_min` (15 or 30)
from the first one strictly after the 08:30 CT open through the 15:00 close inclusive,
on trading days. The market service asks "is a slot due" on its existing poll; there is
no timer. A late tick still fires the slot it is in, once, and a slot at or before
the one already published is never due (an interval changed mid-session cannot put
the day's history out of order). The 15:00 reading is `final`. On its first call after a start the service reads the published view back, so
a restart neither repeats a slot nor loses the day's history. A reading that fails to
build is retried after `retry_sec` (30), not on every poll.

**The two switches apply between slots**, because a switch turned off must not wait up
to half an hour. `enabled = false` replaces a reading that is up with a retraction
(`enabled: false`, no rows), once. A changed `public` republishes the reading that is
up, unchanged but for the flag; that reaches yesterday's reading before the open too.

**`cache:market:read`** (`MarketRead`): `enabled`, `date`, `ts`, `slot`,
`interval_min`, `next_slot`, `final`, `public`, `tally`, `rows`, `history`. Each row is
`{key, verdict, facts, prev}`; `prev` is the previous slot's `{verdict, facts}` for the
same row, or `null`. The `flow` row also carries `estimate: true` and its own
`public`, copied from `options:flow_sides` (Flow Alerts' `[sides] public`). `history`
is the day's `{slot, verdicts}` list.

**The Desk dialog.** Since 2026-10-06 the reading is a dialog opened from a button
in the page header (`build_popups`, `paint_read`), with the head and the tally
repeated beside the button (`read_status`). The page maps each code to a word and a fixed chip class
(`READ_WORDS`, `READ_CHIPS`); an unknown code is "No reading". It formats the facts
itself and computes no verdict. *Since last* is "was …" when the code changed, else the
change in the row's main number (SPX percent, advancing count, VIX level, call lean),
else "unchanged". The head is `live`, `close`, `stale` or `waiting`: `stale` is a
reading from another day, or one older than two of its own intervals while the session
is open. The one-second clock re-checks that, so a reading that stops arriving greys
without a new one.

**Hidden is not "no reading yet".** A retraction hides the button and its status on
every origin, and closes the dialog if it is open. On the public origin a reading
whose `public` is not `true` hides them too, and a row
whose own `public` is not `true` (the Flow row) is drawn as "No reading · Not shown on
this screen" with no figures and no "was", and the tally in the head is recounted from
the rows as drawn (`read_hidden`, `read_view_shown`).

**The Market report dialog** is the same header's second button. It frames
`frame_url` from `cache:market:summary` (`https://<site>/reports/latest.html?v=<report
date>-<slot>`; the version is what reloads an open dialog when a new report lands) and
links `report_url`. Both are drawn only when `https://`; a payload with no
`frame_url` frames `report_url`. A dialog's content is not in the browser's document
while it is closed, so the report is fetched only when it is opened.

**Known limitations.**

- **Every threshold is a starting guess.** None has been measured against outcomes,
  and nobody has tested whether the tally predicts anything.
- **The flow row is weak.** On 2026-10-05 the pooled lean on calls was under two
  points; the row will read `neutral` on most days.
- **Structure reads two symbols.** $SPX and $NDX are not read: the matrix carried no
  flip for either index when this was built (2026-10-04 and 2026-10-05).
- **No early-close handling.** On a half day the slots run to the normal close.

## Bought / sold estimate on flow alerts

**Files:** `services/options_svc/flow_sides.py` (pure arithmetic),
`flow_sides_tick.py` (session state, the store, publishing), `flow_stream.py` (the
stream worker), `options-scanner/gex_history_db.py` (the `flow_contract_days` table).
Design: `docs/plans/2026-10-04-flow-alert-sides-design.md`.

An **estimate**, for the two alert types that name one contract (`uoa`, `big_delta`).
Schwab publishes no time-and-sales tape, so new volume is labelled by the same rule
the hedging-flow model uses, `hiro.classify_side(last, bid, ask)`: at or through the
ask is bought (+1), at or through the bid is sold (−1), otherwise the side of the
midpoint the trade price sits on; exactly at the midpoint, or on a missing, locked or
crossed quote, it is **unlabelled** (0).

**Two sources, never blended.**

| | Minute poll (`poll`) | Stream (`stream`) |
|---|---|---|
| Covers | From the regular open (08:30 CT) to the end of collection, every contract with volume in every fetched chain | One contract, from the moment its alert fires |
| Resolution | One label per contract per fetch | One label per level-one tick |
| Size of a step | `totalVolume` now − the highest `totalVolume` seen | `total_volume` now − the highest seen on the stream |
| First reading | See "unwatched volume" below | Only seeds: books nothing |

The poll is needed because an alert fires *after* the volume that caused it has
printed; a stream subscribed at the alert can only see what follows.

**The invariant: bought + sold + unlabelled = the contract's volume.** Volume is never
dropped. What the service did not watch print is booked as unlabelled:

- the volume a symbol already carries at its first fetch of the process after the
  regular open (the first poll at or after 08:30 CT, or the first after a restart).
  Nothing is booked before the open: until then a chain may still carry yesterday's
  volume for a contract that has not traded yet;
- the volume across a gap longer than `flow_sides_tick.MAX_GAP_SEC` = **450 s**,
  1.5 × the collector's slowest tier (`collection_tiers.MAX_TAIL_INTERVAL_MIN` = 5 min).
  The hedging-flow model uses 150 s because it measures one-minute symbols only; here
  a watchlist-only symbol's normal step must still be labelled;
- on the stream, the first volume-carrying tick of each contract after every
  (re)connect. A quote-only tick does not use that up.

A contract first seen with volume in a symbol that *was* fetched a step earlier stood
at zero then, so all of its volume is new and is labelled. The stored volume is a
high-water mark, so a glitch read of 0 books nothing and is not re-booked.

**State and cost.** The poll hook (`flow_sides_tick.on_chain`, one line in
`compute.collect_gex_snapshots`) works in memory only — it never opens the database —
and keeps one five-number entry per contract that has traded. Once a minute, after
the detectors, `after_alerts` registers newly flagged contracts, writes their rows,
and publishes. No Schwab call is added by any of it. The stream worker holds **one**
SSE connection to the proxy's `/stream/options` for the day's flagged contracts,
oldest alert first, up to `[sides].stream_max_contracts` (200; never more than
`STREAM_HARD_MAX` = 500, since the set travels as one request line). It reconnects when
the set changes, times out after 45 s of silence (three missed keepalives), and backs
off 3 → 60 s on failure. It starts with the scheduler loop, so it does not run under
the dev profile, and there is one worker per process: a loop restarted by its
supervisor reuses the live one. A second alert on a contract that is already streamed
counts "since the alert" from its own alert, not from the first.

**After a restart** the day's flagged contracts and their tallies are read back from
`flow_contract_days`. What had been labelled stays labelled; everything else the
contract has traded becomes unlabelled. Contracts not yet flagged lose their labels
for the day so far. The stream figure is the one exception to "never dropped": what
it had labelled is kept, but volume that printed while the service was down is not
added to "since the alert".

**Storage — `flow_contract_days` in `gex_history.db`.** One row per alert per session,
keyed `(session_date, alert_id)`: the contract, the alert type and time, `oi_prev`
(open interest that day, read in regular hours only — index open interest reads zero
outside them), `volume`, the three `poll_*` and three `stream_*` figures, and once
resolved `oi_next`, `oi_next_date`, `verdict`, `oi_ratio`. Rewritten whenever a
figure moves. Retention is its own: `[followup].keep_sessions` (20).

**The tally at the alert.** Each row also keeps `at_bought`, `at_sold` and
`at_unlabelled`: the poll's tally the minute the alert was registered. They are
written once with the row and never updated, and are `NULL` (never zeros) when the
contract had not been booked yet or the row predates the columns (added 2026-10-05;
an existing table gains them by `ALTER TABLE`). Poll-since-the-alert is the running
tally minus them, which covers the same window as the stream. No screen shows them.

**Comparing the two sources — `tools/flow_sides_report.py`.** Read-only, run by hand:

```
.venv/bin/python tools/flow_sides_report.py                 # the newest stored session
.venv/bin/python tools/flow_sides_report.py --date 2026-10-06 --min 2000
```

A flagged contract is compared only when it has an at-alert tally, a stream tally,
and at least `--min` contracts (default 500) since the alert on **both** sources;
every other row is counted under the reason it was left out. *Lean* is bought minus
sold as a share of the whole tally, unlabelled included. The report prints how many
contracts lean the same way, the median gap between the two leans, their
correlation, and the stream's volume as a share of the poll's. A figure with nothing
behind it prints as absent, never as zero.

**Next-day open interest.** On a later session date the collector's own chain carries
the new open interest; yesterday's unresolved contracts are passed to the tally as a
watch list so one that does not trade today still gets read. No Schwab call and no
scheduler slot are added.

`oi_ratio = (oi_next − oi_prev) ÷ volume`

| Condition | `verdict` | On screen |
|---|---|---|
| contract's expiry ≤ its alert date | `expired` | Expired — no reading |
| `oi_ratio` ≥ `opened_ratio` (+0.5) | `opened` | Mostly opened |
| `oi_ratio` ≤ `closed_ratio` (−0.5) | `closed` | Mostly closed |
| between | `mixed` | Mixed, or traded within the day |
| any figure missing, non-finite or negative, or `volume` ≤ 0 | `none` | No reading |
| the contract is not in today's chain | `none` | No reading |
| the row is older than the previous trading day and was never read | `none` | No reading |
| not read yet | *null* | Waiting for today's open interest |

Both bounds are inclusive. The figure is **re-read on every fetch that day** and the
verdict re-derived when it moves, with an INFO line
(`flow follow-up: open interest moved …`), because when Schwab's chain starts showing
the new open interest has not been measured. A restart that day reloads the rows
already read, so the re-reading continues, and a resolution is kept in memory until
its write succeeds. Open interest does not change within a session, so a zero never
replaces a positive figure already read that day (index open interest reads zero
around the edges of a session).

**Delivery.** `cache:options:flow_sides` (`skip_unchanged`, so it carries no timestamp)
and `cache:options:flow_followup`, each with a top-level `public` flag stamped from
`[sides].public`, which fails closed: only a literal `true` opens it. The Flow Alerts
page and the Desk's flow panel read them through `flow.sides_view_shown`. A switch
takes effect within a minute, in both directions: changing `public` republishes the
follow-up view, and turning `[sides].enabled` or `[followup].enabled` off publishes an
empty, non-public view once so no screen keeps the old figures. The tally runs last
in the flow-alert pass, after the pushes and the alert list. Nothing here changes a
phone push, a chime, the Desk's speech, or any ranking.

**Known limitations.**

- **One label per contract per fetch** on the poll: a minute's volume (up to five for a
  watchlist-only symbol) takes the label of its latest trade.
- **Level-one conflates rapid ticks**, so the stream is a finer sample, not a tape.
- **Spread legs and trades inside the quote** are mislabelled or unlabelled.
- **Bought is not opening.** Only the next-day reading separates the two, and a
  same-day expiry never gets one: on 2026-10-02, 152 of 175 contract alerts (87%).
- **A partial first chain.** If a symbol's first usable chain is missing an expiry,
  the next full chain books those contracts' whole volume with one minute's label.
- **Unmeasured:** whether a pre-open chain carries yesterday's volume (the reason
  nothing is booked before 08:30), the typical unlabelled share, how large a lean must be to mean
  anything, the memory the per-contract entries take, and the hour at which the new
  open interest appears.

---

# Rescue Tested Trades

**Files:** `services/options_svc/rescue.py` (pure engine), `commission.py`,
`compute.compute_rescue` / `compute.assess_open_positions`, and the apply primitives
in `options-scanner/paper_adjust.py`.

The Rescue feature detects credit spreads (PCS/CCS/IC) that have moved against the
position and proposes a ranked, commission-aware menu of adjustments. The
architecture is **hybrid ("Approach C")** — cheap detection rides an existing loop,
while the expensive ranked menu and the apply are on-demand:

| Phase | Where it runs | Output |
|-------|---------------|--------|
| **Detection** (state + heat) | The manual paper manage cycle (**hourly**, 09:00-14:00 CT - see *Service cadences*) | Tags `cache:options:paper_account` rows with `rescue_state` / `heat`; publishes `cache:options:rescue_summary` (counts) for the nav badge. |
| **Ranked menu** | On demand (`rescue` command) | `cache:options:rescue:<position_id>` — the per-position advisory. |
| **Apply** | On demand (`rescue_apply` command) | Mutates the paper account behind a stale-price guard; writes an audit row. |

## Detection model

**File:** `rescue.py` · `assess_position_risk(...)`.

Each open spread is graded on a four-state ladder and assigned a **0–100 heat**:

```
ok  →  watch  →  tested  →  critical
```

The thresholds mirror the manage-cycle stops. Heat is driven by:

- **Short-strike proximity** — how close the underlying is to the short strike
  (the dominant input; ITM short = high heat).
- **Short delta** — the short leg's delta as an assignment-probability proxy.
- **P&L vs credit** — current loss as a fraction of the credit originally taken in.
- **DTE** — less time to recover raises heat.
- **GEX / regime modifiers** — sitting below the dealer **gamma flip** or pinned at a
  **put wall** adjusts heat; the market regime (from the sentiment bridge) nudges it.

`assess_open_positions()` is the **cheap** pass used for the badge: it reuses each
position's stored marks (no fresh chain fetch) to compute state/heat for every open
paper position, and `compute_rescue` does the **expensive** per-position pass (live
reprice + full candidate construction).

## Strategic context

**File:** `rescue.py` · `strategic_context(...)`. Independent of any single
candidate, this annotates the advisory with three reads (notes + boolean flags):

- **Dealer gamma** — rolling *below* the gamma flip is flagged risky (dealers sell
  into weakness, accelerating moves); resting *on a put wall* favors a bounce.
- **Regime fit** — whether the spread's direction aligns with the current trend
  regime.
- **Settlement mechanics** — **index** options are **European, cash-settled** (no
  early assignment); **equity / futures** options are **American** and carry
  assignment risk when in-the-money. (`commission.is_index_symbol` classifies.)

## Candidate builders and economics

`rescue_candidates(...)` orchestrates eleven candidate builders, constructing each
**independently** so one bad candidate can't sink the whole advisory:

| Builder | Apply kind | Idea |
|---------|-----------|------|
| `close` | execute | Buy back the whole spread now. |
| `partial_close` | execute | Close part of the size. |
| `narrow` | execute | Roll the long leg in toward the short (cuts width and max loss). |
| `convert_ic` | execute | Add the opposite-side spread → Iron Condor. |
| `convert_butterfly` | execute | Tighten to an Iron Butterfly. |
| `roll_down` | execute | Roll the spread down (same expiry). |
| `roll_out` | execute | Roll to a later expiry for more time. |
| `roll_down_out` | execute | Roll both down and out. |
| `broken_wing` | advisory | Asymmetric-width repair (place manually). |
| `inverted` | advisory | Invert the strikes (place manually). |
| `futures_hedge` | advisory | Offsetting futures position (place manually). |

**Commission-aware economics.** Commissions come from `config/commissions.toml` via
`commission.py` (`commission_for` / `futures_commission` / `is_index_symbol`) — never
hard-coded. Schwab standard rates: listed equity/ETF options **$0.65/contract per
leg**, index options $0.65 + a Cboe exchange-fee passthrough, futures **$2.25/contract
per side**, letting a leg expire **$0**. Each candidate reports `gross_cash` (before
fees), `commission`, and `net_cash` (after).

The **max loss** of a credit spread (used for both the post-adjustment metric and BP
reconciliation) follows the standard idiom:

```
max_loss = width · 100 · qty − net_credit          # net_credit = credit taken in net of commission
```

(`_spread_max_loss` in `rescue.py`.) The candidate's `new_max_loss` is recomputed for
the adjusted legs.

## Scoring and ranking

**File:** `rescue.py` · `score_candidate(...)`. Candidates are ranked by, in priority
order:

1. **Max-loss reduction per net dollar** — the dominant term; how much risk each net
   dollar removes (or, for credit actions, adds while reducing risk).
2. **Delta** — preferring adjustments that flatten directional exposure.
3. **Debit penalty** — debit actions are penalized, encoding *"never roll for a debit
   just to save it."*
4. **GEX / regime modifiers** — the same gamma-flip / put-wall / regime reads that
   feed heat nudge the score.

## Apply and the stale-price guard

**File:** `options-scanner/paper_adjust.py`. The `rescue_apply` handler dispatches to
`apply_adjustment`, which routes to the per-action primitive (`apply_close`,
`apply_partial_close`, `apply_narrow`, `apply_convert_ic`, `apply_convert_butterfly`,
`apply_roll`, `apply_inverted`).

Before mutating anything, `apply_adjustment` **re-prices the candidate's legs live**
and:

- **Aborts without mutation** if the position is no longer `OPEN`, or if the
  re-priced economics have **drifted past tolerance** from what the candidate
  promised (the GUI surfaces *"prices moved — re-review"*).
- Otherwise mutates the paper DB inside the existing cash / buying-power mechanism,
  **reconciling reserved BP** to the position's new max loss.

**Rolls** close the old position and open a new one linked via the
`parent_position_id` column. Every applied adjustment writes an audit row to the
`position_adjustments` table (`paper_account_db.insert_adjustment` /
`list_adjustments`). `rescue_apply` refuses non-paper ids — **captured signals are
advisory-only** (no paper position to mutate).

---

# Debit exit rules (long options, debit spreads, butterflies, condors)

**Files:** `options-scanner/signal_recommender.py`
(`_is_debit` / `_debit_target_base` / `_recommend_debit`),
`services/options_svc/compute.py` (`manage_ledger_trades` / `_ledger_exit_ctx`),
`config/trade_mgmt.toml` `[structures.LONG_CALL|LONG_PUT|BULL_CALL|BEAR_PUT]`.
Design: `docs/plans/2026-09-12-debit-exit-rules-design.md`.

The Paper Ledger's DEBIT structures (`shared.structures.LEDGER_DEBIT`: the four
above plus the Strategy Finder's `BUTTERFLY_CALL`/`BUTTERFLY_PUT`/`CONDOR_CALL`/
`CONDOR_PUT`, added 2026-09-13) are the only positions in the app whose
exits are **not** credit-denominated. They run on the manual paper manage cycle
(**hourly**, 09:00-14:00 CT), and the rule pass runs **before** the expiry
settlement on that tick, so a position at its target on its expiration day books
the target rather than an intrinsic settlement.

**Expiry settlement — one rule for every paper book.**
`paper_engine.settlement_underlying` supplies the price for the Account, the
Ledger and the captured signals: on the expiration day, at or after 15:00 CT,
the regular-session last from a direct quote; on any later day, the expiration
date's daily close (never a live quote); with no usable price the settlement is
deferred. The Account and the Ledger settle on the 15:05 CT slot
(`[slots.paper_settle]`), which runs no entry, no reprice and no exit rule; the
captured signals settle on their own 5-minute cycle, which runs to 15:15 CT. A
captured signal is valued by `signal_repricer.expiry_value` (each short leg's
intrinsic less each long leg's), and its outcome row records the price it
settled against in `settlement_underlying`. Tracked structures use the same
price on their own 15-minute cycle, whose last slot is 15:10 CT
(`structure_marks.expiry_value`; see *Tracked structures*).

## Why a separate rule set

`recommend` computes `credit_total = entry_credit x 100`, and a debit row stores
`entry_credit` as the **negative** per-share debit. The credit rules therefore do
not merely fail to apply - they invert:

```
rule 1   pnl <= -stop_mult x credit_total   ->   pnl <= +400    CUT/MONEY_STOP
rule 5   pnl >= tp_frac  x credit_total     ->   pnl >= -100    TAKE_PROFIT
```

Measured on the real function with a $2.00 debit, a healthy long call returns
**CUT/MONEY_STOP at every P&L from -$199 to +$399**. `recommend` dispatches on
`direction == "DEBIT"` (or a negative `entry_credit`) so no path reaches the
credit rules with a negative credit.

## The three rules, in order

| # | Rule | Key | Ships |
|---|---|---|---|
| 1 | percent-of-debit stop: `pnl <= -debit_stop_frac x entry_debit` | `debit_stop_frac` | **OFF** |
| 2 | profit target: `pnl >= tp_frac x base` | `tp_frac` (0.50) | on |
| 3 | time exit: `dte <= exit_dte` **and** `dte_at_entry > exit_dte` | `exit_dte` (21) | on |

**Rule 1 is off by source**, not by oversight: the practitioner guidance closes
debit spreads before expiry rather than stopping them out, so a shipped level
would be invention. `loss_rules`, `stop_mult`, `cut_dte` and the delta keys are
credit-denominated and are **not read** on this path (*2x a debit* is a loss that
cannot happen).

**Rule 2's denominator (`_debit_target_base`) differs by structure**, because the
source gives a percentage and not of what:

| structure | base | why |
|---|---|---|
| `BULL_CALL` / `BEAR_PUT` | `max_profit` | mirrors the credit side, where the credit **is** the max profit |
| `BUTTERFLY_*` / `CONDOR_*` | `max_profit` | bounded, so the same rule as a vertical |
| `LONG_CALL` / `LONG_PUT` | `entry_debit` | no max profit exists (`unbounded = True`, `max_profit_total = None`) |

On a $2.00 debit over a $5 width those are **+$150** and **+$100**. An unusable
`max_profit` (absent, zero, negative, NaN, a string, a bool) falls back to the
debit rather than making the target unreachable - or, at zero, firing it at
break-even. `_ledger_exit_ctx` divides `max_profit_total` by quantity, since the
row stores it already multiplied while the repricer's P&L is per contract.

**Rule 3 is profit-blind** (`TAKE_PROFIT` ahead, `CUT` behind, code `TIME_EXIT`
either way - distinct from `TIME_STOP`, which means DTE <= `cut_dte` **and**
underwater), and the `dte_at_entry > exit_dte` condition is load-bearing: the
Market Scanner's Directional tab scans **DTE 0-4** and **DTE 5-15**, so every
debit it can produce arrives inside 21 days and an unguarded rule would close
100% of them on the following cycle. Those positions are bounded by their target
and by the expiry settlement instead. An unknown `dte_at_entry` declines the exit.

⚠ **Butterflies and condors have no `[structures.*]` table, so no `exit_dte` and no
rule 3** (operator decision, 2026-09-13). A time exit suits a trade that loses value
to time; a long fly gains most of its value in the final two weeks — a 95/100/105
call fly at spot 100 and IV 28% is $1.20 at 30 DTE, $1.42 at 21, and reaches its ~$3.10 target
only near 3 DTE — so `exit_dte = 21` would close every 22–30 DTE entry flat. Adding
a table with `exit_dte` reverses that decision. The ledger records, reprices and
settles a butterfly's `qty 2` body leg; straddles and strangles are refused by name
in `paper_trader.create_paper_trade` (analysis only, D1). That function also refuses a
`PAPER_DEBIT_TYPES` signal whose `net_debit` is not a positive finite number
(`ValueError "<TYPE> has no debit"`): `_create_debit_trade` books `net_debit or 0.0`, so
an absent, zero, negative or NaN debit would otherwise open a **free** trade and
overstate every later mark by the premium it never recorded.

## Scope and absences

Credit rows in the ledger are **not** managed by this pass - giving them the
credit rules would change how a second book exits, with its own measurement
attached (the same reason the credit spreads have no `manage_dte`). Three
absences are skips rather than actions: no mark (a zero would satisfy a
zero-threshold rule and record a fabricated close price), a P&L with no value to
write, and an expired row (`expire_ledger_trades` owns expiry).

⚠ **`close_paper_trade` booked a debit's realized P&L with the credit formula
until 2026-09-12** - `(entry_credit - exit_debit) x qty x 100`, so a long call
bought at $2.00 and sold at $3.00 recorded **-$500** against a true **+$100**.
`exit_debit` is the debit PAID on a credit row and the credit RECEIVED on a debit
row, matching `_expire_debit_trade`. The invariant that holds it: a manual close
at $8.00 and an expiry at an $8.00 intrinsic are identical economics and must
book the same number.

⚠ **No debit outcome data exists** - `signals.db` holds only PCS/CCS/IC and the
ledger is empty - so 0.50 and 21 are **sourced, not fitted**. That is why they
are config.

# Market News

`services/news_svc` — one of the two services with no Schwab or Claude call and no
call to the proxy (the other is `blog_svc`, see *Site Blog*). It reads one optional
credential, `FRED_API_KEY`.
Designs: `docs/plans/2026-09-25-news-feed-design.md` (v1) and
`docs/plans/2026-09-26-news-v2-design.md` (impact, the SEC panel, the calendar).

## Sources

Every source is a free public feed listed in `config/news.toml [[feeds]]`, one
adapter per `kind` (`services/news_svc/adapters/`), each a pure parse over the
fetched bytes:

| kind | Fetches | Notes |
|---|---|---|
| `rss` | one URL (MarketWatch, CNBC, ZeroHedge, Benzinga, Federal Reserve, PR Newswire, GlobeNewswire, Business Wire, the Truth Social archive, White House Actions, White House, USTR, SEC Press, EIA, investingLive, StockStory, Techmeme, Endpoints, Fierce Biotech, The Fly, OilPrice, CoinDesk) | Conditional GET: `ETag` / `Last-Modified` are sent back, so an unchanged feed costs a 304. GlobeNewswire ships **disabled** — its host drops non-browser clients |
| `yahoo_ticker` | the URL template once per ticker in the ticker set, 4 at a time | Each item carries the ticker it was fetched for |
| `google_news` | one Google News search (WSJ, Seeking Alpha, AP) | Links stay Google redirects; the publisher comes from the item's `<source>` |
| `edgar_form4` | EDGAR's current-filings Atom for Form 4, then each new filing's ownership XML | Open-market **purchases** (code P) only; a buy on an untracked ticker shows only at or above `min_value_usd` ($1,000,000) |
| `edgar_filings` | the same Atom per form (`S-1`, `S-3`, `424B5`, `S-3ASR`) | Headline "`<company>` files `<form>`" |

The SEC gets `sec_user_agent` (it requires a contact) at most one request every
0.15 s; every other feed gets `feed_user_agent`, which must keep a `Mozilla/5.0`
token (Yahoo answers a 404 without one). A response over `max_body_bytes` (5 MB) is
refused, and each fetch has a total deadline of **3 × `request_timeout_s`**
enforced by a watchdog that closes the socket, so a server trickling bytes cannot
hold the poll.

## Tickers, duplicates, retention

- **Tickers are explicit only**: a cashtag (`$NVDA`), an exchange prefix
  (`(NASDAQ: NVDA)`), or a bare bracket of 3–6 letters (`(NVDA)`), matched against
  the ticker set — the GEX collection list plus `[tickers] extras`. A company name
  never tags. EDGAR items resolve through the SEC's CIK map; Yahoo items carry their
  fetch ticker.
- **An item's id** is a hash of its canonical URL (tracking parameters and the
  fragment removed). The same id from another feed adds that feed's badge and
  tickers to the stored row.
- **The same headline under a different URL** is one story when it comes from a
  **different** feed within a day, or from the **same** feed within
  `[dedupe] same_feed_merge_h` (6 h; 0 turns that off). SEC items never take the
  same-feed rule — their titles are templated, so two filings would fold into one.
- The store keeps **7 days** (`keep_days`); each publish carries the newest **300**
  (`view_items`).

## Views and the public copy

Each poll publishes five views. **`cache:news:feed`** holds the headlines — every
kind but the two SEC ones — and **`cache:news:sec`** only `edgar_form4` /
`edgar_filings` (the newest `sec_view_items`, 100); the split is made by the store's
kind filters, at the producer. **`cache:news:feed_public`** / **`cache:news:sec_public`**
hold only rows whose **primary** feed is public under the current `[feed_flags]`,
re-read at every publish, with each row's tickers and source badges cut to what public
feeds contributed. `cache:news:status` is one row per configured feed. The four item
views carry no timestamp and publish `skip_unchanged`, so a poll that found nothing
new repaints nothing; the page's "Updated" stamp is the key's `:ts` side key,
refreshed on every publish.

## Impact

`services/news_svc/impact.py` is pure over `(row, [impact] config, ticker set)` and
returns points plus the reason codes that produced them:

| Rule | Reason code | Shipped points |
|---|---|---|
| Keyword tiers `[impact.keywords.<tier>]`, matched case-insensitively as whole words / phrases in the **headline** (`match_teaser = true` adds the teaser, searched separately). A tier counts **once**, naming the first word in list order that matched | `kw:<tier>:<word>` | tier1 +5 · tier2 +3 · tier3 +1 |
| `[impact.source_points]` — the **max** over the row's feeds, not the sum | `source:<feed>` | Federal Reserve +3 · Truth Social +2 · WSJ +1 · ZeroHedge −1 |
| Two or more distinct feeds | `sources:<n>` | `multi_source` +1 |
| Tagged with a ticker in the ticker set | `watchlist` (names no ticker) | `watchlist` +2 |
| Form 4 by `detail.total_value`: the largest of `huge_usd` / `large_usd` / `small_usd` it reaches | `form4:$<total>` | $10M +6 · $1M +3 · $250K +1 |
| … plus an officer / director (any relationship but *10% owner* / *insider*), only on a buy that scored | `officer` | +1 |
| Offering filing, by **exact** form (S-3 never takes S-3ASR's value) | `filing:<form>` | 424B5 +3 · S-3 +2 · S-1 +1 · S-3ASR +1 |
| A filing tagged with no ticker (likely a micro-cap) | `untracked` | −1 |

`band`: score ≥ `high_at` (6) → `high`, ≥ `med_at` (3) → `med`, else `low`; a pair
that is not `high_at > med_at` falls back to 6 / 3. A junk row or config scores 0,
never raises.

**Stored uncapped, capped at publish.** A row's impact is stored with
`impact.fingerprint(config, ticker set)`; every poll re-scores the rows whose stored
fingerprint is stale (a Settings edit or a ticker-set change), and a scoring failure is
one `news.impact` degrade while the views still publish with the stored impact. At
publish, `cap_stale` shows a **High older than `stale_after_h` (24 h) as Med** and
appends `stale` to its reasons; an undated item is never capped, and
`stale_after_h` is left out of the fingerprint so editing it re-scores nothing.

⚠ **The public views are re-scored, not copied.** Each public row is scored afresh from
the row as the public view carries it — `sources` and `tickers` already cut to public
feeds — against **`public_universe`**: the ticker set cut to the GEX collection list
(`config/symbols.toml`, public). So a private feed never appears in a `source:` reason,
and a `watchlist` point can never reveal a `[tickers] extras` name. Keywords are
echoed into public reasons, so a ticker or anything private must never be made a
keyword.

## Economic calendar

`services/news_svc/econ_calendar.py` fetches and stores; `econ.py` builds the payload
(pure). Each source is a `[calendar.sources.<name>]` table with its own `url`,
`user_agent` (`""` = the collector's `feed_user_agent`) and `refresh_min` (absent =
`[calendar] refresh_min`, 60):

| Source | What | Time basis | Cadence (shipped) | Requests |
|---|---|---|---|---|
| `fed` | `federalreserve.gov/json/calendar.json`: FOMC, Beige Book, speeches, testimony (`[calendar.fed] types`) | the Board's times are **Eastern**; a non-clock time ("noon", "TBA") keeps a date-only event | 60 min | 1 |
| `bls` | the BLS release schedule (ICS) — CPI, PPI, Employment Situation, JOLTS, ECI | `TZID=US-Eastern` wall clock | 720 min | 1 |
| `bea` | the BEA release schedule (ICS) — PCE, GDP | UTC (`…Z`) | 720 min | 1 |
| `fred_calendar` | FRED's release-calendar HTML, one page per `release_id` a `schedule = "fred"` indicator names (retail sales 9, jobless claims 180), 45 days back to 120 ahead | the page renders **Central**; a date without a time takes the indicator's `time_ct` | 720 min | 2 |
| `nasdaq_ipo` | `api.nasdaq.com` IPO calendar, this month and next; priced + upcoming tables only | dates | 240 min | 2 |
| `fred_api` / `fredgraph` | each enabled indicator's FRED series, ~400 days back | observation dates | `values_refresh_min` (240), and the release watch | 10 |
| `dividends` | the store `trade_svc` fills (below), opened **read-only** | dates | 60 min | 0 (local) |

Every instant is stored as aware UTC and displayed in **Central**. The indicators
(`[calendar.indicators.<key>]`: CPI, core CPI, PPI, payrolls, unemployment, PCE, core
PCE, GDP, retail sales, claims) name a FRED `series`, a `transform` (`pct_mom` % m/m ·
`change_k` change in thousands · `level_pct` · `level_k` level / 1000 · `pct_saar`), a
`schedule` (`bls` / `bea` matched by a case-insensitive release-name PREFIX on a
boundary — `"GDP ("` never takes *GDP by Industry* — or `fred` by `release_id`), a
`tile`, and `high` (true for every shipped indicator but claims; a non-bool is false,
with one WARNING). A missing or non-positive base is `None`, never 0.

**High impact.** `econ.py` stamps `high` on every row it publishes: an event is high
when its title CONTAINS a `[calendar.events] high_impact` phrase, case-insensitively
(`news_config.high_impact_events()`; shipped `FOMC statement`, `Press conference`,
`- Chair` — the Fed adapter titles an FOMC meeting *FOMC statement* and its press
conference *Press conference*, and the Board titles the Chair's own appearances
*Speech - Chair …* / *Testimony - Chairman …*, so `- Chair` never takes a Vice
Chair's; not a list → the default; junk entries dropped with one WARNING; `[]` is
real and highlights nothing), a data entry when its indicator's `high` is `true`; a
dividend or IPO is never high. `econ_calendar` passes the phrases as
`parts["high_events"]` to both builds, so the two views flag alike.

**The FRED key, and its fallback.** With `FRED_API_KEY` set in the process environment
(read at call time, from the stack `.env` only — never config, never `.env.live`),
observations come from the FRED API; without it, from the key-free
`fredgraph.csv` download. The key is a query parameter, so every exception raised while
a key-bearing URL was in play is rebuilt redacted with no cause chain, logged without a
traceback, and every stored error goes through `fred.redact`. When the release-calendar
HTML has never parsed, the key path's release **dates** stand in (their time from
`time_ct`).

**User-Agents are per source, and they disagree.** BLS answers **403** to a browser
User-Agent and FRED resets a bare Chrome one from a datacenter IP, so both keep the
repo's contact-bearing `feed_user_agent`; Nasdaq refuses that one and needs a
**Chrome** User-Agent plus `Accept: application/json` (both in its table).

**The release watch.** For `release_watch_min` (60) after a scheduled release, the
series whose new observation has not landed yet — first seen at or after the release,
and not a first-fill `bootstrap` row — is fetched every `release_poll_min` (2), at most
~30 requests per series per release. A steady day is ~104 requests in all.

**Failure.** Each source fails alone: one `news.cal.<source>` degrade, an `error` on
its row, and its last GOOD parsed result keeps being published (state `stale`). A
failed source is retried after at most `[calendar] refresh_min`. A missing dividends
store is `never`, not "no dividends".

**The views.** `cache:news:calendar` (owner), `cache:news:calendar_public` — BUILT with
`public_symbols` = the collection list, so only dividends differ and an extra's never
reach it — and the private `cache:news:calendar_status` (per-source `last_ok` /
`last_poll` / redacted `error`). All three carry no timestamp and publish
`skip_unchanged`, since the calendar branch republishes every tick. Whether a value is **released** or **awaiting** is a function of
`now`, so the page decides it (`news_view.indicator_state`) from the facts the payload
carries: `last_release_at`, and the latest observation's `first_seen` / `bootstrap`.

## Dividends (trade_svc)

`services/trade_svc/dividends.py` is the write half. Once a **trading day**, at or
after `[calendar.dividends] refresh_at` (**06:40 CT**), `trade_svc`'s scheduler pulls
each followed symbol (`news_config.ticker_set()` less `$` indices) through the proxy —
**one** `/quotes` passthrough call per symbol, since the passthrough splits `params` on
commas — reads the quote's `fundamental` block and writes
`services/trade_svc/data/dividends.db` through `shared/dividends.py`. Each symbol lands
as `ok`, `none` (a non-payer) or `error`; `ok` / `none` replace its forward rows, and
an amount that is absent or not finite is stored `None`, never 0. The day is recorded
in the store, so a restart does not refetch; a failure retries after `[calendar.dividends] retry_min` (15 min shipped). The
`dividends_refresh` command on `cmd:trade` runs the same pull on demand (forced past
the once-a-day guard), and drops a command older than 180 s as a replay. ⚠ Schwab's
dividend field names are unverified on prod; every spelling is in one table,
`dividends._FIELDS`.

**Trending** counts the tickers on items published within `[trending] window_h`
(6 h), skipping `yahoo_ticker` items, whose tag is the ticker they were fetched for.

## Failure policy

One failing feed never stops the poll: it counts one `_degrade`
(`news.feed.<name>`), records its error in `cache:news:status`, and keeps its last
items. An SEC 403, 429, 5xx, network failure or missed deadline is treated as an
outage — that accession and every later one are retried next poll — while any other
status is that filing's own answer and is not retried.

# Site Blog

`services/blog_svc` (port 8217) and `shared/blog_inbox.py`. The Blog on the public
site holds **entries**: each one a self-contained HTML document the owner uploads
on the private **Blog** page. The service cleans the document, keeps it as a
**draft** (a cleaned copy only the owner can see), and writes it into the public
site only when the owner presses Publish. It calls neither Schwab nor Claude nor
the proxy. Design: `docs/plans/2026-10-06-site-blog-design.md`; the reasoning
behind each rule: `docs/reference/blog.md`.

This chapter derives no market number. It records where the Blog's limits come
from, what cleaning removes, and when the service does its work.

## Where the limits come from

Every number is a key in `config/blog.toml`, read through
`shared/blog_inbox.py` and editable under Settings → Configuration → Site blog. A
missing key or a value outside its range reads as the shipped value. After an
edit, restart the blog service; the web app needs no restart.

**`[site]`**

| Key | Shipped | Range | What it bounds |
|---|---|---|---|
| `enabled` | `true` | — | Off: drafts can still be uploaded and previewed, and Publish and Unpublish still change the store, but nothing is written to or removed from the public site: an entry unpublished while it is off stays on the site. The answer says so each time. Switched back on, the site catches up at the next pass below. |
| `republish_min` | 30 | 1–1440 | Minutes between passes. Each pass re-publishes the drafts and entries lists and checks the site's blog files against the store, rewriting only what differs. Both also happen on every change; the pass heals a flushed Redis, retries a site write that failed, and catches the site up after `enabled` is switched back on. |

**`[limits]`**

| Key | Shipped | Range | What it bounds |
|---|---|---|---|
| `max_html_kb` | 512 | 1–4096 | The largest document accepted, in KB of UTF-8 bytes, measured before cleaning. |
| `max_drafts` | 20 | 1–200 | Drafts waiting at once. A new one is refused past this. |
| `title_chars` | 140 | 1–300 | The longest title kept. A longer one is cut. |
| `summary_chars` | 300 | 1–1000 | The longest summary kept. A longer one is cut. |
| `max_tags` | 6 | 1–24 | Tags kept on one entry; the rest are dropped. |
| `tag_chars` | 24 | 1–64 | The longest tag kept. |
| `slug_chars` | 80 | 16–120 | The longest address a NEW entry may have. A longer one is refused, not cut: half an address is a different address. An entry that already exists is held to 120, so lowering this cannot strand it. |
| `clean_sec` | 20 | 2–120 | Seconds one document may take to clean before its worker process is killed and the upload refused. |
| `clean_mem_mb` | 512 | 128–4096 | Memory the cleaning worker may use, in MB. Linux only. |

`clean_sec` and `[fonts] total_sec` (below) are how long one upload can hold the
service, which runs one command at a time. A command that is older than `[age]
replay_max_sec` in `config/services.toml` (900 s as shipped) when its turn comes
is not run, and the page is told the request waited too long. At their ceilings
the two come to 720 s, so one upload cannot expire the request behind it.

`max_html_kb` has a ceiling because a document travels inside one command, and the
`cmd:blog` queue keeps its newest 50 commands (`config/services.toml`
`[stream_keep]`). The two multiply: 512 KB × 50 is about 25 MB of Redis; both at
their ceilings (4096 KB × 500) would be about 2 GB.

One limit is derived rather than set. A cleaned document, with its typeface rules
added, is refused when it is larger than **6 × `max_html_kb` + 512 KB**: cleaning
can multiply a document's size about six times (every `"` inside an attribute is
written `&quot;`), and the typeface rules come to about 300 KB at their own
limits.

**`[fonts]`**

| Key | Shipped | Range | What it bounds |
|---|---|---|---|
| `enabled` | `true` | — | Off: no typeface is copied and the entry is shown in the fallback fonts its own stylesheet names. |
| `subsets` | `latin`, `latin-ext` | up to 16 names | The character sets copied. Each is a separate file per weight. |
| `max_links` | 4 | 1–16 | Typeface stylesheets followed for one entry. |
| `max_css_kb` | 256 | 16–2048 | The most one stylesheet may send back. |
| `max_files` | 24 | 1–64 | Typeface files copied for one entry. |
| `max_file_kb` | 400 | 16–1024 | The largest single file stored. |
| `max_total_mb` | 12 | 1–64 | All of one entry's files together. They are held in memory at once until stored. |
| `max_rules` | 96 | 1–1000 | `@font-face` rules written into one entry. Many rules can name one file, so `max_files` does not bound this. |
| `timeout_sec` | 10 | 1–60 | One request to Google Fonts. |
| `total_sec` | 30 | 1–600 | All of one entry's requests together. |
| `user_agent` | a desktop Chrome's | 20–300 printable characters | Who the service says it is. Google sends the WOFF2 format, split by character set, only to a browser it recognises. |

One rule is at most 3,075 characters, so 96 rules add under 300 KB to an entry.

## What cleaning does

The submitted document is parsed (`lxml`) and only read. The stored document is
built from the cleaner's own lists of tag and attribute names, with every value
escaped and every stylesheet tokenised (`tinycss2`), filtered and written afresh.
The result is cleaned again until cleaning changes nothing, at most five times.

| Removed | Notes |
|---|---|
| `script`, `noscript`, `iframe`, `frame`, `object`, `embed`, `applet`, `template`, `canvas`, `audio`, `video`, `dialog` | With their content. |
| `form` and every form control | With their content. |
| `img`, `picture`, `map` | This version of the Blog carries no images. |
| `base`, `meta`, `link` | The service writes its own charset and viewport. A link to a Google Fonts stylesheet is followed by the typeface copy, then removed like the rest. |
| Event-handler attributes (`onclick` and the like) | Counted. |
| Inside a drawing: `script`, `foreignObject`, `image`, `use`, animation elements, links, `mask`, `pattern`, `symbol`, `filter` | |
| A link address that is not `http://`, `https://`, `mailto:` or an in-page `#fragment` | The link's text stays; the address goes. |
| In a stylesheet: `@import`, `@namespace`, `@font-face`, `@charset`; any `url()` that does not point at an id in the same document; `image()`, `image-set()`, `cross-fade()`, `element()`, `expression()` | A fetching function becomes `none`. |

Kept: headings, paragraphs, lists, tables, the inline text elements, inline `svg`
drawings, the entry's own stylesheets and `style` attributes, and at most 64
attributes on one element. A link that leaves the page is given
`target="_blank" rel="noopener noreferrer"`.

An element on neither list is unwrapped: its text stays and nothing is counted.
Everything else that is removed is counted, and the draft shows the counts as one
sentence. When no title is typed, the draft takes the document's `<title>`, then
its first `<h1>`; the summary falls back to its first paragraph.

A document is **refused**, not partly kept, when it is empty, cannot be read to
its end (cut off inside a tag, or nested past the parser's depth limit), has a tag
with more than 1,024 attributes, or does not settle.

## The worker's time limit

Each document is cleaned in a separate process, killed after `clean_sec` seconds
(20 as shipped). An upload that overruns is refused with "Cleaning the file took
too long, so it was refused." and counted on the service's health check.

The reason is the parser, whose cost is not proportional to the size of its
input: one tag with tens of thousands of attributes took **216 s** to parse at the
512 KB limit (measured on libxml2 2.11.9). A check of the text before parsing
cannot predict that cost reliably, so the limit is a clock on the work itself. On
Linux the worker also lowers its own memory to `clean_mem_mb` and its processor
time to `clean_sec + 5` seconds; on Windows the clock is the whole limit.

## The typeface copy

An entry usually asks Google Fonts for its typefaces. The public site loads
nothing from another site, so at upload the service:

1. follows each `<link rel="stylesheet">` to `https://fonts.googleapis.com/css2?…`,
   up to `max_links`;
2. reads the stylesheet and keeps the blocks labelled with one of `subsets`;
3. fetches each `.woff2` file those blocks name from `https://fonts.gstatic.com`,
   checks that it is a whole WOFF2 file, and stores it under a name made from the
   SHA-256 of its content (20 hex characters, then `.woff2`);
4. writes its own `@font-face` rules into the entry, pointing at
   `../fonts/<name>`.

The rules are rebuilt from values the service understood, never pasted through
from Google's stylesheet. A link or a file that fails costs only itself: the entry
is shown in its fallback fonts and the draft says how many were left out and why.
Typefaces are shared by every entry that uses them, and one no entry or draft
names any more is deleted after a publish, a discard or an unpublish.

## Cadence

| When | What happens |
|---|---|
| At service start | The store is repaired (files made to agree with the rows after an interrupted write), every entry page on the site is rebuilt, and the two lists are published. The rebuild is what carries a changed site menu into the entry pages. |
| Every `republish_min` (30 min) | The site's blog files are checked against the store and only what differs is rewritten, then the two lists are published again. This is what retries a site write that failed and what puts entries on the site (and takes unpublished ones off) after `[site] enabled` is switched back on. |
| Every 30 s | The loop wakes, beats the heartbeat the health check reads, and re-reads the interval. |
| On a command | Upload, Publish, Discard and Unpublish run when the Blog page sends them, one at a time. |

Times shown for an entry are Central: the Blog's list and each entry page date an
entry by the day it was published in `America/Chicago`.

# Known issues

Documented defects a maintainer should know about before trusting a number. These
are recorded rather than silently carried.

## The PRICE sub-score: missing inputs, and the daily horizons

Both price-scoring call sites go through `sentiment_svc/compute._finite_score_price`.
A non-finite indicator is replaced by the value that zeroes its term and its
weight is withheld from the confidence, so an outage moves the sub-score toward
50 **and** lowers its confidence; all inputs missing gives 50.0 at confidence 0,
which drops the price input out of the blend. (Before that guard an all-NaN read
scored 82.50 to 92.50 at full confidence.)

The **Week and Month** gauges have one timeframe (daily bars) and no VWAP. They
call the scorer with no VWAP term, so the direction is spread over alignment,
MACD and RSI (weights 0.50 / 0.15 / 0.15, divided by 0.80), and with one
timeframe of one, so a complete daily read carries full confidence. Until
2026-10-04 they passed a neutral VWAP of 0.0 and one timeframe of an assumed
three: the price sub-score could not leave 10–90 and its weight in the blend was
0.15 against the sector term's 0.20, where the weights are 0.45 and 0.20.

## Carried chains, and what a study may read

With the collector's tail interval above 1 (it ships 1), a symbol collected only
because it is on the watchlist gets a real chain fetch one minute in N. On the
minutes between, its last fetched chain is **carried**: gamma and delta on the
nearest expiration are moved to the live price by the Black-Scholes change, and
volume, premium and volatility are the last fetch's. Two rules keep that honest:

- **A carried row says so.** `snapshots.carried_age_sec` in the GEX history is
  empty for a row computed from a chain fetched in its own minute and holds the
  chain's age in seconds for a carried one, on all five views. A study reads
  fetched rows only (`gex_history_db.fetched_only_clause`).
- **A capped carry is not written.** A carried gamma may grow at most
  `max_gamma_ratio` times Schwab's value; near the close on an expiration day
  the model's ratio is far larger. A cap that binds holds growth and not
  shrinkage, so the carried net exposure can change sign. That symbol is fetched
  for real in the same minute instead (at most `cap_refetch_max` symbols).
- **A symbol with nothing listed rests.** A name with monthly options only has
  no expiration inside the collector's seven-day window for most of the month.
  Schwab answers with an empty chain and nothing is charted for it. The
  collector then leaves that symbol out of its polls for `empty_retry_min`
  minutes (60) and asks again; once the chain lists an expiration it is
  collected every minute as usual. A failed fetch is never treated this way.
  The symbol is still scanned every quarter hour, where the window is 45 days.

## Scanner strike rules

The thresholds that decide which strikes the scanner may sell are settings
(`config/scanner.toml [selection]`, Settings → Configuration → Trade selection):
the highest short delta at entry (0.27), the delta treated as a data fault
(0.40), the move that stops the offside spread (0.6 of the daily expected move),
the credit required above break-even (credit ÷ width ≥ |short delta| + 0.02),
the smallest credit ($0.25 a share), the quote always accepted as tight ($0.02),
the widest spread (200), and the expected-move multiples for the same-day band
(0.618 to 3.0) and the directional band (0 to 0.618).

## Expected Move deliberately disagrees with ThinkorSwim

Not a defect — a definitional difference that has been measured and is documented
in full under *Expected move and IV analysis*. Two independent differences push in
opposite directions and **nearly cancelled on the one symbol they were measured on**,
which is luck rather than calibration. Do not "fix" either number to match a broker
without first deciding which definition is wanted; the same `atm_iv` also sizes the
drawn cone.

## Index open interest reads zero after hours

`$SPX` and `$NDX` report zero open interest overnight, which yields all-zero GEX
grids and arbitrary wall levels. Index gamma is only meaningful during the session.

---

# Constants Appendix

A consolidated table of the load-bearing constants. The cited file governs.

| Constant / set | Value | Where |
|----------------|-------|-------|
| Composite weights | vix 0.20, put_call 0.20, breadth 0.20, rotation 0.15, sector_perf 0.25 | `scoring/__init__.py:WEIGHTS` |
| Risk-free rate `r` | 0.045, static (single source; `q`=0, no dividend) | `options_calculator.py:RISK_FREE_RATE` |
| VIX sub-weights | term 0.50, vix1d 0.33, slope 0.17 | `scoring/__init__.py:VIX_SUB_WEIGHTS` |
| Trend weights | price 0.45, breadth 0.25, sector 0.20, vix 0.10 | `intraday_trend.py:TREND_WEIGHTS` |
| Put/Call thresholds | (1.3,1)(1.1,2)(0.9,5)(0.7,8)(0.0,10) | `put_call.py:PC_THRESHOLDS` |
| Breadth %>50DMA thresholds | (75,10)(65,8)(55,6)(45,5)(35,3)(0,1) | `breadth.py:BREADTH_THRESHOLDS` |
| Rotation lookback | 63 trading days; cash via $IRX | `rotation.py` |
| RRG windows | rs_window 50, mom_window 20 | `rotation.py` / `portfolio/sectors.py` |
| Trend-regime constants | bull dd −5, pullback dd −12, bear-rally dd −10, slope ±0.05, hysteresis 2 | `trend_regime.py` |
| Options score weights | rr15 pop10 theta10 iv12 iv_hv10 vega8 em12 liq5 trend10 gex4 dex4 (=100) | `options-scanner/scoring.py:DEFAULT_WEIGHTS` |
| Expected move | `price·(iv/100)·sqrt(max(dte,0.25)/365)` | `iv_analysis.py:calc_expected_move` |
| IV rank lookback | 252 trading days (HV-30 distribution) | `iv_analysis.py` |
| HV window | 30-day rolling, ann. ×sqrt(252) | `iv_analysis.py` |
| EMA / RSI / ADX / MACD | 14 / 14 / 14 / (12,26,9) | `shared/analysis_lib/technical.py` |
| Relative volume / vol profile | period 20 / 20 bins, 70% value area | `technical.py` |
| GEX per strike | `gamma·OI·100·spot²` (calls +, puts −) | `gamma_tool.py` |
| Trade primitives range | integer −100..+100 | `trade-analyzer/src/analysis/scoring.py` |
| Flow: crossover | band 2% of the larger side, cooldown 30 min, min premium $10k | `config/flow_alerts.toml` `[crossover]` |
| Flow: unusual activity (UOA) | volume ≥ 3.0 × OI, vol floor 500, premium floor **$5M**, top 3 per symbol | `[uoa]` |
| Flow: gamma flip | 0.15% hysteresis band, cooldown 60 min, watching `$SPX SPY QQQ IWM` | `[gamma_flip]` |
| Flow: big delta | fires at **25%** of the symbol's own gross delta-notional AND ≥ $10M; phone push at the higher **35%**; delta band 0.05–0.85 | `[big_delta]` |
| Flow: hedging surge (HIRO model) | 15-min hedge impact ≥ **3×** normal (RMS of full windows over the 5 prior sessions) AND ≥ $25M; ≤ 50% unlabelled volume; cooldown 30 min per direction; phone push at **4×** only with `push = true` (ships `false`); `public = false`; watching `$SPX SPY QQQ IWM` | `[hiro]` |
| Flow: hedging reversal (HIRO model) | running total clears zero by **1.0×** normal; none before 09:00 CT; cooldown 60 min; minute history kept 20 sessions | `[hiro]` |
| Flow: bought / sold estimate | poll gap booked unlabelled past **450 s**; stream at most **200** contracts a day (hard limit 500); stream read timeout 45 s | `[sides]`, `flow_sides_tick.py`, `flow_stream.py` |
| Market read | a reading every **15** min (or 30) from the first slot after the open to the close; sources older than **300 s** dropped; thresholds in the *Market read* section | `config/market_read.toml` |
| Flow: opened or closed (next day) | open-interest change ÷ volume ≥ **+0.5** opened, ≤ **−0.5** closed; flagged contracts kept **20** sessions | `[followup]` |

> **Five detectors, and the file is the source.** `services/options_svc/flow_alerts.py`
> carries defaults, but `config/flow_alerts.toml` overrides them and is what runs —
> it raises the UOA premium floor from $250k to $5M and the big-delta fire bar from
> 0.20 to 0.25. Read the TOML, not the module, when you want the live number.
>
> `big_delta` fires and pushes on **separate** bars on purpose: the Flow screen stays
> comprehensive at 25% while the phone only sees the high-conviction 35%. The
> hedging-flow surge follows the same pattern (fire at `k`, push at `push_k`); its
> `[hiro]` table is the fifth detector, with two rules. Every `[hiro]` value is a
> starting guess until the daily report has measured it — see
> **Hedging-flow model (HIRO)** in the *GEX / Gamma* chapter.
>
> Every `[hiro]` key: `enabled` · `push` · `public` · `symbols` · `window_min` (15) ·
> `k` (3.0) · `push_k` (4.0) · `min_notional` (25,000,000) · `max_unclassified` (0.5) ·
> `cooldown_min` (30) · `baseline_sessions` (5) · `min_minutes` (30) · `flip_enabled`
> · `flip_band` (1.0) · `flip_not_before` ("09:00") · `flip_cooldown_min` (60) ·
> `keep_sessions` (20). All are in **Settings → Configuration → Flow alerts →
> Hedging flow (HIRO model)**.

## Service cadences

All windows are US Central and gate on a trading day. The constants named below are
the source; this table is a summary of them.

| Service | Cadence |
|---------|---------|
| sentiment_svc | Composite refresh every **120 s** (`REFRESH_INTERVAL_SEC`), throttled to one refresh per **15 min** off-hours (`_OFFHOURS_INTERVAL_MIN`); directional trend recompute every **900 s** (`TREND_INTERVAL_SEC`); market-regime recompute every **5 min** (`REGIME_INTERVAL_MIN`); order-flow publish every **30 s** (`ORDER_FLOW_PUBLISH_SEC`); **momentum cascade once nightly at 16:20** (`momentum_due`); rotation at startup / on demand. |
| options_svc | Loop tick **30 s** (`POLL_INTERVAL_SEC`). Auto-scan 15-min slots, 08:00–15:15 (`autoscan_due`); **GEX collection every 1 min**, 08:00–15:20 (`_GEX_INTERVAL_MIN`, mirroring `gex_collector.POLL_INTERVAL_MIN`); term structure every **5 min** (`TERM_POLL_INTERVAL_MIN`); **captured-signal** management every **5 min** (`_CAPTURED_MANAGE_INTERVAL_MIN`); **manual** paper entry+manage **hourly at the top of the hour, 09:00–14:00** (`_PAPER_HOURS`, `_PAPER_GRACE_MIN` = 20); paper **expiry settlement at 15:05** — a settle-only pass over the Account and the Ledger (`paper_settle_due`, `[slots.paper_settle]`, grace 120 min); header + GEX status each tick in market hours, throttled to one per **5 min** off-hours (`periodic_refresh_due`, skip-unchanged). |
| portfolio_svc | Live SSE ticks; throttled publish ≤ every **2 s** (`PUBLISH_INTERVAL_SEC`); full rebuild every **600 s** (`REBUILD_INTERVAL_SEC`), or **3600 s** off-hours (`OFFHOURS_REBUILD_INTERVAL_SEC`), or on demand. |
| trade_svc | Analysis on demand. One scheduled job: the watchlist **dividend pull**, once a trading day at or after **06:40 CT** (`[calendar.dividends] refresh_at` in `config/news.toml`); the loop wakes every **60 s** and retries a failed pull after **15 min**. |
| market_svc | Quote poll **3 s** RTH (`RTH_INTERVAL_SEC`), **15 s** off-hours (`OFFHOURS_INTERVAL_SEC`), **60 s** at weekends (`WEEKEND_INTERVAL_SEC`); report summary re-read when the published market report changes (a stat of `deploy/site/reports/latest.html` + `latest.txt` per poll) — no Claude call. |
| news_svc | Three branches, launched every **30 s** tick (`TICK_S`) as keyed background tasks, so a slow one delays only itself and one still running is skipped, never doubled. **feeds**: every feed polled every **2 min** 08:30–15:00 CT (`[collector] rth_poll_min`), **5 min** in the extended sessions 06:30–08:25 and 15:00–15:15 CT (`eth_poll_min`), **30 min** otherwise on a trading day (`offhours_poll_min`) and **30 min** at weekends and holidays (`weekend_poll_min`), counted from the END of the last poll; nothing polls faster than **60 s** (`MIN_INTERVAL_S`). **calendar**: every tick, fetching only the sources whose own `refresh_min` is due (Fed 60 min, BLS / BEA / FRED calendar 720, Nasdaq 240, values 240). **watch**: every tick, fetching only a series whose release just passed — every **2 min** for up to **60 min**. All in `config/news.toml`, editable in Settings. One cycle of each at a time: a Refresh during one is skipped. |
| blog_svc | The loop wakes every **30 s** (`TICK_S`) to beat its heartbeat and re-read the interval. **At start**: repair the store, rebuild every entry page on the site, publish the two list views. **Then every 30 min** (`[site] republish_min` in `config/blog.toml`, 1 to 1440, counted from the END of the last pass): the site rebuild again, which rewrites only what differs from the store, and the two views again. A pass that fails is retried at the next wake. Everything else is on demand, when the Blog page sends a command. |

Six scheduled jobs are **not** on any service's loop — they are systemd timers,
generated from `config/sessions.toml` by `deploy/systemd/generate_units.py`, so moving
one needs `generate_units --install` plus a `daemon-reload` rather than a service
restart. The EOD report, gallery capture and flow-delta gate on the market calendar in
their own scripts, so their timers only exclude weekends; the labeler and the refit
read history and have no session to gate on. The HIRO report's timer also excludes
weekends only: with no `--date` it reports the newest closed session, so a holiday
firing just rewrites the previous trading day's report from the same stored minutes.

| Job | Slot | What it does |
|-----|------|--------------|
| Schwab sign-in check | **07:30**, every day (`[slots.token_watch]`) | `tools/token_watch.py` reads the proxy's `/health` and sends a Server alert when the sign-in has `[system] token_warn_hours` (48) or fewer left, has expired or been rejected, or cannot be read. A systemd timer. |
| Failure alert | when a unit ends up failed | Every generated unit carries `OnFailure=`; `tools/notify_failure.py` sends a Server alert, at most once per `[system] failure_repeat_hours` (6) for a unit that stays failed. A service reaches it after its restart budget is spent; a timer job on any error exit. |
| EOD report archive | **15:15** (`[slots.eod_report]`) | Writes `webgui/data/eod/<date>/summary.html` + `detail.html` — the `/eod` **Generate** button, unattended. Reads Redis only: no Schwab call, no Claude call. Writes nothing if every cache read was empty. |
| Marketing gallery recapture | **09:07** (`[slots.gallery_capture]`) | Re-photographs the private app for the public gallery. |
| Flow-delta instrumentation | **16:00** (`[slots.flow_delta]`) | The only measurement of the `[big_delta]` / UOA thresholds. |
| HIRO-model validation report | **16:10** (`[slots.hiro_report]`) | `tools/hiro_report.py` via `trading-<env>-hiro-report.timer` (Monday to Friday, `Persistent=true`, so a missed run catches up at boot): replays the day's `hiro_surge` / `hiro_flip` rules and scores the 5- and 15-minute price move after each fire against the same-direction base rate, into `options-scanner/data/hiro_report/<date>/report.md`. Reads `gex_history.db` only — no Schwab, no Redis, no Claude call. The only measurement of the `[hiro]` thresholds. |
| Trade Analyzer outcome labelling | **18:30** (`[slots.label_journal]`) | `tools/label_journal.py`: writes the realized 5/10/20-day forward returns (raw, beta-adjusted, and SPY's) onto recommendations whose horizon has passed. One daily-bar fetch per symbol through the proxy; labels everything outstanding, so a missed night catches up. |
| Swing model refit | **1st of the month, 19:00** (`[slots.swing_refit]`) | `tools/refit_swing_model.sh`: archives the live `swing_model.json`, refits it on five years of history, and replaces it **only if** the fit passes `config/swing_model.toml` — at least 90% of the 78-symbol universe loaded and an out-of-sample IC above 0. A refused fit is kept as `swing_model.rejected.json` + its report, the live model is untouched, and the run shows as failed. |

> **The GEX collection interval is 1 minute, not 2.** The serial per-symbol chain
> fetch was measured dropping roughly 37% of its slots; fetching in a small pool
> (`POLL_FETCH_WORKERS = 6`) and launching scheduler branches as keyed background
> tasks fixed it. A 2-minute figure also silently corrupted the flow-alert spike
> detector, which compares volume increments and reads a 2-minute delta as roughly
> twice baseline.

## Symbol Dossier look-up

The one paid path on `/symbol`: the `dossier` command on `cmd:options`
(`services/options_svc/dossier.py`), written to `cache:options:dossier:<SYMBOL>`.

| Constant | Value | Source |
|---|---|---|
| Schwab calls per look-up | **4**, **5** at most — quote · GEX chain (today..+7 d) · 1-year daily history · IV chain (+20..+45 d) · the IV analysis' today..+60 d fallback when that window is empty | `dossier.build_dossier` |
| Calls when the quote fails or finds nothing | **1** — the other legs are skipped | same |
| Cache lifetime | **900 s** (15 min) — a repeat visit inside it reuses the look-up | `handlers.DOSSIER_TTL_SEC` |
| Duplicate window | **60 s** — a second command for a symbol written this recently fetches nothing, unless that write was a `fetch_failed` | `handlers.DOSSIER_DEDUP_SEC` |
| Replay window | **180 s** — an older queued command is dropped | `handlers.STALE_OPEN_MAX_AGE_SEC` |
| Page poll | **2 s**, one batched version read; the poll **never** enqueues a look-up | `pages/symbol.py:POLL_SEC`, `should_enqueue` |
| Wait before **QUEUED** | **30 s** — the overlay drops and the chip reads QUEUED; the request stays on the stream and the poll picks up the answer. `cmd:options` has one consumer, so a look-up can sit behind a 26–40 s whole-chain Strategy Finder scan | `overlay.LOAD_TIMEOUT_SEC` |

A symbol the scanner covers costs nothing: every fact is already cached, and where the
cache and a look-up both hold a fact the cache wins, since the Opportunity Board is a
minute fresh and a look-up is a snapshot. A look-up is one or two chains on a click,
not a scheduled fan-out, so it does not need the off-quarter-hour placement the
scheduled chain bursts do.

## Desk spoken alerts and the arrival glow

Two Desk constants that are not derived from market data but are load-bearing, and
both have a silent failure mode behind them.

| Constant | Value | Where |
|----------|-------|-------|
| Glow span / steps | **10.0 s** over **10** classes (`desk-neon-0…9`) | `webgui/pages/desk.py:GLOW_SEC`, `GLOW_STEPS` |
| Glow hues | cyan `#22d3ee` = new row · amber `#f5b841` = flag change | `desk.py:SPOT_HEX`, `FLIP_HEX` |
| Default voice / rate | `en-US-AriaNeural` / `+8%` | `webgui/voice.py:DEFAULT_VOICE`, `RATE` |
| Voice cache key | `sha1(voice \| rate \| full sentence)` → `webgui/data/voice/<hex>.mp3`, served at `/voice` | `voice.py:clip_name` |
| Synthesis bound | **20 s** whole-call timeout; measured **~0.9–2.4 s** on a cache miss, **~110 µs** on a hit, ~22–28 KB a clip | `voice.py:SYNTH_TIMEOUT_SEC` |
| Abandoned-temp sweep | `*.part` older than **3600 s**, swept at prewarm | `voice.py:_PART_MAX_AGE_SEC` |
| Board re-entry quiet | a symbol back on the Opportunity Board within **1800 s** of leaving glows but is not spoken | `desk.py:BOARD_REENTRY_QUIET_SEC` |
| Section switches | `voice_board` · `voice_flow` · `voice_positions`, each under `voice_enabled`; detection still runs for a silenced section so re-enabling it announces nothing stale | `desk.py:VOICE_SECTIONS`, `detect_utterances` |

> **`GLOW_SEC` and `GLOW_STEPS` must move together.** The glow is a CSS animation,
> and the Positions panel rebuilds every row on each re-price — **a rebuilt element
> restarts its animation from zero**, so the obvious implementation glows forever.
> The row instead wears one of `GLOW_STEPS` fixed classes carrying a whole-second
> *negative* `animation-delay`, which starts the animation partway through, so a
> rebuilt row **resumes**. Raise the span without adding classes and rows land on
> `desk-neon-<n>` rules that do not exist — and the failure is silent, because a
> missing delay rule means the animation simply restarts.

> **The 20-second synthesis bound is the only thing bounding the whole call.**
> `edge-tts` carries its own timeouts, but they are *per operation* (connect 10 s,
> receive 60 s in 7.2.8) — a stream dribbling one chunk every 59 s trips neither, and
> 60 s alone is three times this budget. Past 20 s the endpoint is gone rather than
> slow, and waiting only pins a worker thread.

## Commissions

Source of truth: `config/commissions.toml`, loaded by
`services/options_svc/commission.py` — never hard-coded. Used by the Rescue
candidate menu's net-cash and ranking.

| Instrument | Rate |
|------------|------|
| Listed equity / ETF options | $0.65 / contract per leg |
| Index options | $0.65 / contract per leg + Cboe exchange-fee passthrough |
| Futures | $2.25 / contract per side |
| Let a leg expire | $0 |
