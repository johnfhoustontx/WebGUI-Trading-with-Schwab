# Technical Audit — WebGUI Trading with Schwab

**Date:** 2026-07-01 · **Branch:** `Using_Highcharts` · **Auditor:** Claude (five parallel deep-dive reviews: code quality, performance, architecture, security, reliability — all findings verified in code with file:line evidence; live measurements taken against the running Redis/SQLite stores where noted)

**Scope:** the full stack launched by `start_all_wt.bat` — NiceGUI webgui (:8500), five FastAPI domain services (:8210–8214), schwab-proxy (:8100), Memurai/Redis (:6379), engine libraries (options-scanner, sentiment-dashboard, trade-analyzer, portfolio-analyzer, claude-driver), shared bus/contracts. Judged for its actual purpose: a single-user, localhost, Windows trading stack.

---

## ⚑ Remediation status (updated 2026-07-01)

**Performance & Speed and Scalability & Architecture — ADDRESSED** (all suites green: options-scanner 1182 [+11 pre-existing baseline], options_svc 322, driver_svc 143, sentiment_svc 61, portfolio_svc 29, shared/bus 20, scaffold 13, webgui 658). See the CLAUDE.md "Last updated" entry for the full change list.

| Finding | Sev | Status |
|---|---|---|
| P1/A1 GEX retention (bounds the 3 GB DB) | High | **FIXED** — `purge_keep_sessions(keep=5)` on both tables, once/day, off-hours-persistence preserved; one-time `VACUUM` documented as a manual op (not auto-run) |
| P2 slim 14 MB `cache:options:gamma` | High | **FIXED** — cropped to display window server-side (16.3→3.07 MB $SPX), flip/walls crop-invariant (computed on full grid first) |
| P3 command handlers block event loop | High | **FIXED** — `run_in_executor` dispatch in `_scaffold` |
| A2 command-stream hygiene (drop/strand/growth) | High | **FIXED** — XADD maxlen + `cmd:{domain}:dead` dead-letter + startup PEL drain (surfaced, never silently lost nor blindly re-executed) |
| A4 serial scheduler tick | Med | **FIXED** — due branches run concurrently, per-branch isolation kept |
| P4 sentiment 24/7 heavy refresh | Med | **FIXED** — off-hours gate + once-per-session-day backfill + skip_unchanged (~95%+ off-hours call reduction) |
| P5/P6 big reads on the GUI loop | Med | **FIXED** — gamma + calc-chain reads via `run.io_bound` under `guard_async` |
| P8/P9 sargable GEX queries + conn reuse | Low | **FIXED** |
| P10 portfolio rebuild ungated | Low | **FIXED** — off-hours gate |
| A5 per-tab result-key race | Med | **DEFERRED (by decision)** — single-user-multi-tab edge case; request-id keying not worth the cross-cutting complexity now |
| A6 retire bridge shim + proxy version-skew | Med | **DEFERRED (by decision)** — bridge retirement is a live-scanner-gating migration; proxy-skew is an ops concern; left for a dedicated pass |

**Reliability & Error Handling — ADDRESSED** (the "add the evidence" batch; all suites green — see the CLAUDE.md entry). The house "never raises" defensiveness is kept; what changed is that degradation is now *recorded and surfaced* instead of silent.

| Finding | Sev | Status |
|---|---|---|
| R1 driver open results discarded | High | **FIXED** — captured + logged + surfaced per-trade (`last_open_results` on the driver view); retires the known silent-KeyError class |
| R2 dead scheduler invisible (/health green) | High | **FIXED** — `_scaffold` restarts a dead scheduler coroutine (backoff + `max_restarts` storm cap) and `/health` exposes `scheduler_alive`/`restarts` |
| R3 no persistent service logging + silent excepts | High | **FIXED** — per-service `RotatingFileHandler` bootstrap in `make_app` (off under pytest, idempotent); 19+ scheduler + handler/compute silent `except: pass` → `log.exception` |
| R4b/R8 stale/down invisible until /status | High/Med | **FIXED** — the existing chime/nav-badge plumbing now alerts on STALE views + down `/health` (transition-deduped, health probe throttled to 30s) |
| R5 stale trade commands execute on restart | Med | **FIXED** — additive `Command.ts` + a 3-min staleness gate rejects stale `driver_paper_create`/`paper_create` |
| R6 non-atomic paper open leaves BP drift | Med | **FIXED** — `reconcile_buying_power` recomputes reserved BP from open positions on both books at startup |
| R7 stand-down reason indistinguishable | Med | **FIXED** — `no_key`/`api_error`/`parse_error`/`model` taxonomy in the decider + surfaced in the /driver log |
| R9 4xx retries / log rotation / SSE backoff / tick guard / EOD atomic | Low | **FIXED** |
| R4a cross-process auto-restart supervisor daemon | (High-adjacent) | **DEFERRED (flagged)** — R2 (in-process restart) + R4b (alerting) cover visibility + self-heal-in-process; a standalone watchdog daemon that auto-restarts dead PROCESSES is new always-on machinery offered as an optional follow-up |

**Note:** `/health`'s `scheduler_last_tick_age_s` is "age since last (re)start" (the supervisor can't see inside a domain loop), so `scheduler_alive` is the load-bearing signal.

**Still open (remaining pillars):** Security (proxy wildcard CORS + no auth on the order path, Memurai no password, unpinned deps — though the R9 proxy 4xx/log-rotation touched the edges) · Code Quality (god-modules, `render()` closures, sys.path/collision debt, CLAUDE.md size).

---

## Executive summary

| Pillar | Score |
|---|---|
| Code Quality & Maintainability | **7 / 10** |
| Performance & Speed | **6.5 / 10** |
| Scalability & Architecture | **7 / 10** |
| Security & Compliance | **7 / 10** |
| Reliability & Error Handling | **5 / 10** |
| **Overall** | **6.5 / 10** |

The 3-tier re-architecture is genuinely executed, not aspirational: the GUI tier imports zero engine code (verified by grep AND enforced by tests), all eight servers bind 127.0.0.1, secrets were never committed, the LLM trading layer has a truly code-authoritative guardrail boundary, and the 2026-06-19 performance pass checks out line-by-line. Test discipline (~3,340 tests, behavioral, with architecture guard tests) is exceptional for a solo project.

The weak pillar is **reliability**, and it is the one that matters most for a system that autonomously opens (paper) trades: the house "defensive — never raises" pattern is implemented as *swallow without record*. Services have no log files, `except Exception: pass` blankets the schedulers, driver open results are discarded, and a dead scheduler leaves `/health` green. The codebase's own memory notes record that this exact pattern already hid a days-long "driver never opened a position" bug — and the structural hole is still open.

### Top 5 actions (highest value across all pillars)

1. **Stop discarding driver open results** — `services/options_svc/handlers.py:593` throws away `open_driver_position`'s status dict. Log it and surface opened/rejected/error per trade on the driver views. Smallest diff, retires the known incident class. *(Reliability)*
2. **Add GEX history retention on the live path** — `gex_history.db` is **3.0 GB** and growing ~250 MB/day; `purge_old` only runs from dead code paths, and `gex_term_snapshots` (3.09 M rows) is never purged at all. Purge to N sessions inside `collect_gex_snapshots` + one-time `VACUUM`. *(Performance + Architecture)*
3. **Give services real logging** — a ~15-line `RotatingFileHandler` bootstrap in `_scaffold.make_app`, then convert every silent `except Exception: pass` to `log.exception(...)`. The catching is policy; the silence is the defect. *(Reliability + Code Quality)*
4. **Lock down the proxy** — replace `allow_origins=["*"]` (`schwab_proxy.py:346`) with the webgui origin, and put even a static shared-secret header on `/orders`, `/accounts`, `/positions`. Today a malicious website in the browser can read account data and POST a **real order** to the unauthenticated proxy — the one place "localhost is my security" fails. *(Security)*
5. **Slim the 14.3 MB `cache:options:gamma` payload and run command handlers in the executor** — the gamma page parses 14 MB of JSON on the GUI event loop every 2 min, and a ~19 s `sim_fetch` freezes the entire options service (health checks, queued trade commands, scheduler) because handlers run directly on the asyncio loop (`_scaffold.py:72`). *(Performance)*

---

## 1. Code Quality & Maintainability — 7/10

*Exceptional testing discipline and enforced-by-test architecture, held back by god-modules and designed-in sys.path debt.*

### Strengths
- **3-tier import rule is enforced by tests**, not just convention — e.g. `webgui/tests/test_options_swing.py:26` asserts engine-import absence; grep of all `webgui/pages/**` confirms zero engine imports.
- **Guard tests encode hard-won lessons**: `test_no_inline_style.py` (Tailwind standard), `test_api_keys.py::test_no_module_shadows_stdlib` (after the `secrets.py` incident), scheduler cadence drift-guards.
- **~3,340 behavioral tests** repo-wide (webgui 656, options_svc 314, options-scanner 1,156…), including a Redis-driven e2e proving the driver-account isolation invariant with a non-vacuity check.
- **Pure-function separation is practiced**: figure/table/score builders are module-level pure functions, TDD'd; docstring coverage is high (89/103 functions in `options_svc/compute.py`); only 5 TODO/FIXME markers in the whole repo; 85 design docs under `docs/plans/`.
- **Extensibility scaffolding**: `services/_scaffold.py:make_app` (130 lines) gives every service health/scheduler/consumer for free; documented 4-step add-a-page pattern; config single-sourced in `repo_paths.py`/`config/ports.toml`.

### Key findings
| # | Sev | Finding |
|---|---|---|
| Q1 | High | **Monster `render()` closures**: `calculator.py` render() is 596 lines; `sentiment.py` 433; `gamma.py` 408; `driver.py` 403. Handlers nested inside are untestable and every edit means navigating a 400–600-line function. |
| Q2 | High | **`services/options_svc/compute.py` is a 3,039-line god-module** — 103 functions across ≥8 unrelated domains, including presentation code (`analyze_infographic_html`, `_ladder_svg` at lines 1638–1798) inside "compute". `handlers.py` mirrors it with a 165-line if/elif dispatcher. |
| Q3 | High | **Designed-in structural debt**: 98 `sys.path.insert` occurrences; hyphenated non-package engine dirs; `scoring`/`notifier`/`config`/`src` module-name collisions that forbid consolidated test runs and once crashed a service at launch (stdlib `secrets` shadowing). Contained by process isolation, but every contributor must absorb ~10 paragraphs of collision lore, and no packaging fix is planned. |
| Q4 | Med | **63 broad `except Exception` in options_svc/compute.py alone**, few with logging — deliberate policy that has already masked a production-grade bug (the driver KeyError incident). |
| Q5 | Med | **Poll/repaint plumbing copy-pasted across 13 pages** (`ui.timer(2, _poll)` + version-dict + repaint) — a shared `watch_views()` helper would remove ~30–50 lines/page. |
| Q6 | Med | **Scattered module-level mutable singletons** (`_NAV_OPEN`, `_CACHE`, `_LAST_CALC/_SIM`, `handoff._pending`) — acceptable single-user, but an invisible coupling web and a hard multi-user blocker. |
| Q7 | Med | **Legacy engine monoliths carried verbatim**: `gamma_tool.py` 7,185 lines (58 undocumented functions), `scanner_engine.py` 1,509 — load-bearing under the webgui. ~2 permanently-red date-relative options-scanner tests train people to ignore failures. |
| Q8 | Med | **CLAUDE.md is itself debt**: 2,463 lines with a ~200-line nested changelog header; reference + lore + history interleaved. |
| Q9 | Low | No lint/format/type gate anywhere (no ruff/mypy/pre-commit); `status.py` quietly violates the stated webgui import rule (`requests`, `subprocess`). |

### Recommendations
- **High:** split `options_svc/compute.py` by domain behind a re-export façade; decompose the four largest `render()` closures into `build_<section>(state)` functions; add a logging floor to every defensive catch (see Reliability).
- **Med:** shared `webgui/pages/poller.py` for the 13 poll loops; restructure CLAUDE.md to a ≤400-line core + `docs/CHANGELOG.md`; add `ruff` (+ optional `mypy` on `shared/`) — the code would pass mostly clean today, the cheapest time to adopt it.
- **Low:** corral page globals into one `session_state.py` with a test-reset hook; `xfail`-mark the known date-relative failures; plan the long-term packaging fix for the engine dirs.

---

## 2. Performance & Speed — 6.5/10

*The claimed 2026-06-19 optimization pass is real and verified; three unfixed structural issues will compound as data accrues.*

### Verified claims (all checked in code)
Off-thread webgui watcher (`main.py:519-541`) ✓ · cheap `:ver` version keys + pipelined batch reads (`shared/bus/client.py:132-153`) ✓ · `skip_unchanged` cache_set ✓ · vectorized EMA/MACD/volume-profile (`technical.py`) ✓ · `parallel_map` fan-outs ✓ · `/pricehistory` fix ✓ · pooled proxy session + non-blocking 0.2 s rate spacing ✓ · consumer-group create-once ✓. **Partial:** market-hours gating (options_svc yes; sentiment_svc and portfolio rebuild NOT gated) and sargable SQLite (two `DATE(ts,...)` queries remain on the 30 s status path, `gex_history_db.py:293-324`).

### Key findings
| # | Sev | Finding |
|---|---|---|
| P1 | High | **`gex_history.db` = 3.0 GB, growing ~250 MB/day** (measured: 288,300 snapshot rows + 3,091,010 term rows; today alone 36,080 rows / 263 MB of `gex_json` at 44 symbols × 4 views × 2-min cadence). `purge_old` only runs from the dead standalone-collector path; nothing ever purges `gex_term_snapshots`; no `VACUUM` anywhere. ~7 GB/month unbounded. |
| P2 | High | **14.3 MB `cache:options:gamma` payload** (measured) — full-day, full-chain grids for all four views, rebuilt from a 16 MB SQLite JSON re-parse every 120 s, then read + `json.loads`'d **synchronously on the NiceGUI event loop** in the page timer (`gamma.py:848`). The page displays one view cropped to ±20 strikes — ~90% of the bytes are discarded. Blocks every connected client; worst at end of session. |
| P3 | High | **Command handlers block the service event loop** — `_scaffold.py:72` calls the sync handler directly on asyncio (only the stream *poll* is executor-run). A ~19 s `sim_fetch` stalls `/health` (Status page flags the service down), serializes every queued command behind it — including trade commands — and pauses the scheduler. A sentiment handler comment (`handlers.py:37-40`) assumes executor dispatch that doesn't exist. |
| P4 | Med | **sentiment_svc refreshes 24/7 ungated**: every 120 s it re-runs `backfill_history(days=35)` from scratch plus ~24 live calls — ≈30–40 proxy→Schwab calls per cycle, nights and weekends; the history view republishes without `skip_unchanged`. |
| P5 | Med | **10.2 MB `cache:options:calc_chain`** (full ~7,000-contract chain), parsed on the GUI loop at symbol load. With P2, two keys are ~87% of Redis's 28 MB. |
| P6 | Med | `run.io_bound` is used in exactly two webgui files — no convention forces big payload reads off-loop. |
| P7 | Low | `skip_unchanged` does a full payload read+parse per call (O(payload), fine at current call sites); two non-sargable GEX status queries; 4 fresh SQLite connections per gamma refresh; portfolio 10-min rebuild ungated off-hours. Polling load itself is a non-issue (~3.5 tiny `:ver` probes/s worst case, measured). |

### Recommendations
- **High:** GEX retention + `VACUUM` on the live collection path (P1); crop/split the gamma cache server-side to the display window, target <1 MB, and cache the day's rows in-process (P2); dispatch command handlers via `run_in_executor` (P3).
- **Med:** off-hours gate + incremental backfill for sentiment (P4); adopt a "payloads >256 KB read off-loop" convention and apply to gamma/calc_chain (P5/P6).
- **Low:** make the two status queries sargable via the existing `_local_unix_range`; hash-compare in `skip_unchanged`; reuse one read-only connection across gamma views; gate the portfolio rebuild.

---

## 3. Scalability & Architecture — 7/10

*The 3-tier re-architecture is genuinely executed and honestly documented; two live defects don't need scale to hurt.*

### Strengths
- **Tier separation verified real** (zero engine imports in the GUI tier; guard tests); one consistent service scaffold (`make_app`) makes a 6th service a small task.
- **Engineered-cheap change detection**: separate `{key}:ver` counters, pipelined `cache_versions`, `skip_unchanged` — the right costing model for a poll-based GUI.
- **Sound in-service concurrency shape** (async loop + executor offload; `/health` responsive during compute — modulo P3 above).
- Pydantic contract envelopes gate cache writes; ports/paths single-sourced; the proxy choke point is *designed* (rate spacing without holding a lock across the round-trip, so fan-outs genuinely overlap).

### Key findings
| # | Sev | Finding |
|---|---|---|
| A1 | High | **The 3 GB unbounded GEX DB** (same as P1 — flagged independently by both auditors; retention exists only on dead code paths). |
| A2 | High | **Command delivery is at-most-once with a stuck-PEL failure mode**: `consume_commands` reads only `">"` — no `XPENDING`/`XAUTOCLAIM` ever; a service dying between read and ack strands the command in the PEL forever; `ack` runs in `finally` even when the handler raised, so a failed command is gone. Acceptable for `rescan`; **not** for `driver_paper_create` (a trade-open command) where a lost command means the decision log says "executed" while no position opened. `cmd:*` streams are also never trimmed (no `maxlen`/`XTRIM`) — unbounded by construction. |
| A3 | Med | **Head-of-line blocking on one serial command queue**: a 19 s interactive `sim_fetch` queues trade-path commands (`driver_paper_create`, `rescue_apply`) behind it. |
| A4 | Med | **Serial scheduler tick**: slow 15-min rescan branches delay the 2-min GEX and 5-min manage slots in the same loop; degrades silently as the watchlist grows. |
| A5 | Med | **Single "latest result" cache keys race with two browser tabs** (calc/sim/trade results have no request ID) — and the app's own UX encourages multiple tabs. Module-level single-user page state is honest and documented, but the failure mode is 2 tabs, not just 2 users. |
| A6 | Med | **Open migration shims + version-skew SPOF**: the sentiment bridge JSON is still dual-written every 120 s (Phase 6 open); the process answering on :8100 may be the *source repo's* proxy — production data can flow through code this repo doesn't contain (e.g. missing the /pricehistory fix). |
| A7 | Med | **Process consolidation is structurally forbidden** by top-level module-name collisions — the 7-process topology is load-bearing, not a choice. Packaging the engines would convert the constraint back into an option. |
| A8 | Low | Memurai + proxy are manual-recovery SPOFs; `INCR`+`SET` non-atomicity is documented and benign; Windows coupling is incidental (confined to `tools/` + batch scripts). |

### Recommendations
- **High:** GEX retention (A1, shared with Performance); Streams hygiene in `Bus` — `xadd(maxlen≈1000, approximate=True)`, drain the PEL on consumer start (`XAUTOCLAIM` or a one-time id-`"0"` read), and move ack out of `finally` for a must-not-drop allowlist (`driver_paper_create`, `rescue_apply`) with idempotency guards (A2).
- **Med:** fast/slow command lanes (or concurrent batch dispatch) so trade commands never queue behind interactive fetches (A3); make time-critical scheduler branches independent tasks (A4); retire Phase-6 shims and stamp/verify the :8100 binary via `/health` at startup (A6).
- **Low:** minimal watchdog probing `/health` and invoking the existing `restart_one.bat` path automatically (A8); `request_id`-keyed interactive results if two-tab use grows (A5); long-term engine packaging (A7).

---

## 4. Security & Compliance — 7/10

*Solid localhost fundamentals; the wildcard-CORS, zero-auth proxy holding Schwab tokens is the one place the threat model fails.*

Threat model calibrated to a single-user localhost app: (a) other local processes, (b) malicious websites reaching localhost cross-origin, (c) accidental port exposure.

### Strengths
- **All 8 servers bind 127.0.0.1** (verified: proxy `schwab_proxy.py:1471`, five services, webgui `main.py:711`). No `0.0.0.0` anywhere.
- **Secrets hygiene is clean**: `.gitignore` covers all token/key/db files; `git ls-files` confirms no real secret was ever committed; no secrets logged or cached to Redis; `/health` exposes booleans, not token material.
- **The LLM guardrail boundary is genuinely code-authoritative**: `guardrails.py` is pure; the model only picks menu IDs the scanner produced; quantity is clamped by code (NaN/inf/malformed → 0); `PAPER_TRADE` is a module constant not reachable via HTTP; `order_executor` short-circuits to simulation.
- Path-serving routes are traversal-safe (`/eod/file` regex + whitelist; `/manuals/file` whitelist); Claude-generated infographic HTML is `html.escape`'d; SQL is parameterized (f-strings only in internal schema-migration identifiers).

### Key findings
| # | Sev | Finding |
|---|---|---|
| S1 | Med→**High** if browser-borne attacks are in scope | **Proxy CORS `allow_origins=["*"]` + no auth** (`schwab_proxy.py:346-351`): a malicious web page can issue cross-origin requests to `:8100` and *read the responses* — accounts, positions, transactions — and `POST /orders/{account_hash}` places a **real Schwab order** gated by nothing (`PAPER_TRADE` governs only the driver, not the proxy). The single most consequential finding. |
| S2 | Med (Critical if any port exposed) | **No authentication on any surface** — `/terminate`, driver enable, restart spawns, and the proxy trading endpoints are all open to anything that can reach the port. The localhost bind is the *only* control. |
| S3 | Low | **Redis has no password** — any local process can enqueue `cmd:driver`/`cmd:options` commands (a second unauthenticated control plane behind the UI). |
| S4 | Low | Schwab OAuth tokens are plaintext files with default user ACLs — inherent to a local token cache; any process in the user's session can exfiltrate the refresh token. |
| S5 | Low | **Dependencies almost entirely unpinned** (`>=` floors, several bare; `anthropic` not even listed — lazy-imported); no lockfile. Supply-chain/reproducibility risk. |
| S6 | Low | One raw-HTML path skips escaping: the Explain fallback interpolates `symbol` unescaped (`compute.py:1267-1270`) — not attacker-controlled in practice (fixed dropdown universe), harden for consistency. |

### Recommendations
- **High:** restrict proxy CORS to `http://127.0.0.1:8500` and specific methods/headers (S1); add a shared-secret header (gitignored local token) required by the proxy's trading endpoints — converts "any local process or web page" into "only components holding the token" (S1/S2).
- **Med:** set Memurai `requirepass` + update `MEMURAI_URL` (S3); add a startup guard asserting every bind is loopback so a future `0.0.0.0` fails fast (S2); pin at least the security-sensitive deps (`fastapi`, `uvicorn`, `requests`, `anthropic`, `schwab-py`) via a lockfile (S5).
- **Low:** escape the Explain-fallback symbol (S6); tighten token-file ACLs or DPAPI-encrypt (S4); consider a confirm step on proxy `/orders` so a live order can never be a single unauthenticated POST (defense-in-depth).

---

## 5. Reliability & Error Handling — 5/10

*The skeleton is good — fail-safe AI path, correct retry asymmetry, real recovery tooling — but "never raises" is implemented as swallow-without-record, so the system keeps running while being unable to tell you it stopped working.*

### Strengths
- **Per-command crash isolation** in the scaffold (`_scaffold.py:52-87`); a bad command can't kill the consume loop.
- **The driver genuinely fails safe**: `parse_decision` is total over adversarial JSON; any failure → stand-down; the STOP kill-switch is re-read mid-cycle so it's honored during a slow Claude call.
- **Proxy logging is the best in the stack**: rotating `errors.log` + full INFO log; retry policy is correctly asymmetric (order POSTs never retried — no duplicate-order risk); the `/pricehistory` 404-flood fix is confirmed in place; token lifecycle failures are explicit and surfaced on the Status page with an Authorize button.
- Rescue apply has a real stale-price abort (>15% drift → no mutation, three-layer advisory refusal); webgui timer/client races are handled (`@guard` + the deleted-slot log filter); off-hours sparse data is handled explicitly, not by accident.

### Key findings
| # | Sev | Finding |
|---|---|---|
| R1 | High | **Driver open results are discarded** — `options_svc/handlers.py:593-599` ignores `open_driver_position`'s return; it never raises by design, so `{"status":"error"/"rejected"}` is invisible. The decision log's "executed" means *enqueued*. Any signal-shape drift or fill rejection re-runs the documented days-long silent-failure incident; the error string is now captured but still invisible. |
| R2 | High | **One unguarded tick permanently kills the sentiment scheduler while `/health` stays green** — `sentiment_svc/scheduler.py:40-42` isn't try/except-wrapped (unlike every other service); a single Memurai blip ends the task forever; the scaffold never restarts a dead scheduler coroutine and health doesn't reflect it. |
| R3 | High | **Services have no persistent logging at all** — zero file handlers in `services/*`; 63 broad excepts vs 3 log statements in the biggest compute; ~15 `except Exception: pass` in the options scheduler (rescan/GEX/manage/analyze failures produce *no output*). Console output dies with the terminal tab. A week of failing manage cycles would be undiagnosable. |
| R4 | High | **No supervision or error alerting** — crashed services leave dead `cmd /k` tabs; the proxy intentionally exits daily at 15:30 CT (`_shutdown_scheduler` → `os._exit(0)`) after which everything silently degrades; the only push alert in the system is the new-scanner-signal chime. A dead options_svc means no 5-min manage/auto-exit on open positions — a risk gap, not a convenience gap. |
| R5 | Med | **Poison-pill batch loss + stale trade commands**: one malformed stream entry fails the whole batch un-acked into the orphaned PEL; no dequeue-time staleness check — a `driver_paper_create` consumed 3 hours late executes on stale economics, corrupting the driver's measured P&L. |
| R6 | Med | **Multi-step paper mutations aren't atomic** — record-order → reserve-BP → insert-position are three separate commits; a crash between them leaves BP reserved against no position with no reconciliation pass to detect it. |
| R7 | Med | **Stand-down-on-error is indistinguishable from stand-down-by-choice** — no key, expired key, network outage, and "no edge today" all produce the identical decision-log entry. A rotated API key would look like weeks of model caution. |
| R8 | Med | **Staleness detection is passive** — the 600 s STALE flag exists only on `/status`; the existing alert plumbing (chime/badges/desktop notifications) was never pointed at staleness or health. |
| R9 | Low | Proxy retries deterministic 4xx (2 wasted retries + backoff each); the INFO log never rotates; portfolio SSE reconnects every 3 s forever, silently; the app-wide `_tick` watcher raises a traceback every 2 s per page when Memurai is down; EOD archives are written without temp+rename (a crash mid-generate leaves a half-file that `/eod/file` serves). |

### Recommendations
- **High:** capture and surface `open_driver_position` results per trade (R1); logging bootstrap in `make_app` + `log.exception` in every scheduler guard (R3); wrap the sentiment tick and make the scaffold restart dead scheduler coroutines, reflecting "scheduler dead" in `/health` (R2); point the existing watcher/chime plumbing at STALE views and down services (R4/R8 — zero new infrastructure).
- **Med:** per-entry decode + dead-letter for poison commands, stream `maxlen`, and a max-age check in `driver_paper_create`/`paper_create` handlers (R5); single-connection or reconciliation-pass atomicity for the open sequence (R6); a `reason` field on driver stand-downs (R7).
- **Low:** don't retry 4xx; rotate the INFO log; backoff + one-line logging on SSE reconnect; `guard_async` + log-once memo on the watcher tick; temp-file + `os.replace` for EOD writes.

---

## Cross-cutting themes

1. **Silent degradation is the systemic risk.** Four of five auditors independently converged on the same root pattern: broad catches with no record (Q4), discarded results (R1), un-logged scheduler failures (R3), indistinguishable stand-downs (R7), invisible staleness (R8). The fix is one theme, not many: *keep the defensiveness, add the evidence* — log files, surfaced error states, and alerts wired into plumbing that already exists.
2. **Two live resource leaks need no growth to hurt**: the 3 GB GEX DB (found independently by two auditors) and the never-trimmed command streams.
3. **The trading-command path deserves stronger semantics than the refresh path.** Today `driver_paper_create` shares at-most-once delivery, a serial queue behind 19 s interactive fetches, ack-on-failure, and no staleness check with `rescan`. Splitting "must not silently drop" commands from "idempotent refresh" commands resolves A2/A3/R5 together.
4. **The proxy is the crown jewels and the least defended component** — it holds the OAuth tokens and the real-order endpoint, with wildcard CORS and no auth. Two small changes (CORS allowlist + shared-secret header) close the only realistic remote path to real-money impact.
5. **What's working should be protected**: the guard-test culture, the pure-function discipline, the contract-gated cache writes, and the code-authoritative guardrails are the assets that make everything else fixable. Add `ruff`/lockfile/logging now, while the codebase is clean enough to adopt them cheaply.
