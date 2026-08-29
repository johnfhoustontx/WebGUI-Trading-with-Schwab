# Moving prod to Linux (2026-08-29)

Relocate the always-on **prod** stack from `D:\WebGUI Trading Prod` (Windows 11)
to a headless Ubuntu Server host, and replace the bespoke process-supervision
layer with `systemd`.

**This is not a port of the application.** The application is already portable —
measured, not assumed (see [The audit](#the-audit)). What is Windows-bound is the
*supervision* layer: twelve `.bat` files, `tools/stop_all.py`'s WMI process
parsing, `tools/watchdog.py`, and the two webgui pages that shell out to them.
The design deletes that layer rather than translating it, because every one of
those files exists to work around something Windows lacks and `systemd` provides
natively.

**It is also not a performance project.** Measured on 2026-08-29, the whole stack
idles at **~6.5% of one core**, Memurai answers in **0.155 ms**, and a full
1-minute GEX write is **~14 ms** of SQLite work. The bottleneck is Schwab's
network plus the proxy's own deliberate 0.2 s call spacing, and an OS change
moves neither. What this buys is **determinism**: an end to silent bind failures,
CRLF batch-parsing traps, and `taskkill` regexes that report success while
matching nothing.

## The organising idea

> The stack is eight Python processes, a Redis, and some SQLite files. Everything
> that made that hard on Windows was scaffolding. Delete the scaffolding.

Every mechanism below is a one-for-one replacement of something that already
exists, so the migration adds no new concepts — it removes them. Measured:
**1,768 lines** of launcher and supervision code (the twelve `.bat` files plus
`stop_all.py`, `watchdog.py`, `check_stack_up.py`, `check_stack_down.py`) and the
**974 lines** of tests that pin them, replaced by nine unit files of roughly
twenty lines each. `promote.bat` is rewritten rather than deleted; everything else
in that count goes.

## The audit

Probed across the whole tree on 2026-08-29. The point of the table is that the
Windows coupling is **bounded and enumerable**, not diffuse.

| Probe | Result |
|---|---|
| `os.name` / `sys.platform` / `platform.system()` branches | **0** |
| Real `winotify` / `winsound` imports | **0** — `winotify` is a declared-but-dead pin in `requirements.txt` + `.lock` |
| `repo_paths.py` | 100% `pathlib`, `REPO_ROOT`-relative — no `D:\` anywhere |
| Notification channels | `requests` + `smtplib` only — Telegram / Discord / SMS / X all portable |
| `tkinter` in the prod path | **0** — only `tools/nq_hud.py`, which no launcher starts |
| `ZoneInfo` users | 78 files |
| Naive `datetime.now()` / `date.today()` in non-test code | **95** — see [the timezone trap](#the-timezone-trap) |

The Windows-bound surface, in full:

| Component | What binds it |
|---|---|
| 12 `.bat` launchers | `cmd.exe`, Windows Terminal, `pythonw.exe` |
| `tools/stop_all.py` | `Get-CimInstance Win32_Process` CSV + `taskkill /F /T` |
| `tools/watchdog.py` | restarts via `tools/restart_one.bat` |
| `tools/check_stack_up.py` / `check_stack_down.py` | port probes around the batch launchers |
| `webgui/pages/status.py` | `cmd /c restart_one.bat`, `Restart-Service Memurai`, `CREATE_NO_WINDOW` |
| `webgui/pages/terminate.py` | spawns `stop_all.bat` detached |
| Memurai | the licensed Windows Redis port |
| `.gitattributes` CRLF rule + `test_batch_line_endings.py` | batch files only |

Nine test files pin that layer: `test_stop_all.py`, `test_batch_call_paths.py`,
`test_batch_line_endings.py`, `test_check_stack_down.py`, `test_check_stack_up.py`,
`test_replay_manual.py`, `tests/test_launcher_ports.py`, `webgui/tests/test_status.py`,
`webgui/tests/test_terminate.py`.

## What is NOT a problem

Three things that look like blockers and are not. Each was checked, not assumed.

**Schwab OAuth on a headless box.** This is the thing that usually kills a
headless migration, and `schwab_proxy.py` already solved it: it serves its own
`/auth` page and an `/auth/callback` **form that accepts a pasted redirect URL**
(`schwab_proxy.py:552`, `:601`). Do the Schwab login in a browser on any machine,
let `https://127.0.0.1:8182` fail to connect, copy the URL out of the address bar,
paste it into the proxy's form. No display is needed on the server, and the weekly
7-day refresh-token renewal is unaffected.

**Data migration.** SQLite files are byte-identical across architectures. The
1.52 GB `gex_history.db` and the ~19 smaller stores copy as-is. Redis moves via an
RDB file copy or `DUMP`/`RESTORE`.

**Memurai to Redis.** A strict upgrade: unlicensed, better maintained, and
`shared/bus/client.py` already supports `MEMURAI_PASSWORD`, so `requirepass` can
finally be turned on. CLAUDE.md notes the unauthenticated bus is what makes a
replayed `cmd:options` entry a real risk.

⚠ **Gitignored artifacts do not travel with git**, and Linux filesystems are
**case-sensitive**. `Top 20.xlsx`, `swing_model.json`, `shared/appsettings.json`,
`shared/tokens.json`, `shared/notifications.json` and every `*/data/` store must be
hand-carried **with exact case**. On Windows a case-mismatched filename resolved
anyway; here it will not.

## Host sizing

Derived from measurements taken on the live stack, not from rules of thumb.

| Measured | Value |
|---|---|
| Whole stack CPU, off-hours, dev+prod both up (~15 procs) | ~6.5% of one core |
| Largest process RSS | 373 MB; most 60–160 MB |
| Schwab calls/day (`api_call_counts.db`) | 67,861 – 74,604 |
| Chain payload, 7-day window | mean 1.99 MB; `$NDX` 4.16, `$SPX` 3.48, NVDA 0.48 |
| Single-stream fetch rate | `$NDX` 4.16 MB in 2.09 s ≈ 2 MB/s |
| Collection universe | 28 symbols × 440 min/day |
| `gex_history.db` | 1.52 GB, 1.78M rows, ~850 B/row |

| Spec | Recommendation | Why |
|---|---|---|
| **Cores** | **4 min, 8 ideal** | The GIL caps any one service at one core. `poll_once` overlaps only the *fetch* — engine calc, zlib and SQLite insert run **serially** for all 28 symbols inside the 60 s budget, so `options_svc` saturates at ~1 core and cannot use more. Concurrent peak ≈ 2.5 cores. The headroom is for `pytest -n auto`, not the stack. |
| **RAM** | **8 GB min, 16 GB recommended** | Processes ≈ 1.2 GB, Redis ≈ 200 MB. The rest is **page cache** for the 1.52 GB `gex_history.db`, whose random reads feed the gamma page's history decode. |
| **Network** | **100 Mbps floor, 250–500 ideal** | Sustained need is ~0.5 MB/s (~4 Mbps). The floor is set by burst: 6 fetch workers × ~2 MB/s ≈ 96 Mbps. Gigabit changes nothing — Schwab's server is the limit. |
| **Disk** | **256 GB NVMe, ext4** | DB grows ~50–80 MB/day. NVMe for *latency* on history decode and `VACUUM`, not throughput. |

⚠ **Two sizing traps.** (1) The CPU figure is an off-hours idle measurement;
peak during RTH collection is an extrapolation, not a measurement. (2) Total
inbound is **~13–16 GB/day ≈ 400–500 GB/month** — at or over the transfer cap of
many budget VPS plans. Check the allowance before choosing a provider.

**Latency and stability matter more than bandwidth.** A dropped chain fetch does
not retry into the next slot; that minute is lost permanently. CLAUDE.md records a
period where ~37% of 1-minute slots were silently lost to a different cause and
went unnoticed for weeks. Prefer wired ethernet, unconditionally.

## OS and interpreter

**Ubuntu Server 24.04 LTS**, minimal install, no desktop. Standard support to
April 2029. 22.04 ships Python 3.10 — below the project's 3.11+ floor — and is out.
26.04 LTS is viable but buys only a longer runway and a newer kernel, neither of
which this workload uses; 24.04's extra two years of hardening is worth more.

**Do not use the system Python.** It is owned by apt and marked
externally-managed (PEP 668). More importantly: the lock was resolved against
**3.11.9**, and this migration should not also be a Python-version change. Install
3.11 alongside:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.11
uv venv --python 3.11 .venv
uv pip install -r requirements.lock
```

Moving to 3.12 later becomes its own separate, revertible change.

**Filesystem: ext4.** Not btrfs or ZFS — copy-on-write has well-known write
amplification against SQLite in WAL mode, and this writes into a 1.5 GB file every
minute.

## The timezone trap

**95 naive `datetime.now()` / `date.today()` calls mean Central time.** CLAUDE.md
states this as a project-wide invariant and 78 files depend on it. Today it holds
because the Windows host is set to CT.

- **Bare metal / VM:** `sudo timedatectl set-timezone America/Chicago`. Done.
- **Docker:** containers default to **UTC**. Every session window, roll-date and
  expiry calculation silently shifts 5–6 hours. This is the exact bug class that
  already cost an hour of phantom option value on three screens — except it would
  hit all 95 sites at once, and every one degrades to a *plausible number*.

**Recommendation: do not containerize.** Single-user, single-host, SQLite with
WAL, a 1.5 GB DB and a shared Redis — containers add a TZ hazard and bind-mount
friction for no benefit. Every unit below also sets `TZ=America/Chicago`
explicitly, as belt-and-braces against a host whose zone is wrong.

Add a startup assertion to `repo_paths.py` that refuses to boot if the resolved
local zone is not CT. A stack that will not start is recoverable; one that
silently reads the wrong clock is not.

## Why `systemd --user`, not system units

The System Status page's per-component **Restart** button must keep working. With
system units that needs root — a polkit rule or a sudoers entry, both of which
grant a network-facing app the ability to restart services. With **user units**
under `systemctl --user`, the webgui restarts its own siblings with **no
privilege escalation at all**.

```bash
sudo loginctl enable-linger $USER
```

That makes user units start at boot and survive logout. Units live in
`~/.config/systemd/user/`.

⚠ **Do not order user units against system units.** `After=redis-server.service`
across the user/system boundary is unreliable. Redis readiness is handled by
`Restart=on-failure` + `RestartSec` instead — a service that starts before Redis
fails fast and is retried. This is the correct systemd idiom, and it matches how
the services already degrade when the bus is down.

## Unit naming

Units are named **`trading-{ENV_NAME}-{name}`**, where `{name}` is verbatim the
`spec["name"]` that `status.restart_spec` already produces — `proxy`,
`sentiment_svc`, `options_svc`, `portfolio_svc`, `trade_svc`, `driver_svc`,
`market_svc`, `webgui`.

That verbatim reuse is deliberate. It makes `restart_command` a single f-string
with **no translation table**, so there is no second list of component names that
can drift out of step with the first — the same reasoning that put the symbol
universe in `config/symbols.toml`.

Both environments are explicit (`trading-prod-…`, `trading-dev-…`) rather than
prod being the unprefixed default, so the two can never be confused at a glance
in `systemctl --user list-units`.

## The units

**They are GENERATED, and there are no `.service` files in git.**
`deploy/systemd/generate_units.py` derives every value from `repo_paths` — ports,
the checkout root, the environment name, and whether this environment owns a
proxy. A committed unit file would be a second copy of all of that, free to drift
from the first, and the drift would surface only as a Restart button that errors
in prod. Same argument that put the ports in one config file.

It also makes dev and prod one code path rather than eighteen near-identical
files, and it removed a defect the hand-written version had: the design first
hardcoded `/home/john/prod`, and the account turned out to be `administrator`.

```bash
.venv/bin/python -m deploy.systemd.generate_units --install
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now trading-prod.target
```

### What it emits

```ini
[Unit]
Description=NeuralStrike prod - options_svc (:8211)
PartOf=trading-prod.target
After=trading-prod-proxy.service
Requires=trading-prod-proxy.service
# A crash-looping unit is retried this many times in this window, then
# left down and logged. Replaces tools/watchdog.py's storm cap.
# NOTE: these belong in [Unit]; systemd moved them there in v229
# and silently ignores them in [Service].
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory=/home/administrator/prod
Environment=PYTHONUNBUFFERED=1
Environment=TZ=America/Chicago
EnvironmentFile=/home/administrator/prod/.env
ExecStartPre=/home/administrator/prod/.venv/bin/python tools/wait_http.py --port 8100 --timeout 120 --label 'the proxy'
ExecStart=/home/administrator/prod/.venv/bin/python services/options_svc/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=trading-prod.target
```

The proxy unit drops `After=`/`Requires=`/`ExecStartPre=`; the webgui keeps
`After=` but **not** `Requires=` and **no** readiness wait, because it renders a
proxy-down banner and is fully usable without one — `restart_spec` already
encodes that as `wait_port: 0`. The target `Wants=` every emitted service and is
`WantedBy=default.target`.

### Ownership is encoded in which units EXIST

`components()` emits a proxy unit **only when `OWNS_PROXY`**. A dev checkout
borrows prod's — the Schwab OAuth refresh token is a single rotating credential,
so there can be only one holder — so dev's target simply has no proxy to pull up.
That is better than the batch version's kill-list filter, which was a rule
someone had to remember to apply. Verified on the VPS: the dev checkout emitted
eight units and no proxy.

⚠ **Validate with `systemd-analyze --user verify`, not with the Python tests.**
The unit tests pin naming, dependency shape, the restart policy and the secrets
rule; only systemd can say whether systemd will accept the file. Both shapes
(dev's 8 units, prod's 9) verify clean.

## What each unit directive replaces

| Today | Unit directive |
|---|---|
| `wait_and_run.bat` port-waiting | `After=` + `ExecStartPre=wait_http.py` |
| `start_all_wt.bat` tab ordering | `Requires=` / `After=` |
| `start_all_hidden.bat` self-relaunch to hide the console | nothing — services have no console |
| `pythonw.exe` + `Start-Process -WindowStyle Hidden` | nothing |
| `tools/watchdog.py` storm-capped restarts | `Restart=on-failure` + `StartLimitBurst` |
| `stop_all.py` WMI parsing to find *this checkout's* PIDs | `PartOf=` — systemd owns the PIDs |
| `check_stack_up.py` / `check_stack_down.py` | `systemctl --user is-active` |
| `restart_one.bat` + `CREATE_NO_WINDOW` | `systemctl --user restart` |
| `logs\<name>.out.log` shell redirection | `journalctl --user -u <unit>` |

## The three Python touchpoints

Everything else in the application is unchanged.

**1. `webgui/pages/status.py` — `restart_command`.** The whole function collapses:

```python
UNIT_PREFIX = f"trading-{ENV_NAME}-"

def restart_command(spec):
    """argv to restart a component's systemd user unit (``None`` -> ``None``)."""
    if not spec:
        return None
    return ["systemctl", "--user", "restart", f"{UNIT_PREFIX}{spec['name']}"]
```

`_NO_WINDOW` and the `creationflags` argument go. The Redis card loses its restart
button entirely — Redis is a *system* unit, one server serves both environments,
and `restart_spec` already returns `None` for it in dev for exactly that reason.
Making it read-only everywhere is the honest simplification, not a regression.

**2. `webgui/pages/terminate.py`.**

```python
["systemctl", "--user", "--no-block", "stop", f"trading-{ENV_NAME}.target"]
```

`--no-block` registers the stop job and returns immediately. This is strictly
*safer* than today's detached-`cmd`-start trick: the job lives in the systemd
manager, not in a child process, so the webgui being killed partway through cannot
orphan the shutdown.

**3. `tools/stop_all.py` — deleted.** Along with `watchdog.py`,
`check_stack_up.py`, `check_stack_down.py`, `wait_and_run.bat`, `restart_one.bat`
and all twelve `.bat` files. `tools/wait_http.py` **stays** — it becomes the
`ExecStartPre` probe, and it already encodes the hard-won lesson that a readiness
check must be an HTTP GET rather than a TCP connect.

`tools/promote.bat` becomes `tools/promote.sh` with the same four guards in the
same order: dev-checkout refusal, dirty-tree refusal *before* stopping anything,
`git pull --ff-only`, conditional reinstall only when `requirements.lock` moved,
then `systemctl --user restart trading-prod.target`.

## Test rework

The nine coupled files are rewritten against `systemctl` argv instead of `.bat`
argv. `test_batch_line_endings.py` and the `.gitattributes` CRLF rule are
**deleted** — they exist solely because `cmd.exe` mis-parses an LF-terminated
batch file under codepage 65001.

`test_stop_all.py` is deleted with its subject. Its replacement is smaller and
better: a test that every unit named in `trading-{env}.target`'s `Wants=` exists
on disk, and that the set of unit names equals the set of `spec["name"]` values
`restart_spec` can produce. That closes the drift the old WMI tests could not see.

⚠ Per the standing rule: **compare the failing set, not the count.** This change
deletes tests, so the totals will fall — that is the healthy signal, and it is the
one case where a dropping count is good news.

## Security: the one genuinely new exposure

`webgui/main.py:2349` binds **`127.0.0.1`**, and the app has **no authentication
of any kind**. On a desktop you sit at, that is correct. On a server you reach
remotely it is the whole problem, because that UI can open paper positions, apply
rescue adjustments, arm the autonomous driver and stop the stack.

**Keep the bind at `127.0.0.1`.** Reach it by SSH tunnel or Tailscale:

```bash
ssh -L 8500:127.0.0.1:8500 john@trading-host
```

If a browser without a tunnel is wanted, put Caddy in front with TLS and
authentication — do not flip the bind to `0.0.0.0`.

Turn on Redis `requirepass` while setting it up; `shared/bus/client.py` already
reads `MEMURAI_PASSWORD`.

Configure `unattended-upgrades` for security updates but **disable automatic
reboots**, or pin the window well outside market hours. The default will happily
restart a service mid-session on a live trading stack.

## Cutover

Parallel-run before switching. Windows prod stays authoritative while the Linux
host runs as a second consumer for **one full week** — that is where timezone,
case-sensitivity and file-permission surprises surface without risking the trading
record.

⚠ **Two proxies must not hold the Schwab refresh token simultaneously.** It is a
single rotating credential; two holders invalidate each other. During the parallel
run the Linux box runs **no proxy of its own** and borrows the Windows one — which
is exactly the relationship `config/environments.toml` already models with
`owns_proxy = false`. Give the Linux checkout a `name = "dev"` marker for the
parallel week, then flip it to `prod` at cutover.

⚠ **On a VPS that borrow needs a private network and a code change.** The
existing relationship works only because dev and prod share a machine —
`repo_paths.py:305` hardcodes `PROXY_URL = f"http://127.0.0.1:{PROXY_PORT}"`.
Across the internet it needs **Tailscale** (never a forwarded port: the proxy is
unauthenticated and would be reachable by anyone) plus a `proxy_host` knob in
`environments.toml`, overridable machine-locally from `env.local.toml` exactly as
`peer_root` already is. `SERVICE_URLS`, `NICEGUI_URL` and `MEMURAI_URL` stay on
`127.0.0.1` — each host runs its own services and Redis, and generalising all four
is the mistake this change invites.

⚠ **Both stacks collecting through one proxy contend for its 0.2 s rate limiter**,
and prod is what suffers. The shadow week therefore runs with schedulers **off**
(validating systemd, timezone, paths, permissions and rendering off a snapshot),
and live collection is validated in a **single supervised hour** with prod's slot
completeness watched throughout.

## Backups

The E:-drive robocopy routine does not survive this move, and nothing replaces it
automatically. **This is a hard requirement, not a follow-up.**
`paper_account.db`, `paper_account_driver.db` and `signals.db` are the trading
record; `gex_history.db` is 1.52 GB of history that cannot be re-fetched at any
price.

Three layers, because the VPS provider losing the instance must not be an
extinction event:

1. **Nightly local dump** — `sqlite3 .backup` (the online API, so the stack keeps
   running) plus a Redis `DUMP`, on a systemd user timer after
   `[windows.collection] stop`, keeping the newest 3.
   `tools/snapshot_from_prod.py` already does exactly this and is the model.
2. **Pulled to home over Tailscale.** A backup that lives only on the VPS is not
   a backup.
3. **Provider snapshots** as the whole-disk layer.

⚠ Never `cp` a live SQLite file — use the online backup API. And **test a
restore**: an untested backup is a hypothesis.

## The HUD is dropped, but only its rendering

**Decided 2026-08-29: the Tk HUD is being redesigned separately, so it does not
constrain this migration** and the Windows box can be archived outright.

⚠ **Do not delete `tools/nq_hud.py` wholesale.** Measured, the file is not the
monolith its `tkinter` import suggests — the Tk surface is `class Hud`
(line 600) and `main()` (line 1063) and nothing else. Everything above line 600
is pure and tested:

| Piece | Lines | Nature |
|---|---|---|
| `nq_hud.py` 1–599 — `read_tape`, `tape_usable`, `read_gamma`/`read_gamma_all`, `pick_source_symbol`, `BasisSmoother`, `build_pane` | ~415 | **pure** |
| `nq_hud.py` `class Hud` + `main()` | ~500 | **Tk — the only part that cannot run headless** |
| `nq_signal.py`, `nq_state.py`, `nq_signal_log.py`, `nq_instruments.py` | 1,097 | pure — `nq_signal`'s docstring states the contract: "every function takes scalars/dicts and returns scalars/dicts" |
| `test_nq_*.py` + `test_basis_smoother.py` | 2,078 | pin the pure half; `test_nq_pane.py` and `test_nq_tape.py` import `nq_hud` directly |

So **~1,512 lines of tested pure logic** survive and **~500 lines of Tk** go. The
HUD was already built to the house pattern — `build_pane` returns a display model
and `class Hud` merely paints it — which is exactly the seam a webgui page would
mount against. Discarding the file wholesale would throw away the half the
redesign wants, along with 377 lines of tests that already pin it.

**None of it is even Tk-coupled at import.** `nq_hud.py` imports `tkinter`
**lazily** (line 721) and `customtkinter` inside `class Hud`, and
`test_nq_hud_import.py` exists precisely to pin that discipline — its docstring
requires the module be importable "with no customtkinter, no tkinter, no
gamma_tool, no gex_history_db and no redis pulled in at module scope." Measured
2026-08-29: the eight HUD suites run **614 passed in 1.15 s**. They need no
display, no Tk libraries, and no change to run on the VPS.

⚠ **`customtkinter` becomes a dead pin, but not yet.** It is declared in both
`requirements.txt` and `requirements.lock` and is imported only by `class Hud`,
so it joins `winotify` as removable — *after* the redesign retires that class,
not during this migration. The distinction matters: `winotify` is imported
nowhere today, `customtkinter` still is.

Retiring the Tk layer is the redesign's business, not this migration's. This
migration only needs the HUD to stop being a *reason to keep a Windows desktop*,
and that is now settled.

## Both environments move to the one VPS

**Decided 2026-08-29.** Two checkouts, `/home/administrator/prod` and `/home/administrator/dev`,
under **one Linux user**, on one host — which is exactly today's shape (two
checkouts, one Windows login, one Redis, db 0 vs db 1). The relocation is
therefore a relocation, not a reconfiguration, for the second time.

**This simplifies more than it complicates.** Three things the design worried
about stop being problems:

| Concern | Resolution |
|---|---|
| dev borrowing prod's proxy across the internet | **gone** — they share a host again, so `owns_proxy = false` works at `127.0.0.1:8100` exactly as it does today |
| `tools/snapshot_from_prod.py` | **works unchanged** — it finds prod via `PEER_ROOT` and needs filesystem read access to prod's stores; same user makes that free |
| Unit naming collisions | **already handled** — `trading-{ENV_NAME}-{name}` yields `trading-prod-options_svc` and `trading-dev-options_svc` |

⚠ **`proxy_host` (plan Task 9) is still built, but its purpose narrows.** With
both environments co-located it is unused in steady state; it exists for the
**Phase-4 parallel run**, where the VPS must borrow the *Windows* box's proxy over
Tailscale. Keep it — it also gives a laptop checkout the same capability — but do
not let its presence imply the steady state needs it.

**One user, not two.** Two Linux users would give stronger isolation, but the port
offsets and the Redis db split already provide it, and two users would break
`snapshot_from_prod.py`'s cross-checkout reads without deliberate permission
grants. Isolation here is by *naming and configuration*, which is what it has
always been.

⚠ **Sizing already accounts for this.** The measured "~6.5% of one core" was taken
with **both** stacks running (~15 processes), so the 4–8 core / 16 GB
recommendation covers sixteen processes, not eight. Dev also runs with schedulers,
Claude, notifications and autonomous trading all off, so it is by far the lighter
of the two.

**Backups (Phase 6) cover prod only.** Dev's stores are a disposable snapshot of
prod by construction — that is what `snapshot_from_prod.py` is for.

### The consequence to confirm: development moves to the VPS

If dev lives on the VPS then **code is edited there**, over SSH. Worktrees, the
`.claude/` hooks and the scratchpad all move with it, and the Windows box is left
with nothing running on it — which is what lets plan Task 23 archive it outright.

⚠ **`.claude/hooks/guard_prod_promote.py` goes INERT in the move, silently.** It
identifies the prod checkout by the case-insensitive path fragment
`"webgui trading prod"` (line 35), chosen deliberately over an absolute path so a
drive-letter change could not defeat it. `/home/administrator/prod` does not contain that
fragment, so **every mutating git verb in prod would sail straight through** — and
the hook exists precisely because knowing the rule was not enough. Its guidance
text also hardcodes `cd "D:\WebGUI Trading Prod" && tools\promote.bat`. Both must
be updated in the same commit that creates the dev checkout, not afterwards.

## Out of scope

Containerization (argued against above), any change to service internals, the
Redis schema, the SQLite schemas, or the webgui itself beyond the two touchpoints.
The application is not being modified — only the thing that starts and stops it.
