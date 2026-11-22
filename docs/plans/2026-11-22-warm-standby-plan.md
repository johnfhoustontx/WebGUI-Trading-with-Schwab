# Warm Standby Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every night the standby box holds prod's code, data, cos-hub and market reports, restored into a stopped checkout; one command takes over, one command demotes a returning old prod, and a start fence stops two boxes running prod at once.

**Architecture:** prod's nightly backup gains the commit and the two neighbour projects; the standby pulls the newest complete generation over a read-only `rrsync` key, checks it and restores it. Roles are symmetric and read from a machine-local identity file outside the checkout. A oneshot fence unit, required by every prod service, refuses to start the stack on a box that is not prod.

**Tech Stack:** Python 3.12 stdlib, systemd user units, rsync/rrsync, git, uv, pytest.

**Spec:** [docs/plans/2026-11-22-warm-standby-design.md](2026-11-22-warm-standby-design.md)

## Global Constraints

- Nothing new may need passwordless sudo. Root steps are printed for the operator.
- Every tunable lives in `config/standby.toml` (or a `config/sessions.toml` slot) with a `webgui/config_schema.py` entry (`webgui/tests/test_config_schema.py`).
- **The box identity file is `~/.config/neuralstrike/box.toml`, never in the checkout.** `config/local/` is swept into every backup (`DATA_TREES`), so an identity there would be restored onto the peer. Keys: `self = "vps2"`, `role = "prod" | "standby"`, `taking_over = false`.
- No identity file means no standby feature: the fence passes, the watch and the sync exit 0 saying so. This is what makes shipping to prod before any box is set up safe.
- Only the `prod` profile is fenced. `ENV_NAME == "dev"` always passes (the drill runs as dev).
- A DNS lookup that fails or times out PASSES the fence. A DNS outage must never keep prod down.
- Never `cp` a live SQLite file: neighbour stores go through the online backup API, like `backup_db`.
- Never restore Redis. Never touch `cmd:*`.
- A swallowed exception in a guard of 15+ lines calls `_degrade.degraded()` (`test_no_silent_degrades.py`).
- Shell files are LF (`tools/tests/test_shell_line_endings.py`). Test files are written with the editor, never a heredoc.
- New test files go under existing CI rows: `tools/tests`, `tests`, `shared/tests`. No new folder.

## Review Focus

1. **A prod box with no identity file after the promote**: every unit still starts, the stack is unchanged. Test in Task 4 (`test_fence_passes_without_identity`, and a `render_all` test that the fence unit's script exits 0 with no file).
2. **The resolver hangs or returns several addresses** (Wix may hand back more than one A record): the fence passes when ANY returned address is this box's, refuses only when every address is the peer's, and passes on a timeout. Task 4.
3. **A generation half-written when the standby looks** (backup still running at 22:15): it has no `BACKUP_OK` yet and is skipped, and the older complete one is used. Task 5 (`test_newest_complete_skips_unmarked`).
4. **The standby's disk fills mid-copy**: the `.partial` is removed, the kept generations and the restored checkout are untouched, the run fails and pushes. Task 5.
5. **A takeover while DNS still points at the old prod, followed by a reboot before Wix is changed**: the box still starts (the takeover mark survives the reboot) and the mark is cleared only once DNS resolves to it. Task 4.

---

### Task 1: The standby configuration and the box identity

**Files:**
- Create: `config/standby.toml`, `shared/standby_config.py`, `shared/tests/test_standby_config.py`
- Modify: `repo_paths.py` (beside `BACKUP_TOML`, line ~121), `webgui/config_schema.py` (new `_STANDBY` beside `_BACKUP`, added to `FILES`)

**Interfaces:**
- Produces, in `shared/standby_config.py` (stdlib + `shared.config_toml` + `repo_paths` only):
  - `load() -> dict`, `reset_cache()` from `toml_loader(STANDBY_TOML, DEFAULTS, label="standby.toml")`
  - `sync_settings() -> dict` with `at: str "HH:MM"`, `max_age_h: int`, `max_shrink_pct: int`, `keep: int`, `generations: pathlib.Path` (expanded), `shrink_stores: list[str]`
  - `boxes() -> dict[str, dict]` each `{ssh: list[str], public_ip: str, proxy_health: str}`
  - `neighbours() -> list[dict]` each `{name, dir, exclude: list[str], units: list[str], files: list[str]}`
  - `@dataclass(frozen=True) class Identity: self_name: str; role: str; taking_over: bool`
  - `read_identity(path: pathlib.Path | None = None) -> Identity | None` (None when absent or unreadable; `role` must be `prod`/`standby` else None)
  - `write_identity(identity: Identity, path: pathlib.Path | None = None) -> None` (0600, atomic replace)
  - `peer(identity: Identity) -> tuple[str, dict]` (the other box's name and entry; raises `ValueError` when the config does not hold exactly two boxes including `self`)
- Produces, in `repo_paths.py`: `STANDBY_TOML = REPO_ROOT / "config" / "standby.toml"`, `BOX_IDENTITY = pathlib.Path.home() / ".config" / "neuralstrike" / "box.toml"`

Values for `config/standby.toml` (copy the design's block, plus):

```toml
[sync]
at = "22:15"
max_age_h = 80
max_shrink_pct = 50
keep = 3
generations = "~/standby/generations"
shrink_stores = ["options-scanner/data/paper_account.db", "options-scanner/data/signals.db", "options-scanner/gex_history.db"]

[boxes.vps2]
ssh = ["trading-prod-2", "63.141.255.25"]
public_ip = "63.141.255.25"
proxy_health = "https://trading-prod-2.tail3050ed.ts.net:8100/health"

[boxes.vps3]
ssh = ["trading-standby", "192.227.249.22"]
public_ip = "192.227.249.22"
proxy_health = "https://trading-standby.tail3050ed.ts.net:8100/health"

[[neighbours]]
name = "cos-hub"
dir = "cos-hub"
exclude = [".venv", "config.local.toml.bak-*", "__pycache__"]
units = ["cos-hub.service"]
files = []

[[neighbours]]
name = "neuralstrike-reports"
dir = "neuralstrike-reports"
exclude = [".venv", "logs", "__pycache__"]
units = ["neuralstrike-report@.service", "neuralstrike-report-failed@.service", "neuralstrike-report-*.timer", "neuralstrike-report@globex.service.d"]
files = [".config/neuralstrike/claude.env"]
```

Confirm the three `shrink_stores` paths against `backup_local.db_paths` output on prod before relying on them (Task 9 step 2 prints them).

- [ ] **Step 1: Write the failing tests** in `shared/tests/test_standby_config.py`: `test_defaults_when_file_missing` (monkeypatch `STANDBY_TOML` to a missing path, `sync_settings()["keep"] == 3`, `["at"] == "22:15"`); `test_identity_round_trip` (`write_identity(Identity("vps3","standby",False), p)`; `read_identity(p) == Identity("vps3","standby",False)`; file mode `0o600` on POSIX); `test_identity_absent_is_none`; `test_identity_bad_role_is_none` (`role = "boss"`); `test_peer_is_the_other_box` (`peer(Identity("vps2","prod",False))[0] == "vps3"`); `test_peer_rejects_unknown_self`; `test_import_set_is_stdlib_config_toml_repo_paths` (parse the module's imports with `ast`).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest shared/tests/test_standby_config.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** the module, the two `repo_paths` constants, `config/standby.toml`, and `_STANDBY` in `config_schema.py` (fields for every `[sync]` key; `boxes` and `neighbours` as read-only/advanced if the schema has no table kind — follow how `_PORTS` treats non-editable structure).
- [ ] **Step 4: Run** the new tests and `cd webgui && ../.venv/bin/python -m pytest tests/test_config_schema.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(standby): config/standby.toml and the box identity file`.

### Task 2: The backup records the commit and carries the neighbours

**Files:**
- Modify: `tools/backup_local.py` (`main`, before the `OK_MARKER` write), `tools/tests/test_backup_local.py`

**Interfaces:**
- Consumes: `standby_config.neighbours()`
- Produces: `GIT_HEAD = "GIT_HEAD"` and `NEIGHBOURS_DIR = "neighbours"` module constants; `write_git_head(out: Path, root: Path, run=subprocess.run) -> str | None` (the sha, or None and a failure recorded when git fails); `backup_neighbour(spec: dict, home: Path, out: Path, run=subprocess.run) -> list[str]` (returns failures)
- Generation layout per neighbour `n`: `neighbours/<n>/dir/<files>`, `neighbours/<n>/units/<unit files and .d dirs>`, `neighbours/<n>/files/<path relative to home>`, `neighbours/<n>/venv-freeze.txt`

Rules: a missing neighbour `dir` is printed `--  <n> (absent)` and is NOT a failure (dev boxes, tests). A file whose first 16 bytes are `b"SQLite format 3\x00"` goes through `backup_db`; `-wal`/`-shm` files are skipped. `exclude` entries match a path component or an `fnmatch` on the name. `venv-freeze.txt` is `uv pip freeze --python <dir>/.venv/bin/python` (uv at `~/.local/bin/uv`); a failure there is a backup failure (the standby cannot rebuild without it). Unit globs are expanded in `~/.config/systemd/user`.

- [ ] **Step 1: Write the failing tests**: `test_generation_carries_git_head` (fake `run` returning a sha; `(out/"GIT_HEAD").read_text().strip() == sha`); `test_git_head_failure_is_a_failure_and_blocks_ok_marker`; `test_neighbour_sqlite_goes_through_online_backup` (a real tiny SQLite in a tmp `home/cos-hub/data/hub.sqlite3` with a row; the copy opens and holds the row; no `-wal` copied); `test_neighbour_excludes` (`.venv/x` and `logs/a.jsonl` absent from the generation, `prompts/p.md` present); `test_neighbour_units_and_files_copied` (glob `neuralstrike-report-*.timer` picks two timers; the `.d` dir copied; `.config/neuralstrike/claude.env` under `files/`, mode 0600); `test_absent_neighbour_is_not_a_failure`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tools/tests/test_backup_local.py -q` → the new tests FAIL.
- [ ] **Step 3: Implement** both functions and call them from `main` before the marker. Keep `test_backup_local`'s existing `.gitignore`-derived guard passing.
- [ ] **Step 4: Run** the whole file → PASS.
- [ ] **Step 5: Commit** `feat(backup): record the commit and carry cos-hub and the market reports`.

### Task 3: Restore refuses unknown paths and never restores the markers or neighbours

**Files:**
- Modify: `tools/restore_backup.py` (`files_in`, `restore`, `main`), `tools/tests/test_restore_backup.py`

**Interfaces:**
- Produces: `restore(generation, into, *, dry_run=False, force=False, out=print, allow=None) -> dict` where `allow: Callable[[pathlib.PurePosixPath], bool] | None`; the summary gains `"refused": [str]`. A refused file is not written. `main` exits 1 when `refused` is non-empty. `files_in` also skips `GIT_HEAD` and everything under `neighbours/`.
- The module keeps its rule: **no imports from this repository** (the predicate is passed in).

- [ ] **Step 1: Write the failing tests**: `test_git_head_and_neighbours_never_restored`; `test_allow_refuses_unlisted_path` (`allow=lambda p: p.parts[0] == "webgui"`; a planted `tools/evil.py` is in `refused`, not on disk under `into`); `test_refused_makes_main_exit_1`; `test_no_allow_keeps_old_behaviour`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tools/tests/test_restore_backup.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS, including the existing round-trip drill test.
- [ ] **Step 5: Commit** `feat(restore): an allow-list, and never restore GIT_HEAD or the neighbours`.

### Task 4: The start fence

**Files:**
- Create: `tools/standby_fence.py`, `tools/tests/test_standby_fence.py`
- Modify: `deploy/systemd/generate_units.py` (new `fence_name()`, `_fence_unit()`, `_with_fence(name, text)` applied in `render_all` beside `_with_failure_alert`), `tests/test_systemd_units.py`

**Interfaces:**
- Produces: `decide(identity: Identity | None, env_name: str, self_ip: str | None, peer_ip: str | None, addresses: list[str] | None) -> tuple[bool, str, bool]` returning `(start_allowed, reason, clear_takeover)`; `addresses=None` means the lookup failed or timed out. `resolve(host: str, timeout_s: float) -> list[str] | None` (`socket.getaddrinfo` on a worker thread; IPv4 only). `main() -> int`: 0 allowed, 1 refused (prints the reason); clears `taking_over` via `write_identity` when told to.
- Produces: unit `trading-{ENV_NAME}-fence.service`: `Type=oneshot`, `RemainAfterExit=yes`, `WorkingDirectory`/`TZ`/`ExecStart` like the token-watch unit, `ExecStart={python} tools/standby_fence.py`, `TimeoutStartSec=30`. `_with_fence` adds `Requires=` and `After=` the fence to every `.service` except the fence and the notifier. The fence keeps its `OnFailure=` (a refusal pushes "this box is not prod; stack held").

`decide` table (the whole rule):

| case | result |
|---|---|
| `env_name == "dev"` | allow, "dev is never fenced" |
| `identity is None` | allow, "no box identity" |
| `identity.role != "prod"` | refuse, "role is standby" |
| `addresses is None` | allow, "lookup failed; not evidence" |
| `self_ip in addresses` | allow; `clear_takeover = identity.taking_over` |
| `addresses` all equal `peer_ip` and `identity.taking_over` | allow, "taking over; DNS not moved yet" |
| `addresses` all equal `peer_ip` | refuse, "public name points at <peer>" |
| anything else (an address neither box owns) | allow, "unknown address; not evidence" |

The host looked up is `repo_paths.APP_HOST`; `resolve` timeout 5 s.

- [ ] **Step 1: Write the failing tests**: one test per table row (`test_fence_passes_for_dev`, `test_fence_passes_without_identity`, `test_fence_refuses_standby_role`, `test_fence_passes_when_lookup_fails`, `test_fence_passes_and_clears_takeover_when_dns_is_self`, `test_fence_passes_while_taking_over`, `test_fence_refuses_when_dns_points_at_peer`, `test_fence_passes_unknown_address`); `test_fence_passes_with_several_addresses_including_self`; `test_resolve_timeout_returns_none` (monkeypatch `getaddrinfo` to sleep past the timeout). In `tests/test_systemd_units.py`: `test_every_prod_service_requires_the_fence` (every `.service` in `render_all()` but the fence and the notifier has both lines); `test_fence_unit_shape` (`RemainAfterExit=yes`, oneshot, no `Requires=` on itself).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tools/tests/test_standby_fence.py tests/test_systemd_units.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS. Also `.venv/bin/python -m deploy.systemd.generate_units --dest /tmp/units-check --no-activate` and `systemd-analyze --user verify /tmp/units-check/*.service` if available (on Windows, skip and note it for Task 9).
- [ ] **Step 5: Commit** `feat(fence): the stack starts only on the box that is prod`.

### Task 5: The nightly sync

**Files:**
- Create: `tools/standby_sync.py`, `tools/tests/test_standby_sync.py`

**Interfaces:**
- Consumes: Task 1 (`sync_settings`, `boxes`, `neighbours`, `read_identity`, `peer`), Task 2 constants, Task 3 `restore(..., allow=)`.
- Produces:
  - `class Remote(Protocol)`: `generations() -> list[str]`, `has(gen: str, name: str) -> bool`, `read(gen: str, name: str) -> str`, `fetch(gen: str, dest: Path, basis: Path | None) -> int` (bytes). `RsyncRemote(targets: list[str])` implements it with `rsync -e "ssh -o BatchMode=yes"` against the rrsync root, trying targets in order.
  - `newest_complete(remote: Remote) -> str | None` (names matching `<env>_YYYY-MM-DD_HHMM`, newest first, the first with both `BACKUP_OK` and `GIT_HEAD`)
  - `generation_age_h(name: str, now: datetime) -> float`
  - `allowed_path(rel: PurePosixPath) -> bool` built from `backup_local.EXTRA_FILES`, `backup_local.DATA_TREES`, suffix `.db`, minus `backup_local.SWEEP_EXCLUDE`
  - `shrink_refusals(new: Path, prev: Path | None, stores: list[str], max_pct: int) -> list[str]` (file size; a store missing in `new` but present in `prev` is a refusal)
  - `move_code(commit: str, repo: Path, run=subprocess.run) -> bool` (fetch; refuse with `SyncError` unless `git merge-base --is-ancestor <commit> origin/main`; `checkout --detach`; returns whether `requirements.lock` changed; then `pip install -r requirements.lock` when it did)
  - `restore_neighbours(gen: Path, home: Path, specs: list[dict], run=subprocess.run) -> None` (copies `dir/` over `~/<dir>` with `.venv` untouched, `files/` to `~/<rel>` at 0600, `units/` to `~/standby/units/<name>/`; rebuilds `~/<dir>/.venv` with `uv venv` + `uv pip install -r venv-freeze.txt` when the freeze differs from `~/standby/units/<name>/venv-freeze.txt`)
  - `prune(gen_root: Path, keep: int) -> list[str]`
  - `run(now: datetime, *, identity, remote: Remote, repo: Path, home: Path, settings, run=subprocess.run) -> dict` (the status dict) and `main() -> int`
  - `class SyncError(Exception)`
- Status file `~/standby/status.json`: `{"checked_at", "result": "synced"|"unchanged"|"failed"|"refused", "generation", "commit", "bytes", "started", "finished", "detail"}`. Written on every run, including failures.

Order inside `run` is the design's section 3 order. Exits:
- no identity file → 0, prints "no box identity; nothing to do", writes no status;
- role `prod`, or any `~/.config/systemd/user/trading-prod-*` file exists → `refused`, exit 1 (the design's first lock);
- `failed` → exit 1;
- `unchanged` → exit 0, unless the newest complete generation is older than `max_age_h` → exit 1;
- `synced` → exit 0.

- [ ] **Step 1: Write the failing tests** with a `FakeRemote` and a fake `run`: `test_refuses_on_promoted_box` (unit file present → `refused`, nothing fetched); `test_newest_complete_skips_unmarked`; `test_unchanged_is_quiet_and_recorded`; `test_unchanged_but_too_old_fails` (`now` 81 h after the generation); `test_shrink_refuses_and_keeps_previous` (paper store 10 KB → 4 KB; `.partial` removed, previous generation intact, `restore` not called); `test_commit_not_on_main_fails`; `test_lock_change_reinstalls` (the `pip install` argv seen by fake `run`); `test_planted_file_fails_the_night` (generation holding `tools/evil.py` → `failed`, `evil.py` not in `repo`); `test_disk_full_mid_copy_leaves_previous_untouched` (`fetch` raises `OSError(ENOSPC)`); `test_prune_keeps_three`; `test_neighbour_venv_rebuilt_only_when_freeze_changes`; `test_status_written_on_failure`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tools/tests/test_standby_sync.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(standby): the nightly pull, check and restore`.

### Task 6: Prod watches the standby

**Files:**
- Create: `tools/standby_watch.py`, `tools/tests/test_standby_watch.py`
- Modify: `deploy/systemd/generate_units.py` (`_standby_watch_units()` in `render_all`), `config/sessions.toml` (`[slots.standby_watch] at = "07:40"`, comment in the `token_watch` style: every day, a systemd timer), `tests/test_systemd_units.py`

**Interfaces:**
- Produces: `assess(status_text: str | None, now: datetime, max_age_h: int) -> str | None` (None = healthy; else the alert's one-line reason: "standby did not answer", "standby status unreadable", "last good sync <N> h ago (<generation>)", "last run failed: <detail>"); `fetch_status(targets: list[str], run=subprocess.run) -> str | None` (`ssh -o BatchMode=yes -o ConnectTimeout=15 <target>`: the forced command prints the file); `main() -> int` (exit 0 always after sending; exit 0 with "not prod / no identity" when the identity is absent or `role != "prod"`). Sends through `shared.notify.system_alert.send(title, lines)` like `tools/token_watch.py`.
- Unit pair `trading-{ENV}-standby-watch.service/.timer`: copy the token-watch shape, `OnCalendar=*-*-* 07:40:00` from the slot, `Persistent=true`.

- [ ] **Step 1: Write the failing tests**: one per `assess` outcome, plus `test_watch_silent_on_standby_box` and `test_watch_unit_rendered_from_slot`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tools/tests/test_standby_watch.py tests/test_systemd_units.py` → PASS.
- [ ] **Step 5: Commit** `feat(standby): prod checks every morning that the standby synced`.

### Task 7: Standby units and one-time provisioning

**Files:**
- Create: `deploy/systemd/standby_units.py`, `tests/test_standby_units.py`, `deploy/standby/provision.sh`
- Modify: `tools/tests/test_shell_line_endings.py` only if it lists files explicitly

**Interfaces:**
- Produces: `render() -> dict[str, str]` with `standby-sync.service` (oneshot, `ExecStart={python} tools/standby_sync.py`, `TimeoutStartSec=7200`, `OnFailure=` the notifier), `standby-sync.timer` (`OnCalendar=*-*-* <[sync] at>:00`, `Persistent=true`, `WantedBy=timers.target`), and the notifier template reused from `generate_units._notifier_unit()`; `install(dest=None) -> list[Path]`; `activate(runner=None)` (daemon-reload, `enable --now standby-sync.timer`); `uninstall(dest=None, runner=None)` (disable the timer, delete the files); CLI `--install`, `--uninstall`.
- `provision.sh <self> ` (run as the operator with sudo prompts; idempotent; `set -euo pipefail`): apt packages (`python3-venv rsync redis-server caddy age zstd rclone` plus what Task 9 step 1's `dpkg` diff adds); `systemctl disable --now caddy`; uv via its official installer into `~/.local/bin`; Claude Code CLI install (no sign-in); clone `https://github.com/<owner>/<repo>.git` to `/home/administrator/dev` if absent (read the URL from `git remote get-url origin` in this checkout when writing the script); `.venv` from `requirements.lock`; `ssh-keygen -t ed25519 -f ~/.ssh/standby_pull -N ''` and print the vps2 `authorized_keys` line (the design's `rrsync -ro` line); append the given status-read public key to `~/.ssh/authorized_keys` with `restrict,command="cat /home/administrator/standby/status.json"`; write `~/.config/age/backup-key.txt` holding only the `# public key:` line passed as an argument; write the identity file (`role = "standby"`); `python -m deploy.systemd.standby_units --install`. Redis `requirepass` and the `live` and `cos` ACL users are configured by a printed block after the first sync has restored `.env`, `.env.live` and the hub's config (they hold the passwords), with `CONFIG REWRITE` to persist.

- [ ] **Step 1: Write the failing tests** in `tests/test_standby_units.py`: `test_sync_timer_from_config` (monkeypatch `[sync] at` to `"23:05"` → `OnCalendar=*-*-* 23:05:00`); `test_sync_service_alerts_on_failure`; `test_no_trading_prod_unit_rendered`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_standby_units.py -q` → FAIL.
- [ ] **Step 3: Implement** the module and the script. `bash -n deploy/standby/provision.sh` must pass.
- [ ] **Step 4: Run** the tests and `tools/tests/test_shell_line_endings.py` → PASS.
- [ ] **Step 5: Commit** `feat(standby): the standby's units and one-time provisioning`.

### Task 8: Takeover, drill and demotion

**Files:**
- Create: `tools/standby_failover.py`, `tools/tests/test_standby_failover.py`

(The design names `failover_to_standby.sh` and `become_standby.sh`; one Python tool with three sub-commands is testable and keeps the probes in one place. My call.)

**Interfaces:**
- Consumes: Tasks 1, 4, 7 (`standby_units.uninstall`), `generate_units.install`/`activate`.
- Produces:
  - `probe_peer(peer: dict, app_host: str, run=subprocess.run, connect=socket.create_connection) -> list[str]` — names of the probes that answered: `"tailnet proxy"` (`curl -fsS --max-time 8 <proxy_health>`), `"public app"` (`curl -sS -o /dev/null --max-time 8 --resolve <app_host>:443:<public_ip> https://<app_host>/login`, answered = any HTTP status), `"public ssh"` (TCP connect to `<public_ip>:22`, 5 s).
  - `takeover(identity, *, peer_is_down: bool, ...) -> int`: refuses (exit 1, naming the probes) when `probe_peer` is non-empty and not `peer_is_down`; else `standby_units.uninstall()`, `write_identity(role="prod", taking_over=True)`, rewrite `proxy_public_url` in `config/env.local.toml` to `https://<self tailnet name>.tail3050ed.ts.net:8100` (derive from `boxes()[self]["proxy_health"]`), install the neighbours' units from `~/standby/units/<name>/` plus a fence drop-in `<unit>.d/fence.conf` (`Requires=` + `After=trading-prod-fence.service`), `generate_units.install()` + `activate()`, `systemctl --user start trading-prod.target cos-hub.service`, enable the report timers, probe every component's `/health` as `promote.sh` does, then print the operator steps (the design's list, with this box's IP and the four Wix records, `sudo tailscale serve`, Claude Code sign-in, the two workstation tasks).
  - `drill() -> int`: refuses unless `identity.role == "standby"`; backs up `config/env.local.toml`, writes `name = "dev"`, `generate_units.install()` with `activate` skipped, starts `trading-dev.target`, probes each component at its +1000 port, stops it, deletes the `trading-dev-*` files it wrote, restores `env.local.toml` (in a `finally`), prints a pass/fail line per component.
  - `become_standby(...) -> int`: refuses when `resolve(APP_HOST)` contains this box's IP; runs `tools/backup_local.py --no-offsite --dest ~/backups/predemote` (the folder name keeps it out of the nightly prune); stops and disables `trading-prod.target`, the neighbours and every `trading-prod-*` timer; deletes the generated `trading-prod-*` unit files; writes `role = "standby"`; runs `standby_units --install`; prints the keys to exchange with the new prod.
  - `fence_neighbours(specs, home) -> list[Path]`: writes the fence drop-in for every neighbour unit file present in `~/.config/systemd/user`, then `daemon-reload`. Used by `takeover`, and run once on today's prod (Task 9 step 4), because cos-hub and the report timers are not in `trading-prod.target` and would otherwise start on a returning old prod.
  - CLI: `takeover [--peer-is-down]`, `drill`, `become-standby`, `fence-neighbours`.

- [ ] **Step 1: Write the failing tests** (fake `run`, fake `connect`, tmp home): `test_fence_neighbours_writes_dropins_for_present_units_only`; `test_takeover_refuses_while_peer_answers` (any one probe answering → exit 1, nothing written); `test_takeover_peer_is_down_overrides`; `test_takeover_marks_taking_over_and_installs`; `test_public_probe_targets_peer_ip_not_dns` (the curl argv holds `--resolve app...:443:<peer ip>`); `test_neighbour_units_get_fence_dropin`; `test_drill_restores_env_local_on_failure`; `test_drill_refuses_on_prod`; `test_become_standby_refuses_when_dns_is_self`; `test_become_standby_takes_predemote_backup_first`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tools/tests/test_standby_failover.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS; then the full `tools/tests`, `tests`, `shared/tests` rows and `webgui/tests/test_config_schema.py`; compare the failing SET with `main`'s.
- [ ] **Step 5: Commit** `feat(standby): takeover, drill and demotion`.

### Task 9: Documents, then the rollout

**Files:**
- Modify: `docs/dev-prod-environments.md` (new section 13 "The standby and failing over": what runs where, the identity file, the nightly sync, the watch, takeover, drill, demotion, the Wix TTL), `docs/manuals/technical-reference/technical-reference.md` (the 22:15 sync and the 07:40 watch in the cadence tables), `docs/manuals/user-guide/user-guide.md` ("Server alerts": the two new alerts), `docs/CHANGELOG.md`, `CLAUDE.md` (Running section: two lines — the identity lives in `~/.config/neuralstrike/box.toml` and never in the checkout or a backup; every prod service requires the fence, so a new unit gets it from `render_all`), the design doc's status line.

- [ ] **Step 1: Write the docs**, rebuild the manuals with `build_docs.py`, run `tests/test_claude_md_size.py`. Commit `docs(standby): runbook, manuals, changelog`.
- [ ] **Step 2: Merge to `main`, push.** Promote vps2 **after 15:20 CT on a trading day or any time on a weekend** (the fence lands on prod with no identity file: it must pass). After the promote: `systemctl --user list-units 'trading-prod-*'` all active, `systemctl --user status trading-prod-fence.service` shows "no box identity", the stream and live-capture timer restarted per the promote note. Then on vps2: `ls ~/backups/<newest>/GIT_HEAD`, and the neighbours folder in the next generation (or a manual `systemctl --user start trading-prod-backup.service` outside market hours).
- [ ] **Step 3: Provision vps3** with the operator: `dpkg --get-selections` diff between the boxes, add anything the stack or the neighbours need to `provision.sh`, the operator runs it, then adds the printed `rrsync` line on vps2 and the status-read key on vps3.
- [ ] **Step 4: Write the identity files**: vps2 `self = "vps2", role = "prod"`; vps3 `self = "vps3", role = "standby"`. Check vps2's fence still passes (`systemctl --user restart trading-prod-fence.service` outside market hours: DNS resolves to vps2). Then `standby_failover.py fence-neighbours` on vps2 and `systemctl --user show cos-hub.service -p Requires` lists the fence.
- [ ] **Step 5: First sync**: `systemctl --user start standby-sync.service` on vps3, read `~/standby/status.json` (`result = "synced"`), the checkout at prod's commit, every database `ok`, `~/cos-hub` and `~/neuralstrike-reports` present. Then the printed Redis block (operator, sudo).
- [ ] **Step 6: Drill**: `standby_failover.py drill` on vps3 → every component passes. Then `standby_watch.py` on vps2 by hand → healthy.
- [ ] **Step 7: Operator**: lower the TTL of the four Wix A records to the lowest Wix allows; note the value in the runbook.
- [ ] **Step 8: Update memory** (`second-vps-racknerd.md`: the standby is live, where the status file is, the drill date).
