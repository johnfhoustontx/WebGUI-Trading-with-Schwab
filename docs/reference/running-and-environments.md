# Running the stack, environments and deployment

> Moved here VERBATIM from `CLAUDE.md` on 2026-10-04 (audit CQ-09), when
> that file was cut from 327 KB to under 100 KB. `CLAUDE.md` keeps the rules;
> this file keeps the detail behind them: the incident, the measurement and
> the reasoning. It is a living reference. Correct it in place, exactly as
> the rules in `CLAUDE.md` say, and never append a correction under a wrong
> sentence. Its section headings are the ones `CLAUDE.md` used.

## Running

**The stack is ten `systemd --user` units on a Linux host** — the target, the
proxy, the six services, the web app, and `webgui_live`, the public read-only
screens (2026-09-07). There are no
launcher scripts: the twelve `.bat` files, `tools/stop_all.py`, `watchdog.py` and
both `check_stack_*` helpers were deleted in the 2026-08-29 migration, because
every one of them existed to work around something Windows lacks.

```bash
systemctl --user start trading-prod.target     # or stop / restart / status
```

```bash
systemctl --user list-units 'trading-prod*'
```

**The units are GENERATED, and no `.service` file is in git.**
`deploy/systemd/generate_units.py` derives every value from `repo_paths` — ports,
the checkout root, the environment name, and whether this environment owns a
proxy. A committed unit would be a second copy of all of that, free to drift, and
the drift would surface only as a Restart button that errors in prod.

```bash
.venv/bin/python -m deploy.systemd.generate_units --install
```

Regenerate after anything that changes a port, a path, or the environment
identity. `tools/promote.sh` already does it on every promote.

⚠ **`--install` ARMS the timers as well as writing them, and that is not a
convenience.** A generated `.timer` that nothing enables is a FILE, not a
schedule — it sits there `disabled`, correct in every byte, and never fires.
That is what happened to `trading-prod-flow-delta.timer`: eleven days of
instrumentation reports were simply never produced, and because the tool exits
0 whether it measured anything or not, the only symptom was a directory that
stopped gaining dates. Every other timer on that host was armed because a human
ran `enable` once, unrecorded, so a **rebuilt box would have got the whole set,
all disabled**. `--install` now does its own `daemon-reload` and
`enable --now`s each timer it wrote, derived from `render_all()` so a timer
added to the generator cannot be forgotten here. It is idempotent — an
already-armed timer reports `already enabled` and nothing changes.

**Only the target is still yours to enable**, because that is the deliberate act
of making this checkout the one that starts at boot (with
`loginctl enable-linger`). **Dev arms nothing** — it generates the same timers,
but its stores are a disposable copy of prod's and gallery/live-capture drive
public surfaces a second checkout must never publish to.

| What it replaces | Directive |
|---|---|
| `wait_and_run.bat` port waiting | `After=` + `ExecStartPre=tools/wait_http.py` |
| `start_all_wt.bat` ordering | `Requires=` / `After=` |
| `start_all_hidden.bat` hidden relaunch | nothing — services have no console |
| `tools/watchdog.py` storm-capped restarts | `Restart=on-failure` + `StartLimitBurst` |
| `stop_all.py`'s WMI hunt for this checkout's PIDs | `PartOf=` — systemd owns the PIDs |
| `check_stack_up/down.py` | `systemctl --user is-active` |
| `restart_one.bat` + `CREATE_NO_WINDOW` | `systemctl --user restart` |
| `logs\*.out.log` redirection | `journalctl --user -u <unit>` |

⚠ **`StartLimitIntervalSec` / `StartLimitBurst` belong in `[Unit]`, not
`[Service]`.** systemd moved them in v229 and **silently ignores them** in the
wrong section, so the storm cap would look configured and not exist. The
**`MemoryHigh`/`MemoryMax`** pair runs the other way — `[Service]`, where cgroup
resource control lives — and carrying both traps in one file is why each is
pinned by its own test.

⚠ **Two units carry a memory cap**: `webgui_live` (`LIVE_MEMORY_HIGH` 768M /
`LIVE_MEMORY_MAX` 1G) and the private web app (`APP_MEMORY_HIGH` 1536M /
`APP_MEMORY_MAX` 2G; it parses the one unauthenticated body, `POST /login`, which
is also limited to 16 KB at the edge and in the handler). `webgui_live` is the one
internet-facing, unauthenticated, **unthrottled** process — no Caddy `rate_limit` (it needs an `xcaddy` build), and
a measured ~619 KB of retained NiceGUI `Client` per anonymous GET pruned only
after ~70 s. The cap does not stop a flood; it decides **who dies** in one: the
public screens alone, back on `Restart=on-failure`, instead of the OOM killer
choosing among the services, the trading UI and Redis. ⚠ **Never add one to the
services or the proxy as a drive-by** — they are not internet-facing and a wrong
value kills the stack. The public unit also sets `NoNewPrivileges`; ⚠ path
isolation (`InaccessiblePaths`, `ProtectHome`) is **silently not applied** in a
`--user` unit on this host, so a test refuses those directives rather than let
them read as protection. The request rate is bounded only when `config/edge.toml`
turns Caddy's page-load limit on — off by default, because it needs a custom
Caddy build with no apt security updates (runbook "Edge rate limit").

**A unit that ends up FAILED sends a push (2026-10-03).** `render_all` adds
`OnFailure=trading-<env>-notify-failure@%n.service` to every generated
`.service` in ONE place, so a unit added tomorrow alerts without anyone
remembering. The template runs `tools/notify_failure.py`, which sends through
the `system` push category (`shared/notify/system_alert.py`; a row in Settings →
General) and repeats a still-failed unit at most every `[system]
failure_repeat_hours` (`config/notify.toml`). It fires on the failed STATE: a
service reaches it only once its restart budget is spent, a timer job (the
backup, a report) on any non-zero exit. ⚠ A job that exits 0 without doing its
work still tells nobody. `trading-<env>-token-watch.timer` runs daily, weekends
included, and warns inside `[system] token_warn_hours` of the Schwab sign-in
lapsing, off the proxy's `/health` `refresh_token_hours_left`; an answer it
cannot read is reported, never taken as plenty of time. Dev sends none of this.

**The nightly backup carries every gitignored file a restored checkout needs,
and a test derives that list from `.gitignore`**: a new credential under
`shared/` goes in `backup_local.EXTRA_FILES` or, with its reason, in
`NOT_BACKED_UP`. The login store was missing until 2026-10-03. Kept: three
dailies plus one generation from each of four earlier weeks (one earlier week
offsite); a clean run writes `BACKUP_OK`, pruning runs after it, and the newest
marked generation is never pruned. Restore with `tools/restore_backup.py`
(stdlib only; it leaves existing files alone unless `--force` and
integrity-checks each database) — runbook section 11. ⚠ It does not restore
Redis, and it has been exercised by the test suite only, never against a
production generation.

⚠ **`--user`, never system units.** That is what lets the Status page restart its
own siblings with no polkit rule and no sudoers entry; a system-unit equivalent
would mean handing root to a network-facing app. `loginctl enable-linger <user>`
is what makes them start at boot and survive logout — without it the whole stack
dies when the login session ends.

⚠ **Ownership is encoded in which units EXIST.** `components()` emits a proxy
unit only when `OWNS_PROXY`, so a dev checkout that borrows prod's proxy simply
has none for its target to pull up. There is no kill-list filter to remember.

**Redis is a SYSTEM unit** (`redis-server`), not part of the target — a
`systemctl --user` stop cannot reach it even in principle, which is why the
Status page's Redis card is read-only in every environment.

**Stopping** is the same target, and that is what the GUI's **More → Stop All
Services** page runs (`systemctl --user --no-block stop trading-<env>.target`).
`--no-block` registers the job with the systemd manager, so the web app being
stopped partway cannot orphan the shutdown.

⚠ **`is-active` going inactive is NOT proof the ports are free.** Measured
2026-08-29: the target reports inactive about a second before its members'
listening sockets close. systemd serialises start-behind-stop per unit so it
cannot race a `systemctl start`, but anything else that binds must wait for the
sockets, not the unit state. `tools/promote.sh` does both.

**Logs** are the journal. `journalctl --user -u trading-prod-options_svc -f`.
`webgui/logging_setup.py` still writes `logs/webgui.log` as well.

**Reaching the app from a workstation.** The normal route is
**`https://app.neuralstrike.co`** — Caddy (a *system* unit, since it needs :443)
terminates TLS and reverse-proxies to the app, which **still binds `127.0.0.1`**.
Behind it sits a password + TOTP login: `webgui/auth_middleware.py` default-denies
every `http` and `websocket` scope except `/login` and `/favicon.ico`. ⚠ **That
list has no other exception, and the rule is total** — a loopback peer buys
nothing, neither does the absence of the `X-Edge` header, and `/static` and
`/_nicegui*` are gated like every other path. It carried a three-condition
loopback exemption for the wall kiosk until 2026-09-23; that went with the page.
Design + plan:
[`docs/plans/2026-09-06-webgui-credentialing-{design,plan}.md`](../plans/2026-09-06-webgui-credentialing-design.md).

**`https://live.neuralstrike.co`** is the same shape with the login taken out: a
third Caddy host block reverse-proxying to `webgui/live_main.py` on `:8501`, which
**also binds `127.0.0.1`**. It runs no auth middleware at all — that is the point
— so the origin separation IS the control. See “The public live screens” above.

`tools/open_webgui.ps1` survives as the **fallback**: if the cert or Caddy breaks,
the way in must not depend on the thing that broke. The proxy on `:8100` is
**never** on the public domain — it is published on the tailnet by
`tailscale serve`, which is also how the Schwab refresh token gets re-minted at
`/auth` every 7 days. ⚠ A link a BROWSER opens must use `PROXY_PUBLIC_URL`
(`proxy_public_url` in `env.local.toml`), never `PROXY_URL` — that is where the
server reaches the proxy, and in the viewer's browser 127.0.0.1 is their own
device. The Status page's Authorize button shipped that way and was dead.

⚠ **The proxy's account routes fail CLOSED, and it has no order route
(2026-10-03).** `/accounts*`, `/positions`, `/transactions` and `/passthrough`
need `X-Proxy-Secret`, and with no `PROXY_SHARED_SECRET` configured they answer
**503 naming the variable** rather than 200 (`require_account_secret`). The
market-data routes stay open on loopback, as before. `/passthrough` forwards
only `PASSTHROUGH_ENDPOINTS`, five market-data paths: it took any path, and
`/../../trader/v1/...` normalised onto the brokerage API. `trader_request`
raises on anything but GET — this app is paper-only, so a second Schwab verb is
a decision, not an edit. A new caller of an account route sends the secret
(`proxy_client._apply_secret`) and surfaces the proxy's `detail`, or a missing
secret reads as "no positions".

⚠ **Never change any of these binds to `0.0.0.0`.** The login is a second control,
not a replacement for the first — Caddy is the only thing that should ever talk to
`:8500` or `:8501`, and the wall exemption's loopback condition is what stops a
widened bind turning into an open door.

**Manual start**, if you are debugging a single component rather than running the
stack:

```bash
.venv/bin/python services/options_svc/app.py     # :8211
```

Same order as the units: Redis, then the proxy on :8100, then the six services
(8210–8213, 8215, 8216), then `webgui/main.py` on :8500. Everything reads market data through
the proxy, so it starts first. `webgui/live_main.py` on :8501 orders after nothing
in the target — it reads Redis (a *system* unit) and nothing else.

> **3-tier note:** Once a domain is migrated, the web GUI no longer computes
> anything for it — its **service must be running** (and Redis up) or the page
> shows a "Waiting for … service" placeholder. **All five domains are migrated**;
> every page reads Redis, and the webgui imports no app engines, so the
> documented `scoring`/`notifier` cross-app collision can no longer occur. The
> exact allow-list (and why the familiar "+ `shared.contracts`" shorthand is
> wrong) is in the "3-tier architecture" section.

## Environments (dev / prod)

The repo **supports** two checkouts running simultaneously on one machine: an
always-on **prod** stack pinned to `main`, and a **dev** checkout where code is
edited. Everything below about how an environment resolves its identity, ports
and suppressions is live, tested code.

⚠ **THE CURRENT BOX RUNS ONE CHECKOUT, AND IT IS AT
`/home/administrator/dev`.** There is no dev environment, and
**`/home/administrator/prod` DOES NOT EXIST** — a `cd` to it fails. The
replacement server (2026-08-30, after the original VPS was suspended) was stood
up as dev deliberately, so the stack could be restored with no Schwab
credentials on disk, then **promoted in place** by flipping `name` to `"prod"`
in `config/env.local.toml`. The directory name is a fossil of that. Measured
2026-08-31: `systemctl --user list-unit-files 'trading-*'` shows only
`trading-prod-*`, and nothing listens on 9500 or 9210-9215. **So the `dev`
column below describes a configuration this repo can produce, not a thing that
is running** — read it as the mechanism, and see THE DEVELOPMENT RULE for what
that costs.

Operator runbook: [`docs/dev-prod-environments.md`](../dev-prod-environments.md).
Rationale: [design](../plans/2026-08-08-dev-prod-environments-design.md).

| | prod | dev |
|---|---|---|
| Folder | `/home/administrator/dev` ⚠ (not `…/prod` — see above) | — none exists today |
| schwab-proxy | **owns** it, `:8100` | **borrows** prod's — runs no proxy unit |
| sentiment / options / portfolio / trade / market / news | 8210–8213, 8215, 8216 | 9210–9213, 9215, 9216 |
| webgui | `:8500` | `:9500` |
| webgui_live (public screens) | `:8501` | `:9501` |
| Redis (`:6379`) | **db 0** | **db 1** |
| SQLite, `logs/`, `webgui/data` | its own | its own |
| Schedulers · Claude · notifications | live | **off** |
| Units | `trading-prod.target` (10 units) | `trading-dev.target` (9 — no proxy, but it DOES get `webgui_live`) |

Prod's ports are byte-identical to the pre-environment numbers, so prod is a
relocation, not a reconfiguration. Dev borrows prod's proxy because the Schwab
OAuth **refresh token is a single rotating credential** — two proxies holding it
can invalidate each other's session. **Accepted consequence: dev's on-demand
fetches need prod's proxy up.**

**Two config files decide identity.** `config/environments.toml` is **tracked** —
both profiles (`port_offset`, `proxy_port`, `redis_db`, `owns_proxy`, the four
behaviour flags). `config/env.local.toml` is **gitignored** — `name = "dev" |
"prod"` plus an optional machine-local `peer_root`. **A missing marker resolves to
`prod`**, so any checkout without one behaves exactly as this repo did before
environments existed, and because it is gitignored **`git pull` can never carry an
identity between checkouts**. Template: `config/env.local.example.toml`. ⚠ a
Windows `peer_root` must be a TOML **literal** string (`'D:\WebGUI Trading Prod'`)
— in a basic string `\W` is an invalid escape that discards the WHOLE document,
`name` included, and the checkout silently resolves to prod.

**Resolution lives in `repo_paths.py`** (not a new module — it already parses
`ports.toml` and is imported by ~40 files, so a module in front of it would be an
import-order hazard). It exports `ENV_NAME` / `ENV_FLAGS` / `IS_DEV` /
`OWNS_PROXY` / `REDIS_DB` / `PEER_ROOT`, and `SERVICE_PORTS` / `NICEGUI_PORT` /
`PROXY_PORT` / `MEMURAI_URL` become profile-derived — so every existing consumer
(services, `deploy/systemd/generate_units.py`, `tools/promote.sh`, the Status
page) follows the environment with no edit of its own. The unit generator is the
newest consumer and the clearest illustration: it emits ports, paths and even
WHETHER A PROXY UNIT EXISTS straight from these values, so a unit file cannot
disagree with the checkout it runs. `[services]` in
`ports.toml` is offset automatically; a **top-level** port is not, which is
correct for a process this repo does not start and a bug for one it does.

**The three suppressions, and where each is enforced** — each reuses a degrade path
the code already has, so a suppressed dev cannot take a code path prod never takes:

| Flag | Enforced in | Effect |
|---|---|---|
| `allow_notifications` | `shared/notify/channels.py:load_config` | recursively zeroes **every** `enabled` key, LAST so it also overrides the `NOTIFY_ENABLED`/`X_ENABLED` env escapes — kills Telegram, Discord, Fi-SMS, **X** (and each of its `kinds`) and the sentiment state-transition alert in one stroke. `options_svc/push_notify.load_config` delegates here, so this is the single chokepoint |
| `allow_claude` | the client factory in `options_svc/compute.py` returns `None` | falls into the existing *no-API-key* path: the briefing renders its explanatory page (market_svc makes no Claude call since 2026-09-16 — its summary quotes the published market report) |
| `schedulers` | `services/_scaffold.py:_schedulers_enabled` (consumed by `make_app`) | all six services stop collecting and polling; **command handlers still run**, so the UI stays fully usable off the snapshot |

**Per-category push switches live in `config/notify.toml` (2026-09-28)** and are
enforced ONLY at `channels.discord_target` / `telegram_target` — a new push category
must resolve its destination through those two, or Settings → General's checkbox for
it will do nothing. They sit below the three gates above (master `enabled`, dev
zeroing), and a malformed value reads as ON. The trade idea's Google Calendar event
(`shared/notify/gcal.py`) relies on the calendar's OWN default notification: an
API-written reminder belongs to the service account, so it would never pop up.

**X has ONE posting path (2026-09-22).** Every post — market reports, hourly
trade ideas, the `/x` page's ad-hoc posts — goes through
`shared/notify/x_post.post`, called only from `options_svc` (market_svc enqueues
`x_post_report` rather than posting). Nothing else imports tweepy or calls the X
API; `test_push_notify.py::test_nothing_but_x_post_talks_to_x` pins it. That one
function owns the gates (`x.enabled`, per-`kinds`, `dry_run`, credentials,
`daily_cap`) and the log (`cache:options:x_log`, `x_posts.jsonl`), so a new
source cannot forget one. ⚠ A post whose create call fails without a confirmation
is recorded `unknown` and COUNTED — it may be live, and reposting is the worse
failure. ⚠ `x_post` and `x_post_report` are replay-guarded: a fresh consumer
group must not re-post a stream's history in public. ⚠ **Two threads call it** —
the scheduler's trade idea (a pool worker) and the command consumer — and the
daily cap is read-then-written, so a module `RLock` serialises every post (network
call included) and every log write. It covers ONE process; a second posting
process would need a Redis-side lock.

**Escape hatch:** `set TRADING_ENABLE_SCHEDULERS=1` before launching turns
schedulers on for that session — the one dev case that genuinely needs collection
(testing the collectors themselves). It makes dev issue **real Schwab calls** on
top of prod's ~84k/day (measured 2026-10-02); the other three suppressions stay on.

**Under pytest the process PRESENTS AS PROD** regardless of the marker — ports,
Redis DB, `owns_proxy` **and `ENV_NAME` itself** — with all four suppressions
forced ON. Tests are hermetic (the bus is already fakeredis), so this keeps the
existing suites passing unchanged inside a dev checkout while guaranteeing no test
can reach Anthropic or a notification channel. **Consequence: dev's own `IS_DEV`
branches are only ever exercised by monkeypatch** — patch a flag with
`monkeypatch.setitem(repo_paths.ENV_FLAGS, …)`, but patch a by-value export like
`IS_DEV`/`OWNS_PROXY` with `monkeypatch.setattr` **on the module that consumed
it**. Verifying that dev really withholds the proxy restart button is a
**manual check with the app running**.

**Cross-environment safety rails.** Most of these are now structural rather
than defensive, which is the real gain from systemd:

* **A dev target has no proxy unit at all** when `owns_proxy` is false, so there
  is nothing to stop, restart or accidentally start. The old rail was
  `stop_all.py` *filtering* the proxy out of a kill list — a rule someone had to
  remember to apply.
* **`PartOf=` scopes a stop to one environment's units**, where `stop_all.py` had
  to match processes by this checkout's root path to avoid reaching the other
  stack's.
* The Status page still renders the proxy card read-only as *"shared — owned by
  prod"*, and the **Redis card is read-only in BOTH environments** now: it is a
  system unit `systemctl --user` cannot reach, and one server serves both.
* Dev's webgui still carries a `DEV` chip in the header lockup and a `DEV ·`
  tab-title prefix.

**THE DEVELOPMENT RULE (mandatory).** Work you are given lands in **dev**, is
**verified running in dev**, and only then moves to prod — via `tools/promote.sh`
and nothing else. Never `git pull`, `merge`, `checkout` or `reset` in the prod
checkout: that skips the dirty-tree refusal, the stop, the conditional dependency
reinstall and the restart, all of which exist because prod is a live trading
stack pinned to `main`.

The order is: commit in a worktree → fast-forward `main` → **run it and confirm
the change actually works** → then promote. "Tests pass" is not "verified" for
anything with a runtime surface; the DEV chip, the Status-page restart gating and
the launcher guards were all green in tests and wrong in practice.

⚠ **With no dev environment, the middle step has nowhere to run — say so rather
than skipping it silently.** `/home/administrator/dev` is the LIVE stack, so
checking a feature branch out there is the exact disaster this rule exists to
prevent. The guard hook knows that path (it matches `/home/administrator/dev` on
a path-component boundary, in an `ssh` command too, and names the promote command
in its refusal), but it stops git verbs, not a decision to test there. The
honest options are to stand a second checkout up as real dev (`name = "dev"`,
ports 9500/9210-9216, `owns_proxy = false`, its own units), or to accept
verifying a genuinely additive, read-only change on prod after it lands — and to
name which one you took. A change under `deploy/site` is the exception that needs
neither: Caddy serves that tree as static files, so serving the directory locally
IS what production does.

**The promote itself, verbatim:**

```bash
ssh vps2 'cd /home/administrator/dev && tools/promote.sh'
```

⚠ **Nothing goes after it.** `promote.sh` already runs
`deploy.systemd.generate_units --install` *and* a `daemon-reload`, so a change to
a unit, a timer or a `[slots.*]` time needs no second command. It fast-forwards
to `origin/main`, so **push to `main` first**: with nothing new there it says
"nothing to promote" and exits WITHOUT stopping the stack (`--restart` cycles it
anyway). It guards on `ENV_NAME`, not the folder name, so a directory called
`dev` is no obstacle.

⚠ **Everything that can fail runs BEFORE the stop, and a failure after it rolls
back.** The fetch, the fast-forward check and (when `requirements.lock` moves) a
`pip install --dry-run` of the new lock all happen with prod still up; after the
stop there is no network step left. If the install, the unit start or any
process's health probe then fails — it probes the proxy, all six services and
the web GUI — an EXIT trap puts the previous commit back and restarts on it.
`tools/promote.sh --rollback` does the same on request, to the commit recorded
in `logs/promote_previous_commit`. Until 2026-10-03 it stopped prod first and
had no way back. `tools/tests/test_promote_script.py` runs the REAL script under
bash against a sandbox repository, failures included — change the script and
that suite together.

**Enforced mechanically**, because knowing the rule was not enough: the whole
environment split was built in a session that then bypassed `promote.bat` on
every commit, since `git pull` in prod is one keystroke shorter.
`.claude/hooks/guard_prod_promote.py` (PreToolUse on `Bash|PowerShell`, wired in
`.claude/settings.json`) blocks a mutating git verb whose target is the prod
checkout — by explicit leading `cd`, or by the Bash tool's persistent cwd, so a
bare `git pull` is caught too. Read-only git in prod stays open (inspecting prod
is how you decide to promote), as does everything in dev and worktrees. The `cd`
match is **anchored at the start of the command**: an unanchored version also
fired on commands that merely *wrote* the prod path into a file, which it did
within a minute of going live.

**The launcher guards are GONE, with the launchers.** From 2026-08-09 to the
Linux migration, `start_all*.bat` carried a `repo_paths.IS_DEV` refusal and a
`check_stack_down.py` already-running probe, because a launcher that starts the
*wrong* stack — or a *second* copy of the right one — looks like success until
you read the ports. Both failures are now structurally impossible rather than
guarded:

- **Wrong stack.** A unit's `ExecStart` and `WorkingDirectory` are generated from
  that checkout's own `repo_paths`, so a dev unit cannot start prod's code. There
  is no shared launcher to point at the wrong tree.
- **Second copy.** systemd will not start a unit that is already active. The
  `check_stack_down.py` probe existed because a batch file happily starts a
  ninth process; `systemctl start` on a running unit is a no-op.
- **Wrong ports.** Ports come from `repo_paths` at generation time, so
  `trading-dev-*` binds 9210–9215 by construction. The `start_webgui.bat` bug —
  announcing `:8500` while binding `:9500` — has no analogue.

⚠ The batch metacharacter traps that section documented (`for /f "usebackq"`
eating quotes, `%` eaten inside a `cmd -c`) are dead with `cmd.exe` and are NOT
worth carrying forward. The shell-script equivalents that DO still bite are the
CRLF one (see `.gitattributes`) and the fact that `is-active` is not proof the
ports are free.

**Both environments DID run simultaneously, verified live 2026-08-29** — prod on
8100/8210-8215/8500, dev on 9210-9215/9500 with all four suppressions (then) enforced
(not merely configured), one shared Redis, and dev holding **no proxy of its
own** and **no Schwab credentials on disk** (only `schwab_proxy.py` reads them).
⚠ **That was the server suspended on 2026-08-30, and the arrangement did not
survive the move.** The replacement box runs prod alone; the paragraph is kept
because it is the only record that the split has actually worked end to end, and
it is what standing dev back up would be restoring. Dev was `enable`d at boot;
its generated `trading-dev-backup.timer` was deliberately **not** enabled, since
its stores are a disposable copy of prod's.
⚠ `/health` reports `up: false` with a `reason` when a service's scheduler has
stopped or its last tick is older than `[health] tick_stale_sec` (600) in
`config/services.toml`; a service whose schedulers are switched OFF (dev) is not
unhealthy. `scheduler_alive` alone still means only "restart budget not
exhausted". `dead_letters` is the count of commands the service could not run
(bounded at `[dead_letters] keep`, never re-run; `None` when it cannot be read). Read
`scheduler_uptime_s` (`null` = never started) and `scheduler_last_tick_age_s`
(time since the loop last went round, via `services/_heartbeat.py`; each
service's `scheduler.loop` must call `_heartbeat.tick()` inside its `while`, and
a test enforces it). See the runbook.

**Data flows one way.** `tools/snapshot_from_prod.py`, run **from dev**, copies
prod's SQLite stores (online-backup API — **prod keeps running**) and `DUMP`s db 0
into db 1. It hard-refuses unless `ENV_NAME == "dev"`, refuses when the two Redis
DBs resolve equal, and refuses while dev is up. It **excludes `cmd:*`** (a stream
is a queue dev would drain and EXECUTE). **Promotion is explicit:** merge to `main` and push, then run
`tools/promote.sh` in the prod checkout — which refuses unless `ENV_NAME`
resolves to `prod` (the FOLDER NAME is not the test, and today's is `dev`),
dirty-tree guard, fetch and fast-forward check *before* stopping anything, then
the fast-forward, reinstall only if `requirements.lock` moved,
`generate_units --install` + `daemon-reload`, restart, and a health probe of
every process, rolling back to the previous commit if any step after the stop
fails.

⚠ **A NEW DEPENDENCY MUST GO IN `requirements.lock`, NOT ONLY IN
`requirements.txt` — otherwise it ships to prod MISSING (2026-08-21).** Prod has
its **own venv**, and promote reinstalls *only when the lock moved*, so a package
added to `requirements.txt` alone is never installed there. Whether that is
visible depends entirely on how the importer degrades: `webgui/voice.py` guards
its `edge_tts` import and returns `None`, so the Desk's spoken alerts would have
gone live on prod **completely silent**, with one log line and nothing on screen
to say why. Caught before promoting only because prod's venv was checked
directly. **Verify with a dry-run against the prod venv before promoting** —
`/home/administrator/dev/.venv/bin/python -m pip install --dry-run -r requirements.lock`
(that checkout IS prod — see the Environments section)
should name exactly the packages you intended and nothing else. Regenerating the
whole lock with `pip freeze` is the wrong fix: the lock has drifted from the
venv before (132 entries against 135 installed, `tweepy` missing entirely), so a
wholesale refresh sweeps unrelated local state into the commit. Add the pins by
hand.

**The lock's invariant is COMPLETENESS, not tidiness — and it was violated in
both directions (audited 2026-08-29).** `requirements.lock` is what prod
installs, so the test that matters is: *every package declared in
`requirements.txt` appears in the lock, and nothing else does.*

- **Missing.** `tweepy` — then a declared runtime dep (the old X/Twitter push channel, retired 2026-09-22) —
  was absent from the lock **along with both of its own deps** (`oauthlib`,
  `requests-oauthlib`). Confirmed live: **prod's venv did not have it**. The
  import in `options_svc/push_notify.py` is lazy, and `twitter.enabled` is unset,
  so the channel silently did nothing instead of crashing — the same shape as the
  edge-tts incident, just already latent.
- **Extra.** The lock was a raw `pip freeze`, so prod also installed the dev
  toolchain: `pytest`'s siblings `ruff`/`pip_audit` plus pip_audit's whole SBOM
  tree (`cyclonedx-python-lib`, `license-expression`, `boolean.py`,
  `packageurl-python`, `py-serializable`, `pip-api`, `pip-requirements-parser`,
  `CacheControl`, `msgpack`, `defusedxml`), and `yfinance`'s orphans
  (`beautifulsoup4`, `protobuf`, `soupsieve`).

⚠ **Two entries look like dev tools and are NOT**: `autopep8` (+`pycodestyle`) is
required by **schwab-py**, and `docutils` by **nicegui**. Check `pip show <pkg>`
→ `Required-by:` before pruning anything. ⚠ `pytest` IS declared in
`requirements.txt` under "Testing" — a prod checkout is expected to run the
suite — so it stays locked; `ruff`/`pip_audit` are `requirements-dev.txt` only
and do not.

**The check is two greps, and worth running whenever the lock moves:** every
name in `requirements.txt` must appear in `requirements.lock`, and
`pip install --dry-run -r requirements.lock` against the PROD venv should install
nothing. This still stands: **do not "fix" the lock with a wholesale
`pip freeze`** — that is what swept the dev toolchain in. Add and remove pins by
hand, verifying each with `Required-by:`.

**Known limits (not defects) are listed in the runbook** — chiefly that dev is
*quiet at rest, not incapable* (command handlers are ungated, so clicking Run scan
in dev still reaches Schwab through prod's proxy).
