# Warm standby on a second provider: design

**Date:** 2026-11-22
**Status:** draft, awaiting the operator's approval (questions answered 2026-11-22)

## Why

On 2026-08-30 the original VPS was suspended by its provider and the whole stack
went dark until a replacement was built and restored by hand from a backup. The
replacement, vps2 (63.141.255.25, tailnet `trading-prod-2`), is on the same
Kansas City network (Nocix). On 2026-11-22 the operator bought a second box on a
different provider, **vps3** (RackNerd, 192.227.249.22, tailnet `trading-standby`,
ssh alias `vps3`), and hardened it the same day.

The operator's goal, in their words: "in the event of VPS2 failing I need VPS3 to
be stood up quickly", and so "the application and data should be in sync at least
once a day".

They chose a **warm standby**: every night vps3 takes the code prod is running
and last night's data and restores them into a ready, stopped checkout. If vps2
fails, the operator runs one command on vps3 and changes the addresses at Wix.
Target: the stack serving again about 15 minutes after the decision, plus DNS.

## Goals

1. Every night, vps3 holds the commit prod ran and prod's newest complete backup,
   already restored into a checkout with a current Python environment. Nothing
   runs.
2. Every nightly restore is checked (every database's integrity check passes, the
   commit is the one prod ran), so each night is a restore drill and a broken
   standby is found the morning after, not on the day it is needed.
3. A failed or skipped night reaches the operator's phone, through the stack's
   existing failure push.
4. One command on vps3, run by the operator, turns the standby into prod: it
   refuses while vps2 is still serving, installs and starts the units, and prints
   the steps only the operator can do (sudo, Wix).
5. A drill mode starts the restored stack on vps3 with nothing outward-facing
   (dev profile: no proxy of its own, schedulers, Claude and notifications off)
   and checks every process answers, without touching vps2.
6. **The roles are symmetric.** The operator decided (2026-11-22): "if VPS2 comes
   back it will then become the backup for now". So nothing names vps2 as prod
   for good. Each box knows which one it is; "prod" and "standby" are roles either
   box can hold, and every tool talks about "the peer", never a fixed host. A
   failover the other way later is the same command run on the other box.
7. **A returning old prod never starts its stack.** If the box that lost the role
   boots again (a lifted suspension, a reboot), it sees the public address points
   at its peer and starts nothing. The operator then turns it into the standby
   with one command.

## Non-goals

- **Automatic failover.** Rejected (see Approaches): a box that wrongly decides
  vps2 is dead starts a second proxy and breaks the Schwab sign-in.
- **More often than daily.** A mid-session failover loses that day's data since
  the previous trading night's backup: the minute gamma history, the day's
  paper-book changes, the day's flow alerts. The operator asked for "at least once
  a day"; more frequent copies can come later on the same path.
- **Automatic role changes.** A returning old prod stops itself, but it becomes
  the standby only when the operator says so. Moving prod back later is a planned
  failover the other way, run by the operator.
- **Restoring Redis.** The stack rebuilds every cache view by itself (runbook §11,
  step 4). A raw RDB would also bring back the `cmd:*` streams, and a stream read
  by a fresh consumer group replays its whole history: paper trades, X posts, a
  live order. Not worth the risk for short-lived state.

## Anti-goals (a failure even if it "works")

- Two boxes holding a running proxy at the same time.
- The standby sending a notification, posting to X, booking a paper trade or
  placing an order while it is a standby.
- A standby that quietly stopped syncing weeks ago.
- A nightly sync overwriting a standby that has already been promoted, with older
  data from a vps2 that came back.
- vps2 able to change anything on vps3. A compromised prod must not be able to
  damage the standby.

## Constraints

- **One proxy.** The Schwab refresh token is one rotating credential. The standby
  holds a copy (it is in the backup) but must never run the proxy unless vps2 is
  down.
- **No passwordless sudo on either box.** Anything needing root is done once by
  the operator, or printed by the failover command for the operator to run.
- **vps2's public ssh rate-limits bursts.** Pull over the tailnet
  (`trading-prod-2`); the public address is the fallback.
- **Backups exist only on weeknights** (`trading-prod-backup.timer`,
  Mon-Fri 20:00 CT). A generation is complete only when it carries `BACKUP_OK`.
  One generation is about 4.2 GB as a plain folder in `~/backups/` on vps2.
- **The repo is public on GitHub**, so vps3 can fetch code without a credential.
- **DNS is at Wix** (`ns10/ns11.wixdns.net`). Every name (`@`, `www` by CNAME,
  `app`, `live`, `cos`) is an A record to 63.141.255.25. Only the operator can
  change them.
- **Configurable by default** (CLAUDE.md): times, paths and limits go in a config
  file with a `config_schema.py` entry.

## Approaches

| | What it is | Failover time | Trade-off |
|---|---|---|---|
| **A. Warm standby** (chosen) | Nightly pull of the backup and prod's commit, restored into a stopped, ready checkout | ~15 min + DNS | One-time sudo setup on vps3; vps3 holds live secrets every night |
| B. Cold copy | vps3 only keeps the newest backup files | 1-2 h, operator at the keyboard | Cheapest to build; the first restore onto a fresh box happens during the outage |
| C. Automatic failover | Health checks switch over by themselves | Minutes | Split brain: a false "vps2 is dead" starts a second proxy; Wix DNS changes are manual anyway |

The operator picked **A** on 2026-11-22.

## Design

The sections below are written for today's roles (vps2 prod, vps3 standby) to be
easy to follow. Every tool reads the roles from configuration, so each statement
holds with the boxes swapped.

### Roles and the fence

`config/standby.toml` (tracked) lists both boxes: name, ssh targets (tailnet
first), public IP, probes. The gitignored `config/local/standby.toml` on each box
says which box it is (`self = "vps2"`) and its role (`role = "prod"` or
`"standby"`), so a git checkout can never carry an identity. The peer is the
other entry.

**The fence** (*operator's decision; mechanism my call*). A oneshot,
`trading-prod-fence.service`, that `trading-prod.target` requires, so no unit of
the stack starts unless it passes. It refuses only on positive evidence:

- the box's role is not `prod`, or
- the public name (`app.` host) resolves to the PEER's public IP, and the box is
  not marked as taking over.

A DNS lookup that fails or times out passes: a DNS outage must never keep prod
down. The failover command marks the box as taking over, because at that moment
DNS still points at the old prod; the fence clears the mark the first time it sees
the public name resolve to itself. The fence is generated by
`generate_units.py`, so it reaches vps2 through a normal promote and travels with
the stack to whichever box is prod.

### Turning a returned old prod into the standby (`tools/become_standby.sh`)

Run by the operator on the box that lost the role. It:

1. Refuses if the public name resolves to this box (it is prod).
2. Takes one local backup of its own data first, kept as
   `predemote_<date>` and never pruned by the nightly rotation. It is the only
   copy of the day the box went down, between its last nightly backup and the
   failure; whether any of it is merged back is a separate, manual decision.
3. Stops and removes the `trading-prod-*` units and timers, sets
   `role = "standby"`, and installs the standby units and keys pointing at the
   peer. The next nightly sync replaces its data with the new prod's.

### Direction: the standby pulls, read-only

vps3 gets its own ssh key. On vps2 that key is authorized only for a read-only
rsync of `~/backups` (Ubuntu's `rrsync`, already on vps2):

```
restrict,command="/usr/bin/rrsync -ro /home/administrator/backups" ssh-ed25519 AAAA... standby-pull@vps3
```

So vps3 can read backups and nothing else on vps2. A compromised vps3 can read
prod's backups, which is no loss beyond what a standby must hold anyway: it needs
the same secrets to take over.

The one key vps2 holds for vps3 can only read the standby's status file (see
"Watching the standby"):

```
restrict,command="cat /home/administrator/standby/status.json" ssh-ed25519 AAAA... standby-watch@vps2
```

What vps2 could still send vps3 is the content of a backup. Two rules keep that
from becoming code or a silent loss (*builder-check finding; my call*):

- **Restore takes only the known data**: the paths `backup_local.py` itself
  produces (`EXTRA_FILES`, `DATA_TREES`, the `*.db` stores, and the `neighbours/`
  folders of section 6). A file anywhere else in a generation (a planted `tools/`
  script, a `.venv` file, a page module) is refused and fails the night. The
  stack's code comes only from GitHub, at the recorded commit. The two neighbours
  are the stated exception (section 6).
- **The standby keeps 3 generations**, not one mirror, and refuses a generation in
  which a main store shrank sharply against the one before (`[sync]
  max_shrink_pct`, default 50, over `paper_account.db`, `signals.db`,
  `gex_history.db`). An emptied book still passes `integrity_check`; a shrink
  check does not let it through.

### 1. One small change on vps2: the generation records the commit

`tools/backup_local.py` writes `GIT_HEAD` (the checkout's `git rev-parse HEAD`)
into each generation before `BACKUP_OK`. `tools/restore_backup.py` skips it, like
`BACKUP_OK`, so it is never restored into a checkout. This reaches prod through a
normal promote, outside the collection window.

### 2. One-time provisioning of vps3

A script, `deploy/standby/provision.sh`, run by the operator with sudo once. It is
written so a second run changes nothing. It:

- installs the packages the stack needs that the bare image lacks. The list comes
  from comparing what vps2 has installed (`python3-venv`, `redis-server`,
  `caddy`, `age`, `zstd`, `rclone`, `rsync`, and whatever the report and card
  renderers need) against vps3. Caddy is installed but **disabled**.
- sets up Redis exactly as on vps2: `requirepass` and the read-only `live` ACL
  user, using the values in the restored `.env` and `.env.live`. Redis runs on the
  standby: it is empty and holds nothing.
- clones the repo to `/home/administrator/dev`, the same path as prod, so every
  generated unit and path is identical, and creates `.venv` from
  `requirements.lock`.
- creates the pull key and prints the `authorized_keys` line for the operator to
  add on vps2, and adds vps2's status-read key to vps3.
- gets the promoted box ready to make its own backups (*builder-check finding; my
  call*): the rclone Drive config, copied once from vps2, and an age file holding
  ONLY the `# public key:` line, which is all `backup_local.age_recipient` reads.
  vps3 never holds the private key, which it does not need (it restores from
  plain generations). After a failover it writes `prod_<date>` generations to the
  same Drive folder and the workstation pull finds them under the same names; only
  one box writes there, which the open question below is about.
- installs the one standby unit pair, `standby-sync.service` + `.timer` (below),
  with `OnFailure=` to the failure notifier. It installs **no** `trading-prod-*`
  unit: with none present, nothing on the standby can be started by mistake.

### 3. The nightly sync (`tools/standby_sync.py`, on vps3)

A timer, daily at `[sync] at` (default 22:15 CT, after the 20:00 backup and the
workstation pull), `Persistent=true`, so a run missed while vps3 was down
happens at the next boot. Each run:

1. **Refuses if this box has been promoted**: if any `trading-prod-*` unit file
   exists, or `config/local/standby.toml` says `role = "prod"`, it exits non-zero
   without touching anything (the failover command also disables the timer;
   this is the second lock).
2. **Finds the newest complete generation** on vps2 (`rsync --list-only` through
   the restricted key, tailnet first, then the public address). Complete means it
   carries `BACKUP_OK` and `GIT_HEAD`.
3. **Stops quietly if that generation is already synced** (weekends, holidays).
   It records the check, so "nothing new" is distinguishable from "did not run".
4. **Copies it** with `rsync -a` into `~/standby/generations/<name>.partial`,
   using the newest kept generation as `--link-dest`/basis, so rsync sends only
   the parts that changed. It is renamed to `<name>` only after the copy, the
   path allow-list and the shrink check pass. The oldest beyond 3 is then deleted
   (~13 GB on a 140 GB disk).
5. **Moves the code** to `GIT_HEAD`: `git fetch origin`, refuse if the commit is
   not on `origin/main`, `git checkout --detach <commit>`. If `requirements.lock`
   changed, `pip install -r requirements.lock`.
6. **Restores** with `tools/restore_backup.py ~/standby/generations/<name>
   --into /home/administrator/dev --force`, with the path allow-list above. Nothing runs on the standby, so `--force` is
   safe here. The tool integrity-checks every database and exits 1 on any failure.
7. **Writes `~/standby/status.json`**: the generation, commit, start and end
   times, bytes moved, result. One line goes to the journal too.

A non-zero exit fires the existing failure push (`OnFailure=` to
`tools/notify_failure.py`, which reads the restored notification credentials).
Separately, the run fails (and so pushes) if the newest generation it can see is
older than `[sync] max_age_h` (default 80 h, which covers a weekend), so "vps2
stopped making backups" also reaches the phone.

### Watching the standby (*builder-check finding; my call*)

A switched-off, suspended or stalled vps3 sends nothing: its own failure push
needs it to be running. So prod watches it. A daily oneshot on vps2
(`trading-prod-standby-watch`, after the sync's time) reads vps3's
`status.json` through the read-only key above and pushes a Server alert when the
last successful sync is older than `[sync] max_age_h`, or vps3 does not answer.
While vps2 is alive, someone is watching the standby; when vps2 is dead, the
standby is the thing being used.

### 4. Failover (`tools/failover_to_standby.sh`, run by the operator on vps3)

1. **Refuses while vps2 still answers** (*builder-check finding; my call*). It
   probes three ways and refuses if ANY answers: the proxy's health through
   `tailscale serve` (`https://trading-prod-2.tail3050ed.ts.net:8100/health`, the
   proxy itself listens on loopback only), the app's login page at the PEER's
   public IP (`app.` host sent to that address, never looked up by name, so the
   probe hits the peer whichever way DNS points), and the peer's public ssh port.
   A broken tailnet alone therefore cannot let a failover through. On 2026-11-22
   all three answered from vps3. If the peer is half-alive and cannot be stopped,
   the operator passes `--peer-is-down` after deciding.
2. Disables `standby-sync.timer`, writes `role = "prod"` to
   `config/local/standby.toml`, and marks the box as taking over (for the fence).
3. Points `proxy_public_url` in `config/env.local.toml` at this box's tailnet
   name (or removes it).
4. Runs `deploy.systemd.generate_units --install`, starts `trading-prod.target`,
   and probes every process's `/health`, as `promote.sh` does.
5. Prints the steps only the operator can do, in order:
   - `sudo ufw allow 80,443/tcp`
   - `sudo tailscale serve --bg --https=8100 http://127.0.0.1:8100`, so the
     peer's probes and the tailnet Authorize link reach this box's proxy
   - `sudo` the Caddyfile generator's `--install`, then enable and start Caddy
   - at Wix: change the A records for `@`, `app`, `live`, `cos` to 192.227.249.22
   - sign in to Schwab again from Status → Authorize if the restored sign-in is
     past its 7 days
   - re-point the workstation tasks (backup pull, social cards pull) from `vps2`
     to `vps3`

Caddy gets its certificates once the new A records resolve. Lowering the Wix TTL
on those records ahead of time shortens the DNS part.

### 5. Drill (`tools/failover_to_standby.sh --drill`)

Starts the restored stack on vps3 under the **dev profile**: ports +1000, Redis
db 1, `owns_proxy = false`, schedulers, Claude and notifications off. It probes
every process, then stops it and removes the drill units, leaving the standby as
it was. Run it once after the build and then monthly. It proves the code and data
boot together without a second proxy and without anything leaving the box.

### 6. The two neighbours: cos-hub and the market reports

The operator decided (2026-11-22) the standby carries both. Measured on vps2 that
day:

| | `cos-hub` (`cos.neuralstrike.co`) | market reports |
|---|---|---|
| Folder | `~/cos-hub`, not a git repo, 285 MB (data 4.2 MB) | `~/neuralstrike-reports`, not a git repo, 1.5 GB (`logs/` 866 MB, kept 30 days) |
| Runs as | `cos-hub.service`, `WantedBy=default.target` | `neuralstrike-report@<slot>` + six timers (`WantedBy=timers.target`), plus a `@globex` drop-in |
| Needs | its own `.venv` (`pyproject.toml`), a live SQLite store (`data/hub.sqlite3`, WAL), Redis ACL user `cos`, secrets in `config.local.toml` and `fcm-service-account.json` | Claude Code CLI (`~/.local/bin/claude`) signed in, `~/.config/neuralstrike/claude.env`, its own `.venv`, the stack's `.venv` for its failure alert |

⚠ **Neither is backed up today.** `backup_local.py` sweeps only this checkout, so
the hub's store and the report history exist only on vps2. Carrying them fixes
that as a side effect: they also reach Drive and the workstation pull.

How they travel (*my call*):

- **In the nightly generation**, under `neighbours/cos-hub/`,
  `neighbours/neuralstrike-reports/` and `neighbours/units/`.
  `backup_local.py` takes the hub's SQLite store through the online backup API
  like every other store, copies both folders without `.venv` and without the
  reports' `logs/`, and copies their unit files and drop-ins from
  `~/.config/systemd/user` and `~/.config/neuralstrike/claude.env`. The list of
  neighbours and their exclusions goes in `config/standby.toml`, so a third one
  is a config change. About +0.7 GB a generation, mostly report history that
  rsync sends once.
- **Restored on the standby** to the same paths (`~/cos-hub`,
  `~/neuralstrike-reports`), each folder's `.venv` rebuilt when its
  `pyproject.toml` (or lock) changes. Their unit files are kept in
  `~/standby/units/` and NOT installed: the standby runs nothing.
- **At failover**, the command installs those unit files, adds the fence to them
  (below) and starts the hub and the report timers.
- **Fenced too.** Neither is part of `trading-prod.target`, so a returning old
  prod would start them at boot regardless of the stack's fence: a second hub, and
  two boxes writing the same reports. `become_standby.sh`, the failover command and
  provisioning install a drop-in on `cos-hub.service` and
  `neuralstrike-report@.service` with `Requires=` + `After=` on
  `trading-prod-fence.service`. vps2 gets those drop-ins when it is set up as a
  peer.

Two consequences of this choice, stated plainly:

- **The code of these two comes from the backup, not from GitHub**, because
  neither is a git repo. So for them, "vps2 cannot plant code on vps3" does not
  hold: a compromised vps2 could change what the hub or the reports run after a
  failover. Putting each in its own private repository later closes that; the
  sync would then fetch their code the way it fetches the stack's.
- **Claude Code's sign-in is not copied.** Provisioning installs the CLI, and the
  failover command prints "sign Claude Code in" (`claude` → `/login`). A missed
  sign-in costs one report, which then fails and pushes an alert as it does today.
  Copying the login would leave a second live session on a box that runs nothing.

Provisioning adds the `cos` Redis ACL user, with the password from the hub's
restored `config.local.toml`.

### Configuration

`config/standby.toml` (tracked, with a `config_schema.py` entry), read by the sync
and the failover command:

```toml
[sync]
at = "22:15"            # CT, daily
max_age_h = 80          # newest generation older than this: fail and push
max_shrink_pct = 50     # refuse a generation whose main stores shrank more
keep = 3                # generations kept on the standby
generations = "~/standby/generations"

[boxes.vps2]
ssh = ["trading-prod-2", "63.141.255.25"]   # tried in order
public_ip = "63.141.255.25"
proxy_health = "https://trading-prod-2.tail3050ed.ts.net:8100/health"

[boxes.vps3]
ssh = ["trading-standby", "192.227.249.22"]
public_ip = "192.227.249.22"
proxy_health = "https://trading-standby.tail3050ed.ts.net:8100/health"
```

Every probe of "the peer" is built from its `[boxes.<peer>]` entry.

The box's role (`role = "standby" | "prod"`) is machine-local and lives in the
gitignored `config/local/standby.toml`, so a git checkout can never carry it.

### Tests

- `backup_local` writes `GIT_HEAD`; `restore_backup` never restores it.
- The sync: refuses on a promoted box; picks the newest generation with both
  markers; is a no-op on an already-synced generation; fails on an over-age newest
  generation; refuses a commit not on `origin/main`; does not swap the mirror when
  the copy or the restore fails. rsync and git calls are faked; nothing touches the
  network (the root `conftest.py` guards).
- The failover command's refusal while vps2 answers, tested with a fake health
  endpoint (`allow_network`) and a partner test where it is down.
- A new test folder needs a row in CI (`tests/test_ci_covers_every_suite.py`).

## Left to the builder

- The exact package list, from comparing `dpkg -l` on both boxes.
- Whether the report and card renderers (headless Chrome, fonts) belong in
  provisioning: they do if any unit in the stack needs them.
- The Wix TTL floor: the operator checks what Wix allows on A records.
- Whether Schwab's registered callback depends on the host. The in-app sign-in
  (2026-10-11) suggests not; confirm before relying on it.

## Decided by the operator (2026-11-22)

- Warm standby (approach A).
- A returning old prod becomes the standby: the roles are symmetric, and it
  starts nothing until told.
- The standby carries `cos-hub` and the market reports.
