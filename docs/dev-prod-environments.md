# Dev and prod environments — operator runbook

How to stand the two environments up, run them, snapshot data between them, and
promote code. Reference only — for *why* it is shaped this way, see
[the design](plans/2026-08-08-dev-prod-environments-design.md).

Every port, path and command below was checked against the repo on 2026-08-08.

---

## 1. Which folder is which

| | prod | dev |
|---|---|---|
| Folder | `D:\WebGUI Trading Prod` | `D:\WebGUI Trading with Schwab` |
| Git | pinned to `main` | feature branches |
| schwab-proxy | **owns** it, `:8100` | **borrows** prod's — starts none |
| sentiment / options / portfolio / trade / driver / market | 8210–8215 | 9210–9215 |
| webgui | `:8500` | `:9500` |
| Redis (Memurai `:6379`) | **db 0** | **db 1** |
| SQLite, `logs\`, `webgui\data` | its own | its own |
| Schedulers · Claude · notifications · autonomous driver | live | **off** |
| Launcher | `start_all_wt.bat` (or `start_all_hidden.bat`) | `start_dev.bat` |

Prod's numbers are byte-identical to what this repo used before environments
existed, so prod is a relocation, not a reconfiguration.

### The two config files that decide it

**`config/environments.toml` — tracked.** The two profiles: `port_offset`
(prod 0, dev 1000), `proxy_port` (dev pins 8100), `redis_db`, `owns_proxy`, and
the four behaviour flags.

**`config/env.local.toml` — gitignored.** Which profile *this checkout* is:

```toml
name = "dev"                          # "dev" | "prod"
peer_root = 'D:\WebGUI Trading Prod'  # optional; SINGLE quotes — see below
```

Rules worth knowing:

- **A missing file resolves to `prod`.** A checkout without a marker behaves
  exactly as the repo did before environments existed.
- Because it is gitignored, **`git pull` can never carry an identity between
  checkouts**, in either direction.
- **`peer_root` must be single-quoted.** `"D:\WebGUI Trading Prod"` is an invalid
  `\W` escape in a TOML *basic* string, and it discards the **whole document** —
  `name` included — so the checkout silently resolves to prod. A literal string
  (`'...'`) takes the path verbatim. Forward slashes in a double-quoted string
  also work.
- Save it **UTF-8**. PowerShell's `>` and `Out-File` write UTF-8 *with BOM*; the
  loader strips a BOM, but other readers may not.
- A marker that exists but will not parse **warns on stderr** (captured to
  `logs\<name>.err.log`) and then falls back to prod. If dev is behaving like
  prod, read that log first.

Check what a checkout thinks it is:

```powershell
.venv\Scripts\python -c "import repo_paths as r; print(r.ENV_NAME, r.SERVICE_PORTS, r.NICEGUI_PORT, r.PROXY_PORT, r.MEMURAI_URL)"
```

Template: `config/env.local.example.toml`.

---

## 2. One-time cutover

Do this once, with the market closed. Order matters.

> **Why the order matters: until step 5 writes dev's marker, BOTH checkouts
> resolve to `prod`.** A missing marker means prod, and prod's own marker says
> prod — so for the whole middle of this checklist there are two prod
> checkouts on one machine, both wanting `:8100`, `:8210`-`:8215`, `:8500` and
> Redis db 0.
>
> Starting the new stack before the old one is stopped therefore collides on
> every port. The failure is nastier than a clean refusal: whichever process
> binds first wins, the rest die into log files, and the survivors are a mix of
> the two checkouts' code — with a proxy that answers on `:8100` while being the
> one you didn't mean to start. `/status` will look plausible.
>
> So: **stop the old stack (step 4) before starting prod (step 6)**, and don't
> start dev until its marker exists. The overlap ends the moment step 5 writes
> `name = "dev"`, which moves that checkout to 9210-9215 / `:9500` / db 1.

**1. Clone prod and pin it to `main`.**

```powershell
git clone "D:\WebGUI Trading with Schwab" "D:\WebGUI Trading Prod"
cd "D:\WebGUI Trading Prod"
git checkout main
```

**2. Build prod's venv.**

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
```

**3. Copy the gitignored secrets** from the dev checkout into prod, same relative
paths. None of these are in git, so a clone has none of them:

| File | Holds |
|---|---|
| `shared\appsettings.json` | Schwab API keys |
| `shared\tokens.json` | Schwab OAuth tokens |
| `shared\notifications.json` | Telegram / Discord / Fi-SMS / X creds |
| `shared\anthropic_key.txt` | Anthropic API key |
| `shared\driver_model.txt` | driver model override |
| `schwab-proxy\proxy_tokens.json` | the proxy's own token store |
| `options-scanner\data\Top 20.xlsx` | scanner watchlist |

The workbook is not optional in practice: **without it the scanner degrades to
base symbols and the Net Prem view's SPDR-sector group is empty.**

**4. Stop the current stack, then copy the live data into prod once.**

```powershell
cd "D:\WebGUI Trading with Schwab"
stop_all.bat
```

Then copy, preserving relative paths (nothing is running, so a plain file copy is
safe here — the online-backup path in §4 exists for later, when prod *is*
running):

- `options-scanner\gex_history.db` (~1.4 GB)
- `options-scanner\data\` — `trades.db`, `signals.db`, `paper_account.db`,
  `paper_account_driver.db`, `gamma_briefings.db`, `daily_trade_log.db`
- `sentiment-dashboard\data\` — `sentiment_intraday.db`, `sector_pcr.db`,
  `momentum.db`, `market_state.db`
- `services\trade_svc\data\iv_history.db`
- `shared\sentiment_bridge.json`

(That is exactly `SQLITE_STORES` + `FILE_STORES` in `tools/snapshot_from_prod.py`.)
After this, dev keeps its existing copy as its first snapshot.

**5. Write the markers.**

`D:\WebGUI Trading Prod\config\env.local.toml`:

```toml
name = "prod"
```

`D:\WebGUI Trading with Schwab\config\env.local.toml`:

```toml
name = "dev"
peer_root = 'D:\WebGUI Trading Prod'
```

**6. Start prod.**

```powershell
cd "D:\WebGUI Trading Prod"
start_all_wt.bat
```

**7. Verify.** On `http://127.0.0.1:8500`:

- **More → System Status** — overall banner green; proxy, all six services,
  Memurai and webgui up.
- The **Schwab Authorization** card says authorized. If not, click **Authorize**
  (it opens the proxy's `/auth`).
- Header shows **no** `DEV` chip; tab title has no `DEV ·` prefix.

**8. Repoint the desktop shortcut** at `D:\WebGUI Trading Prod\start_all_hidden.bat`.

---

## 3. Daily dev loop

```powershell
cd "D:\WebGUI Trading with Schwab"
stop_all.bat
.venv\Scripts\python tools\snapshot_from_prod.py
start_dev.bat
```

Then work at **http://127.0.0.1:9500**. The header carries a `DEV` chip and the
browser tab reads `DEV · NeuralStrike` — that is how you tell the two tabs apart.

`start_dev.bat` launches **seven** processes (six services + webgui, no proxy) as
tabs in one Windows Terminal window. `start_dev.bat nowindow` runs them hidden
with output to `logs\<name>.out.log` / `.err.log`. It **refuses to run unless the
checkout is marked dev**.

Prod keeps running the whole time. You do not stop it to snapshot.

---

## 4. Snapshotting prod's data

```powershell
.venv\Scripts\python tools\snapshot_from_prod.py [--dry-run] [--redis-only] [--skip-gex] [--peer PATH]
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

Four structural guards, each of which will just refuse:

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

```powershell
set TRADING_ENABLE_SCHEDULERS=1
start_dev.bat
```

It must be set in the shell **before** launching, and it applies to processes
started from that shell only.

**Why rarely:** this makes dev issue real Schwab calls, through prod's proxy, on
top of prod's own ~68–76k calls/day. Everything else stays suppressed — no Claude,
no notifications, no autonomous trading.

---

## 6. Promotion

In **dev**: merge to `main` and push.

```powershell
git checkout main
git merge <branch>
git push
```

In **prod**:

```powershell
cd "D:\WebGUI Trading Prod"
tools\promote.bat
```

`promote.bat` refuses in a dev checkout, refuses on a dirty tree (*before*
stopping anything, so a refusal never leaves prod down), stops the stack, waits
for `:8100` and `:8500` to actually free, `git checkout main` + `git pull
--ff-only`, reinstalls dependencies **only if `requirements.lock` moved**, then
restarts via `start_all_wt.bat nowindow`. Check `/status` afterwards.

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
`:8100`. `start_dev.bat` waits for it and says so if it is missing; the services
start anyway and every fetch fails until prod is up.

**3. Restarting Memurai affects both environments.** One server, two logical DBs.
The Status page hides the Memurai restart button in dev for exactly this reason —
if you restart it from prod, dev goes with it.

**4. The legacy `notifier.py` modules** (`options-scanner/notifier.py`,
`sentiment-dashboard/notifier.py`) sit **outside** the notification gate. They are
dead from every service path — nothing but their own tests imports them — but
running one by hand from a dev checkout would push for real.

**5. `options_svc`'s `driver_paper_create` command handler is not env-guarded.**
The producer is (`driver_svc.handlers.run_autonomous_cycle` early-returns), and
the snapshot excludes `cmd:*`, so nothing can reach it today. Worst case is a fake
trade in dev's own paper book, which prod never sees.

**6. Dev's own behaviour cannot be verified by the test suite.** Under pytest,
`repo_paths` pins identity *and* topology to prod, so every `IS_DEV=True` branch
is only ever exercised via monkeypatch. Confirming that dev really withholds the
proxy and Memurai restart buttons, and shows the `DEV` chip, is a **manual check
with the app running**.

**7. The SQLite half of the snapshot has never been run for real.** The cutover
was performed on **2026-08-09** and `--redis-only` has run successfully against
the live stack (263 of 272 keys, the nine `cmd:*` streams excluded, prod's copy
untouched, `cache:driver:control` arriving disabled). The **file** half has not:
the dev checkout *is* the former prod, so it already had every database and never
needed them copied. The first true `gex_history.db` transfer is therefore still
ahead of you — keep running `--redis-only` first and confirm prod's `/status` is
green before letting the ~1.4 GB go.

---

## 8. Gotchas

- **Dev's Terminate stops only dev.** `tools/stop_all.py` drops the proxy from its
  kill list when `owns_proxy` is false, and matches the HUD by *this checkout's
  root path*, so it cannot reach prod's. Memurai is left running either way.
- **A snapshot can never arm dev's autonomous driver** — two independent defences:
  the snapshot rewrites `cache:driver:control` disabled, and
  `run_autonomous_cycle` early-returns on the profile flag before it reads
  anything.
- **`.bat` files must be CRLF.** A `.bat` saved with LF line endings fails in ways
  that look like logic errors. Check before committing one.
- **Ports are labels in the launchers, values in `repo_paths`.** The numbers
  echoed by `start_dev.bat` are cosmetic; every process reads its own port from
  `repo_paths` (`config/ports.toml` + the profile's `port_offset`). If they
  disagree, `repo_paths` wins — and `tests/test_launcher_ports.py` should have
  caught it.
- **The Status page's freshness table will look stale in dev**, because dev
  publishes nothing at rest. That is the snapshot ageing, not a broken service.
- **A healthy stack is 16 python processes per environment, not 8.** The venv's
  `python.exe` is a redirector that re-execs the base interpreter, so every
  component is a **parent/child pair**: the parent's command line carries the
  checkout path, the child holds the port and does the work. Two environments
  running at once is therefore ~32 processes, which is normal.

  This misleads process-hunting in three specific ways, all observed while
  standing prod up:

  | You do | You see | Reality |
  |---|---|---|
  | count processes per service | 2 of each | one component, not a duplicate |
  | grep command lines for the checkout path | only ~half match | children are launched with a **relative** script path, so the checkout never appears |
  | check a port's owning process | interpreter is the **system** python, not `.venv` | it is the re-exec'd base interpreter; the venv is still what resolved the imports |

  So: match on the **script name**, not the checkout path or the interpreter,
  and expect pairs. `tools/stop_all.py` already handles this — `hud_root_pids`
  returns **roots only**, because `taskkill /T` sweeps the child. To tell a real
  duplicate from a pair, check whether one PID is the other's parent; if they
  are independent, you genuinely started twice.

  A second launch is mostly harmless — the losers fail to bind the already-held
  ports and exit within a minute or two — but they do complete their startup
  work first, so expect one duplicate round of collection (a sentiment backfill
  is ~10 Schwab calls) in the logs before they die.

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
| Cross-env kill safety | `tools/stop_all.py` |
| Restart-button safety | `webgui/pages/status.py` |
| Dev chip / tab title | `webgui/main.py` |
| Launchers | `start_dev.bat`, `start_all_wt.bat`, `tools/promote.bat` |
| Snapshot | `tools/snapshot_from_prod.py` |
