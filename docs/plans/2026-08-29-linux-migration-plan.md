# Moving prod to Linux (VPS) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run the prod stack on a headless Ubuntu 24.04 VPS under `systemd --user`, and retire the twelve `.bat` files and the WMI/taskkill supervision layer.

**Architecture:** Nine `systemd` user units replace the launcher matrix. Three Python touchpoints change (`status.restart_command`, `terminate.py`, and a new `proxy_host` knob in `repo_paths`). `tools/stop_all.py`, `watchdog.py` and both `check_stack_*` helpers are deleted; `tools/wait_http.py` survives as the readiness probe.

**Tech Stack:** Ubuntu Server 24.04 LTS, Python 3.11.9 via `uv`, Redis 7, systemd user units, Tailscale, pytest.

**Design doc:** [`2026-08-29-linux-migration-design.md`](2026-08-29-linux-migration-design.md) — read it before starting.

---

## What the VPS changes

**1. Dev moves to the VPS too** (decided 2026-08-29). Two checkouts under **one Linux user** — `/home/john/prod` and `/home/john/dev` — which is exactly today's shape. It *simplifies*: dev borrows prod's proxy at `127.0.0.1` again, `snapshot_from_prod.py` keeps working because it needs filesystem read access to prod's stores, and `trading-{ENV_NAME}-{name}` already makes the unit sets non-colliding. Task 23 stands it up. Consequence: **code is edited on the VPS over SSH**, so worktrees, the `.claude/` hooks and the scratchpad move with it — and Task 12 exists because one of those hooks fails silently if they do.

**2. The parallel run needs a private network and a code change.** The design says the Linux box borrows the Windows proxy (`owns_proxy = false`), because the Schwab refresh token is one rotating credential. That works today only because dev and prod share a machine — `repo_paths.py:305` hardcodes `PROXY_URL = f"http://127.0.0.1:{PROXY_PORT}"`. Across the internet it needs **Tailscale** (never a forwarded port — the proxy is unauthenticated) and a **`proxy_host` knob**. Task 9 adds it.

**3. Backups are no longer a drive you can touch.** The E:-drive robocopy routine dies with the Windows box. Phase 6 replaces it. Do not treat this as optional: `paper_account.db`, `paper_account_driver.db` and `signals.db` are the trading record, and `gex_history.db` is 1.52 GB of history that cannot be re-fetched.

---

## Conventions for every task

**This is a git worktree — the venv is at the repo root.** Test commands:

```bash
(cd "D:/WebGUI Trading with Schwab/.claude/worktrees/prod-linux-migration-596ad3/webgui" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest . -q)
```

```bash
(cd "D:/WebGUI Trading with Schwab/.claude/worktrees/prod-linux-migration-596ad3" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tools/tests tests -q)
```

Confine the `cd` to a subshell as shown. Commit after every task.

⚠ **These commands are Phase 0–1 only.** From Task 13 on the work happens on the VPS and every one of them changes — see the boundary note at the head of Phase 2.

**Baselines** (2026-08-20/21): webgui **2320 green**, `tools/tests` **816**, `tests` **69**. Compare the failing *set*, never the count — and note Phase 1 deletes tests, so totals will fall. That is the intended direction.

⚠ **The Phase-1 code changes must NOT be promoted to Windows prod.** They replace `.bat` supervision with `systemctl`, which Windows cannot run. They live on `claude/prod-linux-migration-596ad3`; the VPS clones **that branch**. Windows prod stays on `main`, fully working, until Task 25 decommissions it. Merge to `main` only at Task 21 (cutover), after the VPS is authoritative.

---

# Phase 0 — Prove the VPS can do the job before buying a year of it

## Task 1: Verify Schwab reachability from a datacenter IP

**The cheapest possible kill-switch.** If Schwab blocks or throttles datacenter ranges, the entire plan dies and it should die here, on an hourly instance, not after a migration.

**Step 1: Rent the smallest instance the provider offers, hourly billing, in a US region.**

**Step 2: From that box, hit the Schwab OAuth and market-data hosts.**

```bash
curl -sS -o /dev/null -w "auth   %{http_code}  %{time_total}s\n" https://api.schwabapi.com/v1/oauth/authorize
```

```bash
curl -sS -o /dev/null -w "market %{http_code}  %{time_total}s\n" https://api.schwabapi.com/marketdata/v1/quotes
```

Expected: HTTP status (401/400 is fine — it means the host answered), **not** a timeout, TLS failure, or a block page. Note `time_total`; anything under ~150 ms to a US-East region is healthy.

**Step 3: Record the latency, then destroy the instance.** Do not proceed until this passes.

## Task 2: Choose the instance

Apply the measured sizing from the design doc. **The transfer cap is the spec most likely to bite**, and the one nobody checks.

| Spec | Requirement |
|---|---|
| vCPU | **4 minimum, 8 preferred** (the extra is for `pytest -n auto`, not the stack) |
| RAM | **8 GB minimum, 16 GB preferred** (page cache for the 1.52 GB `gex_history.db`) |
| Disk | **256 GB NVMe** — 140 GB is workable (the DB grows ~50–80 MB/day) but tight once two checkouts, two venvs, nightly backups and the journal share it |
| **Monthly transfer** | **≥ 1 TB.** Measured need is ~13–16 GB/day ≈ **400–500 GB/month**. A 500 GB cap is a hard fail. |
| Region | US, low latency to `api.schwabapi.com` (Task 1) |
| Billing | Monthly, not annual, until after Task 22 |

⚠ Confirm the cap covers **inbound**. Some providers meter egress only (in which case you are fine), others meter both. Read the plan page, do not assume.

## Task 3: Provision and harden

**Step 1: Install Ubuntu Server 24.04 LTS**, minimal, no desktop.

⚠ **Inject your SSH key at build time** if the provider's panel offers the field. The box then comes up key-only and password auth is never enabled at all.

⚠ **Verify the instance has no prior tenancy before any credential lands on it.** A resold or recycled VPS can arrive carrying another party's login history. The tells: a `Last login:` date predating your purchase, a non-standard admin account (Ubuntu images create `ubuntu`, not `administrator`), and an idle process count well above ~130.

```bash
last -20; sudo lastlog | grep -v 'Never logged in'; awk -F: '$3>=1000 && $3<65534' /etc/passwd; sudo ss -tlnp
```

This machine holds Schwab OAuth tokens for a live brokerage account. Rebuilding from a clean image costs twenty minutes and is never cheaper than before the migration starts. (Hit on 2026-08-29: the first instance showed a July 2024 login from an unrelated foreign IP and was rebuilt.)

**Step 2: SSH keys only.** ⚠ **Editing `/etc/ssh/sshd_config` does NOT work on a cloud-init image, and fails silently.** That file carries `Include /etc/ssh/sshd_config.d/*.conf` near the top (line 12 on Ubuntu 24.04), and **OpenSSH uses the FIRST value it obtains** for each keyword — so `/etc/ssh/sshd_config.d/50-cloud-init.conf` with `PasswordAuthentication yes` beats a `no` written further down the main file. The edit applies, the file reads correctly, and password auth stays on. (Hit 2026-08-29; the same readout also showed `PermitRootLogin yes`.)

Write a file that sorts **before** cloud-init's, so it wins on first-match and survives cloud-init rewriting its own file on reboot:

```bash
sudo tee /etc/ssh/sshd_config.d/00-hardening.conf >/dev/null <<'EOF'
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
EOF
sudo chmod 600 /etc/ssh/sshd_config.d/00-hardening.conf && sudo sshd -t && sudo systemctl reload ssh
```

**Verify what is RUNNING, never what is written** — `sudo sshd -T | grep -iE 'passwordauthentication|permitrootlogin'`. Then open a new session before closing the current one, and confirm password auth is actually refused: a rejected attempt should report `Permission denied (publickey)`, not `(publickey,password)`.

**Step 3: Timezone — the load-bearing one.**

```bash
sudo timedatectl set-timezone America/Chicago && timedatectl
```

Expected: `Time zone: America/Chicago (CDT, -0500)` and `System clock synchronized: yes`. 95 naive `datetime.now()` calls depend on this.

**Step 4: Tailscale.** Needed for webgui access, for the Task 9 proxy borrow, and for Phase 6 backups.

```bash
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
```

**Step 5: Firewall — default deny, and keep the door you came in through.**

⚠ **Order matters and the obvious version locks you out.** This box is reached over its **public IP**, not Tailscale, so allowing only `tailscale0` before enabling `ufw` drops your own session and every future one. Allow SSH *first*, enable *after*:

```bash
sudo ufw default deny incoming && sudo ufw allow 22/tcp && sudo ufw allow in on tailscale0 && sudo ufw --force enable
```

Verify with `sudo ufw status verbose` **and open a second SSH session before closing the first** — that is the cheap insurance against a rule that looks right and is not.

Once Tailscale is up and you have confirmed you can reach the box by its Tailscale name, you may drop the public rule with `sudo ufw delete allow 22/tcp` — but only then, and only if you are content that Tailscale is your sole route in.

⚠ A public SSH port takes continuous brute-force traffic. With `PasswordAuthentication no` (Step 2) that is noise rather than risk, but `sudo apt install -y fail2ban` is cheap and stops it filling the journal.

**Step 6: Swap.** VPS images often ship with none; a 4 GB file prevents an OOM-kill during a `VACUUM` or a parallel test run.

```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
```

Persist it in `/etc/fstab`.

**Step 7: Disable automatic reboots** in `/etc/apt/apt.conf.d/50unattended-upgrades`:

```bash
sudo sed -i 's|^//\?Unattended-Upgrade::Automatic-Reboot .*|Unattended-Upgrade::Automatic-Reboot "false";|' /etc/apt/apt.conf.d/50unattended-upgrades
```

**Step 8: Redis, with a password.**

```bash
sudo apt install -y redis-server && sudo systemctl enable --now redis-server
```

Set `requirepass` in `/etc/redis/redis.conf`, restart, and confirm `redis-cli -a "$PW" ping` returns `PONG`.

**Step 9: `python-is-python3`.** Ubuntu ships `python3` and no `python`; all three `.claude` hooks invoke bare `python` and would fail "command not found", wedging every session.

```bash
sudo apt install -y python-is-python3
```

**Step 10: Enable lingering** so user units start at boot without a login session.

```bash
sudo loginctl enable-linger $USER
```

---

# Phase 1 — Code changes, TDD, on Windows

All of Phase 1 is developed and tested in the worktree on Windows. None of it is promoted.

## Task 4: `restart_command` emits systemctl argv

**Files:** modify `webgui/pages/status.py`, `webgui/tests/test_status.py`

**Step 1: Write the failing tests first.** Replace the `.bat`-argv assertions in `test_status.py` with:

- `restart_command` for each of the eight components returns `["systemctl", "--user", "restart", f"trading-{ENV_NAME}-{name}"]`
- the unit name is `spec["name"]` **verbatim** — assert `options_svc`, not `options`
- `restart_command(None)` returns `None`
- the Redis/Memurai card returns `None` in **both** environments now, not only dev
- no argv element contains `.bat`, `cmd`, or `powershell`

**Step 2: Make them pass.**

```python
UNIT_PREFIX = f"trading-{ENV_NAME}-"

def restart_command(spec):
    """argv to restart a component's systemd user unit (``None`` -> ``None``)."""
    if not spec:
        return None
    return ["systemctl", "--user", "restart", f"{UNIT_PREFIX}{spec['name']}"]
```

Delete `_NO_WINDOW` and the `creationflags=` argument in `_do_restart`. In `restart_spec`, make the `memurai` branch return `None` unconditionally — one Redis serves both environments and it is a *system* unit a user unit cannot restart.

**Step 3: Run the webgui suite.** Expect green minus the deleted assertions.

**Step 4: Commit.**

## Task 5: Terminate stops the target

**Files:** modify `webgui/pages/terminate.py`, `webgui/tests/test_terminate.py`

**Step 1: Failing test** — the argv is `["systemctl", "--user", "--no-block", "stop", f"trading-{ENV_NAME}.target"]`, and the `STOP_BAT` path constant and its existence check are gone.

**Step 2: Implement.** Update the page's copy too: "Re-launch with `start_all_wt.bat`" becomes `systemctl --user start trading-prod.target`.

**Step 3: Note in the docstring why `--no-block` is safer than the old detached-`cmd` trick** — the stop job lives in the systemd manager, not a child process, so the webgui being killed mid-call cannot orphan the shutdown.

**Step 4: Run, commit.**

## Task 6: Timezone boot assertion

**Files:** modify `repo_paths.py`, add `tests/test_timezone_guard.py`

**Step 1: Failing tests.**
- with the local zone resolving to `America/Chicago`, import succeeds
- with it resolving to UTC, a `RuntimeError` naming the expected and actual zone is raised
- under pytest the guard is **inert** (CI and the Windows dev box must not be forced to CT)

**Step 2: Implement** a `_assert_central_time()` that runs at import, skipped when `PYTEST_CURRENT_TEST` is set. Compare the current UTC offset against `ZoneInfo("America/Chicago")`'s for the same instant — that is DST-correct and does not depend on a zone *name* the OS may spell differently.

**Step 3: Run `tests`, commit.**

⚠ This is the single highest-value task in Phase 1. A stack that refuses to start is recoverable; one silently reading a UTC clock produces confident wrong numbers across all 95 naive call sites.

## Task 7: The unit files

**Files:** add `deploy/systemd/*.service`, `deploy/systemd/trading-prod.target`, `deploy/systemd/README.md`; modify `.gitignore`

**Step 1: Write the nine units** exactly as in the design doc, with `/home/john/prod` as `WorkingDirectory` (parameterise in the README, not in the files).

**Step 2: Gitignore the secrets file.** ⚠ **`.env` is NOT currently ignored** (checked 2026-08-29) — and it will hold `ANTHROPIC_API_KEY` and the Redis password. Add it beside the existing secret rules, which are the precedent:

```
# Per-checkout secrets read by the systemd units' EnvironmentFile=.
# Never committed, same rule as appsettings.json / env.local.toml.
.env
```

**Step 3: Commit.** The units are validated on the VPS in Task 13, not here — `systemd-analyze` does not exist on Windows.

## Task 8: The drift test

**Files:** add `tests/test_systemd_units.py`

This is the replacement for `test_stop_all.py`, and it closes drift the WMI tests could not see.

**Step 1: Write it.** Parse `deploy/systemd/trading-prod.target` for its `Wants=` entries and assert:
- every unit named there exists as a file in `deploy/systemd/`
- the set of unit basenames equals `{f"trading-prod-{n}" for n in <the eight component names>}`, derived by calling `status.restart_spec` over every card kind — **not** from a hand-written list
- every `.service` sets `TZ=America/Chicago`, `Restart=on-failure`, and a `StartLimitBurst`
- every `.service` sets `EnvironmentFile=` **without a leading `-`** — the dash would let a unit start mute when the file is missing
- **no `Environment=` line names a secret** (`KEY`, `TOKEN`, `PASSWORD`, `SECRET`, case-insensitive). `systemctl show` prints `Environment=` to any local user; this is the test that keeps one from creeping back in
- every `ExecStart` path starts with `.venv/bin/python`

**Step 2: Run, commit.**

## Task 9: The `proxy_host` knob

**Files:** modify `repo_paths.py`, `config/environments.toml`, `config/env.local.example.toml`, `tests/test_env_profile.py`

Needed for Phase 4. Also permanently useful — it lets a laptop dev checkout borrow prod's proxy.

**Step 1: Failing tests.**
- `proxy_host` defaults to `127.0.0.1` in both profiles, so existing behaviour is byte-identical
- `env.local.toml` may override it (machine-local, gitignored — exactly like `peer_root`)
- `PROXY_URL` composes from it
- ⚠ **`SERVICE_URLS`, `NICEGUI_URL` and `MEMURAI_URL` are unaffected** — each host runs its own services and Redis. Assert this explicitly; it is the mistake this change invites.
- under pytest the host is forced to `127.0.0.1`, matching how the profile already presents as prod under test

**Step 2: Implement.** Add `proxy_host` to `_ENV_DEFAULTS` and both profile tables (`tests/test_env_profile.py` guards that key sets match, so a one-sided addition fails).

**Step 3: Run `tests`, commit.**

## Task 10: Delete the supervision layer

**Files:** delete `tools/stop_all.py`, `tools/watchdog.py`, `tools/check_stack_up.py`, `tools/check_stack_down.py`, all twelve `.bat` files, `tools/tests/test_stop_all.py`, `test_batch_line_endings.py`, `test_batch_call_paths.py`, `test_check_stack_up.py`, `test_check_stack_down.py`, `test_watchdog.py`, `tests/test_launcher_ports.py`, and the `*.bat`/`*.cmd` rules in `.gitattributes`. Add `tools/promote.sh`.

**Step 1: Grep for stragglers** before deleting — `page_help.py:711` names `start_all.bat`, and several docstrings reference the batch files.

```bash
grep -rn "start_all\|stop_all\|restart_one\|wait_and_run\|check_stack" --include=*.py --include=*.md . | grep -v docs/plans
```

**Step 2: Delete, and fix every straggler** the grep found. `page_help.py` is a user-facing manual — CLAUDE.md is explicit that it rots first, so update it in this commit.

**Step 3: Write `tools/promote.sh`** with the same four guards in the same order: dev-checkout refusal, dirty-tree refusal **before** stopping anything, `git pull --ff-only`, reinstall only when `requirements.lock` moved, then `systemctl --user restart trading-prod.target`. Verify with `tools/wait_http.py`, not a TCP probe.

**Step 4: Full suite, all folders. Compare the failing SET.** Commit.

## Task 11: Drop the dead `winotify` pin

**Files:** modify `requirements.txt`, `requirements.lock`

`winotify` is declared in both and imported nowhere (audited 2026-08-29). Remove by hand — **never** regenerate the lock with `pip freeze`, per the standing rule. Confirm nothing else needs it:

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pip show winotify | grep -i "required-by"
```

Expected: an empty `Required-by:`. Commit.

⚠ **Remove `winotify` only — leave `customtkinter`.** It looks like the same dead-Windows-dep case and is not: it is still imported by `nq_hud.py`'s `class Hud`. It becomes removable when the HUD redesign retires that class, which is not this migration.

---

## Task 12: Make the `.claude` tooling survive the move

**Files:** modify `.claude/hooks/guard_prod_promote.py`, `.claude/hooks/ruff_fix.py`, `.claude/settings.json`; add `tools/tests/test_guard_prod_promote.py`

`.claude/` is **tracked** (settings, launch.json, three hooks, one hook test), so it travels to the VPS with the repo — and four things in it are Windows-bound. Audited 2026-08-29. Two fail **silently**, which is why this is a task and not a footnote.

**Every fix below is additive.** Windows must keep working for the whole migration window, including the parallel-run week — so match *both* layouts rather than replacing one with the other.

### A. `guard_prod_promote.py` goes INERT, silently

⚠ **The guard stops firing entirely.** It identifies the prod checkout by the case-insensitive path fragment `"webgui trading prod"` (line 35) — chosen deliberately over an absolute path so a drive-letter change could not defeat it. `/home/john/prod` does not contain that fragment, so every mutating git verb in prod would sail straight through. The hook exists precisely because knowing the rule was not enough.

**Step 1: Failing tests.**

- a mutating verb with a leading `cd /home/john/prod` is blocked
- the same with `cd "D:\WebGUI Trading Prod"` is **still** blocked — the Windows box stays protected for the whole migration window
- read-only git (`status`/`log`/`diff`/`rev-parse`) is not blocked in either
- a command that merely *writes* the prod path into a file is not blocked (the anchoring rule the hook already documents)
- the guidance text names a `promote.sh` invocation, not `tools\promote.bat`

**Step 2: Implement.** Make `PROD_FRAGMENT` a tuple of fragments and match any. Do **not** replace the Windows fragment — matching both is what keeps the guard live at every point in the migration, including the parallel-run week when both prod checkouts exist at once.

**Step 3: Verify the hook actually fires**, since a hook that passes its unit tests can still be mis-wired. Attempt a blocked command and confirm exit 2.

**Step 4: Run `tools/tests`.**

### B. `ruff_fix.py` silently stops auto-fixing

⚠ Line 23 builds `repo / ".venv" / "Scripts" / "python.exe"` and line 24 is `if not py.exists(): return 0`. On Linux that path does not exist, so the hook **no-ops and returns success** — ruff auto-fix quietly stops running and nothing anywhere says so. This is exactly the repo's most-documented bug class: a degrade path with no trace.

**Step 5:** Resolve the interpreter for **either** layout — `.venv/Scripts/python.exe` **or** `.venv/bin/python` — and keep the existing "not found → skip" behaviour only when *neither* exists. Test both branches with a `tmp_path` fake venv.

### C. Every pytest and ruff call would prompt

All eleven `permissions.allow` entries name `.venv/Scripts/python[.exe]` or the absolute `D:/WebGUI Trading with Schwab/...`. None matches `.venv/bin/python`, so on the VPS every test run asks for approval. Not a breakage — just constant friction that makes a session unusable.

**Step 6:** **Add** the `.venv/bin/python` equivalents. Do not remove the Windows entries.

### D. `python` is not on PATH on Ubuntu

All three hook commands invoke bare `python`. Ubuntu 24.04 ships `python3` and no `python`, so every hook would fail "command not found" — and CLAUDE.md already records how badly a failing hook wedges a session, since the hook runs *before* the command that would fix it.

**Step 7:** Fix this in **provisioning, not in `settings.json`** — `sudo apt install -y python-is-python3` on the VPS (added to Task 3). One package beats editing a tracked, currently-portable config; `${CLAUDE_PROJECT_DIR:-.}` already expands correctly under the POSIX shell hooks run in.

⚠ **`.claude/launch.json` is deliberately NOT fixed here.** Its `runtimeExecutable` is a single string (`.venv\Scripts\python.exe`) that cannot name both layouts, and it only drives the Browser-pane preview. It flips when dev moves, in Task 23.

**Step 8: Commit.**

---

# Phase 2 — Stand the stack up on the VPS

> ## ⚠ Every command changes here
>
> **Phase 1 ran on Windows. Everything from Task 13 on runs on the VPS over SSH.** The conventions block at the top of this plan is Phase-1 only — do not carry its commands across this line.
>
> | | Phase 0–1 (Windows) | Phase 2–6 (VPS) |
> |---|---|---|
> | Interpreter | `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"` | `.venv/bin/python` |
> | Checkout | `.claude/worktrees/prod-linux-migration-596ad3` | `/home/john/prod` (and later `/home/john/dev`) |
> | Suite command | `(cd <worktree>/webgui && "<abs>/python.exe" -m pytest . -q)` | `(cd /home/john/prod/webgui && ../.venv/bin/python -m pytest . -q)` |
>
> **The venv sits in a different place, and that is not cosmetic.** On Windows the worktree has no venv, which is why every documented command reaches back to the repo root by absolute path. On the VPS the venv lives **inside the checkout** (`/home/john/prod/.venv`), so a relative `.venv/bin/python` works — until you make a git worktree there, which will have no venv either and needs the absolute `/home/john/prod/.venv/bin/python` again. Same trap, new spelling.
>
> **Baselines do not transfer cleanly, and Task 15 is where you find out.** Compare against the Windows numbers (webgui **2320**, `tools/tests` **816**, `tests` **69**, minus whatever Phase 1 deleted), but expect genuine differences: anything asserting a `\` path separator, and the two known-flaky cases (`test_flow_alert_window`, the date-relative `test_expected_move`). **A failing node ID that is new is a portability bug, not noise** — triage it, do not absorb it into a new baseline. Task 15's clean run *is* the Linux baseline; record it there.
>
> **`pytest -n auto` becomes worth using** now that the box has 4–8 cores and no real-time AV filter driver in the path. Use it for full-suite runs, not for the per-task runs where the failing set matters more than wall-clock.
>
> **Set `git config core.autocrlf false`** in both VPS checkouts. Phase 1 deletes the `*.bat`/`*.cmd` CRLF rules from `.gitattributes`, and nothing on Linux should be rewriting line endings.

## Task 13: Clone, venv, units

**Step 1: Clone the branch** (not `main`) to `/home/john/prod`.

**Step 2: Python 3.11 via uv.**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.11 && uv venv --python 3.11 .venv && uv pip install -r requirements.lock
```

**Step 3: Verify the interpreter matches the lock's target.**

```bash
.venv/bin/python -V
```

Expected: `Python 3.11.9`.

**Step 4: Create the secrets file** the units read. ⚠ Do this **before** enabling anything — `EnvironmentFile=` carries no leading dash, so a unit whose file is missing fails to start. That is deliberate: the alternative is a stack that comes up mute, with Claude briefings and notifications silently doing nothing.

```bash
umask 077 && cat > /home/john/prod/.env
```

Paste `ANTHROPIC_API_KEY=...` and `MEMURAI_PASSWORD=...`, then Ctrl-D. Confirm with `stat -c '%a %U' /home/john/prod/.env` that it reports `600 john`.

**Step 5: Install and validate the units.**

```bash
cp deploy/systemd/* ~/.config/systemd/user/ && systemctl --user daemon-reload
```

```bash
systemd-analyze --user verify ~/.config/systemd/user/trading-prod-proxy.service
```

Expected: no output. Any output is an error.

## Task 14: Carry the gitignored artifacts

⚠ **Linux is case-sensitive.** On Windows a case-mismatched filename resolved anyway; here it will not.

**Step 1: Copy** `shared/appsettings.json`, `shared/tokens.json`, `shared/notifications.json`, `options-scanner/data/Top 20.xlsx`, `trade-analyzer/data/swing_model.json`, and every `*/data/*.db`. Use `sqlite3 <db> ".backup <out>"` for the SQLite files, never `cp` on a live DB.

**Step 2: Verify case exactly.**

```bash
ls -1 "options-scanner/data/Top 20.xlsx" && .venv/bin/python tools/check_env.py
```

**Step 3: Confirm the big one arrived intact.**

```bash
.venv/bin/python -c "import sqlite3;c=sqlite3.connect('options-scanner/gex_history.db');print(c.execute('PRAGMA integrity_check').fetchone(), c.execute('SELECT COUNT(*) FROM snapshots').fetchone())"
```

Expected: `('ok',)` and ~994,192 rows.

## Task 15: Run the suites on Linux

**The first real test of portability.** Run every suite per the Tests section of CLAUDE.md, one folder at a time.

Expected failures to triage, not ignore: anything asserting a `\` path separator, anything asserting Windows-only behaviour, and the two known-flaky cases (`test_flow_alert_window`, the date-relative `test_expected_move`). **Compare the failing set against the Windows baseline** — a *new* name is a real portability bug.

---

# Phase 3 — Snapshot shadow

Validates systemd, timezone, paths, permissions and rendering **without a proxy** and without touching prod. Cheap and safe; do this before Phase 4.

## Task 16: Load a snapshot and boot the stack

**Step 1:** With `name = "dev"` in `config/env.local.toml` (schedulers, Claude, notifications and autonomous trading all off), copy prod's Redis keys into the VPS Redis.

**Step 2: Start.**

```bash
systemctl --user enable --now trading-prod.target && systemctl --user list-units 'trading-*'
```

Expected: nine units `active`.

**Step 3: Reach the webgui over Tailscale.**

```bash
ssh -L 8500:127.0.0.1:8500 john@<tailscale-name>
```

Open `http://127.0.0.1:8500`. Walk every page in the rail.

## Task 17: Verify the three mechanisms that only exist here

**Step 1: Restart** a component from the System Status page. Confirm with `systemctl --user status trading-prod-options_svc` that the unit — not a stray process — bounced.

**Step 2: Storm cap.** Break one unit's `ExecStart` deliberately, `daemon-reload`, and confirm it retries 5 times in 5 minutes then stays `failed`. Repair it.

**Step 3: Reboot the VPS.** Confirm all nine come back unattended (this is what `enable-linger` buys) and that `timedatectl` still reads CDT.

## Task 18: Prove the timezone end-to-end

Not by reading `timedatectl` — by reading the app's own arithmetic.

```bash
.venv/bin/python -c "import datetime,zoneinfo;n=datetime.datetime.now();print('naive',n,'| CT',datetime.datetime.now(zoneinfo.ZoneInfo('America/Chicago')).replace(tzinfo=None))"
```

Expected: the two are **identical to the second**. Then confirm a session-window predicate agrees with the Windows box at the same wall-clock moment.

---

# Phase 4 — Live validation

## Task 19: Borrow the Windows proxy over Tailscale

**Step 1: Install Tailscale on the Windows prod box.** Confirm the VPS can reach it:

```bash
curl -sS http://<windows-tailscale-ip>:8100/health
```

⚠ **Never** port-forward :8100 from the router. The proxy is unauthenticated.

**Step 2: Set `proxy_host = '<windows-tailscale-ip>'` in the VPS's `config/env.local.toml`** (a TOML **literal** string). Keep `name = "dev"` — `owns_proxy = false` is what stops a second holder of the rotating refresh token.

**Step 3: Restart and confirm** on-demand fetches work: run a Trade Analyzer symbol from the UI and watch it return real data.

## Task 20: One supervised collection window

⚠ **The risk this task manages:** two stacks collecting through **one** proxy contend for its 0.2 s rate limiter, and prod can start missing 1-minute slots. Do not leave this running.

**Step 1:** Set `TRADING_ENABLE_SCHEDULERS=1` for the VPS's units and restart, during RTH, **for one hour only**.

**Step 2: Watch prod's slot completeness the whole time.** Query `gex_history.db` on the Windows box for gaps in the last hour. Any gap means stop immediately.

**Step 3: Compare** the VPS's collected rows against prod's for the same minutes — same symbols, same view strings, values in agreement.

**Step 4: Turn schedulers back off.** Record the result. This is the last thing that needs validating before cutover.

---

# Phase 5 — Cutover

## Task 21: Switch authority

Pick a **weekend**, and confirm the Schwab refresh token is not near its 7-day expiry.

**Step 1:** Stop the Windows stack. It stays installed and runnable as the fallback.

**Step 2:** Re-copy the SQLite stores and Redis (Task 14's method) so the VPS has the final state. Prod has been writing all week; the Phase-3 snapshot is stale.

**Step 3:** Flip the VPS's `env.local.toml` to `name = "prod"` and **remove `proxy_host`** — it now owns the proxy on `127.0.0.1`.

**Step 4: Re-mint the Schwab token from the VPS.** Open `http://127.0.0.1:8100/auth` through the SSH tunnel, log in on your desktop, let `https://127.0.0.1:8182` fail, paste the URL into the form. Confirm `/health` reports a valid refresh token.

**Step 5:** Start the target. Verify all nine units, every page, and that notifications now fire from the VPS.

**Step 6: Merge the branch to `main`** and push.

## Task 22: Watch one full trading day

Do not declare done on a green start. Confirm across a whole session: collection slot completeness matches the Windows baseline, the scheduled Claude briefings fire once each, notifications arrive exactly once (**not twice** — proof the Windows stack is really down), and the driver's guardrails behave.

---

# Phase 6 — Dev, backups, then decommission

## Task 23: Bring dev up on the VPS

**Both environments now live on one host under one Linux user** — exactly today's shape, so this is a relocation rather than a reconfiguration.

**Step 1: Clone to `/home/john/dev`** and build its own venv (Task 13's method). Separate checkout, separate venv, separate `logs/` and `*/data/`.

**Step 2: `config/env.local.toml`:**

```toml
name = "dev"
peer_root = '/home/john/prod'
```

⚠ **No `proxy_host`.** Dev borrows prod's proxy at `127.0.0.1:8100` again, exactly as on Windows — co-location is what makes Task 9's knob unnecessary in steady state. Note `peer_root` needs no TOML literal-string quoting on a POSIX path, but keeping the `'...'` form costs nothing and survives a copy back to Windows.

**Step 3: Dev's own `/home/john/dev/.env`,** `chmod 600`. Separate file, separate secrets — dev's `allow_claude` and `allow_notifications` are both false, so it needs only `MEMURAI_PASSWORD`. Leaving `ANTHROPIC_API_KEY` out entirely is a second belt beside the profile flag.

**Step 4: Install the dev units.** `trading-dev-*` and `trading-dev.target`, identical to prod's but for `WorkingDirectory` and the offset ports (services 9210–9215, webgui 9500). The `trading-{ENV_NAME}-{name}` scheme means no collision is possible.

**Step 5: Confirm the four suppressions are live** — schedulers, Claude, notifications and autonomous trading all off. Check `/health` on a dev service and confirm no scheduler is registered.

**Step 6: Prove `snapshot_from_prod.py` still works.** This is the capability co-location preserves, and the reason for one user rather than two — it reads prod's SQLite stores directly.

```bash
cd /home/john/dev && .venv/bin/python tools/snapshot_from_prod.py
```

Expected: it refuses if dev is up (stop it first), copies prod's stores via the online-backup API with prod still running, and DUMPs db 0 into db 1 — excluding `cmd:*` and rewriting `cache:driver:control` disabled.

**Step 7: Flip `.claude/launch.json`.** Both configurations set `"runtimeExecutable": ".venv\Scripts\python.exe"`; on Linux that is `.venv/bin/python`. A single string cannot name both layouts, which is why Task 12 deferred it to here — by now no Windows checkout is being previewed from. Keep `autoPort: false` and the 9500/8500 split.

**Step 8: Confirm the two webguis are independent** — prod on 8500, dev on 9500, dev carrying its `DEV` chip and tab-title prefix. Restart a dev service from dev's Status page and confirm via `systemctl --user status` that only the `trading-dev-` unit moved.

## Task 24: Replace the E:-drive routine

**This is not optional and it has no equivalent on the VPS.** `paper_account.db`, `paper_account_driver.db` and `signals.db` are the trading record; `gex_history.db` is 1.52 GB of history that cannot be re-fetched.

**Step 1: Write `tools/backup_local.py`** — `sqlite3 .backup` (the online API, so the stack keeps running) for every store into a dated directory, then `DUMP` Redis. `tools/snapshot_from_prod.py` already does exactly this and is the model.

**Step 2: A systemd user timer**, nightly after `[windows.collection] stop`, keeping the newest 3 — mirroring the existing two-pass/keep-3 convention.

**Step 3: Pull to home over Tailscale**, so a backup survives the provider losing the instance. A backup that lives only on the VPS is not a backup.

**Step 4: Enable provider snapshots** as the whole-disk layer.

**Step 5: Test a restore.** An untested backup is a hypothesis.

## Task 25: Decommission Windows prod

Only after Task 22 is clean and Task 24's restore has been tested.

**Both** Windows checkouts go, not just prod: archive `D:\WebGUI Trading Prod` **and** `D:\WebGUI Trading with Schwab` to the E: drive, then remove them. Dev moved in Task 23, so nothing is left running on the box.

Update CLAUDE.md's Environments section: both environments are now VPS checkouts under one Linux user, `peer_root` is `/home/john/prod`, the launcher table is gone, and the `systemd --user` unit set replaces it. ⚠ **The proxy-borrow relationship is unchanged** — dev still borrows prod's proxy at `127.0.0.1:8100`, because co-location was preserved. Do not describe it as changed; only the *paths* moved.

**The HUD no longer gates this task** (decided 2026-08-29 — it is being redesigned separately).

⚠ **But do not delete `tools/nq_hud.py` from the repo.** Only `class Hud` (line 600) and `main()` are Tk; lines 1–599 are ~415 lines of pure, tested logic (`read_tape`, `read_gamma`, `BasisSmoother`, `build_pane`), and `test_nq_pane.py` + `test_nq_tape.py` import the module directly. Together with `nq_signal.py` / `nq_state.py` / `nq_signal_log.py` / `nq_instruments.py` that is **~1,512 lines the redesign will want**, plus 2,078 lines of tests already pinning it. Retiring the Tk layer belongs to the redesign, not to this migration — which only needed the HUD to stop being a *reason to keep a Windows desktop*. The modules are pure Python and should pass on Linux unchanged; Task 15 verifies that.
