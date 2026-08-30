# Dev and prod environments — operator runbook

How to stand the two environments up, run them, snapshot data between them, and
promote code. Reference only — for *why* it is shaped this way, see
[the design](plans/2026-08-08-dev-prod-environments-design.md).

Every port, path and command below was checked against the **running VPS** on
2026-08-29, the day dev was stood up beside prod on it. Commands are bash on the
Linux host — the PowerShell spellings that used to be here died with the Windows
box, along with the twelve `.bat` launchers.

---

## 1. Which folder is which

| | prod | dev |
|---|---|---|
| Folder | `/home/administrator/prod` | `/home/administrator/dev` |
| Git | pinned to `main` | feature branches |
| schwab-proxy | **owns** it, `:8100` | **borrows** prod's — starts none |
| sentiment / options / portfolio / trade / driver / market | 8210–8215 | 9210–9215 |
| webgui | `:8500` | `:9500` |
| Redis (one server, `:6379`) | **db 0** | **db 1** |
| SQLite, `logs/`, `webgui/data` | its own | its own |
| Schedulers · Claude · notifications · autonomous driver | live | **off** |
| Units | `trading-prod.target` — **9** units | `trading-dev.target` — **8** (no proxy) |
| Start / stop | `systemctl --user start trading-prod.target` | `systemctl --user start trading-dev.target` |
| Nightly backup timer | **enabled** | **not enabled** — its data is a disposable snapshot of prod |
| Secrets in `.env` | `MEMURAI_PASSWORD` | `MEMURAI_PASSWORD` only |
| Schwab credentials on disk | `appsettings.json`, `tokens.json`, `proxy_tokens.json` | **none** — only `schwab-proxy` reads them, and dev runs no proxy |

Prod's numbers are byte-identical to what this repo used before environments
existed, so prod is a relocation, not a reconfiguration.

### The two config files that decide it

**`config/environments.toml` — tracked.** The two profiles: `port_offset`
(prod 0, dev 1000), `proxy_port` (dev pins 8100), `redis_db`, `owns_proxy`, and
the four behaviour flags.

**`config/env.local.toml` — gitignored.** Which profile *this checkout* is:

```toml
name = "dev"                          # "dev" | "prod"
peer_root = '/home/administrator/prod'  # optional; the snapshot tool's SOURCE
```

Rules worth knowing:

- **A missing file resolves to `prod`.** A checkout without a marker behaves
  exactly as the repo did before environments existed.
- Because it is gitignored, **`git pull` can never carry an identity between
  checkouts**, in either direction.
- **A parse error is silent in the direction that hurts.** A marker that exists
  but will not parse warns on stderr and then falls back to **prod** — so a
  broken dev marker gives you a second prod. On a POSIX path the old
  single-quote rule no longer bites (`'...'` is kept only because it costs
  nothing), but the failure MODE is unchanged: if dev is behaving like prod,
  suspect the marker before anything else, and settle it with the check below
  rather than by reading the file.
- A **git worktree has no marker** — it is gitignored, so it cannot travel —
  and therefore resolves to **prod** and binds `:8500`. On the VPS that is the
  port the live stack is on. Drop a `name = "dev"` marker in the worktree
  before previewing from one, or do not preview from one.

Check what a checkout thinks it is — do this **before** generating units, since
`generate_units.py --install` writes units named for whatever it resolves:

```bash
cd /home/administrator/dev && .venv/bin/python -c "import repo_paths as r; print(r.ENV_NAME, r.SERVICE_PORTS, r.NICEGUI_PORT, r.PROXY_URL, r.REDIS_DB)"
```

Expected in dev: `dev {…9210-9215} 9500 http://127.0.0.1:8100 1`.

Template: `config/env.local.example.toml`.

---

## 2. Standing a checkout up from scratch

Both environments already exist. This is the procedure that built dev on
2026-08-29, kept because it is what you would follow to rebuild either one —
after a provider loss, or on a replacement host.

⚠ **Order matters, and step 3 is the dangerous one.** Until the marker is
written, a fresh checkout resolves to **prod** — so `generate_units.py --install`
run before it would emit `trading-prod-*` units pointing at the NEW checkout and
overwrite the live stack's units. Write the marker, verify it, then generate.

**1. Clone, and pin line endings.**

```bash
git clone https://github.com/johnfhoustontx/WebGUI-Trading-with-Schwab.git /home/administrator/dev
cd /home/administrator/dev && git config core.autocrlf false && git checkout main
```

**2. Its own venv** — never shared, so a dependency bump in one environment
cannot move the other.

```bash
uv venv --python 3.11 .venv && uv pip install -r requirements.lock
```

Verify it is complete rather than assuming: `.venv/bin/python -m pip install
--dry-run -r requirements.lock` must offer to install **nothing**.

**3. The marker**, `config/env.local.toml` — then run the identity check from §1
and confirm it says `dev` before going further.

```toml
name = "dev"
peer_root = '/home/administrator/prod'
```

No `proxy_host`: dev borrows prod's proxy at `127.0.0.1:8100`, which co-location
makes free.

**4. `.env`, mode 600.** The units read it via `EnvironmentFile=` with no
leading dash, so a missing file fails the unit **loudly** — deliberately, since
the alternative is a stack that comes up mute. Copy the one line dev needs
without ever printing it:

```bash
umask 077 && grep '^MEMURAI_PASSWORD=' /home/administrator/prod/.env > /home/administrator/dev/.env
```

⚠ **`ANTHROPIC_API_KEY` is deliberately absent from dev's file.** `allow_claude`
is already false in the profile; leaving the key out is the second belt.

**5. Carry the gitignored artifacts.** Most arrive with the snapshot in §4 —
including `Top 20.xlsx` and the sentiment bridge — so the only hand-copy is the
one store no tool knows about:

```bash
mkdir -p trade-analyzer/data && cp -p /home/administrator/prod/trade-analyzer/data/swing_model.json trade-analyzer/data/
```

⚠ **Dev needs NO Schwab credentials.** `APPSETTINGS` and `TOKENS` are read by
`schwab-proxy/schwab_proxy.py` and nothing else, and dev runs no proxy. Copying
`tokens.json` in would put a second copy of the single rotating refresh token on
disk for no benefit — the exact hazard `owns_proxy = false` exists to avoid.

**6. Generate and install the units.** They are derived from `repo_paths`, never
committed, so this is also how you repair them after any port or path change.

```bash
.venv/bin/python -m deploy.systemd.generate_units --install && systemctl --user daemon-reload
```

Confirm the shape before starting anything: **eight** `trading-dev-*` units and
**no proxy unit** — ownership is encoded in which units exist, not in a kill-list
filter. `systemd-analyze --user verify ~/.config/systemd/user/trading-dev.target`
should print nothing at all; any output is an error.

**7. Load the data** — §4. Do this before the first start, so the services come
up on prod's snapshot rather than creating empty stores.

**8. Start it, and enable it at boot.**

```bash
systemctl --user enable --now trading-dev.target
```

⚠ **Do not enable `trading-dev-backup.timer`.** The generator emits a backup
unit for every environment, but dev's stores are a disposable copy of prod's by
construction; enabling it would encrypt and ship ~1.5 GB of duplicate data every
night. Prod's timer is the one that matters.

**9. Verify.** On `http://127.0.0.1:9500` (tunnelled — see §8): the header
carries a **DEV** chip and the tab title reads `DEV · NeuralStrike`. Then
confirm the suppressions are real, not just configured:

```bash
curl -s 127.0.0.1:9215/health | python3 -m json.tool | head -20
```

`has_scheduler` must be **false** on every dev service.
## 3. Daily dev loop

```bash
cd /home/administrator/dev && systemctl --user stop trading-dev.target
```

```bash
cd /home/administrator/dev && set -a && . ./.env && set +a && .venv/bin/python tools/snapshot_from_prod.py
```

```bash
systemctl --user start trading-dev.target
```

⚠ **The `. ./.env` is load-bearing.** Redis runs with `requirepass`, and the
services get the password from their unit's `EnvironmentFile=` — a shell does
not. Without it the SQLite half completes and the Redis half dies on
`AuthenticationError`, leaving dev with fresh stores and stale cache.

Then work at **http://127.0.0.1:9500**. The header carries a `DEV` chip and the
browser tab reads `DEV · NeuralStrike` — that is how you tell the two tabs apart.

`systemctl --user start trading-dev.target` brings up **eight** units (six
services + webgui, and no proxy — dev borrows prod's). Output goes to the
journal: `journalctl --user -u trading-dev-options_svc -f`.

It cannot start the wrong stack. A unit's `ExecStart` and `WorkingDirectory` are
generated from that checkout's own `repo_paths`, so `trading-dev-*` runs dev's
code on dev's ports by construction — there is no shared launcher to point at
the wrong tree, which is what the old `IS_DEV` refusal existed to catch.

Prod keeps running the whole time. You do not stop it to snapshot.

---

## 4. Snapshotting prod's data

```bash
set -a && . ./.env && set +a && .venv/bin/python tools/snapshot_from_prod.py [--dry-run] [--redis-only] [--skip-gex] [--peer PATH]
```

| Flag | Effect |
|---|---|
| `--dry-run` | report the plan and sizes; write nothing |
| `--redis-only` | skip the file/SQLite copies |
| `--skip-gex` | skip `gex_history.db` (the ~1.4 GB one) |
| `--peer PATH` | prod checkout root (else `peer_root` from `config/env.local.toml`) |

What it does: SQLite via the online-backup API (**prod keeps running and
writing**), then `FLUSHDB` on dev's db and a `DUMP`/`RESTORE` of every key from
prod's, types and TTLs preserved. Expect roughly 30–60 s and ~1.5 GB, dominated by
`gex_history.db`.

⚠ **It needs `MEMURAI_PASSWORD` in the environment** — see §3. The tool builds
its own Redis clients (`redis_connect_kwargs`), so it does not inherit the
services' `EnvironmentFile`.

Five structural guards, each of which will just refuse:

- **Wrong direction** — it only writes into the checkout it is run from, and
  refuses unless that checkout resolves to `dev`. `--peer` names the SOURCE only;
  there is no flag that makes prod a destination.
- **Same Redis db** — the copy flushes the destination first, so identical
  indices would wipe prod's cache. Prod's index is read from *prod's* profile,
  never assumed to be 0.
- **`--peer` is this same checkout, or is not a checkout** (no `repo_paths.py`).
- **Dev is still up** — dev's services hold DB handles and would write over the
  restore. It names the ports still listening. (Prod's proxy on `:8100` is
  deliberately not probed — a listener there is prod doing its job.)
- **`redis` is missing from this interpreter** — checked *before* the first byte
  is copied, not where it is used, so running under the system python fails
  cleanly instead of after ~1.5 GB has landed.

Two things it deliberately does **not** carry over:

- **`cmd:*`** — the command streams and their `cmd:*:dead` lists. A stream is a
  queue dev would drain and *execute* on startup; a stranded
  `driver_paper_create` or `rescue_apply` would double-open a position.
- **`cache:driver:control` armed** — it is rewritten to disabled on copy, so a
  snapshot taken while the driver was armed **can never arm dev's**.

---

## 5. Turning collection on in dev (rarely)

Dev's schedulers are off. To run them for one session — only when the collectors
themselves are what you are testing:

⚠ **Setting it in your shell does nothing.** That was the `.bat` answer, and it
does not survive the move to systemd: a unit's environment comes from its
`Environment=` and `EnvironmentFile=`, never from the shell that ran
`systemctl`. `_scaffold._schedulers_enabled` reads `os.environ`, so the variable
has to reach the *unit*. Put it in dev's `EnvironmentFile` for the session:

```bash
echo 'TRADING_ENABLE_SCHEDULERS=1' >> /home/administrator/dev/.env && systemctl --user restart trading-dev.target
```

Take it out the same way when you are done — this is a session escape hatch, not
a setting:

```bash
sed -i '/^TRADING_ENABLE_SCHEDULERS=/d' /home/administrator/dev/.env && systemctl --user restart trading-dev.target
```

Confirm which state you are in from the service rather than the file:
`curl -s 127.0.0.1:9215/health` reports `has_scheduler`.

(`systemctl --user set-environment` also works, but it is **manager-wide** — it
would apply to prod's units on their next restart too. Prefer the file, which is
scoped to dev by construction.)

**Why rarely:** this makes dev issue real Schwab calls, through prod's proxy, on
top of prod's own ~68–76k calls/day. Everything else stays suppressed — no Claude,
no notifications, no autonomous trading.

---

## 6. Promotion

**Work is verified running in dev first.** "Tests pass" is not "verified in dev"
for anything with a runtime surface — the DEV chip, the Status-page restart
gating and the old launcher guards were each green in tests and wrong in
practice.

In **dev**: merge to `main` and push.

```bash
cd /home/administrator/dev && git checkout main && git merge <branch> && git push
```

In **prod** — and by this route only:

```bash
cd /home/administrator/prod && tools/promote.sh
```

`promote.sh` refuses in a dev checkout, refuses on a dirty tree (*before*
stopping anything, so a refusal never leaves prod down), stops the target, waits
for both the units and their **listening sockets** to go (`is-active` clears
about a second early), `git pull --ff-only origin main`, reinstalls dependencies
**only if `requirements.lock` moved**, regenerates the units, restarts, and then
probes `:8100` and `:8500` over **HTTP** — a dead accept loop stays bound and
would pass a TCP connect. Check `/status` afterwards.

⚠ **Never `git pull`, `merge`, `checkout` or `reset` in the prod checkout.**
Every guard above is skipped, and prod is a live trading stack.
`.claude/hooks/guard_prod_promote.py` blocks the mutating verbs mechanically —
it knows both the old Windows path fragment and `/home/administrator/prod`.

If it refuses on a dirty tree, look at the diff — an unexpected edit in the prod
checkout is for a human to decide about, not a restart script.

---

## 7. Known limits

None of these are defects. Each is a way to be surprised.

**1. Dev is quiet at rest, not incapable.** Schedulers are off, so dev polls
nothing on its own. But **command handlers are not gated** — clicking *Run scan*,
*Analyze*, or loading a Calculator chain in dev still reaches Schwab through
prod's shared proxy. That is deliberate (dev needs on-demand fetches to be
usable), but "dev makes no API calls" is only true while nobody is using it.

**2. Dev needs prod's proxy running** for any on-demand fetch, because it borrows
`:8100`. Each dev service's `ExecStartPre=tools/wait_http.py` waits for it; the
services start anyway, and every fetch fails until prod is up.

**3. Restarting Redis affects both environments.** One server, two logical DBs.
The Status page hides the Redis restart button in dev for exactly this reason —
if you restart it from prod, dev goes with it.

**4. `options_svc`'s `driver_paper_create` command handler is not env-guarded.**
The producer is (`driver_svc.handlers.run_autonomous_cycle` early-returns), and
the snapshot excludes `cmd:*`, so nothing can reach it today. Worst case is a fake
trade in dev's own paper book, which prod never sees.

**5. Dev's own behaviour cannot be verified by the test suite.** Under pytest,
`repo_paths` pins identity *and* topology to prod, so every `IS_DEV=True` branch
is only ever exercised via monkeypatch. Confirming that dev really withholds the
proxy and Redis restart buttons, and shows the `DEV` chip, is a **manual check
with the app running**.

**6. Both halves of the snapshot have now run for real** (2026-08-29, standing
dev up on the VPS). The file half moved **1,542 MB across 14 stores** — the
1.52 GB `gex_history.db` included — in about 13 s, with prod live and writing
throughout. It had never run before that day: the old dev checkout *was* the
former prod, so it already held every database and never needed them copied.

⚠ **The Redis half failed on that first run**, and the failure is the useful
part: the tool builds its own clients, and Redis gained `requirepass` during the
Linux migration, so it died on `AuthenticationError` **after** the 1.5 GB had
landed — the half-applied state the deferred `import redis` check exists to
avoid, arriving through a door that check does not cover. Fixed
(`redis_connect_kwargs`, mirroring `shared/bus/client.py`: unset or empty means
no AUTH). The operational residue is in §3 — a manual run needs the password in
its environment, because only the *units* have an `EnvironmentFile`.

---

## 8. Gotchas

- **Dev's Terminate stops only dev**, structurally rather than by a filter:
  `systemctl --user --no-block stop trading-dev.target` reaches exactly the units
  `PartOf=` binds to it, and dev has **no proxy unit to begin with** when
  `owns_proxy` is false. Redis survives either way — it is a *system* unit a
  `--user` stop cannot reach even in principle, which is also why the Status
  page's Redis card is read-only in **both** environments.
- **A snapshot can never arm dev's autonomous driver** — two independent defences:
  the snapshot rewrites `cache:driver:control` disabled, and
  `run_autonomous_cycle` early-returns on the profile flag before it reads
  anything.
- **`.sh` files must be LF**, the exact inverse of the rule that used to live here
  for `.bat`. A shell script with CRLF does not mis-parse — it does not run at
  all: the kernel reads `#!/usr/bin/env bash` plus a stray CR as a request for an
  interpreter named `bash\r`, and reports `bad interpreter: ...^M`, naming
  neither the real problem nor the file. `.gitattributes` pins it and
  `tools/tests/test_shell_line_endings.py` guards it — that guard caught
  `promote.sh` the first time it ran, after an edit made on Windows.
- **A port in a unit's `Description=` is a label; `repo_paths` holds the value.**
  Both come from the same generator run, so they cannot disagree unless the units
  are stale — regenerate after any port or path change, which `promote.sh` does
  for you.
- **Reach either web GUI over an SSH tunnel.** Both bind `127.0.0.1` and have
  **no authentication of any kind** — correct for a desk-side app, and the whole
  problem on a server, since that UI opens paper positions, arms the autonomous
  driver and stops the stack. `tools/open_webgui.ps1` forwards prod's `:8500`
  **and** `:8100` (the proxy's `/auth`, needed every 7 days when the Schwab
  refresh token expires). For dev, forward `:9500` the same way. ⚠ Never change
  either bind to `0.0.0.0`.
- **The Status page's freshness table will look stale in dev**, because dev
  publishes nothing at rest. That is the snapshot ageing, not a broken service.
- **systemd owns the PIDs, so the process archaeology is over.** Two long gotchas
  lived here: batch metacharacter traps (`for /f "usebackq"` eating quotes around
  a path with spaces, `%` eaten inside a `cmd -c`), and the `pythonw.exe` re-exec
  that made every service appear as a PARENT/CHILD PAIR — so a port check
  reported the "wrong" interpreter and a duplicate-launch check had to tell pairs
  apart from real duplicates.

  Both died with `cmd.exe` and `pythonw`. `systemctl --user status <unit>` reports
  one MainPID, `PartOf=` scopes a stop to one environment's units, and
  `systemctl start` on an already-running unit is a no-op rather than a ninth
  process.

  ⚠ One survives in a new form: **`is-active` going inactive is NOT proof the
  ports are free.** Measured 2026-08-29 — the target reports inactive about a
  second before its members' listening sockets close. systemd serialises
  start-behind-stop per unit so it cannot race a `systemctl start`, but anything
  else that binds must wait for the sockets. `tools/promote.sh` waits for both.

---

## 9. Where the behaviour lives

| Concern | File |
|---|---|
| Identity + profile + derived ports | `repo_paths.py` (`ENV_NAME`, `ENV_FLAGS`, `IS_DEV`, `OWNS_PROXY`, `REDIS_DB`, `PEER_ROOT`) |
| Profiles | `config/environments.toml` |
| Marker | `config/env.local.toml` (gitignored), template `config/env.local.example.toml` |
| Notification gate | `shared/notify/channels.py:load_config` |
| Claude gate | `options_svc/compute.py`, `market_svc/compute.py`, `driver_svc/decider.py` — the three client factories |
| Scheduler gate | `services/_scaffold.py:_schedulers_enabled` / `make_app` |
| Autonomous gate | `driver_svc/handlers.py:run_autonomous_cycle` |
| Cross-env kill safety | `PartOf=` in the generated units — plus the absence of a dev proxy unit |
| Restart-button safety | `webgui/pages/status.py` |
| Dev chip / tab title | `webgui/main.py` |
| Unit generation | `deploy/systemd/generate_units.py` (nothing under `deploy/systemd/` is committed as a `.service`) |
| Start / stop | `systemctl --user start\|stop trading-{dev,prod}.target`; the GUI's More → Stop All Services runs the same target stop |
| Promotion | `tools/promote.sh` + `.claude/hooks/guard_prod_promote.py` |
| Snapshot | `tools/snapshot_from_prod.py` |
| Backups | `tools/backup_local.py`, `trading-prod-backup.timer`, `tools/pull_backups.ps1` |
| Logs | `journalctl --user -u trading-{env}-{svc}`; webgui also writes `logs/webgui.log` |
