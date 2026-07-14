# schwab-proxy — CLAUDE.md

> Cross-app paths and service ports come from the root `repo_paths.py`
> (which reads `config/ports.toml`). Never hard-code `D:\` paths or ports —
> import them. See the root `CLAUDE.md` for the monorepo overview.

## Purpose

Central Schwab API gateway and token manager for the whole monorepo. It owns
Schwab OAuth, refreshes tokens, rate-limits outbound Schwab calls, and exposes a
local HTTP API so the other apps share one set of credentials instead of each
authenticating directly. **It must be started first** — options-scanner,
sentiment-dashboard, and claude-driver all fetch market data through it.

## Entry point & port

- Entry: `schwab_proxy.py` (also `Launch_Proxy.bat`).
- Serves on `http://127.0.0.1:8100` (`PROXY_URL` / `PROXY_PORT` from
  `repo_paths.py`).
- Key endpoints: `/health`, `/stats/api_calls` (per-day outbound Schwab API-call counts — today / last 7 / last 30 days; counted at the marketdata rate-limit chokepoint + the trader request loop into `data/api_call_counts.db`, best-effort/never-raises; feeds the webgui Settings "API usage" card), `/quote`, `/quotes`, `/chains`, `/pricehistory`,
  `/instruments` (fundamentals; `projection=fundamental` → P/E, growth, ROE,
  margins — used by trade_svc), `/accounts`, `/positions`, `/positions/{account_hash}`,
  `/orders/{account_hash}`, and the trade-stream tracker (`/track`, `/untrack`).
- **`/positions` aggregates ALL linked accounts** (not just the first): it loops
  every account hash from `/accounts/accountNumbers`, normalizes each, and folds
  same-symbol holdings across accounts into one row via `_merge_positions` (sums
  qty/market-value/P&L, quantity-weights `avg_price`) so a multi-account user sees
  one whole-account book. A per-account fetch failure is logged and skipped; only a
  total failure surfaces. `/positions/{account_hash}` still returns a single account.

## Key files

| File                 | Role                                                            |
|----------------------|-----------------------------------------------------------------|
| `schwab_proxy.py`    | FastAPI/uvicorn app, token mgmt, proxy + trader endpoints, stream worker. |
| `proxy_client.py`    | Client helper imported by the other apps to call the proxy.     |
| `trade_registry.py`  | Registry of tracked OptionsScanner paper trades.                |
| `trade_detector.py`  | Detects fills/events from the option stream.                    |
| `perf_writer.py`     | Writes trade-performance events + IV snapshots.                 |
| `stream_bridge.py`   | `schwab.streaming` LEVELONE_OPTIONS/EQUITIES subscription bridge. |

**Streaming SSE fan-outs (2026-07-07).** The shared `_stream_worker` fans level-one ticks to SSE
subscribers via **`/stream/quotes?symbols=…`** (equities — `_normalize_level1_equity` widened with
bid/ask/sizes/last-size/volume + RTH `REGULAR_MARKET_*` fallbacks) and **`/stream/options?symbols=<OSI,…>`**
(options — new `_normalize_level1_option` + a refcounted OSI union that is **provably additive to
paper-trade tracking**: the reconcile subscribes `_registry.legs_union() ∪ flow_osis` on the serialized
stream loop, and the trade-untrack orphan guard spares `_option_refcount`, so a tracked leg can NEVER
lose its subscription; the `_on_option_message` trade-detector block is byte-identical, fan-out appended
after). Consumed by `portfolio_svc` (equity P&L) + `sentiment_svc`'s `order_flow_consumer` (aggressor
order-flow for the five-state classifier). Both refcounts support multiple concurrent subscribers.

## Logging

`logs/schwab_proxy.log` holds the full INFO stream. A dedicated
`logs/errors.log` captures **ERROR/CRITICAL only**, via a
`TimedRotatingFileHandler` that rotates **weekly (Monday)** and keeps
`backupCount=4` weeks before auto-deleting the oldest — effectively a weekly
purge. Both handlers plus the console are wired in the `logging.basicConfig`
block at the top of `schwab_proxy.py`.

## Configuration & secrets

Reads `shared/appsettings.json` (Schwab API keys) and `shared/tokens.json`
(OAuth) via `repo_paths.APPSETTINGS` / `repo_paths.TOKENS` — both gitignored,
with `.example` templates in `shared/`. Maintains `proxy_tokens.json`
(gitignored runtime cache). Writes trade DBs into `options-scanner/data/`.

## Dependency on the proxy

This **is** the proxy — it has no upstream dependency in the repo. It only needs
valid Schwab credentials in `shared/` and a network connection to Schwab.

## Tests

```powershell
cd schwab-proxy && python -m pytest tests
```
