# Development and production environments — design

**Date:** 2026-08-08
**Status:** approved, ready to plan

## Problem

The repo has grown into a nine-process stack (proxy + six domain services + webgui,
plus the HUD) that runs live during market hours and holds real state: a ~1.5 GB
`gex_history.db`, two paper books, a Schwab OAuth token, and outbound channels that
push to a phone and to a public X account.

Editing that stack *is* running it. There is no way to try a change without risking
the live one, and no way to leave the live one collecting while you work.

We need two environments on **one machine**, running **simultaneously**.

## What each environment is for

**prod** — the always-on stack. Collects, schedules, notifies, trades paper. Pinned
to `main`; only changes when explicitly promoted.

**dev** — where code is edited. Runs the full service topology so service-side changes
are testable end to end, but **collects nothing and emits nothing** by default. It
works off a **snapshot** of prod's data taken when a development session starts.

## Topology

| | prod | dev |
|---|---|---|
| Folder | `D:\WebGUI Trading Prod` (new clone, pinned to `main`) | `D:\WebGUI Trading with Schwab` (today's folder) |
| schwab-proxy | **owns** it, `:8100` | **borrows** prod's `:8100` — starts no proxy |
| sentiment / options / portfolio / trade / driver / market | 8210–8215 | 9210–9215 |
| webgui | `:8500` | `:9500` |
| Redis | Memurai `:6379` **db 0** | same server, **db 1** |
| SQLite, logs, `webgui/data` | its own | its own |
| Schedulers, Claude, notifications, autonomous driver | live | **off** |

Prod's port numbers are byte-identical to today's, so standing prod up is a
relocation, not a reconfiguration.

### Why one shared proxy

The Schwab OAuth **refresh token is a single rotating credential**. Two proxies
holding the same one can invalidate each other's session; two *separate* Schwab
applications would mean a second developer app and a second authorization to keep
alive. Sharing prod's proxy sidesteps both, and costs nothing while dev's collectors
are off.

**Accepted consequence:** dev's *on-demand* fetches — Trade analyze, swing scan,
Calculator chain loads — need prod's proxy running. With a snapshot loaded this is
rarely blocking, but it is a real dependency, not a detail.

### Why a shared Memurai, separate logical DB

Redis db 1 gives complete key isolation with no second service to install, run or
monitor, and makes `FLUSHDB` on dev a safe, one-line reset. The cost is that
restarting the Memurai *service* takes both environments down — handled in the
safety rails below.

## Environment identity

Two files:

**`config/environments.toml` — tracked.** Defines both profiles: `port_offset`
(prod 0, dev 1000), an explicit `proxy_port` override (dev pins 8100), `redis_db`,
`owns_proxy`, and the four behavior flags. Version-controlled, so a profile change
is reviewable and diffable in one place.

**`config/env.local.toml` — gitignored.** `name = "dev" | "prod"`, plus an optional
`peer_root` naming the other checkout (the snapshot tool needs prod's path, and that
path is machine-local). **A missing file resolves to `prod`**, so any checkout
without a marker behaves exactly as the repo does today.

The marker being gitignored is the load-bearing property: **`git pull` into prod can
never carry dev's identity with it**, in either direction. That was the deciding
factor over an environment variable (anything launched outside the launcher — a bare
`python webgui/main.py`, a pytest run, the Status page's restart buttons — would have
no variable and would silently guess) and over folder-name detection (renaming a
folder must not change runtime behavior on a live trading stack).

### Where resolution lives

Inside **`repo_paths.py`**, not a new module. It already parses `ports.toml` and is
imported by ~40 files; keeping the logic there avoids any import-order or cycle risk.
It gains `ENV_NAME` and an `ENV` flags mapping, and `SERVICE_PORTS` / `NICEGUI_PORT` /
`PROXY_PORT` / `MEMURAI_URL` become profile-derived.

Every consumer — the services, the launchers, `tools/stop_all.py`,
`tools/restart_one.bat`, the Status page — already reads those constants from
`repo_paths`, so they follow the environment with no edits of their own.

### Behavior under pytest

**Decision:** under pytest the profile forces all four suppression flags ON but uses
the **prod port table**, regardless of the marker file.

Tests are hermetic (the bus is already fakeredis under pytest), so ports are inert
constants to them. This keeps the existing suites passing unchanged inside the dev
checkout, while guaranteeing no test can reach Anthropic or a notification channel.

## The four suppressions

Each hangs on a flag and reuses a degrade path the code **already has**, so a
suppressed dev cannot take a code path prod never takes.

| Flag | Chokepoint | Effect |
|---|---|---|
| `allow_claude` | the three client factories — `options_svc/compute.py:3458`, `market_svc/compute.py:341`, `driver_svc/decider.py:231` — return `None` | falls into the existing *no-API-key* path: the briefing renders its explanatory page, the ticker narrative is empty, the decider stands down |
| `allow_notifications` | `shared/notify/channels.py:load_config` and `options_svc/push_notify.py:load_config` return `{"enabled": False}` | one stroke kills Telegram, Discord, Fi-SMS, the public **X/Twitter** poster, and the sentiment state-transition alert |
| `schedulers` | `services/_scaffold.py:make_app` skips wiring the scheduler | all six services stop collecting and polling; command handlers still run, so the UI stays fully usable off the snapshot. `TRADING_ENABLE_SCHEDULERS=1` turns them on for a session when collection itself is what's being tested |
| `autonomous_trading` | `driver_svc/handlers.run_autonomous_cycle` early-returns | belt-and-braces: `cycle` is also a *command*, so the scheduler skip alone would not stop a snapshot that carried `cache:driver:control` enabled |

Roughly six one-line guards.

### Known gap in the notification gate — the legacy `notifier.py` modules

The table above says `allow_notifications` kills every channel "in one stroke".
That is true of every path a **service** can take, and it is **not** total.

`options-scanner/notifier.py` and `sentiment-dashboard/notifier.py` are legacy
desktop-era senders that read their own config and do **not** route through
`shared/notify/channels.load_config`. They are knowingly left outside the gate.

Reachability, verified 2026-08-08: `grep -rn "import notifier\|from notifier"`
over the repo returns `options-scanner/tests/test_notifier.py`,
`options-scanner/tests/test_notifier_channels.py`,
`sentiment-dashboard/tests/test_notifier.py`, and one line inside
`options-scanner/notifier.py`'s own module **docstring** (a usage example, not an
import). **Nothing in `services/`, `webgui/` or any launcher imports either
module** — they are dead from every running path,
which is why gating them would be adding a guard to code that cannot execute.

The residual risk is a human one: running one of them **by hand from a dev
checkout** would push for real. Recorded in the runbook's known-limits section
rather than fixed, because the honest fix is deleting them, and that is a separate
decision from this branch.

## Cross-environment safety rails

Two co-existing stacks create hazards that do not exist today. Three are already
latent in the code:

**1. `tools/stop_all.py` would kill prod's proxy from dev.** `_targets()` leads with
`("proxy", PROXY_PORT)`, and dev's `PROXY_PORT` *is* 8100 — prod's. Skip the proxy
target when `owns_proxy` is false, so dev's Terminate stops only dev's seven
processes.

**2. `stop_all` would kill prod's HUD from dev.** The HUD binds no port, so it is
matched by command line (`nq_hud.py`), and that match cannot distinguish two
checkouts. Require the **own checkout's root path** in the matched command line. The
file's own docstring records that loose matching here once took the whole stack down.

**3. The Status page's Restart buttons.** In dev, the proxy card would offer a restart
that bounces **prod's** proxy, and the Memurai card would restart a Windows service
both environments share. When `owns_proxy` is false the proxy card renders read-only
as *"shared — owned by prod"*, and the Memurai restart is hidden in dev.

**4. Telling the two browser tabs apart.** Dev's webgui carries a `DEV` chip in the
header lockup and an env prefix in the tab title. Two identical-looking NeuralStrike
tabs writing to different books is a mistake waiting to happen.

## Snapshot tool

`tools/snapshot_from_prod.py`, run **from the dev checkout** (the destination), which
**hard-refuses unless `ENV_NAME == "dev"`** — it can never overwrite prod, whatever
arguments it is given.

- **SQLite** via `sqlite3.Connection.backup()`, the online-backup API, so **prod keeps
  running and writing** throughout. Copies `options-scanner/gex_history.db`, the six
  `options-scanner/data` stores, the four `sentiment-dashboard/data` stores, and
  `services/trade_svc/data/iv_history.db`; plus `shared/sentiment_bridge.json` and the
  `Top 20.xlsx` watchlist.
- **Redis**: `FLUSHDB` on db 1, then `SCAN` + `DUMP`/`RESTORE` from db 0, preserving
  types and TTLs. **Excludes `cmd:*` streams** (dev must not inherit a queued command
  backlog) and **forces `cache:driver:control` to disabled**.
- Refuses to run while dev's services are up — they hold DB handles and would write
  over the restore — naming the ports still listening.
- Flags: `--dry-run`, `--redis-only`, `--skip-gex`. Reports per-item size and elapsed.

Expect roughly 30–60 s and ~1.5 GB of disk for a full run, dominated by
`gex_history.db`.

## Cutover and promotion

**One-time cutover**, mostly checklist rather than code: clone to
`D:\WebGUI Trading Prod` on `main`; build its venv from `requirements.lock`; copy the
gitignored secrets (`appsettings.json`, `tokens.json`, `proxy_tokens.json`,
`notifications.json`, `anthropic_key.txt`, `driver_model.txt`) and the watchlist
workbook; copy the live data **once** into prod, after which dev keeps its existing
copy as its first snapshot; write `env.local.toml` in each checkout; stop the old
stack and start prod from the new folder; verify `/status` is green and Schwab is
authorized; repoint the desktop shortcut.

**Promotion stays explicit and manual** — that is what pinning prod to `main` buys.
In dev: merge to `main`, push. In prod: `tools\promote.bat` → `git pull`, reinstall
dependencies only if `requirements.lock` changed, restart the stack.

## Verification

Unit tests for the profile resolver: marker present, absent, and malformed; absent ⇒
prod; pytest ⇒ suppressed flags with prod ports. Guard tests that `stop_all` excludes
a non-owned proxy and that the HUD matcher is root-scoped. A guard that no test can
build a real Anthropic client or load a notification config with `enabled=True`.
Snapshot *planning* tested as a pure function; the copy smoke-tested against temp
SQLite files and fakeredis.

Then live, both stacks up at once: `/status` green on both, dev's Terminate leaves
prod untouched, dev's header shows `DEV` — and the acceptance test that matters,
**dev at rest adds zero calls to prod's `/stats/api_calls` over a 10-minute window**.

## Rejected alternatives

- **Dev runs its own proxy.** Two holders of one rotating Schwab refresh token, or a
  second developer app to maintain. Not worth it while dev collects nothing.
- **Dev collects its own data.** Would roughly double Schwab volume against a stack
  already issuing ~68–76k calls/day, for data a snapshot provides for free.
- **Environment variable or folder-name for identity.** Both fail the "must survive
  being launched any other way" and "renaming a folder must not change behavior"
  tests. See *Environment identity*.
- **Dev as a git worktree.** Shares one `.git` with prod; too easy to disturb the
  checkout of a stack that has to stay up.
