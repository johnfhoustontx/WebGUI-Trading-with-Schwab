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
| webgui_live (public screens) | `:8501` | `:9501` |
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

⚠ **QUOTE ANY VALUE CONTAINING A SPACE.** `.env` is read by **two parsers that
disagree**, and only one of them complains:

* **systemd** (`EnvironmentFile=`) is not a shell. `KEY=two words (x)` is taken
  literally and works.
* **bash**, when you `set -a && . ./.env` — which §3 and §4 tell you to do before
  running `snapshot_from_prod.py`, because that tool needs `MEMURAI_PASSWORD` and
  does not get the units' environment.

So an unquoted value with a space starts the services perfectly and breaks the
snapshot tool later, at a moment unconnected to the edit that caused it. Found
2026-08-31 adding `EDGAR_USER_AGENT=NeuralStrike/1.0 (fernandesj@gmail.com)`:
bash died on the parentheses. Write it as

```
EDGAR_USER_AGENT="NeuralStrike/1.0 (contact@example.com)"
```

systemd **strips** the surrounding quotes, so the service still sees the bare
string — verify that rather than assuming, by reading the running process's
environment:

```bash
tr '\0' '\n' < /proc/$(systemctl --user show -p MainPID --value trading-prod-trade_svc)/environ | grep EDGAR
```

Quotes still present there would mean the remote end sees them too, which for a
User-Agent is a silent wrong value rather than an error.

**What lives in `.env`, and why each is there:**

| Key | Read by | Notes |
|---|---|---|
| `MEMURAI_PASSWORD` | every service via `shared/bus`, and `backup_local.py` via `REDISCLI_AUTH` | unset or empty = no AUTH, so an unauthenticated Redis still works |
| `ALPHAVANTAGE_API_KEY` | `trade_svc` earnings calendar/history | absent = the earnings gate stays quiet, logged at INFO |
| `EDGAR_USER_AGENT` | `trade_svc` EDGAR fundamentals | **not a key** — SEC issues none. A contact string their fair-access policy asks for; without one they answer **403** |
| `ANTHROPIC_API_KEY` | the three Claude client factories | prod only; dev omits it beside `allow_claude = false` |
| `TRADING_ENABLE_SCHEDULERS` | `_scaffold` | the §5 escape hatch, and only ever temporary |

⚠ **`.env` is in the backup** (`backup_local.EXTRA_FILES`) — it was not until
2026-08-31, which meant a restore produced a stack that could not start, from an
archive that looked complete.

**4b. `.env.live`, mode 600 — the PUBLIC process's own file.** The
`webgui_live` unit loads **this** and **not** `.env`. It is the one
internet-facing, unauthenticated process in the fleet, and `.env` supplies
`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `PROXY_SHARED_SECRET`,
`SMS_SMTP_APP_PASSWORD`, `DISCORD_WEBHOOK_URL` and
`GAMMA_BRIEFING_WEBHOOK_URL` — the strongest credential set on the box, held by
the weakest-protected process. No code path in it reads any of them today; the
split is about what an RCE in NiceGUI would cost, not about a live bug.

```bash
umask 077 && cat > /home/administrator/prod/.env.live <<'EOF'
REDIS_LIVE_URL=redis://live:<the ACL user's password>@127.0.0.1:6379/0
MEMURAI_PASSWORD=<the same value as in .env>
EOF
```

| Key | Read by | Notes |
|---|---|---|
| `REDIS_LIVE_URL` | `webgui/live_main.py` and nothing else | the read-only Redis ACL user. **Unset or empty, `live_main` refuses to serve in prod** — the Bus would otherwise fall back to the stack's ordinary full read/write credential, on an origin with no login |
| `MEMURAI_PASSWORD` | `shared/bus` | only used when `REDIS_LIVE_URL` names a user **without** a password. A password inside the URL wins over the kwarg the Bus passes (verified against redis-py 8) |

⚠ **The DB index is in the URL**, so it bypasses the `redis_db` the profile
selects. Prod is `/0`, **dev is `/1`**. Copying prod's line into dev's file aims
dev's public process at prod's data, and every published screen then renders
prod's real book while the checkout looks like dev. `live_main` warns on a
mismatch — into the journal, which is not where anyone is looking.

⚠ **No leading dash on this `EnvironmentFile=` either**, so a missing file
fails the unit. That is not merely the house rule here: a dash would start the
process with no `REDIS_LIVE_URL`, which the prod refusal above kills anyway, so
it could only ever trade a systemd error naming the exact path for a Python one
naming the variable.

⚠ **`.env.live` is in the backup and in `.gitignore`** — both as their own
line, because neither `.env` entry matches this name.

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
.venv/bin/python -m deploy.systemd.generate_units --install
```

It reloads the daemon itself, and **arms every timer it wrote** (`enable --now`)
— on a prod checkout. A written `.timer` nothing enables is a file, not a
schedule, and on this box that silently cost eleven days of flow-delta reports.
In a **dev** checkout it arms nothing, deliberately: see the warning at step 8.
Read its output — a timer it could not arm is printed as `FAILED` and the
command exits non-zero.

Confirm the shape before starting anything: **nine** `trading-dev-*` units and
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

This is now ENFORCED rather than requested: `--install` arms timers only in a
prod checkout, so a dev install writes all of them and enables none. Arming one
by hand still works if you genuinely want it.

**9. Verify.** On `http://127.0.0.1:9500` (tunnelled — see §8) the tab title is
prefixed `DEV ·` and the header carries a **DEV** chip; prod's is bare. Then
confirm the four suppressions are **enforced**, not merely configured — read
them from the enforcement points rather than from the profile, since the profile
is the thing you would be checking against itself:

```bash
cd /home/administrator/dev && .venv/bin/python -c "
import sys; sys.path.insert(0,'.')
from services import _scaffold; import repo_paths as r
from shared.notify import channels
print('schedulers  :', _scaffold._schedulers_enabled())
print('claude      :', r.ENV_FLAGS['allow_claude'])
print('autonomous  :', r.ENV_FLAGS['autonomous_trading'])
print('notify cfg  :', channels.load_config())"
```

All four must be false, and every `enabled` in the notification config must be
false — `load_config` zeroes them recursively and **last**, so it overrides the
`NOTIFY_ENABLED`/`TWITTER_ENABLED` env escapes too.

⚠ **`/health` will not answer this question, and it looks as though it does.**
`scheduler_alive` is `true` on a dev service: it means "the restart budget is
not exhausted", not "a scheduler is running", and it defaults true. The field
that actually discriminates is **`scheduler_last_tick_age_s`** — `null` in dev,
because the task never started. (On prod it is the age since the scheduler last
*started*, not since its last tick, so a large value there is normal.)
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

`systemctl --user start trading-dev.target` brings up **nine** units (six
services + webgui + webgui_live, and no proxy — dev borrows prod's; the live
screens are NOT withheld the way the proxy is, because nothing about them is a
shared exclusive credential). Output goes to the
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
cd /home/administrator/dev && git checkout main && git merge --ff-only <branch> && git push
```

⚠ **The VPS can fetch but cannot push** (measured 2026-08-29): the remote is
HTTPS and the box holds no GitHub credential, so `git push` there dies with
`could not read Username for 'https://github.com'`. Anonymous read works, which
is why everything else in this runbook does. Until a deploy key or PAT is
configured, do the push from a workstation that is authenticated:

```bash
git push origin <branch>:main
```

That fast-forwards `main` to the branch commit — identical in effect to the
merge above, and the reason the merge is spelled `--ff-only`: if it would not
fast-forward, the two routes are no longer equivalent and you want to know.

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
`:8100`. **Nothing waits for it** — `generate_units.py` emits the
`ExecStartPre=tools/wait_http.py` probe only when `OWNS_PROXY` is true, so dev
services have no readiness gate at all. They start cleanly and every fetch fails
until a proxy answers. That is the right behaviour (a dev stack must come up to
read its snapshot whether or not prod is running), but it does mean a missing
proxy is invisible at startup and shows up only as failing fetches.

⚠ This entry claimed the opposite until 2026-08-30 — that each dev service waited
via `ExecStartPre`. It was inherited from the pre-systemd runbook and repeated
through the Linux rewrite without being checked against the generator. Verified
against `deploy/systemd/generate_units.py:140` and against a real dev stack
started with no proxy present at all.

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
- **Reach prod's web GUI at `https://app.neuralstrike.co`** (Caddy + a password
  and TOTP login, since 2026-09-06). `tools/open_webgui.ps1` is the **fallback**
  for when the cert or Caddy breaks, and remains the only route to the proxy's
  `:8100` `/auth` — needed every 7 days when the Schwab refresh token expires —
  which is deliberately not on the public domain. **Dev is not on the domain at
  all**; forward `:9500`, or reach it over the tailnet. ⚠ Never change either
  bind to `0.0.0.0`: the gate's wall exemption is scoped by a loopback check, so
  a widened bind turns a local carve-out into an open door.
- ⚠ **DEV NEEDS ITS OWN CREDENTIALS, or you cannot log into it.** The gate is
  registered at module scope in `main.py`, so **dev runs it too** — deliberately,
  since a dev that skipped authentication would be taking a code path prod never
  takes, which is exactly what the four suppression flags are careful not to do.
  But `auth_store.DEFAULT_PATH` is `<checkout>/shared/webgui_auth.json` and
  `shared/` is gitignored, so **prod's credentials do not travel to dev and
  `promote.sh` does not carry them**. Run `python tools/webgui_credentials.py
  set-password` and `enroll-totp` **inside the dev checkout** once. Two entries in
  your authenticator is the intended outcome; they can share a password if you
  like, but not a file. Symptom if you skip it: a normal-looking login page on
  `:9500` where every attempt fails — that is default-deny working, not a bug.
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
| Driving it from Windows | `tools/trading.bat` — `start` / `stop` / `restart [svc]` / `status` / `health` / `logs [svc]` / `tunnel`. Sends systemctl over SSH; supervises nothing |
| Promotion | `tools/promote.sh` + `.claude/hooks/guard_prod_promote.py` |
| Snapshot | `tools/snapshot_from_prod.py` |
| Backups | `tools/backup_local.py`, `trading-prod-backup.timer`, `tools/pull_backups.ps1` |
| Logs | `journalctl --user -u trading-{env}-{svc}`; webgui also writes `logs/webgui.log` |
